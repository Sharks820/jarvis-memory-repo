"""Tests for the Silero VAD wrapper module (stt_vad.py).

All tests mock ``silero_vad`` and ``torch`` so they run without actual
model downloads or GPU/CPU inference.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import numpy as np


# ---------------------------------------------------------------------------
# Stub specs for external types not installed in the test environment
# ---------------------------------------------------------------------------

class _TorchModuleStub:
    """Spec stub for the torch module."""

    def set_num_threads(self, n: int) -> None: ...  # noqa: D102
    def FloatTensor(self, data) -> object: ...  # noqa: D102, N802


class _SileroModelStub:
    """Spec stub for a Silero VAD model (callable)."""

    def __call__(self, audio_tensor, sample_rate: int) -> object: ...  # noqa: D102
    def reset_states(self) -> None: ...  # noqa: D102


# ---------------------------------------------------------------------------
# 1. Constructor defaults
# ---------------------------------------------------------------------------

def test_constructor_defaults() -> None:
    """SileroVADDetector stores threshold, onset/offset, and sampling_rate."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector()
    assert d._threshold == 0.4
    assert d._onset_threshold == 0.4
    assert d._offset_threshold == 0.6
    assert d._sampling_rate == 16000
    assert d._model is None
    assert d._in_speech is False


def test_constructor_custom_params() -> None:
    """Constructor accepts custom threshold and sampling_rate."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(threshold=0.3, sampling_rate=8000)
    assert d._threshold == 0.3
    assert d._onset_threshold == 0.3  # inherits from threshold
    assert d._sampling_rate == 8000


def test_constructor_explicit_onset_offset() -> None:
    """Constructor accepts explicit onset and offset thresholds."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.35, offset_threshold=0.65)
    assert d._onset_threshold == 0.35
    assert d._offset_threshold == 0.65


# ---------------------------------------------------------------------------
# 2. _ensure_model loads via silero_vad and sets torch threads
# ---------------------------------------------------------------------------

def test_ensure_model_loads_silero_and_sets_threads() -> None:
    """_ensure_model loads Silero VAD model and calls torch.set_num_threads(1)."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector()
    mock_model = MagicMock(spec=_SileroModelStub)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_load = MagicMock(return_value=mock_model)

    with patch.dict("sys.modules", {"torch": mock_torch, "silero_vad": MagicMock(load_silero_vad=mock_load)}):
        d._ensure_model()

    mock_torch.set_num_threads.assert_called_once_with(1)
    mock_load.assert_called_once()
    assert d._model is mock_model


def test_ensure_model_called_once() -> None:
    """_ensure_model is a no-op after the model is already loaded."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector()
    d._model = MagicMock(spec=_SileroModelStub)  # pretend already loaded

    # Should not attempt to import again
    d._ensure_model()
    assert d._model is not None  # unchanged


# ---------------------------------------------------------------------------
# 3. is_speech with high confidence -> True
# ---------------------------------------------------------------------------

def test_is_speech_high_confidence_returns_true() -> None:
    """is_speech returns True when model confidence > onset_threshold."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.4, offset_threshold=0.6)
    mock_model = MagicMock(spec=_SileroModelStub)
    mock_model.return_value.item.return_value = 0.85
    d._model = mock_model

    chunk = np.random.randn(512).astype(np.float32)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    with patch.dict("sys.modules", {"torch": mock_torch}):
        result = d.is_speech(chunk)

    assert result is True
    mock_torch.FloatTensor.assert_called_once()
    mock_model.assert_called_once_with("fake_tensor", 16000)


# ---------------------------------------------------------------------------
# 4. is_speech with low confidence -> False
# ---------------------------------------------------------------------------

def test_is_speech_low_confidence_returns_false() -> None:
    """is_speech returns False when model confidence < onset_threshold."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.4, offset_threshold=0.6)
    mock_model = MagicMock(spec=_SileroModelStub)
    mock_model.return_value.item.return_value = 0.2
    d._model = mock_model

    chunk = np.random.randn(512).astype(np.float32)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    with patch.dict("sys.modules", {"torch": mock_torch}):
        result = d.is_speech(chunk)

    assert result is False


# ---------------------------------------------------------------------------
# 5. get_confidence returns raw float value
# ---------------------------------------------------------------------------

