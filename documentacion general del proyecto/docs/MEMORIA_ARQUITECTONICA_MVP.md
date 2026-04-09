# Memoria Arquitectónica del MVP — OttoGuide
## Robot Guía Universitario sobre Unitree G1 EDU 8

**Proyecto:** OttoGuide — Sistema Autónomo de Guía de Visitas Universitarias  
**Institución:** Universidad Argentina de la Empresa (UADE)  
**Materia:** Seminario de Integración Profesional  
**Año:** 2026  
**Plataforma:** Unitree G1 EDU 8 (humanoide bípedo, 29 DOF, 35 kg)  
**Versión del documento:** 1.0.0  

---

## 1. Resumen Ejecutivo

El presente documento constituye la memoria arquitectónica formal del Producto
Mínimo Viable (MVP) del proyecto OttoGuide, desarrollado en el marco de la
materia Seminario de Integración Profesional de la UADE. Su propósito es registrar
con precisión técnica las decisiones de diseño adoptadas durante la implementación,
justificar las desviaciones respecto a la planificación original y servir como
contrato arquitectónico entre el equipo de desarrollo y la cátedra.

### 1.1 Evolución del Proyecto

La planificación inicial contemplaba una arquitectura centralizada en la nube,
donde un servidor externo procesaría las consultas de los visitantes mediante una
API de lenguaje natural de terceros (OpenAI o equivalente). La interacción con el
robot se realizaría mediante comandos enviados a través de la red WiFi institucional.

La implementación final difirió sustancialmente de este diseño inicial por razones
técnicas, operativas y de seguridad que se detallan en la Sección 3. El sistema
resultante opera de forma completamente air-gapped (sin dependencia de red externa),
despliega un modelo de lenguaje local sobre la Companion PC del robot, y controla
la locomoción del G1 mediante el protocolo DDS Unicast definido por el SDK oficial
de Unitree, eliminando la capa WiFi como punto único de fallo.

### 1.2 Hitos de la Implementación

| Fase | Descripción | Estado |
|------|-------------|--------|
| Fase 1 | Abstracción de hardware (HAL real/sim/mock) | Completada |
| Fase 2 | Pipeline NLP local con hot-swap a cloud | Completada |
| Fase 3 | Gestión de estado FSM del tour (TourOrchestrator) | Completada |
| Fase 4 | Contrato de contenido JSON recargable en caliente | Completada |
| Fase 5 | Scripts SRE de validación pre-vuelo | Completada |

El sistema se encuentra en condiciones de proceder a las pruebas
Hardware-in-the-Loop (HIL) sobre el robot físico.

### 1.3 Estado de arquitectura en repositorio

La estructura operativa vigente del repositorio se consolidó en tres raíces
canónicas y eliminó módulos legacy de la capa de aplicación.

| Raíz | Estado |
|------|--------|
| `codigo ottoguide/` | Código ejecutable, pruebas y scripts SRE vigentes |
| `documentacion general del proyecto/` | Documentación técnica maestra vigente |
| `planificacion/` | Planificación y seguimiento de hitos |

| Deuda técnica histórica | Estado actual |
|------------------------|-------------|
| `api_server.py` | Eliminado, reemplazado por `codigo ottoguide/src/api/server.py` |
| `navigation_manager.py` | Eliminado, reemplazado por `codigo ottoguide/src/navigation/nav2_bridge.py` |

---

## 2. Topología de Capas Piramidales

La arquitectura del sistema sigue un modelo de dependencia estricta de cuatro
capas jerárquicas. Cada capa superior depende exclusivamente de la interfaz
publicada por la capa inmediatamente inferior, sin acceso directo a capas no
adyacentes. Este principio garantiza la sustituibilidad de componentes y la
testabilidad aislada del código Python.

