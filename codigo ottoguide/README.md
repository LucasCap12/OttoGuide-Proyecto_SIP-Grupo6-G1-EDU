# OttoGuide MVP · Capa de Aplicación y SRE

## Alcance
Esta raíz concentra el código ejecutable, pruebas, configuración operativa y artefactos de despliegue para ejecución local, HIL y SITL.

## Topología canónica del repositorio
```text
OttoGuide-Proyecto_SIP-Grupo6-UADE/
├── codigo ottoguide/
├── documentacion general del proyecto/
└── planificacion/
```

## Árbol de directorios
```text
codigo ottoguide/
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── api/
├── config/
├── data/
├── hardware/
├── libs/
├── logs/
├── main.py
├── maps/
├── pyproject.toml
├── README_SITL_3D.md
├── resources/
├── scripts/
├── src/
│   ├── api/
│   ├── core/
│   ├── hardware/
│   ├── interaction/
│   ├── navigation/
│   └── vision/
├── tests_output.txt
└── tests/
```

## Matriz de responsabilidad por módulo
| Módulo | Responsabilidad primaria | Interfaces principales | Dependencias críticas |
|---|---|---|---|
| api | Exposición HTTP de control y observabilidad del orquestador | `src/api/server.py`, FastAPI, endpoints `/tour/start`, `/tour/pause`, `/emergency`, `/status` | `core`, `navigation` |
| core | FSM, orquestación de tour, coordinación inter-módulo | `TourOrchestrator`, contexto de tour y transiciones | `hardware`, `interaction`, `navigation`, `vision` |
| hardware | Abstracción de actuadores y estado físico robot | Adaptadores `real`, `sim`, `mock` | SDK Unitree, capa `interface` |
| interaction | Gestión conversacional, TTS/STT y flujo de interacción | `ConversationManager`, buffers de audio, metadata de sesión | `core`, recursos de audio |
| navigation | Integración ROS2 Nav2 y ejecución de waypoints | `AsyncNav2Bridge`, `NavWaypoint`, estado de navegación | ROS2, `nav2_simple_commander` |
| vision | Percepción visual y estimación de pose | `VisionProcessor`, `PoseEstimate` | OpenCV, modelos y calibración |

## Comandos de inicialización HIL y SITL
| Escenario | Comando | Resultado esperado |
|---|---|---|
| Preflight HIL | `bash scripts/preflight_check.sh` | Verificación de dependencias, red y prerrequisitos HIL |
| Bootstrap HIL | `bash scripts/bootstrap_hil.sh` | Inicialización integral para entorno Hardware-In-the-Loop |
| Arranque robot | `bash scripts/start_robot.sh` | Lanzamiento del stack principal OttoGuide |
| Validación entorno remoto | `bash scripts/verify_remote_env.sh` | Diagnóstico de host remoto y conectividad |
| Lanzamiento SITL | `bash scripts/launch_sitl_tmux.sh` | Simulación software-in-the-loop en sesión tmux |
| Levantado Docker | `docker compose up --build` | Entorno containerizado con topología definida |

## Reglas de operación SRE
| Regla | Estado |
|---|---|
| `docker-compose.yml` conserva `network_mode: "host"` | Vigente |
| FSM del orquestador sin alteración funcional | Vigente |
| Módulos legacy `api_server.py` y `navigation_manager.py` purgados | Aplicado |
| Ruta canónica de API en producción: `src/api/server.py` | Aplicado |
| Ruta canónica de navegación en producción: `src/navigation/nav2_bridge.py` | Aplicado |