from __future__ import annotations

import asyncio
from typing import Any, Optional

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.core import TourOrchestrator


class StartTourRequest(BaseModel):
    # @TASK: Definir payload inicio
    # @INPUT: waypoint_id
    # @OUTPUT: Modelo validado para POST /tour/start
    # @CONTEXT: Contrato de entrada del trigger REST
    # STEP 1: Exigir waypoint_id no vacio
    # STEP 2: Normalizar request para capa orquestador
    # @SECURITY: Minimiza superficie de entrada con schema estricto
    # @AI_CONTEXT: Punto de extension para parametros de sesion futura
    model_config = ConfigDict(extra="forbid")

    waypoint_id: str = Field(min_length=1)


class StartTourResponse(BaseModel):
    # @TASK: Definir ack inicio
    # @INPUT: accepted, detail
    # @OUTPUT: Respuesta HTTP 202 estructurada
    # @CONTEXT: Confirmacion inmediata de ejecucion en background
    # STEP 1: Exponer bandera accepted
    # STEP 2: Incluir detalle operativo para clientes
    # @SECURITY: No filtra internals del sistema
    # @AI_CONTEXT: Respuesta estable para integraciones de frontend
    accepted: bool
    detail: str


class TourStatusResponse(BaseModel):
    # @TASK: Definir estado tour
    # @INPUT: state, waypoint_id, last_error
    # @OUTPUT: Estado consolidado del orquestador
    # @CONTEXT: Endpoint de observabilidad GET /tour/status
    # STEP 1: Exponer estado actual de la maquina
    # STEP 2: Adjuntar contexto operativo relevante
    # @SECURITY: Evita exponer trazas sensibles
    # @AI_CONTEXT: Consumido por dashboards de control en tiempo real
    state: str
    waypoint_id: Optional[str]
    last_error: Optional[str]


def get_tour_orchestrator(request: Request) -> TourOrchestrator:
    # @TASK: Resolver instancia orquestador
    # @INPUT: request
    # @OUTPUT: TourOrchestrator activo de app.state
    # @CONTEXT: Mecanismo de inyeccion para endpoints FastAPI
    # STEP 1: Leer atributo tour_orchestrator desde app.state
    # STEP 2: Validar tipo y retornar instancia operativa
    # @SECURITY: Falla controlada si no hay estado configurado
    # @AI_CONTEXT: Permite reemplazar instancia en tests de API
    orchestrator = getattr(request.app.state, "tour_orchestrator", None)
    if not isinstance(orchestrator, TourOrchestrator):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TourOrchestrator no inicializado en la API.",
        )
    return orchestrator


def create_app(orchestrator: TourOrchestrator) -> FastAPI:
    # @TASK: Crear aplicacion FastAPI
    # @INPUT: orchestrator
    # @OUTPUT: FastAPI app configurada con rutas de tour
    # @CONTEXT: Capa Trigger REST para iniciar y monitorear guiado
    # STEP 1: Instanciar app y guardar orquestador en app.state
    # STEP 2: Registrar endpoints POST/GET del dominio tour
    # @SECURITY: Conserva ejecucion no bloqueante con background tasks
    # @AI_CONTEXT: Factory util para bootstrap y pruebas de integracion
    app = FastAPI(title="Robot Humanoide Trigger API", version="0.1.0")
    app.state.tour_orchestrator = orchestrator

    @app.post(
        "/tour/start",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=StartTourResponse,
    )
    async def start_tour(
        payload: StartTourRequest,
        background_tasks: BackgroundTasks,
        orchestrator_dep: TourOrchestrator = Depends(get_tour_orchestrator),
    ) -> StartTourResponse:
        # @TASK: Disparar inicio tour
        # @INPUT: payload, background_tasks, orchestrator_dep
        # @OUTPUT: HTTP 202 Accepted inmediato
        # @CONTEXT: Trigger asincrono para TourOrchestrator.start_tour
        # STEP 1: Programar tarea background que crea coroutine task
        # STEP 2: Responder sin bloquear thread del servidor web
        # @SECURITY: Evita bloqueo del worker HTTP ante operaciones largas
        # @AI_CONTEXT: Diseñado para llamada remota de boton iniciar tour
        background_tasks.add_task(
            _schedule_start_tour,
            orchestrator_dep,
            payload.waypoint_id,
        )
        return StartTourResponse(
            accepted=True,
            detail=(
                "Solicitud aceptada. El tour se inicio en segundo plano para "
                f"waypoint_id={payload.waypoint_id}."
            ),
        )

    @app.get("/tour/status", response_model=TourStatusResponse)
    async def get_tour_status(
        orchestrator_dep: TourOrchestrator = Depends(get_tour_orchestrator),
    ) -> TourStatusResponse:
        # @TASK: Consultar estado tour
        # @INPUT: orchestrator_dep
        # @OUTPUT: TourStatusResponse
        # @CONTEXT: Lectura del estado de la maquina del orquestador
        # STEP 1: Resolver nombre de estado actual de forma robusta
        # STEP 2: Adjuntar contexto operativo actual
        # @SECURITY: Endpoint de solo lectura sin efectos secundarios
        # @AI_CONTEXT: Fuente de verdad para monitoreo externo
        state_name = getattr(orchestrator_dep, "state_id", None)
        if not isinstance(state_name, str) or len(state_name) == 0:
            current_state_obj = getattr(orchestrator_dep, "current_state", None)
            state_name = _resolve_state_name(current_state_obj)
        context = orchestrator_dep.context
        return TourStatusResponse(
            state=state_name,
            waypoint_id=context.current_waypoint_id,
            last_error=context.last_error,
        )

    return app


