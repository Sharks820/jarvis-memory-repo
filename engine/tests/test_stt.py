"""Tests for speech-to-text pipeline and VoiceListen command."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# 1. TranscriptionResult dataclass defaults
# ---------------------------------------------------------------------------

def test_transcription_result_defaults() -> None:
    from jarvis_engine.stt import TranscriptionResult

    r = TranscriptionResult()
    assert r.text == ""
    assert r.language == ""
    assert r.confidence == 0.0
    assert r.duration_seconds == 0.0


# ---------------------------------------------------------------------------
# 2. SpeechToText lazy model loading
# ---------------------------------------------------------------------------

def test_speech_to_text_lazy_model() -> None:
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    assert stt._model is None  # model not loaded at construction time


# ---------------------------------------------------------------------------
# 3. JARVIS_STT_MODEL env var override
# ---------------------------------------------------------------------------

def test_speech_to_text_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_STT_MODEL", "large-v3")
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    assert stt.model_size == "large-v3"


# ---------------------------------------------------------------------------
# 4. Transcribe audio with mocked WhisperModel
# ---------------------------------------------------------------------------

def test_transcribe_audio_with_mock_model() -> None:
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()

    # Build mock model
    mock_segment = SimpleNamespace(text=" Hello world ")
    mock_info = SimpleNamespace(language="en", language_probability=0.95)
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment], mock_info)

    stt._model = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    result = stt.transcribe_audio(audio)

    assert result.text == "Hello world"
    assert result.language == "en"
    assert result.confidence == 0.95
    assert result.duration_seconds >= 0.0
    mock_model.transcribe.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Transcribe audio with empty segments
# ---------------------------------------------------------------------------

def test_transcribe_audio_empty_segments() -> None:
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()

    mock_info = SimpleNamespace(language="en", language_probability=0.5)
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], mock_info)

    stt._model = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    result = stt.transcribe_audio(audio)

    assert result.text == ""
    assert result.language == "en"
    assert result.confidence == 0.5


# ---------------------------------------------------------------------------
# 6. record_from_microphone -- missing sounddevice
# ---------------------------------------------------------------------------

def test_record_microphone_missing_sounddevice(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis_engine import stt as stt_mod

    # Force sounddevice to fail import inside the function
    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def _fail_sounddevice(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "sounddevice":
            raise ImportError("No module named 'sounddevice'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fail_sounddevice):
        with pytest.raises(RuntimeError, match="sounddevice is not installed"):
            stt_mod.record_from_microphone()


# ---------------------------------------------------------------------------
# 7. SpeechToText -- missing faster_whisper
# ---------------------------------------------------------------------------

def test_stt_missing_faster_whisper() -> None:
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()

    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def _fail_faster_whisper(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fail_faster_whisper):
        with pytest.raises(RuntimeError, match="faster-whisper is not installed"):
            stt._ensure_model()


# ---------------------------------------------------------------------------
# 8. VoiceListenHandler -- success path
# ---------------------------------------------------------------------------

def test_voice_listen_handler_success() -> None:
    from jarvis_engine.stt import TranscriptionResult
    from jarvis_engine.handlers.voice_handlers import VoiceListenHandler
    from jarvis_engine.commands.voice_commands import VoiceListenCommand

    mock_result = TranscriptionResult(
        text="turn on the lights",
        language="en",
        confidence=0.92,
        duration_seconds=2.5,
    )

    handler = VoiceListenHandler(root=Path("."))

    # The handler does a lazy import from jarvis_engine.stt, so patch there.
    with patch("jarvis_engine.stt.listen_and_transcribe", return_value=mock_result):
        result = handler.handle(VoiceListenCommand())

    assert result.text == "turn on the lights"
    assert result.confidence == 0.92
    assert result.duration_seconds == 2.5
    assert result.message == ""


# ---------------------------------------------------------------------------
# 9. VoiceListenHandler -- missing deps returns error result
# ---------------------------------------------------------------------------

def test_voice_listen_handler_missing_deps() -> None:
    from jarvis_engine.handlers.voice_handlers import VoiceListenHandler
    from jarvis_engine.commands.voice_commands import VoiceListenCommand

    handler = VoiceListenHandler(root=Path("."))

    with patch(
        "jarvis_engine.stt.listen_and_transcribe",
        side_effect=RuntimeError("faster-whisper is not installed"),
    ):
        result = handler.handle(VoiceListenCommand())

    assert result.text == ""
    assert result.message == "error: voice listen failed."


# ---------------------------------------------------------------------------
# 10. listen_and_transcribe integration -- mock both record + transcribe
# ---------------------------------------------------------------------------

def test_listen_and_transcribe_integration() -> None:
    from jarvis_engine.stt import TranscriptionResult

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_transcription = TranscriptionResult(
        text="hello jarvis",
        language="en",
        confidence=0.99,
        duration_seconds=1.2,
        backend="mock",
    )

    # Mock record_from_microphone to avoid real mic access, and mock
    # transcribe_smart (not SpeechToText.transcribe_audio) because
    # listen_and_transcribe delegates to transcribe_smart which may
    # route to Groq if GROQ_API_KEY is in the environment.
    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio), \
         patch("jarvis_engine.stt.transcribe_smart", return_value=mock_transcription):
        from jarvis_engine.stt import listen_and_transcribe

        result = listen_and_transcribe(max_duration_seconds=5.0)

    assert result.text == "hello jarvis"
    assert result.confidence == 0.99


# ---------------------------------------------------------------------------
# 11. VoiceListenCommand dataclass defaults
# ---------------------------------------------------------------------------

def test_voice_listen_command_defaults() -> None:
    from jarvis_engine.commands.voice_commands import VoiceListenCommand

    cmd = VoiceListenCommand()
    assert cmd.max_duration_seconds == 30.0
    assert cmd.language == "en"


# ---------------------------------------------------------------------------
# 12. VoiceListenResult dataclass defaults
# ---------------------------------------------------------------------------

def test_voice_listen_result_defaults() -> None:
    from jarvis_engine.commands.voice_commands import VoiceListenResult

    r = VoiceListenResult()
    assert r.text == ""
    assert r.confidence == 0.0
    assert r.duration_seconds == 0.0
    assert r.message == ""


# ---------------------------------------------------------------------------
# 13. TranscriptionResult retried field default
# ---------------------------------------------------------------------------

def test_transcription_result_retried_default() -> None:
    from jarvis_engine.stt import TranscriptionResult

    r = TranscriptionResult()
    assert r.retried is False


# ---------------------------------------------------------------------------
# 14. Confidence retry triggers when confidence < 0.6
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_confidence_retry_triggers_on_low_confidence() -> None:
    """When earlier backends return low confidence, fallback chain continues to find better result."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Parakeet: returns low confidence
    low_conf_result = TranscriptionResult(
        text="hmm maybe",
        language="en",
        confidence=0.4,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )
    # Groq: returns higher confidence (above threshold)
    high_conf_result = TranscriptionResult(
        text="hello jarvis",
        language="en",
        confidence=0.9,
        duration_seconds=1.0,
        backend="groq-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=low_conf_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=high_conf_result), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello jarvis"
    assert result.confidence == 0.9
    assert result.backend == "groq-whisper"


# ---------------------------------------------------------------------------
# 15. Higher-confidence result is kept after retry
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_confidence_retry_keeps_higher_confidence() -> None:
    """When later backends return lower confidence, keep the best result."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Parakeet returns low confidence
    parakeet_result = TranscriptionResult(
        text="set timer",
        language="en",
        confidence=0.5,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )
    # Groq returns even lower
    groq_result = TranscriptionResult(
        text="set time",
        language="en",
        confidence=0.3,
        duration_seconds=1.0,
        backend="groq-whisper",
    )
    # Emergency local also returns lower
    local_result = TranscriptionResult(
        text="set time",
        language="en",
        confidence=0.2,
        duration_seconds=2.0,
        backend="faster-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=parakeet_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=groq_result), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=local_result), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Parakeet result should be kept (highest confidence)
    assert result.text == "set timer"
    assert result.confidence == 0.5
    assert result.backend == "parakeet-tdt"


# ---------------------------------------------------------------------------
# 16. No retry when confidence >= 0.6
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_no_retry_when_confidence_sufficient() -> None:
    """When first backend returns high confidence, chain stops immediately."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    good_result = TranscriptionResult(
        text="turn on lights",
        language="en",
        confidence=0.85,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=good_result) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "turn on lights"
    assert result.confidence == 0.85
    assert result.retried is False
    # Only parakeet should have been called (high confidence -> chain stops)
    mock_pk.assert_called_once()
    mock_dg.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# 17. Metric logging writes correct JSONL
# ---------------------------------------------------------------------------

