"""Speech-to-text module with Groq Whisper (cloud) and faster-whisper (local) backends.

Priority: Groq Whisper Turbo (fast, accurate, free tier) -> faster-whisper (offline fallback).
Backend selected via JARVIS_STT_BACKEND env var: "groq", "local", or "auto" (default).
"""

from __future__ import annotations

import io
import logging
import os
import struct
import tempfile
import time
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """Result of a speech-to-text transcription."""

    text: str = ""
    language: str = ""
    confidence: float = 0.0
    duration_seconds: float = 0.0
    backend: str = ""


# ---------------------------------------------------------------------------
# Groq Whisper STT (cloud)
# ---------------------------------------------------------------------------

def _numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert a mono float32 numpy array to WAV bytes for API upload."""
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    # Write WAV header
    num_samples = len(audio_int16)
    data_size = num_samples * 2  # 16-bit = 2 bytes per sample
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))  # chunk size
    buf.write(struct.pack("<H", 1))   # PCM format
    buf.write(struct.pack("<H", 1))   # mono
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * 2))  # byte rate
    buf.write(struct.pack("<H", 2))   # block align
    buf.write(struct.pack("<H", 16))  # bits per sample
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(audio_int16.tobytes())
    return buf.getvalue()


def transcribe_groq(
    audio: np.ndarray | str,
    *,
    language: str = "en",
    prompt: str = "",
) -> TranscriptionResult:
    """Transcribe audio using Groq's Whisper Turbo API.

    Parameters
    ----------
    audio:
        Either a mono float32 numpy array (16 kHz) or a path to an audio file.
    language:
        Language code hint.
    prompt:
        Optional prompt to bias recognition toward expected vocabulary.
    """
    import httpx

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    t0 = time.monotonic()

    # Prepare audio file
    if isinstance(audio, str):
        with open(audio, "rb") as f:
            audio_bytes = f.read()
        filename = os.path.basename(audio)
    else:
        audio_bytes = _numpy_to_wav_bytes(audio)
        filename = "recording.wav"

    # Default prompt biases recognition toward Jarvis commands
    if not prompt:
        prompt = (
            "Jarvis, set a timer, add a task, check my schedule, "
            "brain status, ops brief, daily brief, self heal, "
            "pause daemon, resume daemon, safe mode"
        )

    # Call Groq Whisper API (OpenAI-compatible)
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": "whisper-large-v3-turbo",
                "language": language,
                "response_format": "verbose_json",
                "temperature": "0.0",
                "prompt": prompt[:224],  # Groq limit: 224 tokens
            },
            files={"file": (filename, audio_bytes, "audio/wav")},
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Groq STT API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    elapsed = time.monotonic() - t0
    text = data.get("text", "").strip()
    detected_lang = data.get("language", language)

    return TranscriptionResult(
        text=text,
        language=detected_lang,
        confidence=0.95,  # Whisper large-v3 is consistently high accuracy
        duration_seconds=round(elapsed, 3),
        backend="groq-whisper",
    )


# ---------------------------------------------------------------------------
# Local faster-whisper STT (offline fallback)
# ---------------------------------------------------------------------------

class SpeechToText:
    """Whisper-grade speech-to-text with lazy model loading.

    The faster-whisper library is only imported when the model is first needed,
    so merely importing this module has zero heavyweight dependencies.
    """

    def __init__(
        self,
        model_size: str = "small.en",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        env_model = os.environ.get("JARVIS_STT_MODEL")
        self.model_size: str = env_model if env_model else model_size
        self.device: str = device
        self.compute_type: str = compute_type
        self._model = None

    def _ensure_model(self) -> None:
        """Lazy-load the WhisperModel on first use."""
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Install with: pip install faster-whisper"
            ) from exc
        logger.info(
            "Loading Whisper model %s on %s (%s)...",
            self.model_size,
            self.device,
            self.compute_type,
        )
        self._model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )

    def transcribe_audio(
        self,
        audio: np.ndarray | str,
        *,
        language: str = "en",
        vad_filter: bool = True,
    ) -> TranscriptionResult:
        """Transcribe audio from a numpy array or file path.

        Parameters
        ----------
        audio:
            Either a numpy float32 array (mono, 16 kHz) or a path to an audio
            file supported by faster-whisper.
        language:
            Language code hint (default ``"en"``).
        vad_filter:
            Enable Voice Activity Detection filtering (default ``True``).

        Returns
        -------
        TranscriptionResult
        """
        self._ensure_model()
        t0 = time.monotonic()
        segments, info = self._model.transcribe(
            audio,
            language=language,
            vad_filter=vad_filter,
        )
        texts: list[str] = []
        for segment in segments:
            texts.append(segment.text.strip())
        elapsed = time.monotonic() - t0
        full_text = " ".join(texts).strip()
        confidence = getattr(info, "language_probability", 0.0)
        detected_lang = getattr(info, "language", language)
        return TranscriptionResult(
            text=full_text,
            language=detected_lang,
            confidence=confidence,
            duration_seconds=round(elapsed, 3),
            backend="faster-whisper",
        )


# ---------------------------------------------------------------------------
# Smart transcription (auto-selects best available backend)
# ---------------------------------------------------------------------------

def transcribe_smart(
    audio: np.ndarray | str,
    *,
    language: str = "en",
    prompt: str = "",
) -> TranscriptionResult:
    """Transcribe using the best available backend.

    Priority order based on JARVIS_STT_BACKEND env var:
    - "groq": Force Groq Whisper (fail if unavailable)
    - "local": Force local faster-whisper (fail if unavailable)
    - "auto" (default): Try Groq first, fall back to local
    """
    backend = os.environ.get("JARVIS_STT_BACKEND", "auto").lower()

    if backend == "groq":
        return transcribe_groq(audio, language=language, prompt=prompt)

    if backend == "local":
        stt = SpeechToText()
        return stt.transcribe_audio(audio, language=language)

    # Auto mode: try Groq first, fall back to local
    if os.environ.get("GROQ_API_KEY", ""):
        try:
            result = transcribe_groq(audio, language=language, prompt=prompt)
            logger.info("Groq STT: '%s' in %.2fs", result.text[:60], result.duration_seconds)
            return result
        except Exception as exc:
            logger.warning("Groq STT failed, falling back to local: %s", exc)

    # Fall back to local faster-whisper
    try:
        stt = SpeechToText()
        result = stt.transcribe_audio(audio, language=language)
        logger.info("Local STT: '%s' in %.2fs", result.text[:60], result.duration_seconds)
        return result
    except Exception as exc:
        logger.error("All STT backends failed: %s", exc)
        return TranscriptionResult(
            text="",
            language=language,
            confidence=0.0,
            duration_seconds=0.0,
            backend="none",
        )


# ---------------------------------------------------------------------------
# Microphone recording
# ---------------------------------------------------------------------------

def record_from_microphone(
    *,
    sample_rate: int = 16000,
    max_duration_seconds: float = 30.0,
) -> np.ndarray:
    """Record audio from the default microphone.

    Returns a mono float32 numpy array at the given sample rate.
    Raises RuntimeError if sounddevice is not installed or no microphone
    is available.
    """
    try:
        import sounddevice as sd  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is not installed. "
            "Install with: pip install sounddevice"
        ) from exc
    try:
        logger.info(
            "Recording from microphone for up to %.1f seconds at %d Hz...",
            max_duration_seconds,
            sample_rate,
        )
        audio = sd.rec(
            int(sample_rate * max_duration_seconds),
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
        )
        sd.wait()
    except Exception as exc:
        # PortAudioError or similar -- microphone not available
        raise RuntimeError(
            f"Microphone recording failed: {exc}. "
            "Check Windows microphone permissions in Settings > Privacy > Microphone."
        ) from exc
    return audio.flatten()


def listen_and_transcribe(
    *,
    max_duration_seconds: float = 30.0,
    language: str = "en",
    model_size: str = "small.en",
) -> TranscriptionResult:
    """Record from microphone and transcribe in one call.

    Uses smart backend selection: Groq Whisper if GROQ_API_KEY is set,
    otherwise falls back to local faster-whisper.
    """
    audio = record_from_microphone(max_duration_seconds=max_duration_seconds)
    return transcribe_smart(audio, language=language)