def test_get_confidence_returns_raw_value() -> None:
    """get_confidence returns the raw probability from the model."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector()
    mock_model = MagicMock(spec=_SileroModelStub)
    mock_model.return_value.item.return_value = 0.73
    d._model = mock_model

    chunk = np.random.randn(512).astype(np.float32)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    with patch.dict("sys.modules", {"torch": mock_torch}):
        conf = d.get_confidence(chunk)

    assert conf == 0.73


# ---------------------------------------------------------------------------
# 6. process_chunk with 1280-sample chunk splits correctly
# ---------------------------------------------------------------------------

def test_process_chunk_splits_large_chunk() -> None:
    """process_chunk splits 1280-sample chunk into 2x 512-sample windows."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.5, offset_threshold=0.7)
    mock_model = MagicMock(spec=_SileroModelStub)
    # First sub-window: low confidence, second: high confidence
    mock_model.return_value.item.side_effect = [0.2, 0.8]
    d._model = mock_model

    chunk = np.random.randn(1280).astype(np.float32)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    with patch.dict("sys.modules", {"torch": mock_torch}):
        result = d.process_chunk(chunk)

    # Max confidence is 0.8 > 0.5 onset_threshold -> True
    assert result is True
    # Should be called twice (1280 // 512 = 2, with 256 leftover not processed)
    assert mock_model.call_count == 2


def test_process_chunk_all_low_confidence() -> None:
    """process_chunk returns False when all sub-windows have low confidence."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.5, offset_threshold=0.7)
    mock_model = MagicMock(spec=_SileroModelStub)
    mock_model.return_value.item.side_effect = [0.1, 0.3]
    d._model = mock_model

    chunk = np.random.randn(1280).astype(np.float32)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    with patch.dict("sys.modules", {"torch": mock_torch}):
        result = d.process_chunk(chunk)

    assert result is False


def test_process_chunk_small_chunk_delegates_to_is_speech() -> None:
    """process_chunk with <=512 samples delegates to is_speech."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.4, offset_threshold=0.6)
    mock_model = MagicMock(spec=_SileroModelStub)
    mock_model.return_value.item.return_value = 0.9
    d._model = mock_model

    chunk = np.random.randn(512).astype(np.float32)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    with patch.dict("sys.modules", {"torch": mock_torch}):
        result = d.process_chunk(chunk)

    assert result is True
    # Only one model call (direct is_speech, no splitting)
    assert mock_model.call_count == 1


# ---------------------------------------------------------------------------
# 7. reset calls model.reset_states()
# ---------------------------------------------------------------------------

def test_reset_calls_model_reset_states() -> None:
    """reset() calls model.reset_states() when model is loaded."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector()
    mock_model = MagicMock(spec=_SileroModelStub)
    d._model = mock_model
    d._in_speech = True  # simulate active speech state

    d.reset()

    mock_model.reset_states.assert_called_once()
    assert d._in_speech is False  # hysteresis state also reset


def test_reset_noop_when_model_not_loaded() -> None:
    """reset() is a no-op when model hasn't been loaded (but clears hysteresis)."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector()
    d._in_speech = True
    assert d._model is None

    # Should not raise
    d.reset()
    assert d._in_speech is False


# ---------------------------------------------------------------------------
# 8. Graceful degradation when silero_vad import fails
# ---------------------------------------------------------------------------

def test_graceful_degradation_no_silero() -> None:
    """When silero_vad is not installed, is_speech returns False, get_confidence returns 0.0."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector()
    # Simulate import failure by leaving _model as None and patching _ensure_model
    # to do nothing (simulating the ImportError path)
    d._ensure_model = lambda: None  # type: ignore[assignment]
    d._model = None

    chunk = np.random.randn(512).astype(np.float32)

    assert d.is_speech(chunk) is False
    assert d.get_confidence(chunk) == 0.0
    assert d.process_chunk(chunk) is False


# ---------------------------------------------------------------------------
# 9. available property
# ---------------------------------------------------------------------------

def test_available_when_both_installed() -> None:
    """available returns True when torch and silero_vad are importable."""
    from jarvis_engine.stt import vad as stt_vad

    # Reset cached values
    stt_vad._torch_available = None
    stt_vad._silero_available = None

    d = stt_vad.SileroVADDetector()

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_silero = MagicMock()

    with patch.dict("sys.modules", {"torch": mock_torch, "silero_vad": mock_silero}):
        # Force re-check
        stt_vad._torch_available = None
        stt_vad._silero_available = None
        result = d.available

    assert result is True


def test_available_when_torch_missing() -> None:
    """available returns False when torch is not importable."""
    from jarvis_engine.stt import vad as stt_vad

    d = stt_vad.SileroVADDetector()

    # Simulate torch not available
    stt_vad._torch_available = False
    stt_vad._silero_available = True

    assert d.available is False

    # Reset
    stt_vad._torch_available = None
    stt_vad._silero_available = None


def test_available_when_silero_missing() -> None:
    """available returns False when silero_vad is not importable."""
    from jarvis_engine.stt import vad as stt_vad

    d = stt_vad.SileroVADDetector()

    # Simulate silero not available
    stt_vad._torch_available = True
    stt_vad._silero_available = False

    assert d.available is False

    # Reset
    stt_vad._torch_available = None
    stt_vad._silero_available = None


# ---------------------------------------------------------------------------
# 10. get_vad_detector singleton
# ---------------------------------------------------------------------------

def test_get_vad_detector_returns_singleton() -> None:
    """get_vad_detector returns the same instance on repeated calls."""
    from jarvis_engine.stt import vad as stt_vad

    # Reset singleton
    stt_vad._vad_instance = None

    d1 = stt_vad.get_vad_detector()
    d2 = stt_vad.get_vad_detector()

    assert d1 is d2

    # Clean up
    stt_vad._vad_instance = None


# ---------------------------------------------------------------------------
# 11. process_chunk with exact 1024-sample chunk (2 full windows)
# ---------------------------------------------------------------------------

def test_process_chunk_exact_two_windows() -> None:
    """process_chunk with 1024 samples processes exactly 2 windows."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.5, offset_threshold=0.7)
    mock_model = MagicMock(spec=_SileroModelStub)
    mock_model.return_value.item.side_effect = [0.6, 0.4]
    d._model = mock_model

    chunk = np.random.randn(1024).astype(np.float32)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    with patch.dict("sys.modules", {"torch": mock_torch}):
        result = d.process_chunk(chunk)

    assert result is True  # max(0.6, 0.4) = 0.6 > 0.5 onset_threshold
    assert mock_model.call_count == 2


