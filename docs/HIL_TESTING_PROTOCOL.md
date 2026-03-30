# HIL Testing Protocol — Unitree G1 EDU

Guía de ejecución operacional para despliegue y validación en hardware físico del robot humanoide Unitree G1 EDU.

---

## Fase -1: Verificacion de Hardware Periferico y Red

### Objetivo

Garantizar integridad mecanica y disponibilidad del plano de red antes de energizar el robot.

### Paso 1: Aseguramiento mecanico del AP externo

1. Verificar que el Access Point externo (modo Wireless Bridge) este fijado con soporte mecanico y bridas anti-vibracion.
2. Confirmar que el cable RJ45 hacia el robot tiene alivio de tension y no puede desconectarse por traccion accidental.
3. Validar que conectores RJ45 y alimentacion del AP no interfieren con articulaciones, arneses o trayectoria de operador.

### Paso 2: Verificacion de alimentacion electrica independiente del AP

1. Confirmar que el AP se alimenta desde fuente independiente (UPS o fuente DC dedicada), no desde buses auxiliares inestables del robot.
2. Verificar estado de LEDs de enlace WAN/LAN y estabilidad de energia por al menos 60 segundos.
3. Simular microcorte en la fuente del AP solo si existe redundancia disponible; confirmar que procedimiento de recuperacion esta definido antes de continuar.

### Paso 3: Confirmacion visual de no obstruccion de sensores

1. Inspeccionar que AP, cableado y fijaciones no bloqueen el campo de vision de la camara D435i.
2. Verificar que no exista obstruccion en el volumen de barrido del LiDAR MID360.
3. Confirmar que no haya reflejos especulares o sombras permanentes introducidas por el hardware de red frente a los sensores.

### Criterio de aprobacion de fase

- AP externo asegurado mecanicamente.
- Alimentacion independiente validada.
- LiDAR MID360 y camara D435i libres de obstruccion.

---

## Fase -0: Inicializacion Electrica y Postural

### Despeje de Interferencias Wi-Fi

1. Verificar que la sala de pruebas esté libre de fuentes de interferencia electromagnética:
   - Desactivar redes Wi-Fi adyacentes no relacionadas con el proyecto.
   - Apagar o alejar equipos Bluetooth, teléfonos móviles cercanos.
   - Evitar proximidad a líneas de alta tensión, antenas celulares o transmisores.

2. La estabilidad del enlace DDS Unicast depende de la integridad de la conexión inalámbrica a 192.168.123.161.
   - Verificar señal mínima de -67 dBm en la PC de desarrollo.
   - Si degradación, reposicionar AP o usar enlace Ethernet si está disponible.

### Secuencia de Activación de Batería

1. **Inserción de Baterías:**
   - Presionar clips laterales de batería con ambas manos.
   - Insertar batería en compartimento lateral del robot.
   - Asegurar que el **interruptor de encendido esté orientado HACIA LA ESPALDA** del robot.
   - Escuchar "clic" de confirmación de cierre.

2. **Activación de Energía:**
   - Presionar **brevemente** (0.5s) el botón de encendido de la batería (pulso corto).
   - Luego, presionar y **mantener >2 segundos** el mismo botón (pulso largo).
   - Observar que los LEDs de la batería se iluminan secuencialmente.

3. **Espera Obligatoria de Torque Cero:**
   - Aguardar **exactamente 60 segundos** desde el pulso largo.
   - Durante este tiempo, el robot inicializa MCU, sensores e IMU.
   - NO tocar ni mover el robot durante esta ventana de inicialización.
   - Finalizado: todas las articulaciones alcanzan estado de torque cero (robot "flácido").

### Secuencia de Control Remoto Pre-Operativa

Una vez alcanzado torque cero (t=60s post-activación):

1. **Amortiguación Inicial:**
   - Presionar `L1 + A` en el control remoto.
   - Efecto: robot entra en modo de amortiguación elástica.
   - Todas las articulaciones ceden bajo presión de mano (pasividad confirmada).

2. **Transición a Postura Bipedestante:
   - Sujetar los hombros del robot por detrás.
   - Presionar `L1 + UP` en el control remoto.
   - El robot se levanta gradualmente hacia postura erguida.
   - Mantener sujeción hasta alcanzar estabilidad (5-10 segundos).

3. **Activación de Develop Mode:**
   - Presionar `L2 + R2` **simultáneamente** en el control remoto.
   - LED indicador del robot cambia a verde/azul (Develop Mode activado).
   - Este modo permite override de seguridad y comando a nivel de SDK.

4. **Modo de Posición:**
   - Presionar `L2 + A` **simultáneamente** en el control remoto.
   - Robot adopta postura de diagnóstico predeterminada (brazos parcialmente flexionados).
   - Sistema listo para recibir órdenes de software (DDS + SDK).

