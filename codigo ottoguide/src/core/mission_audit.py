from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class MissionAuditLogger:
    """Persistencia de auditoria de mision con eventos de FSM en archivos JSON."""

    _ALLOWED_EVENTS = {
        "TOUR_START",
        "NODE_REACHED",
        "INTERACTION_COMPLETED",
        "EMERGENCY_TRIGGERED",
        "TOUR_END",
    }

    def __init__(self, logs_dir: Optional[Path] = None) -> None:
        base_dir = Path(__file__).resolve().parents[2]
        self._logs_dir = logs_dir or (base_dir / "logs")
        self._active_file: Optional[Path] = None
        self._active_mission_id: Optional[str] = None
        self._io_lock = asyncio.Lock()

    @property
    def active_file(self) -> Optional[Path]:
        """Retorna la ruta de auditoria activa de la mision actual."""
        return self._active_file

    async def start_mission(self, mission_id: Optional[str] = None) -> Path:
        """Inicializa archivo de auditoria para una nueva mision."""
        now = datetime.now(timezone.utc)
        timestamp_token = now.strftime("%Y%m%dT%H%M%S%fZ")
        resolved_mission_id = mission_id or f"mission_{timestamp_token}"
        file_path = self._logs_dir / f"mission_{timestamp_token}.json"
        initial_doc = {
            "mission_id": resolved_mission_id,
            "created_at": now.isoformat(),
            "events": [],
        }
        loop = asyncio.get_running_loop()
        async with self._io_lock:
            await loop.run_in_executor(None, self._initialize_file_sync, file_path, initial_doc)
            self._active_file = file_path
            self._active_mission_id = resolved_mission_id
        return file_path

    async def log_event(self, event_type: str, node_id: str, payload: dict[str, Any]) -> None:
        """Registra evento de mision con persistencia atomica en JSON."""
        if event_type not in self._ALLOWED_EVENTS:
            raise ValueError(f"event_type invalido: {event_type}")

        if self._active_file is None:
            await self.start_mission()

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "node_id": node_id,
            "payload": payload,
        }

        loop = asyncio.get_running_loop()
        async with self._io_lock:
            if self._active_file is None:
                raise RuntimeError("No existe archivo de auditoria activo para registrar eventos.")
            file_path = self._active_file
            await loop.run_in_executor(None, self._append_event_sync, file_path, event)

    def _initialize_file_sync(self, file_path: Path, initial_doc: dict[str, Any]) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(file_path, initial_doc)

    def _append_event_sync(self, file_path: Path, event: dict[str, Any]) -> None:
        if file_path.exists():
            raw_content = file_path.read_text(encoding="utf-8")
            try:
                document = json.loads(raw_content)
            except json.JSONDecodeError:
                document = {
                    "mission_id": self._active_mission_id or "unknown_mission",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "events": [],
                }
        else:
            document = {
                "mission_id": self._active_mission_id or "unknown_mission",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "events": [],
            }

        events = document.get("events")
        if not isinstance(events, list):
            events = []

        events.append(event)
        document["events"] = events
        self._atomic_write_json(file_path, document)

    @staticmethod
    def _atomic_write_json(file_path: Path, document: dict[str, Any]) -> None:
        temporary_path = file_path.with_suffix(file_path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, file_path)