def test_stt_metric_logging_writes_jsonl() -> None:
    """_log_stt_metric writes valid JSONL to the expected path."""
    from jarvis_engine.stt import _log_stt_metric

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _log_stt_metric(
            root,
            backend="groq-whisper",
            confidence=0.92,
            latency_ms=450.3,
            text_length=15,
            retried=False,
        )
        _log_stt_metric(
            root,
            backend="faster-whisper",
            confidence=0.78,
            latency_ms=1200.0,
            text_length=22,
            retried=True,
        )

        metrics_path = root / ".planning" / "runtime" / "stt_metrics.jsonl"
        assert metrics_path.exists()

        lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

        record1 = json.loads(lines[0])
        assert record1["backend"] == "groq-whisper"
        assert record1["confidence"] == 0.92
        assert record1["latency_ms"] == 450.3
        assert record1["text_length"] == 15
        assert record1["retried"] is False
        assert "ts" in record1

        record2 = json.loads(lines[1])
        assert record2["backend"] == "faster-whisper"
        assert record2["confidence"] == 0.78
        assert record2["retried"] is True


# ---------------------------------------------------------------------------
# 18. Metric logging with None root_dir is a no-op
# ---------------------------------------------------------------------------

def test_stt_metric_logging_none_root_is_noop() -> None:
    """_log_stt_metric with root_dir=None should silently do nothing."""
    from jarvis_engine.stt import _log_stt_metric

    # Should not raise — None root_dir means no file to write to
    result = _log_stt_metric(
        None,
        backend="groq-whisper",
        confidence=0.95,
        latency_ms=300.0,
        text_length=10,
    )
    assert result is None


# ---------------------------------------------------------------------------
# 19. Retry is graceful -- failed retry returns original result
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_confidence_retry_graceful_on_failure() -> None:
    """If only one backend returns a result, that low-confidence result is used."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_conf = TranscriptionResult(
        text="something",
        language="en",
        confidence=0.3,
        duration_seconds=0.5,
        backend="groq-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=low_conf), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Low-confidence result returned (only backend that worked)
    assert result.text == "something"
    assert result.confidence == 0.3


# ---------------------------------------------------------------------------
# 20. Local primary retries with Groq when GROQ_API_KEY available
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_auto_mode_tries_all_chain_backends() -> None:
    """In auto mode with low-confidence results, the fallback chain tries all backends."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_result = TranscriptionResult(
        text="maybe hello",
        language="en",
        confidence=0.4,
        duration_seconds=1.0,
        backend="faster-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=None) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram", return_value=None) as mock_dg, \
         patch("jarvis_engine.stt._try_groq", return_value=None) as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency", return_value=low_result) as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "maybe hello"
    assert result.confidence == 0.4
    # All backends in the chain should have been tried
    mock_pk.assert_called_once()
    mock_dg.assert_called_once()
    mock_groq.assert_called_once()
    mock_local.assert_called_once()


# ---------------------------------------------------------------------------
# 21. Metrics logged during transcribe_smart with root_dir
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_logs_metrics_with_root_dir() -> None:
    """transcribe_smart logs metrics when root_dir is provided."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    good_result = TranscriptionResult(
        text="turn on lights",
        language="en",
        confidence=0.92,
        duration_seconds=0.5,
        backend="groq-whisper",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        with patch("jarvis_engine.stt._try_parakeet", return_value=good_result), \
             patch("jarvis_engine.stt._try_deepgram", return_value=None), \
             patch("jarvis_engine.stt._try_groq", return_value=None), \
             patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
             patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
             patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
            result = transcribe_smart(fake_audio, root_dir=root)

        assert result.text == "turn on lights"

        metrics_path = root / ".planning" / "runtime" / "stt_metrics.jsonl"
        assert metrics_path.exists()
        lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert record["confidence"] == 0.92


# ---------------------------------------------------------------------------
# 22. Groq transcription computes real confidence from segment logprobs
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_real_confidence() -> None:
    """transcribe_groq extracts confidence from segment avg_logprob."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Simulate a Groq verbose_json response with segments
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "hello jarvis",
        "language": "en",
        "segments": [
            {"text": "hello jarvis", "start": 0.0, "end": 1.5, "avg_logprob": -0.15},
        ],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    assert result.text == "hello jarvis"
    assert result.backend == "groq-whisper"
    # Confidence from min(1.0, max(0.0, 1.0 + avg_logprob))
    # = min(1.0, max(0.0, 1.0 + (-0.15))) = 0.85
    expected = round(min(1.0, max(0.0, 1.0 + (-0.15))), 4)
    assert result.confidence == expected


# ---------------------------------------------------------------------------
# 23. Groq transcription falls back when no segments returned
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_no_segments_fallback() -> None:
    """When Groq returns no segments, confidence falls back to 0.85."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "hello",
        "language": "en",
        # No segments field
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    assert result.confidence == 0.90


# ===========================================================================
# NEW TESTS: backend selection, audio preprocessing, error handling, etc.
# ===========================================================================

import struct
import threading


# ---------------------------------------------------------------------------
# 24. _numpy_to_wav_bytes produces valid WAV header
# ---------------------------------------------------------------------------

def test_numpy_to_wav_bytes_valid_header() -> None:
    """WAV bytes start with RIFF header and contain correct format."""
    from jarvis_engine.stt import _numpy_to_wav_bytes

    audio = np.zeros(16000, dtype=np.float32)
    wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=16000)

    assert wav_bytes[:4] == b"RIFF"
    assert wav_bytes[8:12] == b"WAVE"
    assert wav_bytes[12:16] == b"fmt "
    assert wav_bytes[36:40] == b"data"


def test_numpy_to_wav_bytes_correct_data_size() -> None:
    """WAV data section size equals num_samples * 2 (16-bit)."""
    from jarvis_engine.stt import _numpy_to_wav_bytes

    num_samples = 8000
    audio = np.zeros(num_samples, dtype=np.float32)
    wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=16000)

    # Data chunk size is at offset 40 (little-endian uint32)
    data_size = struct.unpack("<I", wav_bytes[40:44])[0]
    assert data_size == num_samples * 2


def test_numpy_to_wav_bytes_custom_sample_rate() -> None:
    """Custom sample rate is encoded in the WAV header."""
    from jarvis_engine.stt import _numpy_to_wav_bytes

    audio = np.zeros(100, dtype=np.float32)
    wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=44100)

    # Sample rate at offset 24 (little-endian uint32)
    sr = struct.unpack("<I", wav_bytes[24:28])[0]
    assert sr == 44100


def test_numpy_to_wav_bytes_clips_values() -> None:
    """Audio values outside [-1, 1] are clipped to int16 range."""
    from jarvis_engine.stt import _numpy_to_wav_bytes

    # Audio with values beyond [-1, 1]
    audio = np.array([2.0, -2.0, 0.5], dtype=np.float32)
    wav_bytes = _numpy_to_wav_bytes(audio)

    # Extract int16 data (after 44-byte header)
    data = np.frombuffer(wav_bytes[44:], dtype=np.int16)
    assert data[0] == 32767   # clipped to max
    assert data[1] == -32768  # clipped to min
    assert 16000 < data[2] < 16500  # roughly 0.5 * 32767


# ---------------------------------------------------------------------------
# 25. Groq transcription with file path input
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_file_path_input() -> None:
    """transcribe_groq can accept a file path string."""
    from jarvis_engine.stt import transcribe_groq

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "hello from file",
        "language": "en",
        "segments": [],
    }

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"fake wav data")
        temp_path = f.name

    try:
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = transcribe_groq(temp_path)

        assert result.text == "hello from file"
        # Filename should be the basename of the temp file
        files_arg = mock_client.post.call_args[1]["files"]
        assert files_arg["file"][0] == os.path.basename(temp_path)
    finally:
        os.unlink(temp_path)


# ---------------------------------------------------------------------------
# 26. Groq transcription API error
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_api_error_raises() -> None:
    """Non-200 status code raises RuntimeError."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Rate limited"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Groq STT API error 429"):
            transcribe_groq(fake_audio)


# ---------------------------------------------------------------------------
# 27. Groq transcription missing API key
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": ""}, clear=False)
def test_groq_transcription_no_api_key_raises() -> None:
    """Missing GROQ_API_KEY raises RuntimeError."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY not set"):
        transcribe_groq(fake_audio)


# ---------------------------------------------------------------------------
# 28. Groq transcription custom prompt
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_custom_prompt() -> None:
    """Custom prompt is passed to the API (truncated to 224 chars)."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "custom", "language": "en", "segments": []
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        long_prompt = "a" * 300
        transcribe_groq(fake_audio, prompt=long_prompt)

        data_arg = mock_client.post.call_args[1]["data"]
        assert len(data_arg["prompt"]) == 224


