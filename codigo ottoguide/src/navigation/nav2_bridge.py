from __future__ import annotations

# @TASK: Proveer interfaz asincrona entre TourOrchestrator y el stack ROS 2 Nav2
# @INPUT: BasicNavigator de nav2_simple_commander; PoseEstimate de VisionProcessor
# @OUTPUT: Corrutinas consumibles por TourOrchestrator sin bloqueo del event loop
# @CONTEXT: Capa de bridge HIL Fase 3; unico punto de acceso async a Nav2
# STEP 1: Instanciar BasicNavigator y nodo ROS 2 en contexto de hilo aislado
# STEP 2: Mantener spin del executor en daemon thread sin tocar el event loop
# STEP 3: Exponer navegacion, inyeccion AMCL y estado via primitivas asyncio
# STEP 4: Aplicar clamping cinematico estricto 0.3 m/s en cmd_vel interceptado
# @SECURITY: Ninguna llamada bloqueante de ROS 2 se ejecuta en el event loop principal
# @AI_CONTEXT: Concebido como reemplazo/superconjunto de NavigationManager para HIL Fase 3

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

# @TASK: Declarar limites cinematicos de seguridad para el bridge Nav2
# @INPUT: Ninguno
# @OUTPUT: Constantes MAX_LINEAR_VELOCITY y MAX_ANGULAR_VELOCITY de referencia
# @CONTEXT: Restriccion fisica impuesta por friccion del suelo en el Unitree G1 EDU
# STEP 1: Definir cap lineal de 0.3 m/s documentado en el manual de hardware
# STEP 2: Definir cap angular conservador para giros en entornos indoor
# @SECURITY: Estos valores son la unica fuente de verdad de limite cinematico del bridge
# @AI_CONTEXT: MAX_LINEAR_VELOCITY debe coincidir con RobotHardwareAPI.MAX_LINEAR_VELOCITY
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
    # @TASK: Representar un waypoint de navegacion en el frame del mapa
    # @INPUT: Coordenadas x, y en metros y yaw en radianes; frame opcional
    # @OUTPUT: Estructura inmutable consumible por AsyncNav2Bridge
    # @CONTEXT: Dominio interno del bridge; independiente de PoseStamped ROS
    # STEP 1: Capturar posicion 2D y orientacion yaw del plan de ruta
    # STEP 2: Permitir override del frame_id para casos multi-mapa
    # @SECURITY: Frozen evita mutacion accidental durante la ejecucion del plan
    # @AI_CONTEXT: Equivalente a Waypoint en NavigationManager; separado por clean architecture
    x: float
    y: float
    yaw_rad: float
    frame_id: str = "map"


@dataclass(slots=True)
class NavigationStatus:
    # @TASK: Encapsular el estado observable de la tarea de navegacion activa
    # @INPUT: Indicadores de tarea activa, completitud y ultimo resultado Nav2
    # @OUTPUT: Snapshot inmutable del estado consumible desde el event loop
    # @CONTEXT: Estado compartido entre el hilo ROS 2 y las corrutinas async
    # STEP 1: Registrar si hay una tarea Nav2 activa en este momento
    # STEP 2: Mantener resultado del ultimo plan ejecutado para observabilidad
    # @SECURITY: Acceso protegido por asyncio.Lock en el bridge
    # @AI_CONTEXT: No usar directamente; consultado via propiedades del bridge
    task_active: bool = False
    last_result_succeeded: Optional[bool] = None
    active_waypoint_index: int = 0


# ---------------------------------------------------------------------------
# Nodo ROS 2 interno del bridge
# ---------------------------------------------------------------------------

