#!/bin/bash
set -euo pipefail

: <<'DOC'
@TASK: Persistir mapa fisico generado por slam_toolbox mediante nav2_map_server.
@INPUT: Nodo slam_toolbox activo y topicos de mapa disponibles en ROS 2 Humble.
@OUTPUT: Archivos YAML y PGM en ruta absoluta codigo ottoguide/maps/uade_physical_map.
@CONTEXT: Script operativo para cierre de sesion de mapeo HIL.
@SECURITY: Falla de forma explicita si el servicio de guardado no responde o la ruta no es escribible.
STEP [1]: Cargar setup ROS 2 y workspace local.
STEP [2]: Construir ruta absoluta destino y asegurar directorio existente.
STEP [3]: Ejecutar map_saver_cli con timeout de seguridad.
DOC

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MAP_BASENAME="${PROJECT_ROOT}/maps/uade_physical_map"

source /opt/ros/humble/setup.bash
if [ -f "${PROJECT_ROOT}/install/setup.bash" ]; then
  source "${PROJECT_ROOT}/install/setup.bash"
fi

mkdir -p "$(dirname "${MAP_BASENAME}")"

timeout 60 ros2 run nav2_map_server map_saver_cli -f "${MAP_BASENAME}"

echo "@OUTPUT: Mapa guardado en ${MAP_BASENAME}.yaml y ${MAP_BASENAME}.pgm"
