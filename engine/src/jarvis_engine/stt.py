"""Speech-to-text module with Groq Whisper (cloud) and faster-whisper (local) backends.

Priority: Groq Whisper Turbo (fast, accurate, free tier) -> faster-whisper (offline fallback).
Backend selected via JARVIS_STT_BACKEND env var: "groq", "local", or "auto" (default).
"""

from __future__ import annotations

import io
import json
import logging
import os
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from jarvis_engine._compat import UTC

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """Result of a speech-to-text transcription."""

    text: str = ""
    language: str = ""
    confidence: float = 0.0
    duration_seconds: float = 0.0
    backend: str = ""
    retried: bool = False
    segments: list[dict] | None = None


# ---------------------------------------------------------------------------
# STT quality metrics logging
# ---------------------------------------------------------------------------

_stt_metrics_lock = threading.Lock()

CONFIDENCE_RETRY_THRESHOLD = float(os.environ.get("JARVIS_STT_CONFIDENCE_THRESHOLD", "0.6"))
GROQ_STT_MODEL = os.environ.get("JARVIS_GROQ_STT_MODEL", "whisper-large-v3-turbo")

# Default prompt biases local Whisper toward Jarvis-specific vocabulary
JARVIS_DEFAULT_PROMPT = (
    "Jarvis is Conner's AI assistant. Common terms: Jarvis, "
    "ops brief, knowledge graph, proactive engine, Ollama, "
    "Groq, Anthropic, SQLite, Kotlin, Jetpack Compose, "
    "brain status, daily brief, self heal, daemon, safe mode."
)


