# MVP Robot Guía Autónomo — Unitree G1 EDU

Sistema de software autónomo para navegación, interacción conversacional e integración sensorial del robot humanoide Unitree G1 EDU.

---

## Objetivo: MVP Robot Guia Autonomo sobre Unitree G1 EDU

Implementar un sistema de guiado autónomo que integre:
- Navegación autonomizada mediante ROS 2 Nav2 + AMCL + localización visual.
- Interacción conversacional bidireccional con pipeline NLP híbrido (Edge + Cloud para fallback).
- Orquestación de comportamiento mediante máquina de estados asíncrona.
- Operación en entorno air-gapped (sin conexión internet) con topología de red aislada.

---

## Arquitectura del Sistema

### Paradigma de Ejecución: Event Loop Asíncrono No Bloqueante

El sistema prioriza operaciones no bloqueantes sobre el bucle de eventos de `asyncio` para preservar la telemetría de equilibrio dinámico del robot.

- **Contexto:** Toda operación potencialmente bloqueante (I/O de red, cálculos pesados, DDS) se ejecuta en `ThreadPoolExecutor` dedicados.
- **Beneficio:** El hilo principal puede servir callbacks de sensores y cambios de estado sin latencia acumulada.
- **Patrón Principal:** `asyncio.run_in_executor()` + `asyncio.wait_for(timeout)` para timeouts de seguridad.

### Máquina de Estados Orquestadora

**Clase:** `TourOrchestrator` (python-statemachine AsyncEngine).

**Estados Principales:**

| Estado | Actividad | Transición |
|--------|-----------|-----------|
| `IDLE` | Esperando activación via API FastAPI | → `NAVIGATING` |
| `NAVIGATING` | Ejecución de waypoints via Nav2 (`followWaypoints`) | → `INTERACTING` (pausa NLP) o → `IDLE` (plan completado) |
| `INTERACTING` | Pipeline conversacional bidireccional (STT→LLM→TTS) | → `NAVIGATING` (fin diálogo) |
| `EMERGENCY` | Parada de emergencia perentoria (`Damp()` + cancelación de tareas) | Estado final (sin transición de salida) |

**Integración de Dependencias:**
- `hardware_api`: Instancia singleton `RobotHardwareAPI` para comandos cinemáticos.
- `nav_bridge`: Instancia `AsyncNav2Bridge` para despacho de waypoints y corrección AMCL.
- `conversation_manager`: Instancia `ConversationManager` para diálogos.
- `vision_processor`: Instancia `VisionProcessor` para odometría visual.
- Callback `on_enter_emergency`: Ejecuta `await damp()` con timeout de seguridad.

---

## Stack de Navegación y Visión

### Localización y Navegación (ROS 2)

**Middleware:** Eclipse CycloneDDS 0.10.2 (no bloqueante, basado en eventos).

**Nodo Principal:** `nav2_simple_commander.BasicNavigator`
- Método: `followWaypoints(poses: List[PoseStamped])` para recorridos waypoint-a-waypoint.
- Monitorización: Bucle async `while not navigator.isTaskComplete()` con `await asyncio.sleep(0.1)`.
- Inyección de pose: `setInitialPose(initial_pose)` para corrección AMCL basada en visión.

**Parámetro de Seguridad:** Velocidad lineal máxima capada a **0.3 m/s** (override sobre límite teórico de 2 m/s).

### Corrección Odométrica Visual

**Fuente:** Cámara de profundidad VIPCAM D435i integrada en cabeza del G1.

**Método:** Detector de AprilTags (`tag36h11`) + `cv2.solvePnP` para pose absoluta.

**Matemática:**
- Entrada: Puntos 3D conocidos (esquinas del tag), proyección 2D en imagen, matriz intrínseca K, coeficientes de distorsión.
- Salida: Vector de rotación compacta (`rvec` Rodrigues) + vector de traslación (`tvec`).
- Transformación: $R_{mat} = cv2.Rodrigues(rvec)$; $P_{cam} = -R_{mat}^T \times tvec$.

**Integración:**
- Publicación en nodo ROS 2 mediante publisher en `/initialpose`.
- AMCL reinicializa con covarianza conservadora (0.15 xy, 0.4 theta).

---

## Hardware y Topologia de Red

### Definicion Arquitectonica de Conectividad (Auditada)

La conectividad de campo del MVP queda definida como **obligatoriamente cableada por RJ45 en el robot y desacoplada por un Access Point externo en modo puente inalambrico (Wireless Bridge)**.