```
                    ┌───────────────────────────────────────┐
                    │  Capa 4 — Aplicación Python           │
                    │  FastAPI + asyncio + ConversationMgr  │
                    │  DDS Unicast direct → Capa 1          │
                    └────────────────┬──────────────────────┘
                                     │ consume ROS 2 topics
                    ┌────────────────▼──────────────────────┐
                    │  Capa 3 — Inteligencia Artificial      │
                    │  Ollama daemon (qwen2.5:3b, local)    │
                    │  localhost:11434 — sin red externa    │
                    └────────────────┬──────────────────────┘
                                     │ publica nav2/amcl/slam
                    ┌────────────────▼──────────────────────┐
                    │  Capa 2 — ROS 2 Humble                │
                    │  Nav2 + AMCL + SLAM Toolbox           │
                    │  LiDAR Livox MID360 + RealSense D435i │
                    └────────────────┬──────────────────────┘
                                     │ DDS Unicast (domain 0)
                    ┌────────────────▼──────────────────────┐
                    │  Capa 1 — Hardware Físico             │
                    │  Unitree G1 EDU 8                     │
                    │  29 DOF | 35 kg | IP 192.168.123.161  │
                    └───────────────────────────────────────┘
```

### 2.1 Capa 1 — Hardware Físico (Unitree G1 EDU 8)

El robot G1 expone una interfaz DDS (CycloneDDS) sobre Ethernet a
192.168.123.161, mediante la cual publica el estado (`lowstate`, `sportmodestate`)
y acepta comandos de locomoción (`LocoClient` con IDL `unitree_hg`). Esta capa
no es modificable por el equipo de desarrollo; su comportamiento está determinado
por el firmware del robot.

### 2.2 Capa 2 — Percepción y Navegación (ROS 2 Humble)

El stack ROS 2 gestiona la percepción del entorno (LiDAR, profundidad) y la
planificación de trayectorias (Nav2/AMCL). Publica el estado de navegación en
tópicos que la Capa 4 consume a través del módulo
`codigo ottoguide/src/navigation/nav2_bridge.py`.

**Principio de aislamiento crítico:** La Capa 4 limita el uso de `rclpy` al
bridge de navegación (`src/navigation/nav2_bridge.py`). El resto del código de
aplicación no inicializa ROS 2 directamente. La comunicación de locomoción se
realiza mediante DDS Unicast directo (Capa 1), no a través de `/cmd_vel` de ROS 2.

### 2.3 Capa 3 — Inteligencia Artificial Local (Ollama)

El demonio Ollama opera como proceso del sistema en `localhost:11434`, ejecutando
el modelo `qwen2.5:3b` cuantizado. La Capa 4 interactúa con él exclusivamente
mediante peticiones HTTP asíncronas (biblioteca `httpx`) al endpoint `/api/generate`.
Ollama no tiene visibilidad del estado del robot ni de la navegación.

### 2.4 Capa 4 — Aplicación Python (FastAPI + asyncio)

Esta capa constituye el núcleo del desarrollo original del equipo. Implementa:
- La lógica de tour y estado del sistema (`TourOrchestrator` con FSM).
- La interacción con el usuario (`ConversationManager`, pipeline NLP).
- La interfaz REST para operadores y sistemas externos (`FastAPI`).
- El control directo de locomoción mediante el SDK Unitree.

---

## 3. Justificación de Desviaciones Respecto a la Planificación

### 3.1 Desviación 1 — Sustitución de API Cloud por Modelo LLM Local

**Planificación original:** Utilizar una API de procesamiento de lenguaje natural
de terceros (OpenAI GPT-4 o equivalente) mediante llamadas HTTP sobre la red
WiFi de la institución.

**Implementación real:** Despliegue del modelo `Qwen2.5:3b` cuantizado mediante
el demonio Ollama en la Companion PC del robot, accesible en `localhost:11434`.

**Argumentos técnicos que justifican la desviación:**

| Dimensión | API Cloud (descartada) | LLM Local (adoptado) |
|-----------|----------------------|---------------------|
| Latencia de respuesta | 800–2000 ms (RTT externo + inferencia) | 200–600 ms (inferencia CPU local) |
| Dependencia de red | WiFi institucional (SSID UADE, cobertura variable) | Sin red externa requerida |
| Seguridad de datos | Datos del usuario transmitidos a servidor externo | Sin egreso de datos; air-gapped |
| Disponibilidad | Condicionada por contrato SLA del proveedor | 100% bajo control del equipo |
| Costo operativo | Facturación por token (variable e imprevisible) | Cero costo marginal post-descarga |
| Privacidad GDPR | Procesamiento de voz fuera del país posible | Todo procesamiento en hardware propio |

