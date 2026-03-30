from __future__ import annotations

# @TASK: Bootstrap principal del sistema de guiado autonomo HIL Fase 2
# @INPUT: Variables de entorno seteadas por start_robot.sh (RMW_IMPLEMENTATION, CYCLONEDDS_URI)
# @OUTPUT: Stack robotico levantado; event loop activo hasta SIGINT/SIGTERM
# @CONTEXT: Entrypoint unico; ejecutado por start_robot.sh como proceso hijo
# STEP 1: Configurar logging, SDK path y signal handlers antes de todo IO
# STEP 2: Barrera mecanica interactiva — bloqueo hasta confirmacion de operador
# STEP 3: Inicializar RobotHardwareAPI en executor (negociacion DDS aislada)
# STEP 4: Instanciar TourOrchestrator y verificar estado inicial IDLE
# STEP 5: Lanzar tareas residentes y esperar shutdown_event
# STEP 6: Rutina de apagado graceful con Damp() obligatorio antes de exit
# @SECURITY: Damp() se invoca antes de destruir el event loop en cualquier ruta de salida
# @AI_CONTEXT: Toda llamada bloqueante sale del event loop via ThreadPoolExecutor

import asyncio
import contextlib
import logging
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import numpy as np
import rclpy

from src.api import APIServer
from src.core import TourOrchestrator
from src.hardware import RobotHardwareAPI
from src.interaction import CloudNLPPipeline, ConversationManager, LocalNLPPipeline
from src.navigation import AsyncNav2Bridge
from src.vision import CameraModel, VisionProcessor


# ---------------------------------------------------------------------------
# Constantes de configuracion
# ---------------------------------------------------------------------------

_DDS_INIT_TIMEOUT_S: float = 5.0
_DAMP_SHUTDOWN_TIMEOUT_S: float = 2.0
_API_STOP_TIMEOUT_S: float = 3.0
_LOCOMOTION_IP: str = "192.168.123.161"

LOGGER = logging.getLogger("robot_humanoide.main")


# ---------------------------------------------------------------------------
# CONFIGURACION DE ENTORNO
# ---------------------------------------------------------------------------

def _configure_base_logging() -> None:
    # @TASK: Configurar logging base del proceso principal
    # @INPUT: Sin parametros
    # @OUTPUT: Logging global inicializado a nivel INFO con formato canonico
    # @CONTEXT: Primer paso del bootstrap; debe ejecutarse antes de cualquier log
    # STEP 1: Definir formato uniforme incluyendo timestamp, nivel, modulo y mensaje
    # STEP 2: Fijar nivel INFO como base; DEBUG disponible por variable de entorno
    # @SECURITY: Sin exposicion de credenciales ni datos de red en formato base
    # @AI_CONTEXT: Ajustar a DEBUG en integraciones HIL prolongadas si se requiere
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _configure_unitree_sdk_path() -> None:
    # @TASK: Inyectar ruta local del SDK Unitree en sys.path
    # @INPUT: Ruta relativa derivada de __file__
    # @OUTPUT: libs/unitree_sdk2_python-master prepended a sys.path si existe
    # @CONTEXT: Necesario para importacion dinamica del SDK en entorno air-gapped
    # STEP 1: Resolver ruta absoluta del directorio SDK local
    # STEP 2: Insertar al inicio de sys.path para prioridad sobre site-packages
    # @SECURITY: Solo agrega rutas del arbol del proyecto; no modifica PYTHONPATH del OS
    # @AI_CONTEXT: Complementa la inyeccion de PYTHONPATH realizada por start_robot.sh
    sdk_root = Path(__file__).resolve().parent / "libs" / "unitree_sdk2_python-master"
    if sdk_root.exists():
        sdk_str = str(sdk_root)
        if sdk_str not in sys.path:
            sys.path.insert(0, sdk_str)
            LOGGER.debug("SDK path inyectado: %s", sdk_str)
    else:
        LOGGER.warning(
            "Directorio SDK no encontrado: %s — importacion de unitree_sdk2py puede fallar",
            sdk_root,
        )


# ---------------------------------------------------------------------------
# BARRERA MECANICA INTERACTIVA
# ---------------------------------------------------------------------------

