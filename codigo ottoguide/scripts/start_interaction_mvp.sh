#!/usr/bin/env bash
# @TASK: Orquestar modo interactivo MVP con LLM local y backend FastAPI en segundo plano
# @INPUT: Entorno virtual Python, ROS setup opcional, endpoint Ollama local y variables de runtime
# @OUTPUT: Proceso uvicorn activo con ROBOT_MODE=real y NAV_BRIDGE_ACTIVE=false hasta recibir senal
# @CONTEXT: Entry point de operacion HIL interactiva (sin puente de navegacion activo)
# @SECURITY: Verifica salud del LLM antes de iniciar; trap de SIGINT/SIGTERM para limpieza deterministica
# STEP 1: Exportar variables de modo interactivo y validar Ollama
# STEP 2: Levantar FastAPI + manager conversacional en background
# STEP 3: Propagar senales y finalizar procesos hijos sin dejar huerfanos

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
VENV_ACTIVATE="${PROJECT_ROOT}/.venv/bin/activate"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"
OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:3b}"

if [[ ! -f "${VENV_ACTIVATE}" ]]; then
  echo "@OUTPUT: ERROR entorno virtual no encontrado en ${VENV_ACTIVATE}" >&2
  exit 1
fi

if [[ -f "${ROS_SETUP}" ]]; then
  # shellcheck source=/dev/null
  source "${ROS_SETUP}"
fi

# shellcheck source=/dev/null
source "${VENV_ACTIVATE}"

export ROBOT_MODE="real"
export NAV_BRIDGE_ACTIVE="false"
export OLLAMA_HOST
export OLLAMA_MODEL

if ! curl -fsS "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
  echo "@OUTPUT: ERROR LLM local no saludable en ${OLLAMA_HOST}" >&2
  exit 1
fi

API_PID=""

cleanup() {
  local exit_code="${1:-0}"
  trap - INT TERM EXIT

  if [[ -n "${API_PID}" ]] && kill -0 "${API_PID}" >/dev/null 2>&1; then
    kill -TERM "${API_PID}" >/dev/null 2>&1 || true
    wait "${API_PID}" >/dev/null 2>&1 || true
  fi

  exit "${exit_code}"
}

on_signal() {
  cleanup 130
}

trap on_signal INT TERM
trap 'cleanup $?' EXIT

"${VENV_PYTHON}" -m uvicorn main:create_app --factory --host "${API_HOST}" --port "${API_PORT}" &
API_PID="$!"

wait "${API_PID}"
EXIT_CODE="$?"
cleanup "${EXIT_CODE}"