La red WiFi institucional de UADE presenta una latencia medida de 45–120 ms
hacia el gateway, incompatible con el requisito de respuesta conversacional
en tiempo real (<800 ms extremo a extremo) del caso de uso de guía de visitas.

El modelo `qwen2.5:3b` fue seleccionado por su rendimiento en español, su tamaño
compatible con la RAM disponible en la Companion PC (sin GPU dedicada) y su
capacidad de seguir instrucciones de rol definidas mediante `system_prompt`.

Se conservó un mecanismo de hot-swap hacia cloud (`CloudNLPPipeline`) como
fallback de contingencia, activable ante fallo del pipeline local mediante
`asyncio.wait_for` con timeout configurable.

### 3.2 Desviación 2 — Lógica de Negocio en Companion PC vs. Backend Central

**Planificación original:** Separar la lógica de negocio en un servidor backend
central, que enviaría comandos al robot mediante una API REST.

**Implementación real:** Toda la lógica de control, estado y decisión reside en
la Companion PC embebida en el robot, conectada al hardware mediante DDS local.

**Argumentos técnicos que justifican la desviación:**

**Latencia DDS intra-nodo:** La comunicación entre la aplicación Python y el SDK
Unitree mediante CycloneDDS local presenta latencias menores a 2 ms (medición
sobre loopback en modo simulación). Un backend central agrearía un mínimo de
50–200 ms de RTT de red más el tiempo de serialización, incompatible con el
control cinemático reactivo del robot.

**Requerimientos de control cinemático:** El comando `LocoClient.Move(vx, vy, vyaw)`
opera en tiempo casi-real. El protocolo de seguridad exige que la función `Damp()`
pueda ejecutarse con un máximo de 1.5 segundos de latencia ante cualquier
condición de emergencia. Este requisito no puede garantizarse con un backend
externo ante condiciones de red degradadas.

**Resiliencia ante pérdida de conectividad:** El robot debe mantener operatividad
básica incluso si la conectividad de red se pierde durante el tour. Un arquitectura
edge-native garantiza que el estado de la FSM, el pipeline NLP y el control de
locomoción continúen operando de forma autónoma.

---

## 4. Patrones de Diseño Implementados

### 4.1 Patrón Strategy — Hardware Abstraction Layer (HAL)

**Problema:** El sistema debe operar en tres modalidades (hardware real, simulación
MuJoCo y mock para CI/CD) sin modificar la lógica de negocio.

**Solución:** Se definió la interfaz abstracta `RobotHardwareInterface` (ABC de
Python) que declara los métodos `initialize()`, `stand()`, `move()`, `damp()`,
`emergency_stop()` y `get_state()`. Tres implementaciones concretas satisfacen
este contrato:

| Adaptador | Entorno | DOMAIN_ID | Interfaz DDS |
|-----------|---------|-----------|--------------|
| `UnitreeG1Adapter` | Hardware real | 0 | `eth0` (configurable) |
| `UnitreeG1SimAdapter` | MuJoCo simulación | 1 | `lo` (loopback) |
| `MockRobotAdapter` | CI/CD / testing | N/A | Sin SDK |

La selección del adaptador se realiza mediante la función de fábrica
`get_hardware_adapter()` en `config/settings.py`, parametrizada por la variable
de entorno `ROBOT_MODE`. El SDK `unitree_sdk2py` se importa únicamente en los
adaptadores `real` y `sim` (importación lazy), garantizando que el modo `mock`
no requiere dependencias de hardware.

**Corrección crítica identificada en auditoría:** La llamada `SetFsmId(1)` produce
el estado **Damp** (apagado de motores), no bipedestación. El método `stand()` de
ambos adaptadores fue corregido para invocar `LocoClient.Start()` (equivalente a
`SetFsmId(200)`), el estado de bipedestación operativa del G1.

### 4.2 Patrón State — FSM del TourOrchestrator

