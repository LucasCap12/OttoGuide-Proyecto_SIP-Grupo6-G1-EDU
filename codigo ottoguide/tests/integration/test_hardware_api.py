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
# @CONTEXT: Evita colision de paquetes src en entorno compartido
# STEP 1: Resolver raiz del proyecto desde tests/integration
# STEP 2: Insertar ruta al inicio de sys.path
# @SECURITY: Restringe imports al proyecto actual
# @AI_CONTEXT: Mantiene aislamiento entre workspaces de VS Code
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for loaded_module in list(sys.modules):
    if loaded_module == "src" or loaded_module.startswith("src."):
        del sys.modules[loaded_module]

from src.hardware import RobotHardwareAPI, RobotHardwareAPIError
from tests.mocks.mock_unitree_sdk import MockHighLevelClient


@dataclass(slots=True)
class HardwareBundle:
    hardware_api: RobotHardwareAPI
    mock_client: MockHighLevelClient


class FailingMoveClient(MockHighLevelClient):
    def Move(self, vx: float, vy: float, wz: float):
        # @TASK: Forzar falla en Move
        # @INPUT: vx, vy, wz
        # @OUTPUT: Excepcion RuntimeError
        # @CONTEXT: Simulacion de falla para validar recovery de RobotHardwareAPI
        # STEP 1: Consumir parametros de entrada
        # STEP 2: Elevar error controlado
        # @SECURITY: No ejecuta acciones de hardware real
        # @AI_CONTEXT: Debe disparar llamada automatica a Damp en wrapper
        del vx
        del vy
        del wz
        raise RuntimeError("forced-move-failure")


@pytest_asyncio.fixture
async def hardware_bundle() -> AsyncIterator[HardwareBundle]:
    # @TASK: Ensamblar hardware bundle
    # @INPUT: Sin parametros
    # @OUTPUT: Instancia singleton y mock client asociado
    # @CONTEXT: Fixture base para validar wrapper RobotHardwareAPI
    # STEP 1: Limpiar singleton global previo
    # STEP 2: Crear instancia con mock de latencia controlada
    # @SECURITY: Evita side effects entre tests consecutivos
    # @AI_CONTEXT: Reutilizable para pruebas de concurrencia y recovery
    RobotHardwareAPI._instance = None
    mock_client = MockHighLevelClient(default_latency_s=0.05)
    factory: Callable[[], MockHighLevelClient] = lambda: mock_client

    hardware_api = RobotHardwareAPI.get_instance(
        client_factory=factory,
        call_timeout_s=0.3,
        executor_workers=1,
    )

    yield HardwareBundle(hardware_api=hardware_api, mock_client=mock_client)

    hardware_api.close()
    RobotHardwareAPI._instance = None


@pytest.mark.asyncio
async def test_hardware_singleton_instance(hardware_bundle: HardwareBundle) -> None:
    # @TASK: Validar singleton hardware
    # @INPUT: hardware_bundle
    # @OUTPUT: Misma instancia entre multiples get_instance
    # @CONTEXT: Prueba contrato singleton de RobotHardwareAPI
    # STEP 1: Recuperar instancia inicial del fixture
    # STEP 2: Solicitar nueva instancia y comparar identidad
    # @SECURITY: Garantiza punto unico de control de locomocion
    # @AI_CONTEXT: Evita condiciones de carrera por multiples wrappers
    first = hardware_bundle.hardware_api
    second = RobotHardwareAPI.get_instance()
    assert first is second


@pytest.mark.asyncio
async def test_hardware_non_blocking_executor_delegation(
    hardware_bundle: HardwareBundle,
) -> None:
    # @TASK: Validar no bloqueo loop
    # @INPUT: hardware_bundle
    # @OUTPUT: Move y Damp ejecutados sin bloquear event loop
    # @CONTEXT: Prueba de concurrencia sobre wrapper con ThreadPoolExecutor
    # STEP 1: Lanzar ticker asíncrono concurrente
    # STEP 2: Ejecutar move+damp y verificar avance del ticker
    # @SECURITY: Confirma aislamiento de llamadas bloqueantes del hilo principal
    # @AI_CONTEXT: Cobertura critica para telemetria de equilibrio en runtime
    hardware_api = hardware_bundle.hardware_api
    ticks = 0
    stop_event = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop_event.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    ticker_task = asyncio.create_task(ticker())
    try:
        await asyncio.gather(
            hardware_api.move(0.15, 0.0, 0.05),
            hardware_api.damp(),
        )
    finally:
        stop_event.set()
        await ticker_task

    assert ticks > 2


@pytest.mark.asyncio
async def test_hardware_move_failure_triggers_damp_recovery() -> None:
    # @TASK: Validar recovery operativo
    # @INPUT: Sin parametros
    # @OUTPUT: Error en Move y registro de Damp automatico
    # @CONTEXT: Cobertura de seguridad interna en RobotHardwareAPI
    # STEP 1: Inyectar cliente que falla en Move
    # STEP 2: Asertar excepcion de dominio y ejecucion de Damp
    # @SECURITY: Verifica ruta failsafe de postura segura
    # @AI_CONTEXT: Protege al robot frente a fallos de locomocion
    RobotHardwareAPI._instance = None
    failing_client = FailingMoveClient(default_latency_s=0.001)
    factory: Callable[[], FailingMoveClient] = lambda: failing_client

    hardware_api = RobotHardwareAPI.get_instance(
        client_factory=factory,
        call_timeout_s=0.2,
        executor_workers=1,
    )

    try:
        with pytest.raises(RobotHardwareAPIError):
            await hardware_api.move(0.1, 0.0, 0.0)
        assert any(record.command == "Damp" for record in failing_client.history)
    finally:
        hardware_api.close()
        RobotHardwareAPI._instance = None