async def _schedule_start_tour(orchestrator: TourOrchestrator, waypoint_id: str) -> None:
    # @TASK: Encolar inicio asincrono
    # @INPUT: orchestrator, waypoint_id
    # @OUTPUT: asyncio.Task creada en loop activo
    # @CONTEXT: Bridge sync->async para FastAPI BackgroundTasks
    # STEP 1: Obtener event loop activo del worker async de Starlette
    # STEP 2: Crear task no bloqueante para start_tour
    # @SECURITY: Evita ejecucion directa bloqueante en hilo HTTP
    # @AI_CONTEXT: Mantiene semantica fire-and-forget del endpoint
    loop = asyncio.get_running_loop()
    loop.create_task(orchestrator.start_tour(waypoint_id))
    await asyncio.sleep(0)


def _resolve_state_name(state_obj: Any) -> str:
    # @TASK: Resolver nombre estado
    # @INPUT: state_obj
    # @OUTPUT: Nombre string del estado actual
    # @CONTEXT: Compatibilidad entre representaciones de python-statemachine
    # STEP 1: Intentar atributo name y luego id
    # STEP 2: Aplicar fallback a repr en caso extremo
    # @SECURITY: Evita fallo por cambios de libreria
    # @AI_CONTEXT: Normaliza respuesta para API estable
    name = getattr(state_obj, "name", None)
    if isinstance(name, str) and len(name) > 0:
        return name

    state_id = getattr(state_obj, "id", None)
    if isinstance(state_id, str) and len(state_id) > 0:
        return state_id

    return str(state_obj)


class APIServer:
    def __init__(
        self,
        *,
        orchestrator: TourOrchestrator,
        host: str = "0.0.0.0",
        port: int = 8000,
        log_level: str = "info",
    ) -> None:
        # @TASK: Inicializar servidor API
        # @INPUT: orchestrator, host, port, log_level
        # @OUTPUT: Instancia lista para start async
        # @CONTEXT: Runner programatico de FastAPI sobre uvicorn.Server
        # STEP 1: Crear app con factory y orquestador inyectado
        # STEP 2: Construir uvicorn.Config reutilizable
        # @SECURITY: Evita uso de uvicorn.run bloqueante
        # @AI_CONTEXT: Diseñado para integrarse via asyncio.create_task
        self._app: FastAPI = create_app(orchestrator)
        self._config: uvicorn.Config = uvicorn.Config(
            app=self._app,
            host=host,
            port=port,
            log_level=log_level,
            loop="asyncio",
        )
        self._server: uvicorn.Server = uvicorn.Server(config=self._config)

    @property
    def app(self) -> FastAPI:
        # @TASK: Exponer app FastAPI
        # @INPUT: Sin parametros
        # @OUTPUT: Referencia FastAPI interna
        # @CONTEXT: Integracion opcional con pruebas/ASGI tools
        # STEP 1: Retornar instancia app configurada
        # STEP 2: Permitir uso externo controlado
        # @SECURITY: Solo lectura de referencia de app
        # @AI_CONTEXT: Facilita testeo de endpoints sin levantar servidor real
        return self._app

    async def start(self) -> None:
        # @TASK: Iniciar uvicorn server
        # @INPUT: Sin parametros
        # @OUTPUT: Corrutina de servicio HTTP activa
        # @CONTEXT: Metodo attachable con asyncio.create_task
        # STEP 1: Ejecutar server.serve de forma asíncrona
        # STEP 2: Mantener ciclo de vida hasta shutdown
        # @SECURITY: Mantiene event loop principal operativo
        # @AI_CONTEXT: Reemplaza patrones bloqueantes basados en uvicorn.run
        await self._server.serve()

    async def stop(self) -> None:
        # @TASK: Solicitar parada servidor
        # @INPUT: Sin parametros
        # @OUTPUT: Flag de salida activado
        # @CONTEXT: Control de shutdown del componente Trigger API
        # STEP 1: Marcar should_exit en uvicorn.Server
        # STEP 2: Ceder control al event loop para cierre limpio
        # @SECURITY: Permite apagado ordenado sin kill abrupto
        # @AI_CONTEXT: Invocable desde gestor de ciclo de vida principal
        self._server.should_exit = True
        await asyncio.sleep(0)


__all__ = [
    "APIServer",
    "StartTourRequest",
    "StartTourResponse",
    "TourStatusResponse",
    "create_app",
    "get_tour_orchestrator",
]