# ---------------------------------------------------------------------------
# 29. Groq confidence with multiple segments
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_multi_segment() -> None:
    """Confidence is averaged across multiple segments."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "hello world",
        "language": "en",
        "segments": [
            {"text": "hello", "avg_logprob": -0.1, "no_speech_prob": 0.01},
            {"text": "world", "avg_logprob": -0.2, "no_speech_prob": 0.03},
        ],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    avg_logprob = (-0.1 + -0.2) / 2  # = -0.15
    expected = round(min(1.0, max(0.0, 1.0 + avg_logprob)), 4)  # = 0.85
    assert result.confidence == expected


# ---------------------------------------------------------------------------
# 30. Groq confidence with empty segments list
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_empty_segments_list() -> None:
    """Empty segments list falls back to 0.90 confidence."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "hello",
        "language": "en",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    assert result.confidence == 0.90


# ---------------------------------------------------------------------------
# 31. Groq confidence with segments missing logprobs
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_segments_without_logprobs() -> None:
    """Segments without avg_logprob use 0.85 fallback."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "hello",
        "language": "en",
        "segments": [
            {"text": "hello"},  # no avg_logprob or no_speech_prob
        ],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    # Segments present but no logprobs => fallback
    assert result.confidence == 0.90


# ---------------------------------------------------------------------------
# 32. transcribe_smart forced groq backend
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "groq"}, clear=False)
def test_transcribe_smart_forced_groq() -> None:
    """JARVIS_STT_BACKEND=groq forces Groq and skips local fallback."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    groq_result = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=0.3, backend="groq-whisper",
    )

    with patch("jarvis_engine.stt.transcribe_groq", return_value=groq_result) as mock_groq, \
         patch("jarvis_engine.stt._try_local") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello"
    assert result.backend == "groq-whisper"
    mock_groq.assert_called_once()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# 33. transcribe_smart forced local backend
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "local"}, clear=False)
def test_transcribe_smart_forced_local() -> None:
    """JARVIS_STT_BACKEND=local forces local and skips Groq."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    local_result = TranscriptionResult(
        text="local hello", language="en", confidence=0.8,
        duration_seconds=1.0, backend="faster-whisper",
    )

    with patch("jarvis_engine.stt._try_local", return_value=local_result), \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_parakeet") as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.backend == "faster-whisper"
    mock_groq.assert_not_called()
    mock_pk.assert_not_called()
    mock_dg.assert_not_called()


# ---------------------------------------------------------------------------
# 34. transcribe_smart auto mode -- all backends fail
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_all_backends_fail() -> None:
    """When all backends fail, returns empty result with backend='none'."""
    from jarvis_engine.stt import transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio):
        result = transcribe_smart(fake_audio)

    assert result.text == ""
    assert result.confidence == 0.0
    assert result.backend == "none"


# ---------------------------------------------------------------------------
# 35. _try_groq returns None on failure
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_try_groq_returns_none_on_exception() -> None:
    """_try_groq catches exceptions and returns None."""
    from jarvis_engine.stt import _try_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt.transcribe_groq", side_effect=RuntimeError("API error")):
        result = _try_groq(fake_audio, language="en", prompt="")

    assert result is None


# ---------------------------------------------------------------------------
# 36. _try_local returns None on failure
# ---------------------------------------------------------------------------

def test_try_local_returns_none_on_exception() -> None:
    """_try_local catches exceptions and returns None."""
    from jarvis_engine.stt import _try_local

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt.SpeechToText.transcribe_audio", side_effect=RuntimeError("model error")):
        result = _try_local(fake_audio, language="en")

    assert result is None


# ---------------------------------------------------------------------------
# 37. SpeechToText transcribe_audio with multiple segments
# ---------------------------------------------------------------------------

def test_transcribe_audio_multiple_segments() -> None:
    """Multiple segments are joined with spaces."""
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    mock_segments = [
        SimpleNamespace(text=" Hello "),
        SimpleNamespace(text=" world "),
        SimpleNamespace(text=" how are you "),
    ]
    mock_info = SimpleNamespace(language="en", language_probability=0.9)
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (mock_segments, mock_info)
    stt._model = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    result = stt.transcribe_audio(audio)

    assert result.text == "Hello world how are you"
    assert result.confidence == 0.9


# ---------------------------------------------------------------------------
# 38. SpeechToText default parameters
# ---------------------------------------------------------------------------

def test_speech_to_text_default_params() -> None:
    """Default parameters are set correctly."""
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    assert stt.model_size == "small.en"
    assert stt.device == "cpu"
    assert stt.compute_type == "int8"
    assert stt._model is None


# ---------------------------------------------------------------------------
# 39. SpeechToText custom parameters
# ---------------------------------------------------------------------------

def test_speech_to_text_custom_params() -> None:
    """Custom parameters override defaults."""
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText(model_size="large-v3", device="cuda", compute_type="float16")
    assert stt.model_size == "large-v3"
    assert stt.device == "cuda"
    assert stt.compute_type == "float16"


# ---------------------------------------------------------------------------
# 40. SpeechToText._ensure_model is idempotent
# ---------------------------------------------------------------------------

def test_ensure_model_idempotent() -> None:
    """_ensure_model does not reload if model is already set."""
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    mock_model = MagicMock()
    stt._model = mock_model

    # Should not try to import faster_whisper since model is set
    stt._ensure_model()
    assert stt._model is mock_model


# ---------------------------------------------------------------------------
# 41. SpeechToText transcribe_audio passes vad_filter
# ---------------------------------------------------------------------------

def test_transcribe_audio_vad_filter_passed() -> None:
    """vad_filter parameter is passed to the underlying model."""
    from jarvis_engine.stt import JARVIS_DEFAULT_PROMPT, SpeechToText

    stt = SpeechToText()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], SimpleNamespace(language="en", language_probability=0.5))
    stt._model = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    stt.transcribe_audio(audio, vad_filter=False)

    mock_model.transcribe.assert_called_once_with(
        audio,
        language="en",
        vad_filter=False,
        initial_prompt=JARVIS_DEFAULT_PROMPT,
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


# ---------------------------------------------------------------------------
# 42. SpeechToText transcribe_audio with file path
# ---------------------------------------------------------------------------

def test_transcribe_audio_accepts_file_path() -> None:
    """transcribe_audio can accept a string file path."""
    from jarvis_engine.stt import JARVIS_DEFAULT_PROMPT, SpeechToText

    stt = SpeechToText()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [SimpleNamespace(text=" from file ", avg_logprob=-0.12, start=0.0, end=1.0)],
        SimpleNamespace(language="en", language_probability=0.88),
    )
    stt._model = mock_model

    result = stt.transcribe_audio("/tmp/audio.wav")
    assert result.text == "from file"
    mock_model.transcribe.assert_called_once_with(
        "/tmp/audio.wav",
        language="en",
        vad_filter=True,
        initial_prompt=JARVIS_DEFAULT_PROMPT,
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


# ---------------------------------------------------------------------------
# 42b. Local confidence uses segment avg_logprob
# ---------------------------------------------------------------------------

def test_local_confidence_uses_logprobs() -> None:
    """Confidence is computed from segment avg_logprob, not language_probability."""
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [
            SimpleNamespace(text=" hello ", avg_logprob=-0.3, start=0.0, end=0.5),
            SimpleNamespace(text=" world ", avg_logprob=-0.5, start=0.5, end=1.0),
        ],
        SimpleNamespace(language="en", language_probability=0.99),
    )
    stt._model = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    result = stt.transcribe_audio(audio)

    # avg_logprob = (-0.3 + -0.5) / 2 = -0.4
    # confidence = 1.0 + (-0.4) = 0.6
    expected_confidence = 0.6
    assert abs(result.confidence - expected_confidence) < 0.01, f"Expected {expected_confidence}, got {result.confidence}"
    # Should NOT use language_probability (which would give 0.99)
    assert result.confidence != 0.99


# ---------------------------------------------------------------------------
# 43. _confidence_retry with local primary and groq available
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_confidence_retry_local_primary_retries_groq() -> None:
    """When local is primary and confidence is low, retry with Groq."""
    from jarvis_engine.stt import TranscriptionResult, _confidence_retry

    fake_audio = np.zeros(16000, dtype=np.float32)

    primary = TranscriptionResult(
        text="maybe", language="en", confidence=0.4,
        duration_seconds=1.0, backend="faster-whisper",
    )
    groq_result = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=0.3, backend="groq-whisper",
    )

    with patch("jarvis_engine.stt._try_groq", return_value=groq_result):
        result = _confidence_retry(primary, fake_audio, language="en", prompt="", root_dir=None)

    assert result.text == "hello"
    assert result.backend == "groq-whisper"
    assert result.retried is True


# ---------------------------------------------------------------------------
# 44. _confidence_retry with no alternative backend
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": ""}, clear=False)
def test_confidence_retry_no_alternative() -> None:
    """When no alternative backend is available, primary is returned."""
    from jarvis_engine.stt import TranscriptionResult, _confidence_retry

    fake_audio = np.zeros(16000, dtype=np.float32)

    primary = TranscriptionResult(
        text="something", language="en", confidence=0.3,
        duration_seconds=1.0, backend="faster-whisper",
    )

    # No GROQ_API_KEY, so no alternative for faster-whisper
    result = _confidence_retry(primary, fake_audio, language="en", prompt="", root_dir=None)

    assert result.text == "something"
    assert result.confidence == 0.3
    assert result.retried is False


# ---------------------------------------------------------------------------
# 45. Metric logging thread safety (concurrent writes)
# ---------------------------------------------------------------------------

def test_metric_logging_concurrent_writes() -> None:
    """Multiple concurrent _log_stt_metric calls don't corrupt the file."""
    from jarvis_engine.stt import _log_stt_metric

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        threads = []
        for i in range(10):
            t = threading.Thread(
                target=_log_stt_metric,
                args=(root,),
                kwargs={
                    "backend": f"backend-{i}",
                    "confidence": 0.8,
                    "latency_ms": 100.0,
                    "text_length": 10,
                },
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        metrics_path = root / ".planning" / "runtime" / "stt_metrics.jsonl"
        lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 10

        for line in lines:
            record = json.loads(line)
            assert "backend" in record
            assert "confidence" in record


# ---------------------------------------------------------------------------
# 46. listen_and_transcribe uses smart backend selection
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_listen_and_transcribe_uses_transcribe_smart() -> None:
    """listen_and_transcribe delegates to transcribe_smart, not transcribe_audio."""
    from jarvis_engine.stt import TranscriptionResult, listen_and_transcribe

    fake_audio = np.zeros(16000, dtype=np.float32)
    expected = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=1.0, backend="faster-whisper",
    )

    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio), \
         patch("jarvis_engine.stt.transcribe_smart", return_value=expected) as mock_smart:
        result = listen_and_transcribe()

    mock_smart.assert_called_once()
    assert result.text == "hello"


