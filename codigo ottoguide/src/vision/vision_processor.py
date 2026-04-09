from __future__ import annotations

# @TASK: Implementar pipeline de vision continuo con captura D435i, deteccion AprilTag y despacho odometrico
# @INPUT: DeviceIndex o URI de la camara D435i; parametros de calibracion intrinseca
# @OUTPUT: PoseEstimate y OdometryVector en asyncio.Queue consumible por AsyncNav2Bridge
# @CONTEXT: Modulo de vision HIL Fase 5; capa de odometria absoluta via AprilTag tag36h11
# STEP 1: Ciclo de captura continuo en daemon thread aislado del event loop
# STEP 2: Deteccion ArUco tag36h11 con cv2.aruco.ArucoDetector o API legacy
# STEP 3: Estimacion de pose con cv2.solvePnP -> extraccion (x, y, theta) en frame map
# STEP 4: Despacho thread-safe a asyncio.Queue via loop.call_soon_threadsafe
# STEP 5: Reconexion automatica de camara con backoff exponencial ante frame loss
# @SECURITY: Todos los descriptores de video se liberan explicitamente en close()
# @AI_CONTEXT: framerate capping via time.sleep para no saturar bus USB ni CPU embebido

import asyncio
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import cv2
import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# Constantes de configuracion
# ---------------------------------------------------------------------------

# @TASK: Declarar parametros operativos del pipeline de vision
# @INPUT: Ninguno
# @OUTPUT: Constantes de framerate, timeout y backoff de reconexion
# @CONTEXT: Calibrados para el companion PC del Unitree G1 EDU (CPU embebido arm64)
# STEP 1: Framerate maximo para no saturar bus USB 3.0 con D435i en modo RGB
# STEP 2: Timeout de frame para detectar perdida de camara y activar reconexion
# STEP 3: Limites de backoff exponencial para reconexion controlada
# @SECURITY: TARGET_FPS <= 15 previene saturacion del bus USB con D435i a 1280x720
# @AI_CONTEXT: D435i en modo depth+color requiere mas ancho de banda que solo color
TARGET_FPS: float = 10.0
FRAME_PERIOD_S: float = 1.0 / TARGET_FPS
FRAME_TIMEOUT_S: float = 2.0          # segundos sin frame antes de reconectar
RECONNECT_BACKOFF_MIN_S: float = 1.0  # espera minima entre intentos de reconexion
RECONNECT_BACKOFF_MAX_S: float = 16.0 # espera maxima entre intentos (backoff cap)
POSE_QUEUE_MAX_SIZE: int = 4          # estimaciones maximas en cola sin consumir

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CameraModel:
    # @TASK: Encapsular parametros de calibracion intrinseca de la camara D435i
    # @INPUT: Matriz de camara K 3x3 y coeficientes de distorsion de calibracion RealSense
    # @OUTPUT: Estructura inmutable usada en cada llamada a solvePnP
    # @CONTEXT: Calibracion obtenida con rs2 o tablero de ajedrez; fija durante la sesion
    # STEP 1: Persistir camera_matrix (K) y distortion_coefficients (D)
    # @SECURITY: Los valores de calibracion no se modifican en runtime
    # @AI_CONTEXT: Para D435i en 640x480 RGB, fx~fy~615, cx~320, cy~240 como valores tipicos
    camera_matrix: NDArray[np.float64]
    distortion_coefficients: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class PoseEstimate:
    # @TASK: Encapsular estimacion de pose 6DOF de un marcador AprilTag detectado
    # @INPUT: marker_id; rvec (vector de rotacion Rodrigues); tvec (vector de traslacion)
    # @OUTPUT: Estructura inmutable despachada a la cola de odometria
    # @CONTEXT: Producida por solvePnP sobre esquinas del tag36h11 detectado
    # STEP 1: Registrar id del marcador y transformacion 6DOF
    # @SECURITY: Frozen; no puede ser mutado por consumidores downstream
    # @AI_CONTEXT: tvec en metros en el frame de la camara; convertir a frame map en AsyncNav2Bridge
    marker_id: int
    rvec: NDArray[np.float64]
    tvec: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class OdometryVector:
    # @TASK: Encapsular vector de correccion odometrica en el plano XY del mapa
    # @INPUT: Coordenadas x, y en metros y angulo theta en radianes en frame map
    # @OUTPUT: Estructura consumible directamente por AsyncNav2Bridge.inject_absolute_pose
    # @CONTEXT: Resultado final del pipeline de vision expresado en coordenadas del mapa
    # STEP 1: Registrar posicion 2D y orientacion yaw del robot segun la vista del tag
    # @SECURITY: Valores derivados de calibracion; errores de calibracion se propagan aqui
    # @AI_CONTEXT: theta es el yaw del robot en el frame map; positivo = antihorario segun REP103
    marker_id: int
    x: float
    y: float
    theta: float
    pose_estimate: PoseEstimate  # referencia completa para consumidores que necesiten rvec/tvec


