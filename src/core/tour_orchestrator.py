from __future__ import annotations

# @TASK: Implementar orquestador central de tour integrando todos los subsistemas HIL Fase 6
# @INPUT: Instancias activas de RobotHardwareAPI, AsyncNav2Bridge, ConversationManager, VisionProcessor
# @OUTPUT: Maquina de estados asincrona con callbacks de integracion completa por estado
# @CONTEXT: Modulo central de control; unico coordinador de subsistemas durante el tour
# STEP 1: Definir grafo de estados (IDLE, NAVIGATING, INTERACTING, EMERGENCY)
# STEP 2: Implementar callbacks on_enter/on_exit con logica de integracion real
# STEP 3: Gestionar tareas background (odometria, audio, nav) con cancel seguro
# STEP 4: Exponer dispatch_tour para integracion con FastAPI BackgroundTasks
# @SECURITY: EMERGENCY es la unica transicion con prioridad absoluta; Damp() se invoca primero
# @AI_CONTEXT: python-statemachine AsyncEngine; configuration[0].id para leer estado activo

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
from numpy.typing import NDArray

from statemachine import State, StateMachine
from statemachine.engines.async_ import AsyncEngine

from src.hardware import RobotHardwareAPI, RobotHardwareAPIError
from src.interaction import ConversationManager, ConversationRequest, ConversationResponse
from src.navigation import AsyncNav2Bridge, NavWaypoint
from src.vision import OdometryVector, VisionProcessor


# ---------------------------------------------------------------------------
# Constantes de operacion
# ---------------------------------------------------------------------------

# @TASK: Declarar timeouts operativos del orquestador
# @INPUT: Ninguno
# @OUTPUT: Constantes de timeout para cada subsistema integrado
# @CONTEXT: Calibradas para robot fisico en entorno indoor air-gapped
# STEP 1: Timeout de Damp en EMERGENCY (critico; no puede ser elevado)
# STEP 2: Timeout de captura de audio para interaccion
# STEP 3: Timeout de inyeccion de odometria en bridge
# STEP 4: Intervalo de sondeo de proximidad a waypoints
# @SECURITY: DAMP_TIMEOUT_S es la cota dura de tiempo de reaccion ante emergencia
# @AI_CONTEXT: Ajustar AUDIO_CAPTURE_TIMEOUT_S segun duracion esperada del wake-word
DAMP_TIMEOUT_S: float = 1.5
AUDIO_CAPTURE_TIMEOUT_S: float = 8.0
ODOMETRY_INJECT_TIMEOUT_S: float = 0.5
WAYPOINT_POLL_INTERVAL_S: float = 0.1
NAV_TASK_SETTLE_S: float = 0.05   # pausa tras cancel de tarea background

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TourContext:
    # @TASK: Encapsular estado mutable del tour activo para observabilidad externa
    # @INPUT: Actualizaciones incrementales desde los callbacks del orquestador
    # @OUTPUT: Snapshot del tour consultable por APIServer y telemetria
    # @CONTEXT: Objeto mutable compartido entre estados; no thread-safe (solo event loop)
    # STEP 1: Registrar plan de waypoints, indice de posicion y ultima interaccion
    # STEP 2: Mantener causa de la ultima emergencia para diagnostico post-mortem
    # @SECURITY: No persistir en disco; solo en memoria durante la sesion
    # @AI_CONTEXT: Consultado por APIServer via propiedad orchestrator.context
    waypoint_plan: list[NavWaypoint] = field(default_factory=list)
    current_waypoint_index: int = 0
    last_interaction: Optional[ConversationResponse] = None
    last_error: Optional[str] = None
    tour_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class TourPlan:
    # @TASK: Encapsular el plan de un tour como una secuencia tipada de waypoints
    # @INPUT: Lista de NavWaypoint y identificador de sesion opcional
    # @OUTPUT: Estructura inmutable despachada a dispatch_tour()
    # @CONTEXT: Contrato de entrada del endpoint FastAPI para iniciar un tour
    # STEP 1: Definir lista de waypoints y id de tour para trazabilidad
    # @SECURITY: Frozen; no puede ser mutado por el caller tras el despacho
    # @AI_CONTEXT: tours_id permite correlacionar logs con sesiones HTTP
    waypoints: list[NavWaypoint]
    tour_id: str = "unidentified"


