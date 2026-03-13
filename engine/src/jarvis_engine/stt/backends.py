"""STT backend implementations -- individual transcription providers.

Extracted from ``stt.py`` for separation of concerns.  Each backend
function is a standalone helper that attempts transcription via one
provider and returns a :class:`TranscriptionResult` (or ``None`` on
failure) so the caller can fall through to the next backend.

Functions in this module are re-exported by ``stt.py`` to preserve
backward-compatible ``from jarvis_engine.stt import ...`` paths.
"""

from __future__ import annotations

__all__ = ["record_from_microphone"]

import io
import logging
import os
import struct
import time
from collections import deque
from typing import Any, Protocol, cast

import numpy as np

from jarvis_engine.stt.contracts import TranscriptionResult, TranscriptionSegment

logger = logging.getLogger(__name__)

# API endpoints
_DEEPGRAM_API_URL = "https://api.deepgram.com/v1/listen"

# Audio constants
_SAMPLE_RATE_HZ = 16000
_BITS_PER_SAMPLE = 16
_BYTES_PER_SAMPLE = _BITS_PER_SAMPLE // 8  # 16-bit = 2 bytes per sample
_INT16_MAX = 32767
_INT16_MIN = -32768
_DEEPGRAM_MAX_KEYWORDS = 500
_DEEPGRAM_REQUEST_TIMEOUT_S = 30.0

# Recording parameters
_MIN_RECORDING_DURATION_S = 0.5  # at least 0.5s of audio
_PRE_SPEECH_PAD_S = 0.2  # ring buffer before speech onset
_POST_SPEECH_PAD_S = 0.3  # trailing audio after speech ends
_SILERO_CHUNK_DURATION_S = 0.032  # 32ms (512 samples at 16 kHz)
_RMS_CHUNK_DURATION_S = 0.1  # 100ms for RMS fallback
_NOISE_CALIBRATION_DURATION_S = 0.5  # 500ms ambient calibration


class _AudioReadStream(Protocol):
    def read(self, frames: int) -> tuple[np.ndarray, Any]: ...


class _VadDetector(Protocol):
    available: bool

    def process_chunk(self, chunk: np.ndarray) -> bool: ...

    def reset(self) -> None: ...


# Adaptive noise floor calibration

# Clamping bounds for the adaptive noise threshold
_NOISE_FLOOR_MIN = 0.005
_NOISE_FLOOR_MAX = 0.05
_NOISE_FLOOR_MULTIPLIER = 2.5
_NOISE_FLOOR_RECALIBRATE_INTERVAL = 60.0  # seconds


def _calibrate_noise_floor(
    stream: _AudioReadStream,
    sample_rate: int,
) -> float:
    """Capture 500ms of ambient audio and compute an adaptive silence threshold.

    The threshold is set to 2.5x the ambient RMS, clamped between
    ``_NOISE_FLOOR_MIN`` (0.005) and ``_NOISE_FLOOR_MAX`` (0.05) to
    avoid extreme values in very quiet or very noisy environments.
    """
    calibration_samples = int(sample_rate * _NOISE_CALIBRATION_DURATION_S)
    try:
        ambient_chunk, _ = stream.read(calibration_samples)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.debug("Noise floor calibration read failed: %s", exc)
        return 0.01  # fall back to original default

    ambient_rms = float(np.sqrt(np.mean(ambient_chunk**2)))
    threshold = ambient_rms * _NOISE_FLOOR_MULTIPLIER
    clamped = max(_NOISE_FLOOR_MIN, min(_NOISE_FLOOR_MAX, threshold))
    logger.debug(
        "Noise floor calibration: ambient_rms=%.5f, raw_threshold=%.5f, clamped=%.5f",
        ambient_rms,
        threshold,
        clamped,
    )
    return clamped


# Shared WAV conversion utility


def _numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int = _SAMPLE_RATE_HZ) -> bytes:
    """Convert a mono float32 numpy array to WAV bytes for API upload."""
    audio_int16 = np.clip(audio * _INT16_MAX, _INT16_MIN, _INT16_MAX).astype(np.int16)
    buf = io.BytesIO()
    # Write WAV header
    num_samples = len(audio_int16)
    data_size = num_samples * _BYTES_PER_SAMPLE
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))  # chunk size
    buf.write(struct.pack("<H", 1))  # PCM format
    buf.write(struct.pack("<H", 1))  # mono
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * _BYTES_PER_SAMPLE))  # byte rate
    buf.write(struct.pack("<H", _BYTES_PER_SAMPLE))  # block align
    buf.write(struct.pack("<H", _BITS_PER_SAMPLE))  # bits per sample
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(audio_int16.tobytes())
    return buf.getvalue()


