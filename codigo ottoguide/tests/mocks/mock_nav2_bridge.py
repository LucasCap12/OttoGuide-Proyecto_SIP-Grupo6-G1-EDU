from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, List


@dataclass(slots=True)
class MockNav2Bridge:
    navigation_delay_s: float = 0.01
    navigation_calls: List[List[Any]] = field(default_factory=list)
    cancel_calls: int = 0
    injected_poses: List[Any] = field(default_factory=list)
    started: bool = False

    async def start(self) -> None:
        self.started = True

    async def navigate_to_waypoints(self, waypoints: List[Any]) -> bool:
        self.navigation_calls.append(list(waypoints))
        await asyncio.sleep(self.navigation_delay_s)
        return True

    async def cancel_navigation(self) -> None:
        self.cancel_calls += 1

    async def inject_absolute_pose(self, pose_estimate: Any) -> None:
        self.injected_poses.append(pose_estimate)

    async def close(self) -> None:
        self.started = False


__all__ = ["MockNav2Bridge"]