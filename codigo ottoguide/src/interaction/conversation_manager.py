"""
@TASK: Implementar pipeline NLP hibrido local/cloud con patron Strategy y hot-swap ante timeout
@INPUT: Buffers de audio PCM float32 via process_interaction(); texto via respond() y ConversationRequest
@OUTPUT: Texto de respuesta reproducido por ALSA y ConversationResponse tipada retornada al orquestador
@CONTEXT: Modulo de interaccion HIL Fase 4; opera en red air-gapped con fallback cloud automatico.
          Patron Strategy: LocalNLPPipeline (edge) y CloudNLPPipeline (fallback) intercambiables.
          Hot-swap transparente activado por asyncio.wait_for ante timeout de cada etapa del pipeline.
@SECURITY: Ninguna llamada bloqueante de audio o inferencia ocurre directamente en el event loop.
           ThreadPoolExecutor para I/O (sounddevice/ALSA); ProcessPoolExecutor para CPU (whisper/piper).

STEP 1: Definir contratos de Strategy (NLPStrategy, ConversationRequest, ConversationResponse)
STEP 2: Implementar LocalNLPPipeline (faster-whisper + Ollama + piper-tts + sounddevice)
STEP 3: Implementar CloudNLPPipeline (httpx async OpenAI/Gemini + TTS cloud)
STEP 4: Implementar ConversationManager con hot-swap en asyncio.wait_for
STEP 5: Aislar todo computo CPU y I/O de audio en executors inyectados

Constantes operativas del modulo:
  STT_TIMEOUT_S (4.0 s)       — faster-whisper transcripcion; ajustar segun hardware arm64 real
  LLM_LOCAL_TIMEOUT_S (2.5 s) — Ollama primera iteracion en companion PC cuantizado
  TTS_TIMEOUT_S (3.0 s)       — piper-tts + enqueue ALSA; tipicamente < 500 ms frases cortas
  CLOUD_TIMEOUT_S (6.0 s)     — endpoint OpenAI/Gemini RTT incluyendo TLS negotiation
  PIPER_MODEL_PATH             — ruta ONNX del modelo piper; configurable via PIPER_MODEL_PATH env
  AUDIO_SAMPLE_RATE (22050 Hz)— frecuencia nativa de piper-tts; fijo para D435i pipeline
  AUDIO_BLOCK_SIZE (2048)      — frames por bloque ALSA en callback de sounddevice
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Literal

import httpx
import numpy as np
from numpy.typing import NDArray

from .audio_bridge import AudioHardwareBridge
from .llm_client import OllamaAsyncClient

# ---------------------------------------------------------------------------
# Constantes de configuracion
# ---------------------------------------------------------------------------

STT_TIMEOUT_S: float = 4.0
LLM_LOCAL_TIMEOUT_S: float = 2.5
TTS_TIMEOUT_S: float = 3.0
CLOUD_TIMEOUT_S: float = 6.0

PIPER_MODEL_PATH: str = os.environ.get("PIPER_MODEL_PATH", "/usr/share/piper/es_MX-claude-high.onnx")
AUDIO_SAMPLE_RATE: int = 22050
AUDIO_BLOCK_SIZE: int  = 2048

OPENAI_CHAT_URL: str     = "https://api.openai.com/v1/chat/completions"
OPENAI_TTS_URL: str      = "https://api.openai.com/v1/audio/speech"
GEMINI_CHAT_URL_TMPL: str = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent?key={key}"
)

_DEFAULT_OLLAMA_BASE_URL: str = "http://localhost:11434"
_DEFAULT_OLLAMA_MODEL: str    = "qwen2.5:3b"
_DEFAULT_CLOUD_PROVIDER: str  = "openai"

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tipos de datos de dominio
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ConversationRequest:
    """
    @TASK: Encapsular solicitud de interaccion desde el orquestador hacia cualquier NLPStrategy
    @INPUT: Texto del usuario obtenido de STT o directamente de TourOrchestrator
    @OUTPUT: Estructura inmutable consumible por NLPStrategy.generate() sin modificacion
    @CONTEXT: Contrato de entrada del patron Strategy. metadata puede incluir waypoint_id
              y estado del orquestador para personalizar la respuesta del LLM por contexto.
    @SECURITY: No persiste audio crudo; solo texto ya transcripto. No incluye credenciales.

    STEP 1: Capturar user_text, locale y metadata de contexto del tour activo
    """

    user_text: str
    locale: str = "es-MX"
    metadata: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True, slots=True)
class ConversationResponse:
    """
    @TASK: Encapsular respuesta generada por cualquier estrategia NLP para consumo del orquestador
    @INPUT: Texto de respuesta, identificador del pipeline que la genero y flag de disponibilidad de audio
    @OUTPUT: Estructura inmutable retornada por NLPStrategy.generate() y ConversationManager
    @CONTEXT: Contrato de salida del patron Strategy. source_pipeline es "local", "cloud", "scripted"
              o "llm_qa" para telemetria y diagnostico de fallback en el orquestador.
    @SECURITY: No incluye datos de autenticacion del proveedor cloud ni audio crudo PCM.

    STEP 1: Registrar answer_text, source_pipeline de trazabilidad y audio_stream_ready
    """

    answer_text: str
    source_pipeline: str
    audio_stream_ready: bool


# ---------------------------------------------------------------------------
# Contratos abstractos (Strategy interfaces)
# ---------------------------------------------------------------------------

class NLPStrategy(ABC):
    """
    @TASK: Definir el contrato abstracto de estrategia NLP completa para el patron Strategy
    @INPUT: ConversationRequest con user_text ya disponible (STT realizado previamente si aplica)
    @OUTPUT: ConversationResponse con respuesta generada, pipeline source y flag de audio
    @CONTEXT: Interface del patron Strategy; LocalNLPPipeline y CloudNLPPipeline la implementan.
              ConversationManager inyecta y selecciona la estrategia activa en runtime via hot-swap.
    @SECURITY: Cada implementacion es responsable de su aislamiento de I/O y CPU en executors.
    """

    @abstractmethod
    async def generate(self, request: ConversationRequest) -> ConversationResponse:
        """
        @TASK: Generar respuesta NLP completa (LLM + TTS) a partir de una solicitud de texto
        @INPUT: request — ConversationRequest con user_text, locale y metadata opcionales
        @OUTPUT: ConversationResponse con answer_text, source_pipeline y audio_stream_ready
        @CONTEXT: Metodo abstracto; cada estrategia concreta define su pipeline completo.
                  TimeoutError se propaga al ConversationManager para activar hot-swap.
        """
        ...


# ---------------------------------------------------------------------------
# FUNCIONES AISLABLES EN EXECUTOR (top-level para pickle en ProcessPoolExecutor)
# ---------------------------------------------------------------------------

def _run_whisper_transcription(
    audio_pcm: NDArray[np.float32],
    model_size: str,
    language: str,
) -> str:
    """
    @TASK: Ejecutar transcripcion STT con faster-whisper en proceso aislado del event loop
    @INPUT: audio_pcm — array float32 mono normalizado [-1, 1] capturado del microfono;
            model_size — "tiny" | "base" | "small" segun VRAM/RAM del companion PC;
            language — codigo iso639 del idioma del audio
    @OUTPUT: Texto transcripto como string concatenando todos los segmentos del generador
    @CONTEXT: Funcion top-level para compatibilidad con ProcessPoolExecutor (requiere pickle).
              Invocada via loop.run_in_executor(cpu_executor, ...) desde LocalNLPPipeline.transcribe().
              model_size tipico "small" o "base" para companion PC arm64 sin VRAM disponible.
    @SECURITY: Sin escritura a disco en ningun momento; audio_pcm se pasa como array en memoria.
               Import de WhisperModel dentro de la funcion para evitar import en el proceso principal.

    STEP 1: Importar WhisperModel dentro de la funcion para aislamiento del proceso worker
    STEP 2: Instanciar WhisperModel con device=cpu y compute_type=int8 para hardware embebido arm64
    STEP 3: Transcribir con vad_filter=True y beam_size=1; concatenar segmentos del generador
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
    )
    segments, _ = model.transcribe(
        audio_pcm,
        language=language,
        beam_size=1,
        vad_filter=True,
    )
    return " ".join(seg.text.strip() for seg in segments)


