#!/bin/bash

# @TASK: Validar entorno remoto del Unitree G1 EDU para despliegue HIL Fase 1
# @INPUT: Sistema de archivos del companion PC post rsync desde deploy.sh
# @OUTPUT: Reporte estructurado por secciones con veredicto GO/NO-GO y exit code
# @CONTEXT: Pre-flight check ejecutado via SSH antes de invocar start_robot.sh
# STEP 1: Resolver rutas absolutas del proyecto desde BASH_SOURCE
# STEP 2: Instrumentar 5 secciones de validacion (venv, pip, SDK, DDS, L3+HW)
# STEP 3: Emitir veredicto consolidado con contadores y exit code canonico
# @SECURITY: Solo lectura. No modifica ningun estado del sistema ni del robot.
# @AI_CONTEXT: Invocacion: ssh unitree@192.168.123.161 \
#              'bash /home/unitree/RobotHumanoide/scripts/verify_remote_env.sh'

set -euo pipefail

# =============================================================================
# CONFIGURACION GLOBAL
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VENV_DIR="${PROJECT_ROOT}/.venv"
VENV_ACTIVATE="${VENV_DIR}/bin/activate"
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"
CYCLONEDDS_CONFIG="${PROJECT_ROOT}/config/cyclonedds.xml"
SDK_PATH="${PROJECT_ROOT}/libs/unitree_sdk2_python-master"
ROS_SETUP_PATH="${ROS_SETUP:-/opt/ros/humble/setup.bash}"

LOCOMOTION_IP="192.168.123.161"

TOTAL=0
PASSED=0
FAILED=0

# =============================================================================
# UTILIDADES
# =============================================================================

_pass() {
    TOTAL=$((TOTAL + 1))
    PASSED=$((PASSED + 1))
    printf "[PASS] %s\n" "$1"
}

_fail() {
    TOTAL=$((TOTAL + 1))
    FAILED=$((FAILED + 1))
    printf "[FAIL] %s\n" "$1"
    [[ -n "${2:-}" ]] && printf "       ACTION: %s\n" "$2" >&2
}

_info() {
    printf "[INFO] %s\n" "$1"
}

_skip() {
    printf "[SKIP] %s\n" "$1" >&2
}

_header() {
    printf "\n"
    printf "================================================================\n"
    printf " %s\n" "$1"
    printf "================================================================\n"
}

_sep() {
    printf -- "----------------------------------------------------------------\n"
}

# =============================================================================
# ENCABEZADO
# =============================================================================
printf "================================================================\n"
printf " VERIFY_REMOTE_ENV — HIL Phase 1 Pre-Flight Instrumentation\n"
printf " Timestamp : %s\n" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf " Hostname  : %s\n" "$(hostname)"
printf " User      : %s\n" "$(id -un)"
printf " Project   : %s\n" "${PROJECT_ROOT}"
printf "================================================================\n"

# =============================================================================
# SECCION 1: ENTORNO VIRTUAL
# =============================================================================
_header "SECCION 1 — Entorno Virtual (.venv)"

# @TASK: Verificar estructura completa del entorno virtual Python
# @INPUT: VENV_DIR, VENV_ACTIVATE, VENV_PYTHON derivados de PROJECT_ROOT
# @OUTPUT: 3 checks unitarios — directorio, activador, interprete ejecutable
# @CONTEXT: deploy.sh excluye .venv via rsync; debe existir creado manualmente
# STEP 1: Verificar existencia del directorio .venv como artefacto de directorio
# STEP 2: Verificar existencia del archivo bin/activate como activador canonico
# STEP 3: Verificar que bin/python sea ejecutable y reportar version exacta
# @SECURITY: Solo lectura sobre filesystem; no activa el entorno
# @AI_CONTEXT: Fallo en STEP 1 implica crear venv: python3.10 -m venv .venv

_sep
if [[ -d "${VENV_DIR}" ]]; then
    _pass "Directorio .venv presente: ${VENV_DIR}"
else
    _fail "Directorio .venv ausente: ${VENV_DIR}" \
          "python3.10 -m venv ${VENV_DIR} && pip install -r ${REQUIREMENTS_FILE}"
fi