def _log_stt_metric(
    root_dir: Path | None,
    *,
    backend: str,
    confidence: float,
    latency_ms: float,
    text_length: int,
    retried: bool = False,
) -> None:
    """Log STT quality metric for tracking improvement over time."""
    if root_dir is None:
        return
    metrics_path = root_dir / ".planning" / "runtime" / "stt_metrics.jsonl"
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "backend": backend,
        "confidence": round(confidence, 3),
        "latency_ms": round(latency_ms, 1),
        "text_length": text_length,
        "retried": retried,
    }
    try:
        with _stt_metrics_lock:
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            with open(metrics_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    except OSError:
        pass


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
) -> TranscriptionResult | None:
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

    # Minimum audio duration check: require at least 0.1s (1600 samples at 16kHz)
    if isinstance(audio, np.ndarray) and len(audio) < 1600:
        logger.debug(
            "Audio too short for Groq API (%d samples)", len(audio)
        )
        return None

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

    # Call Groq Whisper API (OpenAI-compatible) with retry on transient errors
    resp = None
    with httpx.Client(timeout=30.0) as client:
        for attempt in range(2):
            try:
                resp = client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={
                        "model": GROQ_STT_MODEL,
                        "language": language,
                        "response_format": "verbose_json",
                        "temperature": "0.0",
                        "prompt": prompt[:224],  # Groq limit: 224 tokens
                    },
                    files={"file": (filename, audio_bytes, "audio/wav")},
                )
                if resp.status_code >= 500:
                    logger.warning("Groq API returned %d, attempt %d/2", resp.status_code, attempt + 1)
                    if attempt < 1:
                        time.sleep(1)
                        continue
                break
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                logger.warning("Groq API connection error: %s, attempt %d/2", exc, attempt + 1)
                if attempt < 1:
                    time.sleep(1)
                    continue
                return TranscriptionResult(
                    text="",
                    language=language,
                    confidence=0.0,
                    duration_seconds=round(time.monotonic() - t0, 3),
                    backend="groq-whisper",
                )

    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp is not None else 0
        text = resp.text[:200] if resp is not None else "no response"
        raise RuntimeError(f"Groq STT API error {status}: {text}")

    data = resp.json()
    elapsed = time.monotonic() - t0
    text = data.get("text", "").strip()
    detected_lang = data.get("language", language)

    # Compute real confidence from segment-level avg_logprob
    raw_segments = data.get("segments", [])
    parsed_segments: list[dict] | None = None
    if raw_segments and isinstance(raw_segments, list):
        import math

        logprobs = []
        parsed_segments = []
        for seg in raw_segments:
            if isinstance(seg, dict):
                alp = seg.get("avg_logprob")
                if isinstance(alp, (int, float)) and math.isfinite(alp):
                    logprobs.append(alp)
                # Extract timing for segment-level timestamps
                seg_start = seg.get("start")
                seg_end = seg.get("end")
                seg_text = seg.get("text", "")
                if isinstance(seg_start, (int, float)) and isinstance(seg_end, (int, float)):
                    parsed_segments.append({
                        "start": float(seg_start),
                        "end": float(seg_end),
                        "text": str(seg_text).strip(),
                    })

        if logprobs:
            avg_logprob = sum(logprobs) / len(logprobs)
            confidence = round(min(1.0, max(0.0, 1.0 + avg_logprob)), 4)
        else:
            confidence = 0.90  # fallback if segments lack logprobs
    else:
        confidence = 0.90  # fallback if no segments returned

    return TranscriptionResult(
        text=text,
        language=detected_lang,
        confidence=confidence,
        duration_seconds=round(elapsed, 3),
        backend="groq-whisper",
        segments=parsed_segments if parsed_segments else None,
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
        prompt: str = "",
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
        prompt:
            Optional initial prompt to bias recognition toward expected vocabulary.

        Returns
        -------
        TranscriptionResult
        """
        self._ensure_model()
        t0 = time.monotonic()
        initial_prompt = prompt or JARVIS_DEFAULT_PROMPT
        segments_gen, info = self._model.transcribe(
            audio,
            language=language,
            vad_filter=vad_filter,
            initial_prompt=initial_prompt,
            beam_size=5,
            condition_on_previous_text=False,
            no_repeat_ngram_size=3,
            hallucination_silence_threshold=0.2,
            word_timestamps=True,
            vad_parameters=dict(
                threshold=0.5,
                min_silence_duration_ms=500,
                speech_pad_ms=200,
                min_speech_duration_ms=250,
            ),
        )
        segments = list(segments_gen)
        texts: list[str] = []
        parsed_segments: list[dict] = []
        for segment in segments:
            texts.append(segment.text.strip())
            seg_start = getattr(segment, "start", None)
            seg_end = getattr(segment, "end", None)
            if seg_start is not None and seg_end is not None:
                parsed_segments.append({
                    "start": float(seg_start),
                    "end": float(seg_end),
                    "text": segment.text.strip(),
                })
        elapsed = time.monotonic() - t0
        full_text = " ".join(texts).strip()
        # Compute confidence from segment avg_logprob (not language_probability which is always ~1.0 for English)
        logprobs = [seg.avg_logprob for seg in segments if hasattr(seg, 'avg_logprob')]
        if logprobs:
            avg_logprob = sum(logprobs) / len(logprobs)
            confidence = min(1.0, max(0.0, 1.0 + avg_logprob))
        else:
            confidence = getattr(info, "language_probability", 0.0)
        detected_lang = getattr(info, "language", language)
        return TranscriptionResult(
            text=full_text,
            language=detected_lang,
            confidence=confidence,
            duration_seconds=round(elapsed, 3),
            backend="faster-whisper",
            segments=parsed_segments if parsed_segments else None,
        )


# ---------------------------------------------------------------------------
# Smart transcription (auto-selects best available backend)
# ---------------------------------------------------------------------------

def _try_groq(
    audio: np.ndarray | str, *, language: str, prompt: str
) -> TranscriptionResult | None:
    """Attempt Groq transcription, returning *None* on failure."""
    try:
        return transcribe_groq(audio, language=language, prompt=prompt)
    except Exception as exc:
        logger.warning("Groq STT attempt failed: %s", exc)
        return None


_local_stt_instance: SpeechToText | None = None


def _try_local(
    audio: np.ndarray | str, *, language: str
) -> TranscriptionResult | None:
    """Attempt local faster-whisper transcription, returning *None* on failure."""
    global _local_stt_instance
    try:
        if _local_stt_instance is None:
            _local_stt_instance = SpeechToText()
        return _local_stt_instance.transcribe_audio(audio, language=language)
    except Exception as exc:
        logger.warning("Local STT attempt failed: %s", exc)
        return None


def _confidence_retry(
    primary: TranscriptionResult,
    audio: np.ndarray | str,
    *,
    language: str,
    prompt: str,
    root_dir: Path | None,
) -> TranscriptionResult:
    """If *primary* confidence is below threshold, try the alternative backend.

    Returns whichever result has higher confidence.  If the retry fails
    or produces lower confidence, the original result is returned unchanged.
    At most ONE retry is attempted.
    """
    if primary.confidence >= CONFIDENCE_RETRY_THRESHOLD:
        return primary

    has_groq = bool(os.environ.get("GROQ_API_KEY", ""))

    # Determine alternative backend
    if primary.backend == "groq-whisper":
        retry_result = _try_local(audio, language=language)
    elif primary.backend == "faster-whisper" and has_groq:
        retry_result = _try_groq(audio, language=language, prompt=prompt)
    else:
        # No alternative available
        return primary

    if retry_result is None:
        logger.info(
            "Confidence retry failed; keeping original (%.3f)",
            primary.confidence,
        )
        return primary

    # Log metrics for retry attempt
    _log_stt_metric(
        root_dir,
        backend=retry_result.backend,
        confidence=retry_result.confidence,
        latency_ms=retry_result.duration_seconds * 1000,
        text_length=len(retry_result.text),
        retried=True,
    )

    if retry_result.confidence > primary.confidence:
        logger.info(
            "Confidence retry improved: %.3f (%s) -> %.3f (%s)",
            primary.confidence,
            primary.backend,
            retry_result.confidence,
            retry_result.backend,
        )
        retry_result.retried = True
        return retry_result

    logger.info(
        "Confidence retry did not improve: %.3f (%s) vs %.3f (%s); keeping original",
        primary.confidence,
        primary.backend,
        retry_result.confidence,
        retry_result.backend,
    )
    primary.retried = True  # Mark that a retry was attempted
    return primary


def transcribe_smart(
    audio: np.ndarray | str,
    *,
    language: str = "en",
    prompt: str = "",
    root_dir: Path | None = None,
    gateway: object | None = None,
    entity_list: list[str] | None = None,
) -> TranscriptionResult:
    """Transcribe using the best available backend.

    Priority order based on JARVIS_STT_BACKEND env var:
    - "groq": Force Groq Whisper (fail if unavailable)
    - "local": Force local faster-whisper (fail if unavailable)
    - "auto" (default): Try Groq first, fall back to local

    When *root_dir* is provided, quality metrics are logged to
    ``<root_dir>/.planning/runtime/stt_metrics.jsonl``.

    If the primary transcription confidence is below
    ``CONFIDENCE_RETRY_THRESHOLD`` (0.6), an automatic retry using the
    alternative backend is attempted.  The result with higher confidence
    is returned.

    When *gateway* and/or *entity_list* are provided, post-processing
    (filler removal, LLM correction, NER entity correction) is applied
    to the final transcription text.
    """
    backend = os.environ.get("JARVIS_STT_BACKEND", "auto").lower()

    # Audio preprocessing (only for numpy arrays, not file paths)
    if isinstance(audio, np.ndarray) and len(audio) > 0:
        try:
            from jarvis_engine.stt_postprocess import preprocess_audio
            audio = preprocess_audio(audio)
            if len(audio) == 0:
                logger.info("Audio was pure silence after preprocessing")
                return TranscriptionResult(
                    text="",
                    language=language,
                    confidence=0.0,
                    duration_seconds=0.0,
                    backend="preprocessed-silence",
                )
        except Exception as exc:
            logger.warning("Audio preprocessing failed, using raw audio: %s", exc)

    final: TranscriptionResult | None = None

    if backend == "groq":
        result = transcribe_groq(audio, language=language, prompt=prompt)
        _log_stt_metric(
            root_dir,
            backend=result.backend,
            confidence=result.confidence,
            latency_ms=result.duration_seconds * 1000,
            text_length=len(result.text),
        )
        final = _confidence_retry(
            result, audio, language=language, prompt=prompt, root_dir=root_dir,
        )

    elif backend == "local":
        stt = SpeechToText()
        result = stt.transcribe_audio(audio, language=language)
        _log_stt_metric(
            root_dir,
            backend=result.backend,
            confidence=result.confidence,
            latency_ms=result.duration_seconds * 1000,
            text_length=len(result.text),
        )
        final = _confidence_retry(
            result, audio, language=language, prompt=prompt, root_dir=root_dir,
        )

    else:
        # Auto mode: try Groq first, fall back to local
        result: TranscriptionResult | None = None
        if os.environ.get("GROQ_API_KEY", ""):
            result = _try_groq(audio, language=language, prompt=prompt)
            if result is not None:
                logger.info("Groq STT: '%s' in %.2fs", result.text[:60], result.duration_seconds)

        if result is None:
            result = _try_local(audio, language=language)
            if result is not None:
                logger.info("Local STT: '%s' in %.2fs", result.text[:60], result.duration_seconds)

        if result is None:
            logger.error("All STT backends failed")
            return TranscriptionResult(
                text="",
                language=language,
                confidence=0.0,
                duration_seconds=0.0,
                backend="none",
            )

        _log_stt_metric(
            root_dir,
            backend=result.backend,
            confidence=result.confidence,
            latency_ms=result.duration_seconds * 1000,
            text_length=len(result.text),
        )

        final = _confidence_retry(
            result, audio, language=language, prompt=prompt, root_dir=root_dir,
        )

    # Post-process transcription text
    if final.text.strip():
        try:
            from jarvis_engine.stt_postprocess import postprocess_transcription
            processed = postprocess_transcription(
                final.text,
                final.confidence,
                gateway=gateway,
                entity_list=entity_list,
            )
            final = TranscriptionResult(
                text=processed,
                language=final.language,
                confidence=final.confidence,
                duration_seconds=final.duration_seconds,
                backend=final.backend,
                segments=final.segments,
                retried=final.retried,
            )
        except Exception as exc:
            logger.warning("Post-processing failed, using raw text: %s", exc)

    return final


# ---------------------------------------------------------------------------
# Microphone recording
# ---------------------------------------------------------------------------

def record_from_microphone(
    *,
    sample_rate: int = 16000,
    max_duration_seconds: float = 30.0,
    silence_threshold: float = 0.01,
    silence_duration: float = 2.0,
) -> np.ndarray:
    """Record audio from the default microphone with energy-based VAD.

    Recording stops early when silence is detected after speech, avoiding
    the need to wait for the full *max_duration_seconds*.

    Parameters
    ----------
    sample_rate:
        Audio sample rate in Hz (default 16000).
    max_duration_seconds:
        Maximum recording duration in seconds (default 30).
    silence_threshold:
        RMS energy threshold below which audio is considered silence (default 0.01).
    silence_duration:
        Seconds of continuous silence after speech before stopping (default 2.0).

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

        frames: list[np.ndarray] = []
        speech_detected = False
        silence_frames = 0
        chunk_duration = 0.1  # 100ms chunks
        samples_per_chunk = int(sample_rate * chunk_duration)
        max_silence_chunks = int(silence_duration / chunk_duration)
        min_recording_chunks = int(0.5 / chunk_duration)  # At least 0.5s

        with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32") as stream:
            max_chunks = int(max_duration_seconds / chunk_duration)
            for i in range(max_chunks):
                chunk, _ = stream.read(samples_per_chunk)
                frames.append(chunk.copy())

                rms = float(np.sqrt(np.mean(chunk ** 2)))
                if rms > silence_threshold:
                    speech_detected = True
                    silence_frames = 0
                elif speech_detected:
                    silence_frames += 1
                    if silence_frames >= max_silence_chunks and i >= min_recording_chunks:
                        logger.debug(
                            "Silence detected after speech, stopping recording "
                            "(%.1f seconds recorded)",
                            (i + 1) * chunk_duration,
                        )
                        break

    except Exception as exc:
        # PortAudioError or similar -- microphone not available
        raise RuntimeError(
            f"Microphone recording failed: {exc}. "
            "Check Windows microphone permissions in Settings > Privacy > Microphone."
        ) from exc

    if not frames:
        return np.array([], dtype=np.float32)
    return np.concatenate(frames, axis=0).flatten()


def listen_and_transcribe(
    *,
    max_duration_seconds: float = 30.0,
    language: str = "en",
    root_dir: Path | None = None,
    gateway: object | None = None,
    entity_list: list[str] | None = None,
) -> TranscriptionResult:
    """Record from microphone and transcribe in one call.

    Uses smart backend selection: Groq Whisper if GROQ_API_KEY is set,
    otherwise falls back to local faster-whisper.
    """
    audio = record_from_microphone(max_duration_seconds=max_duration_seconds)
    return transcribe_smart(
        audio, language=language, root_dir=root_dir,
        gateway=gateway, entity_list=entity_list,
    )
