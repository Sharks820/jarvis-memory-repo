"""Silero VAD wrapper for real-time speech/silence detection.

Provides :class:`SileroVADDetector` which wraps the Silero VAD model
(https://github.com/snakers4/silero-vad) behind a simple NumPy-based API
suitable for use in ``record_from_microphone()`` and wake-word detection.

All heavy dependencies (``torch``, ``silero_vad``) are lazily imported so
this module can be imported safely even when they are not installed.
"""

from __future__ import annotations

__all__ = ["SileroVADDetector", "get_vad_detector"]

import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Availability check (no side-effects on import)
# ---------------------------------------------------------------------------

_torch_available: bool | None = None
_silero_available: bool | None = None
_availability_lock = threading.Lock()


def _check_torch() -> bool:
    global _torch_available
    if _torch_available is None:
        with _availability_lock:
            if _torch_available is None:
                try:
                    import torch  # noqa: F401

                    _torch_available = True
                except ImportError:
                    _torch_available = False
    return _torch_available


def _check_silero() -> bool:
    global _silero_available
    if _silero_available is None:
        with _availability_lock:
            if _silero_available is None:
                try:
                    import silero_vad  # type: ignore[import-not-found]  # noqa: F401

                    _silero_available = True
                except ImportError:
                    _silero_available = False
    return _silero_available


# ---------------------------------------------------------------------------
# SileroVADDetector
# ---------------------------------------------------------------------------

# Silero VAD operates on fixed-size windows.  At 16 kHz the recommended
# window is 512 samples (32 ms).
_SILERO_WINDOW_SAMPLES = 512


