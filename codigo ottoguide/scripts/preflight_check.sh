#!/usr/bin/env bash
# ============================================================================
# @TASK: Validar precondiciones del entorno antes de autorizar main.py
# @INPUT: Archivo .env en la raiz del proyecto; variables de entorno del sistema
# @OUTPUT: Exit 0 si todas las precondiciones criticas se cumplen; exit 1 si alguna falla
# @CONTEXT: Script pre-vuelo SRE para OttoGuide HIL en Companion PC (Ubuntu/Debian)
#           Ejecutado por start_robot.sh antes de cualquier inicializacion de hardware
# @SECURITY: Bloqueo estricto ante cualquier falla critica (exit 1)
#            No modifica el entorno del proceso padre; solo lee y valida
# ============================================================================
#
# STEP 1: Cargar .env y establecer defaults seguros
# STEP 2: Verificar interfaz de red (solo ROBOT_MODE=real)
# STEP 3: Verificar conectividad de red (ROBOT_MODE=real/sim)
# STEP 4: Verificar que el puerto API_PORT este libre
# STEP 5: Verificar que Ollama responde y que el modelo requerido esta disponible
# STEP 6: Emitir resumen de diagnostico (PASS/FAIL por seccion)
#
# Uso:
#   bash scripts/preflight_check.sh
#   (siempre desde la raiz del proyecto OttoGuide)
# ============================================================================

set -euo pipefail

# ----------------------------------------------------------------------------
# Colores ANSI para salida de terminal
# ----------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

_ok()    { echo -e "  ${GREEN}[OK]${RESET}    $*"; }
_fail()  { echo -e "  ${RED}[FAIL]${RESET}  $*" >&2; }
_warn()  { echo -e "  ${YELLOW}[WARN]${RESET}  $*"; }
_info()  { echo -e "  ${CYAN}[INFO]${RESET}  $*"; }
_section() {
  echo ""
  echo -e "${BOLD}━━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
}

# ----------------------------------------------------------------------------
# Resolucion de rutas
# ----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

# Contador de fallos criticos
CRITICAL_FAILURES=0

# ----------------------------------------------------------------------------
# STEP 1: Cargar .env y aplicar defaults seguros
# @TASK: Leer variables de configuracion desde .env; si no existe, usar defaults
# @SECURITY: Sin eval; solo exportacion via declaracion explicita de clave=valor
# ----------------------------------------------------------------------------
_section "STEP 1 — Carga de configuracion (.env)"

