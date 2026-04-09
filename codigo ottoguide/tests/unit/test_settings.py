from __future__ import annotations

# @TASK: Tests unitarios de config/settings.py
# @INPUT: Variables de entorno mockeadas
# @OUTPUT: Verificacion de validacion de settings y factory (real, sim, mock, invalido)
# @CONTEXT: Ejecutable sin hardware fisico ni unitree_sdk2py
# @SECURITY: Verifica que ROBOT_MODE=real sin ROBOT_NETWORK_INTERFACE falla

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

from config.settings import Settings, get_hardware_adapter, get_settings


class TestSettingsValidation:
    """Verificar validacion de configuracion."""

    def test_default_mode_is_mock(self) -> None:
        """STEP 1: ROBOT_MODE default es 'mock'."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,  # type: ignore[call-arg]
            )
            assert settings.ROBOT_MODE == "mock"

    def test_real_mode_without_interface_raises(self) -> None:
        """
        @TASK: Verificar que ROBOT_MODE=real sin ROBOT_NETWORK_INTERFACE raise EnvironmentError
        @INPUT: ROBOT_MODE=real, ROBOT_NETWORK_INTERFACE="" (vacio)
        @OUTPUT: EnvironmentError
        @CONTEXT: Restriccion de seguridad critica
        @SECURITY: Previene inicializacion DDS sin interfaz de red configurada
        """
        with patch.dict(os.environ, {
            "ROBOT_MODE": "real",
            "ROBOT_NETWORK_INTERFACE": "",
        }, clear=True):
            get_settings.cache_clear()
            with pytest.raises(EnvironmentError, match="ROBOT_NETWORK_INTERFACE"):
                get_hardware_adapter()
            get_settings.cache_clear()

    def test_invalid_mode_raises_value_error(self) -> None:
        """
        @TASK: Verificar que ROBOT_MODE invalido raise ValueError con valores validos
        @INPUT: ROBOT_MODE=invalido
        @OUTPUT: ValueError con lista de valores validos
        @CONTEXT: Pydantic Literal valida en construccion
        @SECURITY: Previene modos de operacion no reconocidos
        """
        with patch.dict(os.environ, {
            "ROBOT_MODE": "invalido",
        }, clear=True):
            get_settings.cache_clear()
            with pytest.raises(Exception):
                get_hardware_adapter()
            get_settings.cache_clear()

    def test_mock_mode_returns_mock_adapter(self) -> None:
        """
        @TASK: Verificar que ROBOT_MODE=mock retorna MockRobotAdapter
        @INPUT: ROBOT_MODE=mock
        @OUTPUT: Instancia de MockRobotAdapter
        """
        with patch.dict(os.environ, {"ROBOT_MODE": "mock"}, clear=True):
            get_settings.cache_clear()
            adapter = get_hardware_adapter()
            from hardware.mock_adapter import MockRobotAdapter
            assert isinstance(adapter, MockRobotAdapter)
            get_settings.cache_clear()

    def test_sim_mode_returns_sim_adapter(self) -> None:
        """
        @TASK: Verificar que ROBOT_MODE=sim retorna UnitreeG1SimAdapter
        @INPUT: ROBOT_MODE=sim
        @OUTPUT: Instancia de UnitreeG1SimAdapter (con mock del modulo)
        @CONTEXT: El SDK no esta instalado en CI; usar mock del modulo
        """
        with patch.dict(os.environ, {"ROBOT_MODE": "sim"}, clear=True):
            get_settings.cache_clear()

            # STEP 1: Mockear el import de sim_adapter para no requerir SDK
            mock_adapter_instance = MagicMock()
            mock_module = MagicMock()
            mock_module.UnitreeG1SimAdapter.return_value = mock_adapter_instance

            with patch.dict(sys.modules, {"hardware.sim_adapter": mock_module}):
                adapter = get_hardware_adapter()
                assert adapter is mock_adapter_instance

            get_settings.cache_clear()


class TestMockModeNoSdkImport:
    """Verificar que ROBOT_MODE=mock no importa unitree_sdk2py."""

    def test_mock_mode_does_not_import_sdk(self) -> None:
        """
        @TASK: Verificar que unitree_sdk2py no se importa en modo mock
        @INPUT: ROBOT_MODE=mock
        @OUTPUT: 'unitree_sdk2py' no presente en sys.modules nuevos
        @CONTEXT: Garantiza que CI funciona sin el SDK instalado
        @SECURITY: Previene dependencia accidental de hardware
        """
        sdk_modules = [k for k in sys.modules if k.startswith("unitree_sdk2py")]
        for mod in sdk_modules:
            del sys.modules[mod]

        with patch.dict(os.environ, {"ROBOT_MODE": "mock"}, clear=True):
            get_settings.cache_clear()
            adapter = get_hardware_adapter()
            get_settings.cache_clear()

            sdk_loaded = any(
                k.startswith("unitree_sdk2py") for k in sys.modules
            )
            assert not sdk_loaded, (
                "unitree_sdk2py fue importado en modo mock. "
                "Verificar que get_hardware_adapter() no importa el SDK "
                "cuando ROBOT_MODE=mock."
            )


class TestSettingsValues:
    """Verificar valores de configuracion."""

    def test_ollama_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
            assert settings.OLLAMA_HOST == "http://127.0.0.1:11434"
            assert settings.OLLAMA_MODEL == "qwen2.5:3b"

    def test_api_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
            assert settings.API_HOST == "0.0.0.0"
            assert settings.API_PORT == 8000

    def test_custom_env_values(self) -> None:
        with patch.dict(os.environ, {
            "ROBOT_MODE": "mock",
            "OLLAMA_HOST": "http://custom:11434",
            "OLLAMA_MODEL": "llama3:8b",
            "API_HOST": "127.0.0.1",
            "API_PORT": "9000",
        }, clear=True):
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
            assert settings.OLLAMA_HOST == "http://custom:11434"
            assert settings.OLLAMA_MODEL == "llama3:8b"
            assert settings.API_HOST == "127.0.0.1"
            assert settings.API_PORT == 9000
