from __future__ import annotations

# @TASK: Implementar UnitreeG1SimAdapter como adaptador de simulacion MuJoCo
# @INPUT: Interfaz RobotHardwareInterface; SDK unitree_sdk2py importado localmente
# @OUTPUT: Adaptador funcional para control DDS del Unitree G1 en simulador
# @CONTEXT: Identico a real_adapter.py con DOMAIN_ID=1 e INTERFACE=lo
# @SECURITY: Clamping cinematico obligatorio; damp() con timeout 1.5s
# STEP 1: Import lazy de unitree_sdk2py solo dentro de metodos
# STEP 2: ChannelFactoryInitialize(id=1, networkInterface='lo') — firma auditada en
#         libs/unitree_sdk2_python-master/unitree_sdk2py/core/channel.py:298
#         Parametro networkInterface dispara ChannelConfigHasInterface con nombre 'lo'
# STEP 3: Clamping linear_x [-0.3, 0.3], angular_z [-0.5, 0.5]
# STEP 4: damp() con asyncio.wait_for timeout=1.5s
# STEP 5: Validacion en initialize(): verificar que simulador responde
# AUDIT RESULT — ChannelFactoryInitialize: contrato VALIDO (firma auditada channel.py:298)
# AUDIT RESULT — Move(vx, vy, vyaw, continous_move=False): contrato VALIDO
# AUDIT RESULT — stand() CORREGIDO: SetFsmId(1)=Damp; Start()=SetFsmId(200) para bipedestacion
# AUDIT RESULT — Damp(): contrato VALIDO — LocoClient.Damp() llama SetFsmId(1)
# AUDIT RESULT — IDL: unitree_hg confirmado presente en libs/unitree_sdk2py/idl/unitree_hg/
# AUDIT RESULT — Escena simulador: unitree_robots/g1/scene_29dof.xml auditada OK

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Optional

from .interface import MotionCommand, RobotHardwareInterface

LOGGER = logging.getLogger("otto_guide.hardware.sim_adapter")

# ---------------------------------------------------------------------------
# Constantes de seguridad cinematica
# ---------------------------------------------------------------------------
_MAX_LINEAR_X: float = 0.3
_MAX_ANGULAR_Z: float = 0.5
_DAMP_TIMEOUT_S: float = 1.5
_SDK_CALL_TIMEOUT_S: float = 0.75
_SIM_DOMAIN_ID: int = 1
_SIM_INTERFACE: str = "lo"