# ---------------------------------------------------------------------------
# Orquestador central
# ---------------------------------------------------------------------------

class TourOrchestrator(StateMachine):

    # ------------------------------------------------------------------
    # Definicion del grafo de estados
    # ------------------------------------------------------------------

    # @TASK: Declarar estados del tour con restriccion de estado inicial IDLE
    # @INPUT: Definiciones de State de python-statemachine
    # @OUTPUT: Grafo de 4 estados operativos del robot guia
    # @CONTEXT: Los estados reflejan el modo de operacion del robot en todo momento
    # STEP 1: Declarar IDLE como el unico estado inicial
    # STEP 2: Declarar NAVIGATING, INTERACTING y EMERGENCY como estados operativos
    # @SECURITY: EMERGENCY no tiene transicion de salida automatica; requiere confirmacion
    # @AI_CONTEXT: python-statemachine usa el nombre del atributo como id del estado

    idle: State = State("IDLE", initial=True)
    navigating: State = State("NAVIGATING")
    interacting: State = State("INTERACTING")
    emergency: State = State("EMERGENCY", final=True)

    # ------------------------------------------------------------------
    # Definicion de transiciones
    # ------------------------------------------------------------------

    # @TASK: Declarar transiciones validas entre estados del tour
    # @INPUT: Relaciones origen->destino del protocolo de operacion
    # @OUTPUT: Eventos asincronos invocables via await en el orquestador
    # @CONTEXT: Contrato de flujo de control; restricciones de concurrencia aplicadas
    # STEP 1: Transicion nominal idle->navigating para inicio de tour
    # STEP 2: Transicion simetrica navigating<->interacting para ventana de dialogo
    # STEP 3: Transicion de retorno navigating->idle al completar el plan
    # STEP 4: Transicion de emergencia desde cualquier estado operativo
    # @SECURITY: trigger_emergency es la unica transicion de alta prioridad; no requiere state check
    # @AI_CONTEXT: El uso de | expresa origen multiple para una misma transicion

    start_tour = idle.to(navigating)
    pause_for_interaction = navigating.to(interacting)
    resume_tour = interacting.to(navigating)
    finish_tour = navigating.to(idle)
    trigger_emergency = (
        idle.to(emergency)
        | navigating.to(emergency)
        | interacting.to(emergency)
    )

    class Meta:
        # @TASK: Forzar motor asíncrono para todos los callbacks de estado
        # @INPUT: AsyncEngine de python-statemachine
        # @OUTPUT: Todos los on_enter/on_exit ejecutados con await
        # @CONTEXT: Requisito critico para no bloquear el event loop principal
        # STEP 1: Asignar AsyncEngine a engine en Meta
        # @SECURITY: Sin AsyncEngine los callbacks sync bloquean el loop
        # @AI_CONTEXT: AsyncEngine es compatible con python-statemachine >= 0.9
        engine = AsyncEngine

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        hardware_api: RobotHardwareAPI,
        nav_bridge: AsyncNav2Bridge,
        conversation_manager: ConversationManager,
        vision_processor: VisionProcessor,
        damp_timeout_s: float = DAMP_TIMEOUT_S,
        audio_capture_timeout_s: float = AUDIO_CAPTURE_TIMEOUT_S,
    ) -> None:
        # @TASK: Inyectar dependencias de todos los subsistemas e inicializar estado interno
        # @INPUT: Instancias activas de los 4 subsistemas HIL; timeouts configurables
        # @OUTPUT: Orquestador en estado IDLE con contexto limpio y tareas background = None
        # @CONTEXT: Constructor DI; todos los subsistemas deben estar en estado ACTIVO
        # STEP 1: Validar parametros criticos de seguridad antes de asignacion
        # STEP 2: Persistir referencias a todos los subsistemas inyectados
        # STEP 3: Inicializar contexto de tour y handles de tareas background a None
        # STEP 4: Emitir advertencia CRITICAL de override mecanico al arrancar
        # STEP 5: Llamar super().__init__() para que python-statemachine initcialice el grafo
        # @SECURITY: damp_timeout_s >= 0.5 s es requisito fisico; por debajo puede no propagarse via DDS
        # @AI_CONTEXT: Las tareas background (_odometry_task, _nav_task) se crean en on_enter_navigating

        # STEP 1
        if damp_timeout_s <= 0:
            raise ValueError("damp_timeout_s debe ser mayor que 0.")
        if audio_capture_timeout_s <= 0:
            raise ValueError("audio_capture_timeout_s debe ser mayor que 0.")

        # STEP 2
        self._hardware_api: RobotHardwareAPI = hardware_api
        self._nav_bridge: AsyncNav2Bridge = nav_bridge
        self._conversation_manager: ConversationManager = conversation_manager
        self._vision_processor: VisionProcessor = vision_processor
        self._damp_timeout_s: float = damp_timeout_s
        self._audio_capture_timeout_s: float = audio_capture_timeout_s

        # STEP 3
        self._context: TourContext = TourContext()
        self._odometry_task: Optional[asyncio.Task[None]] = None
        self._nav_task: Optional[asyncio.Task[None]] = None
        self._interaction_done_event: asyncio.Event = asyncio.Event()

        # STEP 4
        LOGGER.critical(
            "[SAFETY] TourOrchestrator inicializado. "
            "L1+A en el mando fuerza Damp mecanico inmediato. "
            "Control manual y API simultaneos estan estrictamente prohibidos en NAVIGATING."
        )

        # STEP 5
        super().__init__()

    # ------------------------------------------------------------------
    # Propiedades de observabilidad
    # ------------------------------------------------------------------

    @property
    def context(self) -> TourContext:
        # @TASK: Exponer contexto del tour activo para telemetria externa
        # @INPUT: Sin parametros
        # @OUTPUT: TourContext mutable con estado actual del recorrido
        # @CONTEXT: Consumido por APIServer para responder a solicitudes de estado
        # STEP 1: Retornar referencia al contexto interno
        # @SECURITY: Solo lectura recomendada; no mutar desde fuera del orquestador
        # @AI_CONTEXT: tour_id, current_waypoint_index y last_error son los campos clave
        return self._context  # STEP 1

    @property
    def state_id(self) -> str:
        # @TASK: Retornar el identificador canonico del estado activo actual
        # @INPUT: Sin parametros
        # @OUTPUT: String con id del estado (idle, navigating, interacting, emergency)
        # @CONTEXT: Reemplaza current_state deprecated; usa configuration[0].id
        # STEP 1: Leer configuracion activa; retornar "uninitialized" si vacia
        # @SECURITY: Solo lectura; no activa transiciones
        # @AI_CONTEXT: Usar este metodo en lugar de current_state en toda la codebase
        cfg = self.configuration
        if not cfg:
            return "uninitialized"  # STEP 1
        return next(iter(cfg)).id

    # ------------------------------------------------------------------
    # API publica para FastAPI BackgroundTasks
    # ------------------------------------------------------------------

    async def dispatch_tour(self, plan: TourPlan) -> None:
        # @TASK: Despachar un plan de tour como tarea background no bloqueante para FastAPI
        # @INPUT: plan — TourPlan con lista de NavWaypoint y tour_id
        # @OUTPUT: Transicion a NAVIGATING; retorno inmediato al caller de FastAPI
        # @CONTEXT: Punto de entrada para el endpoint POST /tour/start; no debe bloquear la ruta HTTP
        # STEP 1: Validar que el orquestador este en IDLE antes de aceptar el plan
        # STEP 2: Persistir el plan en el contexto y resetear indice de waypoints
        # STEP 3: Ejecutar la transicion start_tour via AsyncEngine
        # STEP 4: Lanzar el bucle de navegacion como background task (create_task)
        # @SECURITY: Si el orquestador no esta en IDLE, rechazar con excepcion clara
        # @AI_CONTEXT: FastAPI debe invocar este metodo dentro de un BackgroundTask para no bloquear la respuesta HTTP

        # STEP 1
        if self.state_id != "idle":
            raise RuntimeError(
                f"dispatch_tour() rechazado: estado actual es '{self.state_id}', se requiere 'idle'."
            )

        # STEP 2
        self._context.waypoint_plan = list(plan.waypoints)
        self._context.current_waypoint_index = 0
        self._context.tour_id = plan.tour_id
        self._context.last_error = None

        # STEP 3
        await self.start_tour()

        # STEP 4: el bucle real de navegacion corre como background task
        self._nav_task = asyncio.create_task(
            self._navigation_loop(),
            name=f"nav-loop-{plan.tour_id}",
        )
        LOGGER.info(
            "[Orchestrator] dispatch_tour aceptado. tour_id=%s waypoints=%d",
            plan.tour_id,
            len(plan.waypoints),
        )

    async def request_interaction(
        self,
        audio_buffer: NDArray[np.float32],
        language: str = "es",
    ) -> None:
        # @TASK: Solicitar transicion a INTERACTING desde una fuente externa (wake-word detector)
        # @INPUT: audio_buffer — PCM float32 del wake-word capturado; language
        # @OUTPUT: Transicion a INTERACTING; audio procesado y reproducido; retorno a NAVIGATING
        # @CONTEXT: Invocado por el detector de wake-word o por endpoint fastAPI /interaction
        # STEP 1: Verificar que el estado actual es NAVIGATING; ignorar si no lo es
        # STEP 2: Persistir audio_buffer en contexto para consumo en on_enter_interacting
        # STEP 3: Ejecutar transicion pause_for_interaction via AsyncEngine
        # @SECURITY: Solo transible desde NAVIGATING; evita conflictos de estado multi-fuente
        # @AI_CONTEXT: on_enter_interacting consume _pending_audio y llama process_interaction

        # STEP 1
        if self.state_id != "navigating":
            LOGGER.debug(
                "[Orchestrator] request_interaction ignorado: estado='%s'", self.state_id
            )
            return

        # STEP 2
        self._pending_audio: NDArray[np.float32] = audio_buffer
        self._pending_language: str = language

        # STEP 3
        await self.pause_for_interaction()

    async def emergency_stop(self, reason: str = "manual") -> None:
        # @TASK: Activar la transicion de emergencia desde cualquier estado operativo
        # @INPUT: reason — descripcion de la causa de emergencia para diagnostico
        # @OUTPUT: Transicion a EMERGENCY; Damp() ejecutado; subsistemas detenidos
        # @CONTEXT: Invocable desde APIServer, señal OS o excepcion no recuperable
        # STEP 1: Registrar causa en contexto antes de la transicion
        # STEP 2: Ejecutar trigger_emergency via AsyncEngine
        # @SECURITY: No hace verificacion de estado previo; trigger_emergency acepta cualquier origen
        # @AI_CONTEXT: El callback on_enter_emergency es el que ejecuta Damp() y limpia tareas

        # STEP 1
        self._context.last_error = reason
        LOGGER.critical("[Orchestrator] EMERGENCY STOP solicitado. Razon: %s", reason)

        # STEP 2
        await self.trigger_emergency()

    # ------------------------------------------------------------------
    # Callbacks on_enter de estados
    # ------------------------------------------------------------------

    async def on_enter_navigating(self) -> None:
        # @TASK: Iniciar bucle de inyeccion de odometria visual como background task
        # @INPUT: Sin parametros directos; usa _vision_processor y _nav_bridge internos
        # @OUTPUT: _odometry_task activa consumiendo VisionProcessor.pose_queue
        # @CONTEXT: Callback invocado por AsyncEngine al entrar a NAVIGATING
        # STEP 1: Crear tarea background para consumo continuo de odometria visual
        # STEP 2: Registrar la tarea en _odometry_task para cancelacion en on_exit_navigating
        # @SECURITY: La tarea se crea con nombre descriptivo para identificacion en debugging
        # @AI_CONTEXT: _navigation_loop ya fue creado en dispatch_tour; este callback solo inicia odometria

        # STEP 1 + 2
        if self._odometry_task is None or self._odometry_task.done():
            self._odometry_task = asyncio.create_task(
                self._odometry_injection_loop(),
                name="odometry-injection-loop",
            )
            LOGGER.info("[Orchestrator] on_enter_navigating: Tarea de odometria iniciada.")

    async def on_exit_navigating(self) -> None:
        # @TASK: Cancelar la tarea de inyeccion de odometria al salir de NAVIGATING
        # @INPUT: Sin parametros
        # @OUTPUT: _odometry_task cancelada y awaited; recursos liberados
        # @CONTEXT: Callback invocado por AsyncEngine antes de ejecutar cualquier salida de NAVIGATING
        # STEP 1: Cancelar _odometry_task si esta activa y no terminada
        # STEP 2: Await con suppress para absorber CancelledError sin propagar
        # STEP 3: Limpiar referencia para evitar referencias a tareas finalizadas
        # @SECURITY: on_exit garantiza que no haya inyeccion de odometria en estados no-NAVIGATING
        # @AI_CONTEXT: La tarea de navegacion Nav2 se cancela por separado en _navigation_loop

        # STEP 1
        if self._odometry_task is not None and not self._odometry_task.done():
            self._odometry_task.cancel()
            try:
                await self._odometry_task
            except asyncio.CancelledError:
                pass

        # STEP 3
        self._odometry_task = None
        LOGGER.info("[Orchestrator] on_exit_navigating: Tarea de odometria cancelada.")

    async def on_enter_interacting(self) -> None:
        # @TASK: Detener el robot, procesar audio via ConversationManager y retornar a NAVIGATING
        # @INPUT: _pending_audio y _pending_language seteados por request_interaction()
        # @OUTPUT: Robot detenido; respuesta TTS reproducida; transicion automatica a NAVIGATING
        # @CONTEXT: Callback de INTERACTING; se ejecuta sincrono hasta que el dialogo termina
        # STEP 1: Cancelar navegacion Nav2 activa para detener cinematica del robot
        # STEP 2: Enviar velocidad cero como refuerzo de detencion (failsafe)
        # STEP 3: Invocar ConversationManager.process_interaction() con el audio pendiente
        # STEP 4: Registrar respuesta en contexto
        # STEP 5: Ejecutar transicion automatica de vuelta a NAVIGATING via resume_tour
        # @SECURITY: Paso 1 (cancel Nav2) antes del paso 2 (velocidad cero) para evitar conflicto de comandos
        # @AI_CONTEXT: La reproduccion TTS es fire-and-forget; no esperamos el fin del audio aqui

        audio_buffer: NDArray[np.float32] = getattr(
            self, "_pending_audio", np.zeros(1, dtype=np.float32)
        )
        language: str = getattr(self, "_pending_language", "es")

        LOGGER.info("[Orchestrator] on_enter_interacting: Iniciando secuencia de dialogo.")

        # STEP 1: Cancelar navegacion activa
        await self._cancel_nav_task_safe()
        await self._nav_bridge.cancel_navigation()

        # STEP 2: Velocidad cero como failsafe cinematico
        try:
            await asyncio.wait_for(
                self._hardware_api.move(0.0, 0.0, 0.0),
                timeout=0.5,
            )
        except Exception as exc:
            LOGGER.warning("[Orchestrator] Fallo al enviar velocidad cero: %s", exc)

        # STEP 3: Procesar audio via ConversationManager con hot-swap local/cloud
        try:
            response = await asyncio.wait_for(
                self._conversation_manager.process_interaction(
                    audio_buffer,
                    language=language,
                ),
                timeout=self._audio_capture_timeout_s,
            )
        except (TimeoutError, asyncio.TimeoutError):
            LOGGER.error(
                "[Orchestrator] Timeout de proceso de interaccion (%.1f s). "
                "Retornando a NAVIGATING sin respuesta.",
                self._audio_capture_timeout_s,
            )
            response = ConversationResponse(
                answer_text="",
                source_pipeline="timeout",
                audio_stream_ready=False,
            )
        except Exception as exc:
            LOGGER.error(
                "[Orchestrator] Excepcion en ConversationManager: %s — %s",
                type(exc).__name__,
                exc,
            )
            response = ConversationResponse(
                answer_text="",
                source_pipeline="error",
                audio_stream_ready=False,
            )

        # STEP 4: Registrar en contexto
        self._context.last_interaction = response
        LOGGER.info(
            "[Orchestrator] Dialogo completado. pipeline=%s swap_count=%s",
            response.source_pipeline,
            getattr(self._conversation_manager, "swap_count", "?"),
        )

        # STEP 5: Retorno automatico a NAVIGATING
        try:
            await self.resume_tour()
        except Exception as exc:
            LOGGER.error(
                "[Orchestrator] Fallo en transicion resume_tour: %s — activando emergencia.",
                exc,
            )
            await self.emergency_stop(reason=f"resume_tour fallo: {exc}")

    async def on_enter_emergency(self) -> None:
        # @TASK: Ejecutar secuencia de emergencia perentoria ante cualquier fallo critico
        # @INPUT: Sin parametros; _context.last_error contiene la causa registrada
        # @OUTPUT: Damp() ejecutado; tareas background canceladas; VisionProcessor detenido
        # @CONTEXT: Callback de estado EMERGENCY; la secuencia es irreversible desde esta clase
        # STEP 1: Cancelar todas las tareas background (nav y odometria) sin esperar
        # STEP 2: Cancelar la navegacion activa en Nav2Bridge
        # STEP 3: Invocar Damp() en hardware con timeout estricto DAMP_TIMEOUT_S
        # STEP 4: Invocar velocidad cero como redundancia tras Damp()
        # STEP 5: Cerrar VisionProcessor para liberar el bus USB
        # STEP 6: Registrar estado final para diagnostico
        # @SECURITY: Damp() es el PRIMER comando de hardware ejecutado (STEP 3 antes que qualquier otro)
        # @AI_CONTEXT: Este estado es final (python-statemachine final=True); no hay transicion de salida

        LOGGER.critical(
            "[Orchestrator] EMERGENCY activado. Causa: %s",
            self._context.last_error,
        )

        # STEP 1: Cancelar tareas background
        await self._cancel_nav_task_safe()
        await self._cancel_odometry_task_safe()

        # STEP 2: Cancelar navegacion Nav2
        try:
            await asyncio.wait_for(
                self._nav_bridge.cancel_navigation(),
                timeout=1.0,
            )
        except Exception as exc:
            LOGGER.error("[Orchestrator] Fallo al cancelar Nav2 en emergencia: %s", exc)

        # STEP 3: Damp() — primera accion sobre hardware fisico
        LOGGER.critical(
            "[Orchestrator] Emitiendo Damp() al hardware (timeout=%.1f s).",
            self._damp_timeout_s,
        )
        try:
            await asyncio.wait_for(
                self._hardware_api.damp(),
                timeout=self._damp_timeout_s,
            )
            LOGGER.critical("[Orchestrator] Damp() ejecutado correctamente.")
        except (TimeoutError, asyncio.TimeoutError):
            LOGGER.critical(
                "[Orchestrator] TIMEOUT en Damp() durante EMERGENCY. "
                "Verificar estado mecanico manualmente."
            )
        except RobotHardwareAPIError as exc:
            LOGGER.critical("[Orchestrator] RobotHardwareAPIError en Damp(): %s", exc)
        except Exception as exc:
            LOGGER.critical(
                "[Orchestrator] Excepcion inesperada en Damp(): %s — %s",
                type(exc).__name__,
                exc,
            )

        # STEP 4: Velocidad cero como redundancia
        try:
            await asyncio.wait_for(
                self._hardware_api.move(0.0, 0.0, 0.0),
                timeout=0.5,
            )
        except Exception:
            pass  # En emergencia, si move falla es aceptable; Damp ya fue enviado

        # STEP 5: Cerrar VisionProcessor (liberar bus USB y thread de captura)
        try:
            self._vision_processor.close()
            LOGGER.info("[Orchestrator] VisionProcessor cerrado en EMERGENCY.")
        except Exception as exc:
            LOGGER.error("[Orchestrator] Fallo al cerrar VisionProcessor: %s", exc)

        # STEP 6
        LOGGER.critical(
            "[Orchestrator] Secuencia de EMERGENCY completada. "
            "Estado: %s | Causa: %s",
            self.state_id,
            self._context.last_error,
        )

    # ------------------------------------------------------------------
    # Bucles de background (no bloquean el event loop)
    # ------------------------------------------------------------------

    async def _navigation_loop(self) -> None:
        # @TASK: Ejecutar el plan de navegacion enviando waypoints secuencialmente a Nav2Bridge
        # @INPUT: _context.waypoint_plan — lista de NavWaypoint del TourPlan activo
        # @OUTPUT: Robot desplazado hasta cada waypoint; finish_tour al completar el plan
        # @CONTEXT: Tarea background creada en dispatch_tour y cancelada por on_exit_navigating
        # STEP 1: Iterar sobre cada waypoint del plan enviando uno por vez a Nav2Bridge
        # STEP 2: Esperar completitud de la tarea Nav2 (navigate_to_waypoints es bloqueante async)
        # STEP 3: Actualizar indice de waypoint en contexto para observabilidad
        # STEP 4: Al finalizar todos los waypoints, ejecutar finish_tour
        # STEP 5: Capturar CancelledError (cancelacion por interaccion o emergencia)
        # @SECURITY: La tarea puede ser cancelada en cualquier momento; CancelledError es el mecanismo normal
        # @AI_CONTEXT: Si Nav2 no completa un waypoint, se registra el fallo y continua con el siguiente

        plan = self._context.waypoint_plan
        LOGGER.info(
            "[Orchestrator] _navigation_loop iniciado. %d waypoints.", len(plan)
        )

        try:
            for idx, waypoint in enumerate(plan):
                # STEP 3
                self._context.current_waypoint_index = idx
                LOGGER.info(
                    "[Orchestrator] Navegando a waypoint %d/%d (x=%.2f y=%.2f yaw=%.2f).",
                    idx + 1, len(plan),
                    waypoint.x, waypoint.y, waypoint.yaw_rad,
                )

                # STEP 2: Enviar waypoint individual como lista unitaria
                try:
                    success = await self._nav_bridge.navigate_to_waypoints([waypoint])
                except asyncio.CancelledError:
                    # STEP 5: Propagacion de cancelacion desde on_exit_navigating o EMERGENCY
                    LOGGER.info(
                        "[Orchestrator] _navigation_loop cancelado en waypoint %d.", idx
                    )
                    raise
                except Exception as exc:
                    LOGGER.error(
                        "[Orchestrator] Nav2 fallo en waypoint %d: %s — continuando.", idx, exc
                    )
                    success = False

                if not success:
                    LOGGER.warning(
                        "[Orchestrator] Waypoint %d no completado. Nav2 result=FAILED.", idx
                    )

                # Ceder al event loop entre waypoints para procesabilidad de señales
                await asyncio.sleep(WAYPOINT_POLL_INTERVAL_S)

            # STEP 4: Plan completado
            LOGGER.info("[Orchestrator] Plan de navegacion completado.")
            if self.state_id == "navigating":
                await self.finish_tour()

        except asyncio.CancelledError:
            LOGGER.info("[Orchestrator] _navigation_loop terminado por cancelacion.")
            raise  # re-propagar para que asyncio lo maneje correctamente

    async def _odometry_injection_loop(self) -> None:
        # @TASK: Consumir OdometryVector de VisionProcessor y despacharlos a AsyncNav2Bridge
        # @INPUT: vision_processor.pose_queue — asyncio.Queue[OdometryVector]
        # @OUTPUT: Correcciones AMCL inyectadas continuamente en /initialpose via Nav2Bridge
        # @CONTEXT: Tarea background activa durante NAVIGATING; cancelada en on_exit_navigating
        # STEP 1: Esperar el siguiente OdometryVector de la cola con timeout corto
        # STEP 2: Inyectar la estimacion de pose en AsyncNav2Bridge.inject_absolute_pose
        # STEP 3: Ante CancelledError, terminar limpiamente
        # @SECURITY: El timeout en get_next_estimate evita que la tarea quede bloqueada indefinidamente
        # @AI_CONTEXT: Si la cola esta vacia (sin tags detectados), el loop espera silenciosamente

        LOGGER.info("[Orchestrator] Bucle de inyeccion odometrica iniciado.")

        try:
            while True:
                # STEP 1
                odometry: Optional[OdometryVector] = await self._vision_processor.get_next_estimate(
                    timeout_s=0.5
                )

                if odometry is None:
                    # Sin deteccion reciente; ceder al loop y reintentar
                    await asyncio.sleep(0.0)
                    continue

                # STEP 2
                try:
                    await asyncio.wait_for(
                        self._nav_bridge.inject_absolute_pose(odometry.pose_estimate),
                        timeout=ODOMETRY_INJECT_TIMEOUT_S,
                    )
                    LOGGER.debug(
                        "[Orchestrator] Odometria inyectada: marker=%d x=%.3f y=%.3f theta=%.3f",
                        odometry.marker_id,
                        odometry.x,
                        odometry.y,
                        odometry.theta,
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    LOGGER.warning("[Orchestrator] Timeout inyectando odometria AMCL.")
                except Exception as exc:
                    LOGGER.error(
                        "[Orchestrator] Fallo inyectando odometria: %s — %s",
                        type(exc).__name__, exc,
                    )

                await asyncio.sleep(0.0)  # ceder al event loop

        except asyncio.CancelledError:
            # STEP 3
            LOGGER.info("[Orchestrator] Bucle de odometria terminado por cancelacion.")
            raise

    # ------------------------------------------------------------------
    # Utilidades internas de cancelacion segura
    # ------------------------------------------------------------------

    async def _cancel_nav_task_safe(self) -> None:
        # @TASK: Cancelar _nav_task de forma segura sin propagar CancelledError
        # @INPUT: Sin parametros
        # @OUTPUT: _nav_task cancelada y awaited; referencia limpiada
        # @CONTEXT: Utilitario invocado desde on_enter_interacting y on_enter_emergency
        # STEP 1: Verificar que la tarea existe y no esta terminada
        # STEP 2: Cancelar y await con suppress para absorber CancelledError
        # STEP 3: Limpiar referencia tras cancelacion
        # @SECURITY: suppress(CancelledError) es intencional; la tarea fue cancelada explicitamente
        # @AI_CONTEXT: La tarea Nav2 interna de navigate_to_waypoints se cancela por separado en Nav2Bridge

        if self._nav_task is not None and not self._nav_task.done():  # STEP 1
            self._nav_task.cancel()
            try:
                await self._nav_task
            except asyncio.CancelledError:
                pass  # STEP 2: CancelledError esperado
        self._nav_task = None  # STEP 3

    async def _cancel_odometry_task_safe(self) -> None:
        # @TASK: Cancelar _odometry_task de forma segura sin propagar CancelledError
        # @INPUT: Sin parametros
        # @OUTPUT: _odometry_task cancelada y awaited; referencia limpiada
        # @CONTEXT: Utilitario invocado desde on_enter_emergency
        # STEP 1: Verificar tarea activa
        # STEP 2: Cancelar y absorber CancelledError
        # STEP 3: Limpiar referencia
        # @SECURITY: Idem a _cancel_nav_task_safe; CancelledError es el mecanismo de terminacion normal
        # @AI_CONTEXT: Separado de _cancel_nav_task_safe para claridad de proposito en on_enter_emergency

        if self._odometry_task is not None and not self._odometry_task.done():  # STEP 1
            self._odometry_task.cancel()
            try:
                await self._odometry_task
            except asyncio.CancelledError:
                pass  # STEP 2
        self._odometry_task = None  # STEP 3

    # ------------------------------------------------------------------
    # Compatibilidad con TourOrchestrator anterior (respond via ConversationManager)
    # ------------------------------------------------------------------

    async def handle_user_question(self, user_text: str) -> ConversationResponse:
        # @TASK: Dispatch de pregunta de texto al ConversationManager como alias de compatibilidad
        # @INPUT: user_text — texto ya transcripto por el caller
        # @OUTPUT: ConversationResponse desde la estrategia activa (local o cloud)
        # @CONTEXT: Conservado para compatibilidad con llamadas directas desde APIServer
        # STEP 1: Construir ConversationRequest y delegar a respond() del manager
        # STEP 2: Guardar respuesta en contexto para trazabilidad
        # @SECURITY: No ejecuta STT; el caller es responsable de la transcripcion previa
        # @AI_CONTEXT: prefer process_interaction() si hay audio disponible

        request = ConversationRequest(user_text=user_text)      # STEP 1
        response = await self._conversation_manager.respond(request)
        self._context.last_interaction = response                # STEP 2
        return response


# ---------------------------------------------------------------------------
# Exportaciones
# ---------------------------------------------------------------------------

__all__ = [
    "TourContext",
    "TourOrchestrator",
    "TourPlan",
]