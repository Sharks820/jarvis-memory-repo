"""Tests for Parakeet TDT 0.6B backend (_try_parakeet)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _reset_parakeet_global():
    """Reset the _parakeet_model singleton so each test starts clean."""
    import jarvis_engine.stt as stt_mod
    stt_mod._singletons.pop("parakeet", None)


# ---------------------------------------------------------------------------
# P1/P4/P8. test_try_parakeet result assertions (parametrized)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mock_text, expected_text, expected_confidence",
    [
        pytest.param("Hello world", "Hello world", 0.75, id="success_baseline_confidence"),
        pytest.param("", "", 0.0, id="empty_text_zero_confidence"),
        pytest.param("some transcription", "some transcription", 0.75, id="non_empty_baseline_confidence"),
    ],
)
def test_try_parakeet_result(mock_text, expected_text, expected_confidence):
    """_try_parakeet returns correct text, confidence, and backend for various inputs."""
    _reset_parakeet_global()

    from jarvis_engine.stt import _try_parakeet

    mock_model = MagicMock()
    mock_model.with_timestamps.return_value = mock_model
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: mock_text
    mock_result.__bool__ = lambda self: True
    mock_result.tokens = None  # No log probs -> baseline confidence
    mock_model.recognize.return_value = mock_result

    mock_onnx_asr = MagicMock()
    mock_onnx_asr.load_model.return_value = mock_model

    with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
        result = _try_parakeet(np.zeros(16000, dtype=np.float32), language="en")

    assert result is not None
    assert result.text == expected_text
    assert result.backend == "parakeet-tdt"
    assert result.confidence == expected_confidence
    assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# P2. test_try_parakeet_import_error
# ---------------------------------------------------------------------------

def test_try_parakeet_import_error():
    """When onnx_asr is not installed, _try_parakeet returns None gracefully."""
    _reset_parakeet_global()

    import jarvis_engine.stt as stt_mod

    # Remove onnx_asr from sys.modules if present, and make import fail
    original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def failing_import(name, *args, **kwargs):
        if name == "onnx_asr":
            raise ImportError("No module named 'onnx_asr'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=failing_import):
        result = stt_mod._try_parakeet(np.zeros(16000, dtype=np.float32), language="en")

    assert result is None


# ---------------------------------------------------------------------------
# P3. test_try_parakeet_model_error
# ---------------------------------------------------------------------------

def test_try_parakeet_model_error():
    """When load_model raises RuntimeError, _try_parakeet returns None."""
    _reset_parakeet_global()

    from jarvis_engine.stt import _try_parakeet

    mock_onnx_asr = MagicMock()
    mock_onnx_asr.load_model.side_effect = RuntimeError("Model download failed")

    with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
        result = _try_parakeet(np.zeros(16000, dtype=np.float32), language="en")

    assert result is None


# ---------------------------------------------------------------------------
# P5. test_try_parakeet_with_numpy_array
# ---------------------------------------------------------------------------

def test_try_parakeet_with_numpy_array():
    """Numpy array input should call recognize(audio, sample_rate=16000)."""
    _reset_parakeet_global()

    from jarvis_engine.stt import _try_parakeet

    mock_model = MagicMock()
    mock_model.with_timestamps.return_value = mock_model
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "test audio"
    mock_result.__bool__ = lambda self: True
    mock_result.tokens = None
    mock_model.recognize.return_value = mock_result

    mock_onnx_asr = MagicMock()
    mock_onnx_asr.load_model.return_value = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
        _try_parakeet(audio, language="en")

    # Verify recognize was called with numpy array and sample_rate
    mock_model.recognize.assert_called_once()
    call_args = mock_model.recognize.call_args
    np.testing.assert_array_equal(call_args[0][0], audio)
    assert call_args[1]["sample_rate"] == 16000


# ---------------------------------------------------------------------------
# P6. test_try_parakeet_with_file_path
# ---------------------------------------------------------------------------

def test_try_parakeet_with_file_path():
    """String file path input should call recognize(path) without sample_rate."""
    _reset_parakeet_global()

    from jarvis_engine.stt import _try_parakeet

    mock_model = MagicMock()
    mock_model.with_timestamps.return_value = mock_model
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "file audio"
    mock_result.__bool__ = lambda self: True
    mock_result.tokens = None
    mock_model.recognize.return_value = mock_result

    mock_onnx_asr = MagicMock()
    mock_onnx_asr.load_model.return_value = mock_model

    with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
        _try_parakeet("/tmp/test_audio.wav", language="en")

    # Verify recognize was called with just the path (no sample_rate kwarg)
    mock_model.recognize.assert_called_once_with("/tmp/test_audio.wav")


# ---------------------------------------------------------------------------
# P7. test_try_parakeet_lazy_model_load
# ---------------------------------------------------------------------------

def test_try_parakeet_lazy_model_load():
    """Calling _try_parakeet twice should only call load_model once (singleton)."""
    _reset_parakeet_global()

    from jarvis_engine.stt import _try_parakeet

    mock_model = MagicMock()
    mock_model.with_timestamps.return_value = mock_model
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "hello"
    mock_result.__bool__ = lambda self: True
    mock_result.tokens = None
    mock_model.recognize.return_value = mock_result

    mock_onnx_asr = MagicMock()
    mock_onnx_asr.load_model.return_value = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
        result1 = _try_parakeet(audio, language="en")
        result2 = _try_parakeet(audio, language="en")

    assert result1 is not None
    assert result2 is not None
    # load_model should only be called once due to lazy singleton
    mock_onnx_asr.load_model.assert_called_once_with("nemo-parakeet-tdt-0.6b-v2")
