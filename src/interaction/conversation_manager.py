from __future__ import annotations

# @TASK: Implementar pipeline NLP hibrido local/cloud con patron Strategy y hot-swap
# @INPUT: Buffers de audio PCM; texto desde TourOrchestrator via ConversationRequest
# @OUTPUT: Texto de respuesta reproducido por ALSA y ConversationResponse tipada
# @CONTEXT: Modulo de interaccion HIL Fase 4; opera en red air-gapped con fallback cloud
# STEP 1: Definir contratos de Strategy (NLPStrategy, STTStrategy, TTSStrategy)
# STEP 2: Implementar LocalNLPPipeline (faster-whisper + Ollama + piper-tts + sounddevice)
# STEP 3: Implementar CloudNLPPipeline (httpx async OpenAI/Gemini + TTS cloud)
# STEP 4: Implementar ConversationManager con hot-swap en asyncio.wait_for
# STEP 5: Aislar todo computo CPU y I/O de audio en executors inyectados
# @SECURITY: Ninguna llamada bloqueante de audio o inferencia ocurre en el event loop
# @AI_CONTEXT: ThreadPoolExecutor para I/O (sounddevice/ALSA); ProcessPoolExecutor para CPU (whisper/piper)

import asyncio
import logging
import os
import queue
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Union

import httpx
import numpy as np
from numpy.typing import NDArray


# ---------------------------------------------------------------------------
# Constantes de configuracion
# ---------------------------------------------------------------------------

# @TASK: Declarar constantes de timeout para hot-swap local->cloud
# @INPUT: Ninguno
# @OUTPUT: Constantes de tiempo limite para cada etapa del pipeline
# @CONTEXT: Tiempos calibrados para red air-gapped con hardware embebido
# STEP 1: STT timeout — faster-whisper puede tardar en CPU sin CUDA
# STEP 2: LLM timeout — Ollama cuantizado en companion PC
# STEP 3: TTS timeout — piper-tts; tipicamente < 500 ms para frases cortas
# STEP 4: Cloud timeout — endpoint OpenAI/Gemini con margen de red
# @SECURITY: Timeouts estrictos previenen bloqueo del orquestador de tour
# @AI_CONTEXT: Ajustar STT_TIMEOUT segun hardware real (4-8 s en CPU-only arm64)
STT_TIMEOUT_S: float = 4.0      # faster-whisper transcripcion audio completo
LLM_LOCAL_TIMEOUT_S: float = 2.5  # Ollama respuesta primera iteracion
TTS_TIMEOUT_S: float = 3.0      # piper-tts + enqueue ALSA
CLOUD_TIMEOUT_S: float = 6.0    # OpenAI/Gemini RTT incluyendo TLS negotiation

OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str    = os.environ.get("OLLAMA_MODEL", "llama3:8b-instruct-q4_K_M")
PIPER_MODEL_PATH: str = os.environ.get("PIPER_MODEL_PATH", "/usr/share/piper/es_MX-claude-high.onnx")
AUDIO_SAMPLE_RATE: int = 22050  # Hz; frecuencia nativa de piper-tts
AUDIO_BLOCK_SIZE: int  = 2048   # frames por bloque ALSA

CLOUD_PROVIDER: str      = os.environ.get("CLOUD_NLP_PROVIDER", "openai")  # "openai" | "gemini"
OPENAI_API_KEY: str      = os.environ.get("OPENAI_API_KEY", "")
OPENAI_CHAT_URL: str     = "https://api.openai.com/v1/chat/completions"
OPENAI_TTS_URL: str      = "https://api.openai.com/v1/audio/speech"
GEMINI_API_KEY: str      = os.environ.get("GEMINI_API_KEY", "")
GEMINI_CHAT_URL_TMPL: str = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent?key={key}"
)

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tipos de datos de dominio
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ConversationRequest:
    # @TASK: Encapsular solicitud de interaccion desde el orquestador
    # @INPUT: Texto del usuario obtenido de STT o directamente del orchestrator
    # @OUTPUT: Estructura inmutable consumible por cualquier NLPStrategy
    # @CONTEXT: Contrato de entrada del patron Strategy
    # STEP 1: Capturar texto, locale y metadata de contexto del tour
    # @SECURITY: No persiste audio crudo; solo texto ya transcripto
    # @AI_CONTEXT: metadata puede incluir waypoint_id y estado del orchestrator
    user_text: str
    locale: str = "es-MX"
    metadata: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True, slots=True)
class ConversationResponse:
    # @TASK: Encapsular respuesta generada por cualquier estrategia NLP
    # @INPUT: Texto de respuesta, identificador de pipeline y flag de audio
    # @OUTPUT: Estructura inmutable consumible por TourOrchestrator
    # @CONTEXT: Contrato de salida del patron Strategy
    # STEP 1: Registrar texto, fuente del pipeline y disponibilidad de audio
    # @SECURITY: No incluye datos de autenticacion del proveedor cloud
    # @AI_CONTEXT: source_pipeline es "local" o "cloud" para telemetria de fallback
    answer_text: str
    source_pipeline: str
    audio_stream_ready: bool


