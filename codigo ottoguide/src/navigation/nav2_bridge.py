"""
@TASK: Proveer interfaz asincrona entre TourOrchestrator y el stack ROS 2 Nav2
@INPUT: BasicNavigator de nav2_simple_commander; PoseEstimate de VisionProcessor
@OUTPUT: Corrutinas consumibles por TourOrchestrator sin bloqueo del event loop principal
@CONTEXT: Capa de bridge HIL Fase 3; unico punto de acceso async a Nav2 para el orquestador.
          Arquitectura de dos capas: _BridgeNode (nodo ROS 2) + AsyncNav2Bridge (orquestador async).
          El spin del executor ROS 2 corre en un daemon thread aislado del event loop de asyncio.
@SECURITY: Ninguna llamada bloqueante de ROS 2 se ejecuta directamente en el event loop principal.
           ThreadPoolExecutor dedicado (work_executor) aísla todas las llamadas síncronas a Nav2.
           call_soon_threadsafe es el único mecanismo aprobado para cruzar hilo-ROS2 → event loop.

STEP 1: Instanciar BasicNavigator y nodo ROS 2 en contexto de hilo aislado (work_executor)
STEP 2: Mantener spin del executor en daemon thread sin tocar el event loop de asyncio
STEP 3: Exponer navegacion, inyeccion AMCL y estado via primitivas asyncio (await + asyncio.Event)
STEP 4: Aplicar clamping cinematico estricto 0.3 m/s en cmd_vel interceptado por subscriber
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence, TYPE_CHECKING

import cv2
import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from src.vision import PoseEstimate

if TYPE_CHECKING:
    from nav2_simple_commander.robot_navigator import BasicNavigator


# ---------------------------------------------------------------------------
# Constantes de dominio
# ---------------------------------------------------------------------------

"""
@TASK: Declarar limites cinematicos de seguridad y parametros operativos del bridge Nav2
@INPUT: Ninguno
@OUTPUT: Constantes MAX_LINEAR_VELOCITY, MAX_ANGULAR_VELOCITY, topicos y parametros de sondeo
@CONTEXT: Restriccion fisica impuesta por friccion del suelo en el Unitree G1 EDU en espacios indoor.
          MAX_LINEAR_VELOCITY debe coincidir con RobotHardwareAPI.MAX_LINEAR_VELOCITY del HAL.
@SECURITY: Estos valores son la unica fuente de verdad de limite cinematico del bridge.
           Una modificacion aqui afecta el clamping en /cmd_vel y el setSpeedLimit de Nav2.

