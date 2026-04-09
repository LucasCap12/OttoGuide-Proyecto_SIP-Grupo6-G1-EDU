#!/bin/bash
set -euo pipefail

: <<'DOC'
@TASK: Iniciar navegacion autonoma fisica HIL con mapa pre-generado y reloj real.
@INPUT: ROS 2 Humble activo, mapa en codigo ottoguide/maps/uade_physical_map.yaml y drivers de sensores disponibles.
@OUTPUT: Drivers fisicos y stack Nav2/AMCL levantados con use_sim_time:=false.
@CONTEXT: Orquestador operativo para fase de navegacion autonoma fisica en Companion PC.
@SECURITY: No publica comandos manuales de locomocion; solo habilita infraestructura de navegacion.
STEP [1]: Cargar setup de ROS 2 y workspace local si existe.
STEP [2]: Verificar existencia del mapa fisico requerido.
STEP [3]: Levantar Livox MID360 y RealSense.
STEP [4]: Levantar Nav2 bringup con AMCL usando el mapa fisico y use_sim_time:=false.
STEP [5]: Mantener procesos en foreground con limpieza segura por senales.
DOC

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MAP_PATH="${PROJECT_ROOT}/maps/uade_physical_map.yaml"

source /opt/ros/humble/setup.bash
if [ -f "${PROJECT_ROOT}/install/setup.bash" ]; then
  source "${PROJECT_ROOT}/install/setup.bash"
fi

if [ ! -f "${MAP_PATH}" ]; then
  echo "@OUTPUT: ERROR mapa no encontrado en ${MAP_PATH}"
  exit 1
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

ros2 launch nav2_bringup navigation_launch.py map:="${MAP_PATH}" use_sim_time:=false autostart:=true &
PIDS+=("$!")

echo "@OUTPUT: HIL navigation stack iniciado. livox=${PIDS[0]} realsense=${PIDS[1]} nav2=${PIDS[2]}"
echo "@CONTEXT: FSM puede despachar metas 2D sobre el mapa fisico cargado."

wait