async def _mechanical_barrier_interlock(
    *,
    executor: ThreadPoolExecutor,
) -> None:
    # @TASK: Bloquear el arranque hasta confirmacion manual de secuencia de hardware
    # @INPUT: executor — pool dedicado para delegar input() bloqueante fuera del event loop
    # @OUTPUT: Continuacion del bootstrap o SystemExit(1) si el operador cancela
    # @CONTEXT: Barrera obligatoria que garantiza secuencia de hardware previa al SDK
    # STEP 1: Emitir a stderr la secuencia de hardware requerida en el control remoto
    # STEP 2: Solicitar confirmacion por stdin en hilo separado (sin bloquear event loop)
    # STEP 3: Abortar con exit code 1 si la respuesta no es exactamente 'CONFIRMAR'
    # @SECURITY: Impide que la API de hardware se inicialice fuera de Position Mode
    # @AI_CONTEXT: input() se aisla en executor para mantener el event loop responsivo

    _SEQUENCE_PROMPT = (
        "\n"
        "╔══════════════════════════════════════════════════════════════╗\n"
        "║           BARRERA MECANICA — CONFIRMACION OBLIGATORIA        ║\n"
        "╠══════════════════════════════════════════════════════════════╣\n"
        "║  Ejecutar en el control remoto ANTES de continuar:           ║\n"
        "║                                                              ║\n"
        "║  1. Esperar torque cero (60 s tras encendido)                ║\n"
        "║  2. L1 + A   → Damp (amortiguacion / postura de caida)       ║\n"
        "║  3. L1 + UP  → Bipedestacion                                 ║\n"
        "║  4. L2 + R2  → Develop Mode                                  ║\n"
        "║  5. L2 + A   → Position Mode                                 ║\n"
        "║                                                              ║\n"
        "║  Objetivo DDS unicast: " + _LOCOMOTION_IP.ljust(38) + "║\n"
        "╚══════════════════════════════════════════════════════════════╝\n"
    )

    # STEP 1
    sys.stderr.write(_SEQUENCE_PROMPT)
    sys.stderr.flush()

    loop = asyncio.get_running_loop()

    # STEP 2: Delegar input() al executor para no bloquear el event loop
    try:
        operator_response: str = await loop.run_in_executor(
            executor,
            lambda: input("Escribe CONFIRMAR para continuar o cualquier otra entrada para abortar: "),
        )
    except EOFError:
        LOGGER.critical(
            "[BARRERA] EOF recibido en stdin. Proceso no interactivo detectado. Abortando."
        )
        sys.exit(1)

    # STEP 3
    if operator_response.strip() != "CONFIRMAR":
        LOGGER.critical(
            "[BARRERA] Respuesta '%s' invalida. Arranque abortado.",
            operator_response.strip(),
        )
        sys.exit(1)

    LOGGER.info("[BARRERA] Secuencia de hardware confirmada por el operador.")


# ---------------------------------------------------------------------------
# INICIALIZACION HARDWARE (DDS AISLADA)
# ---------------------------------------------------------------------------

async def _initialize_hardware_api(
    *,
    executor: ThreadPoolExecutor,
) -> RobotHardwareAPI:
    # @TASK: Instanciar RobotHardwareAPI aislando la negociacion DDS del event loop
    # @INPUT: executor — pool para delegar get_instance() bloqueante
    # @OUTPUT: Instancia Singleton validada de RobotHardwareAPI
    # @CONTEXT: get_instance() invoca _default_unitree_client_factory que negocia DDS
    # STEP 1: Ejecutar get_instance() en el executor para aislar bloqueo de DDS
    # STEP 2: Aplicar timeout de 5 s mediante asyncio.wait_for
    # STEP 3: Capturar TimeoutError y abortar con exit code 1
    # @SECURITY: DDS opera sobre LAN air-gapped; timeout previene bloqueo indefinido
    # @AI_CONTEXT: La negociacion DDS con 192.168.123.161 puede durar varios segundos

    loop = asyncio.get_running_loop()
    LOGGER.info(
        "[HARDWARE] Iniciando negociacion DDS con %s (timeout: %.1f s)...",
        _LOCOMOTION_IP,
        _DDS_INIT_TIMEOUT_S,
    )

    # STEP 1 + 2
    try:
        hardware_api: RobotHardwareAPI = await asyncio.wait_for(
            loop.run_in_executor(executor, RobotHardwareAPI.get_instance),
            timeout=_DDS_INIT_TIMEOUT_S,
        )
    except TimeoutError:
        LOGGER.critical(
            "[HARDWARE] Timeout (%.1f s) alcanzado durante negociacion DDS con %s. "
            "Verificar topologia de red y bridge RJ45.",
            _DDS_INIT_TIMEOUT_S,
            _LOCOMOTION_IP,
        )
        # STEP 3
        sys.exit(1)
    except Exception as exc:
        LOGGER.critical(
            "[HARDWARE] Fallo en inicializacion del SDK: %s — %s",
            type(exc).__name__,
            exc,
        )
        sys.exit(1)

    LOGGER.info("[HARDWARE] RobotHardwareAPI inicializado correctamente.")
    return hardware_api


