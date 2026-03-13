"""Tests for core SpeechToText class, TranscriptionResult, and basic transcription."""

from __future__ import annotations

import importlib
import inspect
import os
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
    assert r.segments is None
    assert r.utterance is None
    assert r.message == ""


# ---------------------------------------------------------------------------
# 13. TranscriptionResult retried field default
# ---------------------------------------------------------------------------

def test_transcription_result_retried_default() -> None:
    from jarvis_engine.stt import TranscriptionResult

    r = TranscriptionResult()
    assert r.retried is False


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
            threshold=0.4,
            min_silence_duration_ms=400,
            speech_pad_ms=300,
            min_speech_duration_ms=200,
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
            threshold=0.4,
            min_silence_duration_ms=400,
            speech_pad_ms=300,
            min_speech_duration_ms=200,
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
# 48. TranscriptionResult fields are correct types
# ---------------------------------------------------------------------------

def test_transcription_result_backend_field() -> None:
    """TranscriptionResult.backend defaults to empty string."""
    from jarvis_engine.stt import TranscriptionResult

    r = TranscriptionResult()
    assert r.backend == ""
    assert isinstance(r.retried, bool)


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
# 58. Env var for confidence threshold
# ---------------------------------------------------------------------------

def test_confidence_threshold_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """JARVIS_STT_CONFIDENCE_THRESHOLD env var overrides default threshold."""
    monkeypatch.setenv("JARVIS_STT_CONFIDENCE_THRESHOLD", "0.8")
    # Re-import to pick up the new env var value
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
# 36. _try_local returns None on failure
# ---------------------------------------------------------------------------

def test_try_local_returns_none_on_exception() -> None:
    """_try_local catches exceptions and returns None."""
    from jarvis_engine.stt import _try_local

    fake_audio = np.zeros(16000, dtype=np.float32)

    with patch("jarvis_engine.stt.SpeechToText.transcribe_audio", side_effect=RuntimeError("model error")):
        result = _try_local(fake_audio, language="en")

    assert result is None
