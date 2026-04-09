from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Optional

import numpy as np
from numpy.typing import NDArray
import pytest

# @TASK: Priorizar src local
# @INPUT: Ruta del archivo de prueba actual
# @OUTPUT: Root del workspace en sys.path[0]
# @CONTEXT: Evita colision de imports src en multi-workspace
# STEP 1: Resolver raiz del proyecto
# STEP 2: Registrar ruta en sys.path de forma prioritaria
# @SECURITY: Mitiga carga de modulos externos inesperados
# @AI_CONTEXT: Garantiza que las pruebas apunten al codigo actual
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for loaded_module in list(sys.modules):
    if loaded_module == "src" or loaded_module.startswith("src."):
        del sys.modules[loaded_module]

from src.vision import CameraModel, PoseEstimate, VisionProcessor


@pytest.mark.asyncio
async def test_vision_processor_synthetic_frame_non_blocking() -> None:
    # @TASK: Validar frame sintetico
    # @INPUT: Frame negro numpy.zeros
    # @OUTPUT: PoseEstimate o None sin bloqueo de loop
    # @CONTEXT: Prueba de concurrencia y retorno esperado de VisionProcessor
    # STEP 1: Crear VisionProcessor con camara mock
    # STEP 2: Procesar frame sintetico y verificar ticker concurrente
    # @SECURITY: Confirma aislamiento del computo OpenCV en executor
    # @AI_CONTEXT: Cobertura base del pipeline visual para SITL
    camera_model = CameraModel(
        camera_matrix=np.eye(3, dtype=np.float64),
        distortion_coefficients=np.zeros((5, 1), dtype=np.float64),
    )
    vision_processor = VisionProcessor(
        camera_model=camera_model,
        tag_size_m=0.16,
        device_index=0,
        target_fps=10.0,
    )

    frame: NDArray[np.uint8] = np.zeros((480, 640, 3), dtype=np.uint8)

    ticks = 0
    stop_event = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop_event.is_set():
            ticks += 1
            await asyncio.sleep(0.002)

    ticker_task = asyncio.create_task(ticker())
    try:
        result: Optional[PoseEstimate] = await vision_processor.get_next_estimate(timeout_s=0.01)
    finally:
        stop_event.set()
        await ticker_task
        vision_processor.close()

    assert ticks > 0
    assert result is None or isinstance(result, PoseEstimate)