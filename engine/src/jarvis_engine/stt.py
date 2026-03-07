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
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine.stt_backends import (  # noqa: F401 -- re-exports
    _load_keyterms,
    _numpy_to_wav_bytes,
    _try_deepgram,
    record_from_microphone,
)

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
    from jarvis_engine._constants import runtime_dir
    metrics_path = runtime_dir(root_dir) / "stt_metrics.jsonl"
    record = {
        "ts": _now_iso(),
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


# ---------------------------------------------------------------------------
# Groq Whisper STT (cloud) — helpers
# ---------------------------------------------------------------------------


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
) -> "object":
    """Send the audio to Groq Whisper API with retry on transient errors.

    Returns the httpx Response on success.
    Raises RuntimeError on non-200 final status.
    Returns a TranscriptionResult with empty text on transport failure.
    """
    import httpx

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
                if resp.status_code >= 500 or resp.status_code == 429:
                    logger.warning("Groq API returned %d, attempt %d/2", resp.status_code, attempt + 1)
                    if attempt < 1:
                        time.sleep(2 if resp.status_code == 429 else 1)
                        continue
                break
            except httpx.TransportError as exc:
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

    return resp


def _groq_parse_response(
    data: dict,
    language: str,
) -> tuple[str, str, float, list[dict] | None]:
    """Parse Groq API JSON response into (text, language, confidence, segments).

    Computes real confidence from segment-level avg_logprob and no_speech_prob.
    """
    text = data.get("text", "").strip()
    detected_lang = data.get("language", language)

    raw_segments = data.get("segments", [])
    parsed_segments: list[dict] | None = None

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
                if isinstance(seg_start, (int, float)) and isinstance(seg_end, (int, float)):
                    parsed_segments.append({
                        "start": float(seg_start),
                        "end": float(seg_end),
                        "text": str(seg_text).strip(),
                    })

        if logprobs:
            avg_logprob = sum(logprobs) / len(logprobs)
            confidence = min(1.0, max(0.0, 1.0 + avg_logprob))
            # Penalize confidence when Whisper thinks segments are noise
            if no_speech_probs:
                avg_no_speech = sum(no_speech_probs) / len(no_speech_probs)
                if avg_no_speech > 0.5:
                    confidence *= (1.0 - avg_no_speech)
            confidence = round(confidence, 4)
        else:
            confidence = 0.90  # fallback if segments lack logprobs
    else:
        confidence = 0.90  # fallback if no segments returned

    return text, detected_lang, confidence, parsed_segments if parsed_segments else None