# ---------------------------------------------------------------------------
# 47. Groq confidence clamps extreme logprobs
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_extreme_logprob_clamping() -> None:
    """Very negative avg_logprob is clamped to -5.0 before exp()."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "noise",
        "language": "en",
        "segments": [
            {"text": "noise", "avg_logprob": -100.0, "no_speech_prob": 0.0},
        ],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    # 1.0 + (-100) = -99, clamped to 0.0 by max(0.0, ...)
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# 48. TranscriptionResult fields are correct types
# ---------------------------------------------------------------------------

def test_transcription_result_backend_field() -> None:
    """TranscriptionResult.backend defaults to empty string."""
    from jarvis_engine.stt import TranscriptionResult

    r = TranscriptionResult()
    assert r.backend == ""
    assert isinstance(r.retried, bool)


# ---------------------------------------------------------------------------
# 49. Groq confidence with non-finite logprobs
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_skips_non_finite_logprobs() -> None:
    """Non-finite avg_logprob values are skipped."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "test",
        "language": "en",
        "segments": [
            {"text": "test", "avg_logprob": float("nan"), "no_speech_prob": 0.0},
        ],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    # NaN is skipped, so no logprobs available -> fallback to 0.90
    assert result.confidence == 0.90


# ---------------------------------------------------------------------------
# 50. Groq transcription with high no_speech_prob
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_high_no_speech_penalty() -> None:
    """High no_speech_prob reduces confidence."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "maybe noise",
        "language": "en",
        "segments": [
            {"text": "maybe noise", "avg_logprob": -0.1, "no_speech_prob": 0.9},
        ],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio)

    # Confidence = (1.0 + avg_logprob) * (1.0 - no_speech_prob) when no_speech_prob > 0.5
    # = 0.9 * (1.0 - 0.9) = 0.9 * 0.1 = 0.09
    base_conf = min(1.0, max(0.0, 1.0 + (-0.1)))  # = 0.9
    expected = round(base_conf * (1.0 - 0.9), 4)    # = 0.09
    assert result.confidence == expected


# ---------------------------------------------------------------------------
# 51. Groq transcription detected language propagated
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_detected_language() -> None:
    """Detected language from API is used instead of hint."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "bonjour",
        "language": "fr",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(fake_audio, language="en")

    assert result.language == "fr"


# ===========================================================================
# NEW TESTS: Bug-fix verification for P1 voice/STT pipeline
# ===========================================================================


# ---------------------------------------------------------------------------
# 52. Bug 4 fix: _try_local caches SpeechToText instance
# ---------------------------------------------------------------------------

def test_try_local_caches_stt_instance() -> None:
    """_try_local reuses the same SpeechToText instance across calls."""
    import jarvis_engine.stt as stt_mod

    # Reset the cached instance
    original_instance = stt_mod._local_stt_instance
    stt_mod._local_stt_instance = None

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_result = MagicMock()
    mock_result.text = "hello"

    try:
        with patch.object(stt_mod.SpeechToText, "transcribe_audio", return_value=mock_result):
            stt_mod._try_local(fake_audio, language="en")
            first_instance = stt_mod._local_stt_instance

            stt_mod._try_local(fake_audio, language="en")
            second_instance = stt_mod._local_stt_instance

        # Both calls should use the same instance
        assert first_instance is not None
        assert first_instance is second_instance
    finally:
        # Restore original state
        stt_mod._local_stt_instance = original_instance


def test_try_local_does_not_recreate_on_each_call() -> None:
    """_try_local does not construct a new SpeechToText on every invocation."""
    import jarvis_engine.stt as stt_mod

    original_instance = stt_mod._local_stt_instance
    stt_mod._local_stt_instance = None

    fake_audio = np.zeros(16000, dtype=np.float32)

    try:
        with patch("jarvis_engine.stt.SpeechToText") as mock_stt_cls:
            mock_instance = MagicMock()
            mock_instance.transcribe_audio.return_value = MagicMock(text="hi")
            mock_stt_cls.return_value = mock_instance

            stt_mod._try_local(fake_audio, language="en")
            stt_mod._try_local(fake_audio, language="en")
            stt_mod._try_local(fake_audio, language="en")

            # SpeechToText() should only have been called once
            mock_stt_cls.assert_called_once()
    finally:
        stt_mod._local_stt_instance = original_instance


# ---------------------------------------------------------------------------
# 53. Bug 5 fix: listen_and_transcribe forwards root_dir
# ---------------------------------------------------------------------------

def test_listen_and_transcribe_forwards_root_dir() -> None:
    """listen_and_transcribe passes root_dir to transcribe_smart."""
    from jarvis_engine.stt import TranscriptionResult, listen_and_transcribe

    fake_audio = np.zeros(16000, dtype=np.float32)
    expected = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=1.0, backend="faster-whisper",
    )

    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio), \
         patch("jarvis_engine.stt.transcribe_smart", return_value=expected) as mock_smart:
        result = listen_and_transcribe(root_dir=Path("/tmp/test"))

    mock_smart.assert_called_once_with(
        fake_audio, language="en", root_dir=Path("/tmp/test"),
        gateway=None, entity_list=None,
    )
    assert result.text == "hello"


def test_listen_and_transcribe_no_model_size_param() -> None:
    """listen_and_transcribe no longer accepts model_size parameter."""
    import inspect
    from jarvis_engine.stt import listen_and_transcribe

    sig = inspect.signature(listen_and_transcribe)
    assert "model_size" not in sig.parameters
    assert "root_dir" in sig.parameters


def test_listen_and_transcribe_root_dir_defaults_to_none() -> None:
    """listen_and_transcribe root_dir defaults to None."""
    from jarvis_engine.stt import TranscriptionResult, listen_and_transcribe

    fake_audio = np.zeros(16000, dtype=np.float32)
    expected = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=1.0, backend="faster-whisper",
    )

    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio), \
         patch("jarvis_engine.stt.transcribe_smart", return_value=expected) as mock_smart:
        listen_and_transcribe()

    mock_smart.assert_called_once_with(
        fake_audio, language="en", root_dir=None,
        gateway=None, entity_list=None,
    )


