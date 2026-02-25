"""Tests for speech-to-text pipeline and VoiceListen command."""

from __future__ import annotations

import json
import os
import sys
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
    assert cmd.model_size == "small.en"


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
    """When primary backend returns confidence < 0.6, retry with alternative."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Primary: Groq returns low confidence
    low_conf_result = TranscriptionResult(
        text="hmm maybe",
        language="en",
        confidence=0.4,
        duration_seconds=0.5,
        backend="groq-whisper",
    )
    # Retry: local returns higher confidence
    high_conf_result = TranscriptionResult(
        text="hello jarvis",
        language="en",
        confidence=0.9,
        duration_seconds=1.0,
        backend="faster-whisper",
    )

    with patch("jarvis_engine.stt.transcribe_groq", return_value=low_conf_result), \
         patch("jarvis_engine.stt._try_local", return_value=high_conf_result):
        result = transcribe_smart(fake_audio)

    assert result.text == "hello jarvis"
    assert result.confidence == 0.9
    assert result.backend == "faster-whisper"
    assert result.retried is True


# ---------------------------------------------------------------------------
# 15. Higher-confidence result is kept after retry
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_confidence_retry_keeps_higher_confidence() -> None:
    """When retry has lower confidence than primary, keep the primary."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Primary: Groq returns low confidence
    primary = TranscriptionResult(
        text="set timer",
        language="en",
        confidence=0.5,
        duration_seconds=0.5,
        backend="groq-whisper",
    )
    # Retry: local returns EVEN LOWER confidence
    retry = TranscriptionResult(
        text="set time",
        language="en",
        confidence=0.3,
        duration_seconds=1.0,
        backend="faster-whisper",
    )

    with patch("jarvis_engine.stt.transcribe_groq", return_value=primary), \
         patch("jarvis_engine.stt._try_local", return_value=retry):
        result = transcribe_smart(fake_audio)

    # Original primary should be kept (higher confidence)
    assert result.text == "set timer"
    assert result.confidence == 0.5
    assert result.backend == "groq-whisper"
    assert result.retried is True  # retry was attempted


# ---------------------------------------------------------------------------
# 16. No retry when confidence >= 0.6
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_no_retry_when_confidence_sufficient() -> None:
    """When confidence >= 0.6, no retry should be attempted."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    good_result = TranscriptionResult(
        text="turn on lights",
        language="en",
        confidence=0.85,
        duration_seconds=0.5,
        backend="groq-whisper",
    )

    with patch("jarvis_engine.stt.transcribe_groq", return_value=good_result) as mock_groq, \
         patch("jarvis_engine.stt._try_local") as mock_local:
        result = transcribe_smart(fake_audio)

    assert result.text == "turn on lights"
    assert result.confidence == 0.85
    assert result.retried is False
    # _try_local should NOT have been called for retry
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

    # Should not raise
    _log_stt_metric(
        None,
        backend="groq-whisper",
        confidence=0.95,
        latency_ms=300.0,
        text_length=10,
    )


# ---------------------------------------------------------------------------
# 19. Retry is graceful -- failed retry returns original result
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_confidence_retry_graceful_on_failure() -> None:
    """If retry backend fails, the original low-confidence result is returned."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    low_conf = TranscriptionResult(
        text="something",
        language="en",
        confidence=0.3,
        duration_seconds=0.5,
        backend="groq-whisper",
    )

    with patch("jarvis_engine.stt.transcribe_groq", return_value=low_conf), \
         patch("jarvis_engine.stt._try_local", return_value=None):
        result = transcribe_smart(fake_audio)

    # Original result returned even though confidence is low
    assert result.text == "something"
    assert result.confidence == 0.3
    assert result.retried is False  # retry failed, not marked


# ---------------------------------------------------------------------------
# 20. Local primary retries with Groq when GROQ_API_KEY available
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_local_primary_no_retry_without_groq_key() -> None:
    """When local is primary and no GROQ_API_KEY, no retry is attempted."""
    from jarvis_engine.stt import TranscriptionResult, transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    local_result = TranscriptionResult(
        text="maybe hello",
        language="en",
        confidence=0.4,
        duration_seconds=1.0,
        backend="faster-whisper",
    )

    with patch("jarvis_engine.stt._try_groq") as mock_groq, \
         patch("jarvis_engine.stt._try_local", return_value=local_result):
        result = transcribe_smart(fake_audio)

    assert result.text == "maybe hello"
    assert result.confidence == 0.4
    # _try_groq should NOT have been called (no API key)
    mock_groq.assert_not_called()


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
        with patch("jarvis_engine.stt.transcribe_groq", return_value=good_result):
            result = transcribe_smart(fake_audio, root_dir=root)

        assert result.text == "turn on lights"

        metrics_path = root / ".planning" / "runtime" / "stt_metrics.jsonl"
        assert metrics_path.exists()
        lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert record["backend"] == "groq-whisper"
        assert record["confidence"] == 0.92


