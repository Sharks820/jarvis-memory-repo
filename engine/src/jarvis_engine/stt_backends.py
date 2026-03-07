"""STT backend implementations -- individual transcription providers.

Extracted from ``stt.py`` for separation of concerns.  Each backend
function is a standalone helper that attempts transcription via one
provider and returns a :class:`TranscriptionResult` (or ``None`` on
failure) so the caller can fall through to the next backend.

Functions in this module are re-exported by ``stt.py`` to preserve
backward-compatible ``from jarvis_engine.stt import ...`` paths.
"""

from __future__ import annotations

import io
import logging
import os
import struct
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared WAV conversion utility
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


# ---------------------------------------------------------------------------
# Keyterm loading for Deepgram prompting
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Deepgram Nova-3 STT (cloud, keyterm prompting)
# ---------------------------------------------------------------------------

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
    # Import TranscriptionResult lazily to avoid circular imports at
    # module load time (stt.py imports from this module).
    from jarvis_engine.stt import TranscriptionResult

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

    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("Deepgram STT attempt failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 -- httpx exceptions (lazily imported); re-raises others
        if type(exc).__module__.startswith("httpx"):
            logger.warning("Deepgram STT network error: %s", exc)
            return None
        raise


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
    except (ImportError, OSError, RuntimeError) as exc:
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

    except OSError as exc:
        # PortAudioError or similar -- microphone not available
        raise RuntimeError(
            f"Microphone recording failed: {exc}. "
            "Check Windows microphone permissions in Settings > Privacy > Microphone."
        ) from exc

    if not frames:
        return np.array([], dtype=np.float32)
    return np.concatenate(frames, axis=0).flatten()
