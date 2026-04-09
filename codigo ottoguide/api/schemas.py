from __future__ import annotations

# @TASK: Definir schemas Pydantic para la API REST de OttoGuide
# @INPUT: Sin dependencias de hardware ni SDK
# @OUTPUT: Modelos de request/response serializables en JSON
# @CONTEXT: Contratos de API para endpoints de control, observabilidad y contenido de tour
# @SECURITY: extra="forbid" en todos los modelos de entrada de control
# STEP 1: Contratos de hardware/control (sin cambios)
# STEP 2: Contratos de contenido — ZoneContent y TourScript (nuevos)
# STEP 3: Contratos de recarga de script (nuevos)

from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field


class NavWaypointDTO(BaseModel):
    """Waypoint serializable en JSON para POST /tour/start."""
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    yaw_rad: float
    frame_id: str = "map"


class StartTourRequest(BaseModel):
    """Payload para POST /tour/start."""
    model_config = ConfigDict(extra="forbid")

    waypoints: list[NavWaypointDTO] = Field(min_length=1)
    tour_id: str = Field(default="tour-001", min_length=1)


class StartTourResponse(BaseModel):
    """Respuesta HTTP 202 para inicio de tour."""
    accepted: bool
    detail: str
    tour_id: str


class PauseTourRequest(BaseModel):
    """Payload para POST /tour/pause."""
    model_config = ConfigDict(extra="forbid")

    audio_b64: Optional[str] = None
    language: str = "es"


class EmergencyRequest(BaseModel):
    """Payload para POST /emergency."""
    model_config = ConfigDict(extra="forbid")

    reason: str = "emergency-stop-api"


class StatusResponse(BaseModel):
    """Snapshot consolidado del estado del sistema."""
    state: str
    tour_id: Optional[str] = None
    current_waypoint_index: int = 0
    last_error: Optional[str] = None


class QuestionRequest(BaseModel):
    """Payload para POST /question."""
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    language: str = "es"


class QuestionResponse(BaseModel):
    """Respuesta a pregunta de usuario."""
    answer: str
    source_pipeline: str



# ---------------------------------------------------------------------------
# Contratos de Contenido de Tour (TAREA 1)
# ---------------------------------------------------------------------------

class WaypointContent(BaseModel):
    """
    @TASK: Definir contenido de una zona del tour universitario
    @INPUT: JSON editado por el equipo de contenido
    @OUTPUT: Modelo validado consumido por ConversationManager
    @CONTEXT: Unidad atomica de contenido con prompt de sistema para Ollama
    @SECURITY: extra=ignore permite al equipo de contenido agregar metadatos
                sin romper la validacion del schema
    """
    model_config = ConfigDict(extra="ignore")

    waypoint_id: str = Field(
        min_length=1,
        description="Identificador logico del waypoint (I, 1, 2, 3, F).",
    )
    interaction_type: Literal["scripted", "llm_qa"] = Field(
        description="Tipo de interaccion para el waypoint.",
    )
    script_text: Optional[str] = Field(
        default=None,
        description="Texto determinista para TTS offline cuando interaction_type='scripted'.",
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="Prompt base para LLM cuando interaction_type='llm_qa'.",
    )
    pose_2d: dict[str, float] = Field(
        min_length=3,
        description="Coordenadas 2D del mapa para navegacion fisica: {x, y, theta}.",
    )


class TourScript(BaseModel):
    """
    @TASK: Definir el guion completo del tour con todas las zonas
    @INPUT: Archivo JSON editado offline por el equipo de contenido
    @OUTPUT: Modelo validado con lista ordenada de zonas
    @CONTEXT: Archivo maestro de contenido; recargable en caliente via /content/script/reload
    @SECURITY: Validacion estricta de zonas; version como campo auditable
    """
    model_config = ConfigDict(extra="ignore")

    version: str = Field(
        min_length=1,
        description="Version semantica del guion (e.g. '1.0.0'). Usada para auditorias.",
    )
    waypoints: list[WaypointContent] = Field(
        min_length=1,
        description="Lista ordenada de waypoints logicos del tour. Minimo 1.",
    )


class ScriptReloadResponse(BaseModel):
    """
    @TASK: Confirmar resultado de la recarga del guion desde disco
    @INPUT: Sin parametros
    @OUTPUT: Estado de la operacion, version cargada y zonas disponibles
    @CONTEXT: Respuesta de POST /content/script/reload
    @SECURITY: No expone rutas del sistema de archivos del servidor
    """
    reloaded: bool
    version: str
    waypoints_loaded: int
    detail: str


__all__ = [
    "EmergencyRequest",
    "NavWaypointDTO",
    "PauseTourRequest",
    "QuestionRequest",
    "QuestionResponse",
    "ScriptReloadResponse",
    "StartTourRequest",
    "StartTourResponse",
    "StatusResponse",
    "TourScript",
    "WaypointContent",
]
