"""Tests for record_from_microphone VAD integration (both RMS and Silero)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np


# ---------------------------------------------------------------------------
# 62. VAD: record_from_microphone stops early on silence after speech
# ---------------------------------------------------------------------------

def test_record_microphone_vad_stops_on_silence_after_speech() -> None:
    """record_from_microphone stops early when silence follows speech."""
    from jarvis_engine.stt import record_from_microphone

    call_count = [0]
    # First 3 chunks: speech (RMS > threshold), next 25 chunks: silence
    # With silence_duration=2.0 and chunk_duration=0.1, need 20 silence chunks
    def mock_read(n):
        call_count[0] += 1
        if call_count[0] <= 3:
            # Speech chunk (loud signal)
            chunk = np.full((n, 1), 0.5, dtype=np.float32)
        else:
            # Silence chunk
            chunk = np.zeros((n, 1), dtype=np.float32)
        return chunk, False

    mock_stream = MagicMock()
    mock_stream.read = mock_read
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_sd if name == "sounddevice" else __import__(name, *a, **kw)):
        audio = record_from_microphone(
            max_duration_seconds=30.0,
            silence_threshold=0.01,
            silence_duration=2.0,
        )

    # 3 speech chunks + 20 silence chunks = 23 total (not 300 for 30s)
    assert call_count[0] == 23
    assert len(audio) > 0


# ---------------------------------------------------------------------------
# 63. VAD: record_from_microphone records full duration when no speech
# ---------------------------------------------------------------------------

def test_record_microphone_vad_no_speech_records_full() -> None:
    """When no speech is detected, recording continues for max_duration."""
    from jarvis_engine.stt import record_from_microphone

    call_count = [0]
    def mock_read(n):
        call_count[0] += 1
        chunk = np.zeros((n, 1), dtype=np.float32)
        return chunk, False

    mock_stream = MagicMock()
    mock_stream.read = mock_read
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_sd if name == "sounddevice" else __import__(name, *a, **kw)):
        audio = record_from_microphone(
            max_duration_seconds=2.0,  # 2s = 20 chunks
            silence_threshold=0.01,
            silence_duration=1.0,
        )

    # No speech detected, so silence_frames never incremented -> records all 20 chunks
    assert call_count[0] == 20


# ---------------------------------------------------------------------------
# 64. VAD: record_from_microphone honors minimum recording duration
# ---------------------------------------------------------------------------

def test_record_microphone_vad_minimum_recording() -> None:
    """Recording always captures at least 0.5s even if silence detected immediately."""
    from jarvis_engine.stt import record_from_microphone

    call_count = [0]
    def mock_read(n):
        call_count[0] += 1
        if call_count[0] == 1:
            # One speech chunk
            chunk = np.full((n, 1), 0.5, dtype=np.float32)
        else:
            chunk = np.zeros((n, 1), dtype=np.float32)
        return chunk, False

    mock_stream = MagicMock()
    mock_stream.read = mock_read
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_sd if name == "sounddevice" else __import__(name, *a, **kw)):
        audio = record_from_microphone(
            max_duration_seconds=30.0,
            silence_threshold=0.01,
            silence_duration=0.3,  # 3 silence chunks before stop
        )

    # 1 speech + 3 silence = 4 chunks; min is 5 (0.5s), so at least 5 chunks
    assert call_count[0] >= 5


# ---------------------------------------------------------------------------
# 65. VAD: record_from_microphone returns empty on no frames
# ---------------------------------------------------------------------------

def test_record_microphone_vad_returns_empty_array_on_zero_duration() -> None:
    """record_from_microphone returns empty array when max_duration is 0."""
    from jarvis_engine.stt import record_from_microphone

    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch("builtins.__import__", side_effect=lambda name, *a, **kw: mock_sd if name == "sounddevice" else __import__(name, *a, **kw)):
        audio = record_from_microphone(
            max_duration_seconds=0.0,
            silence_threshold=0.01,
            silence_duration=2.0,
        )

    assert len(audio) == 0
    assert audio.dtype == np.float32


# ===========================================================================
# Silero VAD integration in record_from_microphone
# ===========================================================================


# ---------------------------------------------------------------------------
# record_from_microphone uses Silero VAD when available
# ---------------------------------------------------------------------------

def test_record_from_microphone_with_silero_vad() -> None:
    """record_from_microphone uses Silero VAD for speech detection."""
    from jarvis_engine import stt as stt_mod

    # Create a mock VAD detector
    mock_vad = MagicMock()
    mock_vad.available = True
    # First 3 chunks: speech detected, next 200 chunks: silence (trigger stop)
    speech_calls = [True, True, True] + [False] * 200
    mock_vad.process_chunk.side_effect = speech_calls

    # Mock sounddevice InputStream
    mock_stream = MagicMock()
    # Each read returns a 512-sample float32 chunk (32ms at 16kHz)
    fake_chunk = np.random.randn(512, 1).astype(np.float32)
    mock_stream.read.return_value = (fake_chunk, None)
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch.dict("sys.modules", {"sounddevice": mock_sd}), \
         patch("jarvis_engine.stt.get_vad_detector", return_value=mock_vad, create=True), \
         patch("jarvis_engine.stt_vad.get_vad_detector", return_value=mock_vad):
        result = stt_mod.record_from_microphone()

    assert len(result) > 0
    # VAD should have been called multiple times
    assert mock_vad.process_chunk.call_count > 3
    # VAD should be reset after recording
    mock_vad.reset.assert_called_once()


# ---------------------------------------------------------------------------
# record_from_microphone falls back to RMS when Silero unavailable
# ---------------------------------------------------------------------------

def test_record_from_microphone_rms_fallback() -> None:
    """record_from_microphone uses RMS energy when Silero VAD unavailable."""
    from jarvis_engine import stt as stt_mod

    # Create a mock VAD detector that reports not available
    mock_vad = MagicMock()
    mock_vad.available = False

    # Mock sounddevice InputStream
    mock_stream = MagicMock()
    # Create chunks: some with energy (speech), then silence
    speech_chunk = np.full((1600, 1), 0.5, dtype=np.float32)  # High energy
    silence_chunk = np.full((1600, 1), 0.0001, dtype=np.float32)  # Low energy

    # 3 speech chunks then many silence chunks
    chunks = [speech_chunk] * 3 + [silence_chunk] * 100
    read_idx = [0]

    def mock_read(n):
        idx = read_idx[0]
        read_idx[0] += 1
        if idx < len(chunks):
            return (chunks[idx], None)
        return (silence_chunk, None)

    mock_stream.read.side_effect = mock_read
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch.dict("sys.modules", {"sounddevice": mock_sd}), \
         patch("jarvis_engine.stt_vad.get_vad_detector", return_value=mock_vad):
        result = stt_mod.record_from_microphone()

    assert len(result) > 0
    # process_chunk should NOT be called since Silero is unavailable
    mock_vad.process_chunk.assert_not_called()
    # reset should NOT be called since Silero was not used
    mock_vad.reset.assert_not_called()


# ---------------------------------------------------------------------------
# record_from_microphone uses 32ms chunks when Silero is active
# ---------------------------------------------------------------------------

def test_record_from_microphone_silero_uses_32ms_chunks() -> None:
    """When Silero VAD is active, chunk size is 512 samples (32ms at 16kHz)."""
    from jarvis_engine import stt as stt_mod

    mock_vad = MagicMock()
    mock_vad.available = True
    # Only speech for first chunk, then enough silence to stop
    mock_vad.process_chunk.side_effect = [True] + [False] * 2000

    mock_stream = MagicMock()
    fake_chunk = np.zeros((512, 1), dtype=np.float32)
    mock_stream.read.return_value = (fake_chunk, None)
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch.dict("sys.modules", {"sounddevice": mock_sd}), \
         patch("jarvis_engine.stt_vad.get_vad_detector", return_value=mock_vad):
        stt_mod.record_from_microphone()

    # stream.read should be called with 512 samples (32ms at 16kHz)
    first_read_arg = mock_stream.read.call_args_list[0][0][0]
    assert first_read_arg == 512


# ---------------------------------------------------------------------------
# record_from_microphone RMS fallback uses 100ms chunks
# ---------------------------------------------------------------------------

def test_record_from_microphone_rms_uses_100ms_chunks() -> None:
    """When RMS fallback is active, chunk size is 1600 samples (100ms at 16kHz)."""
    from jarvis_engine import stt as stt_mod

    mock_vad = MagicMock()
    mock_vad.available = False

    mock_stream = MagicMock()
    # Create speech + silence pattern
    speech_chunk = np.full((1600, 1), 0.5, dtype=np.float32)
    silence_chunk = np.zeros((1600, 1), dtype=np.float32)
    chunks = [speech_chunk] * 3 + [silence_chunk] * 100
    read_idx = [0]

    def mock_read(n):
        idx = read_idx[0]
        read_idx[0] += 1
        if idx < len(chunks):
            return (chunks[idx], None)
        return (silence_chunk, None)

    mock_stream.read.side_effect = mock_read
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch.dict("sys.modules", {"sounddevice": mock_sd}), \
         patch("jarvis_engine.stt_vad.get_vad_detector", return_value=mock_vad):
        stt_mod.record_from_microphone()

    # stream.read should be called with 1600 samples (100ms at 16kHz)
    first_read_arg = mock_stream.read.call_args_list[0][0][0]
    assert first_read_arg == 1600


# ---------------------------------------------------------------------------
# record_from_microphone resets VAD state after recording
# ---------------------------------------------------------------------------

def test_record_from_microphone_resets_vad_state() -> None:
    """VAD state is reset after recording completes (stateful model)."""
    from jarvis_engine import stt as stt_mod

    mock_vad = MagicMock()
    mock_vad.available = True
    # All speech, hit max duration quickly
    mock_vad.process_chunk.return_value = True

    mock_stream = MagicMock()
    fake_chunk = np.zeros((512, 1), dtype=np.float32)
    mock_stream.read.return_value = (fake_chunk, None)
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch.dict("sys.modules", {"sounddevice": mock_sd}), \
         patch("jarvis_engine.stt_vad.get_vad_detector", return_value=mock_vad):
        stt_mod.record_from_microphone(max_duration_seconds=0.1)

    mock_vad.reset.assert_called_once()


# ---------------------------------------------------------------------------
# record_from_microphone graceful when stt_vad import fails entirely
# ---------------------------------------------------------------------------

def test_record_from_microphone_graceful_stt_vad_import_fail() -> None:
    """If stt_vad module fails to import, falls back to RMS."""
    from jarvis_engine import stt as stt_mod

    mock_stream = MagicMock()
    speech_chunk = np.full((1600, 1), 0.5, dtype=np.float32)
    silence_chunk = np.zeros((1600, 1), dtype=np.float32)
    chunks = [speech_chunk] * 3 + [silence_chunk] * 100
    read_idx = [0]

    def mock_read(n):
        idx = read_idx[0]
        read_idx[0] += 1
        if idx < len(chunks):
            return (chunks[idx], None)
        return (silence_chunk, None)

    mock_stream.read.side_effect = mock_read
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_sd = MagicMock()
    mock_sd.InputStream.return_value = mock_stream

    with patch.dict("sys.modules", {"sounddevice": mock_sd}), \
         patch("jarvis_engine.stt_vad.get_vad_detector", side_effect=ImportError("no module")):
        result = stt_mod.record_from_microphone()

    # Should still produce audio (fell back to RMS)
    assert len(result) > 0