if [[ -f "${ENV_FILE}" ]]; then
  _info "Cargando configuracion desde: ${ENV_FILE}"
  # Parseo seguro: ignorar comentarios, lineas vacias y exportaciones con espacios
  while IFS='=' read -r key value; do
    # Ignorar comentarios y lineas vacias
    [[ "${key}" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${key// }" ]]             && continue
    # Limpiar espacios y comillas del valor
    value="${value%%#*}"             # eliminar comentarios inline
    value="${value%"${value##*[![:space:]]}"}"  # trim trailing space
    value="${value#"${value%%[![:space:]]*}"}"  # trim leading space
    value="${value#\"}" value="${value%\"}"     # eliminar comillas dobles
    value="${value#\'}" value="${value%\'}"     # eliminar comillas simples
    key="${key// /}"                           # eliminar espacios en clave
    [[ -z "${key}" ]] && continue
    export "${key}=${value}" 2>/dev/null || true
  done < "${ENV_FILE}"
  _ok "Archivo .env cargado correctamente."
else
  _warn ".env no encontrado en ${PROJECT_ROOT} — usando defaults de entorno."
fi

# Aplicar defaults si no estan definidos
ROBOT_MODE="${ROBOT_MODE:-mock}"
ROBOT_NETWORK_INTERFACE="${ROBOT_NETWORK_INTERFACE:-eth0}"
OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-${OLLAMA_HOST}}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:3b}"
API_PORT="${API_PORT:-8000}"

_info "ROBOT_MODE             = ${ROBOT_MODE}"
_info "ROBOT_NETWORK_INTERFACE= ${ROBOT_NETWORK_INTERFACE}"
_info "OLLAMA_HOST            = ${OLLAMA_HOST}"
_info "OLLAMA_MODEL           = ${OLLAMA_MODEL}"
_info "API_PORT               = ${API_PORT}"

# ----------------------------------------------------------------------------
# STEP 2: Verificar interfaz de red (solo ROBOT_MODE=real)
# @TASK: Garantizar que la NIC fisica configurada existe en el sistema
# @INPUT: ROBOT_NETWORK_INTERFACE (e.g. eth0, enp3s0)
# @OUTPUT: OK si ip link show iface tiene estado UP; FAIL si no existe
# @SECURITY: Solo lectura de estado de interfaces; sin modificacion de red
# ----------------------------------------------------------------------------
_section "STEP 2 — Interfaz de red DDS (modo: ${ROBOT_MODE})"

if [[ "${ROBOT_MODE}" == "real" ]]; then
  if [[ -z "${ROBOT_NETWORK_INTERFACE}" ]]; then
    _fail "ROBOT_NETWORK_INTERFACE no definida. Requerida para ROBOT_MODE=real."
    (( CRITICAL_FAILURES++ )) || true
  else
    if ip link show "${ROBOT_NETWORK_INTERFACE}" &>/dev/null; then
      # Verificar estado UP
      if ip link show "${ROBOT_NETWORK_INTERFACE}" | grep -q "state UP"; then
        _ok "Interfaz '${ROBOT_NETWORK_INTERFACE}' existe y estado: UP"
      else
        _warn "Interfaz '${ROBOT_NETWORK_INTERFACE}' existe pero estado NO es UP."
        _warn "Verificar conexion de cable Ethernet al robot G1."
        # WARNING, no falla critica — el estado puede tardar en subir
      fi
    else
      _fail "Interfaz de red '${ROBOT_NETWORK_INTERFACE}' no existe en este sistema."
      _fail "Ejecutar: ip link show — para listar interfaces disponibles."
      (( CRITICAL_FAILURES++ )) || true
    fi
  fi
else
  _info "Verificacion de NIC omitida (ROBOT_MODE=${ROBOT_MODE})."
fi

# ----------------------------------------------------------------------------
# STEP 3: Verificar conectividad de red (ping)
# @TASK: Confirmar que el destino DDS es alcanzable antes de inicializar SDK
# @INPUT: ROBOT_MODE determina el destino; ROBOT_NETWORK_INTERFACE para binding
# @OUTPUT: OK si ping responde en <1s; FAIL si timeout
# @SECURITY: ping de 2 paquetes con timeout de 2s para no bloquear el arranque
# ----------------------------------------------------------------------------
_section "STEP 3 — Conectividad de red"

if [[ "${ROBOT_MODE}" == "real" ]]; then
  PING_TARGET="192.168.123.161"
  _info "Modo real: verificando ping a robot G1 en ${PING_TARGET}..."
  if ping -c 2 -W 2 -I "${ROBOT_NETWORK_INTERFACE}" "${PING_TARGET}" &>/dev/null; then
    _ok "Robot G1 alcanzable en ${PING_TARGET} via ${ROBOT_NETWORK_INTERFACE}"
  else
    _fail "Robot G1 NO responde en ${PING_TARGET}."
    _fail "Verificar: cable Ethernet, IP del robot (ipconfig en tablet G1), encendido."
    (( CRITICAL_FAILURES++ )) || true
  fi

elif [[ "${ROBOT_MODE}" == "sim" ]]; then
  PING_TARGET="127.0.0.1"
  _info "Modo sim: verificando loopback ${PING_TARGET}..."
  if ping -c 1 -W 1 "${PING_TARGET}" &>/dev/null; then
    _ok "Loopback ${PING_TARGET} disponible (interfaz 'lo' activa)."
  else
    _fail "Interfaz loopback no responde — estado del sistema operativo critico."
    (( CRITICAL_FAILURES++ )) || true
  fi
  # Advertir si unitree_mujoco no parece estar corriendo
  # (proxy: verificar si alguien tiene el puerto DDS domain 1 abierto con UDP 7401)
  if ss -ulpn 2>/dev/null | grep -q "7401"; then
    _ok "Detectado proceso escuchando en UDP 7401 — posible simulador DDS activo."
  else
    _warn "No se detecta listener en UDP 7401 (domain 1 DDS)."
    _warn "Asegurar que unitree_mujoco.py esta corriendo antes de main.py."
    _warn "Comando: cd libs/unitree_mujoco-main/simulate_python && python3 unitree_mujoco.py"
  fi

else
  _info "Modo mock: verificacion de ping omitida."
fi

# ----------------------------------------------------------------------------
# STEP 4: Verificar que API_PORT esta libre
# @TASK: Evitar conflicto de puerto al iniciar uvicorn/FastAPI
# @INPUT: API_PORT (default 8000)
# @OUTPUT: OK si el puerto esta libre; FAIL si otro proceso lo ocupa
# @SECURITY: Solo lectura del estado de sockets; sin modificacion de procesos
# ----------------------------------------------------------------------------
_section "STEP 4 — Disponibilidad del puerto API (${API_PORT}/tcp)"

PORT_BUSY=false

# Intentar con ss primero (iproute2, moderno); fallback a netstat (net-tools)
if command -v ss &>/dev/null; then
  if ss -tlpn 2>/dev/null | grep -qE ":${API_PORT}[[:space:]]"; then
    PORT_BUSY=true
    OCCUPANT="$(ss -tlpn 2>/dev/null | grep ":${API_PORT}" | awk '{print $NF}')"
  fi
elif command -v netstat &>/dev/null; then
  if netstat -tlpn 2>/dev/null | grep -qE ":${API_PORT}[[:space:]]"; then
    PORT_BUSY=true
    OCCUPANT="$(netstat -tlpn 2>/dev/null | grep ":${API_PORT}" | awk '{print $NF}')"
  fi
else
  _warn "Ni 'ss' ni 'netstat' disponibles — verificacion de puerto omitida."
  _warn "Instalar iproute2: sudo apt install iproute2"
fi

if [[ "${PORT_BUSY}" == "true" ]]; then
  _fail "Puerto TCP ${API_PORT} OCUPADO por: ${OCCUPANT:-proceso desconocido}"
  _fail "Liberar el puerto antes de iniciar OttoGuide:"
  _fail "  sudo fuser -k ${API_PORT}/tcp   ← terminar proceso ocupante"
  _fail "  o cambiar API_PORT en .env"
  (( CRITICAL_FAILURES++ )) || true
else
  _ok "Puerto TCP ${API_PORT} disponible."
fi

# ----------------------------------------------------------------------------
# STEP 5: Verificar Ollama daemon y disponibilidad del modelo
# @TASK: Confirmar que Ollama esta activo y que qwen2.5:3b esta descargado
# @INPUT: OLLAMA_HOST, OLLAMA_MODEL
# @OUTPUT: OK si /api/tags responde y el modelo aparece en la lista; FAIL si no
# @SECURITY: curl con timeout estricto (3s connect, 5s total); sin autenticacion
#            Ollama corre en localhost sin credenciales por diseno air-gapped
# ----------------------------------------------------------------------------
_section "STEP 5 — Demonio Ollama y modelo '${OLLAMA_MODEL}'"

# Extraer hostname:puerto de OLLAMA_HOST (eliminar schema http:// o https://)
OLLAMA_ENDPOINT="${OLLAMA_HOST%/}"
OLLAMA_TAGS_URL="${OLLAMA_ENDPOINT}/api/tags"

_info "Verificando: GET ${OLLAMA_TAGS_URL}"

OLLAMA_RESPONSE=""
CURL_EXIT=0

# Verificar que curl esta disponible
if ! command -v curl &>/dev/null; then
  _fail "curl no disponible — instalar: sudo apt install curl"
  (( CRITICAL_FAILURES++ )) || true
else
  set +e
  OLLAMA_RESPONSE="$(
    curl --silent --max-time 5 --connect-timeout 3 \
         --retry 1 --retry-delay 1 \
         "${OLLAMA_TAGS_URL}" 2>/dev/null
  )"
  CURL_EXIT=$?
  set -e

  if [[ ${CURL_EXIT} -ne 0 ]] || [[ -z "${OLLAMA_RESPONSE}" ]]; then
    _fail "Ollama NO responde en ${OLLAMA_HOST}."
    _fail "Verificar que el daemon esta activo:"
    _fail "  systemctl status ollama"
    _fail "  ollama serve     (si no corre como servicio systemd)"
    (( CRITICAL_FAILURES++ )) || true
  else
    _ok "Ollama daemon responde en ${OLLAMA_HOST}"

    # Parseo sin jq — buscar el nombre del modelo en el JSON raw
    # El campo es: "name":"qwen2.5:3b" o "model":"qwen2.5:3b"
    if echo "${OLLAMA_RESPONSE}" | grep -qF "\"${OLLAMA_MODEL}\""; then
      _ok "Modelo '${OLLAMA_MODEL}' encontrado en la lista de modelos de Ollama."
    else
      _fail "Modelo '${OLLAMA_MODEL}' NO esta disponible en Ollama."
      _fail "Descargar el modelo (requiere conexion a internet UNA VEZ):"
      _fail "  ollama pull ${OLLAMA_MODEL}"
      _fail "Modelos actualmente disponibles:"
      # Extraer nombres de modelos del JSON para orientacion del operador
      echo "${OLLAMA_RESPONSE}" | grep -oP '"name"\s*:\s*"\K[^"]+' | \
        while IFS= read -r m; do _fail "    - ${m}"; done || \
        _fail "    (no se pudo parsear la lista de modelos)"
      (( CRITICAL_FAILURES++ )) || true
    fi
  fi
fi

# ----------------------------------------------------------------------------
# STEP 6: Resumen de diagnostico final
# @TASK: Emitir veredicto go/no-go y bloquear ejecucion si hay fallos criticos
# @OUTPUT: Exit 0 (GO) o exit 1 (NO-GO en ROJO)
# ----------------------------------------------------------------------------
_section "STEP 6 — Resumen pre-vuelo"

echo ""
if [[ "${CRITICAL_FAILURES}" -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}"
  echo "  ╔══════════════════════════════════════════════╗"
  echo "  ║   PREFLIGHT: GO ✓ — Todas las precondiciones ║"
  echo "  ║   criticas satisfechas. Sistema autorizado.  ║"
  echo "  ╚══════════════════════════════════════════════╝"
  echo -e "${RESET}"
  exit 0
else
  echo -e "${RED}${BOLD}"
  echo "  ╔══════════════════════════════════════════════╗"
  echo "  ║   PREFLIGHT: NO-GO ✗ — ${CRITICAL_FAILURES} fallo(s) critico(s)  ║"
  echo "  ║   detectado(s). Arranque BLOQUEADO.          ║"
  echo "  ║   Revisar los errores [FAIL] anteriores y    ║"
  echo "  ║   corregir antes de reintentar.              ║"
  echo "  ╚══════════════════════════════════════════════╝"
  echo -e "${RESET}"
  exit 1
fi