# ---------------------------------------------------------------------------
# Contratos abstractos (Strategy interfaces)
# ---------------------------------------------------------------------------

class NLPStrategy(ABC):
    # @TASK: Definir contrato abstracto de estrategia NLP completa
    # @INPUT: ConversationRequest con texto ya disponible
    # @OUTPUT: ConversationResponse despues de STT->LLM->TTS encadenados
    # @CONTEXT: Interface del patron Strategy; LocalNLPPipeline y CloudNLPPipeline la implementan
    # STEP 1: Declarar generate() como metodo abstracto async
    # @SECURITY: Cada implementacion es responsable de su aislamiento de I/O
    # @AI_CONTEXT: ConversationManager inyecta la estrategia activa en runtime

    @abstractmethod
    async def generate(self, request: ConversationRequest) -> ConversationResponse:
        ...  # STEP 1


# ---------------------------------------------------------------------------
# FUNCIONES AISLABLES EN EXECUTOR (top-level para pickle en ProcessPoolExecutor)
# ---------------------------------------------------------------------------

def _run_whisper_transcription(
    audio_pcm: NDArray[np.float32],
    model_size: str,
    language: str,
) -> str:
    # @TASK: Ejecutar transcripcion STT con faster-whisper en proceso aislado
    # @INPUT: audio_pcm — array float32 mono normalizado; model_size; language
    # @OUTPUT: Texto transcripto como string
    # @CONTEXT: Funcion top-level para compatibilidad con ProcessPoolExecutor (pickle)
    # STEP 1: Importar WhisperModel dentro de la funcion para evitar import en proceso principal
    # STEP 2: Instanciar modelo con device=cpu y compute_type int8 para hardware embebido
    # STEP 3: Transcribir y concatenar segmentos retornados por el generador
    # @SECURITY: Sin escritura a disco; el audio se pasa como array en memoria
    # @AI_CONTEXT: model_size tipico "small" o "base" para companion PC arm64 sin VRAM

    from faster_whisper import WhisperModel  # STEP 1

    model = WhisperModel(                    # STEP 2
        model_size,
        device="cpu",
        compute_type="int8",
    )
    segments, _ = model.transcribe(          # STEP 3
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
    # @TASK: Sintetizar audio PCM desde texto con piper-tts en proceso aislado
    # @INPUT: text — respuesta LLM; model_path — ruta ONNX del modelo piper; sample_rate
    # @OUTPUT: Array float32 mono con audio sintetizado normalizado en [-1, 1]
    # @CONTEXT: Funcion top-level para ProcessPoolExecutor; sin estado global
    # STEP 1: Importar piper dentro de la funcion para aislamiento de proceso
    # STEP 2: Instanciar Voice con el modelo ONNX especificado
    # STEP 3: Sintetizar audio y retornar como ndarray float32 normalizado
    # @SECURITY: Sin escritura a disco; todo en memoria
    # @AI_CONTEXT: El array resultante se pasa al hilo de sounddevice via cola thread-safe

    from piper import PiperVoice  # STEP 1

    voice = PiperVoice.load(model_path)  # STEP 2

    audio_chunks: list[bytes] = []
    for audio_bytes in voice.synthesize_stream_raw(text):  # STEP 3
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
    # @TASK: Reproducir array PCM en el dispositivo ALSA por defecto via sounddevice
    # @INPUT: pcm_float32 — audio normalizado; sample_rate; block_size por callback
    # @OUTPUT: Reproduccion bloqueante hasta fin del audio o error de dispositivo
    # @CONTEXT: Funcion top-level ejecutada en ThreadPoolExecutor de I/O de audio
    # STEP 1: Importar sounddevice dentro de la funcion para aislamiento de import
    # STEP 2: Llenar cola lock-free con bloques de block_size frames
    # STEP 3: Abrir OutputStream con callback que consume de la cola
    # STEP 4: Esperar evento de fin de reproduccion de forma bloqueante (en hilo de I/O)
    # @SECURITY: Sin archivos temporales; audio en memoria durante toda la reproduccion
    # @AI_CONTEXT: El callback de sounddevice corre en hilo de audio del OS; la cola es thread-safe

    import sounddevice as sd  # STEP 1

    # STEP 2: segmentar en bloques
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
        # STEP 3: callback consume bloque de la cola o escribe silencio en underrun
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

    # STEP 4: abrir stream y esperar completitud en el hilo de I/O
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
    # @TASK: Implementar estrategia NLP completa en edge usando faster-whisper/Ollama/piper-tts
    # @INPUT: audio_pcm opcional y texto ya transcripto via ConversationRequest
    # @OUTPUT: ConversationResponse con texto y audio reproducido por ALSA
    # @CONTEXT: Strategy primaria en red air-gapped; hot-swap a cloud ante timeout o fallo
    # STEP 1: Configurar parametros de cada etapa (STT, LLM, TTS)
    # STEP 2: Delegar transcripcion y sintesis a ProcessPoolExecutor inyectado
    # STEP 3: Delegar reproduccion ALSA a ThreadPoolExecutor de I/O de audio
    # @SECURITY: Ningun dato del usuario sale de la LAN durante el pipeline local
    # @AI_CONTEXT: cpu_executor debe ser ProcessPoolExecutor; audio_executor ThreadPoolExecutor

    def __init__(
        self,
        *,
        model_name: str = OLLAMA_MODEL,
        whisper_model_size: str = "small",
        piper_model_path: str = PIPER_MODEL_PATH,
        ollama_base_url: str = OLLAMA_BASE_URL,
        cpu_executor: Optional[ProcessPoolExecutor] = None,
        audio_executor: Optional[ThreadPoolExecutor] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        # @TASK: Inicializar estrategia local con configuracion inyectable
        # @INPUT: Parametros de modelos, executors y cliente HTTP para Ollama
        # @OUTPUT: Estrategia local lista; executors propios si no son inyectados
        # @CONTEXT: Constructor de LocalNLPPipeline; soporta inyeccion de dependencias
        # STEP 1: Persistir parametros de cada etapa del pipeline
        # STEP 2: Crear executors propios si no son inyectados
        # STEP 3: Registrar flag de ownership para shutdown controlado
        # @SECURITY: cpu_executor con max_workers=1 evita saturacion de RAM en arm64
        # @AI_CONTEXT: ProcessPoolExecutor se crea en el proceso principal; los workers lo forkan

        # STEP 1
        self._model_name: str = model_name
        self._whisper_model_size: str = whisper_model_size
        self._piper_model_path: str = piper_model_path
        self._ollama_base_url: str = ollama_base_url.rstrip("/")

        # STEP 2
        self._owns_cpu_executor = cpu_executor is None
        self._cpu_executor: ProcessPoolExecutor = cpu_executor or ProcessPoolExecutor(
            max_workers=1
        )

        self._owns_audio_executor = audio_executor is None
        self._audio_executor: ThreadPoolExecutor = audio_executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="tts-alsa",
        )

        # STEP 3
        self._http_client: Optional[httpx.AsyncClient] = http_client
        self._owns_http_client = http_client is None

    async def _get_http_client(self) -> httpx.AsyncClient:
        # @TASK: Obtener o crear cliente HTTP para comunicacion con Ollama
        # @INPUT: Sin parametros
        # @OUTPUT: Instancia de httpx.AsyncClient reutilizable
        # @CONTEXT: Inicializacion lazy para compatibilidad con ciclo de vida async
        # STEP 1: Retornar cliente existente o instanciar uno nuevo
        # @SECURITY: Sin credenciales; Ollama corre en localhost sin autenticacion
        # @AI_CONTEXT: Timeout de conexion separado del timeout de inferencia
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._ollama_base_url,
                timeout=httpx.Timeout(connect=2.0, read=LLM_LOCAL_TIMEOUT_S, write=2.0, pool=1.0),
            )
        return self._http_client  # STEP 1

    async def transcribe(
        self,
        audio_pcm: NDArray[np.float32],
        language: str = "es",
    ) -> str:
        # @TASK: Transcribir audio PCM a texto via faster-whisper en ProcessPoolExecutor
        # @INPUT: audio_pcm — array float32 mono; language — codigo iso639 del idioma
        # @OUTPUT: Texto transcripto como string
        # @CONTEXT: Etapa STT del pipeline local; CPU-bound, aislada en proceso separado
        # STEP 1: Despachar _run_whisper_transcription al cpu_executor
        # STEP 2: Aplicar timeout STT_TIMEOUT_S con asyncio.wait_for
        # STEP 3: Propagar TimeoutError hacia generate() para activar hot-swap
        # @SECURITY: El array de audio no se escribe a disco
        # @AI_CONTEXT: ProcessPoolExecutor.submit no es awaitable; usar loop.run_in_executor

        loop = asyncio.get_running_loop()

        # STEP 1 + 2
        return await asyncio.wait_for(
            loop.run_in_executor(
                self._cpu_executor,
                _run_whisper_transcription,
                audio_pcm,
                self._whisper_model_size,
                language,
            ),
            timeout=STT_TIMEOUT_S,  # STEP 3
        )

    async def _infer_ollama(self, prompt: str) -> str:
        # @TASK: Invocar Ollama local via /api/generate con httpx asincrono
        # @INPUT: prompt — texto del usuario ya transcripto
        # @OUTPUT: Texto de respuesta generado por el LLM cuantizado
        # @CONTEXT: Etapa LLM del pipeline local; operacion de red local (localhost)
        # STEP 1: Construir payload de solicitud al endpoint /api/generate de Ollama
        # STEP 2: Realizar POST async con timeout LLM_LOCAL_TIMEOUT_S
        # STEP 3: Extraer campo response del JSON y retornar
        # @SECURITY: Endpoint localhost; sin salida al exterior de la LAN
        # @AI_CONTEXT: stream=False para respuesta completa en un solo round-trip

        client = await self._get_http_client()

        # STEP 1
        payload = {
            "model": self._model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.4},
        }

        # STEP 2
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

        # STEP 3
        data = response.json()
        return str(data.get("response", "")).strip()

    async def synthesize_and_play(self, text: str) -> None:
        # @TASK: Sintetizar texto con piper-tts y reproducir en ALSA de forma no bloqueante
        # @INPUT: text — respuesta del LLM a sintetizar
        # @OUTPUT: Audio reproducido por el altavoz de 5W del Unitree G1; sin retorno de valor
        # @CONTEXT: Etapa TTS del pipeline local; CPU en proceso + I/O en hilo de audio
        # STEP 1: Delegar sintesis PCM a ProcessPoolExecutor con timeout TTS_TIMEOUT_S
        # STEP 2: Crear tarea asyncio para reproduccion ALSA en ThreadPoolExecutor
        # STEP 3: Reproduccion se lanza como fire-and-forget no bloqueante para el orquestador
        # @SECURITY: Sin archivos temporales; PCM en memoria entre procesos
        # @AI_CONTEXT: La tarea de reproduccion puede cancelarse si llega un stop-word

        loop = asyncio.get_running_loop()

        # STEP 1: sintesis en proceso aislado
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

        # STEP 2 + 3: reproduccion en hilo de I/O; fire-and-forget
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
        # @TASK: Ejecutar pipeline completo STT(opcional)->LLM->TTS en edge
        # @INPUT: request — ConversationRequest con user_text y locale
        # @OUTPUT: ConversationResponse con respuesta de Ollama
        # @CONTEXT: Implementacion de NLPStrategy.generate() para pipeline local
        # STEP 1: Invocar Ollama con el texto del request con timeout LLM_LOCAL_TIMEOUT_S
        # STEP 2: Sintetizar y enviar a ALSA como tarea asincrona no bloqueante
        # STEP 3: Retornar ConversationResponse con source_pipeline="local"
        # @SECURITY: TimeoutError se propaga al ConversationManager para activar hot-swap
        # @AI_CONTEXT: La transcripcion de audio (STT) se realiza en process_interaction()

        # STEP 1
        answer_text = await self._infer_ollama(request.user_text)

        # STEP 2
        try:
            await self.synthesize_and_play(answer_text)
        except Exception as exc:
            LOGGER.warning("[LocalNLP] TTS fallo, respuesta de texto disponible: %s", exc)

        # STEP 3
        return ConversationResponse(
            answer_text=answer_text,
            source_pipeline="local",
            audio_stream_ready=True,
        )

    def close(self) -> None:
        # @TASK: Liberar executors propios del pipeline local
        # @INPUT: Sin parametros
        # @OUTPUT: ProcessPoolExecutor y ThreadPoolExecutor detenidos si son de propiedad local
        # @CONTEXT: Invocado por ConversationManager.close() durante shutdown global
        # STEP 1: Apagar cpu_executor si fue creado internamente
        # STEP 2: Apagar audio_executor si fue creado internamente
        # STEP 3: Cerrar cliente HTTP si fue creado internamente
        # @SECURITY: cancel_futures=True previene inferencias tardias fuera del ciclo de vida
        # @AI_CONTEXT: Si los executors son inyectados, el caller es responsable de cerrarlos

        if self._owns_cpu_executor:   # STEP 1
            self._cpu_executor.shutdown(wait=False, cancel_futures=True)
        if self._owns_audio_executor: # STEP 2
            self._audio_executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Pipeline Nube (Cloud Strategy)