**Validación:**
Si el robot NO responde a las secuencias de control remoto, presionar `L2 + B` para volver a Amortiguación y repetir desde Paso 1.

---

## Fase 0: Preparación y Transferencia de Código

### Conectividad

1. Conectar PC de desarrollo a la red LAN aislada del Unitree G1 mediante AP externo en modo Wireless Bridge conectado al RJ45 del robot.
   - **Obtener SSID del Access Point y contraseña SSH del usuario `unitree` desde la Guía de Desarrollo Secundario (Developer Guide) o desde la etiqueta física adherida al robot.**
   - Verificar conectividad: `ping 192.168.123.161` (módulo de locomoción).
   - Verificar conectividad: `ping 192.168.123.164` (companion PC interno, si existe).

2. Obtener acceso SSH a la companion PC o directamente al módulo de locomoción.
   - Usuario por defecto: `unitree`.
   - Contraseña: Obtener desde Guía de Desarrollo Secundario o etiqueta física del robot.
   - **(Nota de campo: En sistemas Unitree de fábrica, el password suele ser `123` para el usuario `unitree`, pendiente de validación física).**

### Despliegue Air-Gapped

```bash
# Desde la PC de desarrollo (entorno con bash, ej. Git Bash, WSL, Linux)
cd /ruta/al/proyecto/RobotHumanoide
chmod +x scripts/deploy.sh

# Transferir codebase mediante rsync-over-SSH
./scripts/deploy.sh
```

**Parámetros de override:**
- `ROBOT_USER=unitree` (usuario remoto, por defecto)
- `ROBOT_IP=192.168.123.161` (IP del robot, por defecto)
- Destino remoto: `/home/unitree/robot_guide_app` (por defecto)

Ejemplo con parámetros personalizados:
```bash
ROBOT_USER=admin ROBOT_IP=192.168.123.150 ./scripts/deploy.sh
```

**Exclusiones automáticas:** `.venv/`, `__pycache__/`, `.git/`, `tests/`, `.pytest_cache/`.

---

## Fase 1: Preparación del Entorno en el Robot (SSH)

### Acceso Remoto

```bash
ssh unitree@192.168.123.161
```

### Crear Entorno Virtual Python

```bash
# Navegar al directorio de despliegue
cd /home/unitree/robot_guide_app

# Crear y activar venv
python3 -m venv .venv
source .venv/bin/activate

# Actualizar pip
pip install --upgrade pip
```

### Instalar Dependencias

```bash
# Instalar requirements.txt (excluye unitree_sdk2_python y rclpy)
pip install -r requirements.txt
```

**Dependencias principales esperadas:**
- `python-statemachine`
- `fastapi`, `uvicorn`
- `pydantic`
- `opencv-python`
- `numpy`
- `pytest`, `pytest-asyncio`
- `httpx`
- `sounddevice`
- `faster-whisper`
- `piper-tts`

### Compilar unitree_sdk2_python Localmente

El SDK no está disponible en PyPI; debe compilarse desde fuente:

```bash
# Suponer que libs/unitree_sdk2_python-master existe localmente post-deploy
cd /home/unitree/robot_guide_app/libs/unitree_sdk2_python-master

# Compilar con distutils (o seguir instrucciones del README del SDK)
python setup.py install --user
# O:
pip install -e .

# Verificar importación
python -c "import unitree_sdk2py; print('OK')"
```

Si la compilación falla, verificar que estén instaladas las herramientas de compilación (gcc, g++, cmake).

### Verificar Configuración de Red

```bash
# Validar que CYCLONEDDS_URI y RMW_IMPLEMENTATION se pueden leer
cat config/cyclonedds.xml | head -20
```

---

## Fase 2: Validación Acústica Local (TTS)

**Objetivo:** Verificar que el altavoz estéreo de 5W reproduce audio con potencia suficiente.

### Establecer Modelo Piper TTS

```bash
# Desde entorno virtual activado en robot
export PIPER_MODEL_PATH="/path/to/piper/model.onnx"
export PIPER_SAMPLE_RATE=22050
export PIPER_CMD="piper"
```

Si Piper no está instalado:
```bash
pip install piper-tts
# Descargar modelo (ej. español)
# curl -o model.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/es_MX/male/low/model.onnx
```

### Ejecutar Test Acústico

```bash
python scripts/test_audio.py \
  --model-path /path/to/piper/model.onnx \
  --sample-rate 22050 \
  --text "Prueba acustica del Unitree G1 EDU"
```

**Salida esperada:**
```
[OK] Prueba acustica completada. Verifica inteligibilidad y potencia en entorno fisico.
```