# ---------------------------------------------------------------------------
# VERIFICACION DE ESTADO INICIAL DEL ORCHESTRATOR
# ---------------------------------------------------------------------------

def _assert_orchestrator_idle(orchestrator: TourOrchestrator) -> None:
    # @TASK: Verificar que el TourOrchestrator arranco en estado IDLE
    # @INPUT: orchestrator — instancia recien creada con AsyncEngine
    # @OUTPUT: Log de confirmacion o SystemExit(1) si el estado es inesperado
    # @CONTEXT: python-statemachine AsyncEngine puede diferir la inicializacion
    # STEP 1: Leer configuration[0].id para extraer estado activo actual
    # STEP 2: Comparar contra el identificador canonico 'idle'
    # STEP 3: Abortar si el estado no es IDLE; estado inconsistente es error critico
    # @SECURITY: Un estado no-IDLE en arranque indica corrupcion de la maquina de estados
    # @AI_CONTEXT: configuration es la API estable; current_state esta obsoleta

    # STEP 1
    configuration = orchestrator.configuration
    if not configuration:
        LOGGER.critical(
            "[ORCHESTRATOR] configuration esta vacia tras instanciacion. "
            "AsyncEngine no inicializado correctamente."
        )
        sys.exit(1)

    active_state_id: str = next(iter(configuration)).id

    # STEP 2 + 3
    if active_state_id != "idle":
        LOGGER.critical(
            "[ORCHESTRATOR] Estado inicial inesperado: '%s'. Se requiere 'idle'. "
            "Abortar para evitar comportamiento indeterminado.",
            active_state_id,
        )
        sys.exit(1)

    LOGGER.info(
        "[ORCHESTRATOR] Estado inicial verificado: '%s' (configuration[0].id).",
        active_state_id,
    )


# ---------------------------------------------------------------------------
# SIGNAL HANDLERS
# ---------------------------------------------------------------------------

def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
) -> None:
    # @TASK: Registrar handlers de SIGINT y SIGTERM en el event loop
    # @INPUT: loop — event loop activo; shutdown_event — evento de coordinacion de shutdown
    # @OUTPUT: Signals registrados; cualquier SIGINT/SIGTERM activa shutdown_event
    # @CONTEXT: Los handlers son thread-safe via call_soon_threadsafe
    # STEP 1: Intentar add_signal_handler nativo del loop (Linux/macOS)
    # STEP 2: Aplicar fallback con signal.signal para entornos limitados (Windows)
    # @SECURITY: Garantiza apagado graceful con llamada a Damp() en cualquier ruta de salida
    # @AI_CONTEXT: En el robot (Linux arm64) add_signal_handler es el path esperado

    def _set_shutdown() -> None:
        if not shutdown_event.is_set():
            LOGGER.info("[SIGNAL] Señal de shutdown recibida. Iniciando secuencia de apagado.")
            shutdown_event.set()

    def _fallback_handler(signum: int, frame: Optional[Any]) -> None:
        del signum, frame
        loop.call_soon_threadsafe(_set_shutdown)

    # STEP 1 + 2
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _set_shutdown)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _fallback_handler)


# ---------------------------------------------------------------------------
# TAREA RESIDENTE DEL ORCHESTRATOR
# ---------------------------------------------------------------------------

async def _orchestrator_watchdog(shutdown_event: asyncio.Event) -> None:
    # @TASK: Mantener tarea residente de supervision del orquestador
    # @INPUT: shutdown_event — centinela de coordinacion de shutdown
    # @OUTPUT: Corrutina activa hasta que shutdown_event sea seteado
    # @CONTEXT: Placeholder de ciclo residente; punto de expansion para supervisores
    # STEP 1: Ceder control al event loop cada 250 ms
    # STEP 2: Salir limpiamente cuando shutdown_event este seteado
    # @SECURITY: Sin bloqueos; CPU minimo en espera cooperativa
    # @AI_CONTEXT: Reemplazable por watchers de telemetria o event bus en fase 3

    # STEP 1 + 2
    while not shutdown_event.is_set():
        await asyncio.sleep(0.25)


# ---------------------------------------------------------------------------
# APAGADO GRACEFUL
# ---------------------------------------------------------------------------

