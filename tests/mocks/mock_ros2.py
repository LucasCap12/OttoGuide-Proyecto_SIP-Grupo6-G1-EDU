from __future__ import annotations

import threading
import time
import types
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


_RCLPY_OK: bool = False


def init(args: Optional[List[str]] = None) -> None:
    # @TASK: Inicializar rclpy mock
    # @INPUT: args
    # @OUTPUT: Estado global rclpy en activo
    # @CONTEXT: Stub de rclpy.init para pruebas sin ROS2 nativo
    # STEP 1: Ignorar args de inicializacion
    # STEP 2: Marcar estado de contexto como activo
    # @SECURITY: No accede a red DDS real
    # @AI_CONTEXT: Replica minima del comportamiento de rclpy.init
    del args
    global _RCLPY_OK
    _RCLPY_OK = True


def shutdown() -> None:
    # @TASK: Apagar rclpy mock
    # @INPUT: Sin parametros
    # @OUTPUT: Estado global rclpy inactivo
    # @CONTEXT: Stub de rclpy.shutdown para cierre de tests
    # STEP 1: Marcar contexto como inactivo
    # STEP 2: Finalizar ejecuciones bloqueantes de spin
    # @SECURITY: Evita recursos colgantes en pipeline CI
    # @AI_CONTEXT: Coordina salida de MockExecutor.spin
    global _RCLPY_OK
    _RCLPY_OK = False


def ok() -> bool:
    # @TASK: Consultar estado rclpy
    # @INPUT: Sin parametros
    # @OUTPUT: Booleano de contexto activo
    # @CONTEXT: Stub de rclpy.ok para control de ciclo de vida
    # STEP 1: Leer flag global
    # STEP 2: Retornar resultado al consumidor
    # @SECURITY: Funcion pura sin efectos secundarios
    # @AI_CONTEXT: Compatibilidad con componentes que validan ok()
    return _RCLPY_OK


def spin(node: "MockNode") -> None:
    # @TASK: Simular rclpy.spin
    # @INPUT: node
    # @OUTPUT: Loop bloqueante cooperativo hasta shutdown
    # @CONTEXT: Stub de spin para pruebas de concurrencia
    # STEP 1: Iterar mientras rclpy permanezca activo
    # STEP 2: Dormir intervalos cortos para ceder CPU
    # @SECURITY: Evita bloqueos infinitos durante tests
    # @AI_CONTEXT: Emula spin basico sin callbacks DDS reales
    del node
    while _RCLPY_OK:
        time.sleep(0.01)


class MockPublisher:
    def __init__(self) -> None:
        # @TASK: Inicializar publisher mock
        # @INPUT: Sin parametros
        # @OUTPUT: Buffer de mensajes publicados
        # @CONTEXT: Stub de publisher ROS2 para assertion en tests
        # STEP 1: Crear lista interna vacia
        # STEP 2: Exponer metodo publish compatible
        # @SECURITY: Sin operaciones IO externas
        # @AI_CONTEXT: Permite verificar inyecciones de initialpose
        self.messages: List[Any] = []

    def publish(self, message: Any) -> None:
        # @TASK: Registrar mensaje publicado
        # @INPUT: message
        # @OUTPUT: Mensaje agregado a historial
        # @CONTEXT: Emulacion de publisher ROS2 sin middleware
        # STEP 1: Recibir mensaje del productor
        # STEP 2: Guardar referencia para validaciones
        # @SECURITY: No transmite datos fuera del proceso
        # @AI_CONTEXT: Facilita aserciones unitarias en SITL
        self.messages.append(message)


@dataclass(slots=True)
class _MockClockStamp:
    sec: int = 0
    nanosec: int = 0


class _MockClockNow:
    def to_msg(self) -> _MockClockStamp:
        # @TASK: Convertir tiempo a msg
        # @INPUT: Sin parametros
        # @OUTPUT: Objeto stamp compatible
        # @CONTEXT: Stub de now().to_msg() para headers ROS
        # STEP 1: Construir stamp por defecto
        # STEP 2: Retornar estructura compatible
        # @SECURITY: Tiempo fijo evita dependencias del SO
        # @AI_CONTEXT: Suficiente para pruebas no temporales
        return _MockClockStamp()


class _MockClock:
    def now(self) -> _MockClockNow:
        # @TASK: Obtener instante mock
        # @INPUT: Sin parametros
        # @OUTPUT: Objeto now con to_msg
        # @CONTEXT: Stub de reloj ROS2 usado por NavigationManager
        # STEP 1: Crear objeto intermedio de tiempo
        # STEP 2: Retornar instancia para chaining
        # @SECURITY: Sin llamadas al reloj real
        # @AI_CONTEXT: Mantiene firma esperada por codigo productivo
        return _MockClockNow()