# ---------------------------------------------------------------------------
# 54. Groq retry on 500 response
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_retry_on_500_response() -> None:
    """transcribe_groq retries once on 5xx and succeeds on second attempt."""
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_500_response = MagicMock()
    mock_500_response.status_code = 500
    mock_500_response.text = "Internal Server Error"

    mock_ok_response = MagicMock()
    mock_ok_response.status_code = 200
    mock_ok_response.json.return_value = {
        "text": "hello after retry",
        "language": "en",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = [mock_500_response, mock_ok_response]
        mock_client_cls.return_value = mock_client

        with patch("jarvis_engine.stt.time.sleep") as mock_sleep:
            result = transcribe_groq(fake_audio)

        assert result.text == "hello after retry"
        assert mock_client.post.call_count == 2
        mock_sleep.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# 55. Groq retry on connection error
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_retry_on_connection_error() -> None:
    """transcribe_groq retries once on ConnectError; returns None-like result after exhaustion."""
    import httpx as _httpx
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = _httpx.ConnectError("connection refused")
        mock_client_cls.return_value = mock_client

        with patch("jarvis_engine.stt.time.sleep") as mock_sleep:
            result = transcribe_groq(fake_audio)

        # After 2 failed attempts, returns empty TranscriptionResult
        assert result is not None
        assert result.text == ""
        assert result.confidence == 0.0
        assert result.backend == "groq-whisper"
        assert mock_client.post.call_count == 2
        mock_sleep.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# 56. Groq retry on ReadTimeout
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_retry_on_read_timeout() -> None:
    """transcribe_groq retries once on ReadTimeout; returns empty result after exhaustion."""
    import httpx as _httpx
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = _httpx.ReadTimeout("read timed out")
        mock_client_cls.return_value = mock_client

        with patch("jarvis_engine.stt.time.sleep") as mock_sleep:
            result = transcribe_groq(fake_audio)

        assert result is not None
        assert result.text == ""
        assert result.confidence == 0.0
        assert mock_client.post.call_count == 2
        mock_sleep.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# 57. Groq connection error recovers on second attempt
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_connection_error_recovers_on_retry() -> None:
    """First attempt gets ConnectError, second succeeds."""
    import httpx as _httpx
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_ok_response = MagicMock()
    mock_ok_response.status_code = 200
    mock_ok_response.json.return_value = {
        "text": "recovered",
        "language": "en",
        "segments": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = [
            _httpx.ConnectError("connection refused"),
            mock_ok_response,
        ]
        mock_client_cls.return_value = mock_client

        with patch("jarvis_engine.stt.time.sleep"):
            result = transcribe_groq(fake_audio)

        assert result.text == "recovered"
        assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# 58. Env var for confidence threshold
# ---------------------------------------------------------------------------

def test_confidence_threshold_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """JARVIS_STT_CONFIDENCE_THRESHOLD env var overrides default threshold."""
    monkeypatch.setenv("JARVIS_STT_CONFIDENCE_THRESHOLD", "0.8")
    # Re-import to pick up the new env var value
    import importlib
    import jarvis_engine.stt as stt_mod
    importlib.reload(stt_mod)
    try:
        assert stt_mod.CONFIDENCE_RETRY_THRESHOLD == 0.8
    finally:
        # Restore original value
        monkeypatch.delenv("JARVIS_STT_CONFIDENCE_THRESHOLD", raising=False)
        importlib.reload(stt_mod)


# ---------------------------------------------------------------------------
# 59. Env var for Groq model name
# ---------------------------------------------------------------------------

def test_groq_model_name_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """JARVIS_GROQ_STT_MODEL env var overrides default model name."""
    monkeypatch.setenv("JARVIS_GROQ_STT_MODEL", "whisper-large-v3")
    import importlib
    import jarvis_engine.stt as stt_mod
    importlib.reload(stt_mod)
    try:
        assert stt_mod.GROQ_STT_MODEL == "whisper-large-v3"
    finally:
        monkeypatch.delenv("JARVIS_GROQ_STT_MODEL", raising=False)
        importlib.reload(stt_mod)


# ---------------------------------------------------------------------------
# 60. Default Groq model name constant
# ---------------------------------------------------------------------------

def test_groq_model_name_default() -> None:
    """GROQ_STT_MODEL defaults to whisper-large-v3-turbo."""
    from jarvis_engine.stt import GROQ_STT_MODEL
    # When env var is not set, default is used
    if not os.environ.get("JARVIS_GROQ_STT_MODEL"):
        assert GROQ_STT_MODEL == "whisper-large-v3-turbo"


# ---------------------------------------------------------------------------
# 61. Groq API uses GROQ_STT_MODEL constant in request
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_api_uses_model_constant() -> None:
    """transcribe_groq sends the GROQ_STT_MODEL value in the API request."""
    from jarvis_engine.stt import transcribe_groq, GROQ_STT_MODEL

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "test", "language": "en", "segments": []
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        transcribe_groq(fake_audio)

        data_arg = mock_client.post.call_args[1]["data"]
        assert data_arg["model"] == GROQ_STT_MODEL


# ===========================================================================
# P2 voice pipeline fix tests
# ===========================================================================


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


# ---------------------------------------------------------------------------
# 66. Minimum audio duration: transcribe_groq returns None for short audio
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_too_short_returns_none() -> None:
    """Audio shorter than 0.1s (1600 samples) returns None without API call."""
    from jarvis_engine.stt import transcribe_groq

    short_audio = np.zeros(500, dtype=np.float32)

    with patch("httpx.Client") as mock_client_cls:
        result = transcribe_groq(short_audio)

    assert result is None
    # httpx.Client should NOT have been called
    mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 67. Minimum audio duration: exactly 1600 samples passes
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_exact_threshold_passes() -> None:
    """Audio with exactly 1600 samples is sent to the API."""
    from jarvis_engine.stt import transcribe_groq

    audio = np.zeros(1600, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "short but valid", "language": "en", "segments": []
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = transcribe_groq(audio)

    assert result is not None
    assert result.text == "short but valid"


# ---------------------------------------------------------------------------
# 68. Minimum audio duration: file path input bypasses check
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_file_path_bypasses_duration_check() -> None:
    """File path input is not subject to minimum duration check."""
    from jarvis_engine.stt import transcribe_groq

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "from file", "language": "en", "segments": []
    }

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"fake wav data")
        temp_path = f.name

    try:
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = transcribe_groq(temp_path)

        assert result is not None
        assert result.text == "from file"
    finally:
        os.unlink(temp_path)


# ---------------------------------------------------------------------------
# XX. transcribe_smart calls preprocess_audio
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_transcribe_smart_calls_preprocess() -> None:
    """transcribe_smart calls preprocess_audio on numpy audio."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)
    mock_result = TranscriptionResult(
        text="hello world",
        language="en",
        confidence=0.9,
        duration_seconds=0.5,
        backend="groq",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=mock_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio) as mock_preprocess, \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", return_value="hello world"):
        result = transcribe_smart(fake_audio)
        mock_preprocess.assert_called_once()


@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_calls_postprocess() -> None:
    """transcribe_smart calls postprocess_transcription on result text."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)
    mock_result = TranscriptionResult(
        text="um hello conner",
        language="en",
        confidence=0.8,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=mock_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", return_value="Hello, Conner!") as mock_post:
        mock_gateway = MagicMock()
        result = transcribe_smart(fake_audio, gateway=mock_gateway, entity_list=["Conner"])
        mock_post.assert_called_once_with(
            "um hello conner",
            0.8,
            gateway=mock_gateway,
            entity_list=["Conner"],
        )
        assert result.text == "Hello, Conner!"


def test_transcribe_smart_skips_preprocess_for_file_path() -> None:
    """transcribe_smart does NOT preprocess when audio is a file path string."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    mock_result = TranscriptionResult(
        text="hello world",
        language="en",
        confidence=0.9,
        duration_seconds=0.5,
        backend="local",
    )

    with patch.dict("os.environ", {"JARVIS_STT_BACKEND": "local"}), \
         patch("jarvis_engine.stt._try_local", return_value=mock_result), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio") as mock_preprocess, \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", return_value="hello world"):
        result = transcribe_smart("/tmp/audio.wav")
        mock_preprocess.assert_not_called()


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


# ===========================================================================
# Parakeet TDT 0.6B backend tests (_try_parakeet)
# ===========================================================================


def _reset_parakeet_global():
    """Reset the _parakeet_model singleton so each test starts clean."""
    import jarvis_engine.stt as stt_mod
    stt_mod._parakeet_model = None


# ---------------------------------------------------------------------------
# P1. test_try_parakeet_success
# ---------------------------------------------------------------------------

def test_try_parakeet_success():
    """Mock onnx_asr.load_model to return a mock model that transcribes successfully."""
    _reset_parakeet_global()

    from jarvis_engine.stt import _try_parakeet

    mock_model = MagicMock()
    # .with_timestamps() returns itself (timestamps available)
    mock_model.with_timestamps.return_value = mock_model
    # recognize() returns an object whose str() is "Hello world"
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "Hello world"
    mock_result.__bool__ = lambda self: True
    mock_result.tokens = None  # No log probs -> baseline confidence
    mock_model.recognize.return_value = mock_result

    mock_onnx_asr = MagicMock()
    mock_onnx_asr.load_model.return_value = mock_model

    with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
        result = _try_parakeet(np.zeros(16000, dtype=np.float32), language="en")

    assert result is not None
    assert result.text == "Hello world"
    assert result.backend == "parakeet-tdt"
    assert result.confidence > 0
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
# P4. test_try_parakeet_empty_result
# ---------------------------------------------------------------------------

def test_try_parakeet_empty_result():
    """When model returns empty text, result has text='' and confidence=0.0."""
    _reset_parakeet_global()

    from jarvis_engine.stt import _try_parakeet

    mock_model = MagicMock()
    mock_model.with_timestamps.return_value = mock_model
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: ""
    mock_result.__bool__ = lambda self: True
    mock_result.tokens = None
    mock_model.recognize.return_value = mock_result

    mock_onnx_asr = MagicMock()
    mock_onnx_asr.load_model.return_value = mock_model

    with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
        result = _try_parakeet(np.zeros(16000, dtype=np.float32), language="en")

    assert result is not None
    assert result.text == ""
    assert result.confidence == 0.0
    assert result.backend == "parakeet-tdt"


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


# ---------------------------------------------------------------------------
# P8. test_try_parakeet_confidence_baseline
# ---------------------------------------------------------------------------

def test_try_parakeet_confidence_baseline():
    """When no log probs are available, confidence should be 0.94 (Parakeet baseline WER)."""
    _reset_parakeet_global()

    from jarvis_engine.stt import _try_parakeet

    mock_model = MagicMock()
    mock_model.with_timestamps.return_value = mock_model
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "some transcription"
    mock_result.__bool__ = lambda self: True
    mock_result.tokens = None  # No tokens -> no log probs -> baseline
    mock_model.recognize.return_value = mock_result

    mock_onnx_asr = MagicMock()
    mock_onnx_asr.load_model.return_value = mock_model

    with patch.dict("sys.modules", {"onnx_asr": mock_onnx_asr}):
        result = _try_parakeet(np.zeros(16000, dtype=np.float32), language="en")

    assert result is not None
    assert result.confidence == 0.94
    assert result.backend == "parakeet-tdt"


# ===========================================================================
# DEEPGRAM NOVA-3 BACKEND TESTS
# ===========================================================================

def _reset_keyterms_cache():
    """Reset personal vocab caches so tests start with a clean state."""
    import jarvis_engine._shared as shared_mod
    shared_mod._personal_vocab_stripped_cache = None
    shared_mod._personal_vocab_raw_cache = None


# ---------------------------------------------------------------------------
# D1. test_load_keyterms -- reads personal_vocab.txt and returns terms
# ---------------------------------------------------------------------------

def test_load_keyterms():
    """_load_keyterms reads personal_vocab.txt and returns cleaned term list."""
    _reset_keyterms_cache()
    from jarvis_engine.stt import _load_keyterms

    terms = _load_keyterms()
    assert isinstance(terms, list)
    assert len(terms) > 0
    # Should include known terms from personal_vocab.txt
    assert "Conner" in terms
    assert "Jarvis" in terms
    assert "Ollama" in terms
    # Parenthetical annotations should be stripped
    for term in terms:
        assert "(" not in term, f"Parenthetical not stripped: {term}"
        assert ")" not in term, f"Parenthetical not stripped: {term}"
    _reset_keyterms_cache()


# ---------------------------------------------------------------------------
# D2. test_load_keyterms_caching -- file only read once
# ---------------------------------------------------------------------------

def test_load_keyterms_caching():
    """_load_keyterms caches results: second call returns same list without re-reading."""
    _reset_keyterms_cache()
    import jarvis_engine._shared as shared_mod
    from jarvis_engine.stt import _load_keyterms

    # First call: loads from file
    terms1 = _load_keyterms()
    assert shared_mod._personal_vocab_stripped_cache is not None

    # Second call: returns cached (same object from shared cache)
    terms2 = _load_keyterms()
    assert terms1 is terms2  # Same object (cached)
    _reset_keyterms_cache()


# ---------------------------------------------------------------------------
# D3. test_try_deepgram_no_api_key -- returns None immediately
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": ""}, clear=False)
def test_try_deepgram_no_api_key():
    """_try_deepgram returns None immediately when DEEPGRAM_API_KEY is not set."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)
    result = _try_deepgram(fake_audio, language="en")
    assert result is None


# ---------------------------------------------------------------------------
# D4. test_try_deepgram_import_error -- returns None when httpx missing
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_import_error():
    """_try_deepgram returns None when httpx import fails."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)

    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def _fail_httpx(name, *args, **kwargs):
        if name == "httpx":
            raise ImportError("No module named 'httpx'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fail_httpx):
        result = _try_deepgram(fake_audio, language="en")

    assert result is None


# ---------------------------------------------------------------------------
# D5. test_try_deepgram_success -- mock client, verify TranscriptionResult
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_success():
    """_try_deepgram returns TranscriptionResult with correct fields on success."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "channels": [{
                "alternatives": [{
                    "transcript": "Hello Conner",
                    "confidence": 0.98,
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.4, "confidence": 0.97},
                        {"word": "Conner", "start": 0.5, "end": 0.9, "confidence": 0.99},
                    ],
                }],
            }],
        },
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = _try_deepgram(fake_audio, language="en")

    assert result is not None
    assert result.text == "Hello Conner"
    assert result.backend == "deepgram-nova3"
    assert result.confidence == 0.98
    assert result.language == "en"
    assert result.duration_seconds >= 0.0
    # Word-level segments should be populated
    assert result.segments is not None
    assert len(result.segments) == 2
    assert result.segments[0]["text"] == "Hello"
    assert result.segments[1]["text"] == "Conner"


