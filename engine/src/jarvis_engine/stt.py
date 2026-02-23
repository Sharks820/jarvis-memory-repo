"""Speech-to-text module using faster-whisper with lazy model loading."""

from __future__ import annotations

import logging
import os
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
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Install with: pip install faster-whisper"
            )
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
        )


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
    except ImportError:
        raise RuntimeError(
            "sounddevice is not installed. "
            "Install with: pip install sounddevice"
        )
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

    This is the main convenience entry point for voice command capture.
    """
    audio = record_from_microphone(max_duration_seconds=max_duration_seconds)
    stt = SpeechToText(model_size=model_size)
    return stt.transcribe_audio(audio, language=language)