def _run_piper_synthesis(
    text: str,
    model_path: str,
    sample_rate: int,
) -> NDArray[np.float32]:
    """
    @TASK: Sintetizar audio PCM desde texto con piper-tts en proceso aislado del event loop
    @INPUT: text — respuesta del LLM a sintetizar; model_path — ruta ONNX del modelo piper;
            sample_rate — frecuencia de muestreo en Hz (tipicamente AUDIO_SAMPLE_RATE = 22050 Hz)
    @OUTPUT: Array float32 mono normalizado en [-1, 1] con audio sintetizado listo para ALSA
    @CONTEXT: Funcion top-level para ProcessPoolExecutor; sin estado global ni atributos de instancia.
              El array resultante se pasa al hilo de sounddevice via cola thread-safe en _play_audio_alsa.
    @SECURITY: Sin escritura a disco; todo el procesamiento ocurre en memoria durante la sintesis.
               Import de PiperVoice dentro de la funcion para aislamiento del proceso worker.

    STEP 1: Importar PiperVoice dentro de la funcion para aislamiento del proceso worker
    STEP 2: Cargar el modelo ONNX desde model_path con PiperVoice.load()
    STEP 3: Sintetizar via synthesize_stream_raw; concatenar chunks y convertir de int16 a float32
    """
    from piper import PiperVoice

    voice = PiperVoice.load(model_path)

    audio_chunks: list[bytes] = []
    for audio_bytes in voice.synthesize_stream_raw(text):
        audio_chunks.append(audio_bytes)

    raw = b"".join(audio_chunks)
    pcm_int16 = np.frombuffer(raw, dtype=np.int16)
    pcm_float32 = pcm_int16.astype(np.float32) / 32768.0
    return pcm_float32