class _BridgeNode(Node):
    # @TASK: Proveer nodo ROS 2 dedicado para publicaciones y suscripciones del bridge
    # @INPUT: Nombre del nodo; topicos de AMCL y cmd_vel
    # @OUTPUT: Publisher /initialpose, subscriber /cmd_vel, publisher /cmd_vel_nav
    # @CONTEXT: Nodo interno que vive en el hilo de spin; nunca expuesto al event loop
    # STEP 1: Crear publishers para AMCL y cmd_vel filtrado
    # STEP 2: Crear subscriber en /cmd_vel para interceptacion y clamping
    # STEP 3: Almacenar referencia al callback de clamping inyectado externamente
    # @SECURITY: El subscriber /cmd_vel intercepta y reemplaza; no republica sin clamp
    # @AI_CONTEXT: La instancia es propiedad exclusiva de AsyncNav2Bridge

    def __init__(
        self,
        node_name: str,
        *,
        on_cmd_vel: Callable[[Twist], None],
    ) -> None:
        # @TASK: Inicializar nodo bridge con publishers y subscriber
        # @INPUT: node_name, on_cmd_vel como callback de clamping
        # @OUTPUT: Nodo ROS 2 activo con topicos configurados
        # @CONTEXT: Constructor invocado desde el hilo de spin antes de spin()
        # STEP 1: Llamar super().__init__ con el nombre de nodo asignado
        # STEP 2: Registrar publisher AMCL con QoS compatible con nav2
        # STEP 3: Registrar subscriber /cmd_vel para interceptar velocidades
        # STEP 4: Registrar publisher cmd_vel filtrado para reinyectar post-clamp
        # @SECURITY: QoS depth=1 en AMCL evita acumulacion de correcciones obsoletas
        # @AI_CONTEXT: on_cmd_vel es el metodo _clamp_and_republish del bridge
        super().__init__(node_name)  # STEP 1

        # STEP 2: Publisher /initialpose para correccion AMCL
        self._amcl_publisher = self.create_publisher(
            PoseWithCovarianceStamped,
            AMCL_TOPIC,
            1,
        )

        # STEP 4: Publisher cmd_vel filtrado (post-clamp)
        self._cmd_vel_pub = self.create_publisher(
            Twist,
            CMD_VEL_FILTERED_TOPIC,
            10,
        )

        # STEP 3: Subscriber /cmd_vel — intercepta velocidades de Nav2
        self._cmd_vel_sub = self.create_subscription(
            Twist,
            CMD_VEL_TOPIC,
            on_cmd_vel,
            10,
        )

    def publish_initial_pose(self, msg: PoseWithCovarianceStamped) -> None:
        # @TASK: Publicar correccion de pose en /initialpose
        # @INPUT: msg — mensaje PoseWithCovarianceStamped ya construido
        # @OUTPUT: Mensaje publicado en el topico AMCL
        # @CONTEXT: Invocado desde hilo de trabajo del executor bridge
        # STEP 1: Llamar publisher.publish directamente; llamada thread-safe en rclpy
        # @SECURITY: Sin transformacion adicional; el caller es responsable del contenido
        # @AI_CONTEXT: Debe invocarse solo desde el trabajo encolado en _work_executor
        self._amcl_publisher.publish(msg)  # STEP 1

    def publish_clamped_cmd_vel(self, msg: Twist) -> None:
        # @TASK: Republicar twist clampeado en topico cmd_vel filtrado
        # @INPUT: msg — Twist ya saturado por _clamp_twist
        # @OUTPUT: Mensaje publicado en CMD_VEL_FILTERED_TOPIC
        # @CONTEXT: Resultado de la interceptacion en el subscriber /cmd_vel
        # STEP 1: Publicar el twist ya clampeado en el topico de salida filtrado
        # @SECURITY: Garantiza que ningun comando linear supera MAX_LINEAR_VELOCITY
        # @AI_CONTEXT: El controlador de bajo nivel debe suscribirse a CMD_VEL_FILTERED_TOPIC
        self._cmd_vel_pub.publish(msg)  # STEP 1


# ---------------------------------------------------------------------------
# Bridge principal
# ---------------------------------------------------------------------------

