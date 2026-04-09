from __future__ import annotations

import argparse
import asyncio
import os
import shlex
from dataclasses import dataclass
from typing import Sequence

import sounddevice as sd


TEST_MESSAGE: str = (
	"Prueba acustica de seguridad del Unitree G1 EDU. "
	"Validacion de potencia del altavoz estereo en volumen maximo ALSA."
)


@dataclass(slots=True)
class LocalTTSComponent:
	# @TASK: Modelar componente TTS local basado en Piper CLI
	# @INPUT: piper_cmd, model_path, sample_rate
	# @OUTPUT: Instancia capaz de sintetizar PCM16 mono
	# @CONTEXT: Script aislado para validacion acustica en entorno fisico
	# STEP 1: Persistir binario, modelo ONNX y sample rate operativo
	# STEP 2: Exponer metodo async synthesize_raw_pcm16
	# @SECURITY: No usa red ni servicios cloud para la prueba
	# @AI_CONTEXT: Compatible con despliegues air-gapped en companion PC
	piper_cmd: str
	model_path: str
	sample_rate: int

	async def synthesize_raw_pcm16(self, text: str) -> bytes:
		# @TASK: Sintetizar texto en bytes PCM16
		# @INPUT: text
		# @OUTPUT: Flujo raw PCM16 mono
		# @CONTEXT: Conversor TTS local asincrono usando proceso Piper
		# STEP 1: Lanzar piper con salida raw hacia stdout
		# STEP 2: Enviar texto por stdin y retornar bytes sintetizados
		# @SECURITY: El proceso se ejecuta con shell=False para evitar inyecciones
		# @AI_CONTEXT: Formato de salida compatible con sounddevice.RawOutputStream
		process = await asyncio.create_subprocess_exec(
			self.piper_cmd,
			"--model",
			self.model_path,
			"--output-raw",
			stdin=asyncio.subprocess.PIPE,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)

		stdout_data, stderr_data = await process.communicate(text.encode("utf-8"))
		if process.returncode != 0:
			stderr_text = stderr_data.decode("utf-8", errors="replace")
			raise RuntimeError(
				"Piper finalizo con error al sintetizar audio: "
				f"code={process.returncode}, stderr={stderr_text}"
			)

		if len(stdout_data) == 0:
			raise RuntimeError("Piper no devolvio audio en formato raw PCM16.")

		return stdout_data