# ---------------------------------------------------------------------------
# D6. test_try_deepgram_with_keyterms -- verify keyterms passed in params
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_with_keyterms():
    """_try_deepgram passes keyterms as 'keywords' query params."""
    _reset_keyterms_cache()
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "channels": [{
                "alternatives": [{
                    "transcript": "Jarvis brain status",
                    "confidence": 0.95,
                    "words": [],
                }],
            }],
        },
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        # Pass explicit keyterms
        result = _try_deepgram(
            fake_audio,
            language="en",
            keyterms=["Jarvis", "Conner", "brain status"],
        )

    assert result is not None
    assert result.text == "Jarvis brain status"

    # Verify the 'keywords' params were passed in the API call
    call_kwargs = mock_client.post.call_args
    params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", [])
    keyword_values = [v for k, v in params if k == "keywords"]
    assert "Jarvis" in keyword_values
    assert "Conner" in keyword_values
    assert "brain status" in keyword_values
    _reset_keyterms_cache()


# ---------------------------------------------------------------------------
# D7. test_try_deepgram_api_error -- returns None on error
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_api_error():
    """_try_deepgram returns None when the API returns an error or raises."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Test 1: Non-200 status code
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = _try_deepgram(fake_audio, language="en")

    assert result is None

    # Test 2: Exception raised
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = Exception("Connection refused")
        mock_client_cls.return_value = mock_client

        result = _try_deepgram(fake_audio, language="en")

    assert result is None


# ---------------------------------------------------------------------------
# D8. test_try_deepgram_with_numpy_audio -- verify WAV conversion
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_try_deepgram_with_numpy_audio():
    """_try_deepgram converts numpy audio to WAV bytes before API call."""
    from jarvis_engine.stt import _try_deepgram

    fake_audio = np.random.randn(16000).astype(np.float32)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": {
            "channels": [{
                "alternatives": [{
                    "transcript": "testing audio",
                    "confidence": 0.92,
                    "words": [],
                }],
            }],
        },
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = _try_deepgram(fake_audio, language="en", keyterms=[])

    assert result is not None
    assert result.text == "testing audio"

    # Verify the audio was sent as content (WAV bytes)
    call_kwargs = mock_client.post.call_args
    content = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content", b"")
    # WAV bytes should start with RIFF header
    assert content[:4] == b"RIFF"
    assert content[8:12] == b"WAVE"

    # Content-Type header should be audio/wav
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
    assert headers.get("Content-Type") == "audio/wav"


# ===========================================================================
# 4-tier fallback chain tests (Plan 01-04)
# ===========================================================================


# ---------------------------------------------------------------------------
# FC1. test_transcribe_smart_fallback_chain_order
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_fallback_chain_order():
    """Fallback chain tries backends in order: parakeet -> deepgram -> groq -> local."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)
    call_order = []

    def mock_parakeet(*a, **kw):
        call_order.append("parakeet")
        return None

    def mock_deepgram(*a, **kw):
        call_order.append("deepgram")
        return TranscriptionResult(
            text="low conf", language="en", confidence=0.3,
            duration_seconds=0.5, backend="deepgram-nova3",
        )

    def mock_groq(*a, **kw):
        call_order.append("groq")
        return TranscriptionResult(
            text="hello jarvis", language="en", confidence=0.9,
            duration_seconds=0.4, backend="groq-whisper",
        )

    def mock_local(*a, **kw):
        call_order.append("local")
        return None

    with patch("jarvis_engine.stt._try_parakeet", side_effect=mock_parakeet), \
         patch("jarvis_engine.stt._try_deepgram", side_effect=mock_deepgram), \
         patch("jarvis_engine.stt._try_groq", side_effect=mock_groq), \
         patch("jarvis_engine.stt._try_local_emergency", side_effect=mock_local), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Groq result is used (highest confidence, above threshold)
    assert result.text == "hello jarvis"
    assert result.confidence == 0.9
    assert result.backend == "groq-whisper"
    # Chain tried parakeet, deepgram, groq in order (stopped at groq due to high confidence)
    assert call_order == ["parakeet", "deepgram", "groq"]