if [[ -f "${VENV_ACTIVATE}" ]]; then
    _pass "Activador bin/activate presente"
else
    _fail "Activador bin/activate ausente" \
          "Recrear venv; el archivo de activacion es obligatorio para start_robot.sh"
fi

if [[ -x "${VENV_PYTHON}" ]]; then
    _PY_VER=$("${VENV_PYTHON}" --version 2>&1)
    _pass "Interprete Python ejecutable: ${_PY_VER}"
    # Verificar version minima 3.10
    _PY_MINOR=$("${VENV_PYTHON}" -c "import sys; print(sys.version_info.minor)")
    _PY_MAJOR=$("${VENV_PYTHON}" -c "import sys; print(sys.version_info.major)")
    if [[ "${_PY_MAJOR}" -ge 3 && "${_PY_MINOR}" -ge 10 ]]; then
        _pass "Version Python >= 3.10 (restriccion asyncio del proyecto)"
    else
        _fail "Version Python < 3.10 detectada: ${_PY_VER}" \
              "Instalar Python 3.10+ y recrear el venv"
    fi
else
    _fail "Interprete Python no ejecutable: ${VENV_PYTHON}" \
          "Verificar permisos: chmod +x ${VENV_PYTHON}"
fi

# =============================================================================
# SECCION 2: GRANULARIDAD DE DEPENDENCIAS PIP
# =============================================================================
_header "SECCION 2 — Dependencias pip (requirements.txt)"

# @TASK: Verificar instalacion de cada paquete declarado en requirements.txt
# @INPUT: REQUIREMENTS_FILE, VENV_PIP como executor de consultas
# @OUTPUT: Un check unitario por entrada valida del archivo de dependencias
# @CONTEXT: Detecta paquetes faltantes antes de importaciones en main.py
# STEP 1: Verificar existencia de requirements.txt en PROJECT_ROOT
# STEP 2: Parsear cada linea ignorando blancos, comentarios (#) y flags (-r/-c)
# STEP 3: Extraer nombre base del paquete (sin version specifier: >=, ==, <, !)
# STEP 4: Invocar pip show para determinar instalacion y version
# @SECURITY: pip show es solo lectura; no resuelve, descarga ni instala nada
# @AI_CONTEXT: Paquetes con guion (opencv-python) son normalizados por pip

_sep
if [[ ! -f "${REQUIREMENTS_FILE}" ]]; then
    _fail "requirements.txt ausente: ${REQUIREMENTS_FILE}" \
          "Verificar que deploy.sh transfiere el archivo raiz"
