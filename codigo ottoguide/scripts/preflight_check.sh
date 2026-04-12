#!/usr/bin/env bash
# @TASK: Ejecutar gate pre-vuelo RC1 para operacion HIL antes de arrancar el backend.
# @INPUT: Variables de entorno ROS_DOMAIN_ID/CYCLONEDDS_URI, estado systemd de ollama, script SRE Python.
# @OUTPUT: Exit 0 si el entorno cumple precondiciones; Exit 1 ante cualquier incumplimiento.
# @CONTEXT: Paso 5 del RUNBOOK_STARTUP_RC1 como barrera obligatoria previa a start_robot.sh.
# @SECURITY: Falla cerrada; no arranca componentes core si cualquier validacion falla.
# STEP 1: Verificar ROS_DOMAIN_ID=0.
# STEP 2: Verificar CYCLONEDDS_URI definido y con esquema file://.
# STEP 3: Verificar servicio systemd ollama en estado active.
# STEP 4: Ejecutar sre_health_check.py como barrera final no negociable.

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRE_HEALTH_SCRIPT="${PROJECT_ROOT}/scripts/sre_health_check.py"

log_info() {
  echo "[INFO] $1"
}

log_error() {
  echo "[ERROR] $1" >&2
}

# @TASK: Validar variable critica ROS_DOMAIN_ID para dominio DDS esperado en RC1.
# @INPUT: ROS_DOMAIN_ID desde entorno del proceso.
# @OUTPUT: Continue si ROS_DOMAIN_ID=0; aborta con exit 1 si no.
# @CONTEXT: Compatibilidad con topologia CycloneDDS definida para Companion PC.
# @SECURITY: Previene mezcla de dominios DDS entre stacks concurrentes.
if [[ "${ROS_DOMAIN_ID:-}" != "0" ]]; then
  log_error "ROS_DOMAIN_ID debe ser 0. Valor actual: '${ROS_DOMAIN_ID:-unset}'"
  exit 1
fi
log_info "ROS_DOMAIN_ID=0 validado"

# @TASK: Validar URI de configuracion CycloneDDS requerida por despliegue HIL.
# @INPUT: CYCLONEDDS_URI desde entorno del proceso.
# @OUTPUT: Continue si la variable existe y usa esquema file://; aborta si no.
# @CONTEXT: Alineado con configuración unicast RC1 en config/cyclonedds.xml.
# @SECURITY: Evita arranque con transporte DDS ambiguo o no auditado.
if [[ -z "${CYCLONEDDS_URI:-}" ]]; then
  log_error "CYCLONEDDS_URI no definida"
  exit 1
fi
if [[ "${CYCLONEDDS_URI}" != file://* ]]; then
  log_error "CYCLONEDDS_URI debe iniciar con file://. Valor actual: '${CYCLONEDDS_URI}'"
  exit 1
fi
log_info "CYCLONEDDS_URI validada: ${CYCLONEDDS_URI}"

# @TASK: Verificar que el servicio Ollama este activo en systemd.
# @INPUT: Estado de unidad systemctl 'ollama'.
# @OUTPUT: Continue si estado es active; aborta en cualquier otro estado.
# @CONTEXT: Precondicion de disponibilidad LLM local para pipeline interactivo.
# @SECURITY: Previene arranque con backend NLP degradado.
if ! command -v systemctl >/dev/null 2>&1; then
  log_error "systemctl no disponible en el host"
  exit 1
fi

OLLAMA_STATUS="$(systemctl is-active ollama 2>/dev/null || true)"
if [[ "${OLLAMA_STATUS}" != "active" ]]; then
  log_error "Servicio ollama no activo. Estado actual: '${OLLAMA_STATUS:-unknown}'"
  exit 1
fi
log_info "Servicio ollama activo"

# @TASK: Ejecutar barrera final de health check SRE.
# @INPUT: Script Python scripts/sre_health_check.py y entorno operativo actual.
# @OUTPUT: Continue si el script retorna 0; aborta en retorno no-cero.
# @CONTEXT: Integracion de validaciones de red/audio/puerto LLM en una sola barrera final.
# @SECURITY: No permite bypass silencioso de checks tecnicos criticos.
if [[ ! -f "${SRE_HEALTH_SCRIPT}" ]]; then
  log_error "Script no encontrado: ${SRE_HEALTH_SCRIPT}"
  exit 1
fi

if ! python3 "${SRE_HEALTH_SCRIPT}"; then
  log_error "sre_health_check.py reporto estado NO-GO"
  exit 1
fi

log_info "Preflight RC1 finalizado en estado GO"
exit 0