STEP 1: Definir cap lineal de 0.3 m/s documentado en el manual de hardware del Unitree G1 EDU
STEP 2: Definir cap angular conservador de 0.5 rad/s para giros en espacio indoor reducido
"""
MAX_LINEAR_VELOCITY: float = 0.3   # m/s — clamping estricto por restriccion de friccion
MAX_ANGULAR_VELOCITY: float = 0.5  # rad/s — conservador para giros en espacio reducido

AMCL_TOPIC: str = "/initialpose"
CMD_VEL_TOPIC: str = "/cmd_vel"
CMD_VEL_FILTERED_TOPIC: str = "/cmd_vel_nav"  # topico de publicacion post-clamp
NAV2_TASK_POLL_INTERVAL_S: float = 0.05       # intervalo de sondeo isTaskComplete()

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NavWaypoint:
    """
    @TASK: Representar un waypoint de navegacion en el frame del mapa de forma inmutable
    @INPUT: Coordenadas x, y en metros y yaw en radianes relativas al origen del mapa; frame opcional
    @OUTPUT: Estructura inmutable consumible por AsyncNav2Bridge.navigate_to_waypoints y send_goal
    @CONTEXT: Tipo de dominio interno del bridge; independiente de PoseStamped de ROS 2.
              Equivalente a Waypoint en NavigationManager; separado por clean architecture.
    @SECURITY: frozen=True evita mutacion accidental de coordenadas durante la ejecucion del plan.

    STEP 1: Capturar posicion 2D y orientacion yaw del plan de ruta del TourOrchestrator
    STEP 2: Permitir override del frame_id para casos multi-mapa o frames de odometria
    """

    x: float
    y: float
    yaw_rad: float
    frame_id: str = "map"


@dataclass(slots=True)
class NavigationStatus:
    """
    @TASK: Encapsular el estado observable de la tarea de navegacion activa en el bridge
    @INPUT: Indicadores de tarea activa, resultado del ultimo plan y waypoint activo actual
    @OUTPUT: Snapshot del estado compartido entre el hilo ROS 2 y las corrutinas async
    @CONTEXT: Estado mutable compartido; acceso protegido por asyncio.Lock en el bridge.
              No usar directamente; consultado via propiedades y metodos protegidos del bridge.
    @SECURITY: Acceso siempre protegido por _status_lock (asyncio.Lock) para evitar race conditions
               entre el hilo de spin ROS 2 y el event loop de asyncio.

    STEP 1: Registrar si hay una tarea Nav2 activa y resultado del ultimo plan ejecutado
    STEP 2: Mantener indice del waypoint activo para observabilidad y telemetria
    """

    task_active: bool = False
    last_result_succeeded: Optional[bool] = None
    active_waypoint_index: int = 0


# ---------------------------------------------------------------------------
# Nodo ROS 2 interno del bridge
# ---------------------------------------------------------------------------

class _BridgeNode(Node):
    """
    @TASK: Proveer nodo ROS 2 dedicado para publicaciones y suscripciones del bridge Nav2
    @INPUT: Nombre del nodo y callable de clamping para el subscriber /cmd_vel
    @OUTPUT: Publisher /initialpose (AMCL), subscriber /cmd_vel, publisher /cmd_vel_nav (post-clamp)
    @CONTEXT: Nodo interno del bridge; vive exclusivamente en el hilo de spin del MultiThreadedExecutor.
              Nunca expuesto directamente al event loop de asyncio; propiedad exclusiva de AsyncNav2Bridge.
    @SECURITY: El subscriber /cmd_vel intercepta y satura; nunca republica comandos sin clamping.
               QoS depth=1 en /initialpose evita acumulacion de correcciones AMCL obsoletas en cola.
    """

    def __init__(
        self,
        node_name: str,
        *,
        on_cmd_vel: Callable[[Twist], None],
    ) -> None:
        """
        @TASK: Inicializar el nodo ROS 2 bridge con publishers de AMCL y cmd_vel, y subscriber de velocidad
        @INPUT: node_name — nombre del nodo ROS 2 en el grafo DDS;
                on_cmd_vel — callable inyectado para interceptacion y clamping de Twist
        @OUTPUT: Nodo ROS 2 activo con _amcl_publisher, _cmd_vel_pub y _cmd_vel_sub configurados
        @CONTEXT: Constructor invocado desde el hilo de spin antes de iniciar spin() del executor.
                  on_cmd_vel es tipicamente AsyncNav2Bridge._clamp_and_republish.
        @SECURITY: QoS depth=1 en AMCL evita que correcciones obsoletas se acumulen en la cola DDS.
                   Los publishers y subscribers son thread-safe en rclpy cuando el nodo esta en spin.

        STEP 1: Llamar super().__init__ con el nombre de nodo asignado para registrar en el grafo ROS 2
        STEP 2: Crear publisher /initialpose con QoS depth=1 para correcciones AMCL puntuales
        STEP 3: Crear subscriber /cmd_vel con on_cmd_vel como callback de clamping cinematico
        STEP 4: Crear publisher /cmd_vel_nav para reinyectar el Twist post-clamp al controlador
        """
        super().__init__(node_name)

        self._amcl_publisher = self.create_publisher(
            PoseWithCovarianceStamped,
            AMCL_TOPIC,
            1,
        )

        self._cmd_vel_pub = self.create_publisher(
            Twist,
            CMD_VEL_FILTERED_TOPIC,
            10,
        )

        self._cmd_vel_sub = self.create_subscription(
            Twist,
            CMD_VEL_TOPIC,
            on_cmd_vel,
            10,
        )

    def publish_initial_pose(self, msg: PoseWithCovarianceStamped) -> None:
        """
        @TASK: Publicar correccion de pose absoluta en el topico /initialpose para AMCL
        @INPUT: msg — mensaje PoseWithCovarianceStamped ya construido por _build_amcl_msg
        @OUTPUT: Mensaje publicado en AMCL_TOPIC (/initialpose) en el bus DDS
        @CONTEXT: Invocado desde el work_executor mediante loop.run_in_executor para no bloquear el loop.
                  La llamada a publish() es thread-safe en rclpy cuando el nodo esta en spin activo.
        @SECURITY: Sin transformacion adicional del mensaje; el caller (_build_amcl_msg) es responsable
                   de la covarianza y el contenido geometrico. Invocar solo desde work_executor.
        """
        self._amcl_publisher.publish(msg)

    def publish_clamped_cmd_vel(self, msg: Twist) -> None:
        """
        @TASK: Republicar Twist ya saturado cinematicamente en el topico cmd_vel filtrado
        @INPUT: msg — Twist ya clampeado por _clamp_and_republish con norma verificada
        @OUTPUT: Mensaje publicado en CMD_VEL_FILTERED_TOPIC (/cmd_vel_nav) en el bus DDS
        @CONTEXT: Resultado final de la cadena de interceptacion del subscriber /cmd_vel.
                  El controlador de bajo nivel del robot debe suscribirse a CMD_VEL_FILTERED_TOPIC.
        @SECURITY: Garantiza que ningun comando linear supera MAX_LINEAR_VELOCITY (0.3 m/s)
                   ni ningun comando angular supera MAX_ANGULAR_VELOCITY (0.5 rad/s) en el bus DDS.
        """
        self._cmd_vel_pub.publish(msg)


# ---------------------------------------------------------------------------
# Bridge principal
# ---------------------------------------------------------------------------

class AsyncNav2Bridge:
    """
    @TASK: Encapsular el stack Nav2 como interfaz asincrona no bloqueante para TourOrchestrator
    @INPUT: Parametros de configuracion de nombre de nodo y workers del executor de trabajo
    @OUTPUT: API async para navegacion por waypoints, inyeccion AMCL y consulta de estado de tarea
    @CONTEXT: Componente central de navegacion HIL Fase 3; reemplazo/superconjunto de NavigationManager.
              Inicializacion en dos fases: __init__ (sync ligero, sin ROS 2) + async start() (ROS 2 completo).
              El spin del MultiThreadedExecutor corre en un daemon thread aislado del event loop.
    @SECURITY: Toda llamada bloqueante de Nav2 se ejecuta en _work_executor (ThreadPoolExecutor).
               call_soon_threadsafe es el unico mecanismo aprobado para cruzar hilo-ROS2 → event loop.
               El caller (main.py o NavigationManager) debe invocar await start() antes de cualquier uso.
    """

    def __init__(
        self,
        *,
        node_name: str = "async_nav2_bridge",
        work_executor_workers: int = 1,
    ) -> None:
        """
        @TASK: Construir estado interno del bridge sin inicializar ROS 2 ni rclpy
        @INPUT: node_name — nombre del nodo ROS 2 en el grafo DDS (default "async_nav2_bridge");
                work_executor_workers — numero de workers del ThreadPoolExecutor de trabajo (default 1)
        @OUTPUT: Bridge en estado PRE-INIT con primitivas asyncio creadas pero ROS 2 no activo;
                 inutilizable hasta invocar await start() desde el event loop principal
        @CONTEXT: Separacion __init__/start() para compatibilidad con inyeccion de dependencias en main.py.
                  asyncio.Event y asyncio.Lock se crean aqui y se vinculan al loop activo en start().
        @SECURITY: No toca rclpy en __init__; evita condiciones de orden en el bootstrap del proceso.
                   work_executor_workers <= 0 lanza ValueError antes de crear ningun recurso.

        STEP 1: Validar work_executor_workers > 0; inicializar primitivas asyncio de sincronizacion
        STEP 2: Crear _work_executor (ThreadPoolExecutor) para aislar llamadas bloqueantes a Nav2
        STEP 3: Inicializar referencias que se completaran en start() a None para deteccion de estado
        """
        if work_executor_workers <= 0:
            raise ValueError("work_executor_workers debe ser mayor que 0.")

        self._node_name: str = node_name
        self._started: bool = False

        self._nav_complete_event: asyncio.Event = asyncio.Event()
        self._status_lock: asyncio.Lock = asyncio.Lock()
        self._nav_status: NavigationStatus = NavigationStatus()

        self._work_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=work_executor_workers,
            thread_name_prefix="nav2-bridge-work",
        )

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._node: Optional[_BridgeNode] = None
        self._navigator: Optional["BasicNavigator"] = None
        self._spin_executor: Optional[MultiThreadedExecutor] = None
        self._spin_thread: Optional[threading.Thread] = None

        LOGGER.debug("[Nav2Bridge] Instancia creada en estado PRE-INIT.")

    # -----------------------------------------------------------------------
    # Ciclo de vida
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """
        @TASK: Inicializar ROS 2, el nodo bridge, BasicNavigator y lanzar el spin daemon
        @INPUT: Sin parametros; usa node_name y work_executor_workers del constructor
        @OUTPUT: Bridge en estado ACTIVO con nodo ROS 2 en spin y Nav2 disponible para comandos
        @CONTEXT: Debe invocarse una sola vez desde el event loop principal en main.py lifespan.
                  Llamadas repetidas son ignoradas con LOGGER.warning para idempotencia.
        @SECURITY: waitUntilNav2Active bloqueante se aisla en work_executor para no colgar el event loop.
                   El spin thread es daemon=True; muere automaticamente con el proceso principal.
                   rclpy.init() solo se llama si rclpy.ok() es False para evitar re-inicializacion.

        STEP 1: Capturar referencia al event loop activo para uso thread-safe posterior en callbacks
        STEP 2: Inicializar rclpy si aun no ha sido inicializado (rclpy.ok() == False)
        STEP 3: Instanciar _BridgeNode con _clamp_and_republish como callback de subscriber /cmd_vel
        STEP 4: Instanciar BasicNavigator en work_executor (puede tardar varios segundos en hardware real)
        STEP 5: Configurar MultiThreadedExecutor con el nodo y lanzar daemon thread de spin
        STEP 6: Esperar waitUntilNav2Active en work_executor (bloqueante, aislado del event loop)
        """
        if self._started:
            LOGGER.warning("[Nav2Bridge] start() llamado mas de una vez; ignorado.")
            return

        self._loop = asyncio.get_running_loop()

        if not rclpy.ok():
            rclpy.init(args=None)

        self._node = _BridgeNode(
            self._node_name,
            on_cmd_vel=self._clamp_and_republish,
        )

        LOGGER.info("[Nav2Bridge] Instanciando BasicNavigator en executor...")
        self._navigator = await self._loop.run_in_executor(
            self._work_executor,
            self._create_navigator_sync,
        )

        self._spin_executor = MultiThreadedExecutor(num_threads=2)
        self._spin_executor.add_node(self._node)
        self._spin_thread = threading.Thread(
            target=self._spin_forever,
            name="nav2-bridge-spin",
            daemon=True,
        )
        self._spin_thread.start()
        LOGGER.info("[Nav2Bridge] Spin daemon thread iniciado.")

        LOGGER.info("[Nav2Bridge] Esperando activacion de Nav2...")
        await self._loop.run_in_executor(
            self._work_executor,
            self._wait_nav2_active_sync,
        )

        self._started = True
        LOGGER.info("[Nav2Bridge] Bridge activo. Nav2 disponible.")

    async def close(self) -> None:
        """
        @TASK: Detener el spin executor, destruir el nodo ROS 2 y liberar el executor de trabajo
        @INPUT: Sin parametros
        @OUTPUT: Todos los recursos ROS 2 y threads del bridge liberados de forma ordenada
        @CONTEXT: Invocado desde _graceful_shutdown en main.py durante el lifespan de FastAPI.
                  _close_sync se despacha al work_executor para garantizar destruccion thread-safe del nodo.
        @SECURITY: Debe invocarse antes de rclpy.shutdown() del proceso principal para orden correcto.
                   cancel_futures=True en work_executor evita ejecuciones de Nav2 residuales post-cierre.

        STEP 1: Despachar _close_sync al work_executor para detener spin_executor y destruir el nodo
        STEP 2: Apagar _work_executor con cancel_futures=True para limpiar futures pendientes
        """
        LOGGER.info("[Nav2Bridge] Iniciando cierre.")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._work_executor, self._close_sync)
        self._work_executor.shutdown(wait=False, cancel_futures=True)
        LOGGER.info("[Nav2Bridge] Cierre completado.")

    # -----------------------------------------------------------------------
    # API de navegacion
    # -----------------------------------------------------------------------

    async def navigate_to_waypoints(
        self,
        waypoints: Sequence[NavWaypoint],
    ) -> bool:
        """
        @TASK: Enviar un plan de ruta multi-waypoint a Nav2 y esperar su completitud de forma asincrona
        @INPUT: waypoints — secuencia de NavWaypoint en frame map con x, y y yaw_rad
        @OUTPUT: True si el plan completo fue ejecutado con exito por Nav2; False en caso de fallo
        @CONTEXT: Unica via de navegacion multi-waypoint desde TourOrchestrator en HIL Fase 3.
                  Lista vacia retorna True inmediatamente (plan trivialmente completado).
        @SECURITY: asyncio.Lock (_status_lock) protege NavigationStatus ante race conditions entre
                   el hilo de spin ROS 2 y las corrutinas del event loop.
                   isTaskComplete() se sondea en work_executor; nunca directamente en el event loop.

        STEP 1: Verificar precondiciones con _assert_started; retornar True si lista vacia
        STEP 2: Marcar tarea activa bajo _status_lock y limpiar _nav_complete_event
        STEP 3: Despachar _follow_waypoints_and_signal al work_executor (bloqueante, con sondeo)
        STEP 4: Esperar _nav_complete_event seteado por el hilo de trabajo via call_soon_threadsafe
        STEP 5: Leer last_result_succeeded bajo _status_lock y retornar resultado booleano
        """
        self._assert_started("navigate_to_waypoints")

        if not waypoints:
            LOGGER.warning("[Nav2Bridge] navigate_to_waypoints recibio lista vacia.")
            return True

        async with self._status_lock:
            self._nav_status.task_active = True
            self._nav_status.last_result_succeeded = None
            self._nav_status.active_waypoint_index = 0
        self._nav_complete_event.clear()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._work_executor,
            self._follow_waypoints_and_signal,
            list(waypoints),
            loop,
        )

        await self._nav_complete_event.wait()

        async with self._status_lock:
            result = self._nav_status.last_result_succeeded
        return result is True

    async def send_goal(self, waypoint: NavWaypoint) -> bool:
        """
        @TASK: Enviar un unico waypoint a Nav2 como goal de navegacion y esperar su completitud
        @INPUT: waypoint — NavWaypoint con x, y, yaw_rad y frame_id del destino en frame map
        @OUTPUT: True si Nav2 alcanzo el goal con exito (TaskResult.SUCCEEDED); False en caso de fallo
        @CONTEXT: Alias de navegacion a un solo waypoint; internamente usa goToPose si disponible
                  en la version de Nav2 instalada, o followWaypoints como fallback.
                  Equivalente a navigate_to_waypoints con una lista de un elemento.
        @SECURITY: Toda llamada bloqueante a Nav2 (goToPose, isTaskComplete) se aisla en work_executor
                   via loop.run_in_executor para no bloquear el event loop de asyncio.
                   call_soon_threadsafe es el unico mecanismo para cruzar hilo-trabajo → event loop.
                   _status_lock protege NavigationStatus ante race conditions hilo/corrutina.

        STEP 1: Verificar precondiciones con _assert_started; marcar tarea activa bajo _status_lock
        STEP 2: Limpiar _nav_complete_event; despachar _send_goal_and_wait_sync al work_executor
        STEP 3: Esperar _nav_complete_event seteado por el hilo de trabajo via call_soon_threadsafe
        STEP 4: Leer last_result_succeeded bajo _status_lock y retornar resultado booleano
        """
        self._assert_started("send_goal")
        async with self._status_lock:
            self._nav_status.task_active = True
            self._nav_status.last_result_succeeded = None
            self._nav_status.active_waypoint_index = 0
        self._nav_complete_event.clear()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._work_executor,
            self._send_goal_and_wait_sync,
            waypoint,
            loop,
        )
        await self._nav_complete_event.wait()
        async with self._status_lock:
            result = self._nav_status.last_result_succeeded
        return result is True

    async def cancel_navigation(self) -> None:
        """
        @TASK: Cancelar la tarea de navegacion activa en BasicNavigator de forma asincrona
        @INPUT: Sin parametros
        @OUTPUT: Plan Nav2 cancelado; NavigationStatus reseteado; _nav_complete_event seteado
        @CONTEXT: Invocado por TourOrchestrator al entrar a estado EMERGENCY o INTERACTING.
                  Si no hay tarea activa, retorna inmediatamente sin invocar cancelNav.
        @SECURITY: _status_lock garantiza consistencia al leer task_active antes de cancelar.
                   cancelNav() bloqueante se aisla en work_executor para no colgar el event loop.
                   _nav_complete_event.set() fuerza el retorno de navigate_to_waypoints en espera.

        STEP 1: Verificar _assert_started; leer task_active bajo _status_lock; retornar si inactivo
        STEP 2: Despachar _cancel_nav_sync al work_executor para invocar cancelNav en BasicNavigator
        STEP 3: Resetear NavigationStatus bajo _status_lock y setear _nav_complete_event si no esta set
        """
        self._assert_started("cancel_navigation")

        async with self._status_lock:
            if not self._nav_status.task_active:
                return

        loop = asyncio.get_running_loop()

        await loop.run_in_executor(
            self._work_executor,
            self._cancel_nav_sync,
        )

        async with self._status_lock:
            self._nav_status.task_active = False
            self._nav_status.last_result_succeeded = False

        if not self._nav_complete_event.is_set():
            self._nav_complete_event.set()

        LOGGER.info("[Nav2Bridge] Navegacion cancelada por solicitud del orquestador.")

    async def is_navigation_active(self) -> bool:
        """
        @TASK: Consultar si hay una tarea de navegacion activa en Nav2 en este momento
        @INPUT: Sin parametros
        @OUTPUT: True si Nav2 esta ejecutando un plan actualmente; False si inactivo o completado
        @CONTEXT: Metodo de sondeo para TourOrchestrator o capa de observabilidad de la API.
                  Utilizar en lugar de acceder a _nav_status.task_active directamente.
        @SECURITY: _status_lock garantiza lectura consistente de task_active sin race condition
                   entre el hilo de spin ROS 2 y las corrutinas del event loop.

        STEP 1: Adquirir _status_lock y retornar _nav_status.task_active de forma thread-safe
        """
        async with self._status_lock:
            return self._nav_status.task_active

    # -----------------------------------------------------------------------
    # Inyeccion de odometria absoluta (AprilTag -> AMCL)
    # -----------------------------------------------------------------------

    async def inject_absolute_pose(self, pose_estimate: PoseEstimate) -> None:
        """
        @TASK: Publicar correccion de pose absoluta en /initialpose para correccion de deriva AMCL
        @INPUT: pose_estimate — PoseEstimate con rvec (Rodrigues 3x1) y tvec calculados por VisionProcessor
        @OUTPUT: Mensaje PoseWithCovarianceStamped publicado en /initialpose en el bus DDS
        @CONTEXT: Correccion de deriva odometrica mediante deteccion de marcador AprilTag tag36h11.
                  La frecuencia de inyeccion debe ser controlada por el caller para no saturar AMCL.
        @SECURITY: La publicacion se despacha al work_executor para que ocurra en el hilo del nodo ROS 2.
                   Las covarianzas diagonales (0.15, 0.15, 0.40) son valores de inicio calibrados en HIL.
                   El caller es responsable de controlar la tasa de inyeccion (tipicamente 1-5 Hz max).

        STEP 1: Verificar _assert_started; construir PoseWithCovarianceStamped con _build_amcl_msg
        STEP 2: Despachar publish_initial_pose al work_executor para publicar en el hilo del nodo
        """
        self._assert_started("inject_absolute_pose")

        msg = self._build_amcl_msg(pose_estimate)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._work_executor,
            self._node.publish_initial_pose,
            msg,
        )
        LOGGER.debug(
            "[Nav2Bridge] Pose AMCL inyectada: tvec=(%.3f, %.3f, %.3f) marker_id=%d",
            pose_estimate.tvec[0][0],
            pose_estimate.tvec[1][0],
            pose_estimate.tvec[2][0],
            pose_estimate.marker_id,
        )

    # -----------------------------------------------------------------------
    # Clamping cinematico — subscriber /cmd_vel
    # -----------------------------------------------------------------------

    def _clamp_and_republish(self, msg: Twist) -> None:
        """
        @TASK: Interceptar Twist de Nav2 en /cmd_vel y republicar con velocidades saturadas
        @INPUT: msg — Twist recibido en /cmd_vel desde el planificador de trayectorias de Nav2
        @OUTPUT: Twist clampeado publicado en CMD_VEL_FILTERED_TOPIC (/cmd_vel_nav)
        @CONTEXT: Callback del subscriber /cmd_vel; ejecutado en el hilo de spin del MultiThreadedExecutor.
                  Es la primera barrera cinematica de seguridad (la segunda es setSpeedLimit en Nav2).
        @SECURITY: Ninguna modificacion en caliente del msg original recibido del planificador Nav2.
                   Este callback corre en el hilo de spin ROS 2; nunca acceder a primitivas asyncio aqui.
                   La norma vectorial XY se verifica y reescala para garantizar el limite en 2D.

        STEP 1: Crear nuevo Twist (clamped) sin mutar el mensaje original recibido
        STEP 2: Saturar componentes lineales x e y individualmente a MAX_LINEAR_VELOCITY
        STEP 3: Verificar norma vectorial XY y reescalar proporcionalidente si excede el limite
        STEP 4: Saturar componente angular z a MAX_ANGULAR_VELOCITY
        STEP 5: Publicar el Twist clampeado en CMD_VEL_FILTERED_TOPIC via _node
        """
        clamped = Twist()
        clamped.angular.x = msg.angular.x
        clamped.angular.y = msg.angular.y

        lx = max(-MAX_LINEAR_VELOCITY, min(MAX_LINEAR_VELOCITY, msg.linear.x))
        ly = max(-MAX_LINEAR_VELOCITY, min(MAX_LINEAR_VELOCITY, msg.linear.y))

        norm = (lx * lx + ly * ly) ** 0.5
        if norm > MAX_LINEAR_VELOCITY and norm > 0.0:
            scale = MAX_LINEAR_VELOCITY / norm
            lx *= scale
            ly *= scale

        clamped.linear.x = lx
        clamped.linear.y = ly
        clamped.linear.z = 0.0  # movimiento planar unicamente

        clamped.angular.z = max(
            -MAX_ANGULAR_VELOCITY,
            min(MAX_ANGULAR_VELOCITY, msg.angular.z),
        )

        if self._node is not None:
            self._node.publish_clamped_cmd_vel(clamped)

    # -----------------------------------------------------------------------
    # Metodos sincronos para executor
    # -----------------------------------------------------------------------

    @staticmethod
    def _create_navigator_sync() -> "BasicNavigator":
        """
        @TASK: Instanciar BasicNavigator dentro del work_executor de forma sincrona
        @INPUT: Sin parametros
        @OUTPUT: Instancia de BasicNavigator lista para recibir comandos de Nav2
        @CONTEXT: Llamada despachada al work_executor durante start(); puede tardar varios segundos
                  en hardware real mientras Nav2 carga los costmaps y los servidores de accion.
        @SECURITY: Import dinamico evita ImportError en entornos de desarrollo sin Nav2 instalado.
                   BasicNavigator crea internamente un nodo hijo ROS 2; no interferir con _BridgeNode.

        STEP 1: Importar BasicNavigator en tiempo de ejecucion para desacoplar el import del modulo
        STEP 2: Instanciar y retornar; la instancia puede tardar 5-20 s en robot real con cartographer
        """
        from nav2_simple_commander.robot_navigator import BasicNavigator
        return BasicNavigator()

    def _wait_nav2_active_sync(self) -> None:
        """
        @TASK: Bloquear el hilo del work_executor hasta que el stack Nav2 este completamente activo
        @INPUT: Sin parametros
        @OUTPUT: Retorno del bloqueo tras confirmacion de Nav2 activo; aplicacion de speed limit
        @CONTEXT: Llamada bloqueante aislada en work_executor durante start() del bridge.
                  En hardware real puede tardar 10-20 s segun estado del costmap global.
        @SECURITY: Sin timeout propio en este metodo; el caller en start() puede cancelar con asyncio.
                   _apply_speed_limit_sync se invoca inmediatamente tras waitUntilNav2Active.

        STEP 1: Invocar waitUntilNav2Active en el navigator (bloquea el hilo hasta que Nav2 responde)
        STEP 2: Aplicar speed limit configurado via _apply_speed_limit_sync como segunda barrera
        """
        if self._navigator is None:
            return
        self._navigator.waitUntilNav2Active()
        self._apply_speed_limit_sync()

    def _apply_speed_limit_sync(self) -> None:
        """
        @TASK: Configurar limite de velocidad en BasicNavigator via setSpeedLimit como segunda barrera
        @INPUT: Sin parametros; usa MAX_LINEAR_VELOCITY del modulo como valor configurado
        @OUTPUT: Navigator configurado con cap de 0.3 m/s; LOGGER.warning si setSpeedLimit no disponible
        @CONTEXT: Llamada en work_executor inmediatamente tras waitUntilNav2Active durante start().
                  Complementa el clamping en el subscriber /cmd_vel como segunda barrera cinematica.
        @SECURITY: Complementa el clamping en /cmd_vel; doble barrera de seguridad independiente.
                   setSpeedLimit no esta disponible en todas las versiones de Nav2; se resuelve con getattr.

        STEP 1: Resolver setSpeedLimit de forma defensiva con getattr; LOGGER.warning si no callable
        STEP 2: Invocar con valor absoluto MAX_LINEAR_VELOCITY y flag de porcentaje en False
        """
        set_speed_limit = getattr(self._navigator, "setSpeedLimit", None)
        if not callable(set_speed_limit):
            LOGGER.warning(
                "[Nav2Bridge] BasicNavigator no expone setSpeedLimit(); "
                "clamping /cmd_vel activo como unica barrera cinematica."
            )
            return
        set_speed_limit(MAX_LINEAR_VELOCITY, False)
        LOGGER.info(
            "[Nav2Bridge] setSpeedLimit aplicado: %.2f m/s.",
            MAX_LINEAR_VELOCITY,
        )

    def _follow_waypoints_and_signal(
        self,
        waypoints: list[NavWaypoint],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """
        @TASK: Ejecutar plan Nav2 multi-waypoint en hilo del work_executor y señalizar completitud
        @INPUT: waypoints — lista de NavWaypoint a ejecutar secuencialmente;
                loop — event loop de asyncio para uso de call_soon_threadsafe al finalizar
        @OUTPUT: NavigationStatus actualizado con resultado; _nav_complete_event seteado via loop
        @CONTEXT: Funcion sincrona bloqueante despachada al work_executor desde navigate_to_waypoints.
                  El sondeo de isTaskComplete ocurre en bucle con sleep de NAV2_TASK_POLL_INTERVAL_S.
        @SECURITY: NavigationStatus se actualiza via _set_nav_result_from_thread usando call_soon_threadsafe
                   para garantizar que la escritura ocurre en el event loop y no desde el hilo concurrente.
                   loop.call_soon_threadsafe es el unico mecanismo hilo-ROS2 → event loop aprobado.

        STEP 1: Construir lista de PoseStamped desde los NavWaypoints del dominio interno
        STEP 2: Invocar followWaypoints en el navigator con la lista de PoseStamped
        STEP 3: Sondear isTaskComplete en bucle con time.sleep(NAV2_TASK_POLL_INTERVAL_S) sin bloquear loop
        STEP 4: Actualizar NavigationStatus via call_soon_threadsafe(_set_nav_result_from_thread)
        STEP 5: Setear _nav_complete_event via call_soon_threadsafe para desbloquear la corrutina en espera
        """
        if self._navigator is None:
            return

        ros_poses = [self._build_pose_stamped(wp) for wp in waypoints]

        self._navigator.followWaypoints(ros_poses)

        LOGGER.info("[Nav2Bridge] Plan Nav2 enviado con %d waypoints.", len(ros_poses))

        succeeded = False
        while True:
            if self._navigator.isTaskComplete():
                result = self._navigator.getResult()
                # Nav2 TaskResult.SUCCEEDED == 1
                succeeded = getattr(result, "value", result) == 1
                break
            time.sleep(NAV2_TASK_POLL_INTERVAL_S)

        loop.call_soon_threadsafe(self._set_nav_result_from_thread, succeeded)

        loop.call_soon_threadsafe(self._nav_complete_event.set)

        LOGGER.info(
            "[Nav2Bridge] Plan Nav2 completado. Resultado: %s.",
            "SUCCEEDED" if succeeded else "FAILED",
        )

    def _send_goal_and_wait_sync(
        self,
        waypoint: NavWaypoint,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """
        @TASK: Enviar un unico goal a Nav2 en el hilo del work_executor y señalizar completitud
        @INPUT: waypoint — NavWaypoint con el destino; loop — event loop para call_soon_threadsafe
        @OUTPUT: NavigationStatus actualizado con resultado; _nav_complete_event seteado via loop
        @CONTEXT: Funcion sincrona bloqueante despachada al work_executor desde send_goal.
                  Usa goToPose si disponible en la version de Nav2; fallback a followWaypoints si no.
                  El sondeo de isTaskComplete ocurre en bucle con sleep; no bloquea el event loop.
        @SECURITY: Toda interaccion con BasicNavigator (goToPose, isTaskComplete, getResult) ocurre
                   exclusivamente en el hilo del work_executor; nunca en el event loop de asyncio.
                   call_soon_threadsafe es el unico mecanismo aprobado para cruzar hilo → event loop.
                   NavigationStatus solo se actualiza via _set_nav_result_from_thread en el event loop.

        STEP 1: Construir PoseStamped desde el NavWaypoint con _build_pose_stamped
        STEP 2: Invocar goToPose si disponible en el navigator; fallback a followWaypoints([pose])
        STEP 3: Sondear isTaskComplete en bucle con time.sleep(NAV2_TASK_POLL_INTERVAL_S)
        STEP 4: Actualizar resultado via call_soon_threadsafe(_set_nav_result_from_thread)
        STEP 5: Setear _nav_complete_event via call_soon_threadsafe para desbloquear send_goal
        """
        if self._navigator is None:
            return
        ros_pose = self._build_pose_stamped(waypoint)
        go_to_pose = getattr(self._navigator, "goToPose", None)
        if callable(go_to_pose):
            go_to_pose(ros_pose)
        else:
            self._navigator.followWaypoints([ros_pose])

        succeeded = False
        while True:
            if self._navigator.isTaskComplete():
                result = self._navigator.getResult()
                succeeded = getattr(result, "value", result) == 1
                break
            time.sleep(NAV2_TASK_POLL_INTERVAL_S)

        loop.call_soon_threadsafe(self._set_nav_result_from_thread, succeeded)
        loop.call_soon_threadsafe(self._nav_complete_event.set)

    def _set_nav_result_from_thread(self, succeeded: bool) -> None:
        """
        @TASK: Actualizar NavigationStatus con el resultado de la tarea Nav2 desde el event loop
        @INPUT: succeeded — resultado booleano de la tarea Nav2 (True = SUCCEEDED, False = FAILED)
        @OUTPUT: _nav_status.task_active = False; _nav_status.last_result_succeeded = succeeded
        @CONTEXT: Funcion despachada al event loop via call_soon_threadsafe desde el hilo de trabajo.
                  Al ejecutarse en el event loop, la escritura es segura sin asyncio.Lock adicional.
        @SECURITY: Nunca llamar directamente desde un hilo concurrente; solo via call_soon_threadsafe.
                   call_soon_threadsafe garantiza que la ejecucion ocurre en el event loop principal.

        STEP 1: Actualizar NavigationStatus sin lock async (ejecuta en el event loop por call_soon_threadsafe)
        """
        self._nav_status.task_active = False
        self._nav_status.last_result_succeeded = succeeded

    def _cancel_nav_sync(self) -> None:
        """
        @TASK: Invocar cancelNav en BasicNavigator de forma sincrona desde el work_executor
        @INPUT: Sin parametros
        @OUTPUT: Plan Nav2 activo cancelado; LOGGER.info confirmando la cancelacion
        @CONTEXT: Aislado en work_executor desde cancel_navigation para no bloquear el event loop.
                  cancelNav puede ser bloqueante en algunas versiones de Nav2 hasta confirmacion.
        @SECURITY: Ignorar silenciosamente si _navigator es None para evitar AttributeError en shutdown.
                   cancelNav se resuelve con getattr defensivamente por compatibilidad de versiones Nav2.

        STEP 1: Verificar que _navigator no es None; resolver cancelNav defensivamente con getattr
        STEP 2: Invocar cancelNav si es callable; registrar en LOGGER.info la confirmacion
        """
        if self._navigator is None:
            return
        cancel_fn = getattr(self._navigator, "cancelNav", None)
        if callable(cancel_fn):
            cancel_fn()
        LOGGER.info("[Nav2Bridge] cancelNav() invocado en BasicNavigator.")

    def _spin_forever(self) -> None:
        """
        @TASK: Ejecutar spin continuo del MultiThreadedExecutor en el hilo daemon del bridge
        @INPUT: Sin parametros
        @OUTPUT: Callbacks ROS 2 del _BridgeNode atendidos continuamente hasta shutdown del executor
        @CONTEXT: Unico hilo que procesa suscripciones DDS y publicaciones del nodo bridge.
                  Lanzado como daemon thread en start(); muere automaticamente con el proceso.
        @SECURITY: Hilo daemon=True; muere automaticamente con el proceso sin requerir join explicito.
                   Nunca llamar a asyncio.run() ni await desde este hilo; es exclusivo para rclpy.

        STEP 1: Entrar al loop de spin del MultiThreadedExecutor; bloquea indefinidamente hasta shutdown
        STEP 2: Capturar cualquier excepcion inesperada y registrar en LOGGER.error para diagnostico
        """
        try:
            self._spin_executor.spin()
        except Exception as exc:
            LOGGER.error("[Nav2Bridge] Excepcion en spin thread: %s — %s", type(exc).__name__, exc)

    def _close_sync(self) -> None:
        """
        @TASK: Liberar recursos ROS 2 del bridge de forma sincrona desde el work_executor
        @INPUT: Sin parametros
        @OUTPUT: _spin_executor detenido; _BridgeNode destruido del grafo ROS 2
        @CONTEXT: Invocado desde close() en el work_executor para garantizar destruccion thread-safe.
                  El work_executor se apaga en close() inmediatamente despues de que esta funcion retorna.
        @SECURITY: No cierra rclpy aqui; main.py es responsable del rclpy.shutdown() global del proceso.
                   timeout_sec=0.2 en shutdown del executor evita bloqueo prolongado en cierre.

        STEP 1: Detener MultiThreadedExecutor con timeout breve para interrumpir el daemon spin thread
        STEP 2: Destruir el nodo ROS 2 interno del bridge para liberar subscripciones DDS
        """
        if self._spin_executor is not None:
            self._spin_executor.shutdown(timeout_sec=0.2)
        if self._node is not None:
            self._node.destroy_node()

    # -----------------------------------------------------------------------
    # Metodos auxiliares de construccion de mensajes
    # -----------------------------------------------------------------------

    def _build_amcl_msg(self, pose_estimate: PoseEstimate) -> PoseWithCovarianceStamped:
        """
        @TASK: Construir PoseWithCovarianceStamped desde un PoseEstimate de vision para correccion AMCL
        @INPUT: pose_estimate — PoseEstimate con rvec (Rodrigues 3x1) y tvec calculados por cv2.solvePnP
        @OUTPUT: Mensaje ROS 2 PoseWithCovarianceStamped listo para publicar en /initialpose
        @CONTEXT: Conversion del espacio de camara del D435i al frame map para actualizacion de AMCL.
                  Solo yaw es confiable para robot planar; roll y pitch se fijan a cero.
        @SECURITY: Covarianzas diagonales grandes (0.15, 0.15, 0.40) previenen divergencia de AMCL
                   en la primera inyeccion cuando la estimacion puede ser imprecisa.

        STEP 1: Poblar header con timestamp del reloj del nodo y frame_id="map"
        STEP 2: Asignar posicion XY desde tvec del marcador; Z fijo a 0.0 (robot planar)
        STEP 3: Extraer yaw desde rvec con _extract_yaw (Rodrigues) y construir quaternion planar
        STEP 4: Asignar covarianza diagonal 6x6 conservadora para convergencia inicial de AMCL
        """
        msg = PoseWithCovarianceStamped()

        if self._node is not None:
            msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.header.frame_id = "map"

        msg.pose.pose.position.x = float(pose_estimate.tvec[0][0])
        msg.pose.pose.position.y = float(pose_estimate.tvec[1][0])
        msg.pose.pose.position.z = 0.0  # robot planar; ignorar Z de la camara

        yaw = self._extract_yaw(pose_estimate.rvec)
        msg.pose.pose.orientation.x = 0.0
        msg.pose.pose.orientation.y = 0.0
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        # covarianza diagonal 6x6 (x, y, z, roll, pitch, yaw)
        msg.pose.covariance = [0.0] * 36
        msg.pose.covariance[0]  = 0.15   # sigma^2 x
        msg.pose.covariance[7]  = 0.15   # sigma^2 y
        msg.pose.covariance[35] = 0.40   # sigma^2 yaw

        return msg

    def _build_pose_stamped(self, waypoint: NavWaypoint) -> Any:
        """
        @TASK: Convertir NavWaypoint del dominio interno a PoseStamped de ROS 2 para Nav2
        @INPUT: waypoint — NavWaypoint con x, y en metros, yaw_rad en radianes y frame_id
        @OUTPUT: geometry_msgs/PoseStamped con timestamp y quaternion, compatible con BasicNavigator
        @CONTEXT: Adaptador interno ejecutado en el work_executor durante _follow_waypoints_and_signal.
                  followWaypoints y goToPose de Nav2 aceptan directamente listas de PoseStamped.
        @SECURITY: Z fijo a 0.0; el robot Unitree G1 opera exclusivamente en el plano horizontal.
                   PoseStamped es un mensaje ROS 2 estandar; la conversion es determinista y sin I/O.

        STEP 1: Instanciar PoseStamped con timestamp del reloj del nodo y frame_id del waypoint
        STEP 2: Asignar posicion XY y orientacion como quaternion planar calculado desde yaw_rad
        """
        from geometry_msgs.msg import PoseStamped

        msg = PoseStamped()

        if self._node is not None:
            msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.header.frame_id = waypoint.frame_id

        msg.pose.position.x = waypoint.x
        msg.pose.position.y = waypoint.y
        msg.pose.position.z = 0.0
        msg.pose.orientation.x = 0.0
        msg.pose.orientation.y = 0.0
        msg.pose.orientation.z = math.sin(waypoint.yaw_rad / 2.0)
        msg.pose.orientation.w = math.cos(waypoint.yaw_rad / 2.0)

        return msg

    @staticmethod
    def _extract_yaw(rvec: Any) -> float:
        """
        @TASK: Extraer angulo yaw desde vector de rotacion Rodrigues (rvec) de OpenCV
        @INPUT: rvec — vector Rodrigues (3x1) producido por cv2.solvePnP con el marcador AprilTag
        @OUTPUT: Angulo yaw en radianes en el plano XY del frame del robot
        @CONTEXT: Paso auxiliar para inyeccion de pose AMCL y construccion de quaternion planar.
                  Aproximacion valida para robot planar; el gimbal lock en pitch=90 no aplica en uso normal.
        @SECURITY: Metodo puro sin efectos secundarios ni acceso a estado del bridge.
                   cv2.Rodrigues es determinista para el mismo rvec de entrada.

        STEP 1: Convertir rvec a matriz de rotacion 3x3 con cv2.Rodrigues
        STEP 2: Extraer yaw como atan2(R[1, 0], R[0, 0]) del plano XY de la matriz
        """
        rotation_matrix, _ = cv2.Rodrigues(rvec)
        return math.atan2(
            float(rotation_matrix[1, 0]),
            float(rotation_matrix[0, 0]),
        )

    # -----------------------------------------------------------------------
    # Utilidades internas
    # -----------------------------------------------------------------------

    def _assert_started(self, caller: str) -> None:
        """
        @TASK: Verificar que el bridge fue iniciado con await start() antes de usar su API publica
        @INPUT: caller — nombre del metodo que realiza la verificacion (para mensaje de error descriptivo)
        @OUTPUT: RuntimeError descriptivo si start() no fue invocado; retorno silencioso si activo
        @CONTEXT: Guard clause invocado al inicio de todos los metodos publicos de navegacion del bridge.
                  Previene el uso del bridge con nodo ROS 2 no inicializado que causaria AttributeError.
        @SECURITY: El mensaje de RuntimeError incluye el nombre del metodo caller para diagnostico rapido.
                   No suprime el error; se propaga al TourOrchestrator para manejo en la FSM.

        STEP 1: Lanzar RuntimeError descriptivo si _started es False; retornar silenciosamente si True
        """
        if not self._started:
            raise RuntimeError(
                f"AsyncNav2Bridge.{caller}() invocado antes de await start(). "
                "Llamar start() desde el event loop principal primero."
            )


# ---------------------------------------------------------------------------
# Exportaciones
# ---------------------------------------------------------------------------

__all__ = [
    "AsyncNav2Bridge",
    "MAX_LINEAR_VELOCITY",
    "MAX_ANGULAR_VELOCITY",
    "NavWaypoint",
    "NavigationStatus",
]
