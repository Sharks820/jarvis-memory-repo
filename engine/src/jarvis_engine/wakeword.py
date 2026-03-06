"""Wake word detection using openwakeword ML model for hands-free voice activation."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)


class WakeWordDetector:
    """Detect 'hey_jarvis' wake word using openwakeword with sounddevice input."""

    def __init__(
        self,
        threshold: float = 0.5,
        model_name: str = "hey_jarvis",
        cooldown_seconds: float = 2.0,
    ) -> None:
        self._threshold = threshold
        self._model_name = model_name
        self._cooldown_seconds = cooldown_seconds
        self._model = None
        self._vad = None  # SileroVADDetector (set in start())
        self._vad_available = False
        self._stop_event = threading.Event()
        self._stream = None  # Active mic stream (for pause/resume)
        self._stream_lock = threading.Lock()

    def _load_model(self) -> None:
        """Lazy-load the openwakeword model."""
        from openwakeword.model import Model  # type: ignore[import-untyped]

        self._model = Model(wakeword_models=[self._model_name], inference_framework="onnx")

    def start(
        self,
        on_detected: Callable,
        stop_event: threading.Event | None = None,
        mic_lock: threading.Lock | None = None,
    ) -> None:
        """Main detection loop using sounddevice.

        Args:
            on_detected: Callback invoked when wake word is detected.
            stop_event: External event to signal stop. Uses internal if None.
            mic_lock: Optional lock shared with STT to prevent mic conflicts.
        """
        if stop_event is not None:
            self._stop_event = stop_event

        try:
            self._load_model()
        except ImportError:
            logger.warning(
                "openwakeword not installed. Wake word detection unavailable. "
                "Install with: pip install openwakeword"
            )
            return
        except Exception as exc:
            logger.error("Failed to load wake word model: %s", exc)
            return

        # Initialize Silero VAD (lower threshold for wake word sensitivity)
        try:
            from jarvis_engine.stt_vad import SileroVADDetector
            self._vad = SileroVADDetector(threshold=0.3)
            self._vad_available = self._vad.available
        except Exception as exc:
            logger.debug("Silero VAD init failed for wakeword: %s", exc)
            self._vad = None
            self._vad_available = False

        if self._vad_available:
            logger.info("Wake word using Silero VAD pre-filter (threshold=0.3)")
        else:
            logger.warning(
                "Silero VAD not available for wake word, falling back to RMS energy"
            )

        try:
            import sounddevice as sd  # type: ignore[import-untyped]
        except ImportError:
            logger.warning(
                "sounddevice not installed. Wake word detection unavailable. "
                "Install with: pip install sounddevice"
            )
            return

        chunk_size = 1280  # frames at 16kHz

        try:
            with self._stream_lock:
                self._stream = sd.InputStream(
                    samplerate=16000,
                    channels=1,
                    dtype="float32",
                    blocksize=chunk_size,
                )
                self._stream.start()
            logger.info("Wake word detection started (model=%s, threshold=%.2f)",
                         self._model_name, self._threshold)

            _was_silent = False

            while not self._stop_event.is_set():
                with self._stream_lock:
                    stream = self._stream
                if stream is None:
                    # Stream paused (STT recording in progress) — wait briefly
                    time.sleep(0.1)
                    continue

                if mic_lock is not None:
                    if not mic_lock.acquire(timeout=60):
                        logger.warning("Mic lock acquisition timed out")
                        continue  # Skip this detection cycle

                try:
                    try:
                        audio_data, overflowed = stream.read(chunk_size)
                    except Exception as read_exc:
                        logger.debug("Wakeword stream read failed (may be closed): %s", read_exc)
                        continue
                finally:
                    if mic_lock is not None:
                        mic_lock.release()

                if overflowed:
                    continue

                # Convert float32 [-1,1] to int16 for openwakeword
                audio_int16 = np.clip(audio_data[:, 0] * 32767, -32768, 32767).astype(np.int16)

                # Pre-filter: Silero VAD (ML-based) or RMS energy fallback
                audio_float = audio_data[:, 0]  # mono channel, already float32
                if self._vad_available and self._vad is not None:
                    # Silero VAD path: process_chunk handles 1280-sample via sub-windowing
                    has_speech = self._vad.process_chunk(audio_float)
                    if not has_speech:
                        _was_silent = True
                        continue
                else:
                    # RMS energy fallback
                    rms = float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2)) / 32767.0)
                    if rms < 0.005:
                        _was_silent = True
                        continue  # Silence, skip ML inference

                # Reset prediction buffer when transitioning from silence to speech
                # to prevent stale scores from causing false positives
                if _was_silent:
                    self._model.reset()
                    _was_silent = False

                self._model.predict(audio_int16)

                # Only check the configured wake word model
                target_key = self._model_name
                if target_key in self._model.prediction_buffer:
                    scores = list(self._model.prediction_buffer[target_key])
                    # Score smoothing: require at least 3 frames and average
                    # of last 3 above threshold to reduce false positives
                    if len(scores) >= 3 and sum(scores[-3:]) / 3 > self._threshold:
                        logger.info("Wake word detected! (avg_score=%.3f)", sum(scores[-3:]) / 3)

                        # Reset prediction buffer and VAD state
                        self._model.reset()
                        if self._vad is not None:
                            self._vad.reset()

                        try:
                            on_detected()
                        except Exception as cb_exc:
                            logger.error("Wake word callback error: %s", cb_exc)

                        # Cooldown to prevent rapid re-triggers
                        time.sleep(self._cooldown_seconds)

                        # Drain stale audio that accumulated during callback + cooldown
                        # to prevent false re-triggers from buffered wake word echo.
                        # Use available frames count to avoid blocking on read().
                        with self._stream_lock:
                            drain_stream = self._stream
                        if drain_stream is not None:
                            try:
                                avail = drain_stream.read_available
                                if avail > 0:
                                    drain_stream.read(avail)
                            except Exception as drain_exc:
                                logger.debug("Stream drain failed (may be closed): %s", drain_exc)

                        # Reset silence state so first chunk after drain
                        # goes through the silence-to-speech transition path
                        _was_silent = True
        except Exception as exc:
            logger.error("Wake word detection error: %s", exc)
        finally:
            with self._stream_lock:
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception as stream_exc:
                        logger.debug("Failed to close wakeword stream: %s", stream_exc)
                    self._stream = None
            logger.info("Wake word detection stopped.")

    def pause(self) -> None:
        """Stop the mic stream so another consumer (STT recording) can use it.

        Must be called before opening a second mic stream to avoid dual-stream
        conflicts on Windows WASAPI.  Call ``resume()`` afterwards.
        """
        with self._stream_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                    logger.debug("Wake word mic stream paused")
                except Exception as exc:
                    logger.debug("Error pausing wake word stream: %s", exc)
                self._stream = None

    def resume(self, sd_module: object | None = None) -> None:
        """Re-open the mic stream after STT recording completes.

        *sd_module* must be the ``sounddevice`` module (passed in to avoid a
        top-level import).
        """
        with self._stream_lock:
            if self._stream is not None:
                return  # Already running
            if sd_module is None:
                try:
                    import sounddevice as _sd  # type: ignore[import-untyped]
                    sd_module = _sd
                except ImportError:
                    return
            self._stream = sd_module.InputStream(
                samplerate=16000, channels=1, dtype="float32", blocksize=1280,
            )
            self._stream.start()

            # Reset VAD state for clean slate after pause
            if self._vad is not None:
                self._vad.reset()

            logger.debug("Wake word mic stream resumed")

    def stop(self) -> None:
        """Signal the detection loop to stop."""
        self._stop_event.set()
