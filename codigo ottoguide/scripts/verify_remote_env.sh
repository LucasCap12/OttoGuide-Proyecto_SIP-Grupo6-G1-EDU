#!/usr/bin/env bash
# @TASK: Ejecutar validacion extendida remota del entorno HIL previo al arranque maestro.
# @INPUT: Estado de interfaces de red, permisos de /opt/ottoguide/logs, sockets de OS (8000 y DDS domain 0).
# @OUTPUT: Exit 0 en estado GO; Exit 1 en estado NO-GO.
# @CONTEXT: Paso 6 del RUNBOOK_STARTUP_RC1 para Companion PC Unitree G1 EDU 8.
# @SECURITY: Solo inspeccion local del host; sin comandos de locomocion ni mutaciones de FSM.
# STEP 1: Verificar IP 192.168.123.x en eth0 o wlan0.
# STEP 2: Verificar escritura en /opt/ottoguide/logs/ para MissionAuditLogger.
# STEP 3: Verificar que no hay listeners en puerto 8000.
# STEP 4: Verificar que no hay sockets DDS domain 0 ocupando rango UDP 7400-7500.

set -e

log_info() {
  echo "[INFO] $1"
}

log_error() {
  echo "[ERROR] $1" >&2
}

# @TASK: Resolver interfaz candidata con IP del segmento de operacion HIL.
# @INPUT: Interfaces eth0 y wlan0 del host.
# @OUTPUT: GO si al menos una interfaz tiene IPv4 192.168.123.x; NO-GO si ninguna coincide.
# @CONTEXT: Red operativa del robot y companion en segmento dedicado 192.168.123.0/24.
# @SECURITY: Lectura de estado de red sin alterar configuracion.
HAS_SEGMENT_IP="false"
SEGMENT_IP=""
SEGMENT_IFACE=""

for IFACE in eth0 wlan0; do
  if ip link show "${IFACE}" >/dev/null 2>&1; then
    IFACE_IP="$(ip -4 -o addr show dev "${IFACE}" | awk '{print $4}' | cut -d/ -f1 | head -n1)"
    if [[ -n "${IFACE_IP}" ]] && [[ "${IFACE_IP}" =~ ^192\.168\.123\.[0-9]{1,3}$ ]]; then
      HAS_SEGMENT_IP="true"
      SEGMENT_IP="${IFACE_IP}"
      SEGMENT_IFACE="${IFACE}"
      break
    fi
  fi
done

if [[ "${HAS_SEGMENT_IP}" != "true" ]]; then
  log_error "NO-GO red: eth0/wlan0 sin IP en segmento 192.168.123.x"
  exit 1
fi
log_info "GO red: ${SEGMENT_IFACE}=${SEGMENT_IP}"

# @TASK: Verificar permisos de escritura para auditoria de mision en /opt/ottoguide/logs/.
# @INPUT: Ruta fija /opt/ottoguide/logs/.
# @OUTPUT: GO si se puede crear/escribir/borrar archivo de prueba; NO-GO en caso contrario.
# @CONTEXT: Requisito operativo para persistir mission_*.json en contingencia EMERGENCY.
# @SECURITY: Solo crea archivo temporal de prueba y lo elimina inmediatamente.
LOG_DIR="/opt/ottoguide/logs"
TEST_FILE="${LOG_DIR}/.write_probe_$$.tmp"

if [[ ! -d "${LOG_DIR}" ]]; then
  log_error "NO-GO auditoria: directorio ausente ${LOG_DIR}"
  exit 1
fi

if ! touch "${TEST_FILE}" >/dev/null 2>&1; then
  log_error "NO-GO auditoria: sin permiso de escritura en ${LOG_DIR}"
  exit 1
fi

if ! echo "probe" > "${TEST_FILE}"; then
  rm -f "${TEST_FILE}" >/dev/null 2>&1 || true
  log_error "NO-GO auditoria: escritura fallida en ${LOG_DIR}"
  exit 1
fi

rm -f "${TEST_FILE}" >/dev/null 2>&1 || true
log_info "GO auditoria: escritura habilitada en ${LOG_DIR}"

# @TASK: Verificar ausencia de procesos ocupando puerto 8000 (uvicorn stale/zombie run).
# @INPUT: Tabla de sockets TCP en escucha.
# @OUTPUT: GO si 8000 libre; NO-GO si detecta listener activo.
# @CONTEXT: Evita colisiones de arranque en mvp_master_run.sh.
# @SECURITY: Solo inspeccion de sockets; no finaliza procesos automaticamente.
if ss -ltn | awk '{print $4}' | grep -Eq '(^|:)8000$'; then
  log_error "NO-GO puertos: 8000/tcp ocupado por proceso previo"
  exit 1
fi
log_info "GO puertos: 8000/tcp libre"

# @TASK: Verificar ausencia de ocupacion en rango DDS domain 0 (UDP 7400-7500).
# @INPUT: Tabla de sockets UDP en escucha.
# @OUTPUT: GO si no hay listeners en rango DDS0; NO-GO si hay ocupacion.
# @CONTEXT: Previene colisiones de participantes DDS previos antes de iniciar stack HIL.
# @SECURITY: Inspeccion pasiva de kernel socket table sin afectar procesos.
if ss -lun | awk '{print $5}' | grep -Eq ':(74[0-9]{2}|7500)$'; then
  log_error "NO-GO DDS: dominio 0 ocupado (UDP 7400-7500 detectado)"
  exit 1
fi
log_info "GO DDS: dominio 0 sin sockets ocupados"

log_info "GO verify_remote_env completado"
exit 0