async def _graceful_shutdown(
    *,
    api_server: APIServer,
    api_task: asyncio.Task[None],
    orchestrator_task: asyncio.Task[None],
    hardware_api: RobotHardwareAPI,
    vision_processor: VisionProcessor,
    nav_bridge: AsyncNav2Bridge,
) -> None:
    # @TASK: Ejecutar secuencia de apagado seguro en orden estricto
    # @INPUT: Todas las instancias de subsistemas activos del stack
    # @OUTPUT: Subsistemas detenidos; Damp() emitido antes de liberar hardware
    # @CONTEXT: Invocado desde bloque finally de main() ante cualquier ruta de salida
    # STEP 1: Detener APIServer y esperar cierre de task web con timeout
    # STEP 2: Emitir Damp() como primera accion sobre hardware con timeout propio
    # STEP 3: Cerrar VisionProcessor y NavigationManager sin bloqueos
    # STEP 4: Liberar RobotHardwareAPI (executor shutdown)
    # STEP 5: Cancelar tarea residente del orquestador
    # STEP 6: Shutdown de rclpy si el contexto ROS2 sigue activo
    # @SECURITY: Damp() es la primera operacion sobre hardware; nada cierra el SDK antes
    # @AI_CONTEXT: Orden de destruccion es inverso al orden de inicializacion

    LOGGER.info("[SHUTDOWN] Iniciando secuencia de apagado graceful.")

    # STEP 1: APIServer
    try:
        await api_server.stop()
        await asyncio.wait_for(api_task, timeout=_API_STOP_TIMEOUT_S)
    except TimeoutError:
        api_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await api_task
        LOGGER.warning("[SHUTDOWN] APIServer forzado a cancelar tras timeout.")
    except Exception as exc:
        LOGGER.warning("[SHUTDOWN] Fallo al detener APIServer: %s", exc)

    # STEP 2: Damp() — primera operacion sobre hardware
    LOGGER.info("[SHUTDOWN] Emitiendo Damp() al hardware (timeout: %.1f s).", _DAMP_SHUTDOWN_TIMEOUT_S)
    try:
        await asyncio.wait_for(hardware_api.damp(), timeout=_DAMP_SHUTDOWN_TIMEOUT_S)
        LOGGER.info("[SHUTDOWN] Damp() ejecutado correctamente.")
    except TimeoutError:
        LOGGER.error(
            "[SHUTDOWN] Timeout en Damp() durante apagado. "
            "Verificar estado mecanico del robot manualmente."
        )
    except Exception as exc:
        LOGGER.error("[SHUTDOWN] Fallo critico en Damp(): %s — %s", type(exc).__name__, exc)

    # STEP 3: VisionProcessor
    try:
        vision_processor.close()
        LOGGER.info("[SHUTDOWN] VisionProcessor cerrado.")
    except Exception as exc:
        LOGGER.warning("[SHUTDOWN] Fallo cerrando VisionProcessor: %s", exc)

    # STEP 3 (cont): AsyncNav2Bridge
    try:
        await nav_bridge.close()
        LOGGER.info("[SHUTDOWN] AsyncNav2Bridge cerrado.")
    except Exception as exc:
        LOGGER.warning("[SHUTDOWN] Fallo cerrando AsyncNav2Bridge: %s", exc)

    # STEP 4: RobotHardwareAPI executor
    try:
        hardware_api.close()
        LOGGER.info("[SHUTDOWN] RobotHardwareAPI liberado.")
    except Exception as exc:
        LOGGER.warning("[SHUTDOWN] Fallo liberando RobotHardwareAPI: %s", exc)

    # STEP 5: Orchestrator watchdog task
    if not orchestrator_task.done():
        orchestrator_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await orchestrator_task
        LOGGER.info("[SHUTDOWN] Tarea residente del orquestador cancelada.")

    # STEP 6: rclpy
    if rclpy.ok():
        rclpy.shutdown()
        LOGGER.info("[SHUTDOWN] rclpy detenido.")

    LOGGER.info("[SHUTDOWN] Secuencia de apagado completada.")


# ---------------------------------------------------------------------------
# ENTRYPOINT ASINCRONO
# ---------------------------------------------------------------------------

