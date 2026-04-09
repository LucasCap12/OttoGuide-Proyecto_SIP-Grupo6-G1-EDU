from __future__ import annotations

# @TASK: Implementar MockRobotAdapter como simulador sin hardware
# @INPUT: Sin dependencias de unitree_sdk2py
# @OUTPUT: Adaptador mock con estado interno simulado y latencia controlada
# @CONTEXT: Usado en ROBOT_MODE=mock para desarrollo, CI y testing
# @SECURITY: Cero contacto con SDK fisico; seguro para ejecucion en cualquier entorno
# STEP 1: Estado interno _state y _position simulados
# STEP 2: move() integra posicion: x += linear_x * (duration_ms/1000)
# STEP 3: Latencia simulada: initialize 0.2s, stand 0.1s, damp 0.05s

import asyncio
import logging
import math

from .interface import MotionCommand, RobotHardwareInterface

LOGGER = logging.getLogger("otto_guide.hardware.mock_adapter")

# ---------------------------------------------------------------------------
# Latencias simuladas
# ---------------------------------------------------------------------------
_INIT_LATENCY_S: float = 0.2
_STAND_LATENCY_S: float = 0.1
_DAMP_LATENCY_S: float = 0.05


class MockRobotAdapter(RobotHardwareInterface):
    """
    @TASK: Simular adaptador de hardware para desarrollo y CI
    @INPUT: Sin dependencias de hardware fisico
    @OUTPUT: Estado interno coherente con trazas [MOCK] en cada operacion
    @CONTEXT: Default cuando ROBOT_MODE=mock (nunca real por defecto)
    @SECURITY: Sin efectos secundarios sobre hardware real
    """

    def __init__(self) -> None:
        # @TASK: Inicializar estado interno simulado
        # @INPUT: Sin parametros
        # @OUTPUT: Estado "uninitialized", posicion {x: 0, y: 0, yaw: 0}
        # @CONTEXT: Estado mutable; no thread-safe (event loop unico)
        # @SECURITY: Sin acceso a red ni SDK
        self._state: str = "IDLE"
        self._position: dict[str, float] = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        LOGGER.info("[MOCK] MockRobotAdapter creado.")

    async def initialize(self) -> None:
        """
        @TASK: Simular inicializacion de hardware
        @INPUT: Sin parametros
        @OUTPUT: _state = "initialized"
        @CONTEXT: Latencia simulada 0.2s para emular negociacion DDS
        @SECURITY: Sin IO real
        """
        LOGGER.info("[MOCK] initialize() — latencia simulada %.2fs", _INIT_LATENCY_S)
        await asyncio.sleep(_INIT_LATENCY_S)
        self._state = "initialized"
        LOGGER.info("[MOCK] initialize() completado. state='%s'", self._state)

    async def stand(self) -> None:
        """
        @TASK: Simular bipedestacion
        @INPUT: Sin parametros
        @OUTPUT: _state = "standing"
        @CONTEXT: Latencia simulada 0.1s
        @SECURITY: Sin IO real
        """
        LOGGER.info("[MOCK] stand() — latencia simulada %.2fs", _STAND_LATENCY_S)
        await asyncio.sleep(_STAND_LATENCY_S)
        self._state = "standing"
        LOGGER.info("[MOCK] stand() completado. state='%s'", self._state)

    async def damp(self) -> None:
        """
        @TASK: Simular parada amortiguada
        @INPUT: Sin parametros
        @OUTPUT: _state = "damped"
        @CONTEXT: Latencia simulada 0.05s; timeout 1.5s impuesto por el caller
        @SECURITY: Sin IO real; simula la transicion a estado seguro
        """
        LOGGER.info("[MOCK] damp() — latencia simulada %.2fs", _DAMP_LATENCY_S)
        await asyncio.sleep(_DAMP_LATENCY_S)
        self._state = "damped"
        LOGGER.info("[MOCK] damp() completado. state='%s'", self._state)

    async def move(self, command: MotionCommand) -> None:
        """
        @TASK: Simular movimiento con integracion de posicion
        @INPUT: command — MotionCommand con linear_x, angular_z, duration_ms
        @OUTPUT: _position actualizada: x += linear_x * dt, yaw += angular_z * dt
        @CONTEXT: Integracion simple de primer orden para simulacion cinematica
        STEP 1: Calcular dt = duration_ms / 1000
        STEP 2: Integrar posicion x con linear_x * dt (asume heading en eje x)
        STEP 3: Integrar yaw con angular_z * dt
        @SECURITY: Sin clamping en mock; el ABC no lo requiere en simulacion
        """
        dt = command.duration_ms / 1000.0

        # STEP 2: Integracion de posicion (movimiento en heading actual)
        self._position["x"] += command.linear_x * dt * math.cos(self._position["yaw"])
        self._position["y"] += command.linear_x * dt * math.sin(self._position["yaw"])

        # STEP 3: Integracion de yaw
        self._position["yaw"] += command.angular_z * dt

        self._state = "moving"
        LOGGER.info(
            "[MOCK] move(vx=%.3f, wz=%.3f, dt=%.3fs) → pos=(%.3f, %.3f, yaw=%.3f)",
            command.linear_x,
            command.angular_z,
            dt,
            self._position["x"],
            self._position["y"],
            self._position["yaw"],
        )

    async def get_state(self) -> dict:
        """
        @TASK: Retornar estado simulado del adaptador
        @INPUT: Sin parametros
        @OUTPUT: dict con adapter, state y position
        @CONTEXT: Observabilidad para endpoints REST en modo mock
        @SECURITY: Solo lectura
        """
        LOGGER.debug("[MOCK] get_state() invocado.")
        return {
            "adapter": "MockRobotAdapter",
            "state": self._state,
            "position": dict(self._position),
        }

    async def emergency_stop(self) -> None:
        """
        @TASK: Simular parada de emergencia
        @INPUT: Sin parametros
        @OUTPUT: damp() ejecutado; state = "damped"
        @CONTEXT: Invoca damp() internamente como primera accion
        @SECURITY: damp() es la unica accion; sin comandos adicionales
        """
        LOGGER.critical("[MOCK] EMERGENCY_STOP invocado — ejecutando damp().")
        await self.damp()


__all__ = ["MockRobotAdapter"]
