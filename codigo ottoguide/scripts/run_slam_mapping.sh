#!/usr/bin/env bash
set -euo pipefail

: <<'DOC'
@TASK: Orquestar ejecución asíncrona de slam_toolbox en modo online_async para mapeo SITL.
@INPUT: Entorno ROS 2 Humble activo con telemetría en tópicos /scan y /odom.
@OUTPUT: Proceso slam_toolbox en background y guía operativa para persistencia de mapa.
@CONTEXT: Script SRE de mapeo definitivo para navegación OttoGuide.
@SECURITY: Cierre controlado de proceso mediante trap para evitar procesos huérfanos.
STEP [1]: Lanzar slam_toolbox en background con parámetros online_async y remaps explícitos.
STEP [2]: Mantener sesión activa hasta interrupción del operador.
STEP [3]: Exponer instrucción operativa de guardado de mapa sin comentarios de línea.
DOC

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

LAUNCH_CMD=(
  ros2 launch slam_toolbox online_async_launch.py
  use_sim_time:=true
  scan_topic:=/scan
)

"${LAUNCH_CMD[@]}" &
SLAM_PID=$!

cleanup() {
  if kill -0 "${SLAM_PID}" >/dev/null 2>&1; then
    kill "${SLAM_PID}" >/dev/null 2>&1 || true
    wait "${SLAM_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

MAP_SAVE_CMD="ros2 run nav2_map_server map_saver_cli -f \"${PROJECT_ROOT}/maps/uade_3d_sim\""

echo "@OUTPUT: slam_toolbox online_async iniciado con PID=${SLAM_PID}"
echo "@CONTEXT: Verificar que el bridge publique /scan y /odom antes de iniciar barrido completo"
echo "@TASK: Instrucción guardado de mapa"
echo "${MAP_SAVE_CMD}"

wait "${SLAM_PID}"