@dataclass(slots=True)
class _CaptureStats:
    # @TASK: Registrar metricas de salud del ciclo de captura para observabilidad
    # @INPUT: Actualizaciones incrementales desde el loop de captura
    # @OUTPUT: Contadores consultables via propiedad del VisionProcessor
    # @CONTEXT: Telemetria del daemon thread; no se expone al event loop directamente
    # STEP 1: Inicializar contadores de frames, detecciones y reconexiones
    # @SECURITY: Solo el daemon thread escribe; propiedades externas son solo lectura
    # @AI_CONTEXT: frames_captured es un indicador de salud del flujo USB
    frames_captured: int = 0
    detections: int = 0
    reconnect_count: int = 0
    last_detection_ts: float = 0.0


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------

class VisionProcessor:
    # @TASK: Capturar frames de la D435i, detectar AprilTags y despachar vectores odometricos
    # @INPUT: CameraModel; device_index o URI de la camara; parametros de tag
    # @OUTPUT: asyncio.Queue[OdometryVector] consumible por AsyncNav2Bridge
    # @CONTEXT: Componente central de HIL Fase 5; opera en un daemon thread aislado
    # STEP 1: Inicializar estructura interna sin abrir la camara (separar init/start)
    # STEP 2: start() abre la camara y lanza el daemon thread de captura
    # STEP 3: El daemon thread envia OdometryVector al event loop via call_soon_threadsafe
    # @SECURITY: cv2.VideoCapture.release() se invoca en close() en cualquier ruta de salida
    # @AI_CONTEXT: La clase es NOT thread-safe excepto en los metodos marcados como thread-safe

    def __init__(
        self,
        *,
        camera_model: CameraModel,
        tag_size_m: float,
        device_index: int = 0,
        target_fps: float = TARGET_FPS,
        pose_queue_maxsize: int = POSE_QUEUE_MAX_SIZE,
        preferred_marker_id: Optional[int] = None,
    ) -> None:
        # @TASK: Construir estado interno del VisionProcessor sin inicializar hardware
        # @INPUT: camera_model; tag_size_m en metros; device_index de la D435i; fps; queue size
        # @OUTPUT: VisionProcessor en estado PRE-INIT; inutilizable hasta llamar start()
        # @CONTEXT: Separacion init/start para compatibilidad con DI en main.py
        # STEP 1: Validar parametros de entrada antes de cualquier asignacion
        # STEP 2: Inicializar artefactos de sincronizacion hilo/loop
        # STEP 3: Construir detector ArUco para tag36h11 (creacion ligera, sin camara)
        # STEP 4: Marcar estado PRE-INIT; start() completara la apertura de hardware
        # @SECURITY: Sin acceso a hardware en __init__; garantiza que el proceso puede importar el modulo sin D435i
        # @AI_CONTEXT: _stop_event como threading.Event permite terminacion thread-safe sin asyncio

        if tag_size_m <= 0:
            raise ValueError("tag_size_m debe ser mayor que 0.")
        if target_fps <= 0 or target_fps > 30:
            raise ValueError("target_fps debe estar en el rango (0, 30].")

        # STEP 1
        self._camera_model: CameraModel = camera_model
        self._tag_size_m: float = tag_size_m
        self._device_index: int = device_index
        self._frame_period_s: float = 1.0 / target_fps
        self._preferred_marker_id: Optional[int] = preferred_marker_id

        # STEP 2: Primitivas de sincronizacion
        self._stop_event: threading.Event = threading.Event()
        self._pose_queue: asyncio.Queue[OdometryVector] = asyncio.Queue(
            maxsize=pose_queue_maxsize
        )
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._stats: _CaptureStats = _CaptureStats()
        self._started: bool = False

        # STEP 3: Construir detector ArUco (sin abrir camara)
        self._aruco_dict = cv2.aruco.getPredefinedDictionary(
            cv2.aruco.DICT_APRILTAG_36h11
        )
        self._aruco_params = cv2.aruco.DetectorParameters()
        self._aruco_detector: Optional[Any] = self._build_detector()

        # STEP 4
        LOGGER.debug("[Vision] VisionProcessor en estado PRE-INIT. device=%d", device_index)

    # -----------------------------------------------------------------------
    # Ciclo de vida
    # -----------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        # @TASK: Abrir la camara D435i y lanzar el daemon thread de captura
        # @INPUT: loop — event loop activo del proceso principal para despacho thread-safe
        # @OUTPUT: Camara abierta; daemon thread activo; VisionProcessor en estado ACTIVO
        # @CONTEXT: Invocado desde main.py tras inicializar hardware y orquestador
        # STEP 1: Verificar que start() no se llame mas de una vez
        # STEP 2: Persistir referencia al event loop para call_soon_threadsafe posterior
        # STEP 3: Abrir cv2.VideoCapture con device_index; forzar configuracion D435i
        # STEP 4: Lanzar daemon thread de captura con _capture_loop como target
        # @SECURITY: Si la camara no abre, se lanza RuntimeError; no se inicia el thread
        # @AI_CONTEXT: start() es sincrono intencionalmente; se llama antes de asyncio.run en main.py

        if self._started:
            LOGGER.warning("[Vision] start() llamado mas de una vez; ignorado.")
            return

        # STEP 1 + 2
        self._loop = loop

        # STEP 3
        self._cap = self._open_capture()
        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError(
                f"No se pudo abrir la camara en device_index={self._device_index}. "
                "Verificar conexion USB y permisos /dev/video*."
            )

        # STEP 4
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="vision-capture-daemon",
            daemon=True,
        )
        self._capture_thread.start()
        self._started = True
        LOGGER.info(
            "[Vision] Pipeline de captura iniciado. device=%d fps_cap=%.1f",
            self._device_index,
            1.0 / self._frame_period_s,
        )

    def close(self) -> None:
        # @TASK: Detener el daemon thread de captura y liberar descriptores de video
        # @INPUT: Sin parametros
        # @OUTPUT: Thread detenido; cv2.VideoCapture liberado; recursos USB liberados
        # @CONTEXT: Invocado desde _graceful_shutdown de main.py
        # STEP 1: Setear _stop_event para señalar al daemon thread que debe terminar
        # STEP 2: Esperar terminacion del thread con timeout de 3 s
        # STEP 3: Invocar _release_capture() para liberar el descriptor de video
        # @SECURITY: release() es obligatorio; evita bloqueo del device /dev/video* tras salida
        # @AI_CONTEXT: Si el thread no termina en 3 s es daemon; muere con el proceso igualmente

        LOGGER.info("[Vision] Iniciando cierre del VisionProcessor.")

        # STEP 1
        self._stop_event.set()

        # STEP 2
        if self._capture_thread is not None and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=3.0)
            if self._capture_thread.is_alive():
                LOGGER.warning("[Vision] Daemon thread no termino en 3 s; sera destruido con el proceso.")

        # STEP 3
        self._release_capture()
        LOGGER.info("[Vision] VisionProcessor cerrado. stats=%s", self._stats)

    # -----------------------------------------------------------------------
    # API asincrona para consumidores
    # -----------------------------------------------------------------------

    @property
    def pose_queue(self) -> asyncio.Queue[OdometryVector]:
        # @TASK: Exponer la cola de vectores odometricos para consumo por AsyncNav2Bridge
        # @INPUT: Sin parametros
        # @OUTPUT: asyncio.Queue[OdometryVector] con estimaciones recientes
        # @CONTEXT: Interfaz principal de salida del VisionProcessor hacia el bridge de navegacion
        # STEP 1: Retornar referencia a la cola interna; el caller la consume con await queue.get()
        # @SECURITY: Solo lectura de la referencia; el daemon thread es el unico productor
        # @AI_CONTEXT: La cola tiene maxsize=POSE_QUEUE_MAX_SIZE; put_nowait falla silenciosamente si llena
        return self._pose_queue  # STEP 1

    @property
    def stats(self) -> _CaptureStats:
        # @TASK: Exponer metricas de salud del ciclo de captura
        # @INPUT: Sin parametros
        # @OUTPUT: _CaptureStats con contadores de frames, detecciones y reconexiones
        # @CONTEXT: Metrica de observabilidad para APIServer y monitoreo HIL
        # STEP 1: Retornar referencia al objeto de estadisticas interno
        # @SECURITY: El objeto es mutable; no modificar desde fuera del VisionProcessor
        # @AI_CONTEXT: reconnect_count > 0 indica inestabilidad de conexion USB
        return self._stats  # STEP 1

    async def get_next_estimate(
        self,
        timeout_s: float = 1.0,
    ) -> Optional[OdometryVector]:
        # @TASK: Obtener el siguiente vector odometrico de la cola de forma asincrona
        # @INPUT: timeout_s — espera maxima antes de retornar None
        # @OUTPUT: OdometryVector o None si no hay deteccion dentro del timeout
        # @CONTEXT: Metodo de conveniencia para consumidores que prefieren await sobre queue.get()
        # STEP 1: Esperar extraction de la cola con timeout usando asyncio.wait_for
        # STEP 2: Retornar None ante asyncio.TimeoutError sin propagar excepcion
        # @SECURITY: Sin bloqueo del event loop; asyncio.wait_for es cooperativo
        # @AI_CONTEXT: AsyncNav2Bridge puede usar este metodo o suscribirse directamente a pose_queue
        try:
            # STEP 1
            return await asyncio.wait_for(self._pose_queue.get(), timeout=timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            # STEP 2
            return None

    # -----------------------------------------------------------------------
    # Daemon thread — ciclo de captura, deteccion y reconexion
    # -----------------------------------------------------------------------

    def _capture_loop(self) -> None:
        # @TASK: Ejecutar ciclo continuo de captura, procesamiento y despacho de poses
        # @INPUT: Sin parametros directos; usa estado interno de la instancia
        # @OUTPUT: OdometryVector despachados a la cola via call_soon_threadsafe
        # @CONTEXT: Unico productor de la cola; corre en daemon thread aislado del event loop
        # STEP 1: Leer frame de cv2.VideoCapture con control de timeout de hardware
        # STEP 2: Procesar frame para detectar AprilTag y estimar pose con solvePnP
        # STEP 3: Convertir PoseEstimate a OdometryVector y despachar al event loop
        # STEP 4: Aplicar framerate cap con time.sleep para no saturar CPU y bus USB
        # STEP 5: Ante timeout de frame, activar reconexion con backoff exponencial
        # @SECURITY: Toda excepcion no fatal se registra y continua el loop; solo _stop_event cancela
        # @AI_CONTEXT: El loop corre hasta que _stop_event se setee o la camara falle irrecuperablemente

        backoff_s: float = RECONNECT_BACKOFF_MIN_S

        while not self._stop_event.is_set():
            frame_start = time.monotonic()

            # STEP 1: Captura de frame
            cap = self._cap
            if cap is None or not cap.isOpened():
                LOGGER.warning("[Vision] Camara no disponible; iniciando reconexion.")
                self._reconnect_with_backoff(backoff_s)
                backoff_s = min(backoff_s * 2.0, RECONNECT_BACKOFF_MAX_S)
                continue

            ret, frame_bgr = cap.read()

            if not ret or frame_bgr is None or frame_bgr.size == 0:
                elapsed = time.monotonic() - frame_start
                if elapsed > FRAME_TIMEOUT_S:
                    # STEP 5: timeout de hardware
                    LOGGER.warning(
                        "[Vision] Timeout de frame (%.2f s); reconectando.", elapsed
                    )
                    self._reconnect_with_backoff(backoff_s)
                    backoff_s = min(backoff_s * 2.0, RECONNECT_BACKOFF_MAX_S)
                else:
                    time.sleep(0.01)  # pausa breve en dropped frame puntual
                continue

            # Frame valido: resetear backoff
            backoff_s = RECONNECT_BACKOFF_MIN_S
            self._stats.frames_captured += 1

            # STEP 2: Deteccion y estimacion de pose
            try:
                pose = self._process_frame_sync(frame_bgr)
            except Exception as exc:
                LOGGER.error("[Vision] Error en _process_frame_sync: %s", exc)
                pose = None

            if pose is not None:
                # STEP 3: Construir OdometryVector y despachar thread-safe
                odometry = self._pose_to_odometry(pose)
                self._stats.detections += 1
                self._stats.last_detection_ts = time.monotonic()
                self._dispatch_odometry(odometry)

            # STEP 4: Framerate cap
            elapsed_total = time.monotonic() - frame_start
            sleep_remaining = self._frame_period_s - elapsed_total
            if sleep_remaining > 0.0:
                time.sleep(sleep_remaining)

        LOGGER.info("[Vision] Capture loop terminado por _stop_event.")

    def _reconnect_with_backoff(self, backoff_s: float) -> None:
        # @TASK: Cerrar y reabrir cv2.VideoCapture implementando backoff exponencial
        # @INPUT: backoff_s — tiempo de espera antes de reintentar apertura
        # @OUTPUT: self._cap actualizado con nueva instancia o None si falla
        # @CONTEXT: Recuperacion ante perdida de frame o desconexion USB del D435i
        # STEP 1: Liberar el descriptor de video actual antes de reintentar
        # STEP 2: Esperar backoff_s respetando _stop_event para terminacion rapida
        # STEP 3: Intentar reabrir la camara con _open_capture()
        # @SECURITY: _stop_event se verifica DURANTE el backoff; evita espera larga en shutdown
        # @AI_CONTEXT: D435i puede tardar hasta 3-4 s en reinicializar tras reconexion USB

        self._stats.reconnect_count += 1
        LOGGER.info(
            "[Vision] Reconexion #%d en %.1f s...",
            self._stats.reconnect_count,
            backoff_s,
        )

        # STEP 1
        self._release_capture()

        # STEP 2: espera interruptible
        deadline = time.monotonic() + backoff_s
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return
            time.sleep(0.1)

        # STEP 3
        if not self._stop_event.is_set():
            new_cap = self._open_capture()
            self._cap = new_cap
            if new_cap is not None and new_cap.isOpened():
                LOGGER.info("[Vision] Reconexion exitosa. device=%d", self._device_index)
            else:
                LOGGER.error(
                    "[Vision] Reconexion fallida. device=%d. Proximo intento con backoff mayor.",
                    self._device_index,
                )

    def _dispatch_odometry(self, odometry: OdometryVector) -> None:
        # @TASK: Despachar OdometryVector a la asyncio.Queue del event loop de forma thread-safe
        # @INPUT: odometry — vector de correccion calculado en el daemon thread
        # @OUTPUT: OdometryVector encolado o descartado si la cola esta llena
        # @CONTEXT: Puente thread->event loop; unico mecanismo de comunicacion del daemon con asyncio
        # STEP 1: Definir callable que invoca Queue.put_nowait dentro del event loop
        # STEP 2: Registrar el callable en el loop via call_soon_threadsafe
        # @SECURITY: put_nowait no bloquea; QueueFull se captura silenciosamente para descarte LIFO implicito
        # @AI_CONTEXT: call_soon_threadsafe es el unico mecanismo thread-safe para producir en asyncio.Queue

        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _enqueue() -> None:
            # STEP 1: ejecutado en el event loop; put_nowait no bloquea
            try:
                self._pose_queue.put_nowait(odometry)
            except asyncio.QueueFull:
                LOGGER.debug(
                    "[Vision] Cola de odometria llena (maxsize=%d); descartando estimacion antigua.",
                    self._pose_queue.maxsize,
                )
                # Descartar la estimacion mas antigua y reintentar (politica LIFO implicita)
                try:
                    self._pose_queue.get_nowait()
                    self._pose_queue.put_nowait(odometry)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

        # STEP 2
        loop.call_soon_threadsafe(_enqueue)

    # -----------------------------------------------------------------------
    # Procesamiento de frames (CPU-bound, ejecutado en daemon thread)
    # -----------------------------------------------------------------------

    def _process_frame_sync(
        self,
        frame_bgr: NDArray[np.uint8],
    ) -> Optional[PoseEstimate]:
        # @TASK: Detectar AprilTag y estimar pose 6DOF en un frame BGR
        # @INPUT: frame_bgr — frame capturado por cv2.VideoCapture
        # @OUTPUT: PoseEstimate con rvec/tvec del marcador seleccionado o None
        # @CONTEXT: Nucleo CPU-bound del pipeline; ejecutado en el daemon thread
        # STEP 1: Convertir BGR a escala de grises para reducir carga de deteccion
        # STEP 2: Detectar marcadores tag36h11 con ArucoDetector o API legacy
        # STEP 3: Seleccionar marcador objetivo (preferred_marker_id o primero detectado)
        # STEP 4: Resolver pose 6DOF con cv2.solvePnP (IPPE_SQUARE para marcadores planos)
        # @SECURITY: Retornar None en cualquier condicion degenerada; nunca propagar excepcion
        # @AI_CONTEXT: SOLVEPNP_IPPE_SQUARE es el flag correcto para marcadores cuadrados planos

        if frame_bgr is None or frame_bgr.size == 0:
            return None

        # STEP 1
        grayscale = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        # STEP 2
        corners, ids, _ = self._detect_markers(grayscale)

        if ids is None or len(ids) == 0:
            return None

        # STEP 3
        marker_index = self._select_marker_index(ids, self._preferred_marker_id)
        if marker_index is None:
            return None

        image_points = corners[marker_index].reshape(4, 2).astype(np.float64)
        object_points = _build_object_points(self._tag_size_m)

        # STEP 4
        success, rvec, tvec = cv2.solvePnP(
            object_points,
            image_points,
            self._camera_model.camera_matrix,
            self._camera_model.distortion_coefficients,
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )

        if not success:
            return None

        return PoseEstimate(
            marker_id=int(ids[marker_index].flat[0]),
            rvec=rvec.astype(np.float64),
            tvec=tvec.astype(np.float64),
        )

    def _detect_markers(
        self,
        grayscale: NDArray[np.uint8],
    ) -> tuple[
        list[NDArray[np.float32]],
        Optional[NDArray[np.int32]],
        list[NDArray[np.float32]],
    ]:
        # @TASK: Ejecutar deteccion de marcadores ArUco con API moderna o legacy
        # @INPUT: grayscale — imagen monocromatica uint8
        # @OUTPUT: (corners, ids, rejected) — salida estandar de la API ArUco
        # @CONTEXT: Capa de compatibilidad entre OpenCV 4.6+ (ArucoDetector) y versiones legacy
        # STEP 1: Usar cv2.aruco.ArucoDetector si fue construido en _build_detector()
        # STEP 2: Fallback a cv2.aruco.detectMarkers (API legacy pre-4.6)
        # @SECURITY: Ambas rutas retornan la misma tupla; el caller no distingue
        # @AI_CONTEXT: OpenCV en ROS 2 Humble puede ser la version legacy en algunos binarios

        if self._aruco_detector is not None:
            # STEP 1
            corners, ids, rejected = self._aruco_detector.detectMarkers(grayscale)
            return corners, ids, rejected

        # STEP 2
        corners, ids, rejected = cv2.aruco.detectMarkers(
            grayscale,
            self._aruco_dict,
            parameters=self._aruco_params,
        )
        return corners, ids, rejected

    # -----------------------------------------------------------------------
    # Conversion de coordenadas
    # -----------------------------------------------------------------------

    @staticmethod
    def _pose_to_odometry(pose: PoseEstimate) -> OdometryVector:
        # @TASK: Convertir PoseEstimate 6DOF (frame camara) a OdometryVector 2D (frame map proxy)
        # @INPUT: pose — PoseEstimate con rvec y tvec en frame de la camara
        # @OUTPUT: OdometryVector con x, y en metros y theta en radianes
        # @CONTEXT: Proyeccion de la estimacion 3D al plano XY del mapa para AMCL
        # STEP 1: Extraer traslacion x, y directamente desde tvec (Z se ignora para robot planar)
        # STEP 2: Convertir rvec a matriz de rotacion 3x3 via cv2.Rodrigues
        # STEP 3: Extraer yaw (theta) del plano XY como atan2(R[1,0], R[0,0])
        # @SECURITY: Metodo puro; no modifica estado de la instancia
        # @AI_CONTEXT: La conversion de frame camara a frame map requiere transformacion extrinseca
        #              calibrada; esta implementacion asume camara alineada al robot (ajustar en HIL)

        # STEP 1
        x = float(pose.tvec[0][0])
        y = float(pose.tvec[1][0])

        # STEP 2
        rotation_matrix, _ = cv2.Rodrigues(pose.rvec)

        # STEP 3
        theta = math.atan2(
            float(rotation_matrix[1, 0]),
            float(rotation_matrix[0, 0]),
        )

        return OdometryVector(
            marker_id=pose.marker_id,
            x=x,
            y=y,
            theta=theta,
            pose_estimate=pose,
        )

    # -----------------------------------------------------------------------
    # Utilidades de hardware
    # -----------------------------------------------------------------------

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        # @TASK: Abrir cv2.VideoCapture con configuracion optimizada para la D435i
        # @INPUT: self._device_index — indice V4L2 de la camara
        # @OUTPUT: cv2.VideoCapture abierta y configurada o None en fallo
        # @CONTEXT: Usado en start() y en reconexion automatica
        # STEP 1: Abrir VideoCapture con backend V4L2 para Linux embebido
        # STEP 2: Configurar resolucion 640x480 y formato MJPG para menor latencia USB
        # STEP 3: Verificar apertura y retornar None si falla
        # @SECURITY: El descriptor retornado debe ser liberado con release() en close()
        # @AI_CONTEXT: CAP_V4L2 es el backend correcto para D435i en Linux arm64

        cap = cv2.VideoCapture(self._device_index, cv2.CAP_V4L2)  # STEP 1

        if not cap.isOpened():
            cap.release()
            return None

        # STEP 2: configurar para menor latencia y menor carga USB
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, TARGET_FPS * 2)  # solicitar el doble; el driver lo limita
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)  # buffer minimo para frames recientes

        # STEP 3
        if not cap.isOpened():
            cap.release()
            return None

        return cap

    def _release_capture(self) -> None:
        # @TASK: Liberar el descriptor de video de forma segura
        # @INPUT: Sin parametros
        # @OUTPUT: cv2.VideoCapture liberado y self._cap = None
        # @CONTEXT: Invocado en close() y en _reconnect_with_backoff antes de reintentar
        # STEP 1: Verificar que self._cap no sea None antes de llamar release()
        # STEP 2: Invocar release() y anular referencia
        # @SECURITY: release() libera el file descriptor /dev/video*; critico para evitar bloqueo del dispositivo
        # @AI_CONTEXT: cv2.VideoCapture.release() es idempotente en la mayoria de versiones de OpenCV

        cap = self._cap
        if cap is not None:        # STEP 1
            try:
                cap.release()      # STEP 2
            except Exception as exc:
                LOGGER.error("[Vision] Error al hacer release() de VideoCapture: %s", exc)
            finally:
                self._cap = None

    def _build_detector(self) -> Optional[Any]:
        # @TASK: Instanciar detector ArUco moderno si la API esta disponible
        # @INPUT: Diccionario y parametros ya configurados en __init__
        # @OUTPUT: cv2.aruco.ArucoDetector o None si la clase no existe en esta version
        # @CONTEXT: cv2.aruco.ArucoDetector fue introducido en OpenCV 4.6.0
        # STEP 1: Verificar existencia de la clase ArucoDetector con getattr
        # STEP 2: Instanciar si existe; retornar None para activar la ruta legacy
        # @SECURITY: Sin efectos secundarios; solo introspeccion de la API
        # @AI_CONTEXT: La ruta legacy en _detect_markers es funcionalmente equivalente para tag36h11

        detector_cls = getattr(cv2.aruco, "ArucoDetector", None)
        if detector_cls is None:   # STEP 1
            return None
        return detector_cls(self._aruco_dict, self._aruco_params)  # STEP 2

    # -----------------------------------------------------------------------
    # Utilidades estaticas
    # -----------------------------------------------------------------------

    @staticmethod
    def _select_marker_index(
        ids: NDArray[np.int32],
        preferred_marker_id: Optional[int],
    ) -> Optional[int]:
        # @TASK: Seleccionar el indice del marcador objetivo dentro de la lista de detectados
        # @INPUT: ids — array de IDs detectados; preferred_marker_id — ID prioritario o None
        # @OUTPUT: Indice en la lista de corners o None si no se encuentra
        # @CONTEXT: Politica de seleccion aplicada antes de solvePnP
        # STEP 1: Si preferred_marker_id esta definido, buscarlo en ids
        # STEP 2: Si no se encuentra el preferido o no se especifica, usar el primer marcador
        # @SECURITY: Evita IndexError en acceso a corners con indice fuera de rango
        # @AI_CONTEXT: Reemplazable por heuristica de area de esquinas para mayor robustez

        if ids is None or ids.size == 0:
            return None

        flattened = ids.reshape(-1)

        if preferred_marker_id is not None:  # STEP 1
            for idx, marker_id in enumerate(flattened):
                if int(marker_id) == preferred_marker_id:
                    return idx
            return None  # preferido no encontrado

        return 0  # STEP 2: primer marcador detectado


