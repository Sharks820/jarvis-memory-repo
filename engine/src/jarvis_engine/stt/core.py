"""Speech-to-text module with 4-tier fallback chain.

Backends (in auto-mode priority order):
  1. Parakeet TDT 0.6B (local, 6.05% WER)
  2. Deepgram Nova-3 (cloud, keyterm boosting)
  3. Groq Whisper Turbo (cloud, free tier)
  4. faster-whisper large-v3 (local, emergency fallback)

Backend selected via JARVIS_STT_BACKEND env var:
  "parakeet", "deepgram", "groq", "local", or "auto" (default).

Individual backend implementations live in :mod:`stt_backends`.  This
module re-exports them so existing ``from jarvis_engine.stt import ...``
paths continue to work.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol, cast
from pathlib import Path

import numpy as np

from jarvis_engine._shared import now_iso
from jarvis_engine.stt.contracts import TranscriptionResult, TranscriptionSegment
from jarvis_engine.stt.backends import (  # noqa: F401 -- re-exports
    _load_keyterms,
    _numpy_to_wav_bytes,
    _try_deepgram,
    record_from_microphone,
)

logger = logging.getLogger(__name__)

# API endpoints
_GROQ_STT_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Audio thresholds
_MIN_AUDIO_SAMPLES_16KHZ = 1600  # 0.1s at 16 kHz
_GROQ_PROMPT_MAX_CHARS = 896  # ~224 tokens * 4 chars/token
_GROQ_MAX_API_RETRIES = 2
_GROQ_RATE_LIMIT_BACKOFF_S = 2
_GROQ_ERROR_BACKOFF_S = 1

# Confidence scoring
_FALLBACK_CONFIDENCE = 0.50  # when segments lack logprobs
_NO_SPEECH_PENALTY_THRESHOLD = 0.5  # penalize confidence above this
_PARAKEET_BASELINE_CONFIDENCE = 0.75  # above CONFIDENCE_RETRY_THRESHOLD (0.72) so logprob-less results are accepted

# STT prompt limits
_STT_PROMPT_MAX_CHARS = 1200
_STT_MAX_ENTITY_HINTS = 40


class _HTTPJsonResponse(Protocol):
    status_code: int
    text: str

    def json(self) -> dict[str, Any]: ...


# STT quality metrics logging

_stt_metrics_lock = threading.Lock()

CONFIDENCE_RETRY_THRESHOLD = float(
    os.environ.get("JARVIS_STT_CONFIDENCE_THRESHOLD", "0.72")
)

# STT-11: Low-confidence confirmation threshold.  When the final
# transcription confidence falls below this value the result is flagged
# with ``needs_confirmation=True`` so the voice pipeline can prompt the
# user for verification instead of silently executing a wrong command.
CONFIDENCE_CONFIRMATION_THRESHOLD = float(
    os.environ.get("JARVIS_STT_CONFIRMATION_THRESHOLD", "0.6")
)
GROQ_STT_MODEL = os.environ.get("JARVIS_GROQ_STT_MODEL", "whisper-large-v3-turbo")

# Default prompt biases local Whisper toward Jarvis-specific vocabulary
JARVIS_DEFAULT_PROMPT = (
    "Jarvis is Conner's AI assistant. Common terms: Jarvis, "
    "ops brief, knowledge graph, proactive engine, Ollama, "
    "Groq, Anthropic, SQLite, Kotlin, Jetpack Compose, "
    "brain status, daily brief, self heal, daemon, safe mode."
)

_DEFAULT_STT_ENTITY_TERMS: tuple[str, ...] = (
    "Jarvis",
    "Conner",
    "ops brief",
    "brain status",
    "daily brief",
    "self heal",
    "safe mode",
    "knowledge graph",
    "proactive engine",
    "Ollama",
    "Groq",
    "Anthropic",
    "SQLite",
    "Kotlin",
    "Jetpack Compose",
    "daemon",
)


def _build_default_entity_list(
    entity_list: list[str] | None,
) -> list[str]:
    """Return a deduplicated entity list biased toward Jarvis-specific terms."""
    if entity_list:
        explicit_entities: list[str] = []
        explicit_seen: set[str] = set()
        for value in entity_list:
            cleaned = str(value).strip()
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in explicit_seen:
                continue
            explicit_seen.add(lowered)
            explicit_entities.append(cleaned)
        return explicit_entities

    merged: list[str] = []
    seen: set[str] = set()
    for value in [*(_load_keyterms() or []), *_DEFAULT_STT_ENTITY_TERMS]:
        cleaned = str(value).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(cleaned)
    return merged


def _build_stt_prompt(prompt: str, entity_list: list[str]) -> str:
    """Return the backend prompt enriched with Jarvis-specific vocabulary hints."""
    base_prompt = prompt.strip() or JARVIS_DEFAULT_PROMPT
    if not entity_list:
        return base_prompt
    hint_terms = ", ".join(entity_list[:_STT_MAX_ENTITY_HINTS])
    if not hint_terms:
        return base_prompt
    return (
        f"{base_prompt} "
        f"Recognize names and phrases exactly when spoken: {hint_terms}."
    )[:_STT_PROMPT_MAX_CHARS]


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
    from jarvis_engine._shared import runtime_dir

    metrics_path = runtime_dir(root_dir) / "stt_metrics.jsonl"
    record = {
        "ts": now_iso(),
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
    except OSError as exc:
        logger.debug("Failed to write STT metric: %s", exc)


# Groq Whisper STT (cloud) — helpers


def _groq_prepare_audio(
    audio: np.ndarray | str,
) -> tuple[bytes, str]:
    """Convert audio input to bytes and determine filename.

    Returns (audio_bytes, filename).
    """
    if isinstance(audio, str):
        with open(audio, "rb") as f:
            audio_bytes = f.read()
        filename = os.path.basename(audio)
    else:
        audio_bytes = _numpy_to_wav_bytes(audio)
        filename = "recording.wav"
    return audio_bytes, filename


def _groq_default_prompt(prompt: str) -> str:
    """Return the prompt to send to Groq, using a default if empty."""
    if prompt:
        return prompt
    return (
        "Jarvis, what's on my schedule today? "
        "Jarvis, run the ops brief. "
        "Jarvis, check brain status. "
        "Jarvis, add a task. Jarvis, self heal."
    )


def _groq_api_call(
    api_key: str,
    audio_bytes: bytes,
    filename: str,
    language: str,
    prompt: str,
    t0: float,
) -> _HTTPJsonResponse | TranscriptionResult:
    """Send the audio to Groq Whisper API with retry on transient errors.

    Returns the httpx Response on success.
    Raises RuntimeError on non-200 final status.
    Returns a TranscriptionResult with empty text on transport failure.
    """
    import httpx

    resp = None
    with httpx.Client(timeout=30.0) as client:
        for attempt in range(_GROQ_MAX_API_RETRIES):
            try:
                resp = client.post(
                    _GROQ_STT_API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={
                        "model": GROQ_STT_MODEL,
                        "language": language,
                        "response_format": "verbose_json",
                        "temperature": "0.0",
                        "prompt": prompt[:_GROQ_PROMPT_MAX_CHARS],
                    },
                    files={"file": (filename, audio_bytes, "audio/wav")},
                )
                if resp.status_code >= 500 or resp.status_code == 429:
                    logger.warning(
                        "Groq API returned %d, attempt %d/%d",
                        resp.status_code,
                        attempt + 1,
                        _GROQ_MAX_API_RETRIES,
                    )
                    if attempt < _GROQ_MAX_API_RETRIES - 1:
                        backoff = _GROQ_RATE_LIMIT_BACKOFF_S if resp.status_code == 429 else _GROQ_ERROR_BACKOFF_S
                        time.sleep(backoff)
                        continue
                break
            except httpx.TransportError as exc:
                logger.warning(
                    "Groq API connection error: %s, attempt %d/%d", exc, attempt + 1, _GROQ_MAX_API_RETRIES
                )
                if attempt < _GROQ_MAX_API_RETRIES - 1:
                    time.sleep(_GROQ_ERROR_BACKOFF_S)
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

    return cast(_HTTPJsonResponse, resp)


def _groq_parse_response(
    data: dict,
    language: str,
) -> tuple[str, str, float, list[TranscriptionSegment] | None]:
    """Parse Groq API JSON response into (text, language, confidence, segments).

    Computes real confidence from segment-level avg_logprob and no_speech_prob.
    """
    text = data.get("text", "").strip()
    detected_lang = data.get("language", language)

    raw_segments = data.get("segments", [])
    parsed_segments: list[TranscriptionSegment] | None = None

    if raw_segments and isinstance(raw_segments, list):
        logprobs: list[float] = []
        no_speech_probs: list[float] = []
        parsed_segments = []
        for seg in raw_segments:
            if isinstance(seg, dict):
                alp = seg.get("avg_logprob")
                if isinstance(alp, (int, float)) and math.isfinite(alp):
                    logprobs.append(alp)
                nsp = seg.get("no_speech_prob")
                if isinstance(nsp, (int, float)) and math.isfinite(nsp):
                    no_speech_probs.append(nsp)
                seg_start = seg.get("start")
                seg_end = seg.get("end")
                seg_text = seg.get("text", "")
                if isinstance(seg_start, (int, float)) and isinstance(
                    seg_end, (int, float)
                ):
                    parsed_segments.append(
                        {
                            "start": float(seg_start),
                            "end": float(seg_end),
                            "text": str(seg_text).strip(),
                        }
                    )

        if logprobs:
            avg_logprob = sum(logprobs) / len(logprobs)
            confidence = min(1.0, max(0.0, 1.0 + avg_logprob))
            # Penalize confidence when Whisper thinks segments are noise
            if no_speech_probs:
                avg_no_speech = sum(no_speech_probs) / len(no_speech_probs)
                if avg_no_speech > _NO_SPEECH_PENALTY_THRESHOLD:
                    confidence *= 1.0 - avg_no_speech
            confidence = round(confidence, 4)
        else:
            confidence = _FALLBACK_CONFIDENCE  # fallback if segments lack logprobs
    else:
        confidence = _FALLBACK_CONFIDENCE  # fallback if no segments returned

    return text, detected_lang, confidence, parsed_segments if parsed_segments else None


# Groq Whisper STT (cloud) — main entry point


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
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    # Minimum audio duration check: require at least 0.1s at 16 kHz
    if isinstance(audio, np.ndarray) and len(audio) < _MIN_AUDIO_SAMPLES_16KHZ:
        logger.debug("Audio too short for Groq API (%d samples)", len(audio))
        return None

    t0 = time.monotonic()

    audio_bytes, filename = _groq_prepare_audio(audio)
    prompt = _groq_default_prompt(prompt)

    result = _groq_api_call(api_key, audio_bytes, filename, language, prompt, t0)

    # _groq_api_call returns TranscriptionResult on transport failure
    if isinstance(result, TranscriptionResult):
        return result

    elapsed = time.monotonic() - t0
    try:
        data = result.json()
    except (ValueError, TypeError) as exc:
        logger.warning("Groq API returned non-JSON response: %s", exc)
        return TranscriptionResult(
            text="",
            language=language,
            confidence=0.0,
            duration_seconds=round(elapsed, 3),
            backend="groq-whisper",
        )

    text, detected_lang, confidence, segments = _groq_parse_response(data, language)

    return TranscriptionResult(
        text=text,
        language=detected_lang,
        confidence=confidence,
        duration_seconds=round(elapsed, 3),
        backend="groq-whisper",
        segments=segments,
    )


# Local faster-whisper STT (offline fallback)


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
        self._model: Any | None = None

    def _ensure_model(self) -> None:
        """Lazy-load the WhisperModel on first use."""
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found,import-untyped]
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
        model = self._model
        if model is None:
            raise RuntimeError("Whisper model failed to load")
        segments_gen, info = model.transcribe(
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
                threshold=0.4,
                min_silence_duration_ms=400,
                speech_pad_ms=300,
                min_speech_duration_ms=200,
            ),
        )
        segments = list(segments_gen)
        texts: list[str] = []
        parsed_segments: list[TranscriptionSegment] = []
        for segment in segments:
            texts.append(segment.text.strip())
            seg_start = getattr(segment, "start", None)
            seg_end = getattr(segment, "end", None)
            if seg_start is not None and seg_end is not None:
                parsed_segments.append(
                    {
                        "start": float(seg_start),
                        "end": float(seg_end),
                        "text": segment.text.strip(),
                    }
                )
        elapsed = time.monotonic() - t0
        full_text = " ".join(texts).strip()
        # Compute confidence from segment avg_logprob (not language_probability which is always ~1.0 for English)
        logprobs = [
            seg.avg_logprob
            for seg in segments
            if hasattr(seg, "avg_logprob") and math.isfinite(seg.avg_logprob)
        ]
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


# Smart transcription (auto-selects best available backend)


def _try_groq(
    audio: np.ndarray | str, *, language: str, prompt: str
) -> TranscriptionResult | None:
    """Attempt Groq transcription, returning *None* on failure."""
    try:
        return transcribe_groq(audio, language=language, prompt=prompt)
    except (RuntimeError, OSError, ValueError) as exc:
        logger.warning("Groq STT attempt failed: %s", exc)
        return None


# Lazy singleton containers (dict avoids 'global' keyword)
_singletons: dict[str, Any] = {}
_local_stt_lock = threading.Lock()
_parakeet_lock = threading.Lock()
_local_emergency_lock = threading.Lock()


def _try_local(
    audio: np.ndarray | str, *, language: str, prompt: str = ""
) -> TranscriptionResult | None:
    """Attempt local faster-whisper transcription, returning *None* on failure."""
    try:
        with _local_stt_lock:
            if "local_stt" not in _singletons:
                _singletons["local_stt"] = SpeechToText()
            instance = _singletons["local_stt"]
        return instance.transcribe_audio(audio, language=language, prompt=prompt)
    except (RuntimeError, OSError, ValueError) as exc:
        logger.warning("Local STT attempt failed: %s", exc)
        return None


def _try_parakeet(
    audio: np.ndarray | str, *, language: str, prompt: str = ""
) -> TranscriptionResult | None:
    """Attempt Parakeet TDT 0.6B transcription via onnx-asr.

    Returns *None* on any failure (import error, model error, etc.) so the
    caller can fall back to the next backend.  The model is lazy-loaded on
    first use with double-checked locking to be thread-safe.
    """
    try:
        try:
            import onnx_asr  # type: ignore[import-not-found,import-untyped]
        except ImportError:
            logger.warning(
                "onnx-asr is not installed; Parakeet backend unavailable. "
                "Install with: pip install onnx-asr"
            )
            return None

        t0 = time.monotonic()

        # Lazy model load under lock -- guarantees single initialization.
        with _parakeet_lock:
            if "parakeet" not in _singletons:
                logger.info("Loading Parakeet TDT 0.6B model via onnx-asr...")
                model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2")
                # Try to enable timestamps for log probability access
                try:
                    model = model.with_timestamps()
                    logger.debug("Parakeet timestamps model loaded")
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug("Parakeet timestamps not available, using base model")
                _singletons["parakeet"] = model
            loaded_model = _singletons["parakeet"]

        # Transcription: numpy arrays need explicit sample_rate, file paths do not
        if isinstance(audio, np.ndarray):
            result = loaded_model.recognize(audio, sample_rate=16000)
        else:
            result = loaded_model.recognize(audio)

        text = str(result).strip() if result else ""
        elapsed = time.monotonic() - t0

        if not text:
            return TranscriptionResult(
                text="",
                language="en",
                confidence=0.0,
                duration_seconds=round(elapsed, 3),
                backend="parakeet-tdt",
            )

        # Confidence scoring: try to extract log probabilities from result
        confidence = _PARAKEET_BASELINE_CONFIDENCE
        try:
            # onnx-asr TimestampedResult may expose token-level log probs
            tokens = getattr(result, "tokens", None)
            if tokens:
                logprobs = []
                for tok in tokens:
                    lp = getattr(tok, "logprob", None) or getattr(tok, "log_prob", None)
                    if (
                        lp is not None
                        and isinstance(lp, (int, float))
                        and math.isfinite(lp)
                    ):
                        logprobs.append(lp)
                if logprobs:
                    avg_logprob = sum(logprobs) / len(logprobs)
                    confidence = min(1.0, max(0.0, 1.0 + avg_logprob))
        except (AttributeError, TypeError, ValueError) as exc:
            logger.debug("Parakeet logprob confidence extraction failed: %s", exc)

        return TranscriptionResult(
            text=text,
            language="en",
            confidence=round(confidence, 4),
            duration_seconds=round(elapsed, 3),
            backend="parakeet-tdt",
        )

    except (ImportError, RuntimeError, OSError, ValueError) as exc:
        logger.warning("Parakeet STT attempt failed: %s", exc)
        return None


def _try_local_emergency(
    audio: np.ndarray | str, *, language: str, prompt: str = ""
) -> TranscriptionResult | None:
    """Attempt local faster-whisper transcription with small.en model.

    This is the emergency fallback tier -- uses small.en for a practical
    CPU-speed tradeoff when all other backends have failed or returned
    low-confidence results.  A separate singleton is used so the standard
    ``_try_local()`` path (small.en or JARVIS_STT_MODEL) is unaffected.
    large-v3 on CPU takes 30+ seconds; small.en is far more practical here.
    """
    try:
        with _local_emergency_lock:
            if "local_emergency" not in _singletons:
                _singletons["local_emergency"] = SpeechToText(model_size="small.en")
            instance = _singletons["local_emergency"]
        return instance.transcribe_audio(audio, language=language, prompt=prompt)
    except (RuntimeError, OSError, ValueError) as exc:
        logger.warning("Local emergency STT (small.en) attempt failed: %s", exc)
        return None


# Fallback chain: ordered list of backend names for auto mode.
# Functions are looked up at call time so they can be mocked in tests.

FALLBACK_CHAIN: list[str] = [
    "parakeet",  # Best local: 6.05% WER
    "deepgram",  # Best cloud: keyterm boosting
    "groq",  # Existing cloud: free tier
    "local",  # Emergency: faster-whisper large-v3
]

_BACKEND_FN_NAMES: dict[str, str] = {
    "parakeet": "_try_parakeet",
    "deepgram": "_try_deepgram",
    "groq": "_try_groq",
    "local": "_try_local_emergency",
}

# Resolved at call time so mock.patch works correctly.
def _get_backend_fn(name: str) -> Callable[..., TranscriptionResult | None]:
    """Look up the backend function by name from the current module scope."""
    import sys
    return getattr(sys.modules[__name__], _BACKEND_FN_NAMES[name])


def _preprocess_audio_if_needed(
    audio: np.ndarray | str,
    *,
    language: str,
) -> tuple[np.ndarray | str, TranscriptionResult | None]:
    """Apply audio preprocessing when *audio* is a non-empty numpy array.

    Returns ``(processed_audio, None)`` on success, or
    ``(audio, early_result)`` if audio was pure silence after preprocessing
    (the caller should return *early_result* immediately).
    """
    if not isinstance(audio, np.ndarray) or len(audio) == 0:
        return audio, None
    try:
        from jarvis_engine.stt.postprocess import preprocess_audio

        audio = preprocess_audio(audio)
        if len(audio) == 0:
            logger.info("Audio was pure silence after preprocessing")
            return audio, TranscriptionResult(
                text="",
                language=language,
                confidence=0.0,
                duration_seconds=0.0,
                backend="preprocessed-silence",
            )
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("Audio preprocessing failed, using raw audio: %s", exc)
    return audio, None


def _transcribe_forced(
    audio: np.ndarray | str,
    *,
    backend: str,
    language: str,
    prompt: str,
    root_dir: Path | None,
) -> TranscriptionResult:
    """Run a single forced backend and return its result."""
    if backend == "groq":
        result = _try_groq(audio, language=language, prompt=prompt)
    elif backend == "local":
        result = _try_local_emergency(audio, language=language, prompt=prompt)
    elif backend == "parakeet":
        result = _try_parakeet(audio, language=language, prompt=prompt)
    elif backend == "deepgram":
        result = _try_deepgram(
            audio,
            language=language,
            prompt=prompt,
            keyterms=_load_keyterms(),
        )
    else:
        result = None

    if result is None:
        logger.warning("%s transcription returned None in forced mode", backend)
        return TranscriptionResult(
            text="",
            language=language or "en",
            confidence=0.0,
            duration_seconds=0.0,
            backend=f"{backend}-failed",
        )
    _log_stt_metric(
        root_dir,
        backend=result.backend,
        confidence=result.confidence,
        latency_ms=result.duration_seconds * 1000,
        text_length=len(result.text),
    )
    return result


# Higher confidence threshold for Parakeet when personal vocabulary is
# expected — forces fallthrough to Deepgram which supports keyword boosting.
_PARAKEET_PROPER_NOUN_THRESHOLD = 0.75


def _parakeet_should_fallthrough(
    result: TranscriptionResult,
    entity_list: list[str] | None,
) -> bool:
    """Return True if Parakeet result should fall through due to proper noun heuristic.

    When the caller provides an *entity_list* (personal names, places, etc.)
    and none of those entities appear in the Parakeet transcript, we treat the
    result as below-threshold even if confidence is >= 0.6.  Deepgram with its
    keyword boosting is much better at proper nouns.
    """
    if not entity_list:
        return False
    if result.backend != "parakeet-tdt":
        return False
    if result.confidence >= _PARAKEET_PROPER_NOUN_THRESHOLD:
        return False
    lowered_text = result.text.lower()
    for entity in entity_list:
        if entity.lower() in lowered_text:
            return False  # entity found — Parakeet got it right
    # Entity list provided but none found in transcript — try Deepgram
    logger.info(
        "Parakeet proper-noun heuristic: entity_list=%s not found in '%s', "
        "falling through to next backend",
        entity_list[:5],
        result.text[:60],
    )
    return True


def _transcribe_auto(
    audio: np.ndarray | str,
    *,
    language: str,
    prompt: str,
    root_dir: Path | None,
    entity_list: list[str] | None = None,
) -> TranscriptionResult:
    """Walk the 4-tier fallback chain and return the best result."""
    best_so_far: TranscriptionResult | None = None

    for name in FALLBACK_CHAIN:
        try_fn = _get_backend_fn(name)
        if name == "deepgram":
            result = try_fn(
                audio, language=language, prompt=prompt, keyterms=_load_keyterms()
            )
        else:
            result = try_fn(audio, language=language, prompt=prompt)

        if result is not None and result.text.strip():
            logger.info(
                "%s STT: '%s' in %.2fs (confidence: %.3f)",
                name,
                result.text[:60],
                result.duration_seconds,
                result.confidence,
            )
            _log_stt_metric(
                root_dir,
                backend=result.backend,
                confidence=result.confidence,
                latency_ms=result.duration_seconds * 1000,
                text_length=len(result.text),
            )

            # Parakeet proper-noun heuristic: if caller expects specific
            # entities but Parakeet didn't transcribe any, fall through to
            # Deepgram which has keyword boosting.
            if _parakeet_should_fallthrough(result, entity_list):
                if best_so_far is None or result.confidence > best_so_far.confidence:
                    best_so_far = result
                continue

            if result.confidence >= CONFIDENCE_RETRY_THRESHOLD:
                return result

            if best_so_far is None or result.confidence > best_so_far.confidence:
                best_so_far = result
        else:
            if result is None:
                logger.info("%s STT failed, trying next backend", name)
            else:
                logger.info("%s STT returned empty text, trying next backend", name)

    if best_so_far is None:
        logger.error("All STT backends failed")
        return TranscriptionResult(
            text="",
            language=language,
            confidence=0.0,
            duration_seconds=0.0,
            backend="none",
        )
    return best_so_far


def _apply_postprocessing(
    result: TranscriptionResult,
    *,
    gateway: object | None,
    entity_list: list[str] | None,
) -> TranscriptionResult:
    """Apply post-processing (fillers, LLM, NER) to a transcription result."""
    if not result.text.strip():
        return result
    try:
        from jarvis_engine.stt.postprocess import (
            postprocess_transcription,
            postprocess_transcription_segments,
        )

        processed = postprocess_transcription(
            result.text,
            result.confidence,
            gateway=gateway,
            entity_list=entity_list,
        )
        processed_segments = postprocess_transcription_segments(
            result.segments,
            entity_list=entity_list,
        )
        return TranscriptionResult(
            text=processed,
            language=result.language,
            confidence=result.confidence,
            duration_seconds=result.duration_seconds,
            backend=result.backend,
            segments=processed_segments,
            retried=result.retried,
        )
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("Post-processing failed, using raw text: %s", exc)
        return result


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

    Backend selection via ``JARVIS_STT_BACKEND`` env var:

    - ``"groq"``: Force Groq Whisper only
    - ``"local"``: Force local faster-whisper only (small.en / env override)
    - ``"parakeet"``: Force Parakeet TDT only
    - ``"deepgram"``: Force Deepgram Nova-3 only
    - ``"auto"`` (default): 4-tier fallback chain
      Parakeet -> Deepgram -> Groq -> faster-whisper large-v3

    In auto mode each backend is tried in order.  If a backend succeeds
    with confidence >= ``CONFIDENCE_RETRY_THRESHOLD`` the result is used
    immediately.  If confidence is below threshold the result is saved as
    *best_so_far* and the next backend is tried.  After all backends the
    highest-confidence result is returned.

    When *root_dir* is provided, quality metrics are logged to
    ``<root_dir>/.planning/runtime/stt_metrics.jsonl``.

    When *gateway* and/or *entity_list* are provided, post-processing
    (filler removal, LLM correction, NER entity correction) is applied
    to the final transcription text.
    """
    pipeline_t0 = time.monotonic()

    backend = os.environ.get("JARVIS_STT_BACKEND", "auto").lower()
    resolved_entities = _build_default_entity_list(entity_list)
    resolved_prompt = _build_stt_prompt(prompt, resolved_entities)

    audio, early_result = _preprocess_audio_if_needed(audio, language=language)
    if early_result is not None:
        return early_result

    _FORCED_NAMES = {"groq", "local", "parakeet", "deepgram"}

    if backend in _FORCED_NAMES:
        final = _transcribe_forced(
            audio,
            backend=backend,
            language=language,
            prompt=resolved_prompt,
            root_dir=root_dir,
        )
    else:
        final = _transcribe_auto(
            audio,
            language=language,
            prompt=resolved_prompt,
            root_dir=root_dir,
            entity_list=resolved_entities,
        )

    # Capture raw text before postprocessing (STT sidecar for learning/telemetry)
    pre_postprocess_text = final.text

    result = _apply_postprocessing(final, gateway=gateway, entity_list=resolved_entities)
    result.raw_text = pre_postprocess_text

    # STT-12: Record total pipeline latency (preprocessing + transcription + postprocessing)
    pipeline_elapsed_ms = (time.monotonic() - pipeline_t0) * 1000
    result.pipeline_latency_ms = round(pipeline_elapsed_ms, 1)

    # STT-11: Flag low-confidence results for user confirmation
    if result.text.strip() and result.confidence < CONFIDENCE_CONFIRMATION_THRESHOLD:
        result.needs_confirmation = True
        logger.info(
            "Low-confidence transcription (%.3f < %.3f), flagged for confirmation: %r",
            result.confidence,
            CONFIDENCE_CONFIRMATION_THRESHOLD,
            result.text[:60],
        )

    return result