else
    _pass "requirements.txt presente: ${REQUIREMENTS_FILE}"

    if [[ ! -x "${VENV_PIP}" ]]; then
        _skip "pip de .venv no disponible — omitiendo verificacion de paquetes"
    else
        while IFS= read -r _line || [[ -n "${_line}" ]]; do
            # STEP 2: Normalizar CRLF y eliminar espacios
            _line="$(printf '%s' "${_line}" | tr -d '\r' | xargs 2>/dev/null || true)"
            [[ -z "${_line}" ]]          && continue
            [[ "${_line}" =~ ^[[:space:]]*# ]] && continue
            [[ "${_line}" =~ ^-          ]]    && continue

            # STEP 3: Extraer nombre base sin version specifier
            _pkg="$(printf '%s' "${_line}" | sed 's/[>=<!;[:space:]\[].*//' | xargs)"
            [[ -z "${_pkg}" ]] && continue

            # STEP 4: Consultar pip show
            if "${VENV_PIP}" show "${_pkg}" > /dev/null 2>&1; then
                _ver=$("${VENV_PIP}" show "${_pkg}" 2>/dev/null \
                       | grep '^Version:' | awk '{print $2}')
                _pass "pip: ${_pkg} == ${_ver}"
            else
                _fail "pip: ${_pkg} no instalado" \
                      "${VENV_PIP} install ${_line}"
            fi
        done < "${REQUIREMENTS_FILE}"
    fi
fi

# =============================================================================
# SECCION 3: RESOLUCION DE SDK UNITREE
# =============================================================================
_header "SECCION 3 — Resolucion SDK Unitree (unitree_sdk2_python)"

# @TASK: Verificar que unitree_sdk2py sea importable con PYTHONPATH extendido
# @INPUT: SDK_PATH como raiz local del SDK; VENV_PYTHON como interprete
# @OUTPUT: 2 checks — existencia del directorio SDK e importacion exitosa
# @CONTEXT: El SDK no es un paquete pip; se resuelve via PYTHONPATH en runtime
# STEP 1: Confirmar presencia del directorio libs/unitree_sdk2_python-master
# STEP 2: Ejecutar python -c con PYTHONPATH inyectado para probar importacion
# STEP 3: Capturar salida e interpretar como exito o fallo de compilacion nativa
# @SECURITY: Ejecucion efimera de Python sin I/O de red ni escritura a disco
# @AI_CONTEXT: Fallo suele indicar dependencias nativas C++ ausentes (libunitree_sdk2.so)

_sep
if [[ ! -d "${SDK_PATH}" ]]; then
    _fail "Directorio SDK ausente: ${SDK_PATH}" \
          "Verificar que deploy.sh no excluye libs/ en RSYNC_EXCLUDES"
else
    _pass "Directorio SDK presente: ${SDK_PATH}"

    if [[ ! -x "${VENV_PYTHON}" ]]; then
        _skip "Interprete .venv no disponible — omitiendo import test"
    else
        # Bloque Python inline — formato AI Code Commenter obligatorio
        _IMPORT_SNIPPET=$(cat <<'PYEOF'
# @TASK: Probar importacion de unitree_sdk2py en entorno de ejecucion HIL
# @INPUT: PYTHONPATH extendido con SDK_PATH inyectado por el script bash padre
# @OUTPUT: Cadena OK a stdout si el modulo es importable; excepcion a stderr si falla
# @CONTEXT: Valida dependencias nativas (.so) del SDK Unitree G1 EDU
# STEP 1: Intentar importacion del paquete raiz del SDK
# STEP 2: Imprimir indicador canonico de exito para parseo por bash
# @SECURITY: Sin I/O de red, sin escritura a disco, sin inicializacion de DDS
# @AI_CONTEXT: ImportError indica .so faltante; ModuleNotFoundError indica PYTHONPATH mal inyectado
import sys
try:
    import unitree_sdk2py          # STEP 1
    print("OK")                    # STEP 2
    sys.exit(0)
except ImportError as exc:
    print(f"ImportError: {exc}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"UnexpectedError: {exc}", file=sys.stderr)
    sys.exit(2)
PYEOF
)
        _IMPORT_OUTPUT=$(
            PYTHONPATH="${SDK_PATH}:${PROJECT_ROOT}/src:${PYTHONPATH:-}" \
            "${VENV_PYTHON}" - <<< "${_IMPORT_SNIPPET}" 2>&1
        ) || _IMPORT_RC=$?

        if [[ "${_IMPORT_OUTPUT:-}" == *"OK"* ]]; then
            _pass "import unitree_sdk2py exitoso con PYTHONPATH extendido"
        else
            _fail "import unitree_sdk2py fallido" \
                  "Verificar libs nativas: ls ${SDK_PATH}/lib/*.so 2>/dev/null"
            printf "       STDERR: %s\n" "${_IMPORT_OUTPUT}" >&2
        fi
    fi
fi

# =============================================================================
# SECCION 4: CONFIGURACION MIDDLEWARE / DDS
# =============================================================================
_header "SECCION 4 — Middleware CycloneDDS"

# @TASK: Validar archivo XML de CycloneDDS y variables de entorno DDS
# @INPUT: CYCLONEDDS_CONFIG como ruta al XML desplegado; variables de entorno del shell
# @OUTPUT: 3 checks estructurales sobre el XML + reporte informativo de variables
# @CONTEXT: Variables RMW/CYCLONEDDS_URI son seteadas en runtime por start_robot.sh
# STEP 1: Verificar existencia fisica del archivo cyclonedds.xml desplegado
# STEP 2: Verificar directiva AllowMulticast=false en contenido XML
# STEP 3: Verificar presencia del peer unicast canonico 192.168.123.161
# STEP 4: Reportar valores actuales de variables de entorno (informativo)
# @SECURITY: Solo lectura sobre filesystem y variables de entorno del proceso
# @AI_CONTEXT: RMW_IMPLEMENTATION y CYCLONEDDS_URI deben estar INDEFINIDAS aqui;
#              start_robot.sh los setea justo antes de invocar python main.py

_sep
if [[ ! -f "${CYCLONEDDS_CONFIG}" ]]; then
    _fail "cyclonedds.xml ausente: ${CYCLONEDDS_CONFIG}" \
          "Verificar que deploy.sh transfiere config/"
else
    _pass "cyclonedds.xml presente: ${CYCLONEDDS_CONFIG}"

    # STEP 2: AllowMulticast=false
    if grep -qiE "AllowMulticast[[:space:]]*>[[:space:]]*false" \
            "${CYCLONEDDS_CONFIG}" 2>/dev/null; then
        _pass "cyclonedds.xml: AllowMulticast=false (unicast forzado)"
    else
        _fail "cyclonedds.xml: AllowMulticast no es false" \
              "Editar XML; multicast puede generar colision de participantes DDS en la LAN"
    fi

    # STEP 3: Peer unicast canonico
    if grep -qE "Peer[^>]*address[[:space:]]*=[[:space:]]*\"${LOCOMOTION_IP}\"" \
            "${CYCLONEDDS_CONFIG}" 2>/dev/null; then
        _pass "cyclonedds.xml: peer unicast ${LOCOMOTION_IP} declarado"
    else
        _fail "cyclonedds.xml: peer unicast ${LOCOMOTION_IP} no encontrado" \
              "Agregar <Peer address=\"${LOCOMOTION_IP}\"/> en seccion Discovery"
    fi
fi

# STEP 4: Reporte informativo de variables (no son checks de pass/fail)
printf "\n"
_info "RMW_IMPLEMENTATION = '${RMW_IMPLEMENTATION:-<NO DEFINIDA>}'"
_info "CYCLONEDDS_URI     = '${CYCLONEDDS_URI:-<NO DEFINIDA>}'"
_info "Valor esperado en runtime (seteado por start_robot.sh):"
_info "  RMW_IMPLEMENTATION = 'rmw_cyclonedds_cpp'"
_info "  CYCLONEDDS_URI     = 'file://${CYCLONEDDS_CONFIG}'"

# =============================================================================
# SECCION 5: CAPA 3 Y HARDWARE FISICO
# =============================================================================
_header "SECCION 5 — Capa 3 y Hardware Fisico"

# @TASK: Verificar conectividad, middleware ROS 2 y dispositivo acustico ALSA
# @INPUT: ROS_SETUP_PATH, LOCOMOTION_IP, estado de dispositivos ALSA del sistema
# @OUTPUT: 4 checks — ROS 2 setup, conectividad ICMP, dispositivo ALSA, sounddevice
# @CONTEXT: Valida condiciones de nivel OS que start_robot.sh asume operativas
# STEP 1: Verificar existencia de /opt/ros/humble/setup.bash (sourcing en start_robot.sh)
# STEP 2: Ping ICMP no invasivo a 192.168.123.161 (modulo de locomocion interno)
# STEP 3: Verificar presencia de tarjeta de audio via aplay -l
# STEP 4: Verificar importacion de sounddevice desde .venv para pipeline TTS
# @SECURITY: Ping ICMP es no invasivo y no inicializa ningun stack del robot
# @AI_CONTEXT: Fallo de ping puede indicar bridge RJ45 no conectado o IP changed

_sep

# STEP 1: ROS 2 setup
if [[ -f "${ROS_SETUP_PATH}" ]]; then
    _pass "ROS 2 setup presente: ${ROS_SETUP_PATH}"
else
    _fail "ROS 2 setup ausente: ${ROS_SETUP_PATH}" \
          "Instalar ros-humble-desktop o verificar variable ROS_SETUP"
fi

# STEP 2: Conectividad ICMP al modulo de locomocion
if ping -c 2 -W 2 "${LOCOMOTION_IP}" > /dev/null 2>&1; then
    _RTT=$(ping -c 2 -W 2 "${LOCOMOTION_IP}" 2>/dev/null \
           | grep -oE 'avg[^=]*=[[:space:]]*[0-9.]+' \
           | grep -oE '[0-9.]+$' || echo "?")
    _pass "ICMP ping ${LOCOMOTION_IP} OK (avg RTT: ${_RTT} ms)"
else
    _fail "ICMP ping ${LOCOMOTION_IP} TIMEOUT" \
          "Verificar conexion RJ45 al bridge AP y configuracion de red"
fi

# STEP 3: Dispositivo de audio ALSA
if ! command -v aplay > /dev/null 2>&1; then
    _fail "aplay no disponible en PATH" \
          "apt-get install alsa-utils"
else
    if aplay -l 2>/dev/null | grep -q "^card"; then
        _ALSA_CARD=$(aplay -l 2>/dev/null | grep "^card" | head -1 | awk '{print $1,$2,$3}')
        _pass "Dispositivo ALSA detectado: ${_ALSA_CARD}"
    else
        _fail "Ningun dispositivo ALSA detectado" \
              "Verificar altavoz 5W integrado; revisar aplay -l"
    fi
fi

# STEP 4: Import sounddevice desde .venv (necesario para pipeline piper-tts)
if [[ -x "${VENV_PYTHON}" ]]; then
    _SD_SNIPPET=$(cat <<'PYEOF'
# @TASK: Probar importacion de sounddevice y consultar dispositivos disponibles
# @INPUT: Entorno .venv activo con sounddevice instalado; drivers ALSA del OS
# @OUTPUT: Cadena AUDIO_OK a stdout con conteo de dispositivos; error a stderr
# @CONTEXT: sounddevice requiere libportaudio2 en el sistema y drivers ALSA
# STEP 1: Importar sounddevice para verificar linkeo con libportaudio2
# STEP 2: Consultar query_devices para confirmar disponibilidad en tiempo de ejecucion
# STEP 3: Emitir conteo de dispositivos detectados como indicador de salud
# @SECURITY: Sin reproduccion de audio; query_devices es solo lectura de drivers
# @AI_CONTEXT: Fallo de importacion indica libportaudio2 ausente en el OS
import sys
try:
    import sounddevice as sd       # STEP 1
    devices = sd.query_devices()   # STEP 2
    count = len(devices) if hasattr(devices, '__len__') else 0
    print(f"AUDIO_OK:{count}")     # STEP 3
    sys.exit(0)
except OSError as exc:
    print(f"OSError: {exc}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"Error: {exc}", file=sys.stderr)
    sys.exit(2)
PYEOF
)
    _SD_OUTPUT=$(
        "${VENV_PYTHON}" - <<< "${_SD_SNIPPET}" 2>&1
    ) || true

    if [[ "${_SD_OUTPUT:-}" == AUDIO_OK* ]]; then
        _DEV_COUNT=$(printf '%s' "${_SD_OUTPUT}" | cut -d: -f2)
        _pass "sounddevice importable; dispositivos detectados: ${_DEV_COUNT}"
    else
        _fail "sounddevice fallo en runtime" \
              "apt-get install libportaudio2; pip install sounddevice"
        printf "       STDERR: %s\n" "${_SD_OUTPUT}" >&2
    fi
else
    _skip "Interprete .venv no disponible — omitiendo test sounddevice"
fi

# =============================================================================
# VEREDICTO CONSOLIDADO
# =============================================================================
printf "\n"
printf "================================================================\n"
printf " VEREDICTO FINAL\n"
printf "================================================================\n"
printf " Total checks  : %d\n" "${TOTAL}"
printf " Passed        : %d\n" "${PASSED}"
printf " Failed        : %d\n" "${FAILED}"
printf "================================================================\n"

if [[ "${FAILED}" -eq 0 ]]; then
    printf "[GO]    Entorno validado. Autorizado para ejecutar start_robot.sh\n"
    exit 0
elif [[ "${FAILED}" -le 2 ]]; then
    printf "[WARN]  %d fallo(s) no bloqueante(s). Evaluar impacto antes de continuar.\n" \
           "${FAILED}"
    exit 2
else
    printf "[NO-GO] %d fallo(s) criticos. Resolver antes de continuar con Fase 1 HIL.\n" \
           "${FAILED}"
    exit 1
fi