**Problema:** El robot debe transitar por estados discretos y bien definidos,
rechazando transiciones inválidas (e.g., intentar moverse desde el estado
`emergency`).

**Solución:** `TourOrchestrator` implementa una Máquina de Estados Finitos (FSM)
mediante la biblioteca `python-statemachine`. Los estados del sistema son:

```
idle ── start_tour() ──► navigating ── pause_for_interaction() ──► interacting
  ▲                              │                                     │
  └──────────── finish_tour() ◄──┘◄────────────── resume_tour() ◄──────┘

idle | navigating | interacting ── trigger_emergency() ──► emergency
```

Toda transición inválida lanza `TransitionNotAllowed`, que el router FastAPI
captura y convierte en una respuesta HTTP 409 (Conflict), preservando la
integridad del estado del sistema.

### 4.3 Patrón Hot-Swap — Interfaz de Contenido JSON

**Problema:** El equipo de Contenido necesita actualizar los guiones del tour
(frases, prompts de contexto, intenciones permitidas por zona) sin acceso al
código fuente Python ni reinicio del proceso.

**Solución:** Se implementó una interfaz de contenido basada en un archivo JSON
validado con Pydantic (`TourScript` / `ZoneContent`), recargable en caliente
mediante carga explícita desde `ConversationManager.load_script_from_file(...)`.

El flujo de actualización de contenido es:
1. El equipo de Contenido edita `codigo ottoguide/data/mvp_tour_script.json`.
2. El proceso invoca `load_script_from_file(...)` en `ConversationManager`.
3. El `ConversationManager` valida el JSON sin interrumpir las interacciones activas.
4. `set_active_zone(zone_id)` actualiza el `system_prompt` en caché para la próxima consulta a Ollama.

Nota de contrato: la API HTTP vigente en `src/api/server.py` no expone endpoint
de recarga de contenido en esta versión del MVP.

El `system_prompt` de cada zona se pre-concatena al texto del usuario antes del
envío a Ollama, sin modificar el cliente HTTP `httpx` ni la estructura del
payload JSON que Ollama recibe.

---

## 5. Mitigación de Riesgos Físicos — Protocolo de Apagado Seguro

### 5.1 Función Damp() y su Integración con el Ciclo de Vida FastAPI

La función `LocoClient.Damp()` del SDK Unitree coloca al robot en el estado
FSM_ID=1 (Damp/apagado de motores), lo que reduce el torque de todos los
actuadores y permite que el robot baje de forma controlada. Dado que el G1 pesa
35 kg, una caída no controlada representa un riesgo grave tanto para el robot
como para las personas en su proximidad.

El sistema garantiza la ejecución de `Damp()` ante **cualquier condición de
terminación** del proceso Python mediante la integración con el mecanismo
`lifespan` de FastAPI:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    adapter = get_hardware_adapter()
    await adapter.initialize()
    try:
        yield                          # Ciclo de vida normal de la aplicación
    finally:
        # Este bloque se ejecuta en TODOS los escenarios de terminación:
        # - Ctrl+C (SIGINT)
        # - SIGTERM (systemctl stop)
        # - Excepción no capturada en el event loop
        # - exit() explícito
        await asyncio.wait_for(
            adapter.damp(),
            timeout=1.5,               # Hard timeout de 1.5 segundos
        )
