#!/bin/bash
set -euo pipefail

: <<'DOC'
@TASK: Wrapper operativo para calibrar un waypoint logico usando pose AMCL.
@INPUT: ID de nodo logico como parametro posicional y entorno ROS 2 disponible.
@OUTPUT: Ejecucion de hil_waypoint_calibrator.py con --node-id correspondiente.
@CONTEXT: Flujo rapido para operador en laboratorio durante calibracion HIL.
@SECURITY: Falla temprano si faltan parametros o setup ROS 2.
STEP [1]: Validar parametro posicional node-id.
STEP [2]: Cargar ROS 2 Humble y workspace local si existe.
STEP [3]: Ejecutar calibrador Python para mutacion atomica del JSON.
DOC

if [ "${1:-}" = "" ]; then
  echo "@OUTPUT: ERROR falta parametro node-id. Uso: bash scripts/hil_calibrate_node.sh <I|1|2|3|F>"
  exit 1
fi

NODE_ID="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CALIBRATOR="${SCRIPT_DIR}/hil_waypoint_calibrator.py"

if [ ! -f "/opt/ros/humble/setup.bash" ]; then
  echo "@OUTPUT: ERROR ROS 2 Humble no encontrado en /opt/ros/humble/setup.bash"
  exit 1
fi

source /opt/ros/humble/setup.bash
if [ -f "${PROJECT_ROOT}/install/setup.bash" ]; then
  source "${PROJECT_ROOT}/install/setup.bash"
fi

python3 "${CALIBRATOR}" --node-id "${NODE_ID}"
