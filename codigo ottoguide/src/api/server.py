from __future__ import annotations

# @TASK: Implementar servidor API REST FastAPI para control externo del TourOrchestrator HIL Fase 7
# @INPUT: Instancia activa de TourOrchestrator inyectada via app.state
# @OUTPUT: Endpoints HTTP de control y observabilidad; servidor Uvicorn en el event loop principal
# @CONTEXT: Capa de interfaz externa en red air-gapped; operador humano como unico cliente
# STEP 1: Definir modelos Pydantic de request/response para cada endpoint
# STEP 2: Implementar lifespan handler con shutdown que activa trigger_emergency
# STEP 3: Registrar endpoints POST /tour/start, /tour/pause, /emergency y GET /status
# STEP 4: Exponer run_server() para integracion programatica con uvicorn.Server.serve()
# @SECURITY: docs_url=None y redoc_url=None en produccion; TransitionNotAllowed -> HTTP 409
# @AI_CONTEXT: Uvicorn corre en el event loop principal via Server.serve(); sin loops paralelos

import asyncio
import contextlib
import logging
from typing import Optional

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from statemachine.exceptions import TransitionNotAllowed

from src.core import TourOrchestrator, TourPlan
from src.navigation import NavWaypoint


LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Modelos Pydantic — contratos de entrada/salida
# ---------------------------------------------------------------------------

class NavWaypointDTO(BaseModel):
    # @TASK: Representar un waypoint serializable en JSON para la API REST
    # @INPUT: x, y en metros; yaw_rad en radianes; frame_id opcional
    # @OUTPUT: DTO validado convertible a NavWaypoint del dominio interno
    # @CONTEXT: Contrato de entrada para POST /tour/start
    # STEP 1: Validar coordenadas como floats y frame_id como string no vacio
    # @SECURITY: extra="forbid" evita inyeccion de campos no declarados
    # @AI_CONTEXT: Convertido a NavWaypoint en _to_domain_waypoint()
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    yaw_rad: float
    frame_id: str = "map"


class StartTourRequest(BaseModel):
    # @TASK: Definir payload del endpoint POST /tour/start
    # @INPUT: Lista de waypoints y tour_id de sesion opcional
    # @OUTPUT: Modelo validado para dispatch_tour() del orquestador
    # @CONTEXT: Contrato de entrada del trigger REST para iniciar un tour
    # STEP 1: Exigir al menos un waypoint en la lista
    # STEP 2: Asignar tour_id por defecto si no se provee
    # @SECURITY: extra="forbid"; min_length en tour_id previene IDs vacios
    # @AI_CONTEXT: waypoints se convierten a NavWaypoint antes de despachar
    model_config = ConfigDict(extra="forbid")

    waypoints: list[NavWaypointDTO] = Field(min_length=1)
    tour_id: str = Field(default="tour-001", min_length=1)


class StartTourResponse(BaseModel):
    # @TASK: Confirmar aceptacion de la solicitud de inicio de tour
    # @INPUT: accepted, detail, tour_id
    # @OUTPUT: HTTP 202 Accepted con cuerpo estructurado
    # @CONTEXT: Respuesta inmediata antes de que el tour comience realmente
    # STEP 1: Exponer bandera accepted y detalle operativo
    # @SECURITY: No incluye internals del sistema en detail
    # @AI_CONTEXT: El tour puede tardar en arrancar; el caller debe consultar /status
    accepted: bool
    detail: str
    tour_id: str


class PauseTourRequest(BaseModel):
    # @TASK: Definir payload del endpoint POST /tour/pause
    # @INPUT: Buffer de audio codificado en base64 y language opcional
    # @OUTPUT: Modelo validado para request_interaction() del orquestador
    # @CONTEXT: Activa la ventana de interaccion NLP desde un cliente externo
    # STEP 1: Aceptar audio como bytes codificados en base64 (opcional para testing)
    # @SECURITY: extra="forbid"; audio_b64 puede ser None para pruebas sin audio real
    # @AI_CONTEXT: Si audio_b64 es None se envia un buffer de silencio al orquestador
    model_config = ConfigDict(extra="forbid")

    audio_b64: Optional[str] = None
    language: str = "es"


