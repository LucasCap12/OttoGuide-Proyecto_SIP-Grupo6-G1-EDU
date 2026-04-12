"""
@TASK: Ejecutar health check SRE no bloqueante para operacion HIL de OttoGuide.
@INPUT: Endpoint DDS 192.168.123.161, puerto Ollama 0.0.0.0:11434, dispositivos ALSA del host.
@OUTPUT: Exit 0 si todas las validaciones son exitosas; Exit 1 ante cualquier falla.
@CONTEXT: Barrera tecnica de pre-vuelo alineada al RUNBOOK_STARTUP_RC1 antes del arranque core.
@SECURITY: Solo lecturas locales y conectividad basica; sin comandos de locomocion ni mutacion de estado.
STEP 1: Verificar escucha local de Ollama en 0.0.0.0:11434 y conectividad TCP localhost:11434.
STEP 2: Verificar presencia de dispositivos ALSA de captura y reproduccion.
STEP 3: Verificar conectividad de red basica hacia endpoint DDS 192.168.123.161.
STEP 4: Retornar codigo de salida estricto para gate de preflight.
"""

from __future__ import annotations

import asyncio
from typing import Final

OLLAMA_PORT: Final[int] = 11434
OLLAMA_BIND_EXPECTED: Final[str] = "0.0.0.0"
DDS_ENDPOINT: Final[str] = "192.168.123.161"


def _info(message: str) -> None:
    """
    @TASK: Emitir log informativo estandar para SRE health check.
    @INPUT: message — texto del evento de validacion.
    @OUTPUT: Linea en stdout con prefijo [INFO].
    @CONTEXT: Formato de salida consumible por gates de CI/CD y operadores HIL.
    @SECURITY: No imprime secretos ni variables sensibles.
    """
    print(f"[INFO] {message}")


def _error(message: str) -> None:
    """
    @TASK: Emitir log de error estandar para SRE health check.
    @INPUT: message — texto de la falla de validacion.
    @OUTPUT: Linea en stdout con prefijo [ERROR].
    @CONTEXT: Señal de NO-GO para preflight_check.sh.
    @SECURITY: Solo diagnostico tecnico sin datos sensibles.
    """
    print(f"[ERROR] {message}")


