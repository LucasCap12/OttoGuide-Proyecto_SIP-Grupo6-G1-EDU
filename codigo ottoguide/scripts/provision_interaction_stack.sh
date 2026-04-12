#!/usr/bin/env bash
# @TASK: Aprovisionar stack local de interaccion HIL (ALSA, ffmpeg, Ollama y modelo qwen2.5:3b)
# @INPUT: Permisos sudo/root, conectividad de red saliente para apt/curl, systemd disponible
# @OUTPUT: Dependencias instaladas, servicio Ollama con bind 0.0.0.0:11434 y modelo local descargado
# @CONTEXT: Script de bootstrap para Companion PC Ubuntu previo al modo interactivo del MVP
# @SECURITY: Instalacion desatendida con falla temprana; override systemd explicito para auditar cambios
# STEP 1: Instalar ALSA y ffmpeg de manera no interactiva
# STEP 2: Instalar/validar daemon Ollama y habilitar servicio systemd
# STEP 3: Aplicar override para bind 0.0.0.0:11434 y recargar daemon
# STEP 4: Verificar salud de Ollama y descargar modelo qwen2.5:3b

set -e

if [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

export DEBIAN_FRONTEND=noninteractive

${SUDO} apt-get update -y
${SUDO} apt-get install -y alsa-utils ffmpeg curl ca-certificates

if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

${SUDO} systemctl enable ollama
${SUDO} systemctl start ollama

${SUDO} mkdir -p /etc/systemd/system/ollama.service.d
${SUDO} tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF

${SUDO} systemctl daemon-reload
${SUDO} systemctl restart ollama

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "@OUTPUT: ERROR Ollama no responde en 127.0.0.1:11434" >&2
  exit 1
fi

ollama pull qwen2.5:3b

echo "@OUTPUT: OK stack de interaccion aprovisionado y modelo qwen2.5:3b disponible"