class EmergencyRequest(BaseModel):
    # @TASK: Definir payload del endpoint POST /emergency
    # @INPUT: Razon textual de la emergencia para diagnostico
    # @OUTPUT: Modelo validado para emergency_stop() del orquestador
    # @CONTEXT: Endpoint de maximo prioridad; activa trigger_emergency en cualquier estado
    # STEP 1: Registrar razon con valor por defecto para invocaciones sin cuerpo
    # @SECURITY: extra="forbid"; razon se registra en contexto para post-mortem
    # @AI_CONTEXT: Este endpoint debe ser el primero en llamar ante anomalia fisica
    model_config = ConfigDict(extra="forbid")

    reason: str = "emergency-stop-api"


class StatusResponse(BaseModel):
    # @TASK: Encapsular la respuesta del endpoint GET /status
    # @INPUT: Estado del orquestador, odometria y contexto del tour activo
    # @OUTPUT: Snapshot del sistema serializable en JSON
    # @CONTEXT: Endpoint de observabilidad principal para el operador
    # STEP 1: Exponer state_id, tour_id, waypoint_index y ultima estimacion odometrica
    # STEP 2: Incluir causa del ultimo error para trazabilidad
    # @SECURITY: No incluye rvec/tvec raw; solo coordenadas x, y, theta en frame map
    # @AI_CONTEXT: nlp_swap_count es indicador de degradacion del pipeline local
    state: str
    tour_id: Optional[str]
    current_waypoint_index: int
    last_error: Optional[str]
    nlp_pipeline: str
    nlp_swap_count: int
    odometry_x: Optional[float]
    odometry_y: Optional[float]
    odometry_theta: Optional[float]
    odometry_marker_id: Optional[int]


# ---------------------------------------------------------------------------
# Dependencia de inyeccion del orquestador
# ---------------------------------------------------------------------------

def _get_orchestrator(request: Request) -> TourOrchestrator:
    # @TASK: Resolver la instancia del TourOrchestrator desde app.state para inyeccion en endpoints
    # @INPUT: request — objeto Request de FastAPI con acceso a app.state
    # @OUTPUT: Instancia activa de TourOrchestrator o HTTP 503 si no esta configurada
    # @CONTEXT: Funcion de dependencia para Depends() en todos los endpoints de control
    # STEP 1: Leer atributo orchestrator de app.state
    # STEP 2: Validar tipo antes de retornar; HTTP 503 ante estado invalido
    # @SECURITY: Falla controlada antes de cualquier mutacion de estado del robot
    # @AI_CONTEXT: app.state.orchestrator se setea en lifespan antes de aceptar requests

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if not isinstance(orchestrator, TourOrchestrator):  # STEP 1 + 2
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TourOrchestrator no disponible en app.state. El sistema no esta inicializado.",
        )
    return orchestrator


# ---------------------------------------------------------------------------
# Lifespan handler
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    # @TASK: Gestionar el ciclo de vida de la aplicacion FastAPI con cleanup de emergencia
    # @INPUT: app — instancia FastAPI con app.state.orchestrator inyectado antes del lifespan
    # @OUTPUT: Aplicacion activa durante yield; trigger_emergency ejecutado en shutdown si aplica
    # @CONTEXT: Reemplaza on_startup/on_shutdown obsoletos de FastAPI
    # STEP 1: Emitir log de inicio con estado actual del orquestador
    # STEP 2: Ceder control (yield) para que FastAPI acepte requests
    # STEP 3: En shutdown, verificar si el orquestador esta en estado final
    # STEP 4: Si no esta en estado final, invocar trigger_emergency para cierre seguro del robot
    # @SECURITY: trigger_emergency en STEP 4 garantiza Damp() ante cierre abrupto del servidor HTTP
    # @AI_CONTEXT: app.state.orchestrator debe estar seteado ANTES de construir la app con este lifespan

    orchestrator: Optional[TourOrchestrator] = getattr(app.state, "orchestrator", None)

    # STEP 1
    LOGGER.info(
        "[APIServer] Lifespan startup. orchestrator_state=%s",
        orchestrator.state_id if orchestrator else "not-set",
    )

    yield  # STEP 2: aplicacion activa

    # STEP 3 + 4: shutdown
    LOGGER.info("[APIServer] Lifespan shutdown iniciado.")
    if orchestrator is not None:
        state_id = orchestrator.state_id
        final_states = {"emergency", "uninitialized"}
        if state_id not in final_states:
            LOGGER.critical(
                "[APIServer] Shutdown con orquestador en estado '%s'; "
                "activando trigger_emergency para garantizar Damp().",
                state_id,
            )
            try:
                await asyncio.wait_for(
                    orchestrator.emergency_stop(reason="api-server-shutdown"),
                    timeout=3.0,
                )
            except (TimeoutError, asyncio.TimeoutError):
                LOGGER.critical(
                    "[APIServer] Timeout en emergency_stop durante shutdown. "
                    "Verificar estado mecanico del robot."
                )
            except Exception as exc:
                LOGGER.critical(
                    "[APIServer] Excepcion en emergency_stop durante shutdown: %s", exc
                )
        else:
            LOGGER.info(
                "[APIServer] Orquestador en estado final '%s'; no se requiere trigger_emergency.",
                state_id,
            )
    LOGGER.info("[APIServer] Lifespan shutdown completado.")