class MockNode:
    def __init__(self, name: str) -> None:
        # @TASK: Inicializar nodo mock
        # @INPUT: name
        # @OUTPUT: Nodo con publishers y reloj
        # @CONTEXT: Stub de rclpy.node.Node para tests aislados
        # STEP 1: Guardar nombre del nodo
        # STEP 2: Preparar registro de publishers
        # @SECURITY: No crea sockets ni conexiones DDS
        # @AI_CONTEXT: Sustituto minimo para dependencias de navigation
        self.name: str = name
        self.publishers: List[MockPublisher] = []
        self._clock = _MockClock()

    def create_publisher(self, msg_type: Any, topic: str, qos: int) -> MockPublisher:
        # @TASK: Crear publisher mock
        # @INPUT: msg_type, topic, qos
        # @OUTPUT: Instancia MockPublisher
        # @CONTEXT: Reemplazo de Node.create_publisher
        # STEP 1: Ignorar tipado y qos en entorno mock
        # STEP 2: Registrar publisher para inspeccion
        # @SECURITY: No abre canales de red reales
        # @AI_CONTEXT: topic se conserva para trazabilidad si se amplian asserts
        del msg_type
        del topic
        del qos
        publisher = MockPublisher()
        self.publishers.append(publisher)
        return publisher

    def get_clock(self) -> _MockClock:
        # @TASK: Exponer reloj mock
        # @INPUT: Sin parametros
        # @OUTPUT: Instancia de reloj stub
        # @CONTEXT: Compatibilidad con sellado temporal de mensajes
        # STEP 1: Retornar reloj interno
        # STEP 2: Permitir chaining now().to_msg()
        # @SECURITY: Sin estado global mutable
        # @AI_CONTEXT: Requerido por inyeccion de /initialpose
        return self._clock

    def destroy_node(self) -> None:
        # @TASK: Destruir nodo mock
        # @INPUT: Sin parametros
        # @OUTPUT: Recursos del nodo liberados logicamente
        # @CONTEXT: Stub de cierre de Node para shutdown ordenado
        # STEP 1: Limpiar publishers registrados
        # STEP 2: Mantener metodo idempotente
        # @SECURITY: Evita referencias colgantes entre tests
        # @AI_CONTEXT: Compatible con secuencia close de NavigationManager
        self.publishers.clear()


class MultiThreadedExecutor:
    def __init__(self, num_threads: int = 2) -> None:
        # @TASK: Inicializar executor mock
        # @INPUT: num_threads
        # @OUTPUT: Executor listo para spin simulado
        # @CONTEXT: Stub de rclpy.executors.MultiThreadedExecutor
        # STEP 1: Guardar numero de hilos solicitado
        # STEP 2: Inicializar banderas de control de ciclo
        # @SECURITY: No crea hilos reales innecesarios
        # @AI_CONTEXT: Replica API minima usada por NavigationManager
        self.num_threads: int = num_threads
        self._nodes: List[MockNode] = []
        self._shutdown_event = threading.Event()

    def add_node(self, node: MockNode) -> None:
        # @TASK: Registrar nodo executor
        # @INPUT: node
        # @OUTPUT: Nodo agregado al executor
        # @CONTEXT: Equivalente mock de add_node
        # STEP 1: Recibir instancia de nodo
        # STEP 2: Guardar en lista interna
        # @SECURITY: No hace validaciones costosas en mock
        # @AI_CONTEXT: Permite inspeccion de topologia durante pruebas
        self._nodes.append(node)

    def spin(self) -> None:
        # @TASK: Simular loop spin
        # @INPUT: Sin parametros
        # @OUTPUT: Bloqueo cooperativo hasta shutdown
        # @CONTEXT: Reemplazo de executor.spin en entorno SITL
        # STEP 1: Iterar mientras no se reciba shutdown
        # STEP 2: Dormir intervalos cortos para ceder CPU
        # @SECURITY: Permite finalizacion rapida por evento interno
        # @AI_CONTEXT: Adecuado para pruebas concurrentes de alto nivel
        while not self._shutdown_event.is_set() and _RCLPY_OK:
            time.sleep(0.01)

    def shutdown(self, timeout_sec: float = 0.0) -> None:
        # @TASK: Detener executor mock
        # @INPUT: timeout_sec
        # @OUTPUT: Loop spin marcado para salida
        # @CONTEXT: Equivalente de executor.shutdown
        # STEP 1: Marcar evento interno de parada
        # STEP 2: Consumir timeout sin bloqueo real
        # @SECURITY: Evita deadlocks al cerrar pruebas
        # @AI_CONTEXT: timeout_sec mantenido para compatibilidad de firma
        self._shutdown_event.set()
        if timeout_sec > 0:
            time.sleep(min(timeout_sec, 0.02))


@dataclass(slots=True)
class _MockHeader:
    stamp: _MockClockStamp = field(default_factory=_MockClockStamp)
    frame_id: str = ""


@dataclass(slots=True)
class _MockPosition:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass(slots=True)
class _MockOrientation:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


@dataclass(slots=True)
class _MockPose:
    position: _MockPosition = field(default_factory=_MockPosition)
    orientation: _MockOrientation = field(default_factory=_MockOrientation)


@dataclass(slots=True)
class _MockPoseWithCovariance:
    pose: _MockPose = field(default_factory=_MockPose)
    covariance: List[float] = field(default_factory=lambda: [0.0] * 36)