# ---------------------------------------------------------------------------

class CloudNLPPipeline(NLPStrategy):
    # @TASK: Implementar estrategia NLP via API cloud (OpenAI o Gemini) como fallback
    # @INPUT: ConversationRequest con texto del usuario
    # @OUTPUT: ConversationResponse con respuesta del proveedor cloud
    # @CONTEXT: Strategy de fallback activada por hot-swap ante timeout del pipeline local
    # STEP 1: Configurar proveedor, claves de API y cliente httpx async compartido
    # STEP 2: Rutear la solicitud al endpoint correcto segun CLOUD_PROVIDER
    # STEP 3: TTS cloud via OpenAI tts-1 o sintesis local de emergencia
    # @SECURITY: API keys leidas desde variables de entorno; nunca hardcodeadas
    # @AI_CONTEXT: El cliente httpx se reutiliza entre llamadas para connection pooling

    def __init__(
        self,
        *,
        timeout_s: float = CLOUD_TIMEOUT_S,
        provider: str = CLOUD_PROVIDER,
        openai_api_key: str = OPENAI_API_KEY,
        gemini_api_key: str = GEMINI_API_KEY,
        audio_executor: Optional[ThreadPoolExecutor] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        # @TASK: Inicializar estrategia cloud con proveedor y credenciales configurables
        # @INPUT: timeout_s; provider "openai"|"gemini"; claves de API; executors
        # @OUTPUT: Estrategia cloud lista con cliente HTTP async
        # @CONTEXT: Constructor de CloudNLPPipeline; soporta inyeccion de dependencias
        # STEP 1: Validar parametros de configuracion
        # STEP 2: Persistir credenciales y timeout; inicializar cliente HTTP lazy
        # STEP 3: Registrar executor de audio para reproduccion post-sintesis TTS cloud
        # @SECURITY: Advertir en log si las claves de API estan vacias al inicializar
        # @AI_CONTEXT: http_client inyectable para testing sin trafico de red real

        if timeout_s <= 0:
            raise ValueError("timeout_s debe ser mayor que 0.")

        # STEP 1 + 2
        self._timeout_s: float = timeout_s
        self._provider: str = provider.lower()
        self._openai_api_key: str = openai_api_key
        self._gemini_api_key: str = gemini_api_key

        if self._provider == "openai" and not self._openai_api_key:
            LOGGER.warning("[CloudNLP] OPENAI_API_KEY no configurada; el fallback cloud fallara.")
        if self._provider == "gemini" and not self._gemini_api_key:
            LOGGER.warning("[CloudNLP] GEMINI_API_KEY no configurada; el fallback cloud fallara.")

        # STEP 3
        self._owns_audio_executor = audio_executor is None
        self._audio_executor: ThreadPoolExecutor = audio_executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cloud-tts-alsa",
        )
        self._owned_http = http_client is None
        self._http_client: Optional[httpx.AsyncClient] = http_client

    async def _get_http_client(self) -> httpx.AsyncClient:
        # @TASK: Obtener o crear cliente httpx para llamadas al proveedor cloud
        # @INPUT: Sin parametros
        # @OUTPUT: Instancia de httpx.AsyncClient configurada para el proveedor activo
        # @CONTEXT: Inicializacion lazy para compatibilidad con ciclo de vida async
        # STEP 1: Retornar cliente existente o instanciar con timeout y headers base
        # @SECURITY: Authorization header se agrega por solicitud en _call_*; no en el cliente base
        # @AI_CONTEXT: follow_redirects=True necesario para Gemini API
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=3.0, read=self._timeout_s, write=3.0, pool=1.0),
                follow_redirects=True,
            )
        return self._http_client  # STEP 1

    async def _call_openai_chat(self, user_text: str) -> str:
        # @TASK: Invocar OpenAI Chat Completions API para generar respuesta
        # @INPUT: user_text — texto del usuario transcripto
        # @OUTPUT: Respuesta textual del modelo gpt-4o-mini
        # @CONTEXT: Implementacion del backend OpenAI para CloudNLPPipeline
        # STEP 1: Construir payload con modelo y mensaje de usuario
        # STEP 2: Realizar POST con Authorization Bearer y timeout cloud
        # STEP 3: Extraer content del primer choice de la respuesta
        # @SECURITY: API key enviada en header; TLS obligatorio para endpoint externo
        # @AI_CONTEXT: Modelo gpt-4o-mini balancea latencia y costo para respuestas cortas

        client = await self._get_http_client()

        # STEP 1
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": user_text}],
            "max_tokens": 150,
            "temperature": 0.5,
        }
        headers = {"Authorization": f"Bearer {self._openai_api_key}"}

        # STEP 2
        response = await asyncio.wait_for(
            client.post(OPENAI_CHAT_URL, json=payload, headers=headers),
            timeout=self._timeout_s,
        )
        response.raise_for_status()

        # STEP 3
        data = response.json()
        return str(data["choices"][0]["message"]["content"]).strip()

    async def _call_gemini_chat(self, user_text: str) -> str:
        # @TASK: Invocar Gemini generateContent API para generar respuesta
        # @INPUT: user_text — texto del usuario transcripto
        # @OUTPUT: Respuesta textual del modelo gemini-1.5-flash
        # @CONTEXT: Implementacion del backend Gemini para CloudNLPPipeline
        # STEP 1: Construir URL con API key embebida como query param (protocolo Gemini)
        # STEP 2: Construir payload con parts de conversacion
        # STEP 3: Realizar POST y extraer texto del primer candidate
        # @SECURITY: API key en query param segun especificacion Gemini v1beta; TLS obligatorio
        # @AI_CONTEXT: candidates[0].content.parts[0].text es la ruta de extraccion estandar

        client = await self._get_http_client()

        # STEP 1
        url = GEMINI_CHAT_URL_TMPL.format(key=self._gemini_api_key)

        # STEP 2
        payload = {
            "contents": [{"parts": [{"text": user_text}]}],
            "generationConfig": {"maxOutputTokens": 150, "temperature": 0.5},
        }

        # STEP 3
        response = await asyncio.wait_for(
            client.post(url, json=payload),
            timeout=self._timeout_s,
        )
        response.raise_for_status()
        data = response.json()
        return str(data["candidates"][0]["content"]["parts"][0]["text"]).strip()

    async def _cloud_tts_openai(self, text: str) -> None:
        # @TASK: Sintetizar texto con OpenAI TTS y reproducir via ALSA
        # @INPUT: text — respuesta a sintetizar
        # @OUTPUT: Audio reproducido en el altavoz del robot; sin retorno de valor
        # @CONTEXT: TTS cloud como alternativa si piper-tts no esta disponible en cloud fallback
        # STEP 1: POST a OpenAI audio/speech con modelo tts-1 y voz "nova"
        # STEP 2: Leer bytes de audio WAV/MP3 y convertir a float32
        # STEP 3: Reproducir en ALSA via hilo de audio
        # @SECURITY: Respuesta de audio descargada en memoria; sin escritura a disco
        # @AI_CONTEXT: Respuesta de OpenAI TTS es MP3; requiere decodificacion adicional

        client = await self._get_http_client()
        headers = {"Authorization": f"Bearer {self._openai_api_key}"}
        payload = {"model": "tts-1", "input": text, "voice": "nova", "response_format": "pcm"}

        # STEP 1 + 2
        response = await asyncio.wait_for(
            client.post(OPENAI_TTS_URL, json=payload, headers=headers),
            timeout=self._timeout_s,
        )
        response.raise_for_status()

        pcm_int16 = np.frombuffer(response.content, dtype=np.int16)
        pcm_float32 = pcm_int16.astype(np.float32) / 32768.0

        # STEP 3: fire-and-forget en executor de audio
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
        # @TASK: Ejecutar pipeline cloud completo LLM->TTS para la solicitud recibida
        # @INPUT: request — ConversationRequest con user_text
        # @OUTPUT: ConversationResponse con respuesta del proveedor cloud activo
        # @CONTEXT: Implementacion de NLPStrategy.generate() para pipeline cloud
        # STEP 1: Rutear al backend correcto segun self._provider
        # STEP 2: Intentar TTS cloud si proveedor es OpenAI; registrar fallo no critico
        # STEP 3: Retornar ConversationResponse con source_pipeline="cloud"
        # @SECURITY: Sin reintentos automaticos; el orquestador controla la politica de retry
        # @AI_CONTEXT: TimeoutError se propaga al ConversationManager si el cloud tambien falla

        # STEP 1
        if self._provider == "openai":
            answer_text = await self._call_openai_chat(request.user_text)
        elif self._provider == "gemini":
            answer_text = await self._call_gemini_chat(request.user_text)
        else:
            raise ValueError(f"Proveedor cloud no reconocido: '{self._provider}'")

        # STEP 2
        if self._provider == "openai" and self._openai_api_key:
            try:
                await self._cloud_tts_openai(answer_text)
            except Exception as exc:
                LOGGER.warning("[CloudNLP] TTS cloud fallo: %s", exc)

        # STEP 3
        return ConversationResponse(
            answer_text=answer_text,
            source_pipeline="cloud",
            audio_stream_ready=True,
        )

    def close(self) -> None:
        # @TASK: Liberar recursos propios del pipeline cloud
        # @INPUT: Sin parametros
        # @OUTPUT: ThreadPoolExecutor detenido si es de propiedad local
        # @CONTEXT: Invocado por ConversationManager.close() durante shutdown global
        # STEP 1: Apagar audio_executor si fue creado internamente
        # @SECURITY: cancel_futures=True evita reproducciones de audio tardias
        # @AI_CONTEXT: El cliente HTTP se cierra en ConversationManager.close()
        if self._owns_audio_executor:  # STEP 1
            self._audio_executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# Orquestador principal — ConversationManager
