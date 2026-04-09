from __future__ import annotations

import asyncio
from typing import Optional

import pyttsx3
import speech_recognition as sr


class AudioHardwareBridge:
    """
    Puente asíncrono para captura STT y reproducción TTS en hardware local.
    """

    def __init__(
        self,
        *,
        speech_timeout_seconds: float = 5.0,
        phrase_time_limit_seconds: float = 15.0,
        language: str = "es-ES",
        tts_rate: int = 150,
    ) -> None:
        self._recognizer = sr.Recognizer()
        self._engine = pyttsx3.init()
        self._engine.setProperty("rate", tts_rate)
        self._speech_timeout_seconds = speech_timeout_seconds
        self._phrase_time_limit_seconds = phrase_time_limit_seconds
        self._language = language

    async def listen_stt(self) -> str:
        """
        Captura de voz asíncrona delegando en executor.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._listen_sync)

    async def speak_tts(self, text: str) -> None:
        """
        Síntesis de voz asíncrona delegando en executor.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._speak_sync, text)

    def _listen_sync(self) -> str:
        """
        Captura y transcribe voz en forma sincrónica.
        """
        try:
            with sr.Microphone() as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=1.0)
                audio = self._recognizer.listen(
                    source,
                    timeout=self._speech_timeout_seconds,
                    phrase_time_limit=self._phrase_time_limit_seconds,
                )
                value = self._recognizer.recognize_google(audio, language=self._language)
                if not isinstance(value, str):
                    return ""
                return value.strip()
        except sr.WaitTimeoutError:
            return ""
        except sr.UnknownValueError:
            return ""
        except Exception:
            return ""

    def _speak_sync(self, text: str) -> None:
        """
        Reproduce texto por TTS en forma sincrónica.
        """
        message = text.strip()
        if not message:
            return
        self._engine.say(message)
        self._engine.runAndWait()
