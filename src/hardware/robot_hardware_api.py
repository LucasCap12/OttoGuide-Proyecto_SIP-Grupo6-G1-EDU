from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from importlib import import_module
from threading import Lock
from typing import Any, Callable, Optional, Protocol, TypeVar, runtime_checkable


@runtime_checkable
class SupportsUnitreeHighLevelControl(Protocol):
    def Move(self, vx: float, vy: float, vyaw: float, continous_move: bool = ...) -> Any:
        ...

    def Damp(self) -> Any:
        ...

    def SetFsmId(self, fsm_id: int) -> Any:
        ...

    def SetBalanceMode(self, balance_mode: int) -> Any:
        ...


class RobotHardwareAPIError(RuntimeError):
    pass


class RobotHardwareEmergencyStopError(RobotHardwareAPIError):
    pass


T = TypeVar("T")


# @TASK: Definir limite cinemático de seguridad
# @INPUT: Ninguno
# @OUTPUT: Constante global MAX_LINEAR_VELOCITY en m/s
# @CONTEXT: Restriccion fisica obligatoria para operacion en superficies lisas
# STEP 1: Establecer limite lineal conservador de 0.3 m/s
# STEP 2: Reusar en clamping de comandos Move
# @SECURITY: Minimiza riesgo de caidas por aceleracion excesiva
# @AI_CONTEXT: Override deliberado sobre limite teorico de locomocion
MAX_LINEAR_VELOCITY: float = 0.3


def _default_unitree_client_factory() -> SupportsUnitreeHighLevelControl:
    # @TASK: Resolver cliente SDK2 G1 EDU via secuencia real de inicializacion
    # @INPUT: Sin parametros
    # @OUTPUT: Instancia de LocoClient compatible con Move/Damp/SetFsmId/SetBalanceMode
    # @CONTEXT: Capa de envoltura sobre unitree_sdk2py (nombre real del paquete)
    # STEP 1: Invocar ChannelFactoryInitialize(0) para crear DomainParticipant DDS
    # STEP 2: Instanciar LocoClient y llamar Init() para registrar APIs RPC
    # STEP 3: Verificar conformidad con Protocol antes de retornar
    # @SECURITY: ChannelFactoryInitialize respeta CYCLONEDDS_URI del entorno
    # @AI_CONTEXT: Permite testear con factorias inyectadas sin dependencia directa
    try:
        channel_init = import_module("unitree_sdk2py.core.channel")
        loco_module = import_module("unitree_sdk2py.g1.loco.g1_loco_client")
    except ModuleNotFoundError as exc:
        raise RobotHardwareAPIError(
            f"No se puede importar unitree_sdk2py: {exc}. "
            "Verificar que libs/unitree_sdk2_python-master esta en PYTHONPATH "
            "y que el SDK fue compilado con 'pip install -e .' o 'python setup.py install'."
        ) from exc

    # STEP 1: Inicializar ChannelFactory (DDS DomainParticipant)
    init_fn = getattr(channel_init, "ChannelFactoryInitialize", None)
    if not callable(init_fn):
        raise RobotHardwareAPIError(
            "ChannelFactoryInitialize no encontrado en unitree_sdk2py.core.channel."
        )
    try:
        init_fn(0)
    except Exception as exc:
        raise RobotHardwareAPIError(
            f"ChannelFactoryInitialize(0) fallo: {exc}"
        ) from exc

    # STEP 2: Instanciar LocoClient y registrar APIs
    loco_client_cls = getattr(loco_module, "LocoClient", None)
    if loco_client_cls is None:
        raise RobotHardwareAPIError(
            "LocoClient no encontrado en unitree_sdk2py.g1.loco.g1_loco_client."
        )

    client = loco_client_cls()
    client.Init()

    # STEP 3: Verificar conformidad con Protocol
    if not isinstance(client, SupportsUnitreeHighLevelControl):
        raise RobotHardwareAPIError(
            f"LocoClient no implementa SupportsUnitreeHighLevelControl. "
            f"Metodos disponibles: {[m for m in dir(client) if not m.startswith('_')]}"
        )

    return client