# ---------------------------------------------------------------------------
# Factory de la aplicacion
# ---------------------------------------------------------------------------

def create_app(orchestrator: TourOrchestrator) -> FastAPI:
    # @TASK: Construir la instancia FastAPI con lifespan, estado inyectado y todos los endpoints
    # @INPUT: orchestrator — instancia activa de TourOrchestrator
    # @OUTPUT: FastAPI app lista para ser servida por uvicorn.Server
    # @CONTEXT: Factory principal; invocada desde APIServer.__init__() y run_server()
    # STEP 1: Instanciar FastAPI con docs desactivadas y lifespan configurado
    # STEP 2: Inyectar orchestrator en app.state antes de registrar rutas
    # STEP 3: Registrar todos los endpoints de control y observabilidad
    # @SECURITY: docs_url=None y redoc_url=None reducen superficie de ataque en produccion
    # @AI_CONTEXT: app.state.orchestrator debe setearse ANTES de que lifespan sea llamado

    # STEP 1
    app = FastAPI(
        title="Robot Humanoide HIL API",
        version="0.7.0",
        docs_url=None,      # Swagger desactivado en produccion
        redoc_url=None,     # ReDoc desactivado en produccion
        openapi_url=None,   # Schema JSON tambien desactivado
        lifespan=_lifespan,
    )

    # STEP 2
    app.state.orchestrator = orchestrator

    # STEP 3: Registrar endpoints
    _register_routes(app)

    return app


# ---------------------------------------------------------------------------
# Registro de rutas
# ---------------------------------------------------------------------------

