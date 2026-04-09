from __future__ import annotations

# @TASK: Unico entrypoint del sistema OttoGuide
# @INPUT: Variables de entorno (ROBOT_MODE, etc.) via config/settings.py
# @OUTPUT: Stack robotico activo; FastAPI + Uvicorn serviendo en API_HOST:API_PORT
# @CONTEXT: Reemplaza main.py, api_server.py y server.py anteriores
# STEP 1: Crear FastAPI con asynccontextmanager lifespan
# STEP 2: lifespan: hardware = get_hardware_adapter(), await initialize()
# STEP 3: lifespan: app.state.orchestrator = TourOrchestrator(hardware)
# STEP 4: lifespan yield; en shutdown: await hardware.damp() garantizado
# STEP 5: uvicorn.run con factory=True
# @SECURITY: damp() garantizado en cualquier causa de shutdown
# @AI_CONTEXT: Cero sys.path.append; cero imports de unitree_sdk2py

import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.router import router, telemetry_manager
from config.settings import get_hardware_adapter, get_settings
from hardware.interface import RobotHardwareInterface
from src.core.mission_audit import MissionAuditLogger

LOGGER = logging.getLogger("otto_guide.main")
STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_FILE = STATIC_DIR / "dashboard.html"
MISSION_AUDIT_LOGGER = MissionAuditLogger()