class RobotHardwareAPI:
    _instance: Optional["RobotHardwareAPI"] = None
    _instance_lock: Lock = Lock()

    def __init__(
        self,
        sdk_client: SupportsUnitreeHighLevelControl,
        *,
        call_timeout_s: float = 0.75,
        executor_workers: int = 1,
    ) -> None:
        # @TASK: Inicializar wrapper hardware
        # @INPUT: sdk_client, call_timeout_s, executor_workers
        # @OUTPUT: Estado interno listo para comandos asincronos
        # @CONTEXT: Adaptador Singleton para locomocion de alto nivel
        # STEP 1: Guardar cliente SDK y timeout de seguridad
        # STEP 2: Crear pool dedicado para aislar llamadas bloqueantes
        # @SECURITY: Limita paralelismo a 1 para evitar condiciones de carrera en bus
        # @AI_CONTEXT: Toda llamada SDK sale del event loop principal
        if call_timeout_s <= 0:
            raise ValueError("call_timeout_s debe ser mayor que 0.")
        if executor_workers <= 0:
            raise ValueError("executor_workers debe ser mayor que 0.")

        self._sdk_client: SupportsUnitreeHighLevelControl = sdk_client
        self._call_timeout_s: float = call_timeout_s
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=executor_workers,
            thread_name_prefix="unitree-sdk2",
        )

    @classmethod
    def get_instance(
        cls,
        *,
        client_factory: Optional[Callable[[], SupportsUnitreeHighLevelControl]] = None,
        call_timeout_s: float = 0.75,
        executor_workers: int = 1,
    ) -> "RobotHardwareAPI":
        # @TASK: Obtener singleton hardware
        # @INPUT: client_factory opcional, call_timeout_s, executor_workers
        # @OUTPUT: Instancia unica RobotHardwareAPI
        # @CONTEXT: Punto de acceso global para el TourOrchestrator
        # STEP 1: Crear la instancia una sola vez bajo lock de clase
        # STEP 2: Reusar la misma instancia en llamadas posteriores
        # @SECURITY: Evita condiciones de carrera en inicializacion
        # @AI_CONTEXT: Soporta inyeccion para mocks en pruebas de integracion
        with cls._instance_lock:
            if cls._instance is None:
                resolved_factory = client_factory or _default_unitree_client_factory
                sdk_client = resolved_factory()
                cls._instance = cls(
                    sdk_client,
                    call_timeout_s=call_timeout_s,
                    executor_workers=executor_workers,
                )
            return cls._instance

    async def move(self, vx: float, vy: float, wz: float) -> Any:
        # @TASK: Ejecutar comando Move
        # @INPUT: vx, vy, wz
        # @OUTPUT: Resultado del SDK o excepcion de dominio
        # @CONTEXT: Comando cinemático de alto nivel expuesto al orquestador
        # STEP 1: Invocar Move fuera del event loop con timeout
        # STEP 2: Activar Damp si falla para llevar robot a estado seguro
        # @SECURITY: Failsafe con parada amortiguada ante error
        # @AI_CONTEXT: Mantiene hilo principal libre de bloqueos de IO/SDK
        clamped_vx, clamped_vy = self._clamp_linear_velocity(vx, vy)
        try:
            return await self._invoke_sdk("Move", clamped_vx, clamped_vy, wz)
        except Exception as exc:
            await self._safe_damp_on_failure(exc)
            raise RobotHardwareAPIError("Fallo en Move; se ejecuto Damp().") from exc

    @staticmethod
    def _clamp_linear_velocity(vx: float, vy: float) -> tuple[float, float]:
        # @TASK: Aplicar clamping lineal en plano XY
        # @INPUT: vx, vy
        # @OUTPUT: Par (vx, vy) limitado por MAX_LINEAR_VELOCITY
        # @CONTEXT: Enforce de seguridad para comandos de locomocion API
        # STEP 1: Limitar cada componente a +/- MAX_LINEAR_VELOCITY
        # STEP 2: Si la norma vectorial excede el limite, reescalar ambos ejes
        # @SECURITY: Garantiza que ningun comando lineal supere 0.3 m/s
        # @AI_CONTEXT: Mantiene direccion de desplazamiento original al recortar
        limited_vx = max(-MAX_LINEAR_VELOCITY, min(MAX_LINEAR_VELOCITY, vx))
        limited_vy = max(-MAX_LINEAR_VELOCITY, min(MAX_LINEAR_VELOCITY, vy))

        norm = (limited_vx * limited_vx + limited_vy * limited_vy) ** 0.5
        if norm <= MAX_LINEAR_VELOCITY or norm == 0.0:
            return limited_vx, limited_vy

        scale = MAX_LINEAR_VELOCITY / norm
        return limited_vx * scale, limited_vy * scale

    async def euler(self, roll: float, pitch: float, yaw: float) -> Any:
        # @TASK: Ejecutar ajuste de actitud como adapter sobre SetBalanceMode del SDK G1
        # @INPUT: roll, pitch, yaw (radianes) — solo yaw es controlable de forma segura
        # @OUTPUT: Resultado del SDK o excepcion de dominio
        # @CONTEXT: El SDK G1 LocoClient no expone Euler() nativo; actitud se controla via FSM
        # STEP 1: Activar BalanceStand (mode=1) para habilitar control de equilibrio activo
        # STEP 2: Si el SDK expone Euler nativo (versiones futuras), usarlo directamente
        # STEP 3: Activar Damp si falla para transicion segura
        # @SECURITY: Reduce riesgo de estado mecanico inconsistente
        # @AI_CONTEXT: BalanceStand mode=1 equivale a ajuste postural; no permite roll/pitch arbitrarios
        try:
            euler_method = getattr(self._sdk_client, "Euler", None)
            if callable(euler_method):
                return await self._invoke_sdk("Euler", roll, pitch, yaw)
            return await self._invoke_sdk("SetBalanceMode", 1)
        except Exception as exc:
            await self._safe_damp_on_failure(exc)
            raise RobotHardwareAPIError("Fallo en euler; se ejecuto Damp().") from exc

    async def damp(self) -> Any:
        # @TASK: Ejecutar parada Damp
        # @INPUT: Sin parametros
        # @OUTPUT: Resultado del SDK o excepcion
        # @CONTEXT: Comando de emergencia para desacoplar actuacion
        # STEP 1: Invocar Damp fuera del event loop
        # STEP 2: Propagar resultado para telemetria/observabilidad
        # @SECURITY: Funcion critica de seguridad operacional
        # @AI_CONTEXT: Debe ser invocable tanto en error como manualmente
        return await self._invoke_sdk("Damp")

    async def _safe_damp_on_failure(self, cause: Exception) -> None:
        # @TASK: Proteger en falla critica
        # @INPUT: cause
        # @OUTPUT: Damp ejecutado o excepcion especializada
        # @CONTEXT: Ruta de contencion para errores en comandos cinemáticos
        # STEP 1: Intentar Damp inmediatamente
        # STEP 2: Elevar error de emergencia si Damp tambien falla
        # @SECURITY: Prioriza transicion a estado seguro del robot
        # @AI_CONTEXT: Aisla logica de fallback para reutilizar en Move/Euler
        try:
            await self.damp()
        except Exception as damp_exc:
            raise RobotHardwareEmergencyStopError(
                "Fallo al ejecutar Damp() durante recuperacion de emergencia."
            ) from damp_exc

    async def _invoke_sdk(self, method_name: str, *args: Any) -> Any:
        # @TASK: Invocar metodo SDK
        # @INPUT: method_name, args
        # @OUTPUT: Retorno del metodo remoto del SDK
        # @CONTEXT: Nucleo no bloqueante del wrapper de hardware
        # STEP 1: Resolver metodo y validar disponibilidad
        # STEP 2: Ejecutar en ThreadPool con timeout controlado
        # @SECURITY: Evita llamadas indefinidas con wait_for
        # @AI_CONTEXT: Centraliza toda interaccion bloqueante con unitree_sdk2py
        method = getattr(self._sdk_client, method_name, None)
        if not callable(method):
            raise RobotHardwareAPIError(
                f"El cliente Unitree no implementa el metodo {method_name}."
            )

        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(self._executor, partial(method, *args))
        return await asyncio.wait_for(task, timeout=self._call_timeout_s)

    def close(self) -> None:
        # @TASK: Liberar recursos SDK
        # @INPUT: Sin parametros
        # @OUTPUT: Executor detenido
        # @CONTEXT: Cierre ordenado del adaptador al apagar el sistema
        # STEP 1: Cancelar futures pendientes
        # STEP 2: Liberar hilos del pool dedicado
        # @SECURITY: Previene ejecucion tardia de comandos
        # @AI_CONTEXT: Invocar desde shutdown hook de la aplicacion
        self._executor.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "MAX_LINEAR_VELOCITY",
    "RobotHardwareAPI",
    "RobotHardwareAPIError",
    "RobotHardwareEmergencyStopError",
    "SupportsUnitreeHighLevelControl",
]