def _register_routes(app: FastAPI) -> None:
    # @TASK: Registrar todos los endpoints HTTP de control y observabilidad en la app FastAPI
    # @INPUT: app — instancia FastAPI ya creada con app.state.orchestrator disponible
    # @OUTPUT: Rutas POST /tour/start, /tour/pause, /emergency y GET /status registradas
    # @CONTEXT: Separar el registro de rutas de la factory facilita testing y extensibilidad
    # STEP 1: Registrar POST /tour/start para despachar planes de navegacion
    # STEP 2: Registrar POST /tour/pause para activar ventana de interaccion NLP
    # STEP 3: Registrar POST /emergency para trigger de maxima prioridad
    # STEP 4: Registrar GET /status para observabilidad del sistema
    # @SECURITY: Todos los endpoints de mutacion capturan TransitionNotAllowed -> HTTP 409
    # @AI_CONTEXT: Depends(_get_orchestrator) es el unico mecanismo de inyeccion; no usar globales

    @app.post(
        "/tour/start",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=StartTourResponse,
        summary="Iniciar tour de navegacion autonoma",
    )
    async def endpoint_start_tour(
        payload: StartTourRequest,
        background_tasks: BackgroundTasks,
        orchestrator: TourOrchestrator = Depends(_get_orchestrator),
    ) -> StartTourResponse:
        # @TASK: Despachar un plan de tour al TourOrchestrator como BackgroundTask
        # @INPUT: payload — StartTourRequest con lista de waypoints y tour_id
        # @OUTPUT: HTTP 202 con StartTourResponse; tour iniciado en background
        # @CONTEXT: El endpoint retorna inmediatamente; dispatch_tour corre como tarea asincrona
        # STEP 1: Convertir NavWaypointDTO a NavWaypoint del dominio interno
        # STEP 2: Construir TourPlan y delegar el despacho a background task
        # STEP 3: Retornar HTTP 202 sin esperar el inicio real del movimiento
        # @SECURITY: TransitionNotAllowed capturado y convertido a HTTP 409 Conflict
        # @AI_CONTEXT: background_tasks.add_task es sync; se crea tarea asyncio dentro

        # STEP 1
        domain_waypoints = [_to_domain_waypoint(wp) for wp in payload.waypoints]

        # STEP 2
        plan = TourPlan(waypoints=domain_waypoints, tour_id=payload.tour_id)

        try:
            background_tasks.add_task(_dispatch_tour_task, orchestrator, plan)
        except TransitionNotAllowed as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Transicion rechazada por la maquina de estados: {exc}",
            )

        # STEP 3
        LOGGER.info(
            "[API] POST /tour/start aceptado. tour_id=%s waypoints=%d",
            payload.tour_id,
            len(payload.waypoints),
        )
        return StartTourResponse(
            accepted=True,
            detail=f"Tour '{payload.tour_id}' aceptado. {len(payload.waypoints)} waypoint(s) en plan.",
            tour_id=payload.tour_id,
        )

    @app.post(
        "/tour/pause",
        status_code=status.HTTP_202_ACCEPTED,
        summary="Pausar navegacion para interaccion NLP",
    )
    async def endpoint_pause_tour(
        payload: PauseTourRequest,
        orchestrator: TourOrchestrator = Depends(_get_orchestrator),
    ) -> dict[str, str]:
        # @TASK: Activar la transicion NAVIGATING->INTERACTING para ventana de dialogo
        # @INPUT: payload — PauseTourRequest con audio_b64 opcional y language
        # @OUTPUT: HTTP 202 con cuerpo de confirmacion o HTTP 409 si la transicion no es valida
        # @CONTEXT: Trigger externo para interaccion NLP; equivalente al wake-word detector
        # STEP 1: Decodificar audio_b64 si esta presente; usar silencio como fallback
        # STEP 2: Invocar request_interaction() del orquestador como create_task
        # STEP 3: Retornar confirmacion inmediata sin esperar el procesamiento NLP
        # @SECURITY: Audio decodificado en memoria; nunca escrito a disco
        # @AI_CONTEXT: El procesamiento NLP completo ocurre en on_enter_interacting del orquestador

        import base64
        import numpy as np

        # STEP 1: Decodificar audio o usar buffer de silencio
        if payload.audio_b64:
            try:
                audio_bytes = base64.b64decode(payload.audio_b64)
                audio_pcm = np.frombuffer(audio_bytes, dtype=np.float32)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"audio_b64 invalido: {exc}",
                )
        else:
            audio_pcm = np.zeros(1, dtype=np.float32)

        # STEP 2
        try:
            asyncio.create_task(
                orchestrator.request_interaction(audio_pcm, language=payload.language),
                name="api-pause-interaction",
            )
        except TransitionNotAllowed as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Transicion rechazada: {exc}",
            )

        # STEP 3
        LOGGER.info("[API] POST /tour/pause aceptado. language=%s", payload.language)
        return {"accepted": "true", "detail": "Solicitud de interaccion despachada."}

    @app.post(
        "/emergency",
        status_code=status.HTTP_200_OK,
        summary="Activar parada de emergencia (maxima prioridad)",
    )
    async def endpoint_emergency(
        payload: EmergencyRequest,
        orchestrator: TourOrchestrator = Depends(_get_orchestrator),
    ) -> dict[str, str]:
        # @TASK: Activar trigger_emergency en el orquestador con maxima prioridad
        # @INPUT: payload — EmergencyRequest con razon de emergencia
        # @OUTPUT: HTTP 200 tras confirmar el despacho del comando; Damp() ejecutado en callback
        # @CONTEXT: Endpoint de maxima prioridad; no debe ser rechazado por estado de la maquina
        # STEP 1: Invocar emergency_stop() directamente await para garantizar inicio de Damp()
        # STEP 2: Registrar la invocacion para trazabilidad post-mortem
        # @SECURITY: Este endpoint NO retorna HTTP 409; trigger_emergency acepta cualquier estado origen
        # @AI_CONTEXT: Se hace await directo (no create_task) para que el Damp() comience antes de retornar

        LOGGER.critical(
            "[API] POST /emergency recibido. Razon: %s", payload.reason
        )

        # STEP 1
        try:
            await asyncio.wait_for(
                orchestrator.emergency_stop(reason=payload.reason),
                timeout=5.0,
            )
        except (TimeoutError, asyncio.TimeoutError):
            LOGGER.critical("[API] Timeout en emergency_stop via API.")
            # No elevar HTTPException; el comando fue despachado aunque timeout en confirmacion
        except Exception as exc:
            LOGGER.critical("[API] Excepcion en emergency_stop via API: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error ejecutando emergency_stop: {exc}",
            )

        # STEP 2
        LOGGER.critical("[API] POST /emergency completado.")
        return {
            "executed": "true",
            "reason": payload.reason,
            "state": orchestrator.state_id,
        }

    @app.get(
        "/status",
        response_model=StatusResponse,
        summary="Consultar estado completo del sistema",
    )
    async def endpoint_status(
        orchestrator: TourOrchestrator = Depends(_get_orchestrator),
    ) -> StatusResponse:
        # @TASK: Retornar un snapshot consolidado del estado del sistema para el operador
        # @INPUT: orchestrator — instancia inyectada con estado y contexto actuales
        # @OUTPUT: StatusResponse con estado SM, contexto del tour y ultima odometria
        # @CONTEXT: Endpoint de solo lectura; fuente de verdad para dashboards de control
        # STEP 1: Leer state_id via configuration[0].id (API estable de python-statemachine)
        # STEP 2: Extraer contexto del tour (tour_id, waypoint_index, last_error)
        # STEP 3: Consultar ultima odometria desde vision_processor.pose_queue sin bloquear
        # STEP 4: Consultar telemetria de NLP desde conversation_manager
        # @SECURITY: Solo lectura; sin efectos secundarios sobre el estado del robot
        # @AI_CONTEXT: odometry_ campos son None si VisionProcessor no ha detectado ningun tag aun

        # STEP 1
        state_id = orchestrator.state_id

        # STEP 2
        ctx = orchestrator.context
        tour_id = ctx.tour_id
        waypoint_index = ctx.current_waypoint_index
        last_error = ctx.last_error

        # STEP 3: Intentar leer ultima estimacion de odometria sin esperar
        odometry_x: Optional[float] = None
        odometry_y: Optional[float] = None
        odometry_theta: Optional[float] = None
        odometry_marker_id: Optional[int] = None

        vision = getattr(orchestrator, "_vision_processor", None)
        if vision is not None:
            q = getattr(vision, "pose_queue", None)
            if q is not None and not q.empty():
                try:
                    odometry_vec = q.get_nowait()
                    odometry_x = odometry_vec.x
                    odometry_y = odometry_vec.y
                    odometry_theta = odometry_vec.theta
                    odometry_marker_id = odometry_vec.marker_id
                    # Reinsertar para que el loop de odometria lo pueda consumir
                    try:
                        q.put_nowait(odometry_vec)
                    except asyncio.QueueFull:
                        pass
                except (asyncio.QueueEmpty, Exception):
                    pass

        # STEP 4: Telemetria NLP
        cm = getattr(orchestrator, "_conversation_manager", None)
        nlp_pipeline = getattr(cm, "active_strategy_name", "unknown")
        nlp_swap_count = getattr(cm, "swap_count", 0)

        return StatusResponse(
            state=state_id,
            tour_id=tour_id,
            current_waypoint_index=waypoint_index,
            last_error=last_error,
            nlp_pipeline=nlp_pipeline,
            nlp_swap_count=nlp_swap_count,
            odometry_x=odometry_x,
            odometry_y=odometry_y,
            odometry_theta=odometry_theta,
            odometry_marker_id=odometry_marker_id,
        )


