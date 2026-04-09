#!/usr/bin/env bash

: <<'DOC'
@TASK: Orquestar el arranque E2E del MVP OttoGuide en Companion PC.
@INPUT: Stack local de ROS 2, entorno virtual Python y script HIL de navegacion.
@OUTPUT: Nav2 y backend FastAPI/FSM activos con ROBOT_MODE=real.
@CONTEXT: Supervisor maestro para despliegue persistente del MVP.
@SECURITY: Limpieza estricta de procesos en SIGINT/SIGTERM para evitar huérfanos.
STEP [1]: Validar dependencias locales y activar entorno Python.
STEP [2]: Lanzar la navegacion HIL en background.
STEP [3]: Esperar 10 segundos para convergencia de Nav2/AMCL.
STEP [4]: Lanzar el backend FastAPI/FSM con ROBOT_MODE=real.
STEP [5]: Propagar señales y cerrar toda la cadena de procesos.
DOC

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
NAV_SCRIPT="${SCRIPT_DIR}/hil_start_navigation.sh"
VENV_ACTIVATE="${PROJECT_ROOT}/.venv/bin/activate"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
SDK_PATH="${PROJECT_ROOT}/libs/unitree_sdk2_python-master"
API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"

if [[ ! -f "${NAV_SCRIPT}" ]]; then
  echo "@OUTPUT: ERROR falta hil_start_navigation.sh en ${NAV_SCRIPT}"
  exit 1
fi

if [[ ! -f "${VENV_ACTIVATE}" ]]; then
  echo "@OUTPUT: ERROR falta el entorno virtual en ${VENV_ACTIVATE}"
  exit 1
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "@OUTPUT: ERROR interprete de venv no ejecutable en ${VENV_PYTHON}"
  exit 1
fi

if [[ -f "${ROS_SETUP}" ]]; then
  source "${ROS_SETUP}"
fi

source "${VENV_ACTIVATE}"

export ROBOT_MODE="real"
export RMW_IMPLEMENTATION="rmw_cyclonedds_cpp"
export CYCLONEDDS_URI="file://${PROJECT_ROOT}/config/cyclonedds.xml"
export PYTHONPATH="${PROJECT_ROOT}:${PROJECT_ROOT}/src:${SDK_PATH}:${PYTHONPATH:-}"

NAV_PID=""
API_PID=""

cleanup() {
  local exit_code="${1:-0}"

  trap - INT TERM EXIT

  if [[ -n "${API_PID}" ]] && kill -0 "${API_PID}" >/dev/null 2>&1; then
    kill -TERM "${API_PID}" >/dev/null 2>&1 || true
    wait "${API_PID}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${NAV_PID}" ]] && kill -0 "${NAV_PID}" >/dev/null 2>&1; then
    kill -TERM "${NAV_PID}" >/dev/null 2>&1 || true
    wait "${NAV_PID}" >/dev/null 2>&1 || true
  fi

  exit "${exit_code}"
}

on_signal() {
  cleanup 130
}

trap on_signal INT TERM
trap 'cleanup $?' EXIT

bash "${NAV_SCRIPT}" &
NAV_PID="$!"

sleep 10

"${VENV_PYTHON}" -m uvicorn main:create_app --factory --host "${API_HOST}" --port "${API_PORT}" &
API_PID="$!"

wait -n "${NAV_PID}" "${API_PID}"
EXIT_CODE="$?"

cleanup "${EXIT_CODE}"