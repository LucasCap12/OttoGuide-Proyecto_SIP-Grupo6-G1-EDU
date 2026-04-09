from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket


class TelemetryManager:
    """Gestiona clientes WebSocket y difusion de telemetria asincrona."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Acepta un cliente WebSocket y lo registra para difusion."""
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        """Elimina un cliente WebSocket de la lista de conexiones activas."""
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Difunde telemetria a todos los clientes registrados."""
        payload = self._normalize_payload(message)
        async with self._lock:
            sockets = tuple(self._connections)
        if not sockets:
            return
        await asyncio.gather(*(self._send_payload(ws, payload) for ws in sockets), return_exceptions=True)

    async def _send_payload(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        try:
            await websocket.send_json(payload)
        except Exception:
            await self.disconnect(websocket)

    @staticmethod
    def _normalize_payload(message: dict[str, Any]) -> dict[str, Any]:
        payload = dict(message)
        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        payload.setdefault("fsm_state", "UNKNOWN")
        payload.setdefault("current_waypoint_id", "N/A")
        payload.setdefault("battery_level", None)
        return payload