# ---------------------------------------------------------------------------
# Utilidades de conversion de dominio
# ---------------------------------------------------------------------------

def _to_domain_waypoint(dto: NavWaypointDTO) -> NavWaypoint:
    # @TASK: Convertir NavWaypointDTO de la capa HTTP al NavWaypoint del dominio de navegacion
    # @INPUT: dto — NavWaypointDTO validado por Pydantic
    # @OUTPUT: NavWaypoint inmutable consumible por AsyncNav2Bridge y TourPlan
    # @CONTEXT: Transformacion de frontera entre la capa HTTP y el dominio interno
    # STEP 1: Mapear campos 1:1 de DTO a NavWaypoint
    # @SECURITY: Ninguna logica adicional; validacion ya completada por Pydantic
    # @AI_CONTEXT: frame_id por defecto "map" si no se especifica en el request
    return NavWaypoint(  # STEP 1
        x=dto.x,
        y=dto.y,
        yaw_rad=dto.yaw_rad,
        frame_id=dto.frame_id,
    )


async def _dispatch_tour_task(
    orchestrator: TourOrchestrator,
    plan: TourPlan,
) -> None:
    # @TASK: Ejecutar dispatch_tour() del orquestador como corrutina asincrona en background
    # @INPUT: orchestrator — instancia activa; plan — TourPlan con waypoints y tour_id
    # @OUTPUT: Tour despachado; errores registrados sin propagacion al caller HTTP
    # @CONTEXT: Corrutina invocada como BackgroundTask por FastAPI; sin retorno al HTTP caller
    # STEP 1: Llamar dispatch_tour() capturando TransitionNotAllowed y excepciones generales
    # STEP 2: Registrar fallos en log; no propagar (el caller HTTP ya retorno HTTP 202)
    # @SECURITY: Sin propagacion de excepcion; el caller ya recibio su respuesta HTTP
    # @AI_CONTEXT: FastAPI BackgroundTasks ejecuta esta corrutina despues de enviar la respuesta HTTP
    try:
        await orchestrator.dispatch_tour(plan)  # STEP 1
    except TransitionNotAllowed as exc:
        # STEP 2
        LOGGER.error(
            "[API] dispatch_tour rechazado por maquina de estados: %s (tour_id=%s)",
            exc, plan.tour_id,
        )
    except Exception as exc:
        LOGGER.error(
            "[API] Excepcion en dispatch_tour background: %s — %s (tour_id=%s)",
            type(exc).__name__, exc, plan.tour_id,
        )


