from __future__ import annotations

import asyncio
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def bootstrap_mock_dependencies() -> None:
    if "src.hardware" not in sys.modules:
        hardware_module = types.ModuleType("src.hardware")

        class RobotHardwareAPI:
            pass

        class RobotHardwareAPIError(Exception):
            pass

        hardware_module.RobotHardwareAPI = RobotHardwareAPI
        hardware_module.RobotHardwareAPIError = RobotHardwareAPIError
        sys.modules["src.hardware"] = hardware_module

    if "src.navigation" not in sys.modules:
        navigation_module = types.ModuleType("src.navigation")

        @dataclass(frozen=True, slots=True)
        class NavWaypoint:
            x: float
            y: float
            yaw_rad: float
            frame_id: str = "map"

        class AsyncNav2Bridge:
            pass

        navigation_module.NavWaypoint = NavWaypoint
        navigation_module.AsyncNav2Bridge = AsyncNav2Bridge
        sys.modules["src.navigation"] = navigation_module

    if "src.vision" not in sys.modules:
        vision_module = types.ModuleType("src.vision")

        @dataclass(frozen=True, slots=True)
        class PoseEstimate:
            x: float = 0.0
            y: float = 0.0
            theta: float = 0.0
            frame_id: str = "map"

        @dataclass(frozen=True, slots=True)
        class OdometryVector:
            marker_id: int = 0
            x: float = 0.0
            y: float = 0.0
            theta: float = 0.0
            pose_estimate: PoseEstimate = field(default_factory=PoseEstimate)

        class VisionProcessor:
            pass

        vision_module.PoseEstimate = PoseEstimate
        vision_module.OdometryVector = OdometryVector
        vision_module.VisionProcessor = VisionProcessor
        sys.modules["src.vision"] = vision_module

    if "src.interaction" not in sys.modules:
        interaction_module = types.ModuleType("src.interaction")

        @dataclass(frozen=True, slots=True)
        class ConversationRequest:
            user_text: str

        @dataclass(frozen=True, slots=True)
        class ConversationResponse:
            answer_text: str
            source_pipeline: str
            audio_stream_ready: bool

        class ConversationManager:
            pass

        interaction_module.ConversationRequest = ConversationRequest
        interaction_module.ConversationResponse = ConversationResponse
        interaction_module.ConversationManager = ConversationManager
        sys.modules["src.interaction"] = interaction_module


bootstrap_mock_dependencies()

from src.api.websocket_manager import TelemetryManager
from src.core import MissionAuditLogger, TourOrchestrator, TourPlan
from src.navigation import NavWaypoint


@dataclass(frozen=True, slots=True)
class MockConversationResponse:
    answer_text: str
    source_pipeline: str
    audio_stream_ready: bool


class MockHardwareAPI:
    async def move(self, *args: Any) -> None:
        return None

    async def damp(self) -> None:
        return None

    async def get_state(self) -> dict[str, float]:
        return {"battery_level": 88.0}


class MockNavBridge:
    def __init__(self, travel_delay_s: float = 1.0) -> None:
        self._travel_delay_s = travel_delay_s
        self.completed_goals = 0

    async def navigate_to_waypoints(self, waypoints: list[NavWaypoint]) -> bool:
        await asyncio.sleep(self._travel_delay_s)
        self.completed_goals += len(waypoints)
        return True

    async def cancel_navigation(self) -> None:
        return None

    async def inject_absolute_pose(self, pose: Any) -> None:
        return None


class MockVisionProcessor:
    async def get_next_estimate(self, *, timeout_s: float = 0.5) -> Any:
        await asyncio.sleep(min(timeout_s, 0.05))
        return None

    def close(self) -> None:
        return None


