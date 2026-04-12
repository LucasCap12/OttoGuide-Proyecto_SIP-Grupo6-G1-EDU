#!/usr/bin/env bash
# @TASK: Aprovisionar entorno Python persistente en Companion PC para OttoGuide.
# @INPUT: Path de despliegue en target (/opt/ottoguide o /home/unitree/ottoguide/codigo_ottoguide).
# @OUTPUT: Entorno .venv creado si no existe, pip actualizado y requirements_prod.txt instalado.
# @CONTEXT: Paso 3 del RUNBOOK_DEPLOY para bootstrap en host fisico Ubuntu.
# @SECURITY: Modo fail-fast; ante cualquier error retorna Exit 1 (NO-GO).
# STEP 1: Resolver PROJECT_ROOT en rutas permitidas de despliegue.
# STEP 2: Crear .venv si no existe.
# STEP 3: Actualizar pip dentro de .venv.
# STEP 4: Instalar de forma estricta requirements_prod.txt.

set -e

PROJECT_ROOT=""
if [[ -d "/opt/ottoguide" ]]; then
  PROJECT_ROOT="/opt/ottoguide"
elif [[ -d "/home/unitree/ottoguide/codigo_ottoguide" ]]; then
  PROJECT_ROOT="/home/unitree/ottoguide/codigo_ottoguide"
else
  echo "[ERROR] NO-GO bootstrap: path de despliegue no encontrado" >&2
  exit 1
fi

VENV_DIR="${PROJECT_ROOT}/.venv"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements_prod.txt"

if [[ ! -f "${REQUIREMENTS_FILE}" ]]; then
  echo "[ERROR] NO-GO bootstrap: requirements_prod.txt ausente en ${PROJECT_ROOT}" >&2
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip
python -m pip install --requirement "${REQUIREMENTS_FILE}"

echo "[INFO] GO bootstrap_target completado"
exit 0