# ---------------------------------------------------------------------------
# 12. get_confidence handles model exception gracefully
# ---------------------------------------------------------------------------

def test_get_confidence_handles_model_exception() -> None:
    """get_confidence returns 0.0 if model inference raises."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector()
    mock_model = MagicMock(spec=_SileroModelStub)
    d._model = mock_model

    chunk = np.random.randn(512).astype(np.float32)

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.side_effect = RuntimeError("tensor error")

    with patch.dict("sys.modules", {"torch": mock_torch}):
        conf = d.get_confidence(chunk)

    assert conf == 0.0


# ---------------------------------------------------------------------------
# 13. RC-2: Hysteresis -- onset then offset behavior
# ---------------------------------------------------------------------------

def test_hysteresis_onset_then_offset() -> None:
    """Speech stays True after onset until confidence drops below (1-offset)."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.4, offset_threshold=0.6)
    mock_model = MagicMock(spec=_SileroModelStub)
    d._model = mock_model

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    chunk = np.random.randn(512).astype(np.float32)

    with patch.dict("sys.modules", {"torch": mock_torch}):
        # Start with high confidence -> speech onset
        mock_model.return_value.item.return_value = 0.7
        assert d.is_speech(chunk) is True

        # Conf 0.65 -- still above offset_threshold 0.6, stays in speech
        mock_model.return_value.item.return_value = 0.65
        assert d.is_speech(chunk) is True

        # Dip to 0.45 -- below offset_threshold 0.6, exits speech
        mock_model.return_value.item.return_value = 0.45
        assert d.is_speech(chunk) is False


# ---------------------------------------------------------------------------
# 14. RC-2: Onset does not trigger on noise below threshold
# ---------------------------------------------------------------------------

def test_onset_rejects_noise() -> None:
    """Speech is not detected when confidence stays below onset_threshold."""
    from jarvis_engine.stt.vad import SileroVADDetector

    d = SileroVADDetector(onset_threshold=0.4, offset_threshold=0.6)
    mock_model = MagicMock(spec=_SileroModelStub)
    d._model = mock_model

    mock_torch = MagicMock(spec=_TorchModuleStub)
    mock_torch.FloatTensor.return_value = "fake_tensor"

    chunk = np.random.randn(512).astype(np.float32)

    with patch.dict("sys.modules", {"torch": mock_torch}):
        # Confidence just below onset_threshold
        mock_model.return_value.item.return_value = 0.35
        assert d.is_speech(chunk) is False
        assert d._in_speech is False


# ---------------------------------------------------------------------------
# 15. get_vad_detector accepts onset/offset params
# ---------------------------------------------------------------------------

def test_get_vad_detector_onset_offset_params() -> None:
    """get_vad_detector passes onset/offset thresholds to detector."""
    from jarvis_engine.stt import vad as stt_vad

    stt_vad._vad_instance = None
    try:
        d = stt_vad.get_vad_detector(
            onset_threshold=0.35,
            offset_threshold=0.65,
        )
        assert d._onset_threshold == 0.35
        assert d._offset_threshold == 0.65
    finally:
        stt_vad._vad_instance = None
