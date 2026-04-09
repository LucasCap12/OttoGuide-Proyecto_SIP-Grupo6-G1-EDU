from __future__ import annotations

# @TASK: Implementar UnitreeG1Adapter como adaptador real de hardware
# @INPUT: Interfaz RobotHardwareInterface; SDK unitree_sdk2py importado localmente
# @OUTPUT: Adaptador funcional para control DDS del Unitree G1 EDU 8
# @CONTEXT: Unico archivo que importa unitree_sdk2py en todo el proyecto
# @SECURITY: Clamping cinematico obligatorio; damp() con timeout 1.5s
# STEP 1: Import lazy de unitree_sdk2py solo dentro de metodos
# STEP 2: ChannelFactoryInitialize(id=0, networkInterface=None) — firma auditada en
#         libs/unitree_sdk2_python-master/unitree_sdk2py/core/channel.py:298
#         Sintaxis correcta: ChannelFactoryInitialize(domain_id, network_interface_str)
# STEP 3: Clamping linear_x [-0.3, 0.3], angular_z [-0.5, 0.5]
# STEP 4: damp() con asyncio.wait_for timeout=1.5s
# AUDIT RESULT — ChannelFactoryInitialize: contrato VALIDO (firm signature confirmada)
# AUDIT RESULT — Move(vx, vy, vyaw, continous_move=False): contrato VALIDO
# AUDIT RESULT — stand() CORREGIDO: SetFsmId(1)=Damp; Start()=SetFsmId(200) para bipedestacion
# AUDIT RESULT — Damp(): contrato VALIDO — LocoClient.Damp() llama SetFsmId(1)
# AUDIT RESULT — IDL: unitree_hg confirmado presente en libs/unitree_sdk2py/idl/unitree_hg/

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Optional

from .interface import MotionCommand, RobotHardwareInterface

LOGGER = logging.getLogger("otto_guide.hardware.real_adapter")

# ---------------------------------------------------------------------------
# Constantes de seguridad cinemática
# ---------------------------------------------------------------------------
_MAX_LINEAR_X: float = 0.3
_MAX_ANGULAR_Z: float = 0.5
_DAMP_TIMEOUT_S: float = 1.5
_SDK_CALL_TIMEOUT_S: float = 0.75


