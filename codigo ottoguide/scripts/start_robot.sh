#!/usr/bin/env bash
# ============================================================================
# start_robot.sh — Punto de entrada maestro del sistema HIL Unitree G1 EDU
# Invocacion: bash scripts/start_robot.sh
#             (siempre desde la raiz del proyecto)
# ============================================================================

set -euo pipefail

# ----------------------------------------------------------------------------
# Resolucion de rutas absolutas del proyecto
# ----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERIFY_SCRIPT="${SCRIPT_DIR}/verify_remote_env.sh"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
VENV_ACTIVATE="${PROJECT_ROOT}/.venv/bin/activate"
CYCLONEDDS_CONFIG="${PROJECT_ROOT}/config/cyclonedds.xml"
SRC_PATH="${PROJECT_ROOT}/src"
SDK_PATH="${PROJECT_ROOT}/libs/unitree_sdk2_python-master"
ENTRYPOINT="${PROJECT_ROOT}/main.py"

# ----------------------------------------------------------------------------
# FASE 0 — Validacion de prerequisitos locales del script supervisor
# ----------------------------------------------------------------------------
_require_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "${path}" ]]; then
    echo "[FATAL] ${label} no encontrado: ${path}" >&2
    exit 1
  fi
}

_require_file "${VERIFY_SCRIPT}"       "Script de validacion HIL"
_require_file "${ROS_SETUP}"           "ROS 2 Humble setup.bash"
_require_file "${VENV_ACTIVATE}"       "Activador de entorno virtual"
_require_file "${CYCLONEDDS_CONFIG}"   "Configuracion CycloneDDS XML"
_require_file "${ENTRYPOINT}"          "Entrypoint Python (main.py)"

# ----------------------------------------------------------------------------
# FASE 0.5 — Pre-vuelo SRE: validacion de precondiciones del entorno
# @TASK: Ejecutar preflight_check.sh antes de cualquier inicializacion de hardware
# @INPUT: .env del proyecto; estado del sistema (red, puertos, Ollama)
# @OUTPUT: Exit 0 = precondiciones OK; exit 1 = arranque bloqueado
# @CONTEXT: Barrera obligatoria antes de cargar ROS 2 o activar DDS
# @SECURITY: Ningun recurso de hardware se toca antes de este gate
# STEP 1: Ejecutar preflight_check.sh desde el directorio del proyecto
# STEP 2: Bloquear arranque si preflight retorna exit != 0
# ----------------------------------------------------------------------------
PREFLIGHT_SCRIPT="${SCRIPT_DIR}/preflight_check.sh"

if [[ ! -f "${PREFLIGHT_SCRIPT}" ]]; then
  echo "[FATAL] preflight_check.sh no encontrado: ${PREFLIGHT_SCRIPT}" >&2
  echo "        El script de pre-vuelo es obligatorio. Abortando." >&2
  exit 1
fi

echo "[PRE-VUELO] Iniciando validacion de precondiciones del entorno..." >&2
echo "─────────────────────────────────────────────────────────────────" >&2

set +e
bash "${PREFLIGHT_SCRIPT}"
PREFLIGHT_EXIT=$?
set -e

if [[ "${PREFLIGHT_EXIT}" -ne 0 ]]; then
  echo "" >&2
  echo "════════════════════════════════════════════════════════" >&2
  echo "  [NO-GO] preflight_check.sh retorno exit ${PREFLIGHT_EXIT}." >&2
  echo "  El sistema NO esta autorizado para arrancar." >&2
  echo "  Corregir los errores marcados con [FAIL] y reintentar." >&2
  echo "════════════════════════════════════════════════════════" >&2
  exit 1
fi

echo "─────────────────────────────────────────────────────────────────" >&2
echo "[PRE-VUELO] GO — Precondiciones verificadas. Continuando..." >&2
echo "" >&2

# ----------------------------------------------------------------------------
# FASE 1 — Pre-Ejecucion: validacion del entorno remoto HIL
# ----------------------------------------------------------------------------
echo "[HIL] Ejecutando validacion de entorno: ${VERIFY_SCRIPT}" >&2

set +e
bash "${VERIFY_SCRIPT}"
VERIFY_EXIT_CODE=$?
set -e

case "${VERIFY_EXIT_CODE}" in
  0)
    echo "[HIL] Validacion de entorno: APROBADA (exit 0). Continuando." >&2
    ;;
  1)
    echo "" >&2
    echo "========================================================" >&2
    echo "  [CRITICAL] verify_remote_env.sh retorno EXIT 1." >&2
    echo "  Uno o mas requisitos criticos del entorno HIL han" >&2
    echo "  fallado. El arranque del sistema esta BLOQUEADO." >&2
    echo "  Revisar la salida anterior, corregir el entorno" >&2
    echo "  y volver a ejecutar este script." >&2
    echo "========================================================" >&2
    exit 1
    ;;
  2)
    echo "" >&2
    echo "========================================================" >&2
    echo "  [WARNING] verify_remote_env.sh retorno EXIT 2." >&2
    echo "  Advertencias no criticas detectadas en el entorno." >&2
    echo "  El operador puede forzar el arranque bajo su" >&2
    echo "  responsabilidad exclusiva." >&2
    echo "========================================================" >&2
    read -r -p "[OVERRIDE] Escribir FORZAR para continuar de todos modos, o cualquier otra entrada para abortar: " OPERATOR_OVERRIDE
    if [[ "${OPERATOR_OVERRIDE}" != "FORZAR" ]]; then
      echo "[ABORT] Override no confirmado. Arranque cancelado por el operador." >&2
      exit 1
    fi
    echo "[HIL] Override confirmado por operador. Continuando con advertencias activas." >&2
    ;;
  *)
    echo "[FATAL] verify_remote_env.sh retorno codigo de salida inesperado: ${VERIFY_EXIT_CODE}." >&2
    echo "        El script de validacion debe retornar solo 0, 1 o 2. Abortando." >&2
    exit 1
    ;;
