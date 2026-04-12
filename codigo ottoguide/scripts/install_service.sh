#!/usr/bin/env bash
# @TASK: Registrar unidad systemd de OttoGuide en el host target con privilegios sudo.
# @INPUT: Archivo deploy/ottoguide.service presente en el arbol desplegado.
# @OUTPUT: Unidad copiada a /etc/systemd/system, daemon-reload aplicado y servicio enable --now.
# @CONTEXT: Paso 4 del RUNBOOK_DEPLOY para persistencia operativa tras bootstrap.
# @SECURITY: Requiere sudo explicito para escritura en /etc/systemd/system y control de systemctl.
# STEP 1: Resolver PROJECT_ROOT y validar existencia de deploy/ottoguide.service.
# STEP 2: Copiar unidad a /etc/systemd/system/ottoguide.service.
# STEP 3: Ejecutar systemctl daemon-reload.
# STEP 4: Ejecutar systemctl enable --now ottoguide.service.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UNIT_SRC="${PROJECT_ROOT}/deploy/ottoguide.service"
UNIT_DST="/etc/systemd/system/ottoguide.service"

if [[ ! -f "${UNIT_SRC}" ]]; then
  echo "[ERROR] NO-GO install_service: unidad no encontrada en ${UNIT_SRC}" >&2
  exit 1
fi

sudo cp "${UNIT_SRC}" "${UNIT_DST}"
sudo systemctl daemon-reload
sudo systemctl enable --now ottoguide.service

echo "[INFO] GO install_service completado"
exit 0
