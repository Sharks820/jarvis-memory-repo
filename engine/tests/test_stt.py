"""Tests for speech-to-text pipeline and VoiceListen command."""

from __future__ import annotations

import json
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
