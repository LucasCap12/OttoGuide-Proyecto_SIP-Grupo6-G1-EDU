#!/bin/sh

: <<'DOC'
@TASK: Sincronizar el workspace OttoGuide desde el host local hacia la Companion PC.
@INPUT: SSH operativo, rsync disponible y target unitree@192.168.123.164.
@OUTPUT: Codigo de salida 0 si la sincronizacion finaliza correctamente.
@CONTEXT: Pipeline local de deploy para inyectar cambios en el target HIL.
@SECURITY: Prevalida conectividad SSH y preserva logs y mapas existentes en el target.
STEP [1]: Validar dependencias locales y parametros de destino.
STEP [2]: Verificar conectividad a puerto 22 por ping o nc y luego validar SSH.
STEP [3]: Sincronizar el workspace excluyendo artefactos volatiles.
STEP [4]: Sincronizar mapas de forma no destructiva con ignore-existing.
STEP [5]: Crear la ruta destino remota si no existe y finalizar con estado explicito.
DOC

set -eu

ROBOT_USER=${1:-unitree}
ROBOT_IP=${2:-192.168.123.164}
PROJECT_ROOT=${3:-$(cd "$(dirname "$0")/.." && pwd)}
REMOTE_ROOT=${4:-/home/unitree/ottoguide/codigo_ottoguide}

SSH_TARGET=${ROBOT_USER}@${ROBOT_IP}

if ! command -v rsync >/dev/null 2>&1; then
  echo "@OUTPUT: ERROR rsync no esta disponible en el host local"
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "@OUTPUT: ERROR ssh no esta disponible en el host local"
  exit 1
fi

PROJECT_ROOT=$(cd "${PROJECT_ROOT}" && pwd)

if [ ! -d "${PROJECT_ROOT}" ]; then
  echo "@OUTPUT: ERROR ruta de proyecto invalida: ${PROJECT_ROOT}"
  exit 1
fi

check_ping() {
  if command -v ping >/dev/null 2>&1; then
    ping -c 1 -W 2 "${ROBOT_IP}" >/dev/null 2>&1
    return $?
  fi
  return 2
}

check_nc() {
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 5 "${ROBOT_IP}" 22 >/dev/null 2>&1
    return $?
  fi
  return 2
}

if check_ping; then
  echo "@CONTEXT: Ping a ${ROBOT_IP} exitoso"
else
  PING_STATUS=$?
  if [ "${PING_STATUS}" -eq 2 ]; then
    if check_nc; then
      echo "@CONTEXT: Verificacion netcat a ${ROBOT_IP}:22 exitosa"
    else
      echo "@OUTPUT: ERROR no fue posible validar conectividad a ${ROBOT_IP}:22 con ping ni nc"
      exit 1
    fi
  else
    if check_nc; then
      echo "@CONTEXT: Ping fallido, pero netcat a ${ROBOT_IP}:22 exitoso"
    else
      echo "@OUTPUT: ERROR no fue posible validar conectividad a ${ROBOT_IP}:22"
      exit 1
    fi
  fi
fi

if ! ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "${SSH_TARGET}" 'exit 0' >/dev/null 2>&1; then
  echo "@OUTPUT: ERROR autenticacion SSH o conectividad remota no disponible para ${SSH_TARGET}"
  exit 1
fi

ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "${SSH_TARGET}" "mkdir -p '${REMOTE_ROOT}' '${REMOTE_ROOT}/maps' '${REMOTE_ROOT}/logs'"

echo "@CONTEXT: Sincronizando codigo hacia ${SSH_TARGET}:${REMOTE_ROOT}"

rsync -az --no-perms --no-owner --no-group \
  --exclude=.git/ \
  --exclude=.venv/ \
  --exclude=venv/ \
  --exclude=__pycache__/ \
  --exclude=.pytest_cache/ \
  --exclude=logs/ \
  --exclude=maps/ \
  -e ssh \
  "${PROJECT_ROOT}/" \
  "${SSH_TARGET}:${REMOTE_ROOT}/"

if [ -d "${PROJECT_ROOT}/maps" ]; then
  rsync -az --no-perms --no-owner --no-group --ignore-existing \
    --exclude=.git/ \
    --exclude=.venv/ \
    --exclude=venv/ \
    --exclude=__pycache__/ \
    -e ssh \
    "${PROJECT_ROOT}/maps/" \
    "${SSH_TARGET}:${REMOTE_ROOT}/maps/"
fi

echo "@OUTPUT: Sincronizacion completada contra ${SSH_TARGET}:${REMOTE_ROOT}"
