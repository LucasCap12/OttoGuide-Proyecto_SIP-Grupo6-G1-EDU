#!/bin/bash

set -euo pipefail

# Technical defaults for companion PC target; can be overridden by CLI args.
ROBOT_USER_DEFAULT="unitree"
ROBOT_IP_DEFAULT="192.168.123.161"
PROJECT_NAME_DEFAULT="OttoGuide-Proyecto_SIP-Grupo6-UADE"
ROBOT_DST_DEFAULT="/home/${ROBOT_USER_DEFAULT}/${PROJECT_NAME_DEFAULT}"

ROBOT_USER="${1:-${ROBOT_USER_DEFAULT}}"
ROBOT_IP="${2:-${ROBOT_IP_DEFAULT}}"
ROBOT_DST="${3:-${ROBOT_DST_DEFAULT}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RSYNC_EXCLUDES=(
  "--exclude=.venv/"
  "--exclude=__pycache__/"
  "--exclude=.git/"
  "--exclude=tests/"
  "--exclude=.pytest_cache/"
)

echo "[INFO] Deploy source: ${PROJECT_ROOT}/"
echo "[INFO] Deploy target: ${ROBOT_USER}@${ROBOT_IP}:${ROBOT_DST}/"

# -a keeps permissions/timestamps/symlinks; -z compresses transfer payload;
# --delete keeps remote tree aligned with local source after exclusions.
# -e ssh forces encrypted transport over the LAN air-gapped channel.
rsync -az --delete \
  "${RSYNC_EXCLUDES[@]}" \
  -e ssh \
  "${PROJECT_ROOT}/" \
  "${ROBOT_USER}@${ROBOT_IP}:${ROBOT_DST}/"

echo "[INFO] Deploy completed successfully."