# ---------------------------------------------------------------------------
# Groq Whisper STT (cloud) — main entry point
# ---------------------------------------------------------------------------


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

    # Minimum audio duration check: require at least 0.1s (1600 samples at 16kHz)
    if isinstance(audio, np.ndarray) and len(audio) < 1600:
        logger.debug("Audio too short for Groq API (%d samples)", len(audio))
        return None

    t0 = time.monotonic()

    audio_bytes, filename = _groq_prepare_audio(audio)
    prompt = _groq_default_prompt(prompt)

    result = _groq_api_call(api_key, audio_bytes, filename, language, prompt, t0)

    # _groq_api_call returns TranscriptionResult on transport failure
    if isinstance(result, TranscriptionResult):
        return result

    data = result.json()
    elapsed = time.monotonic() - t0

    text, detected_lang, confidence, segments = _groq_parse_response(data, language)

    return TranscriptionResult(
        text=text,
        language=detected_lang,
        confidence=confidence,
        duration_seconds=round(elapsed, 3),
        backend="groq-whisper",
        segments=segments,
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
        logprobs = [
            seg.avg_logprob for seg in segments
            if hasattr(seg, 'avg_logprob') and math.isfinite(seg.avg_logprob)
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


# ---------------------------------------------------------------------------
# Smart transcription (auto-selects best available backend)
# ---------------------------------------------------------------------------

def _try_groq(
    audio: np.ndarray | str, *, language: str, prompt: str
) -> TranscriptionResult | None:
    """Attempt Groq transcription, returning *None* on failure."""
    try:
        return transcribe_groq(audio, language=language, prompt=prompt)
    except (RuntimeError, OSError, ValueError) as exc:
        logger.warning("Groq STT attempt failed: %s", exc)
        return None


_local_stt_instance: SpeechToText | None = None
_local_stt_lock = threading.Lock()

_parakeet_model = None
_parakeet_lock = threading.Lock()

_local_emergency_instance: SpeechToText | None = None
_local_emergency_lock = threading.Lock()


def _try_local(
    audio: np.ndarray | str, *, language: str, prompt: str = ""
) -> TranscriptionResult | None:
    """Attempt local faster-whisper transcription, returning *None* on failure."""
    global _local_stt_instance
    try:
        if _local_stt_instance is None:
            with _local_stt_lock:
                if _local_stt_instance is None:
                    _local_stt_instance = SpeechToText()
        return _local_stt_instance.transcribe_audio(audio, language=language, prompt=prompt)
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
    global _parakeet_model

    try:
        try:
            import onnx_asr  # type: ignore[import-untyped]
        except ImportError:
            logger.warning(
                "onnx-asr is not installed; Parakeet backend unavailable. "
                "Install with: pip install onnx-asr"
            )
            return None

        t0 = time.monotonic()

        # Lazy model load with double-checked locking
        if _parakeet_model is None:
            with _parakeet_lock:
                if _parakeet_model is None:
                    logger.info("Loading Parakeet TDT 0.6B model via onnx-asr...")
                    model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2")
                    # Try to enable timestamps for log probability access
                    try:
                        model = model.with_timestamps()
                        logger.debug("Parakeet timestamps model loaded")
                    except (AttributeError, RuntimeError, TypeError):
                        logger.debug(
                            "Parakeet timestamps not available, using base model"
                        )
                    _parakeet_model = model

        # Transcription: numpy arrays need explicit sample_rate, file paths do not
        if isinstance(audio, np.ndarray):
            result = _parakeet_model.recognize(audio, sample_rate=16000)
        else:
            result = _parakeet_model.recognize(audio)

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
        confidence = 0.94  # Baseline: Parakeet's known 6.05% WER
        try:
            # onnx-asr TimestampedResult may expose token-level log probs
            tokens = getattr(result, "tokens", None)
            if tokens:
                logprobs = []
                for tok in tokens:
                    lp = getattr(tok, "logprob", None) or getattr(tok, "log_prob", None)
                    if lp is not None and isinstance(lp, (int, float)) and math.isfinite(lp):
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
    """Attempt local faster-whisper transcription with large-v3 model.

    This is the emergency fallback tier -- uses the highest-quality local
    model (large-v3) for maximum accuracy when all other backends have
    failed or returned low-confidence results.  A separate singleton is
    used so the standard ``_try_local()`` path (small.en or JARVIS_STT_MODEL)
    is unaffected.
    """
    global _local_emergency_instance
    try:
        if _local_emergency_instance is None:
            with _local_emergency_lock:
                if _local_emergency_instance is None:
                    _local_emergency_instance = SpeechToText(model_size="large-v3")
        return _local_emergency_instance.transcribe_audio(audio, language=language, prompt=prompt)
    except (RuntimeError, OSError, ValueError) as exc:
        logger.warning("Local emergency STT (large-v3) attempt failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Fallback chain: ordered list of backend names for auto mode.
# Functions are looked up at call time so they can be mocked in tests.
# ---------------------------------------------------------------------------

FALLBACK_CHAIN: list[str] = [
    "parakeet",     # Best local: 6.05% WER
    "deepgram",     # Best cloud: keyterm boosting
    "groq",         # Existing cloud: free tier
    "local",        # Emergency: faster-whisper large-v3
]

_BACKEND_FN_MAP: dict[str, str] = {
    "parakeet": "_try_parakeet",
    "deepgram": "_try_deepgram",
    "groq": "_try_groq",
    "local": "_try_local_emergency",
}


def _preprocess_audio_if_needed(
    audio: np.ndarray | str, *, language: str,
) -> tuple[np.ndarray | str, TranscriptionResult | None]:
    """Apply audio preprocessing when *audio* is a non-empty numpy array.

    Returns ``(processed_audio, None)`` on success, or
    ``(audio, early_result)`` if audio was pure silence after preprocessing
    (the caller should return *early_result* immediately).
    """
    if not isinstance(audio, np.ndarray) or len(audio) == 0:
        return audio, None
    try:
        from jarvis_engine.stt_postprocess import preprocess_audio
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
    _forced_backends: dict[str, object] = {
        "groq": _try_groq,
        "local": _try_local,
        "parakeet": _try_parakeet,
        "deepgram": _try_deepgram,
    }

    try_fn = _forced_backends[backend]
    if backend == "groq":
        result = transcribe_groq(audio, language=language, prompt=prompt)
    elif backend == "deepgram":
        result = try_fn(audio, language=language, prompt=prompt, keyterms=_load_keyterms())
    else:
        result = try_fn(audio, language=language, prompt=prompt)

    if result is None:
        logger.warning("%s transcription returned None in forced mode", backend)
        return TranscriptionResult(
            text="", language=language or "en",
            confidence=0.0, duration_seconds=0.0,
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


def _transcribe_auto(
    audio: np.ndarray | str,
    *,
    language: str,
    prompt: str,
    root_dir: Path | None,
) -> TranscriptionResult:
    """Walk the 4-tier fallback chain and return the best result."""
    import sys
    _this_module = sys.modules[__name__]
    best_so_far: TranscriptionResult | None = None

    for name in FALLBACK_CHAIN:
        try_fn = getattr(_this_module, _BACKEND_FN_MAP[name])
        if name == "deepgram":
            result = try_fn(audio, language=language, prompt=prompt, keyterms=_load_keyterms())
        else:
            result = try_fn(audio, language=language, prompt=prompt)

        if result is not None and result.text.strip():
            logger.info(
                "%s STT: '%s' in %.2fs (confidence: %.3f)",
                name, result.text[:60], result.duration_seconds,
                result.confidence,
            )
            _log_stt_metric(
                root_dir,
                backend=result.backend,
                confidence=result.confidence,
                latency_ms=result.duration_seconds * 1000,
                text_length=len(result.text),
            )

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
        from jarvis_engine.stt_postprocess import postprocess_transcription
        processed = postprocess_transcription(
            result.text,
            result.confidence,
            gateway=gateway,
            entity_list=entity_list,
        )
        return TranscriptionResult(
            text=processed,
            language=result.language,
            confidence=result.confidence,
            duration_seconds=result.duration_seconds,
            backend=result.backend,
            segments=result.segments,
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
    backend = os.environ.get("JARVIS_STT_BACKEND", "auto").lower()

    audio, early_result = _preprocess_audio_if_needed(audio, language=language)
    if early_result is not None:
        return early_result

    _FORCED_NAMES = {"groq", "local", "parakeet", "deepgram"}

    if backend in _FORCED_NAMES:
        final = _transcribe_forced(
            audio, backend=backend, language=language, prompt=prompt,
            root_dir=root_dir,
        )
    else:
        final = _transcribe_auto(
            audio, language=language, prompt=prompt, root_dir=root_dir,
        )

    return _apply_postprocessing(final, gateway=gateway, entity_list=entity_list)


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
