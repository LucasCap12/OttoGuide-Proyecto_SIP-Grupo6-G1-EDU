"""@TASK: Validar prerequisitos del entorno antes del arranque del MVP OttoGuide.
@INPUT: Red local del robot, demonio Ollama y mapa fisico compilado.
@OUTPUT: Exit 0 si el entorno es viable; exit 1 si cualquier validacion falla.
@CONTEXT: CLI aislada de la FSM para preflight operativo SRE.
@SECURITY: Solo lecturas locales y ping de alcance; sin dependencias pesadas.
STEP [1]: Ejecutar ping al endpoint DDS 192.168.123.161.
STEP [2]: Verificar escucha local del puerto 11434 mediante sockets.
STEP [3]: Confirmar existencia del mapa maps/uade_physical_map.yaml.
STEP [4]: Imprimir diagnostico directo por stdout y retornar codigo de salida.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path


ROBOT_DDS_ENDPOINT = "192.168.123.161"
OLLAMA_HOST = "localhost"
OLLAMA_PORT = 11434


def check_dds_ping() -> tuple[bool, str]:
    """Verifica conectividad basica contra el endpoint DDS del robot."""
    command = ["ping", "-c", "1", "-W", "2", ROBOT_DDS_ENDPOINT]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except FileNotFoundError:
        return False, "ping no esta disponible en el sistema"
    except subprocess.TimeoutExpired:
        return False, f"timeout ejecutando ping hacia {ROBOT_DDS_ENDPOINT}"

    if completed.returncode == 0:
        return True, f"DDS reachable via ping to {ROBOT_DDS_ENDPOINT}"
    return False, f"DDS ping fallo hacia {ROBOT_DDS_ENDPOINT}"


def check_ollama_port() -> tuple[bool, str]:
    """Verifica escucha local en el puerto de Ollama mediante sockets."""
    try:
        with socket.create_connection((OLLAMA_HOST, OLLAMA_PORT), timeout=2):
            return True, f"Ollama escuchando en {OLLAMA_HOST}:{OLLAMA_PORT}"
    except OSError as exc:
        return False, f"Ollama no responde en {OLLAMA_HOST}:{OLLAMA_PORT}: {exc}"


def check_map_file() -> tuple[bool, str]:
    """Confirma la presencia del mapa fisico requerido."""
    project_root = Path(__file__).resolve().parent.parent
    map_path = project_root / "maps" / "uade_physical_map.yaml"
    if map_path.is_file():
        return True, f"Mapa encontrado en {map_path}"
    return False, f"Mapa ausente en {map_path}"


def main() -> int:
    """Ejecuta la bateria de validaciones y retorna codigo de salida."""
    checks = [check_dds_ping(), check_ollama_port(), check_map_file()]
    failures = 0

    for passed, message in checks:
        print(message)
        if not passed:
            failures += 1

    if failures == 0:
        print("Health check OK")
        return 0

    print("Health check FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())