from __future__ import annotations

# @TASK: Definir router FastAPI con endpoints de control, observabilidad y gestion de contenido
# @INPUT: TourOrchestrator y ConversationManager inyectados via app.state
# @OUTPUT: APIRouter con POST /tour/start, /tour/pause, /emergency, GET /status,
#          GET /content/script, POST /content/script/reload
# @CONTEXT: Capa de interfaz HTTP; cero logica de negocio en este archivo
# @SECURITY: TransitionNotAllowed → HTTP 409; docs desactivadas en produccion
# STEP 1: Registrar endpoints de mutacion de estado (POST)
# STEP 2: Registrar endpoints de observabilidad (GET)
# STEP 3: Registrar endpoints de gestion de contenido (GET/POST)

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from statemachine.exceptions import TransitionNotAllowed
from src.api.websocket_manager import TelemetryManager

from .schemas import (
    EmergencyRequest,
    PauseTourRequest,
    QuestionRequest,
    QuestionResponse,
    ScriptReloadResponse,
    StartTourRequest,
    StartTourResponse,
    StatusResponse,
    TourScript,
)

LOGGER = logging.getLogger("otto_guide.api.router")

router = APIRouter()
telemetry_manager = TelemetryManager()


# ---------------------------------------------------------------------------
# Dependencia de inyeccion
# ---------------------------------------------------------------------------

