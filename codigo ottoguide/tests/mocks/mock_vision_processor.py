from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class MockVisionProcessor:
    get_next_calls: int = 0
    closed: bool = False

    async def get_next_estimate(self, timeout_s: float = 0.5):
        self.get_next_calls += 1
        await asyncio.sleep(min(timeout_s, 0.01))
        return None

    def close(self) -> None:
        self.closed = True


__all__ = ["MockVisionProcessor"]