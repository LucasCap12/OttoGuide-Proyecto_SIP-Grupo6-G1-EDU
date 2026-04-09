from __future__ import annotations

from importlib import import_module
from typing import Any

_SYMBOL_MODULE_MAP: dict[str, str] = {
    "AsyncNav2Bridge": ".nav2_bridge",
    "NavWaypoint": ".nav2_bridge",
    "NavigationStatus": ".nav2_bridge",
}


def __getattr__(name: str) -> Any:
    module_name = _SYMBOL_MODULE_MAP.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name, package=__name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(__all__))

__all__ = [
    "AsyncNav2Bridge",
    "NavWaypoint",
    "NavigationStatus",
]