class SileroVADDetector:
    """Thin wrapper around the Silero VAD model.

    Parameters
    ----------
    threshold:
        Speech probability above which a chunk is considered speech.
        Used as both onset and offset threshold when *onset_threshold*
        and *offset_threshold* are not provided.  Kept for backward
        compatibility.
    onset_threshold:
        Speech probability above which speech **starts** being detected.
        More sensitive (lower) than *offset_threshold* to catch soft
        speech beginnings.  Defaults to 0.4.
    offset_threshold:
        Speech probability below which speech is considered **ended**.
        Higher than *onset_threshold* to avoid premature cutoff on
        momentary dips.  Defaults to 0.6.
    sampling_rate:
        Audio sampling rate in Hz.  Silero supports 8000 and 16000.
    """

    def __init__(
        self,
        threshold: float = 0.4,
        sampling_rate: int = 16000,
        *,
        onset_threshold: float | None = None,
        offset_threshold: float | None = None,
    ) -> None:
        self._threshold = threshold
        # RC-2: split onset/offset thresholds for hysteresis
        self._onset_threshold = (
            onset_threshold if onset_threshold is not None else threshold
        )
        self._offset_threshold = (
            offset_threshold if offset_threshold is not None else 0.6
        )
        self._sampling_rate = sampling_rate
        self._model = None  # lazy-loaded
        self._threads_set = False
        # Track whether we are currently in a speech region (for hysteresis)
        self._in_speech = False

    # -- lazy model loading --------------------------------------------------

    def _ensure_model(self) -> None:
        """Load the Silero VAD model on first use.

        Sets ``torch.set_num_threads(1)`` to prevent thread contention with
        sentence-transformers / other PyTorch users (Silero recommendation).
        """
        if self._model is not None:
            return
        try:
            import torch
            from silero_vad import load_silero_vad
        except ImportError:
            logger.warning(
                "silero-vad or torch not installed -- "
                "SileroVADDetector will return fallback values"
            )
            return

        if not self._threads_set:
            torch.set_num_threads(1)
            self._threads_set = True

        self._model = load_silero_vad()
        logger.info(
            "Silero VAD model loaded (onset=%.2f, offset=%.2f)",
            self._onset_threshold,
            self._offset_threshold,
        )

    # -- public API -----------------------------------------------------------

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Return ``True`` if *audio_chunk* contains speech.

        Uses hysteresis: once speech is detected (confidence > onset_threshold),
        it remains detected until confidence drops below offset_threshold.
        This prevents rapid toggling on borderline audio.

        *audio_chunk* should be a 1-D float32 array of 512 samples at
        16 kHz.  If the model is unavailable the method returns ``False``.
        """
        conf = self.get_confidence(audio_chunk)
        if self._in_speech:
            # Already in speech -- require stronger silence to exit
            if conf < (1.0 - self._offset_threshold):
                self._in_speech = False
            return self._in_speech
        else:
            # Not in speech -- use more sensitive onset
            if conf > self._onset_threshold:
                self._in_speech = True
            return self._in_speech

    def get_confidence(self, audio_chunk: np.ndarray) -> float:
        """Return the raw speech probability for *audio_chunk*.

        Returns ``0.0`` when the model is not available.
        """
        self._ensure_model()
        if self._model is None:
            return 0.0
        try:
            import torch

            tensor = torch.FloatTensor(audio_chunk)
            return float(self._model(tensor, self._sampling_rate).item())
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.debug("SileroVAD inference error: %s", exc)
            return 0.0

    def process_chunk(self, audio_chunk: np.ndarray) -> bool:
        """Check speech presence in a chunk of *any* size.

        Splits the chunk into 512-sample sub-windows and returns ``True``
        if the **maximum** confidence across sub-windows exceeds the
        onset threshold (or offset threshold if currently in speech).
        This handles e.g. wakeword.py's 1280-frame chunks.
        """
        self._ensure_model()
        if self._model is None:
            return False

        length = len(audio_chunk)
        if length <= _SILERO_WINDOW_SAMPLES:
            return self.is_speech(audio_chunk)

        max_conf = 0.0
        offset = 0
        while offset + _SILERO_WINDOW_SAMPLES <= length:
            sub = audio_chunk[offset : offset + _SILERO_WINDOW_SAMPLES]
            conf = self.get_confidence(sub)
            if conf > max_conf:
                max_conf = conf
            offset += _SILERO_WINDOW_SAMPLES

        # Apply hysteresis at the chunk level
        active_threshold = (
            (1.0 - self._offset_threshold) if self._in_speech else self._onset_threshold
        )
        if max_conf > active_threshold:
            self._in_speech = True
        elif self._in_speech and max_conf < (1.0 - self._offset_threshold):
            self._in_speech = False

        return max_conf > active_threshold

    def reset(self) -> None:
        """Reset internal model state between utterances.

        Silero VAD is stateful (recurrent layers carry over across calls).
        Call this after each recording session / wake-word activation.
        """
        self._in_speech = False
        if self._model is not None:
            try:
                self._model.reset_states()
            except (RuntimeError, AttributeError) as exc:
                logger.debug("SileroVAD reset error: %s", exc)

    @property
    def available(self) -> bool:
        """``True`` when both ``torch`` and ``silero_vad`` are importable."""
        return _check_torch() and _check_silero()


# ---------------------------------------------------------------------------
# Module-level singleton (same pattern as _local_stt_instance in stt.py)
# ---------------------------------------------------------------------------

_vad_instance: SileroVADDetector | None = None
_vad_lock = threading.Lock()


def get_vad_detector(
    threshold: float = 0.4,
    sampling_rate: int = 16000,
    *,
    onset_threshold: float | None = None,
    offset_threshold: float | None = None,
) -> SileroVADDetector:
    """Return a shared :class:`SileroVADDetector` singleton.

    Thread-safe.  The first call creates the detector; subsequent calls
    return the same instance.
    """
    global _vad_instance
    if _vad_instance is None:
        with _vad_lock:
            if _vad_instance is None:
                _vad_instance = SileroVADDetector(
                    threshold=threshold,
                    sampling_rate=sampling_rate,
                    onset_threshold=onset_threshold,
                    offset_threshold=offset_threshold,
                )
    return _vad_instance