def warmup_stt_backends() -> None:
    """Pre-load STT models in the background to eliminate cold-start latency.

    Intended to be called from the daemon startup path via a background
    thread::

        threading.Thread(target=warmup_stt_backends, daemon=True).start()

    Currently warms up:
    - Parakeet TDT 0.6B (the primary local backend)

    Handles ``ImportError`` gracefully when ``onnx_asr`` is not installed.
    """
    try:
        import onnx_asr  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("onnx_asr not installed; skipping Parakeet warmup")
        return

    with _parakeet_lock:
        if "parakeet" in _singletons:
            logger.debug("Parakeet model already loaded; skipping warmup")
            return
        try:
            logger.info("Warming up Parakeet TDT 0.6B model...")
            model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2")
            try:
                model = model.with_timestamps()
            except (AttributeError, RuntimeError, TypeError):
                logger.debug(
                    "Parakeet model does not support with_timestamps(), using without"
                )
            _singletons["parakeet"] = model
            logger.info("Parakeet TDT 0.6B model warmed up successfully")
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning("Parakeet warmup failed: %s", exc)


def listen_and_transcribe(
    *,
    max_duration_seconds: float = 30.0,
    language: str = "en",
    mode: str = "conversation",
    root_dir: Path | None = None,
    gateway: object | None = None,
    entity_list: list[str] | None = None,
) -> TranscriptionResult:
    """Record from microphone and transcribe in one call.

    Uses smart backend selection: Groq Whisper if GROQ_API_KEY is set,
    otherwise falls back to local faster-whisper.
    """
    audio = record_from_microphone(
        max_duration_seconds=max_duration_seconds,
        mode=mode,
    )
    return transcribe_smart(
        audio,
        language=language,
        root_dir=root_dir,
        gateway=gateway,
        entity_list=entity_list,
    )
