"""
@TASK: Implementar logger de auditoria de mision con persistencia atomica en JSON
@INPUT: Eventos FSM desde TourOrchestrator (TOUR_START, NODE_REACHED, INTERACTION_COMPLETED,
        EMERGENCY_TRIGGERED, TOUR_END) junto con node_id y payload arbitrario
@OUTPUT: Archivo JSON por mision en logs/<mission_id>.json con escritura atomica via rename
@CONTEXT: Componente de observabilidad HIL; unico punto de persistencia de eventos de mision.
          Invocado exclusivamente por _schedule_audit_event() en TourOrchestrator.

STEP 1: Definir conjunto de eventos validos para validacion de contrato en _ALLOWED_EVENTS
STEP 2: Inicializar archivo de mision con estructura JSON base en start_mission()
STEP 3: Acumular eventos con append atomico en log_event()
STEP 4: Garantizar escritura atomica via archivo .tmp + fsync + os.replace
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class MissionAuditLogger:
    """
    @TASK: Persistir eventos de auditoria de mision (FSM) en archivos JSON con escritura atomica
    @INPUT: event_type, node_id y payload desde TourOrchestrator
    @OUTPUT: Archivo JSON en logs/<mission_id>.json con array de eventos cronologico
    @CONTEXT: Invocado por _schedule_audit_event() del orquestador; opera en el event loop.
              asyncio.Lock garantiza acceso exclusivo al archivo activo por evento.

    El atributo de clase _ALLOWED_EVENTS define la whitelist de tipos de evento validos.
    Contrato: cualquier event_type fuera de este conjunto produce ValueError inmediato,
    previniendo inyeccion de campos arbitrarios en el registro de auditoria.
    """

    _ALLOWED_EVENTS = {
        "TOUR_START",
        "NODE_REACHED",
        "INTERACTION_COMPLETED",
        "EMERGENCY_TRIGGERED",
        "TOUR_END",
    }

    def __init__(self, logs_dir: Optional[Path] = None) -> None:
        """
        @TASK: Inicializar el logger con el directorio de destino de archivos de auditoria
        @INPUT: logs_dir — Path al directorio de logs o None para usar <proyecto>/logs/
        @OUTPUT: Instancia lista con _active_file=None; ningun archivo es creado hasta start_mission()
        @CONTEXT: Constructor ligero; el directorio se crea en start_mission() si no existe,
                  nunca en el constructor, para evitar side-effects en tiempo de instanciacion.
        """
        base_dir = Path(__file__).resolve().parents[2]
        self._logs_dir = logs_dir or (base_dir / "logs")
        self._active_file: Optional[Path] = None
        self._active_mission_id: Optional[str] = None
        self._io_lock = asyncio.Lock()

    @property
    def active_file(self) -> Optional[Path]:
        """
        @TASK: Exponer la ruta del archivo de auditoria de la mision activa
        @INPUT: Sin parametros
        @OUTPUT: Path del archivo JSON activo o None si no hay mision en curso
        @CONTEXT: Propiedad de observabilidad de solo lectura para diagnostico externo
        """
        return self._active_file

    async def start_mission(self, mission_id: Optional[str] = None) -> Path:
        """
        @TASK: Abrir un nuevo archivo de auditoria para una mision y preparar su estructura JSON
        @INPUT: mission_id — identificador opcional; si es None se genera desde timestamp UTC
        @OUTPUT: Path del archivo JSON recien creado; self._active_file y self._active_mission_id actualizados
        @CONTEXT: Invocado por log_event() si no hay mision activa; tambien invocable manualmente.
                  asyncio.Lock garantiza que no se inicialicen dos misiones de forma concurrente.

        STEP 1: Generar token de timestamp UTC y construir nombre de archivo unico
        STEP 2: Construir documento JSON inicial con structure base (mission_id, created_at, events=[])
        STEP 3: Escribir atomicamente el documento en disco y activar como mision actual bajo lock
        """
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
        """
        @TASK: Persistir un evento de FSM en el archivo de auditoria activo
        @INPUT: event_type — tipo del evento; debe pertenecer a _ALLOWED_EVENTS
                node_id — identificador del waypoint o nodo logico de la mision
                payload — dict con datos adicionales especificos del evento
        @OUTPUT: Evento appendeado atomicamente al array 'events' del JSON activo; sin retorno
        @CONTEXT: Invocado desde _schedule_audit_event() del TourOrchestrator.
                  ValueError ante event_type invalido previene inyeccion de campos arbitrarios.

        STEP 1: Validar que event_type pertenece al conjunto _ALLOWED_EVENTS
        STEP 2: Invocar start_mission() si no hay archivo activo (primer evento de la sesion)
        STEP 3: Construir objeto de evento con timestamp UTC, event_type, node_id y payload
        STEP 4: Acumular evento en el archivo activo de forma atomica bajo asyncio.Lock
        """
        if event_type not in self._ALLOWED_EVENTS:
            raise ValueError(
                f"event_type invalido: {event_type!r}. Valores aceptados: {self._ALLOWED_EVENTS}"
            )

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
        """
        @TASK: Crear el archivo de auditoria en disco con la estructura JSON inicial
        @INPUT: file_path — ruta de destino del archivo JSON
                initial_doc — documento base con mission_id, created_at y events vacio
        @OUTPUT: Archivo JSON escrito atomicamente en disco; directorio padre creado si no existe
        @CONTEXT: Ejecutado en executor de IO desde start_mission(); es bloqueante intencionalmente.
                  mkdir con exist_ok=True es idempotente; sin race condition en la creacion.

        STEP 1: Crear el directorio padre con parents=True, exist_ok=True
        STEP 2: Escribir el documento inicial de forma atomica via _atomic_write_json
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(file_path, initial_doc)

    def _append_event_sync(self, file_path: Path, event: dict[str, Any]) -> None:
        """
        @TASK: Leer el documento JSON activo, appendear el nuevo evento y reescribir atomicamente
        @INPUT: file_path — ruta del archivo de auditoria activo
                event — dict del evento ya construido con timestamp, event_type, node_id y payload
        @OUTPUT: Documento JSON en disco actualizado con el nuevo evento al final del array 'events'
        @CONTEXT: Ejecutado en executor de IO desde log_event(); bloqueante.
                  Ante json.JSONDecodeError el documento se reconstruye desde cero sin perder
                  la referencia a la mision activa (_active_mission_id).

        STEP 1: Leer y parsear el documento existente; reconstruir estructura base ante corrupcion
        STEP 2: Appendear el evento al array y reescribir el documento completo de forma atomica
        """
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
        """
        @TASK: Escribir un documento JSON en disco con garantia de atomicidad via rename
        @INPUT: file_path — ruta final de destino del archivo JSON
                document — dict serializable como JSON con el contenido completo a persistir
        @OUTPUT: Archivo en file_path con contenido consistente y completo; ninguna escritura
                 parcial es visible externamente en ningun momento del proceso
        @CONTEXT: Patron write-to-tmp + fsync + os.replace; resistente a SIGKILL entre pasos.
                  os.replace es la unica operacion visible al exterior; garantizado por POSIX.

        STEP 1: Serializar y escribir el documento en archivo temporal adyacente al destino
        STEP 2: Invocar fsync para garantizar flush fisico a disco antes del rename
        STEP 3: Ejecutar os.replace atomico (rename POSIX) para hacer visible el nuevo contenido
        """
        temporary_path = file_path.with_suffix(file_path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, file_path)
