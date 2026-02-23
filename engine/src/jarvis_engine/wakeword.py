"""Wake word detection using openwakeword ML model for hands-free voice activation."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)


class WakeWordDetector:
    """Detect 'hey_jarvis' wake word using openwakeword with sounddevice input."""

    def __init__(self, threshold: float = 0.5, model_name: str = "hey_jarvis") -> None:
        self._threshold = threshold
        self._model_name = model_name
        self._model = None
        self._stop_event = threading.Event()

    def _load_model(self) -> None:
        """Lazy-load the openwakeword model."""
        from openwakeword.model import Model  # type: ignore[import-untyped]

        self._model = Model(inference_framework="onnx")

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
            stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype="float32",
                blocksize=chunk_size,
            )
        except Exception as exc:
            logger.error("Failed to open audio stream: %s", exc)
            return

        stream.start()
        logger.info("Wake word detection started (model=%s, threshold=%.2f)",
                     self._model_name, self._threshold)

        try:
            while not self._stop_event.is_set():
                if mic_lock is not None:
                    mic_lock.acquire()

                try:
                    audio_data, overflowed = stream.read(chunk_size)
                finally:
                    if mic_lock is not None:
                        mic_lock.release()

                if overflowed:
                    continue

                # Convert float32 [-1,1] to int16 for openwakeword
                audio_int16 = (audio_data[:, 0] * 32767).astype(np.int16)

                self._model.predict(audio_int16)

                # Check detection scores
                for key in self._model.prediction_buffer.keys():
                    scores = list(self._model.prediction_buffer[key])
                    if scores and scores[-1] > self._threshold:
                        logger.info("Wake word detected! (score=%.3f)", scores[-1])

                        # Release mic before callback
                        if mic_lock is not None:
                            mic_lock.acquire()
                        try:
                            self._model.reset()
                        finally:
                            if mic_lock is not None:
                                mic_lock.release()

                        on_detected()
                        break
        except Exception as exc:
            logger.error("Wake word detection error: %s", exc)
        finally:
            stream.stop()
            stream.close()
            logger.info("Wake word detection stopped.")

    def stop(self) -> None:
        """Signal the detection loop to stop."""
        self._stop_event.set()