**Validación sensorial:**
- Escuchar claramente el mensaje de prueba reproducido por el altavoz.
- Volumen suficiente para ser escuchado a 2 metros de distancia.
- Ausencia de distorsión artefactual o ruido excesivo.

Si el audio no se escucha o está distorsionado:
1. Verificar nivel de volumen ALSA: `amixer get Master`
2. Probar con un archivo WAV conocido: `play test.wav` (si SoX está disponible).
3. Revisar conexión física del altavoz en la interfaz de audio del robot.

---

## Fase 3: Sanity Check Cinemático

### ⚠️ ADVERTENCIA CRÍTICA DE SEGURIDAD

**El robot debe estar suspendido en el marco protector de pruebas.** No ejecutar esta fase en tierra firme ni sin supervisión directa.

- Colocar el Unitree G1 EDU en el marco de seguridad horizontal.
- Asegurar que las extremidades no toquen el suelo.
- Mantener al menos una persona vigilando al robot durante toda la prueba.
- Tener acceso inmediato al control remoto con L1+A (hardstop) disponible.

### Ejecutar Test de Cinemática

```bash
# Desde .venv activado en robot
python scripts/test_kinematics.py
```

**Secuencia esperada:**
1. Inicializar `RobotHardwareAPI`.
2. Ejecutar `Damp()` inmediatamente (estado elástico de seguridad).
3. Aguardar 2 segundos.
4. Ejecutar `Euler(roll=0.0, pitch=0.1, yaw=0.0)` (inclinación leve de pitch).
5. Aguardar 2 segundos (observar movimiento de torso).
6. Ejecutar `Damp()` nuevamente (retorno a estado elástico).
7. Finalizar con éxito.

**Observaciones a registrar:**
- El torso se inclina hacia adelante (pitch positivo ~5 grados).
- Movimiento es suave y controlado, sin sacudidas.
- Respuesta a comandos dentro de 200 ms.
- Sin ruidos anómalos en servos.

**En caso de fallo:**
- El script ejecutará `Damp()` automáticamente en el bloque `finally`.
- Si el robot no responde, presionar L1+A en el control remoto para hardstop.
- Revisar logs de error: `echo $?` (código de salida del script).

---

## Fase 4: Ejecución del Orquestador Completo

### Precondiciones

- ✓ Validación acústica completada sin errores.
- ✓ Test cinemático verificó respuesta de locomoción.
- ✓ Ambiente físico preparado para movimiento autonomizado (arena o zona delimitada).
- ✓ Control remoto del robot en mano del operador.

### Iniciar Sistema

```bash
# Desde el directorio raíz del proyecto en el robot
source .venv/bin/activate
chmod +x scripts/start_robot.sh
./scripts/start_robot.sh
```

### Prompt Bloqueante de Confirmación

El script detendrá la ejecución y mostrará:

```
[SAFETY] Confirmacion operativa requerida antes de iniciar el sistema.
[SAFETY] 1) Presiona L2 + R2 para entrar en Develop Mode.
[SAFETY] 2) Luego presiona L2 + A para habilitar Position Mode.
Escribe CONFIRMAR para continuar o cualquier otra tecla para abortar:
```

**Procedimiento obligatorio:**
1. Verificar que el robot esté en posición neutral segura.
2. Con el control remoto en mano:
   - Presionar **L2 + R2** simultáneamente (Develop Mode). Esperar confirmación visual (LED del robot).
   - Presionar **L2 + A** simultáneamente (Position Mode).
3. Escribir en la terminal: `CONFIRMAR` (exactamente, mayúsculas).
4. Presionar Enter.

Si se escribe cualquier otra cadena o se presiona Ctrl+C, el script abortará con `exit 1` sin inicializar el robot.

### Monitoreo en Tiempo Real

Una vez autorizado, el orquestador iniciará:
- Carga del entorno ROS2 y CycloneDDS.
- Publicación de `/initialpose` en AMCL.
- Espera de comandos de tour desde la API FastAPI.
- Bucle de telemetría de sensores (IMU, odometría).

**Logs esperados:**
```
[INFO] TourOrchestrator iniciado en estado IDLE.
[INFO] AsyncNav2Bridge activo. Nav2 disponible.
[CRITICAL] OVERRIDE DE SEGURIDAD: L1 + A en el control remoto...
```

### Interrupción de Emergencia

Para detener el orquestador:
- **Opción 1 (Recomendada):** Presionar **L1 + A** en el control remoto (hardstop del hardware).
- **Opción 2:** Presionar **Ctrl+C** en la terminal (graceful shutdown con `Damp()`).

El script forward_shutdown_signal capturará SIGINT/SIGTERM y ejecutará `Damp()` automáticamente.

---

## Protocolo de Emergencia

### Hardware Override — L1 + A

