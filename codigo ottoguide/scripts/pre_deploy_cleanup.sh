#!/usr/bin/env bash

: <<'DOC'
@TASK: Sanitizar entorno local antes de despliegue.
@INPUT: Arbol de codigo en la raiz de codigo ottoguide.
@OUTPUT: Cache Python eliminada y logs vaciados con .gitkeep preservado.
@CONTEXT: Paso previo obligatorio antes de deploy_to_companion.sh.
@SECURITY: Finaliza en codigo 1 ante cualquier error de filesystem.
STEP [1]: Resolver ruta absoluta del proyecto.
STEP [2]: Eliminar directorios __pycache__ y archivos .pyc.
STEP [3]: Vaciar logs y regenerar logs/.gitkeep.
STEP [4]: Retornar codigo 0 en exito.
DOC

set -e
trap 'exit 1' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOGS_DIR="${PROJECT_ROOT}/logs"

if [ ! -d "${PROJECT_ROOT}" ]; then
  echo "@OUTPUT: ERROR ruta de proyecto no disponible: ${PROJECT_ROOT}"
  exit 1
fi

find "${PROJECT_ROOT}" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "${PROJECT_ROOT}" -type f -name "*.pyc" -delete

mkdir -p "${LOGS_DIR}"
find "${LOGS_DIR}" -mindepth 1 -exec rm -rf {} +
: > "${LOGS_DIR}/.gitkeep"

echo "@OUTPUT: Sanitizacion completada en ${PROJECT_ROOT}"
exit 0
