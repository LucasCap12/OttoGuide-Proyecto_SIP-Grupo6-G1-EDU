#!/usr/bin/env bash

: <<'DOC'
@TASK: Aprovisionar entorno Python productivo en Companion PC.
@INPUT: Usuario unitree ejecutando localmente en Ubuntu target.
@OUTPUT: Entorno virtual creado/actualizado, dependencias instaladas y scripts ejecutables.
@CONTEXT: Paso obligatorio previo a instalar y arrancar ottoguide_mvp.service.
@SECURITY: Falla con codigo 1 ante error de ruta, venv o instalacion de dependencias.
STEP [1]: Cambiar al directorio /home/unitree/ottoguide/codigo_ottoguide.
STEP [2]: Crear .venv si no existe.
STEP [3]: Activar .venv e instalar requirements_prod.txt.
STEP [4]: Aplicar chmod +x recursivo sobre scripts/.
STEP [5]: Retornar codigo 0 en exito.
DOC

set -e
trap 'exit 1' ERR

PROJECT_ROOT="/home/unitree/ottoguide/codigo_ottoguide"
VENV_DIR="${PROJECT_ROOT}/.venv"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements_prod.txt"
SCRIPTS_DIR="${PROJECT_ROOT}/scripts"

if [ ! -d "${PROJECT_ROOT}" ]; then
  echo "@OUTPUT: ERROR no existe el directorio ${PROJECT_ROOT}"
  exit 1
fi

cd "${PROJECT_ROOT}"

if [ ! -d "${VENV_DIR}" ]; then
  python3 -m venv "${VENV_DIR}"
fi

if [ ! -f "${REQUIREMENTS_FILE}" ]; then
  echo "@OUTPUT: ERROR no existe ${REQUIREMENTS_FILE}"
  exit 1
fi

if [ ! -d "${SCRIPTS_DIR}" ]; then
  echo "@OUTPUT: ERROR no existe ${SCRIPTS_DIR}"
  exit 1
fi

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "${REQUIREMENTS_FILE}"

find "${SCRIPTS_DIR}" -type f -exec chmod +x {} +

echo "@OUTPUT: Bootstrap target completado en ${PROJECT_ROOT}"
exit 0