# Keyterm loading for Deepgram prompting


def _load_keyterms() -> list[str]:
    """Load keyterms from personal_vocab.txt for Deepgram prompting.

    Each line in personal_vocab.txt may contain annotations in
    parentheses (e.g. ``"Conner (not Connor, Conor)"``).  Only the
    primary term before the parenthetical is extracted for use as a
    Deepgram keyword.

    Results are cached in ``_shared.load_personal_vocab_lines``.
    """
    from jarvis_engine._shared import load_personal_vocab_lines

    return load_personal_vocab_lines(strip_parens=True)


# Deepgram Nova-3 STT (cloud, keyterm prompting)


def _prepare_deepgram_audio(audio: np.ndarray | str) -> tuple[bytes, str]:
    """Convert audio input to WAV bytes and content type for Deepgram upload."""
    if isinstance(audio, str):
        with open(audio, "rb") as f:
            audio_bytes = f.read()
    else:
        audio_bytes = _numpy_to_wav_bytes(audio)
    return audio_bytes, "audio/wav"


def _build_deepgram_params(
    language: str,
    keyterms: list[str] | None,
) -> list[tuple[str, str | int | float | bool | None]]:
    """Build Deepgram REST API query params with keyword prompting."""
    if keyterms is None:
        keyterms = _load_keyterms()

    params: list[tuple[str, str | int | float | bool | None]] = [
        ("model", "nova-3"),
        ("language", language),
        ("punctuate", "true"),
        ("smart_format", "true"),
        ("utterances", "true"),
        ("endpointing", "300"),
        ("filler_words", "false"),
        ("numerals", "true"),
    ]
    # Deepgram supports up to _DEEPGRAM_MAX_KEYWORDS keywords per request
    for kt in keyterms[:_DEEPGRAM_MAX_KEYWORDS]:
        params.append(("keywords", f"{kt}:2.0"))
    return params


def _parse_deepgram_utterances(data: dict) -> tuple[list[TranscriptionSegment] | None, float | None]:
    """Extract utterance-level segments from a Deepgram response."""
    raw_utterances = data.get("results", {}).get("utterances", [])
    if not isinstance(raw_utterances, list) or not raw_utterances:
        return None, None

    parsed_segments: list[TranscriptionSegment] = []
    confidences: list[float] = []
    for utterance in raw_utterances:
        if not isinstance(utterance, dict):
            continue
        seg_start = utterance.get("start")
        seg_end = utterance.get("end")
        seg_text = utterance.get("transcript", utterance.get("text", ""))
        if isinstance(seg_start, (int, float)) and isinstance(seg_end, (int, float)):
            cleaned = str(seg_text).strip()
            if cleaned:
                parsed_segments.append(
                    {
                        "start": float(seg_start),
                        "end": float(seg_end),
                        "text": cleaned,
                        "kind": "utterance",
                    }
                )
        seg_confidence = utterance.get("confidence")
        if isinstance(seg_confidence, (int, float)):
            confidences.append(float(seg_confidence))

    if not parsed_segments:
        return None, None
    if not confidences:
        return parsed_segments, None
    return parsed_segments, round(sum(confidences) / len(confidences), 4)


def _parse_deepgram_words(best: dict) -> list[TranscriptionSegment] | None:
    """Extract fallback word-level timing spans from Deepgram output."""
    words = best.get("words", [])
    if not isinstance(words, list) or not words:
        return None

    parsed_segments: list[TranscriptionSegment] = []
    for word_info in words:
        if not isinstance(word_info, dict):
            continue
        w_start = word_info.get("start")
        w_end = word_info.get("end")
        w_word = word_info.get("word", "")
        if isinstance(w_start, (int, float)) and isinstance(w_end, (int, float)):
            cleaned = str(w_word).strip()
            if cleaned:
                parsed_segments.append(
                    {
                        "start": float(w_start),
                        "end": float(w_end),
                        "text": cleaned,
                        "kind": "word",
                    }
                )
    return parsed_segments if parsed_segments else None


def _parse_deepgram_response(
    data: dict,
) -> tuple[str, float, list[TranscriptionSegment] | None]:
    """Extract transcript, confidence, and segments from Deepgram JSON.

    Returns ``("", 0.0, None)`` and logs a warning when the response
    structure is unexpected.
    """
    channels = data.get("results", {}).get("channels", [])
    if not channels:
        logger.warning("Deepgram returned no channels")
        return "", 0.0, None

    alternatives = channels[0].get("alternatives", [])
    if not alternatives:
        logger.warning("Deepgram returned no alternatives")
        return "", 0.0, None

    best = alternatives[0]
    transcript = str(best.get("transcript", "")).strip()
    confidence = float(best.get("confidence", 0.0) or 0.0)

    parsed_segments, utterance_confidence = _parse_deepgram_utterances(data)
    if parsed_segments is None:
        parsed_segments = _parse_deepgram_words(best)
    if not transcript and parsed_segments:
        transcript = " ".join(segment["text"] for segment in parsed_segments).strip()
    if confidence <= 0.0 and utterance_confidence is not None:
        confidence = utterance_confidence

    return transcript, confidence, parsed_segments if parsed_segments else None


