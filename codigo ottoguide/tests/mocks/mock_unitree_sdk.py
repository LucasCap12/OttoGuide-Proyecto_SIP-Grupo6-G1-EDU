from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class CommandRecord:
    command: str
    payload: Dict[str, float]


@dataclass(slots=True)
class MockHighLevelClient:
    default_latency_s: float = 0.002
    history: List[CommandRecord] = field(default_factory=list)

    def Move(self, vx: float, vy: float, wz: float) -> Dict[str, Any]:
        # @TASK: Simular comando Move
        # @INPUT: vx, vy, wz
        # @OUTPUT: Diccionario de confirmacion de movimiento
        # @CONTEXT: Mock compatible con interfaz de unitree_sdk2_python
        # STEP 1: Simular latencia minima con asyncio.sleep puenteado
        # STEP 2: Registrar y retornar el comando ejecutado
        # @SECURITY: No accede a hardware real durante pruebas SITL
        # @AI_CONTEXT: Permite validar no bloqueo en wrapper RobotHardwareAPI
        _run_async_sleep(self.default_latency_s)
        record = CommandRecord(command="Move", payload={"vx": vx, "vy": vy, "wz": wz})
        self.history.append(record)
        return {
            "ok": True,
            "command": record.command,
            "payload": record.payload,
        }

    def Euler(self, roll: float, pitch: float, yaw: float) -> Dict[str, Any]:
        # @TASK: Simular comando Euler
        # @INPUT: roll, pitch, yaw
        # @OUTPUT: Diccionario de confirmacion de orientacion
        # @CONTEXT: Mock de ajuste cinemático de alto nivel
        # STEP 1: Simular tiempo de respuesta minimo
        # STEP 2: Registrar y retornar el comando ejecutado
        # @SECURITY: Aisla pruebas de actuadores fisicos
        # @AI_CONTEXT: Usado para tests de transicion y control de estado
        _run_async_sleep(self.default_latency_s)
        record = CommandRecord(
            command="Euler",
            payload={"roll": roll, "pitch": pitch, "yaw": yaw},
        )
        self.history.append(record)
        return {
            "ok": True,
            "command": record.command,
            "payload": record.payload,
        }

    def Damp(self) -> Dict[str, Any]:
        # @TASK: Simular comando Damp
        # @INPUT: Sin parametros
        # @OUTPUT: Diccionario de confirmacion de postura segura
        # @CONTEXT: Mock de parada amortiguada para rutas de emergencia
        # STEP 1: Simular latencia minima de seguridad
        # STEP 2: Registrar ejecucion del comando Damp
        # @SECURITY: Garantiza cobertura de escenarios de fallo en pruebas
        # @AI_CONTEXT: Verifica comportamiento failsafe del orquestador
        _run_async_sleep(self.default_latency_s)
        record = CommandRecord(command="Damp", payload={})
        self.history.append(record)
        return {
            "ok": True,
            "command": record.command,
            "payload": record.payload,
        }


def create_high_level_client() -> MockHighLevelClient:
    # @TASK: Exponer factory mock
    # @INPUT: Sin parametros
    # @OUTPUT: Instancia de MockHighLevelClient
    # @CONTEXT: Reemplazo de unitree_sdk2_python.create_high_level_client
    # STEP 1: Crear cliente mock con latencia minima
    # STEP 2: Retornar instancia para inyeccion en tests
    # @SECURITY: Evita dependencia de libreria nativa en CI
    # @AI_CONTEXT: Compatible con resolver dinamico del RobotHardwareAPI
    return MockHighLevelClient()


def _run_async_sleep(delay_s: float) -> None:
    # @TASK: Ejecutar sleep asincrono
    # @INPUT: delay_s
    # @OUTPUT: Pausa minima completada
    # @CONTEXT: Simulacion temporal sin bloquear event loop principal de tests
    # STEP 1: Abrir loop aislado en el hilo actual
    # STEP 2: Esperar coroutine asyncio.sleep
    # @SECURITY: No reutiliza loops externos para evitar interferencias
    # @AI_CONTEXT: Preserva requerimiento de uso explicito de asyncio.sleep
    asyncio.run(asyncio.sleep(delay_s))


__all__ = [
    "CommandRecord",
    "MockHighLevelClient",
    "create_high_level_client",
]