from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import AsyncIterator, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# @TASK: Priorizar src local
# @INPUT: Ruta del archivo de prueba actual
# @OUTPUT: Root del workspace en sys.path[0]
# @CONTEXT: Evita colision con paquetes src de otros proyectos en entorno global
# STEP 1: Resolver raiz del proyecto desde tests/integration
# STEP 2: Insertar ruta al inicio de sys.path si aun no existe
# @SECURITY: Limita imports al workspace actual durante la suite
# @AI_CONTEXT: Corrige conflictos de importacion en runners compartidos
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for loaded_module in list(sys.modules):
    if loaded_module == "src" or loaded_module.startswith("src."):
        del sys.modules[loaded_module]

from tests.mocks.mock_nav2_bridge import MockNav2Bridge
from tests.mocks.mock_ros2 import install_mocks
from tests.mocks.mock_vision_processor import MockVisionProcessor

install_mocks(sys.modules)

from src.core import TourOrchestrator
from src.core import TourPlan
from src.hardware import RobotHardwareAPI
from src.interaction import ConversationManager, ConversationResponse
from src.navigation import NavWaypoint
from tests.mocks.mock_unitree_sdk import MockHighLevelClient


@dataclass(slots=True)
class OrchestratorBundle:
    orchestrator: TourOrchestrator
    hardware_api: RobotHardwareAPI
    mock_client: MockHighLevelClient
    conversation_manager: ConversationManager
    nav_bridge: MockNav2Bridge
    vision_processor: MockVisionProcessor


@pytest_asyncio.fixture
async def orchestrator_bundle() -> AsyncIterator[OrchestratorBundle]:
    # @TASK: Ensamblar orquestador test
    # @INPUT: Sin parametros
    # @OUTPUT: Bundle con orquestador y dependencias mockeadas
    # @CONTEXT: Fixture async base para pruebas de integracion SITL
    # STEP 1: Inyectar RobotHardwareAPI con MockHighLevelClient explicito
    # STEP 2: Construir ConversationManager y TourOrchestrator para pruebas
    # @SECURITY: Aisla pruebas de hardware y servicios externos
    # @AI_CONTEXT: Resetea singleton para evitar fugas entre casos
    mock_client = MockHighLevelClient(default_latency_s=0.001)
    factory: Callable[[], MockHighLevelClient] = lambda: mock_client

    RobotHardwareAPI._instance = None
    hardware_api = RobotHardwareAPI.get_instance(
        client_factory=factory,
        call_timeout_s=0.2,
        executor_workers=1,
    )

    local_strategy = MagicMock()
    local_strategy.generate = AsyncMock(
        return_value=ConversationResponse(
            answer_text="respuesta local",
            source_pipeline="local",
            audio_stream_ready=False,
        )
    )
    local_strategy.close = MagicMock()

    cloud_strategy = MagicMock()
    cloud_strategy.generate = AsyncMock(
        return_value=ConversationResponse(
            answer_text="respuesta cloud",
            source_pipeline="cloud",
            audio_stream_ready=False,
        )
    )
    cloud_strategy.close = MagicMock()

    conversation_manager = ConversationManager(
        local_strategy=local_strategy,
        cloud_strategy=cloud_strategy,
    )

    nav_bridge = MockNav2Bridge(navigation_delay_s=0.1)
    vision_processor = MockVisionProcessor()

    orchestrator = TourOrchestrator(
        hardware_api=hardware_api,
        nav_bridge=nav_bridge,
        conversation_manager=conversation_manager,
        vision_processor=vision_processor,
    )
    await orchestrator.activate_initial_state()

    yield OrchestratorBundle(
        orchestrator=orchestrator,
        hardware_api=hardware_api,
        mock_client=mock_client,
        conversation_manager=conversation_manager,
        nav_bridge=nav_bridge,
        vision_processor=vision_processor,
    )

    hardware_api.close()
    RobotHardwareAPI._instance = None


@pytest.mark.asyncio
async def test_dispatch_tour_enters_navigating(orchestrator_bundle: OrchestratorBundle) -> None:
    # @TASK: Validar despacho nominal
    # @INPUT: orchestrator_bundle
    # @OUTPUT: dispatch_tour completa y el estado pasa a navigating
    # @CONTEXT: Cobertura del contrato publico actual del orquestador
    # STEP 1: Despachar un plan con un waypoint
    # STEP 2: Validar persistencia de contexto y llamada al bridge
    # @SECURITY: No usa hardware real ni ROS2 real
    # @AI_CONTEXT: Verifica la ruta activa usada por FastAPI
    orchestrator = orchestrator_bundle.orchestrator
    plan = TourPlan(
        waypoints=[NavWaypoint(x=0.0, y=0.0, yaw_rad=0.0)],
        tour_id="tour-001",
    )

    await orchestrator.dispatch_tour(plan)
    await asyncio.sleep(0.05)

    assert orchestrator.state_id == "navigating"
    assert orchestrator.context.tour_id == "tour-001"
    assert orchestrator_bundle.nav_bridge.navigation_calls


@pytest.mark.asyncio
async def test_emergency_stop_triggers_damp(orchestrator_bundle: OrchestratorBundle) -> None:
    # @TASK: Validar emergencia con Damp
    # @INPUT: orchestrator_bundle
    # @OUTPUT: Registro de comando Damp en historial mock
    # @CONTEXT: Verifica rutina de seguridad en estado final EMERGENCY
    # STEP 1: Forzar emergencia
    # STEP 2: Asertar estado final y Damp ejecutado
    # @SECURITY: Ruta failsafe critica
    # @AI_CONTEXT: Cubre la transicion de maxima prioridad
    orchestrator = orchestrator_bundle.orchestrator
    mock_client = orchestrator_bundle.mock_client

    await orchestrator.emergency_stop("forced-test-error")

    assert orchestrator.state_id == "emergency"
    assert any(record.command == "Damp" for record in mock_client.history)


@pytest.mark.asyncio
async def test_handle_user_question_returns_response(orchestrator_bundle: OrchestratorBundle) -> None:
    # @TASK: Validar question path
    # @INPUT: orchestrator_bundle
    # @OUTPUT: Response de ConversationManager y contexto actualizado
    # @CONTEXT: Cobertura de compatibilidad para la API de texto directa
    # STEP 1: Enviar pregunta de texto
    # STEP 2: Asertar que se registra la ultima interaccion
    # @SECURITY: Sin dependencia de voz ni hardware
    # @AI_CONTEXT: Conserva contrato usado por endpoints de soporte
    orchestrator = orchestrator_bundle.orchestrator

    response_obj = await asyncio.wait_for(
        orchestrator.handle_user_question("¿Dónde está la salida?"),
        timeout=0.5,
    )

    assert response_obj.answer_text
    assert orchestrator.context.last_interaction is response_obj