# ---------------------------------------------------------------------------
# Funciones auxiliares del modulo (fuera de la clase para reutilizacion)
# ---------------------------------------------------------------------------

def _build_object_points(tag_size_m: float) -> NDArray[np.float64]:
    # @TASK: Construir array de puntos 3D del marcador AprilTag en su sistema de referencia local
    # @INPUT: tag_size_m — lado del marcador en metros
    # @OUTPUT: Array (4, 3) float64 con vertices del marcador en orden TL, TR, BR, BL
    # @CONTEXT: Modelo geometrico requerido por cv2.solvePnP para marcadores cuadrados planos
    # STEP 1: Calcular semilado del marcador
    # STEP 2: Construir los 4 vertices en el plano Z=0 del frame local del marcador
    # @SECURITY: Metodo puro; no modifica estado global
    # @AI_CONTEXT: El orden TL, TR, BR, BL debe coincidir con el orden de corners retornado por cv2.aruco

    half = tag_size_m / 2.0  # STEP 1
    return np.array(          # STEP 2
        [
            [-half,  half, 0.0],  # TL (top-left)
            [ half,  half, 0.0],  # TR (top-right)
            [ half, -half, 0.0],  # BR (bottom-right)
            [-half, -half, 0.0],  # BL (bottom-left)
        ],
        dtype=np.float64,
    )


# ---------------------------------------------------------------------------
# Exportaciones
# ---------------------------------------------------------------------------

__all__ = [
    "CameraModel",
    "OdometryVector",
    "PoseEstimate",
    "VisionProcessor",
]