def _get_orchestrator(request: Request):
    """
    @TASK: Resolver TourOrchestrator desde app.state
    @INPUT: request
    @OUTPUT: Instancia activa o HTTP 503
    @CONTEXT: Mecanismo de DI para todos los endpoints
    @SECURITY: Falla antes de cualquier mutacion si no hay orquestador
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TourOrchestrator no disponible. El sistema no esta inicializado.",
        )
    return orchestrator


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/tour/start",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StartTourResponse,
    summary="Iniciar tour de navegacion autonoma",
)
async def endpoint_start_tour(
    payload: StartTourRequest,
    background_tasks: BackgroundTasks,
    orchestrator=Depends(_get_orchestrator),
) -> StartTourResponse:
    """
    @TASK: Despachar plan de tour al orchestrator en background
    @INPUT: payload con waypoints y tour_id
    @OUTPUT: HTTP 202 Accepted
    @CONTEXT: El endpoint retorna inmediatamente; dispatch corre en background
    @SECURITY: TransitionNotAllowed → HTTP 409
    """
    from src.navigation import NavWaypoint
    from src.core import TourPlan

    domain_waypoints = [
        NavWaypoint(x=wp.x, y=wp.y, yaw_rad=wp.yaw_rad, frame_id=wp.frame_id)
        for wp in payload.waypoints
    ]
    plan = TourPlan(waypoints=domain_waypoints, tour_id=payload.tour_id)

    async def _dispatch():
        try:
            await orchestrator.dispatch_tour(plan)
        except TransitionNotAllowed as exc:
            LOGGER.error("[API] dispatch_tour rechazado: %s", exc)
        except Exception as exc:
            LOGGER.error("[API] Excepcion en dispatch_tour: %s", exc)

    background_tasks.add_task(_dispatch)

    LOGGER.info(
        "[API] POST /tour/start aceptado. tour_id=%s waypoints=%d",
        payload.tour_id, len(payload.waypoints),
    )
    return StartTourResponse(
        accepted=True,
        detail=f"Tour '{payload.tour_id}' aceptado. {len(payload.waypoints)} waypoint(s).",
        tour_id=payload.tour_id,
    )


@router.post(
    "/tour/pause",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Pausar navegacion para interaccion NLP",
)
async def endpoint_pause_tour(
    payload: PauseTourRequest,
    orchestrator=Depends(_get_orchestrator),
) -> dict:
    """
    @TASK: Activar transicion NAVIGATING→INTERACTING
    @INPUT: payload con audio_b64 opcional
    @OUTPUT: HTTP 202
    @CONTEXT: Trigger externo para ventana de dialogo
    @SECURITY: Audio decodificado en memoria; nunca escrito a disco
    """
    import base64
    import numpy as np

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

    return {"accepted": "true", "detail": "Solicitud de interaccion despachada."}


@router.post(
    "/emergency",
    status_code=status.HTTP_200_OK,
    summary="Activar parada de emergencia (maxima prioridad)",
)
async def endpoint_emergency(
    payload: EmergencyRequest,
    orchestrator=Depends(_get_orchestrator),
) -> dict:
    """
    @TASK: Trigger de emergencia con Damp() inmediato
    @INPUT: payload con reason
    @OUTPUT: HTTP 200 tras despacho
    @CONTEXT: Maxima prioridad; acepta cualquier estado origen
    @SECURITY: await directo para que Damp() inicie antes de retornar
    """
    LOGGER.critical("[API] POST /emergency recibido. Razon: %s", payload.reason)

    try:
        await asyncio.wait_for(
            orchestrator.emergency_stop(reason=payload.reason),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        LOGGER.critical("[API] Timeout en emergency_stop.")
    except Exception as exc:
        LOGGER.critical("[API] Excepcion en emergency_stop: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error ejecutando emergency_stop: {exc}",
        )

    return {
        "executed": "true",
        "reason": payload.reason,
        "state": orchestrator.state_id,
    }


@router.post(
    "/api/emergency_stop",
    status_code=status.HTTP_200_OK,
    summary="Kill switch de emergencia para detener la FSM inmediatamente",
)
async def endpoint_emergency_stop(
    orchestrator=Depends(_get_orchestrator),
) -> dict:
    nav_bridge = getattr(orchestrator, "_nav_bridge", None)
    if nav_bridge is not None:
        try:
            await asyncio.wait_for(nav_bridge.cancel_navigation(), timeout=1.0)
        except Exception as exc:
            LOGGER.error("[API] cancel_navigation previo a kill switch fallo: %s", exc)

    try:
        await asyncio.wait_for(orchestrator.emergency_stop(reason="api_kill_switch"), timeout=5.0)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Timeout activando emergency_stop",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error activando emergency_stop: {exc}",
        )

    return {"status": "emergency_engaged"}


@router.websocket("/ws/telemetry")
async def websocket_telemetry(
    websocket: WebSocket,
) -> None:
    await telemetry_manager.connect(websocket)
    try:
        orchestrator = getattr(websocket.app.state, "orchestrator", None)
        if orchestrator is not None and hasattr(orchestrator, "build_telemetry_payload"):
            payload = await orchestrator.build_telemetry_payload()
            await websocket.send_json(payload)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await telemetry_manager.disconnect(websocket)
    except Exception:
        await telemetry_manager.disconnect(websocket)
        raise


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Consultar estado completo del sistema",
)
async def endpoint_status(
    orchestrator=Depends(_get_orchestrator),
) -> StatusResponse:
    """
    @TASK: Snapshot del estado del sistema
    @INPUT: orchestrator
    @OUTPUT: StatusResponse serializado
    @CONTEXT: Solo lectura; sin efectos secundarios
    @SECURITY: Endpoint de observabilidad sin mutacion de estado
    """
    ctx = orchestrator.context
    return StatusResponse(
        state=orchestrator.state_id,
        tour_id=ctx.tour_id,
        current_waypoint_index=ctx.current_waypoint_index,
        last_error=ctx.last_error,
    )


@router.post(
    "/question",
    response_model=QuestionResponse,
    summary="Enviar pregunta de texto al ConversationManager",
)
async def endpoint_question(
    payload: QuestionRequest,
    orchestrator=Depends(_get_orchestrator),
) -> QuestionResponse:
    """
    @TASK: Procesar pregunta de texto via ConversationManager
    @INPUT: payload con text y language
    @OUTPUT: QuestionResponse con respuesta y pipeline utilizado
    @CONTEXT: Compatibilidad con interfaz de texto directa
    @SECURITY: Sin ejecucion de STT; texto plano
    """
    try:
        response = await orchestrator.handle_user_question(payload.text)
        return QuestionResponse(
            answer=response.answer_text,
            source_pipeline=response.source_pipeline,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error procesando pregunta: {exc}",
        )


# ---------------------------------------------------------------------------
# Endpoints de Gestion de Contenido (TAREA 3)
# ---------------------------------------------------------------------------

_SCRIPT_DEFAULT_PATH = Path("data/mvp_tour_script.json")


def _get_conversation_manager(request: Request):
    """
    @TASK: Resolver ConversationManager desde app.state
    @INPUT: request
    @OUTPUT: Instancia activa de ConversationManager o HTTP 503
    @CONTEXT: Dependencia de inyeccion para endpoints de contenido
    @SECURITY: Falla antes de cualquier operacion de contenido
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sistema no inicializado: orchestrator no disponible.",
        )
    cm = getattr(orchestrator, "conversation_manager", None)
    if cm is None:
        cm = getattr(orchestrator, "_conversation_manager", None)
    if cm is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ConversationManager no accesible desde el orquestador.",
        )
    return cm


