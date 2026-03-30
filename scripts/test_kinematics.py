#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


# @TASK: Configurar rutas runtime
# @INPUT: Ubicacion del script y estructura del proyecto
# @OUTPUT: sys.path con raiz y SDK local
# @CONTEXT: Permite resolver RobotHardwareAPI y unitree_sdk2_python en HIL
# STEP 1: Resolver directorio raiz del proyecto desde scripts/
# STEP 2: Insertar rutas requeridas en prioridad de importacion
# @SECURITY: Limita resolucion de modulos al arbol de despliegue esperado
# @AI_CONTEXT: Evita ModuleNotFoundError en companion PC air-gapped
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
SDK_ROOT: Path = PROJECT_ROOT / "libs" / "unitree_sdk2_python-master"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from src.hardware import RobotHardwareAPI


async def run_kinematics_smoke_test() -> None:
    # @TASK: Ejecutar sanity kinematica
    # @INPUT: Sin parametros
    # @OUTPUT: Secuencia Damp->Euler->Damp completada
    # @CONTEXT: Validacion HIL de enlace DDS unicast y SDK Unitree real
    # STEP 1: Inicializar RobotHardwareAPI y asegurar postura base con Damp
    # STEP 2: Ejecutar inclinacion minima de pitch y retornar a Damp
    # @SECURITY: Bloque finally garantiza comando Damp aun ante excepciones
    # @AI_CONTEXT: No instancia TourOrchestrator ni ROS2 por requerimiento
    hardware_api = RobotHardwareAPI.get_instance()
    try:
        await hardware_api.damp()
        await asyncio.sleep(2)
        await hardware_api.euler(0.0, 0.1, 0.0)
        await asyncio.sleep(2)
    finally:
        try:
            await hardware_api.damp()
        finally:
            hardware_api.close()


async def main() -> None:
    # @TASK: Ejecutar entrypoint async
    # @INPUT: Sin parametros
    # @OUTPUT: Finalizacion controlada del sanity check
    # @CONTEXT: Punto unico de ejecucion para prueba de humo fisica
    # STEP 1: Lanzar secuencia principal del test
    # STEP 2: Propagar excepciones al proceso llamador
    # @SECURITY: Delega failsafe al finally de la rutina principal
    # @AI_CONTEXT: Integrable con script start_robot.sh o ejecucion manual
    await run_kinematics_smoke_test()


if __name__ == "__main__":
    # @TASK: Invocar ciclo asyncio
    # @INPUT: Sin parametros
    # @OUTPUT: Ejecucion de la corrutina principal
    # @CONTEXT: Bootstrap local para companion PC en pruebas HIL
    # STEP 1: Ejecutar main() con asyncio.run
    # STEP 2: Permitir que errores salgan con codigo no-cero
    # @SECURITY: Mantiene semantica de fallo visible para operador
    # @AI_CONTEXT: Uso directo desde shell: python scripts/test_kinematics.py
    asyncio.run(main())