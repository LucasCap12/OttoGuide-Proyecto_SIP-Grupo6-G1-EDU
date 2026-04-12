#!/usr/bin/env bash
# @TASK: Sincronizar release OttoGuide desde host local hacia Companion PC por rsync seguro.
# @INPUT: USER, HOST y TARGET_DIR como parametros posicionales obligatorios.
# @OUTPUT: Codigo fuente sincronizado en TARGET_DIR usando rsync -avz --delete con exclusiones de deploy.
# @CONTEXT: Paso 2 del RUNBOOK_STARTUP_RC1 y RUNBOOK_DEPLOY (Transferencia al Target).
# @SECURITY: Requiere ping previo exitoso al HOST y aborta ante parametros invalidos o fallo de conectividad.
# STEP 1: Validar existencia de 3 parametros y dependencias locales (rsync, ssh, ping).
# STEP 2: Validar conectividad ICMP al HOST antes de iniciar transferencia.
# STEP 3: Crear TARGET_DIR remoto por SSH.
# STEP 4: Ejecutar rsync -avz --delete con --exclude-from=.gitignore y exclusiones equivalentes.

set -e

if [[ "$#" -ne 3 ]]; then
  echo "[ERROR] Uso: $0 USER HOST TARGET_DIR" >&2
  exit 1
fi

USER_NAME="$1"
HOST_NAME="$2"
TARGET_DIR="$3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SSH_TARGET="${USER_NAME}@${HOST_NAME}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "[ERROR] rsync no disponible" >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "[ERROR] ssh no disponible" >&2
  exit 1
fi

if ! command -v ping >/dev/null 2>&1; then
  echo "[ERROR] ping no disponible" >&2
  exit 1
fi

if ! ping -c 1 -W 2 "${HOST_NAME}" >/dev/null 2>&1; then
  echo "[ERROR] Host sin respuesta ICMP: ${HOST_NAME}" >&2
  exit 1
fi

ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "${SSH_TARGET}" "mkdir -p '${TARGET_DIR}'"

if [[ -f "${PROJECT_ROOT}/.gitignore" ]]; then
  rsync -avz --delete \
    --exclude-from="${PROJECT_ROOT}/.gitignore" \
    --exclude=".git/" \
    --exclude=".venv/" \
    --exclude="venv/" \
    --exclude="__pycache__/" \
    -e ssh \
    "${PROJECT_ROOT}/" \
    "${SSH_TARGET}:${TARGET_DIR}/"
else
  rsync -avz --delete \
    --exclude=".git/" \
    --exclude=".venv/" \
    --exclude="venv/" \
    --exclude="__pycache__/" \
    -e ssh \
    "${PROJECT_ROOT}/" \
    "${SSH_TARGET}:${TARGET_DIR}/"
fi

echo "[INFO] deploy_to_companion completado"
exit 0
