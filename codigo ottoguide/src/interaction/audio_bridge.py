from __future__ import annotations

import asyncio
from typing import Optional

import numpy as np
import pyttsx3
import speech_recognition as sr
from numpy.typing import NDArray


class AudioHardwareBridge:
    """
    @TASK: Proveer captura de microfono local y reproduccion TTS sin bloquear el event loop
    @INPUT: Audio de microfono via SpeechRecognition + texto para reproducir por pyttsx3
    @OUTPUT: PCM float32 mono para STT local en ConversationManager y audio reproducido por TTS local
    @CONTEXT: Capa de hardware de audio del flujo HIL Mic -> STT local -> LLM local -> TTS local
    @SECURITY: No usa STT cloud ni envia audio fuera del host; I/O bloqueante aislado en run_in_executor
    """

    def __init__(
        self,
        *,
        speech_timeout_seconds: float = 5.0,
        phrase_time_limit_seconds: float = 15.0,
        language: str = "es-ES",
        sample_rate_hz: int = 16000,
        tts_rate: int = 150,
    ) -> None:
        self._recognizer = sr.Recognizer()
        self._engine = pyttsx3.init()
        self._engine.setProperty("rate", tts_rate)
        self._speech_timeout_seconds = speech_timeout_seconds
        self._phrase_time_limit_seconds = phrase_time_limit_seconds
        self._language = language
        self._sample_rate_hz = sample_rate_hz

    async def listen_pcm(self) -> NDArray[np.float32]:
        """
        @TASK: Capturar audio de microfono y devolverlo como PCM float32 mono para STT local
        @INPUT: Configuracion interna de timeout/phrase_time_limit/sample_rate
        @OUTPUT: NDArray[np.float32] normalizado en [-1, 1], vacio si no hubo audio util
        @CONTEXT: Consumido por ConversationManager.start_interactive_session en modo llm_qa local
        @SECURITY: Captura local en memoria; sin persistencia ni servicios remotos
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._listen_pcm_sync)

    async def listen_stt(self) -> str:
        """
        @TASK: Compatibilidad legacy para callers antiguos de STT textual
        @INPUT: Audio de microfono local
        @OUTPUT: Siempre string vacio para evitar STT cloud en runtime
        @CONTEXT: El STT real local se ejecuta en LocalNLPPipeline.transcribe() con faster-whisper
        @SECURITY: Deshabilita reconocimiento cloud implicito de speech_recognition
        """
        _ = await self.listen_pcm()
        return ""

    async def speak_tts(self, text: str) -> None:
        """
        Síntesis de voz asíncrona delegando en executor.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._speak_sync, text)

    def _listen_sync(self) -> str:
        """
        @TASK: Alias legacy de compatibilidad para codigo existente
        @INPUT: Sin parametros
        @OUTPUT: String vacio
        @CONTEXT: Se mantiene para no romper imports antiguos
        @SECURITY: Sin llamada a STT cloud
        """
        return ""

    def _listen_pcm_sync(self) -> NDArray[np.float32]:
        """
        @TASK: Capturar audio crudo de microfono y convertirlo a float32 normalizado
        @INPUT: Timeout de escucha, phrase_time_limit y sample_rate configurados en la instancia
        @OUTPUT: NDArray[np.float32] mono; array vacio ante timeout, silencio o error
        @CONTEXT: Ejecutado en thread worker via run_in_executor para no bloquear asyncio
        @SECURITY: Procesamiento en memoria local; no persiste ni transmite audio
        """
        try:
            with sr.Microphone() as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=1.0)
                audio = self._recognizer.listen(
                    source,
                    timeout=self._speech_timeout_seconds,
                    phrase_time_limit=self._phrase_time_limit_seconds,
                )
                raw = audio.get_raw_data(convert_rate=self._sample_rate_hz, convert_width=2)
                pcm_int16 = np.frombuffer(raw, dtype=np.int16)
                if pcm_int16.size == 0:
                    return np.empty(0, dtype=np.float32)
                return pcm_int16.astype(np.float32) / 32768.0
        except sr.WaitTimeoutError:
            return np.empty(0, dtype=np.float32)
        except Exception:
            return np.empty(0, dtype=np.float32)

    def _speak_sync(self, text: str) -> None:
        """
        Reproduce texto por TTS en forma sincrónica.
        """
        message = text.strip()
        if not message:
            return
        self._engine.say(message)
        self._engine.runAndWait()