def _play_audio_alsa(
    pcm_float32: NDArray[np.float32],
    sample_rate: int,
    block_size: int,
) -> None:
    """
    @TASK: Reproducir array PCM float32 en el dispositivo ALSA por defecto via sounddevice
    @INPUT: pcm_float32 — audio normalizado float32 mono [-1, 1]; sample_rate — Hz del audio;
            block_size — frames por bloque para el callback de sounddevice
    @OUTPUT: Reproduccion bloqueante hasta fin del audio o error del dispositivo ALSA; sin retorno
    @CONTEXT: Funcion top-level ejecutada en ThreadPoolExecutor de I/O de audio (thread_name_prefix="tts-alsa").
              El callback _audio_callback corre en el hilo de audio del OS; la cola audio_queue es thread-safe.
              Invocada via loop.run_in_executor(audio_executor, ...) como fire-and-forget desde el event loop.
    @SECURITY: Sin archivos temporales; el audio permanece en memoria durante toda la reproduccion.
               Import de sounddevice dentro de la funcion para aislamiento de import en el worker.
               finished_event.wait() bloquea solo el hilo de I/O; nunca bloquea el event loop de asyncio.

    STEP 1: Importar sounddevice; segmentar pcm_float32 en bloques de block_size frames en audio_queue
    STEP 2: Definir _audio_callback que consume bloques de la cola; escribe silencio ante underrun
    STEP 3: Abrir OutputStream con _audio_callback; esperar finished_event.set() desde finished_callback
    """
    import sounddevice as sd

    audio_queue: queue.Queue[Optional[NDArray[np.float32]]] = queue.Queue()
    for start in range(0, len(pcm_float32), block_size):
        audio_queue.put(pcm_float32[start : start + block_size])
    audio_queue.put(None)  # sentinel de fin

    finished_event = threading.Event()

    def _audio_callback(
        outdata: NDArray[np.float32],
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        if status:
            LOGGER.warning("[TTS/ALSA] Estado sounddevice: %s", status)
        try:
            chunk = audio_queue.get_nowait()
        except queue.Empty:
            outdata[:] = 0
            raise sd.CallbackStop()

        if chunk is None:
            outdata[:] = 0
            raise sd.CallbackStop()

        n = len(chunk)
        if n < frames:
            outdata[:n, 0] = chunk
            outdata[n:, 0] = 0.0
        else:
            outdata[:, 0] = chunk[:frames]

    with sd.OutputStream(
        samplerate=sample_rate,
        blocksize=block_size,
        channels=1,
        dtype="float32",
        callback=_audio_callback,
        finished_callback=finished_event.set,
    ):
        finished_event.wait()


# ---------------------------------------------------------------------------
# Pipeline Local (Edge Strategy)
# ---------------------------------------------------------------------------

class LocalNLPPipeline(NLPStrategy):
    """
    @TASK: Implementar estrategia NLP completa en edge usando faster-whisper, Ollama y piper-tts
    @INPUT: audio_pcm opcional y texto ya transcripto via ConversationRequest
    @OUTPUT: ConversationResponse con respuesta de Ollama y audio reproducido por ALSA
    @CONTEXT: Strategy primaria en red air-gapped; hot-swap a cloud ante timeout o fallo de hardware.
              cpu_executor (ProcessPoolExecutor) para operaciones CPU-bound (whisper, piper).
              audio_executor (ThreadPoolExecutor) para I/O bloqueante de ALSA/sounddevice.
    @SECURITY: Ningun dato del usuario sale de la LAN durante el pipeline local completo.
               cpu_executor con max_workers=1 evita saturacion de RAM en arm64 sin VRAM disponible.
    """

    def __init__(
        self,
        *,
        model_name: str = _DEFAULT_OLLAMA_MODEL,
        whisper_model_size: str = "small",
        piper_model_path: str = PIPER_MODEL_PATH,
        ollama_base_url: str = _DEFAULT_OLLAMA_BASE_URL,
        cpu_executor: Optional[ProcessPoolExecutor] = None,
        audio_executor: Optional[ThreadPoolExecutor] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        @TASK: Inicializar el pipeline local con configuracion inyectable para cada etapa
        @INPUT: model_name — nombre del modelo Ollama cuantizado (ej. "llama3:8b-instruct-q4_K_M")
                whisper_model_size — "tiny" | "base" | "small" segun RAM del companion PC
                piper_model_path — ruta al archivo ONNX del modelo piper-tts
                ollama_base_url — base URL del servidor Ollama local (default http://localhost:11434)
                cpu_executor — ProcessPoolExecutor inyectado o None para crear propio (max_workers=1)
                audio_executor — ThreadPoolExecutor de I/O de audio o None para crear propio
                http_client — httpx.AsyncClient inyectado para testing o None para inicializacion lazy
        @OUTPUT: Instancia LocalNLPPipeline lista; executors propios creados si no son inyectados;
                 _owns_cpu_executor y _owns_audio_executor registran ownership para shutdown controlado
        @CONTEXT: Soporta inyeccion de dependencias para testing sin hardware real ni modelos descargados.
                  ProcessPoolExecutor se crea en el proceso principal; los workers lo forkan.
        @SECURITY: cpu_executor propio con max_workers=1 evita saturacion de RAM en arm64.
                   cancel_futures=True en close() previene inferencias tardias fuera del ciclo de vida.

        STEP 1: Persistir parametros de configuracion de cada etapa del pipeline
        STEP 2: Crear executors propios con max_workers=1 si no son inyectados externamente
        STEP 3: Registrar cliente HTTP y flags de ownership para shutdown controlado en close()
        """
        self._model_name: str = model_name
        self._whisper_model_size: str = whisper_model_size
        self._piper_model_path: str = piper_model_path
        self._ollama_base_url: str = ollama_base_url.rstrip("/")

        self._owns_cpu_executor = cpu_executor is None
        self._cpu_executor: ProcessPoolExecutor = cpu_executor or ProcessPoolExecutor(
            max_workers=1
        )

        self._owns_audio_executor = audio_executor is None
        self._audio_executor: ThreadPoolExecutor = audio_executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="tts-alsa",
        )

        self._http_client: Optional[httpx.AsyncClient] = http_client
        self._owns_http_client = http_client is None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """
        @TASK: Obtener o crear de forma lazy el cliente HTTP para comunicacion con Ollama
        @INPUT: Sin parametros
        @OUTPUT: Instancia de httpx.AsyncClient configurada con timeouts de conexion e inferencia
        @CONTEXT: Inicializacion lazy para compatibilidad con el ciclo de vida async del event loop.
                  Timeout de conexion (2.0 s) separado del timeout de inferencia (LLM_LOCAL_TIMEOUT_S).
        @SECURITY: Sin credenciales en el cliente base; Ollama corre en localhost sin autenticacion.

        STEP 1: Retornar cliente existente si ya fue instanciado; crear httpx.AsyncClient si es None
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._ollama_base_url,
                timeout=httpx.Timeout(connect=2.0, read=LLM_LOCAL_TIMEOUT_S, write=2.0, pool=1.0),
            )
        return self._http_client

    async def transcribe(
        self,
        audio_pcm: NDArray[np.float32],
        language: str = "es",
    ) -> str:
        """
        @TASK: Transcribir audio PCM a texto via faster-whisper en ProcessPoolExecutor aislado
        @INPUT: audio_pcm — array float32 mono normalizado capturado del microfono del robot;
                language — codigo iso639 del idioma del audio (default "es")
        @OUTPUT: Texto transcripto como string; TimeoutError propagado si supera STT_TIMEOUT_S
        @CONTEXT: Etapa STT del pipeline local; CPU-bound, aislada en proceso separado via cpu_executor.
                  TimeoutError se propaga hacia generate() o process_interaction() para activar hot-swap.
        @SECURITY: El array de audio no se escribe a disco en ningun momento del proceso de transcripcion.
                   ProcessPoolExecutor.submit no es awaitable; se usa loop.run_in_executor correctamente.

        STEP 1: Obtener el event loop activo y despachar _run_whisper_transcription al cpu_executor
        STEP 2: Aplicar asyncio.wait_for con timeout STT_TIMEOUT_S; propagar TimeoutError al caller
        """
        loop = asyncio.get_running_loop()

        return await asyncio.wait_for(
            loop.run_in_executor(
                self._cpu_executor,
                _run_whisper_transcription,
                audio_pcm,
                self._whisper_model_size,
                language,
            ),
            timeout=STT_TIMEOUT_S,
        )

    async def _infer_ollama(self, prompt: str) -> str:
        """
        @TASK: Invocar Ollama local via POST /api/generate con httpx asincrono
        @INPUT: prompt — texto del usuario ya transcripto (con system_prompt de zona prepended si aplica)
        @OUTPUT: Texto de respuesta generado por el LLM cuantizado como string; RuntimeError ante HTTP error
        @CONTEXT: Etapa LLM del pipeline local; operacion de red local (localhost:11434).
                  stream=False para respuesta completa en un solo round-trip sin streaming parcial.
        @SECURITY: Endpoint localhost; ninguna solicitud sale al exterior de la LAN en ningun momento.
                   httpx.HTTPStatusError se convierte en RuntimeError para propagacion homogenea al caller.

        STEP 1: Construir payload JSON con modelo, prompt, stream=False y temperatura 0.4
        STEP 2: Realizar POST async con asyncio.wait_for y timeout LLM_LOCAL_TIMEOUT_S
        STEP 3: Extraer campo "response" del JSON de Ollama y retornar como string strip()
        """
        client = await self._get_http_client()

        payload = {
            "model": self._model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.4},
        }

        try:
            response = await asyncio.wait_for(
                client.post("/api/generate", json=payload),
                timeout=LLM_LOCAL_TIMEOUT_S,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama HTTP error {exc.response.status_code}"
            ) from exc

        data = response.json()
        return str(data.get("response", "")).strip()

    async def synthesize_and_play(self, text: str) -> None:
        """
        @TASK: Sintetizar texto con piper-tts y reproducir en ALSA de forma no bloqueante
        @INPUT: text — respuesta del LLM a sintetizar; tipicamente < 200 palabras para latencia optima
        @OUTPUT: Side-effect: asyncio.Task "tts-alsa-playback" creada como fire-and-forget; sin retorno
        @CONTEXT: Etapa TTS del pipeline local. Sintesis en cpu_executor (CPU-bound, proceso aislado);
                  reproduccion ALSA en audio_executor (I/O bloqueante, hilo dedicado de OS).
                  La tarea de reproduccion puede cancelarse si llega un stop-word o una emergencia.
        @SECURITY: Sin archivos temporales entre sintesis y reproduccion; PCM en memoria en todo momento.
                   La closure _audio_callback corre en hilo de audio del OS; la cola es thread-safe.

        STEP 1: Despachar _run_piper_synthesis al cpu_executor con asyncio.wait_for timeout TTS_TIMEOUT_S
        STEP 2: Crear asyncio.Task "tts-alsa-playback" con _play_audio_alsa en audio_executor (fire-and-forget)
        """
        loop = asyncio.get_running_loop()

        pcm_float32: NDArray[np.float32] = await asyncio.wait_for(
            loop.run_in_executor(
                self._cpu_executor,
                _run_piper_synthesis,
                text,
                self._piper_model_path,
                AUDIO_SAMPLE_RATE,
            ),
            timeout=TTS_TIMEOUT_S,
        )

        asyncio.create_task(
            loop.run_in_executor(
                self._audio_executor,
                _play_audio_alsa,
                pcm_float32,
                AUDIO_SAMPLE_RATE,
                AUDIO_BLOCK_SIZE,
            ),
            name="tts-alsa-playback",
        )

    async def generate(self, request: ConversationRequest) -> ConversationResponse:
        """
        @TASK: Ejecutar el pipeline completo local LLM-TTS en edge para la solicitud recibida
        @INPUT: request — ConversationRequest con user_text ya transcripto (STT externo) y locale
        @OUTPUT: ConversationResponse con respuesta de Ollama y source_pipeline="local"
        @CONTEXT: Implementacion de NLPStrategy.generate() para el pipeline local.
                  STT se realiza externamente en process_interaction(); este metodo solo recibe texto.
                  TimeoutError se propaga al ConversationManager para activar hot-swap a cloud.
        @SECURITY: Todo el procesamiento ocurre dentro de la LAN; ningun dato sale al exterior.

        STEP 1: Invocar _infer_ollama con user_text del request; TimeoutError activa hot-swap en caller
        STEP 2: Sintetizar y reproducir en ALSA como asyncio.Task no bloqueante; degradar si TTS falla
        STEP 3: Retornar ConversationResponse con source_pipeline="local" y audio_stream_ready=True
        """
        answer_text = await self._infer_ollama(request.user_text)

        try:
            await self.synthesize_and_play(answer_text)
        except Exception as exc:
            LOGGER.warning("[LocalNLP] TTS fallo, respuesta de texto disponible: %s", exc)

        return ConversationResponse(
            answer_text=answer_text,
            source_pipeline="local",
            audio_stream_ready=True,
        )

    def close(self) -> None:
        """
        @TASK: Liberar executors propios del pipeline local en el shutdown global del sistema
        @INPUT: Sin parametros
        @OUTPUT: _cpu_executor y _audio_executor detenidos con cancel_futures=True si son de propiedad local
        @CONTEXT: Invocado por ConversationManager.close() durante el shutdown del lifespan de FastAPI.
                  Si los executors fueron inyectados externamente, el caller es responsable de cerrarlos.
        @SECURITY: cancel_futures=True previene inferencias tardias fuera del ciclo de vida del tour.

        STEP 1: Apagar _cpu_executor con cancel_futures=True si _owns_cpu_executor es True
        STEP 2: Apagar _audio_executor con cancel_futures=True si _owns_audio_executor es True
        """
        if self._owns_cpu_executor:
            self._cpu_executor.shutdown(wait=False, cancel_futures=True)
        if self._owns_audio_executor:
            self._audio_executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Pipeline Nube (Cloud Strategy)
# ---------------------------------------------------------------------------

class CloudNLPPipeline(NLPStrategy):
    """
    @TASK: Implementar estrategia NLP via API cloud (OpenAI o Gemini) como fallback de hot-swap
    @INPUT: ConversationRequest con texto del usuario ya transcripto
    @OUTPUT: ConversationResponse con respuesta del proveedor cloud configurado
    @CONTEXT: Strategy de fallback activada por ConversationManager ante timeout del pipeline local.
              Soporta dos proveedores: "openai" (Chat + TTS) y "gemini" (solo Chat).
              El cliente httpx se reutiliza entre llamadas para connection pooling eficiente.
    @SECURITY: API keys leidas desde config/settings.py por el caller; nunca hardcodeadas ni de os.environ.
               TLS obligatorio para todos los endpoints cloud; Authorization en header por solicitud.
    """

    def __init__(
        self,
        *,
        timeout_s: float = CLOUD_TIMEOUT_S,
        provider: str = _DEFAULT_CLOUD_PROVIDER,
        openai_api_key: str = "",
        gemini_api_key: str = "",
        audio_executor: Optional[ThreadPoolExecutor] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        @TASK: Inicializar el pipeline cloud con proveedor, credenciales y executors configurables
        @INPUT: timeout_s — timeout total de la llamada cloud en segundos (default CLOUD_TIMEOUT_S)
                provider — "openai" o "gemini" segun proveedor activo configurado
                openai_api_key — clave de la API de OpenAI provista desde config/settings.py
                gemini_api_key — clave de la API de Gemini provista desde config/settings.py
                audio_executor — ThreadPoolExecutor de I/O de audio o None para crear propio
                http_client — httpx.AsyncClient inyectado para testing o None para lazy init
        @OUTPUT: Instancia CloudNLPPipeline lista con cliente HTTP lazy; LOGGER.warning si API key ausente
        @CONTEXT: Soporta inyeccion de dependencias para testing sin trafico de red real.
                  follow_redirects=True es necesario para Gemini API v1beta.
        @SECURITY: Las API keys se almacenan como atributos privados; LOGGER.warning si estan vacias.
                   Nunca se logean las keys; solo se emite warning de ausencia antes del primer uso.

        STEP 1: Validar timeout_s > 0 (ValueError); normalizar provider a lowercase
        STEP 2: Persistir credenciales, timeout y configuracion del proveedor; LOGGER.warning si key vacia
        STEP 3: Crear _audio_executor propio si no inyectado; registrar _http_client para lazy init
        """
        if timeout_s <= 0:
            raise ValueError("timeout_s debe ser mayor que 0.")

        self._timeout_s: float = timeout_s
        self._provider: str = provider.lower()
        self._openai_api_key: str = openai_api_key
        self._gemini_api_key: str = gemini_api_key

        if self._provider == "openai" and not self._openai_api_key:
            LOGGER.warning("[CloudNLP] OPENAI_API_KEY no configurada; el fallback cloud fallara.")
        if self._provider == "gemini" and not self._gemini_api_key:
            LOGGER.warning("[CloudNLP] GEMINI_API_KEY no configurada; el fallback cloud fallara.")

        self._owns_audio_executor = audio_executor is None
        self._audio_executor: ThreadPoolExecutor = audio_executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cloud-tts-alsa",
        )
        self._owned_http = http_client is None
        self._http_client: Optional[httpx.AsyncClient] = http_client

    async def _get_http_client(self) -> httpx.AsyncClient:
        """
        @TASK: Obtener o crear de forma lazy el cliente httpx para llamadas al proveedor cloud
        @INPUT: Sin parametros
        @OUTPUT: Instancia de httpx.AsyncClient configurada con timeouts y follow_redirects=True
        @CONTEXT: Inicializacion lazy para compatibilidad con el ciclo de vida async del event loop.
                  El cliente se reutiliza entre llamadas para connection pooling con el proveedor cloud.
        @SECURITY: El header Authorization se agrega por solicitud individual en _call_*; nunca en el
                   cliente base, para evitar filtrado de credenciales en logs internos de httpx.

        STEP 1: Retornar cliente existente si ya fue instanciado; crear httpx.AsyncClient si es None
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=self._timeout_s, write=3.0, pool=1.0),
                follow_redirects=True,
            )
        return self._http_client

    async def _call_openai_chat(self, user_text: str) -> str:
        """
        @TASK: Invocar OpenAI Chat Completions API para generar respuesta de texto
        @INPUT: user_text — texto del usuario ya transcripto por STT local o placeholder de error STT
        @OUTPUT: Respuesta textual del modelo gpt-4o-mini como string; HTTPStatusError ante error HTTP
        @CONTEXT: Implementacion del backend OpenAI para CloudNLPPipeline.
                  Modelo gpt-4o-mini balancea latencia y costo para respuestas cortas de guia turistico.
        @SECURITY: API key enviada en header Authorization Bearer; TLS obligatorio para OPENAI_CHAT_URL.
                   raise_for_status() propaga HTTPStatusError al caller para logging centralizado.

        STEP 1: Construir payload con modelo gpt-4o-mini, max_tokens=150 y temperatura 0.5
        STEP 2: Realizar POST con header Authorization Bearer y asyncio.wait_for timeout cloud
        STEP 3: Extraer content del primer choice de la respuesta JSON de la API
        """
        client = await self._get_http_client()

        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": user_text}],
            "max_tokens": 150,
            "temperature": 0.5,
        }
        headers = {"Authorization": f"Bearer {self._openai_api_key}"}

        response = await asyncio.wait_for(
            client.post(OPENAI_CHAT_URL, json=payload, headers=headers),
            timeout=self._timeout_s,
        )
        response.raise_for_status()

        data = response.json()
        return str(data["choices"][0]["message"]["content"]).strip()

    async def _call_gemini_chat(self, user_text: str) -> str:
        """
        @TASK: Invocar Gemini generateContent API para generar respuesta de texto
        @INPUT: user_text — texto del usuario ya transcripto
        @OUTPUT: Respuesta textual del modelo gemini-1.5-flash como string
        @CONTEXT: Implementacion del backend Gemini para CloudNLPPipeline.
                  La ruta de extraccion de respuesta es candidates[0].content.parts[0].text.
        @SECURITY: API key embebida como query param segun especificacion Gemini v1beta; TLS obligatorio.
                   follow_redirects=True requerido en el cliente para redirects de la API de Google.

        STEP 1: Formatear GEMINI_CHAT_URL_TMPL con la gemini_api_key como query param
        STEP 2: Construir payload con contents/parts segun formato Gemini y generationConfig
        STEP 3: Realizar POST async con asyncio.wait_for y extraer texto del primer candidate
        """
        client = await self._get_http_client()

        url = GEMINI_CHAT_URL_TMPL.format(key=self._gemini_api_key)

        payload = {
            "contents": [{"parts": [{"text": user_text}]}],
            "generationConfig": {"maxOutputTokens": 150, "temperature": 0.5},
        }

        response = await asyncio.wait_for(
            client.post(url, json=payload),
            timeout=self._timeout_s,
        )
        response.raise_for_status()
        data = response.json()
        return str(data["candidates"][0]["content"]["parts"][0]["text"]).strip()

    async def _cloud_tts_openai(self, text: str) -> None:
        """
        @TASK: Sintetizar texto con OpenAI TTS (tts-1) y reproducir via ALSA como fire-and-forget
        @INPUT: text — respuesta del LLM cloud a sintetizar para reproduccion en el altavoz del robot
        @OUTPUT: Side-effect: asyncio.Task "cloud-tts-alsa-playback" creada en audio_executor
        @CONTEXT: TTS cloud como alternativa cuando piper-tts no esta disponible en el fallback cloud.
                  Respuesta de OpenAI TTS en formato PCM (response_format="pcm"); sample_rate 24000 Hz.
        @SECURITY: Respuesta de audio descargada completamente en memoria; sin escritura a disco.
                   API key enviada en header Authorization Bearer; TLS obligatorio para OPENAI_TTS_URL.

        STEP 1: POST a OPENAI_TTS_URL con modelo tts-1, voz "nova" y response_format="pcm"
        STEP 2: Leer bytes PCM de la respuesta y convertir de int16 a float32 normalizado [-1, 1]
        STEP 3: Crear asyncio.Task "cloud-tts-alsa-playback" en audio_executor (fire-and-forget)
        """
        client = await self._get_http_client()
        headers = {"Authorization": f"Bearer {self._openai_api_key}"}
        payload = {"model": "tts-1", "input": text, "voice": "nova", "response_format": "pcm"}

        response = await asyncio.wait_for(
            client.post(OPENAI_TTS_URL, json=payload, headers=headers),
            timeout=self._timeout_s,
        )
        response.raise_for_status()

        pcm_int16 = np.frombuffer(response.content, dtype=np.int16)
        pcm_float32 = pcm_int16.astype(np.float32) / 32768.0

        loop = asyncio.get_running_loop()
        asyncio.create_task(
            loop.run_in_executor(
                self._audio_executor,
                _play_audio_alsa,
                pcm_float32,
                24000,  # OpenAI TTS PCM rate
                AUDIO_BLOCK_SIZE,
            ),
            name="cloud-tts-alsa-playback",
        )

    async def generate(self, request: ConversationRequest) -> ConversationResponse:
        """
        @TASK: Ejecutar el pipeline cloud completo LLM-TTS para la solicitud recibida
        @INPUT: request — ConversationRequest con user_text ya disponible
        @OUTPUT: ConversationResponse con respuesta del proveedor cloud activo y source_pipeline="cloud"
        @CONTEXT: Implementacion de NLPStrategy.generate() para el pipeline cloud.
                  Ruteo al backend correcto segun _provider ("openai" o "gemini").
                  Sin reintentos automaticos; el ConversationManager controla la politica de retry.
        @SECURITY: TimeoutError se propaga al ConversationManager si la llamada cloud supera CLOUD_TIMEOUT_S.
                   El TourOrchestrator es responsable de trigger_emergency ante excepcion no recuperada.

        STEP 1: Rutear al backend correcto segun _provider; ValueError si provider no reconocido
        STEP 2: Intentar TTS cloud via _cloud_tts_openai si provider es "openai"; error es no critico
        STEP 3: Retornar ConversationResponse con source_pipeline="cloud" y audio_stream_ready=True
        """
        if self._provider == "openai":
            answer_text = await self._call_openai_chat(request.user_text)
        elif self._provider == "gemini":
            answer_text = await self._call_gemini_chat(request.user_text)
        else:
            raise ValueError(f"Proveedor cloud no reconocido: '{self._provider}'")

        if self._provider == "openai" and self._openai_api_key:
            try:
                await self._cloud_tts_openai(answer_text)
            except Exception as exc:
                LOGGER.warning("[CloudNLP] TTS cloud fallo: %s", exc)

        return ConversationResponse(
            answer_text=answer_text,
            source_pipeline="cloud",
            audio_stream_ready=True,
        )

    def close(self) -> None:
        """
        @TASK: Liberar el executor de audio propio del pipeline cloud en el shutdown global
        @INPUT: Sin parametros
        @OUTPUT: _audio_executor detenido con cancel_futures=True si fue creado internamente
        @CONTEXT: Invocado por ConversationManager.close() durante el shutdown del lifespan de FastAPI.
                  El cliente HTTP compartido es responsabilidad del caller si fue inyectado.
        @SECURITY: cancel_futures=True evita reproducciones de audio tardias fuera del ciclo de vida.

        STEP 1: Apagar _audio_executor con cancel_futures=True si _owns_audio_executor es True
        """
        if self._owns_audio_executor:
            self._audio_executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Orquestador principal — ConversationManager
