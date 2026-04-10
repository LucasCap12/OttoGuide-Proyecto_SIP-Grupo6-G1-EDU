# Arquitectura Operativa RC1 - OttoGuide MVP

## 1. Definicion de arquitectura vigente

OttoGuide RC1 opera con una arquitectura de control en borde orientada a HIL:

- Control de aplicacion: FastAPI + asyncio estricto.
- Orquestacion de dominio: FSM asincrona (TourOrchestrator).
- Integracion robotica: ROS 2/Nav2 por bridge dedicado + control locomocion via SDK Unitree.
- Transporte: CycloneDDS Unicast.
- Interaccion: pipeline local STT/LLM/TTS.
- Observabilidad: REST status, WebSocket telemetria, auditoria JSON por mision.

## 2. Capas funcionales

| Capa | Componente | Responsabilidad |
|---|---|---|
| Capa 4 | main.py + api/router.py | Exponer interfaces HTTP/WS y ciclo de vida seguro |
| Capa 4 | src/core/tour_orchestrator.py | Coordinar estados, tareas asincronas y seguridad operativa |
| Capa 3 | src/interaction/conversation_manager.py | Ejecutar estrategia NLP local/cloud y devolver respuesta |
| Capa 2 | src/navigation/nav2_bridge.py | Conectar con Nav2/AMCL y clamping de comandos de movimiento |
| Capa 1 | hardware/*.py + unitree_sdk2 | Ejecutar acciones sobre hardware real/sim/mock |

## 3. Flujo de datos E2E

```text
Operador/API -> FastAPI router -> TourOrchestrator FSM
TourOrchestrator -> AsyncNav2Bridge -> ROS 2 Nav2/AMCL
TourOrchestrator -> Hardware Adapter -> Unitree SDK2 (DDS Unicast)
TourOrchestrator -> ConversationManager -> Ollama local -> TTS
TourOrchestrator -> TelemetryManager -> Dashboard WebSocket
TourOrchestrator -> MissionAuditLogger -> logs/mission_*.json
```

## 4. Contrato de estados

| Estado | Descripcion | Salidas permitidas |
|---|---|---|
| IDLE | Sistema listo sin mision activa | NAVIGATING, EMERGENCY |
| NAVIGATING | Ejecucion de waypoints y monitoreo de ruta | INTERACTING, IDLE, EMERGENCY |
| INTERACTING | Ventana conversacional en curso | NAVIGATING, EMERGENCY |
| EMERGENCY | Estado final de seguridad con damp() | Sin salida automatica |

## 5. Controles de seguridad vigentes

1. damp() garantizado en shutdown del lifecycle de aplicacion.
2. Endpoint de emergencia de maxima prioridad.
3. Intercepcion y clamping de velocidad en bridge de navegacion.
4. Barrera de preflight antes de inicializar hardware.
5. Confirmacion operatoria obligatoria de modos seguros del robot.

## 6. Alcance de documentos referenciales

- Los documentos SITL/simulacion se consideran de apoyo historico.
- La operacion RC1 oficial se define por runbooks y protocolos HIL.
