from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import AsyncIterator, Callable

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

from src.core import TourOrchestrator
from src.hardware import RobotHardwareAPI
from src.interaction import CloudNLPPipeline, ConversationManager, LocalNLPPipeline
from tests.mocks.mock_unitree_sdk import MockHighLevelClient


@dataclass(slots=True)
class OrchestratorBundle:
    orchestrator: TourOrchestrator
    hardware_api: RobotHardwareAPI
    mock_client: MockHighLevelClient
    conversation_manager: ConversationManager


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

    conversation_manager = ConversationManager(
        cloud_strategy=CloudNLPPipeline(
            timeout_s=0.25,
            simulated_latency_s=0.005,
            provider_name="test-cloud",
        ),
        local_strategy=LocalNLPPipeline(model_name="test-local"),
    )

    orchestrator = TourOrchestrator(
        hardware_api=hardware_api,
        conversation_manager=conversation_manager,
    )
    await orchestrator.activate_initial_state()

    yield OrchestratorBundle(
        orchestrator=orchestrator,
        hardware_api=hardware_api,
        mock_client=mock_client,
        conversation_manager=conversation_manager,
    )

    hardware_api.close()
    RobotHardwareAPI._instance = None


@pytest.mark.asyncio
async def test_standard_tour_flow(orchestrator_bundle: OrchestratorBundle) -> None:
    # @TASK: Validar flujo estandar
    # @INPUT: orchestrator_bundle
    # @OUTPUT: Transiciones IDLE->NAVIGATING->GUIDING->INTERACTING->IDLE correctas
    # @CONTEXT: Prueba de integracion principal del TourOrchestrator
    # STEP 1: Ejecutar transiciones nominales del recorrido
    # STEP 2: Asertar estado esperado en cada etapa
    # @SECURITY: Garantiza comportamiento determinista base
    # @AI_CONTEXT: Cobertura minima del happy path del state machine
    orchestrator = orchestrator_bundle.orchestrator

    assert orchestrator.state_id == "idle"
    await orchestrator.start_tour("wp-001")
    assert orchestrator.state_id == "navigating"

    await orchestrator.mark_waypoint_reached("wp-001")
    assert orchestrator.state_id == "guiding"

    await orchestrator.start_guided_interaction()
    assert orchestrator.state_id == "interacting"

    await orchestrator.complete_tour()
    assert orchestrator.state_id == "idle"


@pytest.mark.asyncio
async def test_error_recovery_triggers_damp(orchestrator_bundle: OrchestratorBundle) -> None:
    # @TASK: Validar recovery con Damp
    # @INPUT: orchestrator_bundle
    # @OUTPUT: Registro de comando Damp en historial mock
    # @CONTEXT: Verifica rutina de seguridad al entrar ERROR_RECOVERY
    # STEP 1: Forzar evento de fallo del orquestador
    # STEP 2: Asertar estado y comando Damp ejecutado
    # @SECURITY: Prueba comportamiento failsafe critico
    # @AI_CONTEXT: Asegura acion de postura segura ante errores
    orchestrator = orchestrator_bundle.orchestrator
    mock_client = orchestrator_bundle.mock_client

    await orchestrator.trigger_error_recovery("forced-test-error")

    assert orchestrator.state_id == "error_recovery"
    assert any(record.command == "Damp" for record in mock_client.history)


@pytest.mark.asyncio
async def test_nlp_hot_swap_fallback(orchestrator_bundle: OrchestratorBundle) -> None:
    # @TASK: Validar hot-swap NLP
    # @INPUT: orchestrator_bundle
    # @OUTPUT: Fallback cloud->local sin bloqueo en INTERACTING
    # @CONTEXT: Prueba resiliencia del ConversationManager bajo timeout cloud
    # STEP 1: Reconfigurar manager con cloud que provoca TimeoutError
    # STEP 2: Ejecutar pregunta y validar respuesta local y continuidad
    # @SECURITY: Evita degradacion por latencias cloud en runtime
    # @AI_CONTEXT: Cobertura de estrategia de contingencia conversacional
    mock_client = orchestrator_bundle.mock_client

    timeout_conversation_manager = ConversationManager(
        cloud_strategy=CloudNLPPipeline(
            timeout_s=0.01,
            simulated_latency_s=0.05,
            provider_name="timeout-cloud",
        ),
        local_strategy=LocalNLPPipeline(model_name="fallback-local"),
    )
    timeout_orchestrator = TourOrchestrator(
        hardware_api=orchestrator_bundle.hardware_api,
        conversation_manager=timeout_conversation_manager,
    )
    await timeout_orchestrator.activate_initial_state()

    await timeout_orchestrator.start_tour("wp-hot-swap")
    await timeout_orchestrator.mark_waypoint_reached("wp-hot-swap")
    await timeout_orchestrator.start_guided_interaction()

    response_obj = await asyncio.wait_for(
        timeout_orchestrator.handle_user_question("¿Dónde está la salida?"),
        timeout=0.3,
    )

    assert timeout_orchestrator.state_id == "interacting"
    assert response_obj.source_pipeline == "local"
    assert timeout_conversation_manager.active_strategy_name == "LocalNLPPipeline"