**Función:** Fuerza la ejecución inmediata de `Damp()` en el control de bajo nivel del robot, desacoplando todos los actuadores a estado elástico pasivo.

**Cuándo usar:**
- Robot en comportamiento inestable o impredecible.
- Pérdida de comunicación DDS (sin recepción de comandos API).
- Riesgo inminente de caída o colisión.
- **Cualquier situación que requiera parada inmediata.**

**Procedimiento:**
1. Mantener L1 presionado.
2. Presionar A mientras L1 está activo.
3. Liberar ambas teclas.
4. Observar que el robot transiciona a postura elástica (las articulaciones ceden bajo presión).

### Prohibición de Operación Dual

**Está estrictamente prohibido operar simultáneamente:**
- Control remoto manual (comando de movimiento directo).
- API del orquestador (`/tour/start` en FastAPI).

Conflictos en comandos de locomoción pueden resultar en comportamiento no determinístico o inestable.

**Regla operativa:**
- Durante estado `NAVIGATING` del orquestador: **ceder control total a la API**, retirar la mano del control remoto excepto para emergencias (L1+A).
- Si intervenir manualmente: abortar tour primero, limpiar estado del orquestador, luego operar control remoto.

---

## Checklist de Despliegue

- [ ] PC conectada a LAN aislada del Unitree G1.
- [ ] SSH accesible a `unitree@192.168.123.161`.
- [ ] `scripts/deploy.sh` ejecutado sin errores.
- [ ] Entorno virtual Python creado y renovado en robot.
- [ ] `requirements.txt` instalado sin fallos.
- [ ] `unitree_sdk2_python` compilado e importable (`import unitree_sdk2py`).
- [ ] Validación acústica (`test_audio.py`) completada exitosamente.
- [ ] Test cinemático (`test_kinematics.py`) ejecutado en marco protector.
- [ ] Control remoto accesible y con baterías verificadas.
- [ ] Zona de pruebas delimitada y sin obstáculos.
- [ ] Persona de seguridad designada con acceso a L1+A.
- [ ] `scripts/start_robot.sh` ejecutado y confirmación MANUAL validada.

---

## Apagado Mecánico Seguro

### Procedimiento Obligatorio de Shutdown

**⚠️ CRÍTICO: Nunca apagar el robot estando de pie sin soporte.**

#### Opción A: Apagado Sentado (Recomendado)

1. **Preparación:**
   - Colocar una silla estable frente al robot.
   - Verificar que el área esté despejada y sin obstáculos.

2. **Transición a Postura Sentada:**
   - Sujetar la parte trasera de ambos hombros del robot.
   - Presionar `L1 + IZQ` (LEFT) en el control remoto.
   - Mantener sujeción mientras el robot se sienta gradualmente.
   - Robot descansa de forma estable en la silla (confirmación táctil).

3. **Activación de Amortiguación:**
   - Presionar `L1 + A` en el control remoto.
   - Robot entra en estado elástico (todas las articulaciones ceden).

4. **Corte de Energía:**
   - Presionar **brevemente** (0.5s) el botón de encendido de la batería.
   - Luego, presionar y **mantener >2 segundos** el mismo botón.
   - Robot se apaga completamente (LEDs de batería se oscurecen).

#### Opción B: Apagado Suspendido (Marco Protector)

1. **Verificación de Suspensión:**
   - Confirmar que el robot está completamente suspendido en el marco protector.
   - Asegurar que la cuerda de suspensión está tensa y ambas patas NO tocan el suelo.

2. **Activación de Amortiguación:**
   - Presionar `L1 + A` en el control remoto.
   - Robot entra en estado elástico.

3. **Corte de Energía:**
   - Presionar **brevemente** el botón de encendido de la batería.
   - Luego, presionar y **mantener >2 segundos** el mismo botón.
   - Robot se apaga (verificar oscurecimiento de LEDs de batería).

### Post-Apagado

- **Ajustar Articulaciones:** Posicionar brazos y pies en configuración de reposo recomendada.
- **Almacenamiento Prolongado:** Si el robot no se usará por >1 semana, retirar la batería presionando clips laterales con ambas manos.
- **Remoción de Batería:** Asegurar que el robot esté completamente apagado (sin LEDs activos) antes de retirar batería.

### Prohibiciones Explícitas

- **PROHIBIDO apagar el robot estando de pie sin soporte.**
- **PROHIBIDO cortar energía mediante disruptor auxiliar (solo usar botón de batería).**
- **PROHIBIDO desconectar cables DDS/ROS2 mientras el robot está encendido (causa comportamiento inestable).**

---

**Fecha de Actualización:** Marzo 2026  
**Versión del Protocolo:** 1.0 — MVP Unitree G1 EDU  
**Estado:** Listo para despliegue en campo.