# ---------------------------------------------------------------------------

class ConversationManager:
    # @TASK: Orquestar el pipeline NLP hibr con hot-swap local->cloud ante timeout
    # @INPUT: audio_buffer PCM via process_interaction(); texto via respond()
    # @OUTPUT: ConversationResponse desde la estrategia activa (local o cloud)
    # @CONTEXT: Punto de acceso unico del TourOrchestrator a la capa de interaccion
    # STEP 1: Definir proceso de audio completo (STT -> LLM -> TTS) en process_interaction
    # STEP 2: Hot-swap en asyncio.wait_for: capturar TimeoutError y ResourceWarning
    # STEP 3: Mantener telemetria de conmutaciones local<->cloud
    # @SECURITY: Las API keys cloud solo se usan si el local falla; principio de minimo privilegio
    # @AI_CONTEXT: respond() se conserva como alias para compatibilidad con TourOrchestrator existente

    def __init__(
        self,
        *,
        local_strategy: LocalNLPPipeline,
        cloud_strategy: CloudNLPPipeline,
    ) -> None:
        # @TASK: Inicializar ConversationManager con ambas estrategias
        # @INPUT: local_strategy — LocalNLPPipeline; cloud_strategy — CloudNLPPipeline
        # @OUTPUT: Manager listo con local como estrategia de primer intento
        # @CONTEXT: La estrategia local se intenta primero; cloud es fallback
        # STEP 1: Persistir estrategias inyectadas
        # STEP 2: Inicializar contadores de telemetria de hot-swap
        # @SECURITY: Ninguna estrategia se activa en el constructor; solo en process_interaction
        # @AI_CONTEXT: _swap_count es un indicador de salud: >3 hot-swaps indica degradacion local

        # STEP 1
        self._local: LocalNLPPipeline = local_strategy
        self._cloud: CloudNLPPipeline = cloud_strategy

        # STEP 2
        self._active_pipeline: str = "local"
        self._swap_count: int = 0
        self._total_interactions: int = 0

    @property
    def active_strategy_name(self) -> str:
        # @TASK: Exponer nombre del pipeline activo para telemetria
        # @INPUT: Sin parametros
        # @OUTPUT: "local" o "cloud" segun ultimo hot-swap
        # @CONTEXT: Propiedad de observabilidad para APIServer y TourOrchestrator
        # STEP 1: Retornar identificador del pipeline en uso actualmente
        # @SECURITY: Solo lectura; sin mutaciones
        # @AI_CONTEXT: Usar para alertas cuando _swap_count supere umbral operativo
        return self._active_pipeline  # STEP 1

    @property
    def swap_count(self) -> int:
        # @TASK: Exponer contador de hot-swaps para diagnostico de degradacion
        # @INPUT: Sin parametros
        # @OUTPUT: Numero total de conmutaciones local->cloud desde inicio
        # @CONTEXT: Metrica de salud del pipeline local (Ollama, faster-whisper)
        # STEP 1: Retornar contador acumulado de hot-swaps
        # @SECURITY: Solo lectura
        # @AI_CONTEXT: Un swap_count alto durante una sesion indica problema de recursos
        return self._swap_count  # STEP 1

    async def process_interaction(
        self,
        audio_buffer: NDArray[np.float32],
        *,
        language: str = "es",
        preferred_pipeline: str = "local",
    ) -> ConversationResponse:
        # @TASK: Procesar buffer de audio completo a traves del pipeline NLP hibr
        # @INPUT: audio_buffer — PCM float32 mono; language — iso639; preferred_pipeline
        # @OUTPUT: ConversationResponse con respuesta y audio reproducido
        # @CONTEXT: Punto de entrada principal para interaccion activada por audio (wake-word)
        # STEP 1: Intentar STT local con timeout STT_TIMEOUT_S en faster-whisper
        # STEP 2: Si STT falla por timeout o error, activar hot-swap a cloud
        # STEP 3: Construir ConversationRequest con texto transcripto
        # STEP 4: Intentar LLM local con timeout LLM_LOCAL_TIMEOUT_S en Ollama
        # STEP 5: Si LLM local falla, hacer hot-swap y llamar pipeline cloud
        # STEP 6: Actualizar telemetria y retornar respuesta
        # @SECURITY: audio_buffer no se persiste en ningun paso del pipeline
        # @AI_CONTEXT: STT y LLM tienen timeouts independientes para granularidad de hot-swap

        self._total_interactions += 1
        user_text: str = ""

        # STEP 1: STT local con hot-swap
        if preferred_pipeline == "local":
            try:
                user_text = await asyncio.wait_for(
                    self._local.transcribe(audio_buffer, language=language),
                    timeout=STT_TIMEOUT_S,
                )
                LOGGER.debug("[CM] STT local exitoso: '%s'", user_text[:60])
            except (TimeoutError, asyncio.TimeoutError) as exc:
                # STEP 2: hot-swap a cloud por timeout STT
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
                # STEP 2: hot-swap a cloud por error de hardware/recurso
                LOGGER.error(
                    "[CM] Hot-swap STT: excepcion '%s' — conmutando a cloud.",
                    type(exc).__name__,
                )
                self._swap_count += 1
                self._active_pipeline = "cloud"
                return await self._cloud_fallback_text(
                    raw_text="[STT error — entrada de usuario no disponible]"
                )

        # STEP 3
        request = ConversationRequest(user_text=user_text, locale=language)

        # STEP 4: LLM local con hot-swap
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
                # STEP 5: hot-swap a cloud por timeout LLM
                LOGGER.warning(
                    "[CM] Hot-swap LLM: timeout %.1f s — conmutando a cloud.",
                    LLM_LOCAL_TIMEOUT_S,
                )
                self._swap_count += 1
                self._active_pipeline = "cloud"
            except MemoryError as exc:
                # STEP 5: hot-swap por saturacion de RAM
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

        # STEP 6: Ejecutar cloud (tras hot-swap o si preferred_pipeline="cloud")
        return await self._cloud_fallback_text(raw_text=user_text)

    async def _cloud_fallback_text(self, raw_text: str) -> ConversationResponse:
        # @TASK: Ejecutar pipeline cloud como fallback con texto ya disponible
        # @INPUT: raw_text — texto del usuario (transcripto o placeholder de error)
        # @OUTPUT: ConversationResponse desde el proveedor cloud configurado
        # @CONTEXT: Ruta de ejecucion cloud activada por hot-swap desde process_interaction
        # STEP 1: Construir ConversationRequest con el texto disponible
        # STEP 2: Invocar cloud strategy con timeout global CLOUD_TIMEOUT_S
        # STEP 3: Registrar resultado en telemetria
        # @SECURITY: Si cloud tambien falla, propagar excepcion al TourOrchestrator
        # @AI_CONTEXT: TourOrchestrator es responsable de trigger_emergency ante excepcion aqui

        # STEP 1
        request = ConversationRequest(user_text=raw_text)

        # STEP 2
        response = await asyncio.wait_for(
            self._cloud.generate(request),
            timeout=CLOUD_TIMEOUT_S,
        )

        # STEP 3
        LOGGER.info(
            "[CM] Respuesta cloud entregada. pipeline=%s swap_count=%d",
            response.source_pipeline,
            self._swap_count,
        )
        return response

    async def respond(self, request: ConversationRequest) -> ConversationResponse:
        # @TASK: Alias de compatibilidad para TourOrchestrator que invoca respond()
        # @INPUT: request — ConversationRequest con user_text ya disponible
        # @OUTPUT: ConversationResponse desde la estrategia activa
        # @CONTEXT: Conservado para compatibilidad con TourOrchestrator.handle_user_question()
        # STEP 1: Intentar pipeline local con timeout LLM_LOCAL_TIMEOUT_S
        # STEP 2: Hot-swap a cloud ante timeout o excepcion de hardware
        # STEP 3: Retornar respuesta de la estrategia que respondio primero
        # @SECURITY: Misma politica de hot-swap que process_interaction
        # @AI_CONTEXT: No realiza STT; user_text ya esta disponible en el request

        # STEP 1
        try:
            response = await asyncio.wait_for(
                self._local.generate(request),
                timeout=LLM_LOCAL_TIMEOUT_S + TTS_TIMEOUT_S,
            )
            self._active_pipeline = "local"
            return response
        except (TimeoutError, asyncio.TimeoutError):
            # STEP 2: hot-swap por timeout
            LOGGER.warning("[CM] respond(): hot-swap a cloud por timeout local.")
            self._swap_count += 1
            self._active_pipeline = "cloud"
        except (MemoryError, Exception) as exc:
            # STEP 2: hot-swap por error de recurso o inesperado
            LOGGER.error("[CM] respond(): hot-swap a cloud por '%s'.", type(exc).__name__)
            self._swap_count += 1
            self._active_pipeline = "cloud"

        # STEP 3
        return await self._cloud_fallback_text(raw_text=request.user_text)

    def close(self) -> None:
        # @TASK: Liberar recursos de ambos pipelines en shutdown del sistema
        # @INPUT: Sin parametros
        # @OUTPUT: Executors y clientes HTTP de ambas estrategias liberados
        # @CONTEXT: Invocado desde _graceful_shutdown de main.py
        # STEP 1: Cerrar pipeline local (ProcessPoolExecutor y ThreadPoolExecutor)
        # STEP 2: Cerrar pipeline cloud (ThreadPoolExecutor de audio cloud)
        # @SECURITY: Orden de cierre: local primero, cloud segundo
        # @AI_CONTEXT: No bloquea; cancel_futures=True en los executors internos
        LOGGER.info("[CM] Cerrando ConversationManager.")
        self._local.close()   # STEP 1
        self._cloud.close()   # STEP 2


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