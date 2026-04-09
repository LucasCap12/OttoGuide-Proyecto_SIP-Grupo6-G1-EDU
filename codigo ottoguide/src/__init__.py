# @TASK: Definir paquete src
# @INPUT: Sin parametros
# @OUTPUT: Paquete Python regular local
# @CONTEXT: Evita colisiones de namespace package en entorno multi-workspace
# STEP 1: Marcar directorio src como paquete explicito
# STEP 2: Priorizar imports del proyecto actual
# @SECURITY: Reduce riesgo de importar modulos externos por accidente
# @AI_CONTEXT: Necesario para ejecucion de tests en entornos compartidos