class AsyncNav2Bridge:
    # @TASK: Encapsular el stack Nav2 como interfaz asincrona no bloqueante
    # @INPUT: Parametros de configuracion de red ROS 2 y cinematica
    # @OUTPUT: API async para navegacion, inyeccion AMCL y consulta de estado
    # @CONTEXT: Componente central de navegacion HIL Fase 3
    # STEP 1: Inicializar en dos fases — __init__ (sync ligero) + async start()
    # STEP 2: Lanzar spin en daemon thread aislado del event loop principal
    # STEP 3: Sincronizar estado hilo-ROS2/corrutinas via asyncio.Event y asyncio.Lock
    # @SECURITY: Toda llamada bloqueante de Nav2 se ejecuta en _work_executor
    # @AI_CONTEXT: El caller (main.py o NavigationManager) debe invocar await start() antes de usar

    def __init__(
        self,
        *,
        node_name: str = "async_nav2_bridge",
        work_executor_workers: int = 1,
    ) -> None:
        # @TASK: Construir estado interno del bridge sin inicializar ROS 2
        # @INPUT: node_name para el nodo ROS 2; worker count del executor de trabajo
        # @OUTPUT: Bridge en estado PRE-INIT; inutilizable hasta llamar await start()
        # @CONTEXT: Separacion init/start para compatibilidad con injection en main.py
        # STEP 1: Inicializar primitivas asyncio que sincronizaran hilo-ROS2/loop
        # STEP 2: Crear executor de trabajo para Nav2 calls bloqueantes
        # STEP 3: Marcar estado como no iniciado; start() completara la inicializacion
        # @SECURITY: No toca rclpy en __init__; evita condiciones de orden en bootstrap
        # @AI_CONTEXT: asyncio.Event y asyncio.Lock se crean aqui; bind al loop en start()

        if work_executor_workers <= 0:
            raise ValueError("work_executor_workers debe ser mayor que 0.")

        self._node_name: str = node_name
        self._started: bool = False

        # STEP 1: Primitivas de sincronizacion entre hilo ROS 2 y event loop
        self._nav_complete_event: asyncio.Event = asyncio.Event()
        self._status_lock: asyncio.Lock = asyncio.Lock()
        self._nav_status: NavigationStatus = NavigationStatus()

        # STEP 2: Executor de trabajo para llamadas bloqueantes a BasicNavigator
        self._work_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=work_executor_workers,
            thread_name_prefix="nav2-bridge-work",
        )

        # STEP 3: Referencias que se completan en start()
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
        # @TASK: Inicializar ROS 2, nodo, BasicNavigator y lanzar spin daemon
        # @INPUT: Sin parametros; usa configuracion del constructor
        # @OUTPUT: Bridge en estado ACTIVO; corrutinas de navegacion disponibles
        # @CONTEXT: Debe invocarse una sola vez desde el event loop principal (main.py)
        # STEP 1: Capturar referencia al event loop activo para uso thread-safe posterior
        # STEP 2: Inicializar rclpy si aun no ha sido inicializado
        # STEP 3: Instanciar _BridgeNode con callback de clamping cmd_vel
        # STEP 4: Instanciar BasicNavigator en el executor (puede bloquear algunos segundos)
        # STEP 5: Configurar MultiThreadedExecutor con el nodo y lanzar daemon thread
        # STEP 6: Esperar a que Nav2 este activo (waitUntilNav2Active en executor)
        # @SECURITY: waitUntilNav2Active bloqueante se aisla en executor para no colgar el loop
        # @AI_CONTEXT: El spin thread es daemon=True; muere con el proceso principal automaticamente

        if self._started:
            LOGGER.warning("[Nav2Bridge] start() llamado mas de una vez; ignorado.")
            return

        # STEP 1
        self._loop = asyncio.get_running_loop()

        # STEP 2
        if not rclpy.ok():
            rclpy.init(args=None)

        # STEP 3
        self._node = _BridgeNode(
            self._node_name,
            on_cmd_vel=self._clamp_and_republish,
        )

        # STEP 4: BasicNavigator instanciado en hilo dedicado
        LOGGER.info("[Nav2Bridge] Instanciando BasicNavigator en executor...")
        self._navigator = await self._loop.run_in_executor(
            self._work_executor,
            self._create_navigator_sync,
        )

        # STEP 5: Spin executor y daemon thread
        self._spin_executor = MultiThreadedExecutor(num_threads=2)
        self._spin_executor.add_node(self._node)
        self._spin_thread = threading.Thread(
            target=self._spin_forever,
            name="nav2-bridge-spin",
            daemon=True,
        )
        self._spin_thread.start()
        LOGGER.info("[Nav2Bridge] Spin daemon thread iniciado.")

        # STEP 6: Esperar activacion de Nav2 (bloqueante, aislado en executor)
        LOGGER.info("[Nav2Bridge] Esperando activacion de Nav2...")
        await self._loop.run_in_executor(
            self._work_executor,
            self._wait_nav2_active_sync,
        )

        self._started = True
        LOGGER.info("[Nav2Bridge] Bridge activo. Nav2 disponible.")

    async def close(self) -> None:
        # @TASK: Detener spin executor, destruir nodo y liberar executor de trabajo
        # @INPUT: Sin parametros
        # @OUTPUT: Todos los recursos ROS 2 y threads liberados ordenadamente
        # @CONTEXT: Invocado desde _graceful_shutdown en main.py
        # STEP 1: Detener MultiThreadedExecutor para interrumpir el spin daemon
        # STEP 2: Destruir el nodo ROS 2 interno del bridge
        # STEP 3: Apagar executor de trabajo cancelando futures pendientes
        # @SECURITY: Debe invocarse antes de rclpy.shutdown() del proceso principal
        # @AI_CONTEXT: El spin thread termina solo al cerrarse _spin_executor

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
        # @TASK: Enviar un plan de ruta a Nav2 y esperar su completitud de forma asincrona
        # @INPUT: waypoints — secuencia de NavWaypoint en frame map
        # @OUTPUT: True si el plan completo fue ejecutado con exito; False en fallo
        # @CONTEXT: Unica via de navegacion desde TourOrchestrator en Fase 3 HIL
        # STEP 1: Validar precondiciones (bridge activo, waypoints no vacios)
        # STEP 2: Marcar tarea activa y resetear evento de completitud
        # STEP 3: Despachar plan a BasicNavigator en el executor de trabajo
        # STEP 4: Esperar _nav_complete_event seteado por el hilo de sondeo
        # STEP 5: Leer resultado bajo lock y retornar
        # @SECURITY: asyncio.Lock protege NavigationStatus ante race conditions
        # @AI_CONTEXT: isTaskComplete() se sondea en executor; nunca en el event loop

        self._assert_started("navigate_to_waypoints")

        if not waypoints:
            LOGGER.warning("[Nav2Bridge] navigate_to_waypoints recibio lista vacia.")
            return True

        # STEP 2
        async with self._status_lock:
            self._nav_status.task_active = True
            self._nav_status.last_result_succeeded = None
            self._nav_status.active_waypoint_index = 0
        self._nav_complete_event.clear()

        # STEP 3 + 4: despachar y esperar
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._work_executor,
            self._follow_waypoints_and_signal,
            list(waypoints),
            loop,
        )

        # Esperar en el event loop hasta que el hilo de trabajo sete el evento
        await self._nav_complete_event.wait()

        # STEP 5
        async with self._status_lock:
            result = self._nav_status.last_result_succeeded
        return result is True

    async def cancel_navigation(self) -> None:
        # @TASK: Cancelar la tarea de navegacion activa en BasicNavigator
        # @INPUT: Sin parametros
        # @OUTPUT: Plan Nav2 cancelado; estado interno reseteado
        # @CONTEXT: Invocado por TourOrchestrator al entrar a EMERGENCY o INTERACTING
        # STEP 1: Verificar que hay una tarea activa antes de cancelar
        # STEP 2: Invocar cancelNav en executor para no bloquear el loop
        # STEP 3: Resetear estado de navegacion y setear evento de completitud
        # @SECURITY: Evita comandos de cancelacion redundantes sin tarea activa
        # @AI_CONTEXT: La cancelacion forza el retorno de navigate_to_waypoints

        self._assert_started("cancel_navigation")

        async with self._status_lock:
            if not self._nav_status.task_active:
                return

        loop = asyncio.get_running_loop()

        # STEP 2
        await loop.run_in_executor(
            self._work_executor,
            self._cancel_nav_sync,
        )

        # STEP 3
        async with self._status_lock:
            self._nav_status.task_active = False
            self._nav_status.last_result_succeeded = False

        if not self._nav_complete_event.is_set():
            self._nav_complete_event.set()

        LOGGER.info("[Nav2Bridge] Navegacion cancelada por solicitud del orquestador.")

    async def is_navigation_active(self) -> bool:
        # @TASK: Consultar si hay una tarea de navegacion activa
        # @INPUT: Sin parametros
        # @OUTPUT: True si Nav2 esta ejecutando un plan actualmente
        # @CONTEXT: Metodo de sondeo para TourOrchestrator o layer de observabilidad
        # STEP 1: Adquirir lock y leer campo task_active del status
        # @SECURITY: Lock garantiza lectura consistente sin race condition
        # @AI_CONTEXT: Utilizar en lugar de sondear isTaskComplete() directamente
        async with self._status_lock:  # STEP 1
            return self._nav_status.task_active

    # -----------------------------------------------------------------------
    # Inyeccion de odometria absoluta (AprilTag -> AMCL)
    # -----------------------------------------------------------------------

    async def inject_absolute_pose(self, pose_estimate: PoseEstimate) -> None:
        # @TASK: Publicar correccion de pose absoluta en /initialpose para AMCL
        # @INPUT: pose_estimate — PoseEstimate con rvec/tvec calculado por VisionProcessor
        # @OUTPUT: Mensaje PoseWithCovarianceStamped publicado en /initialpose
        # @CONTEXT: Correccion de deriva odometrica mediante AprilTag tag36h11
        # STEP 1: Construir PoseWithCovarianceStamped desde rvec/tvec de la estimacion
        # STEP 2: Extraer yaw desde rvec usando rodrigues y atan2(R[1,0], R[0,0])
        # STEP 3: Asignar covarianza conservadora para convergencia estable de AMCL
        # STEP 4: Invocar publish en el hilo de trabajo para no bloquear el loop
        # @SECURITY: La frecuencia de inyeccion debe ser controlada por el caller
        # @AI_CONTEXT: Covarianzas diagonales (0.15, 0.15, 0.40) son valores de inicio HIL

        self._assert_started("inject_absolute_pose")

        # STEP 1 + 2 + 3: construccion del mensaje (sync, CPU ligero)
        msg = self._build_amcl_msg(pose_estimate)

        # STEP 4: publicar en executor para que el nodo este en su hilo
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
        # @TASK: Interceptar Twist de Nav2 y republicar con velocidades saturadas
        # @INPUT: msg — Twist recibido en /cmd_vel desde el planificador Nav2
        # @OUTPUT: Twist clampeado publicado en CMD_VEL_FILTERED_TOPIC
        # @CONTEXT: Callback del subscriber /cmd_vel; ejecutado en el hilo de spin ROS 2
        # STEP 1: Clonar el mensaje para no mutar el original recibido
        # STEP 2: Saturar componentes lineales x e y a MAX_LINEAR_VELOCITY
        # STEP 3: Verificar norma vectorial xy y reescalar si excede el limite
        # STEP 4: Saturar componente angular z a MAX_ANGULAR_VELOCITY
        # STEP 5: Publicar el mensaje clampeado en el topico filtrado
        # @SECURITY: Ninguna modificacion en caliente del msg original del planificador
        # @AI_CONTEXT: Este callback corre en hilo spin; no acceder a primitivas asyncio aqui

        # STEP 1
        clamped = Twist()
        clamped.angular.x = msg.angular.x
        clamped.angular.y = msg.angular.y

        # STEP 2: saturar por componente
        lx = max(-MAX_LINEAR_VELOCITY, min(MAX_LINEAR_VELOCITY, msg.linear.x))
        ly = max(-MAX_LINEAR_VELOCITY, min(MAX_LINEAR_VELOCITY, msg.linear.y))

        # STEP 3: reescalar si la norma vectorial supera el limite
        norm = (lx * lx + ly * ly) ** 0.5
        if norm > MAX_LINEAR_VELOCITY and norm > 0.0:
            scale = MAX_LINEAR_VELOCITY / norm
            lx *= scale
            ly *= scale

        clamped.linear.x = lx
        clamped.linear.y = ly
        clamped.linear.z = 0.0  # movimiento planar unicamente

        # STEP 4: saturar angulo z
        clamped.angular.z = max(
            -MAX_ANGULAR_VELOCITY,
            min(MAX_ANGULAR_VELOCITY, msg.angular.z),
        )

        # STEP 5
        if self._node is not None:
            self._node.publish_clamped_cmd_vel(clamped)

    # -----------------------------------------------------------------------
    # Metodos sincronos para executor
    # -----------------------------------------------------------------------

    @staticmethod
    def _create_navigator_sync() -> "BasicNavigator":
        # @TASK: Instanciar BasicNavigator dentro del executor de trabajo
        # @INPUT: Sin parametros
        # @OUTPUT: Instancia de BasicNavigator lista para comandos Nav2
        # @CONTEXT: Llamada en hilo del work_executor durante start()
        # STEP 1: Importar BasicNavigator en tiempo de ejecucion para desacoplar import
        # STEP 2: Retornar instancia; puede tardar varios segundos en robot real
        # @SECURITY: Import dinamico evita ImportError en entornos de desarrollo sin Nav2
        # @AI_CONTEXT: BasicNavigator crea internamente un nodo hijo; no interferir con _node
        from nav2_simple_commander.robot_navigator import BasicNavigator  # STEP 1
        return BasicNavigator()  # STEP 2

    def _wait_nav2_active_sync(self) -> None:
        # @TASK: Bloquear hasta que el stack Nav2 este completamente activo
        # @INPUT: Sin parametros
        # @OUTPUT: Retorno tras confirmacion de Nav2 activo
        # @CONTEXT: Llamada bloqueante aislada en work_executor durante start()
        # STEP 1: Invocar waitUntilNav2Active en el navigator (bloquea hasta ready)
        # STEP 2: Aplicar setSpeedLimit si la API esta disponible
        # @SECURITY: Sin timeout propio; el caller en start() puede cancelar con asyncio
        # @AI_CONTEXT: En hardware real puede tardar 10-20 s segun estado del costmap
        if self._navigator is None:
            return
        self._navigator.waitUntilNav2Active()  # STEP 1
        self._apply_speed_limit_sync()          # STEP 2

    def _apply_speed_limit_sync(self) -> None:
        # @TASK: Configurar limite de velocidad en BasicNavigator via setSpeedLimit
        # @INPUT: Sin parametros; usa MAX_LINEAR_VELOCITY del modulo
        # @OUTPUT: Navigator configurado con cap de 0.3 m/s o warning si no soporta
        # @CONTEXT: Llamada en executor tras waitUntilNav2Active en start()
        # STEP 1: Resolver setSpeedLimit de forma defensiva con getattr
        # STEP 2: Invocar con limite absoluto y flag de porcentaje en False
        # @SECURITY: Complementa el clamping en /cmd_vel; doble barrera de seguridad
        # @AI_CONTEXT: setSpeedLimit no esta disponible en todas las versiones de Nav2
        set_speed_limit = getattr(self._navigator, "setSpeedLimit", None)
        if not callable(set_speed_limit):  # STEP 1
            LOGGER.warning(
                "[Nav2Bridge] BasicNavigator no expone setSpeedLimit(); "
                "clamping /cmd_vel activo como unica barrera cinematica."
            )
            return
        set_speed_limit(MAX_LINEAR_VELOCITY, False)  # STEP 2
        LOGGER.info(
            "[Nav2Bridge] setSpeedLimit aplicado: %.2f m/s.",
            MAX_LINEAR_VELOCITY,
        )

    def _follow_waypoints_and_signal(
        self,
        waypoints: list[NavWaypoint],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        # @TASK: Ejecutar plan Nav2 y setear nav_complete_event al terminar
        # @INPUT: waypoints — lista de NavWaypoint; loop — event loop para call_soon_threadsafe
        # @OUTPUT: NavigationStatus actualizado; nav_complete_event seteado
        # @CONTEXT: Funcion bloqueante despachada al work_executor desde navigate_to_waypoints
        # STEP 1: Construir lista de PoseStamped desde los NavWaypoints del dominio
        # STEP 2: Invocar followWaypoints en el navigator
        # STEP 3: Sondear isTaskComplete en bucle con sleep corto (no bloquea loop)
        # STEP 4: Leer resultado y actualizar NavigationStatus via thread-safe setter
        # STEP 5: Setear nav_complete_event desde el hilo usando call_soon_threadsafe
        # @SECURITY: NavigationStatus se actualiza via _set_status_from_thread (lock-free thread-safe)
        # @AI_CONTEXT: loop.call_soon_threadsafe es el unico mecanismo hilo->loop correcto

        if self._navigator is None:
            return

        # STEP 1
        ros_poses = [self._build_pose_stamped(wp) for wp in waypoints]

        # STEP 2
        self._navigator.followWaypoints(ros_poses)

        LOGGER.info("[Nav2Bridge] Plan Nav2 enviado con %d waypoints.", len(ros_poses))

        # STEP 3: sondeo en hilo de trabajo
        succeeded = False
        while True:
            if self._navigator.isTaskComplete():
                result = self._navigator.getResult()
                # Nav2 TaskResult.SUCCEEDED == 1
                succeeded = getattr(result, "value", result) == 1
                break
            time.sleep(NAV2_TASK_POLL_INTERVAL_S)

        # STEP 4: actualizar estado via call_soon_threadsafe
        loop.call_soon_threadsafe(self._set_nav_result_from_thread, succeeded)

        # STEP 5: desbloquear la corrutina en espera
        loop.call_soon_threadsafe(self._nav_complete_event.set)

        LOGGER.info(
            "[Nav2Bridge] Plan Nav2 completado. Resultado: %s.",
            "SUCCEEDED" if succeeded else "FAILED",
        )

    def _set_nav_result_from_thread(self, succeeded: bool) -> None:
        # @TASK: Actualizar NavigationStatus desde el hilo de trabajo
        # @INPUT: succeeded — resultado booleano de la tarea Nav2
        # @OUTPUT: _nav_status actualizado con task_active=False y resultado
        # @CONTEXT: Llamado via call_soon_threadsafe desde _follow_waypoints_and_signal
        # STEP 1: Actualizar NavigationStatus sin lock async (se ejecuta en el event loop)
        # @SECURITY: call_soon_threadsafe garantiza que se ejecuta en el event loop; es thread-safe
        # @AI_CONTEXT: Nunca llamar directamente desde un hilo; solo via call_soon_threadsafe
        self._nav_status.task_active = False              # STEP 1
        self._nav_status.last_result_succeeded = succeeded

    def _cancel_nav_sync(self) -> None:
        # @TASK: Invocar cancelNav en BasicNavigator de forma sincrona
        # @INPUT: Sin parametros
        # @OUTPUT: Plan Nav2 cancelado
        # @CONTEXT: Aislado en executor desde cancel_navigation
        # STEP 1: Llamar cancelNav si el navigator esta disponible
        # @SECURITY: Ignorar si navigator es None para evitar AttributeError en shutdown
        # @AI_CONTEXT: cancelNav es bloqueante en algunas versiones de Nav2
        if self._navigator is None:
            return
        cancel_fn = getattr(self._navigator, "cancelNav", None)
        if callable(cancel_fn):
            cancel_fn()  # STEP 1
        LOGGER.info("[Nav2Bridge] cancelNav() invocado en BasicNavigator.")

    def _spin_forever(self) -> None:
        # @TASK: Ejecutar spin continuo del MultiThreadedExecutor en hilo daemon
        # @INPUT: Sin parametros
        # @OUTPUT: Callbacks ROS 2 atendidos hasta que _spin_executor se detenga
        # @CONTEXT: Unico hilo que procesa suscripciones y publicaciones del nodo bridge
        # STEP 1: Entrar al loop de spin del executor; bloquea indefinidamente
        # STEP 2: Capturar cualquier excepcion y registrar para diagnostico
        # @SECURITY: Hilo daemon; muere automaticamente con el proceso sin bloquear shutdown
        # @AI_CONTEXT: Nunca llamar a asyncio.run o await desde este hilo
        try:
            self._spin_executor.spin()  # STEP 1
        except Exception as exc:        # STEP 2
            LOGGER.error("[Nav2Bridge] Excepcion en spin thread: %s — %s", type(exc).__name__, exc)

    def _close_sync(self) -> None:
        # @TASK: Liberar recursos ROS 2 de forma sincrona desde el executor
        # @INPUT: Sin parametros
        # @OUTPUT: _spin_executor detenido; nodo destruido
        # @CONTEXT: Invocado desde close() en el work_executor
        # STEP 1: Detener MultiThreadedExecutor (interrumpe el daemon spin thread)
        # STEP 2: Destruir el nodo ROS 2 interno del bridge
        # @SECURITY: No cierra rclpy aqui; main.py es responsable del rclpy.shutdown global
        # @AI_CONTEXT: work_executor se apaga en close() despues de que esta funcion retorna
        if self._spin_executor is not None:
            self._spin_executor.shutdown(timeout_sec=0.2)  # STEP 1
        if self._node is not None:
            self._node.destroy_node()                       # STEP 2

    # -----------------------------------------------------------------------
    # Metodos auxiliares de construccion de mensajes
    # -----------------------------------------------------------------------

    def _build_amcl_msg(self, pose_estimate: PoseEstimate) -> PoseWithCovarianceStamped:
        # @TASK: Construir PoseWithCovarianceStamped desde un PoseEstimate de vision
        # @INPUT: pose_estimate — rvec y tvec calculados por VisionProcessor con solvePnP
        # @OUTPUT: Mensaje ROS 2 listo para publicar en /initialpose
        # @CONTEXT: Conversion de espacio de camara a frame map para AMCL
        # STEP 1: Poblar header con timestamp actual del nodo y frame_id map
        # STEP 2: Asignar posicion XYZ desde tvec del marcador detectado
        # STEP 3: Extraer yaw desde rvec con Rodrigues y construir quaternion planar
        # STEP 4: Asignar covarianza diagonal conservadora para convergencia inicial
        # @SECURITY: Covarianzas grandes (0.15, 0.15, 0.40) previenen divergencia de AMCL
        # @AI_CONTEXT: Solo yaw es confiable para robot planar; roll/pitch se fijan a cero

        msg = PoseWithCovarianceStamped()

        # STEP 1
        if self._node is not None:
            msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.header.frame_id = "map"

        # STEP 2
        msg.pose.pose.position.x = float(pose_estimate.tvec[0][0])
        msg.pose.pose.position.y = float(pose_estimate.tvec[1][0])
        msg.pose.pose.position.z = 0.0  # robot planar; ignorar Z de la camara

        # STEP 3
        yaw = self._extract_yaw(pose_estimate.rvec)
        msg.pose.pose.orientation.x = 0.0
        msg.pose.pose.orientation.y = 0.0
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        # STEP 4: covarianza diagonal 6x6 (x, y, z, roll, pitch, yaw)
        msg.pose.covariance = [0.0] * 36
        msg.pose.covariance[0]  = 0.15   # sigma^2 x
        msg.pose.covariance[7]  = 0.15   # sigma^2 y
        msg.pose.covariance[35] = 0.40   # sigma^2 yaw

        return msg

    def _build_pose_stamped(self, waypoint: NavWaypoint) -> Any:
        # @TASK: Convertir NavWaypoint del dominio a PoseStamped de ROS 2
        # @INPUT: waypoint — NavWaypoint con x, y, yaw_rad y frame_id
        # @OUTPUT: geometry_msgs/PoseStamped compatible con BasicNavigator.followWaypoints
        # @CONTEXT: Adaptador interno ejecutado en el work_executor
        # STEP 1: Instanciar PoseStamped con timestamp del nodo y frame_id del waypoint
        # STEP 2: Asignar posicion XY y orientacion como quaternion planar desde yaw
        # @SECURITY: Z fijo a 0.0; robot opera en plano horizontal
        # @AI_CONTEXT: followWaypoints acepta lista de PoseStamped directamente
        from geometry_msgs.msg import PoseStamped

        msg = PoseStamped()

        # STEP 1
        if self._node is not None:
            msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.header.frame_id = waypoint.frame_id

        # STEP 2
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
        # @TASK: Extraer angulo yaw desde vector de rotacion rvec de OpenCV
        # @INPUT: rvec — vector Rodrigues (3x1) producido por cv2.solvePnP
        # @OUTPUT: Angulo yaw en radianes en el plano XY
        # @CONTEXT: Paso auxiliar para inyeccion de pose AMCL y construccion quaternion
        # STEP 1: Convertir rvec a matriz de rotacion 3x3 con cv2.Rodrigues
        # STEP 2: Extraer yaw como atan2(R[1, 0], R[0, 0]) del plano XY
        # @SECURITY: Metodo puro sin efectos secundarios
        # @AI_CONTEXT: Aproximacion valida para robot planar; ignora gimbal lock en pitch=90
        rotation_matrix, _ = cv2.Rodrigues(rvec)    # STEP 1
        return math.atan2(                           # STEP 2
            float(rotation_matrix[1, 0]),
            float(rotation_matrix[0, 0]),
        )

    # -----------------------------------------------------------------------
    # Utilidades internas
    # -----------------------------------------------------------------------

    def _assert_started(self, caller: str) -> None:
        # @TASK: Verificar que el bridge fue iniciado antes de usar su API
        # @INPUT: caller — nombre del metodo que realiza la verificacion
        # @OUTPUT: RuntimeError si start() no fue invocado previamente
        # @CONTEXT: Guard clause para todos los metodos publicos de navegacion
        # STEP 1: Lanzar RuntimeError descriptivo si _started es False
        # @SECURITY: Previene uso del bridge con nodo ROS 2 no inicializado
        # @AI_CONTEXT: El mensaje incluye el metodo caller para facilitar debugging
        if not self._started:  # STEP 1
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