def _try_deepgram(
    audio: np.ndarray | str,
    *,
    language: str,
    prompt: str = "",
    keyterms: list[str] | None = None,
) -> "TranscriptionResult | None":
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

    _httpx_errors = (httpx.HTTPError, httpx.StreamError)

    try:
        t0 = time.monotonic()
        audio_bytes, content_type = _prepare_deepgram_audio(audio)
        params = _build_deepgram_params(language, keyterms)

        with httpx.Client(timeout=_DEEPGRAM_REQUEST_TIMEOUT_S) as client:
            resp = client.post(
                _DEEPGRAM_API_URL,
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

        transcript, confidence, segments = _parse_deepgram_response(resp.json())
        if not transcript:
            # No usable transcript — let the caller fall through to next backend
            return None

        return TranscriptionResult(
            text=transcript,
            language=language,
            confidence=round(float(confidence), 4),
            duration_seconds=round(elapsed, 3),
            backend="deepgram-nova3",
            segments=segments,
        )

    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("Deepgram STT attempt failed: %s", exc)
        return None
    except _httpx_errors as exc:
        logger.warning("Deepgram STT network error: %s", exc)
        return None


# Microphone recording


def _init_vad(sample_rate: int) -> tuple[_VadDetector | None, bool]:
    """Initialize Silero VAD detector with graceful fallback.

    Returns ``(detector, use_silero)`` where *use_silero* is ``False``
    when Silero is unavailable and the caller should fall back to RMS.
    """
    vad_detector = None
    use_silero = False
    try:
        from jarvis_engine.stt.vad import get_vad_detector

        vad_detector = cast(_VadDetector, get_vad_detector(sampling_rate=sample_rate))
        use_silero = vad_detector.available
    except (ImportError, OSError, RuntimeError) as exc:
        logger.debug("VAD detector initialization failed: %s", exc)

    if use_silero:
        logger.info("Using Silero VAD for speech detection")
    else:
        logger.warning("Silero VAD not available, falling back to energy-based VAD")
    return vad_detector, use_silero


def _detect_speech(
    chunk: np.ndarray,
    vad_detector: _VadDetector | None,
    use_silero: bool,
    silence_threshold: float,
) -> bool:
    """Return True if *chunk* contains speech using Silero VAD or RMS energy."""
    if use_silero and vad_detector is not None:
        mono = chunk[:, 0] if chunk.ndim > 1 else chunk
        return vad_detector.process_chunk(mono)
    # RMS energy fallback
    rms = float(np.sqrt(np.mean(chunk**2)))
    return rms > silence_threshold


def _capture_audio_loop(
    stream: _AudioReadStream,
    *,
    sample_rate: int,
    max_duration_seconds: float,
    silence_threshold: float,
    silence_duration: float,
    drain_seconds: float,
    vad_detector: _VadDetector | None,
    use_silero: bool,
    pre_speech_pad_seconds: float = _PRE_SPEECH_PAD_S,
    post_speech_pad_seconds: float = _POST_SPEECH_PAD_S,
) -> list[np.ndarray]:
    """Read audio chunks from *stream*, stopping on post-speech silence.

    Returns the list of captured numpy frames.

    RC-3 speech padding: keeps a ring buffer of pre-speech audio so that
    word beginnings are not clipped, and appends a small amount of
    post-speech audio so trailing consonants are preserved.
    """
    # Drain stale audio from OS buffer (e.g. wake word remnants)
    if drain_seconds > 0:
        drain_samples = int(sample_rate * drain_seconds)
        stream.read(drain_samples)
        logger.debug("Drained %.0fms of stale audio", drain_seconds * 1000)

    # Silero VAD works best with 32ms (512-sample) chunks at 16 kHz.
    # RMS fallback uses 100ms chunks for backward compatibility.
    chunk_duration = _SILERO_CHUNK_DURATION_S if use_silero else _RMS_CHUNK_DURATION_S
    samples_per_chunk = int(sample_rate * chunk_duration)
    max_silence_chunks = int(silence_duration / chunk_duration)
    min_recording_chunks = int(_MIN_RECORDING_DURATION_S / chunk_duration)
    max_chunks = int(max_duration_seconds / chunk_duration)

    # RC-3: pre-speech ring buffer (~200ms of audio before speech onset)
    pre_pad_chunks = max(1, int(pre_speech_pad_seconds / chunk_duration))
    pre_speech_buffer: deque[np.ndarray] = deque(maxlen=pre_pad_chunks)

    # RC-3: post-speech padding (~300ms after VAD says speech ended)
    _post_pad_chunks = max(1, int(post_speech_pad_seconds / chunk_duration))  # noqa: F841 — reserved for post-speech padding

    frames: list[np.ndarray] = []
    speech_detected = False
    silence_frames = 0

    for i in range(max_chunks):
        chunk, _ = stream.read(samples_per_chunk)

        is_speech = _detect_speech(
            chunk,
            vad_detector,
            use_silero,
            silence_threshold,
        )

        if is_speech:
            if not speech_detected:
                # RC-3: speech just started -- prepend buffered pre-speech audio
                for buffered in pre_speech_buffer:
                    frames.append(buffered)
                pre_speech_buffer.clear()
            speech_detected = True
            silence_frames = 0
            frames.append(chunk.copy())
        elif speech_detected:
            silence_frames += 1
            frames.append(chunk.copy())  # RC-3: keep post-speech audio
            if silence_frames >= max_silence_chunks and i >= min_recording_chunks:
                logger.debug(
                    "Silence detected after speech, stopping recording "
                    "(%.1f seconds recorded)",
                    (i + 1) * chunk_duration,
                )
                break
        else:
            # No speech yet -- keep filling the pre-speech ring buffer
            pre_speech_buffer.append(chunk.copy())

    return frames


# RC-1: silence timeout defaults by mode
_SILENCE_DURATION_COMMAND = 1.2  # relaxed cutoff to avoid clipping utterance tails
_SILENCE_DURATION_DICTATION = 2.0  # longer pause tolerance for dictation


def record_from_microphone(
    *,
    sample_rate: int = 16000,
    max_duration_seconds: float = 30.0,
    silence_threshold: float = 0.01,
    silence_duration: float = _SILENCE_DURATION_COMMAND,
    drain_seconds: float = 0.3,
    mode: str = "command",
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
        Seconds of continuous silence after speech before stopping
        (default 0.8 for command mode, 2.0 for dictation mode).
    drain_seconds:
        Seconds of audio to read and discard when opening the stream.  This
        flushes stale audio left in the OS audio buffer (e.g. wake word
        remnants) before the actual recording begins.
    mode:
        Recording mode: ``"command"`` (default) uses 0.8s silence timeout
        for snappy command recognition; ``"dictation"`` uses 2.0s to allow
        natural pauses in longer speech.  Only applies when *silence_duration*
        is not explicitly overridden.

    Returns a mono float32 numpy array at the given sample rate.
    Raises RuntimeError if sounddevice is not installed or no microphone
    is available.
    """
    try:
        import sounddevice as sd  # type: ignore[import-not-found,import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "sounddevice is not installed. Install with: pip install sounddevice"
        ) from exc

    # RC-1: apply mode-specific silence duration if caller used the default
    if mode in {"dictation", "conversation"} and silence_duration == _SILENCE_DURATION_COMMAND:
        silence_duration = _SILENCE_DURATION_DICTATION

    vad_detector, use_silero = _init_vad(sample_rate)

    try:
        logger.info(
            "Recording from microphone for up to %.1f seconds at %d Hz (mode=%s)...",
            max_duration_seconds,
            sample_rate,
            mode,
        )

        with sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="float32"
        ) as stream:
            # Adaptive noise floor: calibrate from ambient audio when using
            # RMS fallback (not Silero VAD).  Re-calibrates every 60 seconds.
            if not use_silero:
                silence_threshold = _calibrate_noise_floor(stream, sample_rate)
                logger.info(
                    "Adaptive noise floor: silence_threshold=%.5f",
                    silence_threshold,
                )

            frames = _capture_audio_loop(
                stream,
                sample_rate=sample_rate,
                max_duration_seconds=max_duration_seconds,
                silence_threshold=silence_threshold,
                silence_duration=silence_duration,
                drain_seconds=drain_seconds,
                vad_detector=vad_detector,
                use_silero=use_silero,
            )

        # Reset VAD state for next recording session (stateful model)
        if use_silero and vad_detector is not None:
            vad_detector.reset()

    except OSError as exc:
        # PortAudioError or similar -- microphone not available
        raise RuntimeError(
            f"Microphone recording failed: {exc}. "
            "Check Windows microphone permissions in Settings > Privacy > Microphone."
        ) from exc

    if not frames:
        return np.array([], dtype=np.float32)
    return np.concatenate(frames, axis=0).flatten()