async def set_alsa_max_volume() -> None:
	# @TASK: Forzar volumen maximo de ALSA
	# @INPUT: Ninguno
	# @OUTPUT: Controles Master/PCM ajustados a 100% cuando existan
	# @CONTEXT: Prueba de potencia de altavoz requiere volumen maximo
	# STEP 1: Intentar comandos amixer para canales comunes
	# STEP 2: Fallar si no se pudo aplicar ningun ajuste de volumen
	# @SECURITY: Comandos ejecutados sin shell para reducir superficie de riesgo
	# @AI_CONTEXT: Puede requerir permisos de audio del usuario actual
	commands: Sequence[Sequence[str]] = (
		("amixer", "sset", "Master", "100%"),
		("amixer", "sset", "PCM", "100%"),
	)

	applied_any = False
	for command in commands:
		process = await asyncio.create_subprocess_exec(
			*command,
			stdin=asyncio.subprocess.DEVNULL,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		_, _ = await process.communicate()
		if process.returncode == 0:
			applied_any = True

	if not applied_any:
		rendered_commands = " | ".join(" ".join(shlex.quote(x) for x in cmd) for cmd in commands)
		raise RuntimeError(
			"No fue posible establecer volumen maximo ALSA. "
			f"Comandos intentados: {rendered_commands}"
		)


def play_pcm16_mono_blocking(raw_pcm16: bytes, sample_rate: int) -> None:
	# @TASK: Reproducir bytes PCM16 en salida local
	# @INPUT: raw_pcm16, sample_rate
	# @OUTPUT: Audio audible por altavoz fisico del robot
	# @CONTEXT: Etapa final de validacion acustica de potencia
	# STEP 1: Abrir stream raw mono int16 con sample rate del modelo
	# STEP 2: Escribir buffer completo y esperar finalizacion
	# @SECURITY: No persiste WAV en disco; buffer solo en memoria
	# @AI_CONTEXT: El dispositivo por defecto debe mapear a salida ALSA
	with sd.RawOutputStream(
		samplerate=sample_rate,
		channels=1,
		dtype="int16",
		latency="high",
	) as stream:
		stream.write(raw_pcm16)


def build_parser() -> argparse.ArgumentParser:
	# @TASK: Construir parser CLI
	# @INPUT: Ninguno
	# @OUTPUT: Parser con opciones de modelo, piper y sample rate
	# @CONTEXT: Parametrizacion del script para distintos despliegues
	# STEP 1: Leer defaults desde variables de entorno
	# STEP 2: Exponer argumentos para override local
	# @SECURITY: Requiere model_path explicito para evitar ejecuciones ambiguas
	# @AI_CONTEXT: Facilita uso en companion PC o host de desarrollo
	parser = argparse.ArgumentParser(description="Validacion acustica TTS local para Unitree G1 EDU.")
	parser.add_argument(
		"--model-path",
		default=os.environ.get("PIPER_MODEL_PATH", ""),
		help="Ruta al modelo ONNX de Piper (o usar PIPER_MODEL_PATH).",
	)
	parser.add_argument(
		"--piper-cmd",
		default=os.environ.get("PIPER_CMD", "piper"),
		help="Comando ejecutable de Piper.",
	)
	parser.add_argument(
		"--sample-rate",
		type=int,
		default=int(os.environ.get("PIPER_SAMPLE_RATE", "22050")),
		help="Sample rate del modelo Piper en Hz.",
	)
	parser.add_argument(
		"--text",
		default=TEST_MESSAGE,
		help="Texto de prueba a sintetizar.",
	)
	return parser


async def run_audio_validation(args: argparse.Namespace) -> int:
	# @TASK: Ejecutar flujo completo de validacion acustica
	# @INPUT: Namespace CLI
	# @OUTPUT: Codigo de salida estilo shell (0 ok, 1 error)
	# @CONTEXT: Validacion aislada de TTS local + potencia de altavoz
	# STEP 1: Verificar modelo piper y establecer volumen ALSA maximo
	# STEP 2: Sintetizar texto y reproducirlo por salida local
	# @SECURITY: Aborta temprano ante configuracion incompleta
	# @AI_CONTEXT: Rutina preparada para pruebas presenciales HIL
	if args.model_path == "":
		print("[FATAL] Falta --model-path o variable PIPER_MODEL_PATH.")
		return 1

	tts = LocalTTSComponent(
		piper_cmd=args.piper_cmd,
		model_path=args.model_path,
		sample_rate=args.sample_rate,
	)

	try:
		await set_alsa_max_volume()
		raw_pcm16 = await tts.synthesize_raw_pcm16(args.text)
		loop = asyncio.get_running_loop()
		await loop.run_in_executor(None, play_pcm16_mono_blocking, raw_pcm16, args.sample_rate)
		print("[OK] Prueba acustica completada. Verifica inteligibilidad y potencia en entorno fisico.")
		return 0
	except Exception as exc:
		print(f"[ERROR] Prueba acustica fallida: {exc}")
		return 1


def main() -> int:
	# @TASK: Entry point sincronico
	# @INPUT: Argumentos CLI
	# @OUTPUT: Codigo de retorno de run_audio_validation
	# @CONTEXT: Punto de arranque ejecutable del script
	# STEP 1: Parsear argumentos de usuario
	# STEP 2: Ejecutar rutina async con asyncio.run
	# @SECURITY: Encapsula control de excepciones dentro de la corrida async
	# @AI_CONTEXT: Compatible con invocacion directa python scripts/test_audio.py
	parser = build_parser()
	args = parser.parse_args()
	return asyncio.run(run_audio_validation(args))


if __name__ == "__main__":
	raise SystemExit(main())
