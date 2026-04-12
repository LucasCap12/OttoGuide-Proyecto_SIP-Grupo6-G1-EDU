#!/usr/bin/env bash
# @TASK: Congelar dependencias del entorno activo en requirements_prod.txt estricto.
# @INPUT: Python/pip disponibles dentro del entorno de release local.
# @OUTPUT: Archivo requirements_prod.txt con salida exacta de pip freeze.
# @CONTEXT: Paso 1 del RUNBOOK_STARTUP_RC1 y RUNBOOK_DEPLOY (freeze de dependencias).
# @SECURITY: No instala ni actualiza paquetes; solo exporta estado actual.
# STEP 1: Resolver PROJECT_ROOT y validar comando python.
# STEP 2: Ejecutar python -m pip freeze y persistir salida en requirements_prod.txt.
# STEP 3: Verificar que el archivo generado no quede vacio.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_FILE="${PROJECT_ROOT}/requirements_prod.txt"

if [[ ! -d "${PROJECT_ROOT}" ]]; then
  echo "[ERROR] PROJECT_ROOT invalido: ${PROJECT_ROOT}" >&2
  exit 1
fi

if ! command -v python >/dev/null 2>&1; then
  echo "[ERROR] python no disponible" >&2
  exit 1
fi

python -m pip freeze > "${OUTPUT_FILE}"

if [[ ! -s "${OUTPUT_FILE}" ]]; then
  echo "[ERROR] requirements_prod.txt vacio" >&2
  exit 1
fi

echo "[INFO] freeze_dependencies completado"
exit 0
