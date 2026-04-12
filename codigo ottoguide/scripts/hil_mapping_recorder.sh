#!/usr/bin/env bash
# @TASK: Grabar rosbag2 pasivo de alta densidad para mapeo HIL sin ejecutar navegacion
# @INPUT: ROS 2 activo, topicos LiDAR/IMU/camara/tf/odom disponibles, directorio de salida opcional
# @OUTPUT: Bag rosbag2 almacenado con topicos crudos para postproceso de mapeo
# @CONTEXT: Captura de datos HIL previa a calibracion AMCL/Nav2 en Companion PC
# @SECURITY: Solo suscripcion pasiva de topicos; no publica comandos ni muta estado de navegacion
# STEP 1: Cargar entorno ROS 2 y preparar directorio de salida
# STEP 2: Iniciar ros2 bag record sobre topicos de alta densidad requeridos

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
OUT_DIR="${1:-${PROJECT_ROOT}/logs/bags}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BAG_PATH="${OUT_DIR}/hil_mapping_${STAMP}"

mkdir -p "${OUT_DIR}"

if [[ -f "${ROS_SETUP}" ]]; then
  # shellcheck source=/dev/null
  source "${ROS_SETUP}"
fi

exec ros2 bag record \
  --storage mcap \
  --output "${BAG_PATH}" \
  --max-cache-size 0 \
  /livox/lidar \
  /livox/imu \
  /camera/color/image_raw \
  /camera/depth/image_rect_raw \
  /tf \
  /robot_state/odom
