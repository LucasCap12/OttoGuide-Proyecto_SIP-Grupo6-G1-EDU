from __future__ import annotations

# @TASK: Tests unitarios del MockRobotAdapter
# @INPUT: MockRobotAdapter y MotionCommand desde hardware/
# @OUTPUT: Verificacion de ABC, integracion de posicion, emergency_stop y no-SDK
# @CONTEXT: Ejecutable sin hardware fisico ni unitree_sdk2py
# @SECURITY: Ninguna dependencia de SDK real

import asyncio
import math
import sys

import pytest

from hardware.interface import MotionCommand, RobotHardwareInterface
from hardware.mock_adapter import MockRobotAdapter


class TestMockAdapterABC:
    """Verificar que MockRobotAdapter implementa todos los metodos del ABC."""

    def test_is_subclass_of_abc(self) -> None:
        """STEP 1: MockRobotAdapter hereda de RobotHardwareInterface."""
        assert issubclass(MockRobotAdapter, RobotHardwareInterface)

    def test_is_instance_of_abc(self) -> None:
        """STEP 2: Instancia mock satisface isinstance check."""
        adapter = MockRobotAdapter()
        assert isinstance(adapter, RobotHardwareInterface)

    def test_all_abstract_methods_implemented(self) -> None:
        """STEP 3: Todos los metodos abstractos estan implementados."""
        required_methods = [
            "initialize",
            "stand",
            "damp",
            "move",
            "get_state",
            "emergency_stop",
        ]
        adapter = MockRobotAdapter()
        for method_name in required_methods:
            assert hasattr(adapter, method_name), f"Falta metodo: {method_name}"
            assert callable(getattr(adapter, method_name)), f"No callable: {method_name}"


