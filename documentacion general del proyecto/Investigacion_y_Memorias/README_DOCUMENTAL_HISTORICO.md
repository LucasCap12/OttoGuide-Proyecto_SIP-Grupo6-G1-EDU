# OttoGuide MVP · Capa de Especificación y Protocolos

## Objetivo
Repositorio documental oficial del proyecto OttoGuide MVP para arquitectura, protocolos de integración, operación HIL y guía funcional.

## Topología canónica de 3 raíces
| Raíz | Rol operativo |
|---|---|
| `codigo ottoguide/` | Código ejecutable, pruebas, scripts y dependencias locales |
| `documentacion general del proyecto/` | Especificaciones, protocolos y memorias técnicas |
| `planificacion/` | Planeamiento, hitos y roadmap del proyecto |

## Índice maestro de documentos
| Documento | Dominio | Uso principal |
|---|---|---|
| G1-Manual-de-usuario-Transcripcion.md | Operación | Referencia funcional y operación del robot Unitree G1 |
| HIL_TESTING_PROTOCOL.md | Validación | Procedimiento de pruebas Hardware-In-the-Loop |
| Investigacion.md | Investigación | Base técnica y decisiones de diseño |
| MEMORIA_ARQUITECTONICA_MVP.md | Arquitectura | Definición de arquitectura del MVP |
| README_simulation.md | Simulación | Alcance y lineamientos de entorno simulado |
| ROS2_INTEGRATION.md | Integración | Acoplamiento ROS2, buses y runtime |

## Arquitectura base
| Capa | Contenido | Resultado |
|---|---|---|
| Aplicación | Orquestador FSM, API HTTP, coordinación de navegación/interacción | Control determinístico de misión guiada |
| Integración robótica | ROS2 Nav2, bridge de navegación, acoplamiento hardware/simulación | Ejecución de waypoints y telemetría consistente |
| Operación SRE | Scripts de bootstrap, preflight, despliegue y validación | Arranque reproducible HIL/SITL |
| Evidencia técnica | Protocolos, memorias, transcripciones, manuales | Trazabilidad de decisiones y pruebas |

## Topología de red DDS
| Dominio DDS | Rol recomendado | Tráfico esperado |
|---|---|---|
| Domain 0 | HIL/robot físico | Telemetría operacional, control de movimiento y sincronización de estado |
| Domain 1 | Simulación/SITL | Publicación de tópicos de navegación y percepción en entorno controlado |

## Estado de deuda técnica de aplicación
| Componente histórico | Estado actual | Reemplazo vigente |
|---|---|---|
| `api_server.py` | Eliminado | `codigo ottoguide/src/api/server.py` |
| `navigation_manager.py` | Eliminado | `codigo ottoguide/src/navigation/nav2_bridge.py` |

## Flujo del protocolo HIL
| Etapa | Acción operativa | Evidencia |
|---|---|---|
| 1. Preflight | Validación de entorno, red, dependencias y configuración | Logs de `preflight_check.sh` |
| 2. Bootstrap | Inicialización de procesos de robot, middleware y control | Trazas de `bootstrap_hil.sh` |
| 3. Ejecución | Dispatch de tour, navegación y ventanas de interacción | Estado API y registros de orquestador |
| 4. Observabilidad | Monitoreo continuo de odometría, estado FSM y fallos | Endpoint `/status`, logs runtime |
| 5. Cierre seguro | Finalización de misión o parada de emergencia | Registro de transiciones y cierre controlado |