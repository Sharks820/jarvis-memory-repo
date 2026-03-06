"""Speech-to-text module with 4-tier fallback chain.

Backends (in auto-mode priority order):
  1. Parakeet TDT 0.6B (local, 6.05% WER)
  2. Deepgram Nova-3 (cloud, keyterm boosting)
  3. Groq Whisper Turbo (cloud, free tier)
  4. faster-whisper large-v3 (local, emergency fallback)

Backend selected via JARVIS_STT_BACKEND env var:
  "parakeet", "deepgram", "groq", "local", or "auto" (default).
"""

from __future__ import annotations

import io
import json
import logging
import math
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


# ---------------------------------------------------------------------------
# Keyterm loading for Deepgram prompting
# ---------------------------------------------------------------------------

_keyterms_cache: list[str] | None = None


def _load_keyterms() -> list[str]:
    """Load keyterms from personal_vocab.txt for Deepgram prompting.

    Each line in personal_vocab.txt may contain annotations in
    parentheses (e.g. ``"Conner (not Connor, Conor)"``).  Only the
    primary term before the parenthetical is extracted for use as a
    Deepgram keyword.

    Keyterms are cached after the first read so the file is only
    opened once per process lifetime.
    """
    global _keyterms_cache
    if _keyterms_cache is not None:
        return _keyterms_cache
    from jarvis_engine._shared import load_personal_vocab_lines
    _keyterms_cache = load_personal_vocab_lines(strip_parens=True)
    return _keyterms_cache


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
    except OSError as exc:
        logger.debug("Failed to write STT metric: %s", exc)


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

    # Default prompt biases recognition toward Jarvis commands.
    # Format as example utterances starting with "Jarvis" so Whisper
    # expects "Jarvis" at the beginning of the current transcription.
    if not prompt:
        prompt = (
            "Jarvis, what's on my schedule today? "
            "Jarvis, run the ops brief. "
            "Jarvis, check brain status. "
            "Jarvis, add a task. Jarvis, self heal."
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
                if resp.status_code >= 500 or resp.status_code == 429:
                    logger.warning("Groq API returned %d, attempt %d/2", resp.status_code, attempt + 1)
                    if attempt < 1:
                        time.sleep(2 if resp.status_code == 429 else 1)
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

    # Compute real confidence from segment-level avg_logprob and no_speech_prob
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
    except Exception as exc:
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
    except Exception as exc:
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
                    except Exception:
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
        except Exception as exc:
            logger.debug("Parakeet logprob confidence extraction failed: %s", exc)

        return TranscriptionResult(
            text=text,
            language="en",
            confidence=round(confidence, 4),
            duration_seconds=round(elapsed, 3),
            backend="parakeet-tdt",
        )

    except Exception as exc:
        logger.warning("Parakeet STT attempt failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Deepgram Nova-3 STT (cloud, keyterm prompting)
# ---------------------------------------------------------------------------

def _try_deepgram(
    audio: np.ndarray | str,
    *,
    language: str,
    prompt: str = "",
    keyterms: list[str] | None = None,
) -> TranscriptionResult | None:
    """Attempt Deepgram Nova-3 transcription with keyterm prompting.

    Returns *None* immediately if ``DEEPGRAM_API_KEY`` is not set or
    if the request fails for any reason, so the caller can fall back
    to the next backend.

    Parameters
    ----------
    audio:
        Either a mono float32 numpy array (16 kHz) or a path to an audio file.
    language:
        Language code hint (e.g. ``"en"``).
    prompt:
        Unused for Deepgram (kept for API compatibility with other backends).
    keyterms:
        Explicit keyterm list.  If *None*, auto-loaded from
        ``personal_vocab.txt`` via :func:`_load_keyterms`.
    """
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        return None

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; Deepgram backend unavailable")
        return None

    try:
        t0 = time.monotonic()

        # Prepare audio bytes
        if isinstance(audio, str):
            with open(audio, "rb") as f:
                audio_bytes = f.read()
            content_type = "audio/wav"
        else:
            audio_bytes = _numpy_to_wav_bytes(audio)
            content_type = "audio/wav"

        # Build query params as list of tuples to support repeated "keywords"
        # Deepgram REST API uses "keywords" param (can be repeated)
        if keyterms is None:
            keyterms = _load_keyterms()

        params: list[tuple[str, str]] = [
            ("model", "nova-3"),
            ("language", language),
            ("punctuate", "true"),
            ("smart_format", "true"),
        ]
        # Deepgram supports up to 500 keywords per request
        for kt in keyterms[:500]:
            params.append(("keywords", kt))

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                "https://api.deepgram.com/v1/listen",
                headers={
                    "Authorization": f"Token {api_key}",
                    "Content-Type": content_type,
                },
                params=params,
                content=audio_bytes,
            )

        elapsed = time.monotonic() - t0

        if resp.status_code != 200:
            logger.warning(
                "Deepgram API returned %d: %s",
                resp.status_code,
                resp.text[:200],
            )
            return None

        data = resp.json()

        # Extract transcript and confidence from Deepgram response
        # Response structure: results.channels[0].alternatives[0]
        channels = data.get("results", {}).get("channels", [])
        if not channels:
            logger.warning("Deepgram returned no channels")
            return None

        alternatives = channels[0].get("alternatives", [])
        if not alternatives:
            logger.warning("Deepgram returned no alternatives")
            return None

        best = alternatives[0]
        transcript = best.get("transcript", "").strip()
        confidence = best.get("confidence", 0.0)

        # Extract per-word data for segments if available
        words = best.get("words", [])
        parsed_segments: list[dict] | None = None
        if words:
            parsed_segments = []
            for word_info in words:
                w_start = word_info.get("start")
                w_end = word_info.get("end")
                w_word = word_info.get("word", "")
                if isinstance(w_start, (int, float)) and isinstance(w_end, (int, float)):
                    parsed_segments.append({
                        "start": float(w_start),
                        "end": float(w_end),
                        "text": str(w_word),
                    })

        return TranscriptionResult(
            text=transcript,
            language=language,
            confidence=round(float(confidence), 4),
            duration_seconds=round(elapsed, 3),
            backend="deepgram-nova3",
            segments=parsed_segments if parsed_segments else None,
        )

    except Exception as exc:
        logger.warning("Deepgram STT attempt failed: %s", exc)
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
    except Exception as exc:
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
        retry_result = _try_local(audio, language=language, prompt=prompt)
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

    # Map of forced backend modes to their try functions
    _forced_backends: dict[str, object] = {
        "groq": _try_groq,
        "local": _try_local,
        "parakeet": _try_parakeet,
        "deepgram": _try_deepgram,
    }

    final: TranscriptionResult | None = None

    if backend in _forced_backends:
        # Forced single-backend mode
        try_fn = _forced_backends[backend]
        if backend == "groq":
            # Groq forced mode uses transcribe_groq directly (raises on failure)
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
        final = result

    else:
        # Auto mode: 4-tier fallback chain
        # Resolve function references at call time (supports mock patching)
        import sys
        _this_module = sys.modules[__name__]
        best_so_far: TranscriptionResult | None = None

        for name in FALLBACK_CHAIN:
            try_fn = getattr(_this_module, _BACKEND_FN_MAP[name])
            # Call the try function with appropriate kwargs
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
                    # Good enough -- use this result
                    best_so_far = result
                    break

                # Low confidence -- keep as best if better than previous
                if best_so_far is None or result.confidence > best_so_far.confidence:
                    best_so_far = result
                # Continue to next backend
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

        final = best_so_far

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
    drain_seconds: float = 0.0,
) -> np.ndarray:
    """Record audio from the default microphone with Silero VAD.

    Uses Silero VAD (ML-based) for speech detection by default.  Falls
    back to RMS energy-based VAD when ``torch`` / ``silero-vad`` are not
    installed.

    Recording stops early when silence is detected after speech, avoiding
    the need to wait for the full *max_duration_seconds*.

    Parameters
    ----------
    sample_rate:
        Audio sample rate in Hz (default 16000).
    max_duration_seconds:
        Maximum recording duration in seconds (default 30).
    silence_threshold:
        RMS energy threshold below which audio is considered silence
        (default 0.01).  Only used when Silero VAD is unavailable.
    silence_duration:
        Seconds of continuous silence after speech before stopping (default 2.0).
    drain_seconds:
        Seconds of audio to read and discard when opening the stream.  This
        flushes stale audio left in the OS audio buffer (e.g. wake word
        remnants) before the actual recording begins.

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

    # Lazy-import VAD detector (graceful degradation if not installed)
    _vad_detector = None
    _use_silero = False
    try:
        from jarvis_engine.stt_vad import get_vad_detector
        _vad_detector = get_vad_detector(sampling_rate=sample_rate)
        _use_silero = _vad_detector.available
    except Exception as exc:
        logger.debug("VAD detector initialization failed: %s", exc)

    if _use_silero:
        logger.info("Using Silero VAD for speech detection")
    else:
        logger.warning(
            "Silero VAD not available, falling back to energy-based VAD"
        )

    try:
        logger.info(
            "Recording from microphone for up to %.1f seconds at %d Hz...",
            max_duration_seconds,
            sample_rate,
        )

        frames: list[np.ndarray] = []
        speech_detected = False
        silence_frames = 0

        # Silero VAD works best with 32ms (512-sample) chunks at 16 kHz.
        # RMS fallback uses 100ms chunks for backward compatibility.
        if _use_silero:
            chunk_duration = 0.032  # 32ms for Silero VAD
        else:
            chunk_duration = 0.1   # 100ms for RMS fallback
        samples_per_chunk = int(sample_rate * chunk_duration)
        max_silence_chunks = int(silence_duration / chunk_duration)
        min_recording_chunks = int(0.5 / chunk_duration)  # At least 0.5s

        with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32") as stream:
            # Drain stale audio from OS buffer (e.g. wake word remnants)
            if drain_seconds > 0:
                drain_samples = int(sample_rate * drain_seconds)
                stream.read(drain_samples)
                logger.debug("Drained %.0fms of stale audio", drain_seconds * 1000)

            max_chunks = int(max_duration_seconds / chunk_duration)
            for i in range(max_chunks):
                chunk, _ = stream.read(samples_per_chunk)
                frames.append(chunk.copy())

                # --- Speech detection ---
                if _use_silero and _vad_detector is not None:
                    # Silero VAD path: process mono channel
                    mono = chunk[:, 0] if chunk.ndim > 1 else chunk
                    is_speech = _vad_detector.process_chunk(mono)
                else:
                    # RMS energy fallback
                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    is_speech = rms > silence_threshold

                if is_speech:
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

        # Reset VAD state for next recording session (stateful model)
        if _use_silero and _vad_detector is not None:
            _vad_detector.reset()

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