Justificacion tecnica:
- El puerto RJ45 del robot mantiene el plano de datos del controlador de locomocion en una interfaz Ethernet estable y determinista para DDS.
- El AP en modo puente absorbe cambios del medio radio (roaming, RSSI variable, reconexiones 802.11) sin desmontar el enlace logico Ethernet visto por la pila DDS del robot.
- Esta separacion evita que CycloneDDS pierda participantes por eventos de down/up de interfaz inalambrica del host de control.
- El resultado es continuidad del flujo asincrono de telemetria y comandos, preservando el event loop sin bloqueos por renegociaciones de red.

Evidencia en base de codigo:
- `config/cyclonedds.xml`: multicast deshabilitado y descubrimiento unicast por pares estaticos.
- `scripts/start_robot.sh`: forzado de `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` y `CYCLONEDDS_URI` para arranque determinista.

### Topologia Air-Gapped

### Configuracion DDS Unicast

**Razón:** El multicast UDP estándar genera pérdida severa de paquetes sobre IEEE 802.11 (WLAN).

**Solución:** Desactivación de SPDP multicast + resolución estática de pares (peers) en XML.

**Archivo de Configuración:** `config/cyclonedds.xml`

```xml
<CycloneDDS>
  <Domain id="any">
    <General>
      <Interfaces>
        <NetworkInterface name="eth0" />
      </Interfaces>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <ParticipantIndex>auto</ParticipantIndex>
      <Peers>
        <Peer address="192.168.123.161" />
      </Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
```

**Inyección de Entorno:**
```bash
export CYCLONEDDS_URI="file://$(pwd)/config/cyclonedds.xml"
export RMW_IMPLEMENTATION="rmw_cyclonedds_cpp"
```

### Topologia Fisica

- **Robot G1 (módulo locomoción):** IP `192.168.123.161:7890` (DDS).
- **Companion PC (si existe):** IP `192.168.123.164`.
- **Enlace obligatorio:** Access Point 802.11ac externo conectado al puerto RJ45 del robot y configurado como Wireless Bridge.
- **Sin:** Gateway a Internet (air-gapped deliberado).

---

## Seguridad Operacional

### Overrides Mecanicos Implementados

- `Damp()` por software en rutas de error y shutdown controlado.
- Override hardware inmediato `L1 + A` en control remoto.
- Clamping cinematico estricto de velocidad lineal maxima a `0.3 m/s`.

### Restricción Cinemática (Clamping Obligatorio)

**Constante:** `MAX_LINEAR_VELOCITY = 0.3 m/s`.

**Aplicación:**
- En `RobotHardwareAPI.move(vx, vy, wz)`: método `_clamp_linear_velocity()` limita cada componente y norma vectorial.
- En `AsyncNav2Bridge`: `setSpeedLimit(0.3, False)` configura BasicNavigator + subscriber `/cmd_vel` como doble barrera.

**Rationale:** Minimiza riesgo de caídas del robot sobre superficies lisas (vidrio, cerámica).

### Comando de Emergencia Hardware

**Override:** `L1 + A` en control remoto.

**Acción:** Fuerza ejecución inmediata de `Damp()` a nivel de firmware (desacoplamiento elástico de actuadores).

**Tiempo de Respuesta:** <50 ms (independiente del software).

### Punto de Fallo Seguro

**En `TourOrchestrator.on_enter_emergency()`:**
```python
try:
    await asyncio.wait_for(self._hardware_api.damp(), timeout=1.5)
except TimeoutError:
    log.error("Damp timeout en EMERGENCY")
```

**Resultado:** Robot entra en amortiguación pasiva incluso ante fallos de comunicación. Estado EMERGENCY es terminal; requiere reinicio del proceso.

### Prohibiciones de Operación

- **No simultaneidad:** Control remoto manual + API orchestrator al mismo tiempo.
- **No de pie sin soporte:** Apagado permite solo `IDLE` (sentado) o suspendido.
- **No I/O de red sin autorización:** Confirmación manual obligatoria (`CONFIRMAR` en prompt).

---

## Estructura de Fases del Proyecto

### Fase 1: Diseño Arquitectónico (Completado)

- Especificación `Investigacion.md` con análisis de requerimientos.
- Definición de máquina de estados, patrones (Singleton, Strategy), topología DDS.

### Fase 2: Codificación Modular (Completado)

Módulos implementados:

| Módulo | Ubicación | Responsabilidad |
|--------|-----------|-----------------|
| `RobotHardwareAPI` | `src/hardware/` | Singleton wrapper para SDK Unitree (Move, Euler, Damp). |
| `ConversationManager` | `src/interaction/` | Strategy para STT/LLM/TTS con fallback cloud→local. |
| `TourOrchestrator` | `src/core/` | Máquina de estados async (AsyncEngine). |
| `VisionProcessor` | `src/vision/` | Detector AprilTag + pose via solvePnP. |
| `AsyncNav2Bridge` | `src/navigation/` | Interfaz asíncrona Nav2 + clamping /cmd_vel + inyección AMCL. |
| `APIServer` | `src/api/` | FastAPI endpoints para /tour/start, /tour/status. |
| `main.py` | `./` | Bootstrap con DI, signal handling, graceful shutdown. |

### Fase 3: Testing e Integración (Completado)

- **SITL Mocks:** `tests/mocks/{mock_unitree_sdk.py, mock_ros2.py}`.
- **Suites de Integración:** `tests/integration/{test_tour_orchestrator.py, test_hardware_api.py, test_vision_processor.py, test_api_server.py}`.
- **Estado:** 7/7 tests SITL + 3/3 tests API passing.

### Fase 4: HIL Testing (en Ejecución)

- **Preparación:** Protocolo `docs/HIL_TESTING_PROTOCOL.md` con 6 fases (Fase -1, Fase -0 y Fase 0 a Fase 4).
- **Scripts de Despliegue:**
  - `scripts/deploy.sh` (air-gapped rsync-over-SSH).
  - `scripts/test_kinematics.py` (smoke test: damp→euler→damp).
  - `scripts/test_audio.py` (validación TTS local).
- **Operación:** `scripts/start_robot.sh` con confirmación obligatoria (L2+R2, L2+A, CONFIRMAR).

---

## Pipeline NLP Híbrido (Edge + Cloud Fallback)

### Arquitectura

**Patrón Strategy:** Tres interfaces (`ISTTStrategy`, `ILLMStrategy`, `ITTSStrategy`) intercambiables.

### Componentes Locales (Edge)

- **STT:** `faster-whisper` con modelo cuantizado int8 + CTranslate2.
- **LLM:** `Ollama` con modelos estratégicos (Llama-3.2, Qwen2.5).
- **TTS:** `piper-tts` con modelos ONNX (ARM-compatible, 22050 Hz mono).

### Cloud (Fallback)

- **Activación:** Timeout en operación local (`asyncio.wait_for` + `TimeoutError`).
- **Hot-swap:** `ConversationManager` muta internamente a `CloudNLPPipeline`.
- **Retorno:** Reintentos posteriores regresan a local si cloud está disponible.

**Justificación:** Latencia de ruta hacia São Paulo (Brasil) provoca picos de 37-40 ms; hot-swap del núcleo de inferencia permite transparencia operativa.

---

## Entorno Virtual y Dependencias

### Setup Local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Dependencias Críticas

- `python-statemachine` (>=2.3.0): AsyncEngine para orquestador.
- `fastapi`, `uvicorn`: API REST + background tasks.
- `opencv-python`, `numpy`: Visión y álgebra.
- `pytest`, `pytest-asyncio`: Testing SITL.
- `sounddevice`, `faster-whisper`, `piper-tts`: NLP local.

**Nota:** `unitree_sdk2_python` (repo; paquete importable: `unitree_sdk2py`) y `rclpy` no están en `requirements.txt` (compilación local requerida post-deploy).

---

## Checklist Pre-Deployment

- [ ] Despliegue `scripts/deploy.sh` ejecutado sin errores.
- [ ] Compilación `unitree_sdk2_python` verificada en robot (`import unitree_sdk2py`).
- [ ] Prueba acústica (`scripts/test_audio.py`) pasada.
- [ ] Smoke cinemático (`scripts/test_kinematics.py`) en marco protector exitoso.
- [ ] Control remoto: baterías confirmadas, vinculación verificada.
- [ ] Zona de pruebas: delimitada, sin obstáculos, persona de seguridad asignada.
- [ ] Confirmación manual (CONFIRMAR) lista para `scripts/start_robot.sh`.

---

## Documentación Complementaria

- **Especificación Técnica:** [`docs/Investigacion.md`](docs/Investigacion.md)
- **Protocolo HIL:** [`docs/HIL_TESTING_PROTOCOL.md`](docs/HIL_TESTING_PROTOCOL.md)

---

**Última Actualización:** Marzo 2026  
**Versión:** 1.0 MVP — Unitree G1 EDU  
**Estado:** Listo para HIL en campo.