# ---------------------------------------------------------------------------

class ConversationManager:
    """
    @TASK: Orquestar el pipeline NLP hibrido con hot-swap automatico local->cloud ante timeout
    @INPUT: audio_buffer PCM float32 via process_interaction(); texto ya disponible via respond()
    @OUTPUT: ConversationResponse desde la estrategia activa (local o cloud);
             side-effects: audio TTS reproducido en ALSA, _swap_count y _active_pipeline actualizados
    @CONTEXT: Punto de acceso unico del TourOrchestrator a la capa de interaccion HIL.
              Hot-swap transparente: TimeoutError en local activa automaticamente el pipeline cloud.
              _swap_count > 3 en una sesion indica degradacion del pipeline local (Ollama/whisper).
    @SECURITY: Las API keys cloud solo se usan si el pipeline local falla; principio de minimo privilegio.
               audio_buffer no se persiste ni escribe a disco en ningun paso del pipeline.
    """

    def __init__(
        self,
        *,
        local_strategy: LocalNLPPipeline,
        cloud_strategy: CloudNLPPipeline,
        llm_client: Optional[OllamaAsyncClient] = None,
        audio_bridge: Optional[AudioHardwareBridge] = None,
    ) -> None:
        """
        @TASK: Inicializar ConversationManager con ambas estrategias NLP y estado de telemetria
        @INPUT: local_strategy — LocalNLPPipeline configurada para edge computing
                cloud_strategy — CloudNLPPipeline configurada como fallback de hot-swap
                llm_client — OllamaAsyncClient para modo interactivo (None para instancia por defecto)
                audio_bridge — AudioHardwareBridge para STT/TTS en modo llm_qa (None para default)
        @OUTPUT: Manager listo con _active_pipeline="local", _swap_count=0, _total_interactions=0;
                 _script=None, _current_waypoint="" (sin guion hasta load_script_from_file)
        @CONTEXT: La estrategia local se intenta siempre primero; cloud es fallback por hot-swap.
                  _swap_count es indicador de salud: > 3 hot-swaps en sesion indica degradacion local.
        @SECURITY: Ninguna estrategia se activa en el constructor; solo en process_interaction y respond.

        STEP 1: Persistir referencias a ambas estrategias inyectadas como atributos privados
        STEP 2: Inicializar contadores de telemetria de hot-swap y pipeline activo
        STEP 3: Inicializar estado de contenido del tour (script, waypoint activo y cache de prompt)
        """
        self._local: LocalNLPPipeline = local_strategy
        self._cloud: CloudNLPPipeline = cloud_strategy

        self._active_pipeline: str = "local"
        self._swap_count: int = 0
        self._total_interactions: int = 0

        self._script: Optional[object] = None
        self._current_waypoint: str = ""
        self._current_waypoint_prompt: str = ""
        self._current_waypoint_interaction_type: Literal["scripted", "llm_qa"] = "llm_qa"
        self._current_waypoint_script_text: str = ""
        self._llm_client: OllamaAsyncClient = llm_client or OllamaAsyncClient()
        self._audio_bridge: AudioHardwareBridge = audio_bridge or AudioHardwareBridge()

    @property
    def active_strategy_name(self) -> str:
        """
        @TASK: Exponer el nombre del pipeline activo para telemetria y diagnostico
        @INPUT: Sin parametros
        @OUTPUT: "local" o "cloud" segun el ultimo hot-swap registrado en _active_pipeline
        @CONTEXT: Propiedad de observabilidad para APIServer y TourOrchestrador.
                  Combinada con swap_count permite detectar degradacion del pipeline local.
        @SECURITY: Solo lectura; sin mutaciones ni side-effects de ningun tipo.
        """
        return self._active_pipeline

    @property
    def swap_count(self) -> int:
        """
        @TASK: Exponer el contador acumulado de hot-swaps para diagnostico de degradacion
        @INPUT: Sin parametros
        @OUTPUT: Numero total de conmutaciones local->cloud desde el inicio de la sesion activa
        @CONTEXT: Metrica de salud del pipeline local (Ollama, faster-whisper, piper-tts).
                  Un swap_count alto durante una sesion indica problema de recursos en edge.
        @SECURITY: Solo lectura; sin mutaciones ni side-effects de ningun tipo.
        """
        return self._swap_count

    @property
    def current_zone(self) -> str:
        """
        @TASK: Exponer zona activa del tour para observabilidad
        @INPUT: Sin parametros
        @OUTPUT: zone_id de la zona activa o string vacio si no hay script cargado
        @CONTEXT: Propiedad de observabilidad para /content/script endpoint
        @SECURITY: Solo lectura
        """
        return self._current_waypoint

    @property
    def loaded_script(self) -> Optional[object]:
        """
        @TASK: Exponer el guion cargado para serializacion en /content/script
        @INPUT: Sin parametros
        @OUTPUT: Instancia TourScript actual o None
        @CONTEXT: Consumida por el endpoint GET /content/script para serializar JSON
        @SECURITY: Solo lectura; el objeto es inmutable post-validacion Pydantic
        """
        return self._script

    def load_script_from_file(self, filepath: Path) -> None:
        """
        @TASK: Cargar y validar el guion de tour desde un archivo JSON
        @INPUT: filepath — Path al archivo JSON del guion (data/mvp_tour_script.json)
        @OUTPUT: self._script asignado con TourScript validado
        @CONTEXT: Invocado en startup y por POST /content/script/reload
                  Import lazy de TourScript para evitar importacion circular con api.schemas
        STEP 1: Leer bytes del archivo JSON desde disco
        STEP 2: Parsear JSON y validar con TourScript (Pydantic)
        STEP 3: Asignar _script y restaurar zona activa si sigue existiendo
        @SECURITY: FileNotFoundError y ValidationError se propagan al caller
                   Sin ejecucion de codigo arbitrario; solo deserializacion JSON
        """
        from api.schemas import TourScript

        raw = filepath.read_text(encoding="utf-8")

        data = json.loads(raw)
        new_script = TourScript.model_validate(data)

        self._script = new_script
        waypoint_ids = {w.waypoint_id for w in new_script.waypoints}
        if self._current_waypoint not in waypoint_ids:
            self._current_waypoint = new_script.waypoints[0].waypoint_id if new_script.waypoints else ""
        self._refresh_waypoint_cache()
        LOGGER.info(
            "[CM] Script cargado: version='%s' waypoints=%d waypoint_activo='%s'",
            new_script.version,
            len(new_script.waypoints),
            self._current_waypoint,
        )

    def set_active_zone(self, zone_id: str) -> None:
        """
        @TASK: Cambiar la zona activa del tour y actualizar el system_prompt en cache
        @INPUT: zone_id — identificador de zona definido en TourScript
        @OUTPUT: _current_zone y _current_zone_prompt actualizados
        @CONTEXT: Invocado por TourOrchestrator al alcanzar un trigger_waypoint
        STEP 1: Validar que el script esta cargado
        STEP 2: Buscar la zona por zone_id
        STEP 3: Actualizar zona activa y el prompt en cache
        @SECURITY: ValueError si zone_id no existe en el guion cargado
        """
        if self._script is None:
            LOGGER.warning("[CM] set_active_zone('%s') ignorado: no hay script cargado.", zone_id)
            return

        waypoint = next(
            (w for w in self._script.waypoints if w.waypoint_id == zone_id),
            None,
        )
        if waypoint is None:
            raise ValueError(
                f"waypoint_id='{zone_id}' no existe en el guion cargado "
                f"(version='{self._script.version}'). "
                f"Waypoints validos: {[w.waypoint_id for w in self._script.waypoints]}"
            )

        self._current_waypoint = zone_id
        self._refresh_waypoint_cache()
        LOGGER.info("[CM] Waypoint activo cambiado a '%s'.", zone_id)

    def _refresh_waypoint_cache(self) -> None:
        """
        @TASK: Actualizar la cache del system_prompt de la zona activa
        @INPUT: Sin parametros (lee _script y _current_zone)
        @OUTPUT: _current_zone_prompt actualizado
        @CONTEXT: Helper interno; invocado por load_script_from_file y set_active_zone
        @SECURITY: Sin efectos secundarios externos
        """
        if self._script is None or not self._current_waypoint:
            self._current_waypoint_prompt = ""
            self._current_waypoint_interaction_type = "llm_qa"
            self._current_waypoint_script_text = ""
            return
        waypoint = next(
            (w for w in self._script.waypoints if w.waypoint_id == self._current_waypoint),
            None,
        )
        if waypoint is None:
            self._current_waypoint_prompt = ""
            self._current_waypoint_interaction_type = "llm_qa"
            self._current_waypoint_script_text = ""
            return
        self._current_waypoint_prompt = waypoint.system_prompt or ""
        self._current_waypoint_interaction_type = waypoint.interaction_type
        self._current_waypoint_script_text = waypoint.script_text or ""

    def _build_zoned_text(self, user_text: str) -> str:
        """
        @TASK: Construir el prompt final pre-concatenando el system_prompt de zona al input
        @INPUT: user_text — texto del usuario ya transcripto o recibido por texto
        @OUTPUT: String con system_prompt prepended si hay zona activa; user_text sin cambios si no
        @CONTEXT: Unico punto de inyeccion de contenido antes del envio a Ollama o cloud LLM
        STEP 1: Si hay zone_prompt activo, prepend con separador canonico "Usuario:"
        STEP 2: Si no, retornar user_text sin modificacion alguna
        @SECURITY: Sin modificacion del http client ni del payload JSON de Ollama;
                   solo se modifica el string de texto antes de construir ConversationRequest
        """
        if self._current_waypoint_prompt:
            return f"{self._current_waypoint_prompt}\n\nUsuario: {user_text}"
        return user_text

    def get_waypoint_interaction_type(self, waypoint_id: str) -> Literal["scripted", "llm_qa"]:
        """
        @TASK: Consultar el tipo de interaccion configurado para un waypoint especifico del guion
        @INPUT: waypoint_id — identificador del waypoint a consultar ("I", "1", "2", "3", "F")
        @OUTPUT: "scripted" si el waypoint tiene script fijo; "llm_qa" si requiere LLM dinamico;
                 "llm_qa" como fallback seguro si no hay script cargado o el waypoint no existe
        @CONTEXT: Invocado por TourOrchestrator.on_enter_interacting() para seleccionar el pipeline
                  de interaccion correcto (process_scripted_interaction vs process_interaction).
        @SECURITY: Solo lectura sobre _script; sin mutaciones de estado ni side-effects.
        """
        if self._script is None:
            return "llm_qa"
        waypoint = next(
            (w for w in self._script.waypoints if w.waypoint_id == waypoint_id),
            None,
        )
        if waypoint is None:
            return "llm_qa"
        return waypoint.interaction_type

    async def process_scripted_interaction(self, waypoint_id: str) -> ConversationResponse:
        """
        @TASK: Ejecutar interaccion de guion fijo para un waypoint con interaction_type="scripted"
        @INPUT: waypoint_id — identificador del waypoint con script fijo a reproducir
        @OUTPUT: ConversationResponse con script_text del waypoint y audio reproducido via TTS local;
                 ConversationResponse vacio con audio_stream_ready=False si el waypoint no es valido
        @CONTEXT: Invocado por TourOrchestrator.on_enter_interacting() cuando interaction_type="scripted".
                  No realiza STT ni LLM; reproduce directamente el texto fijo del guion cargado.
        @SECURITY: Sin llamadas externas; el script_text proviene del guion validado por Pydantic.

        STEP 1: Retornar respuesta vacia con audio_stream_ready=False si no hay script cargado
        STEP 2: Buscar waypoint; retornar vacio si no existe o interaction_type != "scripted"
        STEP 3: Sintetizar script_text via synthesize_and_play y retornar ConversationResponse
        """
        if self._script is None:
            return ConversationResponse(
                answer_text="",
                source_pipeline="scripted",
                audio_stream_ready=False,
            )
        waypoint = next(
            (w for w in self._script.waypoints if w.waypoint_id == waypoint_id),
            None,
        )
        if waypoint is None or waypoint.interaction_type != "scripted":
            return ConversationResponse(
                answer_text="",
                source_pipeline="scripted",
                audio_stream_ready=False,
            )
        script_text = waypoint.script_text or ""
        if not script_text:
            return ConversationResponse(
                answer_text="",
                source_pipeline="scripted",
                audio_stream_ready=False,
            )
        await self._local.synthesize_and_play(script_text)
        return ConversationResponse(
            answer_text=script_text,
            source_pipeline="scripted",
            audio_stream_ready=True,
        )

    def get_waypoint_pose_2d(self, waypoint_id: str) -> Optional[tuple[float, float, float]]:
        """
        @TASK: Obtener la pose 2D calibrada (x, y, theta) de un waypoint desde el guion cargado
        @INPUT: waypoint_id — identificador del waypoint a consultar ("I", "1", "2", "3", "F")
        @OUTPUT: Tupla (x: float, y: float, theta: float) con la pose en el marco del mapa;
                 None si no hay script, el waypoint no existe o pose_2d no es un dict valido
        @CONTEXT: Invocado por TourOrchestrator._resolve_navigation_target() en modo robot_mode="real"
                  para obtener coordenadas calibradas del mapa en lugar del fallback del TourPlan.
        @SECURITY: Solo lectura sobre _script; sin mutaciones. TypeError y ValueError se absorben
                   retornando None para garantizar que el orquestador use el waypoint fallback seguro.
        """
        if self._script is None:
            return None
        waypoint = next(
            (w for w in self._script.waypoints if w.waypoint_id == waypoint_id),
            None,
        )
        if waypoint is None:
            return None
        pose = getattr(waypoint, "pose_2d", None)
        if not isinstance(pose, dict):
            return None
        try:
            x = float(pose.get("x", 0.0))
            y = float(pose.get("y", 0.0))
            theta = float(pose.get("theta", 0.0))
        except (TypeError, ValueError):
            return None
        return (x, y, theta)

    async def process_interaction(
        self,
        audio_buffer: NDArray[np.float32],
        *,
        language: str = "es",
        preferred_pipeline: str = "local",
    ) -> ConversationResponse:
        """
        @TASK: Procesar buffer de audio completo a traves del pipeline NLP hibrido con hot-swap
        @INPUT: audio_buffer — PCM float32 mono capturado por el detector de wake-word del robot
                language — codigo iso639 del idioma del audio (default "es")
                preferred_pipeline — "local" o "cloud" para forzar el pipeline inicial
        @OUTPUT: ConversationResponse con respuesta de texto y audio reproducido;
                 source_pipeline en la respuesta indica si respondio "local" o "cloud"
        @CONTEXT: Punto de entrada principal para interaccion activada por audio (wake-word).
                  STT y LLM tienen timeouts independientes para granularidad maxima de hot-swap.
                  Si interaction_type del waypoint activo es "llm_qa", delega a start_interactive_session.
        @SECURITY: audio_buffer no se persiste ni escribe a disco en ningun paso del pipeline completo.
                   Si cloud tambien falla, la excepcion se propaga sin atrapar al TourOrchestrator.

        STEP 1: Si interaction_type activo es "llm_qa", delegar a start_interactive_session y retornar
        STEP 2: Incrementar _total_interactions; intentar STT local con timeout STT_TIMEOUT_S
        STEP 3: Hot-swap a cloud via _cloud_fallback_text ante timeout o excepcion de STT
        STEP 4: Construir ConversationRequest con texto transcripto y zone prompt prepended
        STEP 5: Intentar LLM local con timeout LLM_LOCAL_TIMEOUT_S + TTS_TIMEOUT_S; hot-swap si falla
        STEP 6: Ejecutar pipeline cloud via _cloud_fallback_text tras hot-swap o si preferred="cloud"
        """
        if self._current_waypoint_interaction_type == "llm_qa":
            return await self.start_interactive_session(self._current_waypoint)

        self._total_interactions += 1
        user_text: str = ""

        if preferred_pipeline == "local":
            try:
                user_text = await asyncio.wait_for(
                    self._local.transcribe(audio_buffer, language=language),
                    timeout=STT_TIMEOUT_S,
                )
                LOGGER.debug("[CM] STT local exitoso: '%s'", user_text[:60])
            except (TimeoutError, asyncio.TimeoutError) as exc:
                LOGGER.warning(
                    "[CM] Hot-swap STT: timeout %.1f s — conmutando a cloud. (%s)",
                    STT_TIMEOUT_S,
                    type(exc).__name__,
                )
                self._swap_count += 1
                self._active_pipeline = "cloud"
                return await self._cloud_fallback_text(
                    raw_text="[STT timeout — entrada de usuario no disponible]"
                )
            except Exception as exc:
                LOGGER.error(
                    "[CM] Hot-swap STT: excepcion '%s' — conmutando a cloud.",
                    type(exc).__name__,
                )
                self._swap_count += 1
                self._active_pipeline = "cloud"
                return await self._cloud_fallback_text(
                    raw_text="[STT error — entrada de usuario no disponible]"
                )

        request = ConversationRequest(
            user_text=self._build_zoned_text(user_text),
            locale=language,
        )

        if preferred_pipeline == "local" and self._active_pipeline == "local":
            try:
                response = await asyncio.wait_for(
                    self._local.generate(request),
                    timeout=LLM_LOCAL_TIMEOUT_S + TTS_TIMEOUT_S,
                )
                self._active_pipeline = "local"
                LOGGER.info("[CM] Respuesta local entregada. swap_count=%d", self._swap_count)
                return response
            except (TimeoutError, asyncio.TimeoutError):
                LOGGER.warning(
                    "[CM] Hot-swap LLM: timeout %.1f s — conmutando a cloud.",
                    LLM_LOCAL_TIMEOUT_S,
                )
                self._swap_count += 1
                self._active_pipeline = "cloud"
            except MemoryError as exc:
                LOGGER.error("[CM] Hot-swap LLM: MemoryError — %s", exc)
                self._swap_count += 1
                self._active_pipeline = "cloud"
            except Exception as exc:
                LOGGER.error(
                    "[CM] Hot-swap LLM: excepcion inesperada '%s' — conmutando a cloud.",
                    type(exc).__name__,
                )
                self._swap_count += 1
                self._active_pipeline = "cloud"

        return await self._cloud_fallback_text(raw_text=user_text)

    async def _cloud_fallback_text(self, raw_text: str) -> ConversationResponse:
        """
        @TASK: Ejecutar pipeline cloud como fallback con texto ya disponible post-hot-swap
        @INPUT: raw_text — texto del usuario transcripto localmente o placeholder de error de STT
        @OUTPUT: ConversationResponse desde el proveedor cloud configurado (_provider)
        @CONTEXT: Ruta de ejecucion cloud activada por hot-swap desde process_interaction o respond.
                  Si cloud tambien falla, la excepcion se propaga al TourOrchestrator sin atrapar.
        @SECURITY: TimeoutError del cloud se propaga al caller que controla la politica de retry.
                   raw_text no se persiste; solo se usa como contenido del ConversationRequest.

        STEP 1: Construir ConversationRequest con raw_text disponible (transcripto o placeholder)
        STEP 2: Invocar cloud_strategy.generate() con asyncio.wait_for timeout CLOUD_TIMEOUT_S
        STEP 3: Registrar resultado en LOGGER.info para trazabilidad y telemetria de fallback
        """
        request = ConversationRequest(user_text=raw_text)

        response = await asyncio.wait_for(
            self._cloud.generate(request),
            timeout=CLOUD_TIMEOUT_S,
        )

        LOGGER.info(
            "[CM] Respuesta cloud entregada. pipeline=%s swap_count=%d",
            response.source_pipeline,
            self._swap_count,
        )
        return response

    async def respond(self, request: ConversationRequest) -> ConversationResponse:
        """
        @TASK: Alias de compatibilidad para TourOrchestrator.handle_user_question() con hot-swap
        @INPUT: request — ConversationRequest con user_text ya transcripto (sin STT en este metodo)
        @OUTPUT: ConversationResponse desde la estrategia que responda sin timeout (local o cloud)
        @CONTEXT: Conservado para compatibilidad con TourOrchestrator.handle_user_question().
                  No realiza STT; user_text ya esta disponible en el ConversationRequest.
                  Misma politica de hot-swap que process_interaction: TimeoutError activa cloud.
        @SECURITY: Sin ejecucion de STT; el caller es responsable de que user_text este saneado.

        STEP 1: Intentar pipeline local con asyncio.wait_for timeout LLM_LOCAL_TIMEOUT_S + TTS_TIMEOUT_S
        STEP 2: Ante TimeoutError, MemoryError o excepcion inesperada, incrementar swap y activar cloud
        STEP 3: Invocar _cloud_fallback_text con el texto zoneado como fallback final
        """
        try:
            zoned_request = ConversationRequest(
                user_text=self._build_zoned_text(request.user_text),
                locale=request.locale,
                metadata=request.metadata,
            )
            response = await asyncio.wait_for(
                self._local.generate(zoned_request),
                timeout=LLM_LOCAL_TIMEOUT_S + TTS_TIMEOUT_S,
            )
            self._active_pipeline = "local"
            return response
        except (TimeoutError, asyncio.TimeoutError):
            LOGGER.warning("[CM] respond(): hot-swap a cloud por timeout local.")
            self._swap_count += 1
            self._active_pipeline = "cloud"
        except (MemoryError, Exception) as exc:
            LOGGER.error("[CM] respond(): hot-swap a cloud por '%s'.", type(exc).__name__)
            self._swap_count += 1
            self._active_pipeline = "cloud"

        return await self._cloud_fallback_text(
            raw_text=self._build_zoned_text(request.user_text)
        )

    async def start_interactive_session(self, waypoint_id: str) -> ConversationResponse:
        """
        @TASK: Ejecutar sesion interactiva completa Mic -> STT local -> LLM local -> TTS local
        @INPUT: waypoint_id — identificador de la zona activa para contextualizar el prompt de zona
        @OUTPUT: ConversationResponse local; fallback seguro si falla captura de microfono o pipeline local
        @CONTEXT: Invocado por process_interaction() cuando interaction_type="llm_qa".
                  Captura PCM con AudioHardwareBridge, STT con LocalNLPPipeline.transcribe(),
                  inferencia Ollama local + TTS local con LocalNLPPipeline.generate().
        @SECURITY: Audio y texto se procesan en memoria local; sin STT cloud ni salida de datos fuera del host.

        STEP 1: Activar zona y capturar PCM local desde microfono mediante audio_bridge.listen_pcm()
        STEP 2: Transcribir PCM con _local.transcribe() y construir prompt zoneado via _build_zoned_text()
        STEP 3: Ejecutar _local.generate() para LLM local + TTS local sin bloquear el event loop
        STEP 4: En error, reproducir fallback seguro via _local.synthesize_and_play() y retornar respuesta segura
        """
        try:
            self.set_active_zone(waypoint_id)
        except Exception:
            pass

        try:
            audio_pcm = await self._audio_bridge.listen_pcm()
            if audio_pcm.size == 0:
                message = "No detecte entrada de voz. Retornando a estado seguro."
                await self._local.synthesize_and_play(message)
                return ConversationResponse(
                    answer_text=message,
                    source_pipeline="local",
                    audio_stream_ready=True,
                )

            user_input = await asyncio.wait_for(
                self._local.transcribe(audio_pcm, language="es"),
                timeout=STT_TIMEOUT_S,
            )
            if not user_input.strip():
                fallback = "Error de procesamiento de hardware. Retornando a estado seguro."
                await self._local.synthesize_and_play(fallback)
                return ConversationResponse(
                    answer_text=fallback,
                    source_pipeline="local",
                    audio_stream_ready=True,
                )

            request = ConversationRequest(
                user_text=self._build_zoned_text(user_input),
                locale="es",
            )
            response = await asyncio.wait_for(
                self._local.generate(request),
                timeout=LLM_LOCAL_TIMEOUT_S + TTS_TIMEOUT_S,
            )
            self._active_pipeline = "local"
            return response
        except Exception:
            fallback = "Error de procesamiento de hardware. Retornando a estado seguro."
            try:
                await self._local.synthesize_and_play(fallback)
            except Exception:
                pass
            return ConversationResponse(
                answer_text=fallback,
                source_pipeline="local",
                audio_stream_ready=False,
            )

    def close(self) -> None:
        """
        @TASK: Liberar recursos de ambos pipelines NLP en el shutdown global del sistema
        @INPUT: Sin parametros
        @OUTPUT: Executors y clientes HTTP de LocalNLPPipeline y CloudNLPPipeline liberados correctamente
        @CONTEXT: Invocado desde _graceful_shutdown de main.py durante el lifespan de FastAPI.
                  No bloquea; cancel_futures=True en los executors internos de cada pipeline garantiza
                  que no haya inferencias o reproducciones de audio tardias fuera del ciclo de vida.
        @SECURITY: Orden de cierre determinista: local primero, cloud segundo, para evitar uso del
                   pipeline cloud mientras el local esta parcialmente apagado.

        STEP 1: Invocar _local.close() liberando ProcessPoolExecutor y ThreadPoolExecutor locales
        STEP 2: Invocar _cloud.close() liberando ThreadPoolExecutor de audio cloud
        """
        LOGGER.info("[CM] Cerrando ConversationManager.")
        self._local.close()
        self._cloud.close()


# ---------------------------------------------------------------------------
# Exportaciones
# ---------------------------------------------------------------------------

__all__ = [
    "CloudNLPPipeline",
    "ConversationManager",
    "ConversationRequest",
    "ConversationResponse",
    "LocalNLPPipeline",
    "NLPStrategy",
]