class TestMockAdapterInitialize:
    """Verificar ciclo de vida basico."""

    @pytest.mark.asyncio
    async def test_initial_state_is_idle(self) -> None:
        """STEP 1: Estado inicial es IDLE."""
        adapter = MockRobotAdapter()
        state = await adapter.get_state()
        assert state["state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_initialize_sets_state(self) -> None:
        adapter = MockRobotAdapter()
        await adapter.initialize()
        state = await adapter.get_state()
        assert state["state"] == "initialized"

    @pytest.mark.asyncio
    async def test_stand_sets_state(self) -> None:
        adapter = MockRobotAdapter()
        await adapter.stand()
        state = await adapter.get_state()
        assert state["state"] == "standing"

    @pytest.mark.asyncio
    async def test_damp_sets_state(self) -> None:
        adapter = MockRobotAdapter()
        await adapter.damp()
        state = await adapter.get_state()
        assert state["state"] == "damped"


class TestMockAdapterMove:
    """Verificar integracion de posicion en move()."""

    @pytest.mark.asyncio
    async def test_move_forward_updates_x(self) -> None:
        """
        @TASK: Verificar que move() integra posicion en x
        @INPUT: linear_x=0.3, angular_z=0, duration_ms=1000
        @OUTPUT: x ≈ 0.3 (yaw inicial = 0 → cos(0) = 1)
        """
        adapter = MockRobotAdapter()
        cmd = MotionCommand(linear_x=0.3, angular_z=0.0, duration_ms=1000)
        await adapter.move(cmd)
        state = await adapter.get_state()
        assert abs(state["position"]["x"] - 0.3) < 1e-6
        assert abs(state["position"]["y"]) < 1e-6

    @pytest.mark.asyncio
    async def test_move_rotation_updates_yaw(self) -> None:
        """
        @TASK: Verificar que move() integra yaw
        @INPUT: linear_x=0, angular_z=0.5, duration_ms=2000
        @OUTPUT: yaw ≈ 1.0 rad
        """
        adapter = MockRobotAdapter()
        cmd = MotionCommand(linear_x=0.0, angular_z=0.5, duration_ms=2000)
        await adapter.move(cmd)
        state = await adapter.get_state()
        assert abs(state["position"]["yaw"] - 1.0) < 1e-6

    @pytest.mark.asyncio
    async def test_move_combined_updates_position(self) -> None:
        """
        @TASK: Verificar integracion combinada linear + angular
        @INPUT: linear_x=0.2, angular_z=0.1, duration_ms=1000
        @OUTPUT: posicion actualizada con heading
        """
        adapter = MockRobotAdapter()
        cmd = MotionCommand(linear_x=0.2, angular_z=0.1, duration_ms=1000)
        await adapter.move(cmd)
        state = await adapter.get_state()
        assert abs(state["position"]["yaw"] - 0.1) < 1e-6
        assert abs(state["position"]["x"] - 0.2) < 1e-6

    @pytest.mark.asyncio
    async def test_move_zero_duration(self) -> None:
        """
        @TASK: Verificar que duration_ms=0 no cambia posicion
        """
        adapter = MockRobotAdapter()
        cmd = MotionCommand(linear_x=1.0, angular_z=1.0, duration_ms=0)
        await adapter.move(cmd)
        state = await adapter.get_state()
        assert state["position"]["x"] == 0.0
        assert state["position"]["y"] == 0.0
        assert state["position"]["yaw"] == 0.0


class TestMockAdapterEmergencyStop:
    """Verificar que emergency_stop() llama a damp()."""

    @pytest.mark.asyncio
    async def test_emergency_stop_calls_damp(self) -> None:
        """
        @TASK: Verificar que emergency_stop() transiciona a estado "damped"
        @INPUT: Sin parametros
        @OUTPUT: state == "damped" tras emergency_stop()
        """
        adapter = MockRobotAdapter()
        await adapter.initialize()
        await adapter.emergency_stop()
        state = await adapter.get_state()
        assert state["state"] == "damped"

    @pytest.mark.asyncio
    async def test_emergency_stop_from_any_state(self) -> None:
        """
        @TASK: Verificar emergency_stop desde estado moving
        """
        adapter = MockRobotAdapter()
        cmd = MotionCommand(linear_x=0.1, angular_z=0.0, duration_ms=100)
        await adapter.move(cmd)
        state_before = await adapter.get_state()
        assert state_before["state"] == "moving"
        await adapter.emergency_stop()
        state_after = await adapter.get_state()
        assert state_after["state"] == "damped"


class TestMockAdapterGetState:
    """Verificar estructura de get_state()."""

    @pytest.mark.asyncio
    async def test_get_state_structure(self) -> None:
        adapter = MockRobotAdapter()
        state = await adapter.get_state()
        assert "adapter" in state
        assert state["adapter"] == "MockRobotAdapter"
        assert "state" in state
        assert "position" in state
        assert "x" in state["position"]
        assert "y" in state["position"]
        assert "yaw" in state["position"]


class TestMockAdapterNoSdkImport:
    """Verificar que MockRobotAdapter no importa unitree_sdk2py."""

    def test_mock_module_does_not_import_sdk(self) -> None:
        """
        @TASK: Verificar que el modulo mock_adapter no contiene imports de unitree_sdk2py
        @INPUT: Importar hardware.mock_adapter
        @OUTPUT: No ImportError ni dependencia de SDK
        @CONTEXT: Garantiza que CI funciona sin el SDK instalado
        @SECURITY: Previene dependencia accidental de hardware
        """
        # STEP 1: Limpiar imports previos del SDK
        sdk_modules = [k for k in sys.modules if k.startswith("unitree_sdk2py")]
        for mod in sdk_modules:
            del sys.modules[mod]

        # STEP 2: Re-importar mock_adapter
        import importlib
        importlib.reload(__import__("hardware.mock_adapter"))

        # STEP 3: Verificar que unitree_sdk2py no fue cargado
        sdk_loaded = any(
            k.startswith("unitree_sdk2py") for k in sys.modules
        )
        assert not sdk_loaded, (
            "unitree_sdk2py fue importado por mock_adapter. "
            "El mock adapter NO debe depender del SDK."
        )