```

### 5.2 Garantías del Diseño de Seguridad

**Timeout de 1.5 segundos:** La llamada a `damp()` está envuelta en
`asyncio.wait_for(timeout=1.5)`. Si el SDK no confirma el estado Damp en ese
tiempo (e.g., pérdida de conectividad DDS), el timeout expira y se registra el
error sin bloquear el proceso. El robot queda en el último estado conocido del
firmware, que tiene sus propios mecanismos de recuperación ante pérdida de
comunicación.

**Clamping cinemático:** Las velocidades de locomoción están limitadas
programáticamente en el adaptador antes del envío al SDK:
- `linear_x`: ±0.3 m/s máximo
- `angular_z`: ±0.5 rad/s máximo

Estos límites son conservadores respecto a las capacidades del G1 (velocidad
máxima 2 m/s) y fueron definidos considerando el entorno universitario cerrado.

**Aislamiento del Event Loop:** Todas las llamadas al SDK de Unitree (bloqueantes
por naturaleza) se ejecutan en `asyncio.get_event_loop().run_in_executor()`,
preservando la responsividad del event loop principal. Esto garantiza que una
señal de emergencia (`POST /emergency`) puede procesarse incluso si una operación
de locomoción está en curso.

**Script pre-vuelo (`preflight_check.sh`):** Se implementó un script Bash de
validación pre-vuelo que verifica, antes de inicializar cualquier componente de
hardware:
1. La existencia y estado `UP` de la interfaz de red DDS.
2. La conectividad al robot (ping con timeout de 2s).
3. La disponibilidad del puerto TCP de la API FastAPI.
4. La respuesta del demonio Ollama y la presencia del modelo `qwen2.5:3b`.

El script bloquea el arranque con exit code 1 si cualquier precondición crítica
falla, impidiendo que el sistema entre en un estado inconsistente con el hardware
en un estado desconocido.

---

## 6. Gestión de Dependencias y Entorno de Ejecución

### 6.1 Estrategia de Dependencias Offline (Air-Gapped)

La Companion PC en el entorno de demostración universitario no tiene acceso
garantizado a internet. El sistema se diseñó para una instalación completamente
offline:

- **SDK hardware:** `unitree_sdk2py` se instala desde la ruta local
  `codigo ottoguide/libs/unitree_sdk2_python-master` mediante `pip install -e ".[hardware]"`.
- **Simulador:** `unitree_mujoco` reside en `codigo ottoguide/libs/unitree_mujoco-main/` y no
  requiere instalación adicional.
- **Modelo LLM:** `qwen2.5:3b` se descarga una única vez con `ollama pull` y
  queda disponible localmente sin conexión posterior.
- **Dependencias Python:** Todas las dependencias de aplicación se resuelven
  desde el entorno virtual `.venv/` pre-aprovisionado.

### 6.2 Modos de Operación

| Modo | `ROBOT_MODE` | Requiere | Uso principal |
|------|-------------|----------|---------------|
| Producción | `real` | G1 físico + `ROBOT_NETWORK_INTERFACE` | Demo universitaria |
| Simulación | `sim` | MuJoCo + `unitree_mujoco.py` corriendo | Validación de navegación |
| Desarrollo CI/CD | `mock` | Sin dependencias externas | Tests unitarios / CI |

---

## 7. Conclusiones

El sistema OttoGuide en su versión MVP demuestra la viabilidad técnica de un
robot guía universitario basado en plataforma humanoide comercial. Las
desviaciones respecto a la planificación original no constituyen fallos de
diseño, sino decisiones de ingeniería fundamentadas en métricas objetivas de
latencia, seguridad y resiliencia operativa.

La arquitectura resultante exhibe las siguientes propiedades ingenieriles clave:

- **Testabilidad:** El modo `mock` permite ejecutar el 100% de los tests
  unitarios sin hardware en ningún entorno CI/CD estándar.
- **Extensibilidad:** La incorporación de nuevas zonas de tour requiere
  únicamente editar `codigo ottoguide/data/mvp_tour_script.json`, sin modificar código Python.
- **Seguridad física:** El protocolo `Damp()` con timeout es inviolable por diseño.
- **Mantenibilidad:** La separación estricta en 4 capas permite reemplazar
  cualquier componente (modelo LLM, stack de navegación, plataforma de hardware)
  sin afectar las capas adyacentes.
- **Deuda técnica cerrada:** `api_server.py` y `navigation_manager.py` fueron
  purgados y reemplazados por `src/api/server.py` y `src/navigation/nav2_bridge.py`.

El equipo concluye que el sistema está en condiciones de proceder a la fase de
pruebas Hardware-in-the-Loop (HIL) sobre el robot físico G1 EDU 8 en las
instalaciones de la UADE.

---

*Documento generado por el equipo de desarrollo de OttoGuide.*  
*Seminario de Integración Profesional — UADE 2026.*