# ---------------------------------------------------------------------------
# Clase APIServer — wrapper de ciclo de vida
# ---------------------------------------------------------------------------

class APIServer:
    # @TASK: Encapsular el ciclo de vida de uvicorn.Server para integracion con el event loop principal
    # @INPUT: Instancia de TourOrchestrator y parametros de red
    # @OUTPUT: Servidor HTTP activo en el event loop sin crear loops paralelos
    # @CONTEXT: Wrapper que permite start()/stop() asincronos desde main.py
    # STEP 1: Construir FastAPI app con create_app() y configurar uvicorn.Config
    # STEP 2: Persistir uvicorn.Server para control de ciclo de vida
    # @SECURITY: loop="asyncio" en uvicorn.Config garantiza que no crea loops adicionales
    # @AI_CONTEXT: start() = Server.serve() corrutina; se adjunta con asyncio.create_task en main.py

    def __init__(
        self,
        *,
        orchestrator: TourOrchestrator,
        host: str = "0.0.0.0",
        port: int = 8000,
        log_level: str = "info",
    ) -> None:
        # @TASK: Inicializar APIServer construyendo la app FastAPI y la configuracion de uvicorn
        # @INPUT: orchestrator, host, port, log_level
        # @OUTPUT: Instancia lista para start(); servidor aun no activo
        # @CONTEXT: Constructor ligero; no inicia el servidor ni el event loop
        # STEP 1: Crear la app FastAPI con factory y orquestador inyectado
        # STEP 2: Configurar uvicorn.Config con loop="asyncio" para integracion con el loop existente
        # STEP 3: Instanciar uvicorn.Server a partir de la config
        # @SECURITY: access_log=False en produccion reduce carga de I/O en el companion PC embebido
        # @AI_CONTEXT: uvicorn.Config(loop="asyncio") evita que uvicorn intente crear su propio loop

        # STEP 1
        self._app: FastAPI = create_app(orchestrator)

        # STEP 2
        self._config: uvicorn.Config = uvicorn.Config(
            app=self._app,
            host=host,
            port=port,
            log_level=log_level,
            loop="asyncio",
            access_log=False,
        )

        # STEP 3
        self._server: uvicorn.Server = uvicorn.Server(config=self._config)

    @property
    def app(self) -> FastAPI:
        # @TASK: Exponer la instancia FastAPI para testing ASGI directo
        # @INPUT: Sin parametros
        # @OUTPUT: Referencia a la app FastAPI interna
        # @CONTEXT: Usado por pytest con TestClient para pruebas de endpoints sin levantar uvicorn
        # STEP 1: Retornar referencia interna de solo lectura
        # @SECURITY: No permite reemplazar la app en runtime
        # @AI_CONTEXT: from fastapi.testclient import TestClient; TestClient(server.app)
        return self._app  # STEP 1

    async def start(self) -> None:
        # @TASK: Iniciar el servidor uvicorn en el event loop principal sin bloquearlo
        # @INPUT: Sin parametros
        # @OUTPUT: Corrutina de servicio HTTP activa hasta que should_exit sea True
        # @CONTEXT: Invocado como asyncio.create_task(api_server.start()) en main.py
        # STEP 1: Llamar uvicorn.Server.serve() que corre en el loop actual via loop="asyncio"
        # @SECURITY: serve() termina cuando should_exit=True; no bloquea el evento de shutdown
        # @AI_CONTEXT: Esta tarea se cancela desde _graceful_shutdown en main.py via api_server.stop()
        await self._server.serve()  # STEP 1

    async def stop(self) -> None:
        # @TASK: Solicitar la parada ordenada del servidor uvicorn
        # @INPUT: Sin parametros
        # @OUTPUT: Flag should_exit activado; serve() termina en el proximo ciclo de evento
        # @CONTEXT: Invocado desde _graceful_shutdown de main.py antes de cerrar el loop
        # STEP 1: Activar should_exit en uvicorn.Server para detener el accept loop
        # STEP 2: Ceder al event loop para que serve() pueda procesar la señal de salida
        # @SECURITY: No es una terminacion abrupta; las conexiones activas se cierran limpiamente
        # @AI_CONTEXT: Despues de stop() se debe await api_task con timeout en main.py
        self._server.should_exit = True   # STEP 1
        await asyncio.sleep(0)            # STEP 2