# ---------------------------------------------------------------------------
# FC2. test_transcribe_smart_parakeet_primary
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_parakeet_primary():
    """Parakeet with high confidence stops the chain immediately."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    good_result = TranscriptionResult(
        text="hello world", language="en", confidence=0.95,
        duration_seconds=0.2, backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=good_result), \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello world"
    assert result.backend == "parakeet-tdt"
    # Other backends should NOT have been called
    mock_dg.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# FC3. test_transcribe_smart_all_fail_fallback_chain
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_all_fail_fallback_chain():
    """All backends returning None gives empty result with backend='none'."""
    from jarvis_engine.stt import transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio):
        result = transcribe_smart(fake_audio)

    assert result.text == ""
    assert result.confidence == 0.0
    assert result.backend == "none"


# ---------------------------------------------------------------------------
# FC4. test_transcribe_smart_forced_parakeet
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "parakeet"}, clear=False)
def test_transcribe_smart_forced_parakeet():
    """JARVIS_STT_BACKEND=parakeet forces only parakeet backend."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    pk_result = TranscriptionResult(
        text="hello", language="en", confidence=0.9,
        duration_seconds=0.3, backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=pk_result) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello"
    assert result.backend == "parakeet-tdt"
    mock_pk.assert_called_once()
    mock_dg.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# FC5. test_transcribe_smart_forced_deepgram
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "deepgram", "DEEPGRAM_API_KEY": "test-key"}, clear=False)
def test_transcribe_smart_forced_deepgram():
    """JARVIS_STT_BACKEND=deepgram forces only deepgram backend."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    dg_result = TranscriptionResult(
        text="hello jarvis", language="en", confidence=0.88,
        duration_seconds=0.4, backend="deepgram-nova3",
    )

    with patch("jarvis_engine.stt._try_deepgram", return_value=dg_result) as mock_dg, \
         patch("jarvis_engine.stt._try_parakeet") as mock_pk, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello jarvis"
    assert result.backend == "deepgram-nova3"
    mock_dg.assert_called_once()
    mock_pk.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# FC6. test_transcribe_smart_low_confidence_fallthrough
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_low_confidence_fallthrough():
    """Parakeet with low confidence falls through to next backends."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_conf = TranscriptionResult(
        text="maybe hello", language="en", confidence=0.3,
        duration_seconds=0.2, backend="parakeet-tdt",
    )
    high_conf = TranscriptionResult(
        text="hello jarvis", language="en", confidence=0.85,
        duration_seconds=0.5, backend="deepgram-nova3",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=low_conf), \
         patch("jarvis_engine.stt._try_deepgram", return_value=high_conf), \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Deepgram result used (above threshold)
    assert result.text == "hello jarvis"
    assert result.confidence == 0.85
    assert result.backend == "deepgram-nova3"
    # Groq and local should NOT have been called
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# FC7. test_fallback_chain_has_four_entries
# ---------------------------------------------------------------------------

def test_fallback_chain_has_four_entries():
    """FALLBACK_CHAIN module constant has 4 backend entries in correct order."""
    from jarvis_engine.stt import FALLBACK_CHAIN
    assert len(FALLBACK_CHAIN) == 4
    assert FALLBACK_CHAIN[0] == "parakeet"
    assert FALLBACK_CHAIN[1] == "deepgram"
    assert FALLBACK_CHAIN[2] == "groq"
    assert FALLBACK_CHAIN[3] == "local"


# ---------------------------------------------------------------------------
# FC8. test_try_local_emergency_uses_large_v3
# ---------------------------------------------------------------------------

def test_try_local_emergency_uses_large_v3():
    """_try_local_emergency creates SpeechToText with model_size='large-v3'."""
    import jarvis_engine.stt as stt_mod

    # Reset the singleton
    original = stt_mod._local_emergency_instance
    stt_mod._local_emergency_instance = None

    try:
        with patch("jarvis_engine.stt.SpeechToText") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.transcribe_audio.return_value = MagicMock(
                text="hello", language="en", confidence=0.8,
                duration_seconds=1.0, backend="faster-whisper"
            )
            mock_cls.return_value = mock_instance

            stt_mod._try_local_emergency(
                np.zeros(16000, dtype=np.float32), language="en"
            )

            # Verify large-v3 model was requested
            mock_cls.assert_called_once_with(model_size="large-v3")
    finally:
        stt_mod._local_emergency_instance = original


# ---------------------------------------------------------------------------
# FC9. test_forced_parakeet_failure
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"JARVIS_STT_BACKEND": "parakeet"}, clear=False)
def test_forced_parakeet_failure():
    """Forced parakeet mode returns error result when parakeet fails."""
    from jarvis_engine.stt import transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio):
        result = transcribe_smart(fake_audio)

    assert result.text == ""
    assert result.backend == "parakeet-failed"


# ===========================================================================
# INTEGRATION TESTS: Full STT pipeline end-to-end (Plan 01-05)
# ===========================================================================


# ---------------------------------------------------------------------------
# INT-1. test_full_pipeline_parakeet_happy_path
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_parakeet_happy_path():
    """Parakeet returns high-confidence result; post-processing applied, metric logged,
    other backends never called."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    parakeet_result = TranscriptionResult(
        text="turn on the lights",
        language="en",
        confidence=0.94,
        duration_seconds=0.3,
        backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=parakeet_result) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram") as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t) as mock_pp, \
         patch("jarvis_engine.stt._log_stt_metric") as mock_metric:
        result = transcribe_smart(fake_audio)

    # Parakeet result used
    assert result.text == "turn on the lights"
    assert result.backend == "parakeet-tdt"
    assert result.confidence == 0.94
    # Post-processing was called
    mock_pp.assert_called_once()
    # Metric was logged with correct backend
    mock_metric.assert_called_once()
    assert mock_metric.call_args[1]["backend"] == "parakeet-tdt"
    # Other backends never called
    mock_pk.assert_called_once()
    mock_dg.assert_not_called()
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# INT-2. test_full_pipeline_fallback_to_deepgram
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "fake-dg", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_fallback_to_deepgram():
    """Parakeet fails (returns None), Deepgram succeeds.  Verifies correct fallback."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    deepgram_result = TranscriptionResult(
        text="set a timer for five minutes",
        language="en",
        confidence=0.92,
        duration_seconds=0.8,
        backend="deepgram-nova3",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=None) as mock_pk, \
         patch("jarvis_engine.stt._try_deepgram", return_value=deepgram_result) as mock_dg, \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "set a timer for five minutes"
    assert result.backend == "deepgram-nova3"
    # Parakeet tried first, then Deepgram
    mock_pk.assert_called_once()
    mock_dg.assert_called_once()
    # Groq and local never needed
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# INT-3. test_full_pipeline_fallback_to_groq
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "DEEPGRAM_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_fallback_to_groq():
    """Parakeet fails, Deepgram fails (no API key), Groq succeeds."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    groq_result = TranscriptionResult(
        text="what time is it",
        language="en",
        confidence=0.88,
        duration_seconds=1.2,
        backend="groq-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=groq_result) as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "what time is it"
    assert result.backend == "groq-whisper"
    assert result.confidence == 0.88
    mock_groq.assert_called_once()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# INT-4. test_full_pipeline_emergency_local
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_emergency_local():
    """All cloud/parakeet fail, faster-whisper large-v3 emergency fallback succeeds."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    emergency_result = TranscriptionResult(
        text="brain status",
        language="en",
        confidence=0.75,
        duration_seconds=3.0,
        backend="faster-whisper",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=emergency_result), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    assert result.text == "brain status"
    assert result.backend == "faster-whisper"
    assert result.confidence == 0.75