async def main() -> None:
    # @TASK: Ejecutar secuencia de bootstrap completa del sistema HIL Fase 2
    # @INPUT: Variables de entorno del proceso (seteadas por start_robot.sh)
    # @OUTPUT: Stack robotico activo; bloqueado en shutdown_event.wait()
    # @CONTEXT: Punto de entrada central; toda la logica de ciclo de vida pasa por aqui
    # STEP 1: Configurar logging y SDK path antes de cualquier importacion de modulos
    # STEP 2: Inicializar rclpy si no esta activo
    # STEP 3: Crear executor dedicado para operaciones bloqueantes
    # STEP 4: Ejecutar barrera mecanica interactiva antes de cualquier IO de hardware
    # STEP 5: Inicializar RobotHardwareAPI con timeout DDS (aislado en executor)
    # STEP 6: Instanciar ConversationManager, TourOrchestrator y verificar estado IDLE
    # STEP 7: Instanciar VisionProcessor, NavigationManager y APIServer
    # STEP 8: Instalar signal handlers y lanzar tareas residentes
    # STEP 9: Bloquear en shutdown_event; liberar en apagado graceful desde finally
    # @SECURITY: Barrera (STEP 4) garantiza Position Mode activo antes del STEP 5
    # @AI_CONTEXT: ThreadPoolExecutor con max_workers=2: 1 para DDS init, 1 para input()

    # STEP 1
    _configure_base_logging()
    _configure_unitree_sdk_path()

    LOGGER.info("[BOOT] Robot Humanoide — HIL Fase 2 — Inicializando.")

    # STEP 2
    if not rclpy.ok():
        rclpy.init(args=None)
        LOGGER.info("[BOOT] rclpy inicializado.")

    # STEP 3: Executor dedicado — workers separados para input() y DDS init
    boot_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="boot-blocking")

    # STEP 4: Barrera mecanica — debe completarse antes de cualquier contacto con el SDK
    await _mechanical_barrier_interlock(executor=boot_executor)

    # STEP 5: Inicializacion hardware con DDS aislada
    hardware_api = await _initialize_hardware_api(executor=boot_executor)

    # El executor de boot ya no es necesario a partir de este punto
    boot_executor.shutdown(wait=False)

    # STEP 6: Subsistemas de percepcion
    camera_model = CameraModel(
        camera_matrix=np.eye(3, dtype=np.float64),
        distortion_coefficients=np.zeros((5, 1), dtype=np.float64),
    )
    vision_processor = VisionProcessor(
        camera_model=camera_model,
        tag_size_m=0.16,
    )

    # STEP 6b: AsyncNav2Bridge — reemplaza NavigationManager legacy
    nav_bridge = AsyncNav2Bridge()
    LOGGER.info("[BOOT] Iniciando AsyncNav2Bridge (await start)...")
    await nav_bridge.start()
    LOGGER.info("[BOOT] AsyncNav2Bridge activo.")

    # STEP 6c: Composicion de capas de control NLP
    conversation_manager = ConversationManager(
        cloud_strategy=CloudNLPPipeline(timeout_s=1.0),
        local_strategy=LocalNLPPipeline(model_name="ollama-local"),
    )

    # STEP 6d: TourOrchestrator con las 4 dependencias obligatorias
    orchestrator = TourOrchestrator(
        hardware_api=hardware_api,
        nav_bridge=nav_bridge,
        conversation_manager=conversation_manager,
        vision_processor=vision_processor,
    )

    # Verificacion de estado inicial antes de exponer al exterior
    _assert_orchestrator_idle(orchestrator)

    # STEP 7: API REST
    api_server = APIServer(orchestrator=orchestrator)

    # STEP 8: Signal handlers y event de coordinacion
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop, shutdown_event)

    LOGGER.info("[BOOT] Stack completo inicializado. Lanzando tareas residentes.")

    api_task: asyncio.Task[None] = asyncio.create_task(
        api_server.start(), name="api-server"
    )
    orchestrator_task: asyncio.Task[None] = asyncio.create_task(
        _orchestrator_watchdog(shutdown_event), name="orchestrator-watchdog"
    )

    # STEP 9: Bloqueo cooperativo hasta señal de apagado
    try:
        await shutdown_event.wait()
    finally:
        await _graceful_shutdown(
            api_server=api_server,
            api_task=api_task,
            orchestrator_task=orchestrator_task,
            hardware_api=hardware_api,
            vision_processor=vision_processor,
            nav_bridge=nav_bridge,
        )


# ---------------------------------------------------------------------------
# BLOQUE DE EJECUCION DIRECTO
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # @TASK: Lanzar corrutina main en event loop gestionado
    # @INPUT: Sin parametros; sys.argv no utilizado en esta fase
    # @OUTPUT: Proceso en ejecucion hasta SIGINT/SIGTERM o fallo critico
    # @CONTEXT: Unico punto de invocacion; start_robot.sh ejecuta este modulo directamente
    # STEP 1: Suprimir traza de KeyboardInterrupt para salida limpia en terminal
    # STEP 2: Ejecutar asyncio.run con main() como corrutina raiz
    # @SECURITY: KeyboardInterrupt suprimida; la señal real SIGINT es capturada por handler
    # @AI_CONTEXT: asyncio.run crea un nuevo event loop; no usar loop.run_until_complete
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())