class MockConversationManager:
    def __init__(self) -> None:
        self.swap_count = 0
        self.active_strategy_name = "mock"
        self._active_zone = "I"
        self.llm_qa_invocations = 0
        self.last_llm_answer = ""

    def get_waypoint_interaction_type(self, waypoint_id: str) -> str:
        if waypoint_id == "F":
            return "llm_qa"
        return "scripted"

    def set_active_zone(self, zone_id: str) -> None:
        self._active_zone = zone_id

    async def process_scripted_interaction(self, waypoint_id: str) -> MockConversationResponse:
        return MockConversationResponse(
            answer_text=f"scripted_{waypoint_id}",
            source_pipeline="scripted",
            audio_stream_ready=True,
        )

    async def process_interaction(
        self,
        audio_buffer: np.ndarray,
        *,
        language: str = "es",
    ) -> MockConversationResponse:
        if self._active_zone == "F":
            self.llm_qa_invocations += 1
            self.last_llm_answer = "respuesta simulada ollama"
            return MockConversationResponse(
                answer_text=self.last_llm_answer,
                source_pipeline="llm_qa_mock",
                audio_stream_ready=True,
            )
        return MockConversationResponse(
            answer_text="respuesta simulada",
            source_pipeline="scripted_mock",
            audio_stream_ready=True,
        )

    async def respond(self, request: Any) -> MockConversationResponse:
        return await self.process_interaction(np.zeros(8, dtype=np.float32), language="es")

    def get_waypoint_pose_2d(self, waypoint_id: str) -> tuple[float, float, float]:
        positions = {
            "I": (0.0, 0.0, 0.0),
            "1": (1.0, 0.0, 0.0),
            "2": (2.0, 0.0, 0.0),
            "3": (3.0, 0.0, 0.0),
            "F": (4.0, 0.0, 0.0),
        }
        return positions.get(waypoint_id, (0.0, 0.0, 0.0))


async def wait_for_state(orchestrator: TourOrchestrator, expected_state: str, timeout_s: float) -> None:
    start = asyncio.get_running_loop().time()
    while orchestrator.state_id != expected_state:
        if asyncio.get_running_loop().time() - start > timeout_s:
            raise TimeoutError(
                f"Timeout esperando estado {expected_state}; estado actual {orchestrator.state_id}"
            )
        await asyncio.sleep(0.1)


async def run_validation() -> int:
    os.environ["ROBOT_MODE"] = "mock"

    telemetry_manager = TelemetryManager()
    audit_logger = MissionAuditLogger()
    hardware_api = MockHardwareAPI()
    nav_bridge = MockNavBridge(travel_delay_s=1.0)
    conversation_manager = MockConversationManager()
    vision_processor = MockVisionProcessor()

    orchestrator = TourOrchestrator(
        hardware_api=hardware_api,
        nav_bridge=nav_bridge,
        conversation_manager=conversation_manager,
        vision_processor=vision_processor,
        telemetry_manager=telemetry_manager,
        mission_audit_logger=audit_logger,
    )

    await orchestrator.activate_initial_state()

    await telemetry_manager.broadcast(
        {
            "fsm_state": orchestrator.state_id,
            "current_waypoint_id": "I",
            "battery_level": 88.0,
        }
    )

    waypoints = [
        NavWaypoint(x=0.0, y=0.0, yaw_rad=0.0),
        NavWaypoint(x=1.0, y=0.0, yaw_rad=0.0),
        NavWaypoint(x=2.0, y=0.0, yaw_rad=0.0),
        NavWaypoint(x=3.0, y=0.0, yaw_rad=0.0),
        NavWaypoint(x=4.0, y=0.0, yaw_rad=0.0),
    ]

    plan = TourPlan(waypoints=waypoints, tour_id="e2e_mock_2026")
    await orchestrator.dispatch_tour(plan)

    observed_nodes: list[str] = []
    logical_ids = ["I", "1", "2", "3", "F"]
    last_index = -1

    while orchestrator.state_id == "navigating":
        idx = orchestrator.context.current_waypoint_index
        if 0 <= idx < len(logical_ids) and idx != last_index:
            node_id = logical_ids[idx]
            observed_nodes.append(node_id)
            print(f"NODE_REACHED_SIM {node_id}")
            last_index = idx
            await asyncio.sleep(1.0)
        else:
            await asyncio.sleep(0.1)

    await wait_for_state(orchestrator, "idle", timeout_s=10.0)

    if observed_nodes != logical_ids:
        raise RuntimeError(f"Secuencia de nodos invalida: {observed_nodes}")

    orchestrator.context.current_waypoint_index = 4
    await orchestrator.start_tour()
    await asyncio.sleep(0.3)

    sample_audio = np.zeros(128, dtype=np.float32)
    await orchestrator.request_interaction(sample_audio, language="es")
    await asyncio.sleep(0.3)

    if orchestrator.state_id == "navigating":
        await orchestrator.finish_tour()

    await wait_for_state(orchestrator, "idle", timeout_s=5.0)

    if conversation_manager.llm_qa_invocations < 1:
        raise RuntimeError("No se invoco la ruta llm_qa en el nodo F")

    if conversation_manager.last_llm_answer != "respuesta simulada ollama":
        raise RuntimeError("El mock de Ollama no devolvio el texto esperado")

    print("E2E_MOCK_VALIDATION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_validation()))