@router.get(
    "/content/script",
    response_model=TourScript,
    summary="Consultar el guion de tour cargado actualmente",
)
async def endpoint_get_script(
    cm=Depends(_get_conversation_manager),
) -> TourScript:
    """
    @TASK: Retornar el guion de tour actualmente cargado en ConversationManager
    @INPUT: Sin parametros
    @OUTPUT: TourScript serializado en JSON
    @CONTEXT: Observabilidad del estado de contenido; sin efectos secundarios
    STEP 1: Verificar que hay un script cargado
    STEP 2: Retornar el objeto TourScript serializado por Pydantic
    @SECURITY: Solo lectura; sin mutacion de estado
    """
    # STEP 1
    script = cm.loaded_script
    if script is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay guion cargado. Usar POST /content/script/reload para cargar.",
        )
    # STEP 2
    return script


@router.post(
    "/content/script/reload",
    response_model=ScriptReloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Recargar el guion de tour desde disco de forma asíncrona",
)
async def endpoint_reload_script(
    cm=Depends(_get_conversation_manager),
) -> ScriptReloadResponse:
    """
    @TASK: Forzar recarga del guion de tour desde data/mvp_tour_script.json
    @INPUT: Sin payload
    @OUTPUT: ScriptReloadResponse con version y cantidad de waypoints cargados
    @CONTEXT: Permite actualizacion de contenido en caliente sin reiniciar el proceso
    STEP 1: Verificar existencia del archivo en la ruta default
    STEP 2: Invocar load_script_from_file() de forma asincrona en executor
    STEP 3: Retornar confirmacion con datos del script recargado
    @SECURITY: Ruta de archivo fija en el servidor; sin parametro de ruta en la API
               FileNotFoundError y ValidationError retornan HTTP 422
    """
    script_path = _SCRIPT_DEFAULT_PATH

    # STEP 1
    if not script_path.exists():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Archivo no encontrado: {script_path}. "
                   "Crear data/mvp_tour_script.json a partir de la plantilla.",
        )

    # STEP 2: load_script_from_file es sincrono (I/O de disco + Pydantic); ejecutar en executor
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, cm.load_script_from_file, script_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Error al cargar el guion: {exc}",
        )

    # STEP 3
    script = cm.loaded_script
    LOGGER.info(
        "[API] POST /content/script/reload exitoso. version='%s' waypoints=%d",
        script.version,
        len(script.waypoints),
    )
    return ScriptReloadResponse(
        reloaded=True,
        version=script.version,
        waypoints_loaded=len(script.waypoints),
        detail=f"Guion version '{script.version}' cargado con {len(script.waypoints)} waypoint(s).",
    )


__all__ = ["router", "telemetry_manager"]