# ---------------------------------------------------------------------------
# INT-5. test_full_pipeline_confidence_fallthrough
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "fake-dg", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_confidence_fallthrough():
    """Parakeet returns confidence=0.4 (below threshold), Deepgram returns 0.9.
    Deepgram result used because it exceeds the threshold."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_result = TranscriptionResult(
        text="muffled words",
        language="en",
        confidence=0.4,
        duration_seconds=0.3,
        backend="parakeet-tdt",
    )
    high_result = TranscriptionResult(
        text="clear words here",
        language="en",
        confidence=0.9,
        duration_seconds=0.8,
        backend="deepgram-nova3",
    )

    with patch("jarvis_engine.stt._try_parakeet", return_value=low_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=high_result), \
         patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local_emergency") as mock_local, \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t):
        result = transcribe_smart(fake_audio)

    # Deepgram result used (confidence 0.9 >= threshold 0.6)
    assert result.text == "clear words here"
    assert result.backend == "deepgram-nova3"
    assert result.confidence == 0.9
    # Chain stopped at Deepgram (no need for Groq/local)
    mock_groq.assert_not_called()
    mock_local.assert_not_called()


# ---------------------------------------------------------------------------
# INT-6. test_full_pipeline_postprocessing_integration
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_postprocessing_integration():
    """Post-processing pipeline transforms backend output: filler removal + entity correction."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Backend returns text with fillers
    raw_result = TranscriptionResult(
        text="um uh Hello Connor how are you doing today",
        language="en",
        confidence=0.85,
        duration_seconds=0.5,
        backend="parakeet-tdt",
    )

    # Mock postprocess_transcription to simulate filler removal + entity correction
    def mock_postprocess(text, confidence, **kwargs):
        return "Hello Conner how are you doing today"

    with patch("jarvis_engine.stt._try_parakeet", return_value=raw_result), \
         patch("jarvis_engine.stt._try_deepgram", return_value=None), \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=mock_postprocess):
        result = transcribe_smart(fake_audio)

    # Post-processing applied: fillers removed, entity corrected
    assert result.text == "Hello Conner how are you doing today"
    assert "um" not in result.text
    assert "uh" not in result.text
    assert "Connor" not in result.text


# ---------------------------------------------------------------------------
# INT-7. test_full_pipeline_personal_vocab_flows
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "DEEPGRAM_API_KEY": "fake-dg", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_full_pipeline_personal_vocab_flows():
    """Personal vocab flows to Deepgram as keyterms and to post-processing as entity_list."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    deepgram_result = TranscriptionResult(
        text="ask Conner about the project",
        language="en",
        confidence=0.91,
        duration_seconds=0.7,
        backend="deepgram-nova3",
    )

    vocab_terms = ["Conner", "Jarvis", "Ollama"]

    with patch("jarvis_engine.stt._try_parakeet", return_value=None), \
         patch("jarvis_engine.stt._try_deepgram", return_value=deepgram_result) as mock_dg, \
         patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local_emergency", return_value=None), \
         patch("jarvis_engine.stt._load_keyterms", return_value=vocab_terms), \
         patch("jarvis_engine.stt_postprocess.preprocess_audio", return_value=fake_audio), \
         patch("jarvis_engine.stt_postprocess.postprocess_transcription", side_effect=lambda t, *a, **kw: t) as mock_pp:
        result = transcribe_smart(fake_audio, entity_list=vocab_terms)

    # Verify Deepgram received keyterms
    dg_call_kwargs = mock_dg.call_args[1]
    assert dg_call_kwargs["keyterms"] == vocab_terms

    # Verify postprocess_transcription received entity_list
    pp_call_kwargs = mock_pp.call_args[1]
    assert pp_call_kwargs["entity_list"] == vocab_terms

    assert result.text == "ask Conner about the project"


# ---------------------------------------------------------------------------
# INT-8. test_listen_and_transcribe_uses_new_pipeline
# ---------------------------------------------------------------------------

def test_listen_and_transcribe_uses_new_pipeline():
    """listen_and_transcribe() calls record_from_microphone + transcribe_smart
    and returns the result from the new fallback chain."""
    from jarvis_engine.stt import TranscriptionResult, listen_and_transcribe

    fake_audio = np.zeros(32000, dtype=np.float32)
    pipeline_result = TranscriptionResult(
        text="hello jarvis",
        language="en",
        confidence=0.92,
        duration_seconds=1.5,
        backend="parakeet-tdt",
    )

    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio) as mock_record, \
         patch("jarvis_engine.stt.transcribe_smart", return_value=pipeline_result) as mock_smart:
        result = listen_and_transcribe(max_duration_seconds=10.0)

    # record_from_microphone was called
    mock_record.assert_called_once_with(max_duration_seconds=10.0)
    # transcribe_smart was called with the recorded audio
    mock_smart.assert_called_once()
    call_args = mock_smart.call_args
    np.testing.assert_array_equal(call_args[0][0], fake_audio)
    # Result comes from transcribe_smart
    assert result.text == "hello jarvis"
    assert result.backend == "parakeet-tdt"


# ---------------------------------------------------------------------------
# INT-9. test_caller_voice_handler_integration
# ---------------------------------------------------------------------------

def test_caller_voice_handler_integration():
    """VoiceListenHandler calls listen_and_transcribe() and works with new return format."""
    from jarvis_engine.stt import TranscriptionResult
    from jarvis_engine.handlers.voice_handlers import VoiceListenHandler
    from jarvis_engine.commands.voice_commands import VoiceListenCommand

    pipeline_result = TranscriptionResult(
        text="set timer for five minutes",
        language="en",
        confidence=0.93,
        duration_seconds=2.1,
        backend="deepgram-nova3",
        segments=[{"start": 0.0, "end": 2.0, "text": "set timer for five minutes"}],
    )

    handler = VoiceListenHandler(root=Path("."))

    with patch("jarvis_engine.stt.listen_and_transcribe", return_value=pipeline_result):
        result = handler.handle(VoiceListenCommand())

    assert result.text == "set timer for five minutes"
    assert result.confidence == 0.93
    assert result.duration_seconds == 2.1
    assert result.message == ""


# ---------------------------------------------------------------------------
# INT-10. test_caller_proactive_handler_integration
# ---------------------------------------------------------------------------

def test_caller_proactive_handler_integration():
    """WakeWordStartHandler._on_detected() callback works with new
    record_from_microphone (Silero VAD) and transcribe_smart (fallback chain)."""
    from jarvis_engine.stt import TranscriptionResult
    from jarvis_engine.handlers.proactive_handlers import WakeWordStartHandler
    from jarvis_engine.commands.proactive_commands import WakeWordStartCommand

    fake_audio = np.zeros(16000, dtype=np.float32)
    pipeline_result = TranscriptionResult(
        text="jarvis brain status",
        language="en",
        confidence=0.95,
        duration_seconds=1.0,
        backend="parakeet-tdt",
    )

    handler = WakeWordStartHandler(root=Path("."))

    # Mock the WakeWordDetector so start() captures the on_detected callback
    # then we invoke it manually to test the integration.
    # WakeWordDetector is lazy-imported inside handle(), so patch at its source.
    captured_callback = {}

    class MockDetector:
        def __init__(self, **kwargs):
            self.pause_called = False
            self.resume_called = False

        def start(self, on_detected, stop_event=None, mic_lock=None):
            captured_callback["fn"] = on_detected
            # Simulate detection loop ending immediately
            if stop_event:
                stop_event.set()

        def pause(self):
            self.pause_called = True

        def resume(self, sd_module=None):
            self.resume_called = True

    with patch("jarvis_engine.wakeword.WakeWordDetector", MockDetector):
        handler.handle(WakeWordStartCommand(threshold=0.5))

    # Now invoke the captured callback with mocked STT functions
    assert "fn" in captured_callback, "on_detected callback should have been captured"

    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio), \
         patch("jarvis_engine.stt.transcribe_smart", return_value=pipeline_result), \
         patch("jarvis_engine.stt_postprocess._load_personal_vocab", return_value=["Conner"]), \
         patch("jarvis_engine.handlers.proactive_handlers._time_mod") as mock_time:
        mock_time.sleep = MagicMock()
        mock_time.time.return_value = 0.0
        # The callback will try to dispatch via _cmd_voice_run_impl
        with patch("jarvis_engine.main._cmd_voice_run_impl"):
            with patch("jarvis_engine.main.repo_root", return_value=Path(".")):
                captured_callback["fn"]()

    # If we got here without error, the callback worked with the new pipeline
