from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp


class OllamaAsyncClient:
    """
    Cliente HTTP asíncrono para inferencia local con Ollama.
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "qwen2.5:3b",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.generate_url = f"{self.host}/api/generate"
        self.timeout_seconds = timeout_seconds

    async def generate_response(self, prompt: str) -> Optional[str]:
        """
        Envía petición asíncrona al endpoint /api/generate.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.generate_url, json=payload) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()
                    value = data.get("response", "")
                    if not isinstance(value, str):
                        return None
                    return value.strip()
        except asyncio.TimeoutError:
            return None
        except aiohttp.ClientError:
            return None
        except Exception:
            return None