class UnitreeG1Adapter(RobotHardwareInterface):
    """
    @TASK: Adaptador real para Unitree G1 EDU 8 via unitree_sdk2py
    @INPUT: Red DDS configurada via CYCLONEDDS_URI (variable de entorno del OS)
    @OUTPUT: Control de locomocion de alto nivel con clamping de seguridad
    @CONTEXT: Solo instanciar cuando ROBOT_MODE=real
    @SECURITY: Toda llamada al SDK se aisla en ThreadPoolExecutor
    """

    def __init__(self) -> None:
        # @TASK: Inicializar estado interno sin contactar el SDK
        # @INPUT: Sin parametros
        # @OUTPUT: Estado _initialized=False; SDK no cargado
        # @CONTEXT: Constructor ligero; la inicializacion real ocurre en initialize()
        # @SECURITY: No se toca el SDK hasta que initialize() sea invocado
        self._sdk_client: Optional[Any] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._initialized: bool = False
        self._network_interface: str = os.environ.get("ROBOT_NETWORK_INTERFACE", "")
        LOGGER.info(
            "[REAL] UnitreeG1Adapter creado. network_interface='%s'",
            self._network_interface,
        )

    async def initialize(self) -> None:
        """
        @TASK: Inicializar SDK Unitree y negociar DDS en executor
        @INPUT: ROBOT_NETWORK_INTERFACE desde os.environ
        @OUTPUT: _sdk_client listo; _initialized=True
        @CONTEXT: ChannelFactory.Instance().Init() es bloqueante
        STEP 1: Importar unitree_sdk2py localmente
        STEP 2: Ejecutar init DDS en executor
        STEP 3: Crear LocoClient e invocar Init()
        @SECURITY: Sin IPs hardcodeadas; DDS resuelve via CYCLONEDDS_URI
        """
        if self._initialized:
            LOGGER.warning("[REAL] initialize() invocado en adaptador ya inicializado.")
            return

        # STEP 1: Import lazy del SDK — unico punto de importacion en el proyecto
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient
        except ImportError as exc:
            raise RuntimeError(
                f"No se puede importar unitree_sdk2py: {exc}. "
                "Instalar con: pip install 'otto-guide[hardware]'"
            ) from exc

        # STEP 2: Crear executor dedicado y negociar DDS
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="unitree-sdk2",
        )

        loop = asyncio.get_running_loop()
        LOGGER.info("[REAL] Negociando DDS via ChannelFactoryInitialize(0)...")

        try:
            await loop.run_in_executor(
                self._executor,
                partial(ChannelFactoryInitialize, 0),
            )
        except Exception as exc:
            raise RuntimeError(
                f"ChannelFactoryInitialize(0) fallo: {exc}"
            ) from exc

        # STEP 3: Instanciar LocoClient
        client = LocoClient()

        try:
            await loop.run_in_executor(
                self._executor,
                client.Init,
            )
        except Exception as exc:
            raise RuntimeError(
                f"LocoClient.Init() fallo: {exc}"
            ) from exc

        self._sdk_client = client
        self._initialized = True
        LOGGER.info("[REAL] SDK inicializado correctamente. LocoClient activo.")

    async def stand(self) -> None:
        """
        @TASK: Comandar bipedestacion via Start() (SetFsmId=200)
        @INPUT: Sin parametros
        @OUTPUT: Robot de pie en posicion neutra
        @CONTEXT: Auditado contra g1_loco_client.py — Start() = SetFsmId(200)
                  CORRECCION: SetFsmId(1) = Damp (estado seguro), NO bipedestacion
                  SetFsmId(200) = Start = bipedestacion operativa
        @SECURITY: Verificar que initialize() fue invocado
        """
        self._assert_initialized()
        await self._invoke_sdk("Start")
        LOGGER.info("[REAL] Stand ejecutado via Start() (SetFsmId=200).")

    async def damp(self) -> None:
        """
        @TASK: Ejecutar parada amortiguada con timeout hard 1.5s
        @INPUT: Sin parametros
        @OUTPUT: Actuadores desacoplados
        @CONTEXT: Timeout impuesto localmente; no delegado al caller
        @SECURITY: Funcion critica — timeout 1.5s es hard limit
        """
        self._assert_initialized()
        try:
            await asyncio.wait_for(
                self._invoke_sdk("Damp"),
                timeout=_DAMP_TIMEOUT_S,
            )
            LOGGER.info("[REAL] Damp() ejecutado correctamente (timeout=%.1fs).", _DAMP_TIMEOUT_S)
        except asyncio.TimeoutError:
            LOGGER.critical(
                "[REAL] TIMEOUT en Damp() (%.1fs). Verificar estado mecanico manualmente.",
                _DAMP_TIMEOUT_S,
            )
            raise

    async def move(self, command: MotionCommand) -> None:
        """
        @TASK: Ejecutar comando de movimiento con clamping cinematico
        @INPUT: command — MotionCommand con linear_x, angular_z, duration_ms
        @OUTPUT: Robot en movimiento durante duration_ms
        @CONTEXT: Clamping obligatorio antes de despacho al SDK
        STEP 1: Aplicar clamping a linear_x y angular_z
        STEP 2: Invocar Move del SDK
        STEP 3: Esperar duration_ms
        @SECURITY: linear_x clamped [-0.3, 0.3]; angular_z clamped [-0.5, 0.5]
        """
        self._assert_initialized()

        # STEP 1: Clamping cinematico
        clamped_vx = max(-_MAX_LINEAR_X, min(_MAX_LINEAR_X, command.linear_x))
        clamped_wz = max(-_MAX_ANGULAR_Z, min(_MAX_ANGULAR_Z, command.angular_z))

        # STEP 2: Despachar al SDK
        try:
            await self._invoke_sdk("Move", clamped_vx, 0.0, clamped_wz)
        except Exception as exc:
            LOGGER.error("[REAL] Fallo en Move; ejecutando Damp() de emergencia.")
            await self.damp()
            raise RuntimeError(f"Fallo en Move; Damp() ejecutado: {exc}") from exc

        # STEP 3: Esperar duracion del comando
        if command.duration_ms > 0:
            await asyncio.sleep(command.duration_ms / 1000.0)

    async def get_state(self) -> dict:
        """
        @TASK: Obtener estado del adaptador
        @INPUT: Sin parametros
        @OUTPUT: dict con estado actual
        @CONTEXT: Observabilidad para endpoints REST
        @SECURITY: Solo lectura
        """
        return {
            "adapter": "UnitreeG1Adapter",
            "initialized": self._initialized,
            "network_interface": self._network_interface,
        }

    async def emergency_stop(self) -> None:
        """
        @TASK: Parada de emergencia inmediata
        @INPUT: Sin parametros
        @OUTPUT: damp() ejecutado
        @CONTEXT: Maxima prioridad; invocable desde cualquier estado
        @SECURITY: damp() como primera y unica accion
        """
        LOGGER.critical("[REAL] EMERGENCY_STOP invocado.")
        await self.damp()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _assert_initialized(self) -> None:
        """Validar que initialize() fue invocado antes de cualquier comando."""
        if not self._initialized or self._sdk_client is None:
            raise RuntimeError(
                "UnitreeG1Adapter no inicializado. Invocar initialize() primero."
            )

    async def _invoke_sdk(self, method_name: str, *args: Any) -> Any:
        """
        @TASK: Invocar metodo del SDK en executor con timeout
        @INPUT: method_name, args
        @OUTPUT: Retorno del metodo SDK
        @CONTEXT: Toda llamada bloqueante al SDK pasa por aqui
        @SECURITY: wait_for con timeout previene bloqueo indefinido
        """
        method = getattr(self._sdk_client, method_name, None)
        if not callable(method):
            raise RuntimeError(
                f"LocoClient no implementa el metodo '{method_name}'."
            )

        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(self._executor, partial(method, *args))
        return await asyncio.wait_for(task, timeout=_SDK_CALL_TIMEOUT_S)


__all__ = ["UnitreeG1Adapter"]