# ---------------------------------------------------------------------------
# 22. Groq transcription computes real confidence from segment logprobs
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_transcription_real_confidence() -> None:
    """transcribe_groq extracts confidence from segment avg_logprob."""
    import math
    from jarvis_engine.stt import transcribe_groq

    fake_audio = np.zeros(16000, dtype=np.float32)

    # Simulate a Groq verbose_json response with segments
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "text": "hello jarvis",
        "language": "en",
        "segments": [
            {"text": "hello jarvis", "avg_logprob": -0.15, "no_speech_prob": 0.02},
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
    # Confidence from exp(-0.15) * (1 - 0.02*0.5) ≈ 0.86 * 0.99 ≈ 0.851
    expected = round(math.exp(-0.15) * (1.0 - 0.02 * 0.5), 4)
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

    assert result.confidence == 0.85


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
    import math
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

    avg_logprob = (-0.1 + -0.2) / 2
    avg_no_speech = (0.01 + 0.03) / 2
    expected = round(math.exp(avg_logprob) * (1.0 - avg_no_speech * 0.5), 4)
    assert result.confidence == expected


# ---------------------------------------------------------------------------
# 30. Groq confidence with empty segments list
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_empty_segments_list() -> None:
    """Empty segments list falls back to 0.85 confidence."""
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

    assert result.confidence == 0.85


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
    assert result.confidence == 0.85


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
         patch("jarvis_engine.stt._try_local") as mock_local:
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

    with patch("jarvis_engine.stt.SpeechToText.transcribe_audio", return_value=local_result), \
         patch("jarvis_engine.stt._try_groq") as mock_groq:
        result = transcribe_smart(fake_audio)

    assert result.backend == "faster-whisper"
    mock_groq.assert_not_called()


# ---------------------------------------------------------------------------
# 34. transcribe_smart auto mode -- all backends fail
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "", "JARVIS_STT_BACKEND": "auto"}, clear=False)
def test_transcribe_smart_all_backends_fail() -> None:
    """When all backends fail, returns empty result with backend='none'."""
    from jarvis_engine.stt import transcribe_smart

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt._try_groq", return_value=None), \
         patch("jarvis_engine.stt._try_local", return_value=None):
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
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], SimpleNamespace(language="en", language_probability=0.5))
    stt._model = mock_model

    audio = np.zeros(16000, dtype=np.float32)
    stt.transcribe_audio(audio, vad_filter=False)

    mock_model.transcribe.assert_called_once_with(audio, language="en", vad_filter=False)


# ---------------------------------------------------------------------------
# 42. SpeechToText transcribe_audio with file path
# ---------------------------------------------------------------------------

def test_transcribe_audio_accepts_file_path() -> None:
    """transcribe_audio can accept a string file path."""
    from jarvis_engine.stt import SpeechToText

    stt = SpeechToText()
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (
        [SimpleNamespace(text=" from file ")],
        SimpleNamespace(language="en", language_probability=0.88),
    )
    stt._model = mock_model

    result = stt.transcribe_audio("/tmp/audio.wav")
    assert result.text == "from file"
    mock_model.transcribe.assert_called_once_with("/tmp/audio.wav", language="en", vad_filter=True)


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
    import math
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

    # exp(-5.0) is the clamped value, not exp(-100)
    expected = round(math.exp(-5.0) * 1.0, 4)
    assert result.confidence == expected


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

    # NaN is skipped, so no logprobs available -> fallback to 0.85
    assert result.confidence == 0.85


# ---------------------------------------------------------------------------
# 50. Groq transcription with high no_speech_prob
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"GROQ_API_KEY": "fake-key"}, clear=False)
def test_groq_confidence_high_no_speech_penalty() -> None:
    """High no_speech_prob reduces confidence."""
    import math
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

    # High no_speech_prob penalizes confidence
    expected = round(math.exp(-0.1) * (1.0 - 0.9 * 0.5), 4)
    assert result.confidence == expected
    assert result.confidence < 0.6  # Should be significantly penalized


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
