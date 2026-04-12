#!/usr/bin/env bash
# @TASK: Ejecutar corte de release RC1 con verificacion de arbol limpio y versionado inmutable.
# @INPUT: Repositorio Git local del proyecto codigo ottoguide.
# @OUTPUT: Tag local RC1-YYYYMMDD creado y archivo version.info con hash de commit actual.
# @CONTEXT: Paso 1 del RUNBOOK_STARTUP_RC1 y RUNBOOK_DEPLOY (Release Cut).
# @SECURITY: Aborta si hay cambios sin commitear o si no existe repositorio Git valido.
# STEP 1: Validar disponibilidad de git y resolver REPO_ROOT.
# STEP 2: Verificar working tree limpio con git status --porcelain.
# STEP 3: Crear tag local con formato RC1-$(date +%Y%m%d).
# STEP 4: Persistir commit hash actual en version.info en la raiz del codigo.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(git -C "${PROJECT_ROOT}" rev-parse --show-toplevel 2>/dev/null || true)"
TAG_NAME="RC1-$(date +%Y%m%d)"
VERSION_FILE="${PROJECT_ROOT}/version.info"

if ! command -v git >/dev/null 2>&1; then
  echo "[ERROR] git no disponible" >&2
  exit 1
fi

if [[ -z "${REPO_ROOT}" ]]; then
  echo "[ERROR] repositorio git no detectado" >&2
  exit 1
fi

if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain)" ]]; then
  echo "[ERROR] working tree con cambios sin commitear" >&2
  exit 1
fi

if ! git -C "${REPO_ROOT}" rev-parse -q --verify "refs/tags/${TAG_NAME}" >/dev/null 2>&1; then
  git -C "${REPO_ROOT}" tag "${TAG_NAME}"
fi

COMMIT_HASH="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
printf '%s\n' "${COMMIT_HASH}" > "${VERSION_FILE}"

echo "[INFO] cut_release completado"
exit 0