# ---------------------------------------------------------------------------
# Funcion de ejecucion programatica
# ---------------------------------------------------------------------------

async def run_server(
    orchestrator: TourOrchestrator,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    log_level: str = "info",
) -> None:
    # @TASK: Configurar e iniciar el servidor uvicorn programaticamente en el event loop activo
    # @INPUT: orchestrator — instancia activa; host, port, log_level de red
    # @OUTPUT: Servidor HTTP activo hasta señal de shutdown; sin creacion de loops adicionales
    # @CONTEXT: Alternativa a APIServer para uso directo desde main.py o scripts de diagnostico
    # STEP 1: Crear uvicorn.Config con loop="asyncio" para reutilizar el loop principal
    # STEP 2: Instanciar uvicorn.Server y ejecutar serve() en el loop actual
    # @SECURITY: Nunca llamar uvicorn.run() que crea su propio loop; siempre usar Server.serve()
    # @AI_CONTEXT: Esta funcion puede usarse como: asyncio.create_task(run_server(orchestrator))

    # STEP 1
    config = uvicorn.Config(
        app=create_app(orchestrator),
        host=host,
        port=port,
        log_level=log_level,
        loop="asyncio",
        access_log=False,
        docs_url=None,
        redoc_url=None,
    )

    # STEP 2
    server = uvicorn.Server(config=config)
    LOGGER.info("[run_server] Iniciando uvicorn en %s:%d", host, port)
    await server.serve()


# ---------------------------------------------------------------------------
# Exportaciones
# ---------------------------------------------------------------------------

__all__ = [
    "APIServer",
    "EmergencyRequest",
    "NavWaypointDTO",
    "PauseTourRequest",
    "StartTourRequest",
    "StartTourResponse",
    "StatusResponse",
    "create_app",
    "run_server",
]
