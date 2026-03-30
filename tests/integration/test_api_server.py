from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio

# @TASK: Priorizar src local
# @INPUT: Ruta actual del test
# @OUTPUT: Workspace root como primer path de importacion
# @CONTEXT: Evita colision con paquetes src de otros proyectos
# STEP 1: Resolver raiz del proyecto desde tests/integration
# STEP 2: Insertar ruta en sys.path al inicio
# @SECURITY: Reduce riesgo de cargar modulos externos inesperados
# @AI_CONTEXT: Requerido para ejecucion estable en entornos multi-workspace
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for loaded_module in list(sys.modules):
    if loaded_module == "src" or loaded_module.startswith("src."):
        del sys.modules[loaded_module]

from src.api import create_app
from src.core import TourOrchestrator
from src.hardware import RobotHardwareAPI
from src.interaction import CloudNLPPipeline, ConversationManager, LocalNLPPipeline
from tests.mocks.mock_unitree_sdk import MockHighLevelClient


@dataclass(slots=True)
class ApiBundle:
    app: object
    orchestrator: TourOrchestrator
    hardware_api: RobotHardwareAPI
    mock_client: MockHighLevelClient


@pytest_asyncio.fixture
async def api_bundle() -> AsyncIterator[ApiBundle]:
    # @TASK: Ensamblar API bundle
    # @INPUT: Sin parametros
    # @OUTPUT: App FastAPI y orquestador mockeado listos
    # @CONTEXT: Fixture base para pruebas de endpoints REST
    # STEP 1: Inyectar RobotHardwareAPI con MockHighLevelClient
    # STEP 2: Crear orquestador y app con estado activo en app.state
    # @SECURITY: Ejecuta pruebas sin hardware ni servicios cloud reales
    # @AI_CONTEXT: Reutilizable para validaciones de contracto HTTP
    RobotHardwareAPI._instance = None
    mock_client = MockHighLevelClient(default_latency_s=0.001)
    factory: Callable[[], MockHighLevelClient] = lambda: mock_client

    hardware_api = RobotHardwareAPI.get_instance(
        client_factory=factory,
        call_timeout_s=0.2,
        executor_workers=1,
    )
    conversation_manager = ConversationManager(
        cloud_strategy=CloudNLPPipeline(
            timeout_s=0.25,
            simulated_latency_s=0.005,
            provider_name="api-test-cloud",
        ),
        local_strategy=LocalNLPPipeline(model_name="api-test-local"),
    )
    orchestrator = TourOrchestrator(
        hardware_api=hardware_api,
        conversation_manager=conversation_manager,
    )
    await orchestrator.activate_initial_state()

    app = create_app(orchestrator)
    app.state.tour_orchestrator = orchestrator

    yield ApiBundle(
        app=app,
        orchestrator=orchestrator,
        hardware_api=hardware_api,
        mock_client=mock_client,
    )

    hardware_api.close()
    RobotHardwareAPI._instance = None


@pytest_asyncio.fixture
async def async_client(api_bundle: ApiBundle) -> AsyncIterator[httpx.AsyncClient]:
    # @TASK: Crear cliente async API
    # @INPUT: api_bundle
    # @OUTPUT: httpx.AsyncClient conectado via ASGITransport
    # @CONTEXT: Cliente de prueba para endpoints FastAPI sin servidor real
    # STEP 1: Construir transport ASGI sobre app mockeada
    # STEP 2: Abrir y cerrar cliente async en contexto controlado
    # @SECURITY: Evita exposicion de sockets de red durante pruebas
    # @AI_CONTEXT: Permite validar BackgroundTasks y status codes
    transport = httpx.ASGITransport(app=api_bundle.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_get_status_idle(async_client: httpx.AsyncClient) -> None:
    # @TASK: Validar status inicial
    # @INPUT: async_client
    # @OUTPUT: HTTP 200 con estado idle
    # @CONTEXT: Verifica endpoint GET /tour/status
    # STEP 1: Ejecutar solicitud GET al endpoint de estado
    # STEP 2: Validar codigo de respuesta y payload JSON
    # @SECURITY: Comprueba endpoint de solo lectura
    # @AI_CONTEXT: Asegura contrato inicial para panel de control
    response = await async_client.get("/tour/status")
    assert response.status_code == 200

    payload = response.json()
    assert payload["state"] == "idle"


@pytest.mark.asyncio
async def test_post_start_tour(async_client: httpx.AsyncClient) -> None:
    # @TASK: Validar inicio tour
    # @INPUT: async_client
    # @OUTPUT: HTTP 202 Accepted
    # @CONTEXT: Verifica trigger POST /tour/start
    # STEP 1: Enviar payload valido con waypoint_id
    # STEP 2: Asertar codigo de respuesta 202 estricto
    # @SECURITY: Confirma desacople request/ejecucion de fondo
    # @AI_CONTEXT: Contrato clave para disparo remoto del recorrido
    response = await async_client.post(
        "/tour/start",
        json={"waypoint_id": "wp-api-001"},
    )
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_post_start_tour_triggers_state_change(
    async_client: httpx.AsyncClient,
) -> None:
    # @TASK: Validar cambio estado
    # @INPUT: async_client
    # @OUTPUT: Estado navigating o superior tras POST /tour/start
    # @CONTEXT: Verifica ejecucion diferida de BackgroundTasks
    # STEP 1: Lanzar POST /tour/start y esperar breve ventana de ejecucion
    # STEP 2: Consultar /tour/status y validar transicion de estado
    # @SECURITY: Comprueba que la cola de tareas no bloquee el endpoint
    # @AI_CONTEXT: Cubre comportamiento async de trigger API
    response = await async_client.post(
        "/tour/start",
        json={"waypoint_id": "wp-api-002"},
    )
    assert response.status_code == 202

    await asyncio.sleep(0.05)

    status_response = await async_client.get("/tour/status")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["state"] in {"navigating", "guiding", "interacting", "error_recovery"}