async def _run_command(*args: str, timeout_s: float = 5.0) -> tuple[int, str, str]:
    """
    @TASK: Ejecutar comando del sistema de forma asincrona y acotada por timeout.
    @INPUT: args — comando/argumentos; timeout_s — ventana maxima de ejecucion.
    @OUTPUT: Tupla (returncode, stdout, stderr).
    @CONTEXT: Utilidad compartida para checks de red y audio sin bloquear el event loop.
    @SECURITY: No usa shell=True; evita expansion de comandos arbitrarios.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", f"comando no encontrado: {args[0]}"

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return 124, "", f"timeout ejecutando {' '.join(args)}"

    return process.returncode, stdout_bytes.decode("utf-8", errors="ignore"), stderr_bytes.decode("utf-8", errors="ignore")


async def check_ollama_port() -> bool:
    """
    @TASK: Verificar disponibilidad del puerto local de Ollama y bind esperado en 0.0.0.0:11434.
    @INPUT: Herramientas de sistema ss/netstat y socket TCP localhost:11434.
    @OUTPUT: True si escucha en :11434 y acepta conexion TCP local; False en caso contrario.
    @CONTEXT: Validacion de capa LLM local previa al arranque de backend interactivo HIL.
    @SECURITY: Solo comprobacion local de red; sin solicitudes de inferencia ni envio de prompts.
    STEP 1: Comprobar socket listener en :11434 por ss o netstat.
    STEP 2: Validar bind 0.0.0.0 (o wildcard equivalente).
    STEP 3: Validar handshake TCP a 127.0.0.1:11434.
    """
    bind_ok = False
    rc, out, err = await _run_command("ss", "-ltn", timeout_s=3.0)
    listen_dump = out

    if rc == 127:
        rc, out, err = await _run_command("netstat", "-ltn", timeout_s=3.0)
        listen_dump = out

    if rc != 0:
        _error(f"No se pudo inspeccionar sockets locales: {err.strip() or 'sin detalle'}")
        return False

    lines = [line.strip() for line in listen_dump.splitlines() if f":{OLLAMA_PORT}" in line]
    if not lines:
        _error(f"Ollama no escucha en puerto local {OLLAMA_PORT}")
        return False

    for line in lines:
        if f"{OLLAMA_BIND_EXPECTED}:{OLLAMA_PORT}" in line or f"*:{OLLAMA_PORT}" in line:
            bind_ok = True
            break

    if not bind_ok:
        _error(f"Ollama no esta ligado a {OLLAMA_BIND_EXPECTED}:{OLLAMA_PORT}")
        return False

    try:
        connect_coro = asyncio.open_connection("127.0.0.1", OLLAMA_PORT)
        reader, writer = await asyncio.wait_for(connect_coro, timeout=2.0)
        writer.close()
        await writer.wait_closed()
        _info(f"Ollama operativo en {OLLAMA_BIND_EXPECTED}:{OLLAMA_PORT}")
        _ = reader
        return True
    except Exception as exc:
        _error(f"Ollama no acepta conexion TCP local en 127.0.0.1:{OLLAMA_PORT}: {exc}")
        return False


async def check_alsa_devices() -> bool:
    """
    @TASK: Verificar presencia de dispositivos ALSA de captura y reproduccion.
    @INPUT: Comandos arecord -l y aplay -l.
    @OUTPUT: True si ambos comandos detectan hardware de audio; False en caso contrario.
    @CONTEXT: Validacion de capa audio para flujo Mic->STT local->TTS local.
    @SECURITY: Solo consulta inventario ALSA; no captura ni reproduce audio.
    STEP 1: Ejecutar arecord -l para tarjetas de entrada.
    STEP 2: Ejecutar aplay -l para tarjetas de salida.
    STEP 3: Confirmar presencia de al menos una card en cada direccion.
    """
    rec_rc, rec_out, rec_err = await _run_command("arecord", "-l", timeout_s=4.0)
    play_rc, play_out, play_err = await _run_command("aplay", "-l", timeout_s=4.0)

    if rec_rc != 0:
        _error(f"ALSA captura no disponible: {rec_err.strip() or 'arecord fallo'}")
        return False
    if play_rc != 0:
        _error(f"ALSA reproduccion no disponible: {play_err.strip() or 'aplay fallo'}")
        return False

    has_capture = "card" in rec_out.lower()
    has_playback = "card" in play_out.lower()

    if not has_capture:
        _error("ALSA captura sin dispositivos detectados")
        return False
    if not has_playback:
        _error("ALSA reproduccion sin dispositivos detectados")
        return False

    _info("ALSA operativo: captura y reproduccion detectadas")
    return True


async def check_dds_ping() -> bool:
    """
    @TASK: Verificar conectividad basica de red hacia endpoint DDS de locomocion.
    @INPUT: Comando ping -c 1 -W 2 192.168.123.161.
    @OUTPUT: True si el endpoint responde; False si no hay conectividad.
    @CONTEXT: Comprobacion de enlace de red HIL sin enviar comandos de movimiento.
    @SECURITY: Solo ICMP de diagnostico; no interactua con APIs de locomocion.
    """
    rc, _, err = await _run_command("ping", "-c", "1", "-W", "2", DDS_ENDPOINT, timeout_s=5.0)
    if rc == 0:
        _info(f"Conectividad DDS OK hacia {DDS_ENDPOINT}")
        return True

    _error(f"Sin conectividad DDS hacia {DDS_ENDPOINT}: {err.strip() or 'ping fallo'}")
    return False


async def run_checks() -> int:
    """
    @TASK: Orquestar validaciones SRE en paralelo y consolidar resultado final.
    @INPUT: Checks asincronos de Ollama, ALSA y conectividad DDS.
    @OUTPUT: 0 si todos los checks pasan; 1 si al menos uno falla.
    @CONTEXT: Punto de integracion con preflight_check.sh como barrera final.
    @SECURITY: Falla cerrada; cualquier check no exitoso bloquea arranque.
    STEP 1: Ejecutar checks concurrentes para minimizar latencia operativa.
    STEP 2: Contabilizar fallos y emitir veredicto final [INFO]/[ERROR].
    """
    results = await asyncio.gather(
        check_ollama_port(),
        check_alsa_devices(),
        check_dds_ping(),
    )

    if all(results):
        _info("Health check SRE finalizado en estado GO")
        return 0

    _error("Health check SRE finalizado en estado NO-GO")
    return 1


def main() -> int:
    """
    @TASK: Ejecutar entrypoint sincrono del health check asincrono.
    @INPUT: Sin argumentos CLI.
    @OUTPUT: Exit code compatible con barreras shell (0/1).
    @CONTEXT: Invocado por preflight_check.sh y operadores SRE.
    @SECURITY: Manejo controlado de excepciones para evitar stack traces no estructurados.
    """
    try:
        return asyncio.run(run_checks())
    except Exception as exc:
        _error(f"Fallo inesperado en health check: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
