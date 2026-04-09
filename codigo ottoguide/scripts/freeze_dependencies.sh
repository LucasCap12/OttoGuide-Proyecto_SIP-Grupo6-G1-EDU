#!/usr/bin/env bash

: <<'DOC'
@TASK: Congelar dependencias productivas del entorno Python activo.
@INPUT: Entorno virtual ya activado y pip disponible.
@OUTPUT: Archivo requirements_prod.txt con versiones exactas compatibles con Ubuntu.
@CONTEXT: Paso de code freeze para Release Candidate.
@SECURITY: Excluye paquetes Windows-especificos y retorna 1 ante fallo.
STEP [1]: Resolver raiz de proyecto y destino de lockfile.
STEP [2]: Ejecutar pip freeze en archivo temporal.
STEP [3]: Filtrar entradas no pinneadas y dependencias Windows-only.
STEP [4]: Persistir requirements_prod.txt y salir con codigo 0.
DOC

set -e
trap 'exit 1' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_FILE="${PROJECT_ROOT}/requirements_prod.txt"
TMP_FILE="$(mktemp)"

cleanup_tmp() {
  rm -f "${TMP_FILE}"
}

trap cleanup_tmp EXIT

if [ ! -d "${PROJECT_ROOT}" ]; then
  echo "@OUTPUT: ERROR ruta de proyecto no disponible: ${PROJECT_ROOT}"
  exit 1
fi

if ! command -v python >/dev/null 2>&1; then
  echo "@OUTPUT: ERROR comando python no disponible"
  exit 1
fi

python -m pip freeze > "${TMP_FILE}"

grep -E '^[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9].*$' "${TMP_FILE}" \
  | grep -Eiv '^(pywin32|pywinpty|pypiwin32|pyreadline|pyreadline3|win10toast|windows-curses|wincertstore|comtypes|pypiwin32)==' \
  | LC_ALL=C sort > "${OUTPUT_FILE}"

if [ ! -s "${OUTPUT_FILE}" ]; then
  echo "@OUTPUT: ERROR requirements_prod.txt quedo vacio"
  exit 1
fi

echo "@OUTPUT: Lockfile generado en ${OUTPUT_FILE}"
exit 0
