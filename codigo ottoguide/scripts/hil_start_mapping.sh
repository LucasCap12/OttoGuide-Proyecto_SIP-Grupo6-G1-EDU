#!/bin/bash
set -euo pipefail

: <<'DOC'
@TASK: Orquestar inicio de mapeo fisico HIL en Companion PC con sensores reales del Unitree G1.
@INPUT: Entorno ROS 2 Humble disponible con drivers Livox MID360 y RealSense instalados.
@OUTPUT: Drivers de sensores y slam_toolbox online_async ejecutandose en paralelo para generar mapa.
@CONTEXT: Flujo de pre-configuracion para mapeo fisico teleoperado por joystick nativo del G1.
@SECURITY: No inicia teleoperacion por teclado ni publica comandos de movimiento.
STEP [1]: Cargar setup ROS 2 y workspace local.
STEP [2]: Levantar driver Livox MID360.
STEP [3]: Levantar driver RealSense.
STEP [4]: Levantar slam_toolbox en modo online_async con reloj real.
STEP [5]: Mantener sesion viva y cerrar procesos hijos de forma segura al terminar.
DOC

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

source /opt/ros/humble/setup.bash
if [ -f "${PROJECT_ROOT}/install/setup.bash" ]; then
  source "${PROJECT_ROOT}/install/setup.bash"
fi

PIDS=()

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
      wait "${pid}" >/dev/null 2>&1 || true
    fi
  done
}

trap cleanup EXIT INT TERM

ros2 launch livox_ros_driver2 msg_MID360_launch.py &
PIDS+=("$!")

ros2 launch realsense2_camera rs_launch.py enable_depth:=true enable_color:=true pointcloud.enable:=true &
PIDS+=("$!")

ros2 launch slam_toolbox online_async_launch.py use_sim_time:=false scan_topic:=/scan &
PIDS+=("$!")

echo "@OUTPUT: HIL mapping stack iniciado. livox=${PIDS[0]} realsense=${PIDS[1]} slam=${PIDS[2]}"
echo "@CONTEXT: Teleoperar el G1 con joystick nativo para recorrer el entorno durante el mapeo."

wait
