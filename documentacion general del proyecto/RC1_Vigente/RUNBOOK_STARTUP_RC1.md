# Runbook Startup RC1 - OttoGuide HIL

## 1. Objetivo

Definir la secuencia operativa oficial para iniciar OttoGuide MVP en modo HIL sobre Unitree G1 EDU 8, con validaciones de seguridad y criterios GO/NO-GO.

## 2. Secuencia paso a paso

### Paso 1 - Preparacion de release (estacion de desarrollo)

1. Ejecutar limpieza previa.
2. Congelar dependencias.
3. Generar corte de release.

Scripts:

- scripts/pre_deploy_cleanup.sh
- scripts/freeze_dependencies.sh
- scripts/cut_release.sh

### Paso 2 - Transferencia al target

Sincronizar codigo a companion PC:

- scripts/deploy_to_companion.sh

### Paso 3 - Bootstrap de companion

Inicializar entorno del target:

- scripts/bootstrap_target.sh

### Paso 4 - Health check tecnico

Validar prerequisitos de conectividad, Ollama y mapa:

- scripts/sre_health_check.py

### Paso 5 - Gate preflight obligatorio

Ejecutar barrera de entorno antes de arrancar core:

- scripts/preflight_check.sh

Condicion minima: sin fallos criticos.

### Paso 6 - Validacion entorno HIL

Ejecutar verificacion extendida remota:

- scripts/verify_remote_env.sh

Reglas:

- Exit 0: continuar.
- Exit 1: bloqueo total.
- Exit 2: solo con override operatorio explicito.

### Paso 7 - Confirmacion de seguridad en hardware

Confirmar manualmente en mando:

1. Develop Mode activo.
2. Position Mode activo.

Sin esta confirmacion no se autoriza arranque.

### Paso 8 - Arranque del sistema

Opciones recomendadas:

- Supervisor integral: scripts/start_robot.sh
- Orquestacion E2E: scripts/mvp_master_run.sh

### Paso 9 - Validacion post-arranque

1. Verificar estado por endpoint /status.
2. Confirmar stream WebSocket /ws/telemetry.
3. Confirmar visibilidad de dashboard web.

### Paso 10 - Contingencia y parada segura

1. API: POST /emergency.
2. Hardware: comando manual L1+A.

## 3. Matriz GO/NO-GO

| Validacion | GO | NO-GO |
|---|---|---|
| Preflight | Exit 0 | Error critico |
| Entorno HIL | Exit 0 / override controlado | Exit 1 |
| Estado Ollama | Responde y modelo disponible | Timeout o modelo ausente |
| Red robotica | Endpoint DDS alcanzable | Sin conectividad |
| Seguridad operatoria | Confirmaciones manuales completas | Confirmacion ausente |

## 4. Nota operacional

Para RC1, esta secuencia reemplaza cualquier runbook basado en simulacion como fuente principal de operacion.
