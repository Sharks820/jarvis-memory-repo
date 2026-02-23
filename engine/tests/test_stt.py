"""Tests for speech-to-text pipeline and VoiceListen command."""

from __future__ import annotations

import sys
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

    mock_segment = SimpleNamespace(text=" hello jarvis ")
    mock_info = SimpleNamespace(language="en", language_probability=0.99)

    mock_model_instance = MagicMock()
    mock_model_instance.transcribe.return_value = ([mock_segment], mock_info)

    with patch(
        "jarvis_engine.stt.record_from_microphone", return_value=fake_audio
    ), patch(
        "jarvis_engine.stt.SpeechToText._ensure_model"
    ) as mock_ensure:
        # After _ensure_model is called, we want the _model to be set
        from jarvis_engine.stt import listen_and_transcribe, SpeechToText

        # We need to mock _ensure_model to set _model
        def _set_mock_model(self: SpeechToText = None) -> None:  # type: ignore[assignment]
            pass

        with patch.object(SpeechToText, "_ensure_model", _set_mock_model), \
             patch.object(SpeechToText, "_model", mock_model_instance, create=True):
            # Patch at the instance level by intercepting the transcribe_audio call
            pass

    # Cleaner approach: mock transcribe_audio directly
    mock_transcription = TranscriptionResult(
        text="hello jarvis",
        language="en",
        confidence=0.99,
        duration_seconds=1.2,
    )
    with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio), \
         patch.object(
             __import__("jarvis_engine.stt", fromlist=["SpeechToText"]).SpeechToText,
             "transcribe_audio",
             return_value=mock_transcription,
         ):
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