esac

# ----------------------------------------------------------------------------
# FASE 2 — Confirmacion operativa de hardware (barrera mecanica)
# ----------------------------------------------------------------------------
echo "" >&2
echo "========================================================" >&2
echo "  [SAFETY] Confirmacion de modo hardware requerida." >&2
echo "  Antes de continuar, verificar en el mando del robot:" >&2
echo "    1) L2 + R2  => Develop Mode activo." >&2
echo "    2) L2 + A   => Position Mode activo." >&2
echo "  ADVERTENCIA: operar el robot sin Position Mode activo" >&2
echo "               puede causar caidas y daños mecanicos." >&2
echo "========================================================" >&2
read -r -p "[SAFETY] Escribir CONFIRMAR para continuar o cualquier otra entrada para abortar: " OPERATOR_CONFIRMATION
if [[ "${OPERATOR_CONFIRMATION}" != "CONFIRMAR" ]]; then
  echo "[ABORT] Confirmacion de seguridad no valida. Arranque cancelado." >&2
  exit 1
fi
echo "[SAFETY] Confirmacion valida. Procediendo con el arranque del sistema." >&2

# ----------------------------------------------------------------------------
# FASE 3 — Aprovisionamiento del entorno de ejecucion
# ----------------------------------------------------------------------------

# ROS 2 Humble
# shellcheck source=/opt/ros/humble/setup.bash
source "${ROS_SETUP}"
echo "[ENV] ROS 2 Humble cargado desde: ${ROS_SETUP}" >&2

# Entorno virtual Python
# shellcheck source=/dev/null
source "${VENV_ACTIVATE}"
echo "[ENV] Entorno virtual activado: ${VENV_ACTIVATE}" >&2

# Middleware DDS — CycloneDDS Unicast hacia 192.168.123.161
export RMW_IMPLEMENTATION="rmw_cyclonedds_cpp"
export CYCLONEDDS_URI="file://${CYCLONEDDS_CONFIG}"
echo "[DDS] RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION}" >&2
echo "[DDS] CYCLONEDDS_URI=${CYCLONEDDS_URI}" >&2

# PYTHONPATH — src/ + SDK local Unitree (prepend sobre el existente)
export PYTHONPATH="${PROJECT_ROOT}:${SRC_PATH}:${SDK_PATH}:${PYTHONPATH:-}"
echo "[ENV] PYTHONPATH configurado (raiz + src + SDK)." >&2

# ----------------------------------------------------------------------------
# FASE 4 — Manejo de senales OS (trap)
# ----------------------------------------------------------------------------
CHILD_PID=""
FINAL_EXIT_CODE=0

_forward_signal() {
  local sig_name="$1"
  echo "" >&2
  echo "[SIGNAL] Senal ${sig_name} recibida por el supervisor. Propagando al proceso Python..." >&2
  if [[ -n "${CHILD_PID}" ]] && kill -0 "${CHILD_PID}" 2>/dev/null; then
    kill "-${sig_name}" "${CHILD_PID}" 2>/dev/null || true
    # Esperar al hijo para recoger el exit code real
    wait "${CHILD_PID}" 2>/dev/null || true
  fi
}

_on_sigterm() {
  _forward_signal "TERM"
  # El exit lo gestiona el bloque wait principal; forzamos exit no-cero si el hijo murio anomalamente
  FINAL_EXIT_CODE=143  # 128 + SIGTERM
}

_on_sigint() {
  _forward_signal "INT"
  FINAL_EXIT_CODE=130  # 128 + SIGINT
}

trap '_on_sigterm' SIGTERM
trap '_on_sigint'  SIGINT

# ----------------------------------------------------------------------------
# FASE 5 — Ejecucion del core
# ----------------------------------------------------------------------------
echo "" >&2
echo "[CORE] Iniciando sistema robotico: python ${ENTRYPOINT}" >&2
echo "--------------------------------------------------------" >&2

python "${ENTRYPOINT}" &
CHILD_PID="$!"

# Esperar al proceso hijo; recoger su exit code exacto
set +e
wait "${CHILD_PID}"
CHILD_EXIT_CODE=$?
set -e

# Limpiar traps antes de salir para evitar doble disparo
trap - SIGTERM SIGINT

# Si el proceso murio por senal, usar el exit code de senal calculado en los handlers
# Si murio normalmente, usar su propio exit code
if [[ "${CHILD_EXIT_CODE}" -ne 0 ]] && [[ "${FINAL_EXIT_CODE}" -eq 0 ]]; then
  FINAL_EXIT_CODE="${CHILD_EXIT_CODE}"
fi

if [[ "${FINAL_EXIT_CODE}" -ne 0 ]]; then
  echo "" >&2
  echo "[ERROR] El proceso principal termino de forma anomala." >&2
  echo "        PID: ${CHILD_PID} | Exit code reportado: ${FINAL_EXIT_CODE}" >&2
  echo "        Revisar logs de sesion SSH para diagnostico." >&2
fi

echo "[CORE] Supervisor terminado. Exit code: ${FINAL_EXIT_CODE}" >&2
exit "${FINAL_EXIT_CODE}"