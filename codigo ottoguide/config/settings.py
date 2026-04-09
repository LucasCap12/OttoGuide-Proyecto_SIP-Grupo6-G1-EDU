from __future__ import annotations

# @TASK: Centralizar configuracion del sistema via Pydantic BaseSettings
# @INPUT: Variables de entorno (.env o shell)
# @OUTPUT: Instancia Settings singleton + factory get_hardware_adapter()
# @CONTEXT: Unico punto de configuracion; reemplaza variables dispersas
# @SECURITY: ROBOT_MODE default "mock" — nunca real sin intencion explicita
# STEP 1: Definir Settings con todas las variables del sistema
# STEP 2: Implementar factory get_hardware_adapter() con import lazy
# STEP 3: Validar ROBOT_NETWORK_INTERFACE requerida si ROBOT_MODE=real

import logging
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings

LOGGER = logging.getLogger("otto_guide.config.settings")


class Settings(BaseSettings):
    """
    @TASK: Modelo de configuracion centralizado del sistema OttoGuide
    @INPUT: Variables de entorno ROBOT_MODE, ROBOT_NETWORK_INTERFACE, etc.
    @OUTPUT: Instancia validada con valores por defecto seguros
    @CONTEXT: Pydantic BaseSettings lee automaticamente de .env y shell
    @SECURITY: ROBOT_MODE default "mock" previene inicializacion DDS accidental
    """

    # --- Hardware ---
    ROBOT_MODE: Literal["real", "sim", "mock"] = "mock"
    ROBOT_NETWORK_INTERFACE: str = ""

    # --- NLP / LLM ---
    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b"

    # --- API Server ---
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    @TASK: Obtener instancia singleton de Settings
    @INPUT: Sin parametros
    @OUTPUT: Settings cacheada
    @CONTEXT: lru_cache garantiza una sola lectura de .env por proceso
    @SECURITY: Inmutable una vez construida
    """
    return Settings()


def get_hardware_adapter():
    """
    @TASK: Factory de adaptador de hardware basada en ROBOT_MODE
    @INPUT: Settings.ROBOT_MODE y Settings.ROBOT_NETWORK_INTERFACE
    @OUTPUT: Instancia de RobotHardwareInterface (real, sim o mock)
    @CONTEXT: Import lazy — unitree_sdk2py solo se importa si mode=real|sim
    STEP 1: Si ROBOT_MODE=real y ROBOT_NETWORK_INTERFACE vacio → EnvironmentError
    STEP 2: Si ROBOT_MODE=real → importar lazy UnitreeG1Adapter, retornar instancia
    STEP 3: Si ROBOT_MODE=sim → importar lazy UnitreeG1SimAdapter, retornar instancia
              Nota: requiere unitree_mujoco corriendo en domain 1 (loopback)
    STEP 4: Si ROBOT_MODE=mock → importar MockRobotAdapter, retornar instancia
    STEP 5: Cualquier otro valor → ValueError con valores validos listados
    @SECURITY: unitree_sdk2py nunca se importa en modo mock
    """
    settings = get_settings()

    if settings.ROBOT_MODE == "real":
        # STEP 1: Validar interfaz de red
        if not settings.ROBOT_NETWORK_INTERFACE:
            raise EnvironmentError(
                "ROBOT_MODE=real requiere ROBOT_NETWORK_INTERFACE. "
                "Ejemplo: ROBOT_NETWORK_INTERFACE=eth0"
            )

        # STEP 2: Import lazy del adaptador real
        LOGGER.info(
            "[CONFIG] ROBOT_MODE=real. Cargando UnitreeG1Adapter "
            "(interface='%s').",
            settings.ROBOT_NETWORK_INTERFACE,
        )
        from hardware.real_adapter import UnitreeG1Adapter
        return UnitreeG1Adapter()

    if settings.ROBOT_MODE == "sim":
        # STEP 3: Import lazy del adaptador de simulacion
        LOGGER.info(
            "[CONFIG] ROBOT_MODE=sim. Cargando UnitreeG1SimAdapter "
            "(domain_id=1, interface=lo). "
            "Requiere unitree_mujoco corriendo en domain 1."
        )
        from hardware.sim_adapter import UnitreeG1SimAdapter
        return UnitreeG1SimAdapter()

    if settings.ROBOT_MODE == "mock":
        # STEP 4: Modo mock (default)
        LOGGER.info("[CONFIG] ROBOT_MODE=mock. Cargando MockRobotAdapter.")
        from hardware.mock_adapter import MockRobotAdapter
        return MockRobotAdapter()

    # STEP 5: Modo no reconocido
    raise ValueError(
        f"ROBOT_MODE='{settings.ROBOT_MODE}' no es valido. "
        "Valores validos: 'real', 'sim', 'mock'."
    )


__all__ = [
    "Settings",
    "get_hardware_adapter",
    "get_settings",
]
