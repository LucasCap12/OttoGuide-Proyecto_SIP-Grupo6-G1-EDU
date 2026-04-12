#!/usr/bin/env bash
# @TASK: Ejecutar limpieza local previa al corte de release RC1.
# @INPUT: Arbol del repositorio local en codigo ottoguide.
# @OUTPUT: Cachés Python, logs locales y artefactos de build residuales eliminados.
# @CONTEXT: Paso 1 del RUNBOOK_STARTUP_RC1 y RUNBOOK_DEPLOY (Preparacion de Release).
# @SECURITY: Ejecucion fail-fast; cualquier error de filesystem aborta el proceso.
# STEP 1: Resolver PROJECT_ROOT y validar existencia.
# STEP 2: Eliminar __pycache__, .pytest_cache y archivos Python temporales.
# STEP 3: Eliminar logs locales antiguos logs/*.json y logs/*.log.
# STEP 4: Eliminar artefactos residuales de build (build, dist, *.egg-info).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ ! -d "${PROJECT_ROOT}" ]]; then
  echo "[ERROR] PROJECT_ROOT invalido: ${PROJECT_ROOT}" >&2
  exit 1
fi

find "${PROJECT_ROOT}" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "${PROJECT_ROOT}" -type d -name ".pytest_cache" -prune -exec rm -rf {} +
find "${PROJECT_ROOT}" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

mkdir -p "${PROJECT_ROOT}/logs"
find "${PROJECT_ROOT}/logs" -maxdepth 1 -type f \( -name "*.json" -o -name "*.log" \) -delete

rm -rf "${PROJECT_ROOT}/build" "${PROJECT_ROOT}/dist"
find "${PROJECT_ROOT}" -maxdepth 1 -type d -name "*.egg-info" -prune -exec rm -rf {} +

echo "[INFO] pre_deploy_cleanup completado"
exit 0