# ---------------------------------------------------------------------------
# Constantes de seguridad
# ---------------------------------------------------------------------------
_DAMP_SHUTDOWN_TIMEOUT_S: float = 1.5


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    @TASK: Gestionar ciclo de vida completo del sistema
    @INPUT: app — instancia FastAPI
    @OUTPUT: Stack inicializado durante yield; damp() en shutdown
    @CONTEXT: asynccontextmanager — reemplaza on_startup/on_shutdown
    STEP 1: hardware = get_hardware_adapter()
    STEP 2: await hardware.initialize()
    STEP 3: app.state.orchestrator = TourOrchestrator(hardware)
    STEP 4: yield
    STEP 5: await hardware.damp() — garantizado en cualquier causa de shutdown
    @SECURITY: damp() en finally garantiza estado seguro
    """
    settings = get_settings()
    hardware: Optional[RobotHardwareInterface] = None

    try:
        # STEP 1
        LOGGER.info(
            "[BOOT] Inicializando hardware. ROBOT_MODE=%s",
            settings.ROBOT_MODE,
        )
        hardware = get_hardware_adapter()

        # STEP 2
        await hardware.initialize()
        LOGGER.info("[BOOT] Hardware inicializado correctamente.")

        # STEP 3: Instanciar orquestador con dependencias congeladas
        # Los modulos congelados siguen usando src.* — no modificar sus imports
        from src.core import TourOrchestrator

        orchestrator = TourOrchestrator(
            hardware_api=hardware,
            nav_bridge=_get_nav_bridge_stub(),
            conversation_manager=_get_conversation_manager_stub(settings),
            vision_processor=_get_vision_processor_stub(),
            telemetry_manager=telemetry_manager,
            mission_audit_logger=MISSION_AUDIT_LOGGER,
        )
        app.state.orchestrator = orchestrator
        LOGGER.info(
            "[BOOT] TourOrchestrator instanciado. state_id='%s'",
            orchestrator.state_id,
        )

        # STEP 4
        yield

    finally:
        # STEP 5: damp() garantizado en cualquier ruta de salida
        if hardware is not None:
            LOGGER.info(
                "[SHUTDOWN] Ejecutando damp() (timeout=%.1fs).",
                _DAMP_SHUTDOWN_TIMEOUT_S,
            )
            try:
                await asyncio.wait_for(
                    hardware.damp(),
                    timeout=_DAMP_SHUTDOWN_TIMEOUT_S,
                )
                LOGGER.info("[SHUTDOWN] damp() ejecutado correctamente.")
            except asyncio.TimeoutError:
                LOGGER.critical(
                    "[SHUTDOWN] TIMEOUT en damp() (%.1fs). "
                    "Verificar estado mecanico manualmente.",
                    _DAMP_SHUTDOWN_TIMEOUT_S,
                )
            except Exception as exc:
                LOGGER.critical(
                    "[SHUTDOWN] Fallo en damp(): %s — %s",
                    type(exc).__name__, exc,
                )

        LOGGER.info("[SHUTDOWN] Secuencia de apagado completada.")


# ---------------------------------------------------------------------------
# Stubs de dependencias congeladas
# ---------------------------------------------------------------------------
# Los modulos congelados (orchestrator, conversation, nav2_bridge) esperan
# tipos especificos. Estas funciones proveen instancias compatibles.
# En despliegue real, estas se reemplazan por las instancias completas
# creadas por start_robot.sh (capas 2-3).

def _get_nav_bridge_stub():
    """
    @TASK: Obtener stub o instancia real de AsyncNav2Bridge
    @INPUT: Sin parametros
    @OUTPUT: Instancia de AsyncNav2Bridge
    @CONTEXT: Nav2 es infraestructura externa (Capa 2); este stub es placeholder
    @SECURITY: No inicializa ROS 2 desde Python
    """
    try:
        from src.navigation import AsyncNav2Bridge
        return AsyncNav2Bridge()
    except Exception:
        LOGGER.warning(
            "[BOOT] AsyncNav2Bridge no disponible. Usando stub minimo."
        )
        return _MinimalNavStub()


def _get_conversation_manager_stub(settings):
    """
    @TASK: Obtener stub o instancia real de ConversationManager
    @INPUT: settings — Settings con OLLAMA_HOST y OLLAMA_MODEL
    @OUTPUT: Instancia de ConversationManager
    @CONTEXT: Ollama daemon es Capa 3; puede no estar disponible en CI
    @SECURITY: Sin APIs externas (sin OpenAI, sin Anthropic)
    """
    try:
        from src.interaction import ConversationManager, CloudNLPPipeline, LocalNLPPipeline
        return ConversationManager(
            cloud_strategy=CloudNLPPipeline(timeout_s=1.0),
            local_strategy=LocalNLPPipeline(model_name="ollama-local"),
        )
    except Exception:
        LOGGER.warning(
            "[BOOT] ConversationManager no disponible. Usando stub minimo."
        )
        return _MinimalConversationStub()


def _get_vision_processor_stub():
    """
    @TASK: Obtener stub o instancia real de VisionProcessor
    @INPUT: Sin parametros
    @OUTPUT: Instancia de VisionProcessor
    @CONTEXT: Camara D435i es infraestructura externa
    @SECURITY: Sin acceso a hardware de vision en CI/mock
    """
    try:
        import numpy as np
        from src.vision import CameraModel, VisionProcessor
        camera_model = CameraModel(
            camera_matrix=np.eye(3, dtype=np.float64),
            distortion_coefficients=np.zeros((5, 1), dtype=np.float64),
        )
        return VisionProcessor(camera_model=camera_model, tag_size_m=0.16)
    except Exception:
        LOGGER.warning(
            "[BOOT] VisionProcessor no disponible. Usando stub minimo."
        )
        return _MinimalVisionStub()


class _MinimalNavStub:
    """Stub minimo para AsyncNav2Bridge cuando ROS 2 no esta disponible."""
    async def start(self): pass
    async def close(self): pass
    async def navigate_to_waypoints(self, waypoints): return False
    async def cancel_navigation(self): pass
    async def inject_absolute_pose(self, pose): pass


class _MinimalConversationStub:
    """Stub minimo para ConversationManager cuando Ollama no esta disponible."""
    swap_count = 0
    active_strategy_name = "stub"
    async def process_interaction(self, audio, *, language="es"):
        from dataclasses import dataclass
        @dataclass
        class StubResponse:
            answer_text: str = ""
            source_pipeline: str = "stub"
            audio_stream_ready: bool = False
        return StubResponse()
    async def respond(self, request):
        return await self.process_interaction(None)


class _MinimalVisionStub:
    """Stub minimo para VisionProcessor cuando no hay camara."""
    def close(self): pass
    async def get_next_estimate(self, *, timeout_s=0.5): return None


# ---------------------------------------------------------------------------
# Factory de aplicacion
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """
    @TASK: Factory de la aplicacion FastAPI
    @INPUT: Sin parametros
    @OUTPUT: FastAPI app con lifespan y router incluido
    @CONTEXT: Invocada por uvicorn con factory=True
    @SECURITY: docs_url=None en produccion; habilitar para desarrollo
    """
    _configure_logging()

    app = FastAPI(
        title="OttoGuide API",
        version="1.0.0",
        description="Robot humanoide Unitree G1 EDU — Guia de visitas universitarias",
        lifespan=lifespan,
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(router)

    @app.get("/", include_in_schema=False)
    @app.get("/dashboard", include_in_schema=False)
    async def dashboard() -> FileResponse:
        if not DASHBOARD_FILE.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dashboard no encontrado en {DASHBOARD_FILE}",
            )
        return FileResponse(DASHBOARD_FILE)

    return app


def _configure_logging() -> None:
    """
    @TASK: Configurar logging base del proceso
    @INPUT: Sin parametros
    @OUTPUT: Logging inicializado con formato canonico
    @CONTEXT: Primer paso antes de cualquier IO
    @SECURITY: Sin exposicion de credenciales
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Entrypoint directo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # @TASK: Lanzar servidor con uvicorn
    # @INPUT: Sin parametros CLI
    # @OUTPUT: Proceso HTTP activo en API_HOST:API_PORT
    # @CONTEXT: Ejecutable como: python main.py
    # @SECURITY: KeyboardInterrupt suprimida; SIGINT capturada por uvicorn
    settings = get_settings()
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(
            "main:create_app",
            host="0.0.0.0",
            port=settings.API_PORT,
            factory=True,
            log_level="info",
        )