#!/usr/bin/env bash

: <<'DOC'
@TASK: Ejecutar corte de release RC1 con tag anotado.
@INPUT: Repositorio git y arbol de trabajo limpio.
@OUTPUT: Tag v1.0.0-MVP creado o error tecnico con codigo 1.
@CONTEXT: Paso final de versionado previo al despliegue inmutable en Companion PC.
@SECURITY: Aborta si existen cambios pendientes o si el tag ya existe.
STEP [1]: Resolver raiz de proyecto y validar disponibilidad de git.
STEP [2]: Verificar git status --porcelain vacio.
STEP [3]: Crear tag anotado v1.0.0-MVP.
STEP [4]: Emitir instruccion operativa final por stdout.
DOC

set -e
trap 'exit 1' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(git -C "${PROJECT_ROOT}" rev-parse --show-toplevel 2>/dev/null || true)"
TAG_NAME="v1.0.0-MVP"
TAG_MESSAGE="Release Candidate 1 - OttoGuide HIL UADE 2026"

if ! command -v git >/dev/null 2>&1; then
  echo "@OUTPUT: ERROR git no disponible en el entorno"
  exit 1
fi

if [ ! -d "${PROJECT_ROOT}" ]; then
  echo "@OUTPUT: ERROR ruta de proyecto no disponible: ${PROJECT_ROOT}"
  exit 1
fi

if [ -z "${REPO_ROOT}" ]; then
  echo "@OUTPUT: ERROR no se detecto repositorio git valido"
  exit 1
fi

if [ -n "$(git -C "${REPO_ROOT}" status --porcelain)" ]; then
  echo "@OUTPUT: ERROR arbol de trabajo con cambios sin confirmar"
  exit 1
fi

if git -C "${REPO_ROOT}" rev-parse -q --verify "refs/tags/${TAG_NAME}" >/dev/null 2>&1; then
  echo "@OUTPUT: ERROR el tag ${TAG_NAME} ya existe"
  exit 1
fi

git -C "${REPO_ROOT}" tag -a "${TAG_NAME}" -m "${TAG_MESSAGE}"

echo "Ejecutar 'git push origin v1.0.0-MVP' para consolidar el release. Proceder luego con 'deploy_to_companion.sh'"
exit 0
