#!/usr/bin/env bash
# @TASK: Orquestar arranque E2E HIL del MVP OttoGuide con barreras previas y contingencia de cierre.
# @INPUT: scripts/preflight_check.sh, scripts/verify_remote_env.sh, entorno Python del proyecto, API local.
# @OUTPUT: Backend FastAPI activo en background con PIDs registrados; cierre seguro ante señales.
# @CONTEXT: Paso 8 del RUNBOOK_STARTUP_RC1 (Arranque Maestro) con integracion de pasos 5 y 6.
# @SECURITY: Trap SIGINT/SIGTERM dispara POST /emergency antes de terminar procesos para fail-safe mecanico.
# STEP 1: Ejecutar preflight_check.sh y verify_remote_env.sh en secuencia estricta.
# STEP 2: Exportar ROBOT_MODE=real y NAV_BRIDGE_ACTIVE=true.
# STEP 3: Levantar backend FastAPI (main.py) en background y registrar PID.
# STEP 4: En señales, invocar /emergency y luego finalizar procesos hijos.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PREFLIGHT_SCRIPT="${SCRIPT_DIR}/preflight_check.sh"
VERIFY_SCRIPT="${SCRIPT_DIR}/verify_remote_env.sh"
VENV_ACTIVATE="${PROJECT_ROOT}/.venv/bin/activate"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"
EMERGENCY_URL="http://127.0.0.1:${API_PORT}/emergency"

BACKEND_PID=""

# @TASK: Ejecutar barreras operativas previas obligatorias del runbook.
# @INPUT: preflight_check.sh y verify_remote_env.sh.
# @OUTPUT: Continua solo si ambos scripts retornan exit 0.
# @CONTEXT: Control de GO/NO-GO antes de iniciar backend.
# @SECURITY: Bloqueo estricto ante fallo en cualquier barrera.
if [[ ! -f "${PREFLIGHT_SCRIPT}" ]]; then
  echo "[ERROR] Falta script: ${PREFLIGHT_SCRIPT}" >&2
  exit 1
fi
if [[ ! -f "${VERIFY_SCRIPT}" ]]; then
  echo "[ERROR] Falta script: ${VERIFY_SCRIPT}" >&2
  exit 1
fi

bash "${PREFLIGHT_SCRIPT}"
bash "${VERIFY_SCRIPT}"

# @TASK: Preparar entorno de ejecucion para modo HIL real con bridge de navegacion activo.
# @INPUT: ROS setup opcional y entorno virtual Python.
# @OUTPUT: Variables exportadas ROBOT_MODE/NAV_BRIDGE_ACTIVE disponibles para main.py.
# @CONTEXT: Requisito operativo del Paso 8 del runbook.
# @SECURITY: No sobreescribe variables de red de locomocion ni modifica capa bloqueada.
if [[ -f "${ROS_SETUP}" ]]; then
  # shellcheck source=/dev/null
  source "${ROS_SETUP}"
fi

if [[ ! -f "${VENV_ACTIVATE}" ]] || [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[ERROR] Entorno virtual incompleto en ${PROJECT_ROOT}/.venv" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${VENV_ACTIVATE}"

export ROBOT_MODE="real"
export NAV_BRIDGE_ACTIVE="true"

# @TASK: Ejecutar contingencia de parada ante señal del sistema operativo.
# @INPUT: Señales SIGINT/SIGTERM y PID del backend activo.
# @OUTPUT: POST /emergency enviado; procesos hijos finalizados; exit controlado.
# @CONTEXT: Paso 10 del runbook para parada segura.
# @SECURITY: El trigger de emergencia se envía antes del kill para priorizar transición EMERGENCY.
cleanup() {
  local exit_code="${1:-0}"

  trap - INT TERM EXIT

  curl -sS -X POST "${EMERGENCY_URL}" \
    -H "Content-Type: application/json" \
    -d '{"reason":"master_trap_signal"}' >/dev/null 2>&1 || true

  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
    kill -TERM "${BACKEND_PID}" >/dev/null 2>&1 || true
    wait "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi

  exit "${exit_code}"
}

on_signal() {
  cleanup 130
}

trap on_signal INT TERM
trap 'cleanup $?' EXIT

# @TASK: Levantar backend FastAPI en background y persistir PID para supervisión.
# @INPUT: main.py del proyecto y API_HOST/API_PORT.
# @OUTPUT: Proceso Python en ejecución; BACKEND_PID asignado.
# @CONTEXT: Supervisor maestro de ciclo de vida del backend.
# @SECURITY: PID explícito para evitar procesos huérfanos al finalizar sesión.
"${VENV_PYTHON}" -m uvicorn main:create_app --factory --host "${API_HOST}" --port "${API_PORT}" &
BACKEND_PID="$!"

echo "[INFO] Backend iniciado PID=${BACKEND_PID} HOST=${API_HOST} PORT=${API_PORT}"

wait "${BACKEND_PID}"
EXIT_CODE="$?"
cleanup "${EXIT_CODE}"