class PoseWithCovarianceStamped:
    def __init__(self) -> None:
        # @TASK: Crear mensaje pose covariance
        # @INPUT: Sin parametros
        # @OUTPUT: Estructura compatible con geometry_msgs
        # @CONTEXT: Stub para publicacion de /initialpose
        # STEP 1: Crear header vacio
        # STEP 2: Crear pose con covariance por defecto
        # @SECURITY: Sin serializacion ni transporte de red
        # @AI_CONTEXT: Replica forma de mensaje usada por NavigationManager
        self.header = _MockHeader()
        self.pose = _MockPoseWithCovariance()


class PoseStamped:
    def __init__(self) -> None:
        # @TASK: Crear mensaje pose stamped
        # @INPUT: Sin parametros
        # @OUTPUT: Estructura compatible con geometry_msgs
        # @CONTEXT: Stub para waypoints en BasicNavigator
        # STEP 1: Crear header vacio
        # STEP 2: Crear pose por defecto
        # @SECURITY: Sin dependencias binarias ROS
        # @AI_CONTEXT: Suficiente para followWaypoints mock
        self.header = _MockHeader()
        self.pose = _MockPose()


class BasicNavigator:
    def __init__(self) -> None:
        # @TASK: Inicializar navigator mock
        # @INPUT: Sin parametros
        # @OUTPUT: Navigator con historial de waypoints
        # @CONTEXT: Stub de nav2_simple_commander.robot_navigator.BasicNavigator
        # STEP 1: Preparar almacenamiento de rutas recibidas
        # STEP 2: Configurar bandera de tarea completada
        # @SECURITY: Sin dependencia de stack Nav2 real
        # @AI_CONTEXT: Permite pruebas SITL de flujo de navegacion
        self.received_waypoints: List[Any] = []
        self._task_complete: bool = True

    def followWaypoints(self, waypoints: List[Any]) -> None:
        # @TASK: Registrar waypoints recibidos
        # @INPUT: waypoints
        # @OUTPUT: Estado de tarea marcado como en progreso breve
        # @CONTEXT: Simulacion de comando de ruta en Nav2
        # STEP 1: Copiar waypoints a historial interno
        # STEP 2: Simular progreso minimo y marcar completion
        # @SECURITY: No envía comandos de movimiento reales
        # @AI_CONTEXT: Compatible con polling isTaskComplete del wrapper
        self.received_waypoints = list(waypoints)
        self._task_complete = False
        time.sleep(0.01)
        self._task_complete = True

    def isTaskComplete(self) -> bool:
        # @TASK: Consultar estado tarea
        # @INPUT: Sin parametros
        # @OUTPUT: Booleano de completitud
        # @CONTEXT: API requerida por NavigationManager
        # STEP 1: Leer bandera interna
        # STEP 2: Retornar resultado al llamador
        # @SECURITY: Sin efectos secundarios
        # @AI_CONTEXT: Soporta ciclos de polling en pruebas
        return self._task_complete


def install_mocks(target: Dict[str, Any]) -> None:
    # @TASK: Instalar modulos mock
    # @INPUT: target
    # @OUTPUT: Entradas de sys.modules para ROS2 y Nav2
    # @CONTEXT: Helper para aislar tests de dependencias nativas
    # STEP 1: Construir objetos modulo para rutas importadas
    # STEP 2: Inyectar stubs en diccionario destino
    # @SECURITY: Evita carga accidental de paquetes ROS reales
    # @AI_CONTEXT: target tipico es sys.modules desde un fixture pytest
    rclpy_module = types.ModuleType("rclpy")
    rclpy_module.init = init
    rclpy_module.shutdown = shutdown
    rclpy_module.ok = ok
    rclpy_module.spin = spin

    rclpy_executors_module = types.ModuleType("rclpy.executors")
    rclpy_executors_module.MultiThreadedExecutor = MultiThreadedExecutor

    rclpy_node_module = types.ModuleType("rclpy.node")
    rclpy_node_module.Node = MockNode

    geometry_msgs_module = types.ModuleType("geometry_msgs")
    geometry_msgs_msg_module = types.ModuleType("geometry_msgs.msg")
    geometry_msgs_msg_module.PoseWithCovarianceStamped = PoseWithCovarianceStamped
    geometry_msgs_msg_module.PoseStamped = PoseStamped

    nav2_module = types.ModuleType("nav2_simple_commander")
    nav2_robot_navigator_module = types.ModuleType("nav2_simple_commander.robot_navigator")
    nav2_robot_navigator_module.BasicNavigator = BasicNavigator

    target["rclpy"] = rclpy_module
    target["rclpy.executors"] = rclpy_executors_module
    target["rclpy.node"] = rclpy_node_module
    target["geometry_msgs"] = geometry_msgs_module
    target["geometry_msgs.msg"] = geometry_msgs_msg_module
    target["nav2_simple_commander"] = nav2_module
    target["nav2_simple_commander.robot_navigator"] = nav2_robot_navigator_module


__all__ = [
    "BasicNavigator",
    "MockNode",
    "MultiThreadedExecutor",
    "PoseStamped",
    "PoseWithCovarianceStamped",
    "init",
    "install_mocks",
    "ok",
    "shutdown",
    "spin",
]