class UnitreeG1SimAdapter(RobotHardwareInterface):
    """
    @TASK: Adaptador de simulacion para Unitree G1 EDU 8 via unitree_mujoco
    @INPUT: DDS domain 1 en interfaz loopback (lo)
    @OUTPUT: Control de locomocion de alto nivel con clamping de seguridad
    @CONTEXT: Solo instanciar cuando ROBOT_MODE=sim
              Requiere unitree_mujoco corriendo como proceso externo:
              cd /ruta/a/unitree_mujoco && python3 simulate.py g1/scene_29dof.xml
              Repositorio: github.com/unitreerobotics/unitree_mujoco
              Escena: scene_29dof.xml (G1 EDU 8 = 29 DOF)
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
        LOGGER.info(
            "[SIM] UnitreeG1SimAdapter creado. domain_id=%d, interface='%s'",
            _SIM_DOMAIN_ID,
            _SIM_INTERFACE,
        )

    async def initialize(self) -> None:
        """
        @TASK: Inicializar SDK Unitree y negociar DDS en executor para simulador
        @INPUT: Sin parametros (DOMAIN_ID=1, INTERFACE=lo fijos)
        @OUTPUT: _sdk_client listo; _initialized=True
        @CONTEXT: ChannelFactoryInitialize(1) → DDS domain 1, loopback
        STEP 1: Importar unitree_sdk2py localmente
        STEP 2: Ejecutar init DDS en executor (domain 1)
        STEP 3: Crear LocoClient e invocar Init()
        STEP 4: Validar que el simulador responde en domain 1
        @SECURITY: Sin IPs hardcodeadas; loopback para simulacion
        """
        if self._initialized:
            LOGGER.warning("[SIM] initialize() invocado en adaptador ya inicializado.")
            return

        # STEP 1: Import lazy del SDK — unico punto de importacion en simulacion
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient
        except ImportError as exc:
            raise RuntimeError(
                f"No se puede importar unitree_sdk2py: {exc}. "
                "Instalar con: pip install 'otto-guide[hardware]'"
            ) from exc

        # STEP 2: Crear executor dedicado y negociar DDS domain 1 (simulador)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="unitree-sim",
        )

        loop = asyncio.get_running_loop()
        LOGGER.info(
            "[SIM] Negociando DDS via ChannelFactoryInitialize(%d)...",
            _SIM_DOMAIN_ID,
        )

        try:
            await loop.run_in_executor(
                self._executor,
                partial(ChannelFactoryInitialize, _SIM_DOMAIN_ID, _SIM_INTERFACE),
            )
        except Exception as exc:
            raise RuntimeError(
                f"ChannelFactoryInitialize({_SIM_DOMAIN_ID}, '{_SIM_INTERFACE}') fallo: {exc}. "
                "Verificar que unitree_mujoco esta corriendo:\n"
                "  cd /ruta/a/unitree_mujoco\n"
                "  python3 simulate.py g1/scene_29dof.xml\n"
                "Repositorio: https://github.com/unitreerobotics/unitree_mujoco"
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
                f"LocoClient.Init() fallo en modo simulacion: {exc}. "
                "Verificar que unitree_mujoco esta corriendo con escena g1/scene_29dof.xml "
                "en DDS domain 1 (loopback).\n"
                "Comando: cd /ruta/a/unitree_mujoco && python3 simulate.py g1/scene_29dof.xml"
            ) from exc

        self._sdk_client = client
        self._initialized = True
        LOGGER.info(
            "[SIM] SDK inicializado correctamente. LocoClient activo en domain %d.",
            _SIM_DOMAIN_ID,
        )

    async def stand(self) -> None:
        """
        @TASK: Comandar bipedestacion via Start() (SetFsmId=200) en simulador
        @INPUT: Sin parametros
        @OUTPUT: Robot simulado de pie en posicion neutra
        @CONTEXT: Auditado contra g1_loco_client.py — Start() = SetFsmId(200)
                  CORRECCION: SetFsmId(1) = Damp (estado seguro), NO bipedestacion
                  SetFsmId(200) = Start = bipedestacion operativa
        @SECURITY: Verificar que initialize() fue invocado
        """
        self._assert_initialized()
        await self._invoke_sdk("Start")
        LOGGER.info("[SIM] Stand ejecutado via Start() (SetFsmId=200).")

    async def damp(self) -> None:
        """
        @TASK: Ejecutar parada amortiguada con timeout hard 1.5s en simulador
        @INPUT: Sin parametros
        @OUTPUT: Actuadores desacoplados en simulacion
        @CONTEXT: Timeout impuesto localmente; no delegado al caller
        @SECURITY: Funcion critica — timeout 1.5s es hard limit
        """
        self._assert_initialized()
        try:
            await asyncio.wait_for(
                self._invoke_sdk("Damp"),
                timeout=_DAMP_TIMEOUT_S,
            )
            LOGGER.info("[SIM] Damp() ejecutado correctamente (timeout=%.1fs).", _DAMP_TIMEOUT_S)
        except asyncio.TimeoutError:
            LOGGER.critical(
                "[SIM] TIMEOUT en Damp() (%.1fs). Verificar estado del simulador.",
                _DAMP_TIMEOUT_S,
            )
            raise

    async def move(self, command: MotionCommand) -> None:
        """
        @TASK: Ejecutar comando de movimiento con clamping cinematico en simulador
        @INPUT: command — MotionCommand con linear_x, angular_z, duration_ms
        @OUTPUT: Robot simulado en movimiento durante duration_ms
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
            LOGGER.error("[SIM] Fallo en Move; ejecutando Damp() de emergencia.")
            await self.damp()
            raise RuntimeError(f"Fallo en Move; Damp() ejecutado: {exc}") from exc

        # STEP 3: Esperar duracion del comando
        if command.duration_ms > 0:
            await asyncio.sleep(command.duration_ms / 1000.0)

    async def get_state(self) -> dict:
        """
        @TASK: Obtener estado del adaptador de simulacion
        @INPUT: Sin parametros
        @OUTPUT: dict con estado actual
        @CONTEXT: Observabilidad para endpoints REST
        @SECURITY: Solo lectura
        """
        return {
            "adapter": "UnitreeG1SimAdapter",
            "initialized": self._initialized,
            "domain_id": _SIM_DOMAIN_ID,
            "interface": _SIM_INTERFACE,
        }

    async def emergency_stop(self) -> None:
        """
        @TASK: Parada de emergencia inmediata en simulador
        @INPUT: Sin parametros
        @OUTPUT: damp() ejecutado
        @CONTEXT: Maxima prioridad; invocable desde cualquier estado
        @SECURITY: damp() como primera y unica accion
        """
        LOGGER.critical("[SIM] EMERGENCY_STOP invocado.")
        await self.damp()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _assert_initialized(self) -> None:
        """Validar que initialize() fue invocado antes de cualquier comando."""
        if not self._initialized or self._sdk_client is None:
            raise RuntimeError(
                "UnitreeG1SimAdapter no inicializado. Invocar initialize() primero."
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


__all__ = ["UnitreeG1SimAdapter"]
