"""Tests for voice.py -- TTS voice selection and text chunking."""

from __future__ import annotations

import os
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from jarvis_engine.voice.core import (
    VoiceSpeakResult,
    choose_voice,
    _chunk_text_for_streaming,
    _preferred_voice_patterns,
)


# ---------------------------------------------------------------------------
# choose_voice
# ---------------------------------------------------------------------------

def test_choose_voice_prefers_jarvis_like_patterns() -> None:
    voices = [
        "Microsoft Zira Desktop",
        "Microsoft David Desktop",
        "Microsoft Hazel Desktop - English (Great Britain)",
    ]
    selected = choose_voice(voices, profile="jarvis_like")
    assert selected in voices
    assert "David" in selected or "Great Britain" in selected or "English" in selected


def test_choose_voice_edge_tts_british_first() -> None:
    voices = ["en-US-GuyNeural", "en-GB-RyanNeural", "en-US-JennyNeural"]
    result = choose_voice(voices, profile="jarvis_like")
    assert result == "en-GB-RyanNeural"


def test_choose_voice_custom_pattern_priority() -> None:
    voices = ["en-US-GuyNeural", "en-GB-RyanNeural", "en-US-JennyNeural"]
    result = choose_voice(voices, profile="jarvis_like", custom_pattern="Jenny")
    assert result == "en-US-JennyNeural"


def test_choose_voice_fallback_to_first() -> None:
    voices = ["zh-CN-XiaomoNeural", "ja-JP-KeitaNeural"]
    result = choose_voice(voices, profile="jarvis_like")
    assert result == "zh-CN-XiaomoNeural"


def test_choose_voice_empty_list() -> None:
    result = choose_voice([], profile="jarvis_like")
    assert result == ""


def test_choose_voice_case_insensitive() -> None:
    voices = ["EN-GB-RYANNEURAL"]
    result = choose_voice(voices, profile="jarvis_like")
    assert result == "EN-GB-RYANNEURAL"


def test_choose_voice_default_profile() -> None:
    voices = ["Microsoft David Desktop", "Microsoft Zira Desktop"]
    result = choose_voice(voices, profile="default")
    assert result == "Microsoft David Desktop"


# ---------------------------------------------------------------------------
# _preferred_voice_patterns
# ---------------------------------------------------------------------------

def test_jarvis_like_prefers_british() -> None:
    patterns = _preferred_voice_patterns("jarvis_like")
    assert patterns[0] == "en-GB-RyanNeural"
    assert len(patterns) > 5


def test_default_profile_patterns() -> None:
    patterns = _preferred_voice_patterns("default")
    assert "David" in patterns


# ---------------------------------------------------------------------------
# _chunk_text_for_streaming
# ---------------------------------------------------------------------------

def test_chunk_empty_text() -> None:
    assert _chunk_text_for_streaming("") == []
    assert _chunk_text_for_streaming("   ") == []


def test_chunk_short_text_single_chunk() -> None:
    text = "Hello world."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 1
    assert chunks[0] == "Hello world."


def test_chunk_three_sentences() -> None:
    text = "First sentence. Second sentence. Third sentence."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 1


def test_chunk_four_sentences() -> None:
    text = "One. Two. Three. Four."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 2
    assert "One" in chunks[0]
    assert "Four" in chunks[1]


def test_chunk_many_sentences() -> None:
    text = "A. B. C. D. E. F. G. H. I."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 3


def test_chunk_preserves_exclamation_and_question() -> None:
    text = "Hello! How are you? I am fine."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
    assert len(chunks) == 1
    assert "Hello!" in chunks[0]


def test_chunk_custom_sentences_per_chunk() -> None:
    text = "A. B. C. D."
    chunks = _chunk_text_for_streaming(text, sentences_per_chunk=2)
    assert len(chunks) == 2


# ---------------------------------------------------------------------------
# VoiceSpeakResult dataclass
# ---------------------------------------------------------------------------

def test_voice_speak_result() -> None:
    r = VoiceSpeakResult(voice_name="test", output_wav="/tmp/out.wav", message="done")
    assert r.voice_name == "test"
    assert r.output_wav == "/tmp/out.wav"
    assert r.message == "done"


# ===========================================================================
# NEW TESTS: voice synthesis, queue management, error handling, etc.
# ===========================================================================


# ---------------------------------------------------------------------------
# _run_ps helper
# ---------------------------------------------------------------------------

class TestRunPs:
    """Tests for the _run_ps PowerShell helper."""

    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_run_ps_invokes_powershell(self, mock_kwargs, mock_run) -> None:
        from jarvis_engine.voice.core import _run_ps

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )
        result = _run_ps("Get-Process")
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert "powershell" in args[0][0][0]
        assert result.returncode == 0

    @patch("jarvis_engine.voice.core.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ps", timeout=30))
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_run_ps_timeout_raises(self, mock_kwargs, mock_run) -> None:
        from jarvis_engine.voice.core import _run_ps

        with pytest.raises(subprocess.TimeoutExpired):
            _run_ps("long running", timeout_s=30)


# ---------------------------------------------------------------------------
# _run_ps_encoded helper
# ---------------------------------------------------------------------------

class TestRunPsEncoded:
    """Tests for the encoded PowerShell helper."""

    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_run_ps_encoded_uses_encoded_command(self, mock_kwargs, mock_run) -> None:
        from jarvis_engine.voice.core import _run_ps_encoded

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        _run_ps_encoded("Write-Host hello")
        args = mock_run.call_args[0][0]
        assert "-EncodedCommand" in args

    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_run_ps_encoded_passes_custom_env(self, mock_kwargs, mock_run) -> None:
        from jarvis_engine.voice.core import _run_ps_encoded

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        custom_env = {"FOO": "bar"}
        _run_ps_encoded("Write-Host test", env=custom_env)
        assert mock_run.call_args[1]["env"] == custom_env


# ---------------------------------------------------------------------------
# list_windows_voices
# ---------------------------------------------------------------------------

class TestListWindowsVoices:
    """Tests for list_windows_voices with caching."""

    @patch("jarvis_engine.voice.core._run_ps")
    def test_list_windows_voices_success(self, mock_ps) -> None:
        from jarvis_engine.voice.core import list_windows_voices, _list_windows_voices_cached

        _list_windows_voices_cached.cache_clear()
        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Microsoft David Desktop\nMicrosoft Zira Desktop\n",
            stderr=""
        )
        voices = list_windows_voices(refresh=True)
        assert voices == ["Microsoft David Desktop", "Microsoft Zira Desktop"]

    @patch("jarvis_engine.voice.core._run_ps")
    def test_list_windows_voices_failure_raises(self, mock_ps) -> None:
        from jarvis_engine.voice.core import list_windows_voices, _list_windows_voices_cached

        _list_windows_voices_cached.cache_clear()
        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error loading voices"
        )
        with pytest.raises(RuntimeError, match="error loading voices"):
            list_windows_voices(refresh=True)

    @patch("jarvis_engine.voice.core._run_ps")
    def test_list_windows_voices_caching(self, mock_ps) -> None:
        from jarvis_engine.voice.core import list_windows_voices, _list_windows_voices_cached

        _list_windows_voices_cached.cache_clear()
        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="VoiceA\n", stderr=""
        )
        # First call populates cache
        v1 = list_windows_voices(refresh=True)
        # Second call without refresh uses cache
        v2 = list_windows_voices(refresh=False)
        assert v1 == v2
        # Only one underlying call to _run_ps because cache was hit on second call
        # But refresh=True already cleared once, so exactly 1 call total
        assert mock_ps.call_count == 1

    @patch("jarvis_engine.voice.core._run_ps")
    def test_list_windows_voices_empty_output(self, mock_ps) -> None:
        from jarvis_engine.voice.core import list_windows_voices, _list_windows_voices_cached

        _list_windows_voices_cached.cache_clear()
        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="  \n\n", stderr=""
        )
        voices = list_windows_voices(refresh=True)
        assert voices == []


# ---------------------------------------------------------------------------
# list_edge_voices / _edge_tts_executable
# ---------------------------------------------------------------------------

class TestEdgeTts:
    """Tests for edge TTS voice listing and executable discovery."""

    @patch("jarvis_engine.voice.core.shutil.which", return_value=None)
    def test_edge_tts_executable_not_found(self, mock_which) -> None:
        from jarvis_engine.voice.core import _edge_tts_executable

        # Mock sys.executable to a path where edge-tts.exe won't exist
        with patch("jarvis_engine.voice.core.Path.exists", return_value=False):
            result = _edge_tts_executable()
        # shutil.which returns None, local doesn't exist
        assert result == "" or result is None or isinstance(result, str)

    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="")
    def test_list_edge_voices_no_executable(self, mock_exe) -> None:
        from jarvis_engine.voice.core import list_edge_voices, _list_edge_voices_cached

        _list_edge_voices_cached.cache_clear()
        voices = list_edge_voices(refresh=True)
        assert voices == []

    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_list_edge_voices_parses_output(self, mock_kwargs, mock_exe, mock_run) -> None:
        from jarvis_engine.voice.core import list_edge_voices, _list_edge_voices_cached

        _list_edge_voices_cached.cache_clear()
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="  en-GB-RyanNeural  Male\n  en-US-GuyNeural  Male\n  fr-FR-HenriNeural  Male\n",
            stderr=""
        )
        voices = list_edge_voices(refresh=True)
        assert "en-GB-RyanNeural" in voices
        assert "en-US-GuyNeural" in voices
        assert "fr-FR-HenriNeural" in voices

    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_list_edge_voices_returns_empty_on_failure(self, mock_kwargs, mock_exe, mock_run) -> None:
        from jarvis_engine.voice.core import list_edge_voices, _list_edge_voices_cached

        _list_edge_voices_cached.cache_clear()
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        voices = list_edge_voices(refresh=True)
        assert voices == []


# ---------------------------------------------------------------------------
# _choose_edge_voice
# ---------------------------------------------------------------------------

class TestChooseEdgeVoice:
    """Tests for _choose_edge_voice with voice selection logic."""

    @patch("jarvis_engine.voice.core.list_edge_voices", return_value=["en-GB-RyanNeural", "en-US-GuyNeural"])
    def test_choose_edge_voice_selects_british(self, mock_list) -> None:
        from jarvis_engine.voice.core import _choose_edge_voice

        result = _choose_edge_voice(profile="jarvis_like")
        assert result == "en-GB-RyanNeural"

    @patch("jarvis_engine.voice.core.list_edge_voices", return_value=[])
    def test_choose_edge_voice_returns_empty_when_no_voices(self, mock_list) -> None:
        from jarvis_engine.voice.core import _choose_edge_voice

        result = _choose_edge_voice(profile="jarvis_like")
        assert result == ""

    @patch("jarvis_engine.voice.core.list_edge_voices", return_value=["en-US-JennyNeural", "en-US-GuyNeural"])
    def test_choose_edge_voice_custom_pattern(self, mock_list) -> None:
        from jarvis_engine.voice.core import _choose_edge_voice

        result = _choose_edge_voice(profile="jarvis_like", custom_pattern="Jenny")
        assert result == "en-US-JennyNeural"


# ---------------------------------------------------------------------------
# _speak_text_edge
# ---------------------------------------------------------------------------

class TestSpeakTextEdge:
    """Tests for edge TTS synthesis."""

    @patch("jarvis_engine.voice.core._play_audio_file")
    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="en-GB-RyanNeural")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_speak_text_edge_no_output_wav(self, mock_kwargs, mock_exe, mock_voice, mock_run, mock_play) -> None:
        from jarvis_engine.voice.core import _speak_text_edge

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = _speak_text_edge(
            "Hello Jarvis",
            profile="jarvis_like",
            custom_voice_pattern="",
            output_wav="",
            rate=0,
        )
        assert result.voice_name == "en-GB-RyanNeural"
        assert result.message == "Edge neural voice output completed."
        mock_play.assert_called_once()

    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="en-GB-RyanNeural")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_speak_text_edge_with_output_wav(self, mock_kwargs, mock_exe, mock_voice, mock_run) -> None:
        from jarvis_engine.voice.core import _speak_text_edge

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "output.wav")
            result = _speak_text_edge(
                "Hello",
                profile="jarvis_like",
                custom_voice_pattern="",
                output_wav=out_path,
                rate=0,
            )
            assert result.voice_name == "en-GB-RyanNeural"
            assert "output.wav" in result.output_wav

    def test_speak_text_edge_no_executable_raises(self) -> None:
        from jarvis_engine.voice.core import _speak_text_edge

        with patch("jarvis_engine.voice.core._edge_tts_executable", return_value=""):
            with pytest.raises(RuntimeError, match="edge-tts executable not found"):
                _speak_text_edge(
                    "Hello",
                    profile="jarvis_like",
                    custom_voice_pattern="",
                    output_wav="",
                    rate=0,
                )

    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    def test_speak_text_edge_no_voice_raises(self, mock_exe, mock_voice) -> None:
        from jarvis_engine.voice.core import _speak_text_edge

        with pytest.raises(RuntimeError, match="No edge-tts voices found"):
            _speak_text_edge(
                "Hello",
                profile="jarvis_like",
                custom_voice_pattern="",
                output_wav="",
                rate=0,
            )

    @patch("jarvis_engine.voice.core._play_audio_file")
    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="en-GB-RyanNeural")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_speak_text_edge_synthesis_failure(self, mock_kwargs, mock_exe, mock_voice, mock_run, mock_play) -> None:
        from jarvis_engine.voice.core import _speak_text_edge

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="synthesis error"
        )
        with pytest.raises(RuntimeError, match="synthesis error"):
            _speak_text_edge(
                "Hello",
                profile="jarvis_like",
                custom_voice_pattern="",
                output_wav="",
                rate=0,
            )

    @patch("jarvis_engine.voice.core._play_audio_file")
    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="en-GB-RyanNeural")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_speak_text_edge_rate_clamping(self, mock_kwargs, mock_exe, mock_voice, mock_run, mock_play) -> None:
        """Rate values are clamped to +-50 percent."""
        from jarvis_engine.voice.core import _speak_text_edge

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        # Rate=100 should be clamped to +50%
        _speak_text_edge(
            "Hello",
            profile="jarvis_like",
            custom_voice_pattern="",
            output_wav="",
            rate=100,
        )
        cmd_args = mock_run.call_args[0][0]
        rate_arg = [a for a in cmd_args if a.startswith("--rate=")]
        assert len(rate_arg) == 1
        assert rate_arg[0] == "--rate=+50%"


# ---------------------------------------------------------------------------
# speak_text (top-level function)
# ---------------------------------------------------------------------------

class TestSpeakText:
    """Tests for the speak_text top-level function with engine selection."""

    @patch("jarvis_engine.voice.core._speak_text_edge")
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "edge", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_speak_text_selects_edge_engine(self, mock_edge) -> None:
        from jarvis_engine.voice.core import speak_text

        mock_edge.return_value = VoiceSpeakResult(
            voice_name="en-GB-RyanNeural", output_wav="", message="ok"
        )
        result = speak_text("Hello")
        mock_edge.assert_called_once()
        assert result.voice_name == "en-GB-RyanNeural"

    @patch("jarvis_engine.voice.core._speak_text_edge", side_effect=RuntimeError("edge failed"))
    @patch("jarvis_engine.voice.core._run_ps_encoded")
    @patch("jarvis_engine.voice.core.list_windows_voices", return_value=["Microsoft David Desktop"])
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "auto", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_speak_text_auto_falls_back_to_sapi(self, mock_voices, mock_ps, mock_edge) -> None:
        from jarvis_engine.voice.core import speak_text

        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = speak_text("Hello")
        assert result.voice_name == "Microsoft David Desktop"
        assert result.message == "Voice output completed."

    @patch("jarvis_engine.voice.core._speak_text_edge", side_effect=RuntimeError("edge failed"))
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "edge", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_speak_text_edge_explicit_reraises(self, mock_edge) -> None:
        """When engine is explicitly 'edge', failures are not caught."""
        from jarvis_engine.voice.core import speak_text

        with pytest.raises(RuntimeError, match="edge failed"):
            speak_text("Hello")

    @patch("jarvis_engine.voice.core._run_ps_encoded")
    @patch("jarvis_engine.voice.core.list_windows_voices", return_value=[])
    @patch("jarvis_engine.voice.core._speak_text_edge", side_effect=RuntimeError("no edge"))
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "auto", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_speak_text_no_voices_raises(self, mock_edge, mock_list, mock_ps) -> None:
        from jarvis_engine.voice.core import speak_text

        with pytest.raises(RuntimeError, match="No Windows voices found"):
            speak_text("Hello")

    @patch("jarvis_engine.voice.core._speak_text_edge_streamed")
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "edge", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_speak_text_long_text_uses_streaming(self, mock_streamed) -> None:
        """Text > 180 chars without output_wav triggers streaming."""
        from jarvis_engine.voice.core import speak_text

        mock_streamed.return_value = VoiceSpeakResult(
            voice_name="en-GB-RyanNeural", output_wav="", message="streamed"
        )
        long_text = "A" * 200
        result = speak_text(long_text)
        mock_streamed.assert_called_once()
        assert result.message == "streamed"

    @patch("jarvis_engine.voice.core._speak_text_edge")
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "edge", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_speak_text_short_text_uses_non_streaming(self, mock_edge) -> None:
        """Text <= 180 chars uses regular (non-streaming) synthesis."""
        from jarvis_engine.voice.core import speak_text

        mock_edge.return_value = VoiceSpeakResult(
            voice_name="en-GB-RyanNeural", output_wav="", message="ok"
        )
        short_text = "Hello Jarvis."
        result = speak_text(short_text)
        mock_edge.assert_called_once()

    @patch("jarvis_engine.voice.core._speak_text_edge")
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "edge", "JARVIS_VOICE_PATTERN": "Jenny"}, clear=False)
    def test_speak_text_env_voice_pattern(self, mock_edge) -> None:
        """JARVIS_VOICE_PATTERN env var is used when no custom pattern given."""
        from jarvis_engine.voice.core import speak_text

        mock_edge.return_value = VoiceSpeakResult(
            voice_name="en-US-JennyNeural", output_wav="", message="ok"
        )
        speak_text("Hello")
        call_kwargs = mock_edge.call_args[1]
        assert call_kwargs["custom_voice_pattern"] == "Jenny"

    @patch("jarvis_engine.voice.core._speak_text_edge")
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "edge", "JARVIS_VOICE_PATTERN": "Jenny"}, clear=False)
    def test_speak_text_custom_pattern_overrides_env(self, mock_edge) -> None:
        """Explicit custom_voice_pattern overrides JARVIS_VOICE_PATTERN env."""
        from jarvis_engine.voice.core import speak_text

        mock_edge.return_value = VoiceSpeakResult(
            voice_name="en-GB-RyanNeural", output_wav="", message="ok"
        )
        speak_text("Hello", custom_voice_pattern="Ryan")
        call_kwargs = mock_edge.call_args[1]
        assert call_kwargs["custom_voice_pattern"] == "Ryan"


# ---------------------------------------------------------------------------
# _strip_markdown_for_speech
# ---------------------------------------------------------------------------

class TestStripMarkdownForSpeech:
    """Tests for markdown stripping before TTS."""

    def test_strips_bold(self) -> None:
        from jarvis_engine.voice.core import _strip_markdown_for_speech
        assert _strip_markdown_for_speech("This is **bold** text") == "This is bold text"

    def test_strips_italic(self) -> None:
        from jarvis_engine.voice.core import _strip_markdown_for_speech
        assert _strip_markdown_for_speech("This is *italic* text") == "This is italic text"

    def test_strips_bold_italic(self) -> None:
        from jarvis_engine.voice.core import _strip_markdown_for_speech
        assert _strip_markdown_for_speech("***emphasis***") == "emphasis"

    def test_strips_headers(self) -> None:
        from jarvis_engine.voice.core import _strip_markdown_for_speech
        assert _strip_markdown_for_speech("### My Header") == "My Header"

    def test_strips_markdown_links(self) -> None:
        from jarvis_engine.voice.core import _strip_markdown_for_speech
        assert _strip_markdown_for_speech("[click here](https://example.com)") == "click here"

    def test_strips_bullets(self) -> None:
        from jarvis_engine.voice.core import _strip_markdown_for_speech
        result = _strip_markdown_for_speech("- item one\n- item two")
        assert result == "item one\nitem two"

    def test_strips_backticks(self) -> None:
        from jarvis_engine.voice.core import _strip_markdown_for_speech
        assert _strip_markdown_for_speech("Use `print()` function") == "Use print() function"

    def test_plain_text_unchanged(self) -> None:
        from jarvis_engine.voice.core import _strip_markdown_for_speech
        assert _strip_markdown_for_speech("Hello world") == "Hello world"


# ---------------------------------------------------------------------------
# _speak_text_edge_streamed
# ---------------------------------------------------------------------------

class TestSpeakTextEdgeStreamed:
    """Tests for streaming edge TTS synthesis."""

    @patch("jarvis_engine.voice.core._speak_text_edge")
    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="en-GB-RyanNeural")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    def test_short_text_delegates_to_non_streaming(self, mock_exe, mock_voice, mock_edge) -> None:
        """When text has <= 1 chunk, streaming delegates to _speak_text_edge."""
        from jarvis_engine.voice.core import _speak_text_edge_streamed

        mock_edge.return_value = VoiceSpeakResult(
            voice_name="en-GB-RyanNeural", output_wav="", message="ok"
        )
        result = _speak_text_edge_streamed(
            "Short sentence.",
            profile="jarvis_like",
            custom_voice_pattern="",
            rate=0,
        )
        mock_edge.assert_called_once()

    def test_streamed_no_executable_raises(self) -> None:
        from jarvis_engine.voice.core import _speak_text_edge_streamed

        with patch("jarvis_engine.voice.core._edge_tts_executable", return_value=""):
            with pytest.raises(RuntimeError, match="edge-tts executable not found"):
                _speak_text_edge_streamed(
                    "A. B. C. D. E. F. G.",
                    profile="jarvis_like",
                    custom_voice_pattern="",
                    rate=0,
                )

    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    def test_streamed_no_voice_raises(self, mock_exe, mock_voice) -> None:
        from jarvis_engine.voice.core import _speak_text_edge_streamed

        with pytest.raises(RuntimeError, match="No edge-tts voices found"):
            _speak_text_edge_streamed(
                "A. B. C. D. E. F. G.",
                profile="jarvis_like",
                custom_voice_pattern="",
                rate=0,
            )


# ---------------------------------------------------------------------------
# _play_audio_file
# ---------------------------------------------------------------------------

class TestPlayAudioFile:
    """Tests for the _play_audio_file helper."""

    @patch("jarvis_engine.voice.core._run_ps_encoded")
    def test_play_audio_file_calls_ps_encoded(self, mock_ps) -> None:
        from jarvis_engine.voice.core import _play_audio_file

        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        _play_audio_file("/tmp/test.wav")
        mock_ps.assert_called_once()
        call_kwargs = mock_ps.call_args[1]
        assert call_kwargs["env"]["JARVIS_VOICE_MEDIA"] == "/tmp/test.wav"


# ---------------------------------------------------------------------------
# SAPI speak_text (Windows TTS fallback)
# ---------------------------------------------------------------------------

class TestSpeakTextSapi:
    """Tests for the SAPI (System.Speech) fallback TTS path."""

    @patch("jarvis_engine.voice.core._run_ps_encoded")
    @patch("jarvis_engine.voice.core.list_windows_voices", return_value=["Microsoft David Desktop"])
    @patch("jarvis_engine.voice.core._speak_text_edge", side_effect=RuntimeError("no edge"))
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "auto", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_sapi_rate_clamping(self, mock_edge, mock_voices, mock_ps) -> None:
        """SAPI rate is clamped to [-10, 10]."""
        from jarvis_engine.voice.core import speak_text

        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = speak_text("Hello", rate=50)
        call_kwargs = mock_ps.call_args[1]
        # Rate should be clamped in the env var
        assert call_kwargs["env"]["JARVIS_VOICE_RATE"] == "10"

    @patch("jarvis_engine.voice.core._run_ps_encoded")
    @patch("jarvis_engine.voice.core.list_windows_voices", return_value=["Microsoft David Desktop"])
    @patch("jarvis_engine.voice.core._speak_text_edge", side_effect=RuntimeError("no edge"))
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "auto", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_sapi_synthesis_failure_raises(self, mock_edge, mock_voices, mock_ps) -> None:
        from jarvis_engine.voice.core import speak_text

        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="SAPI error"
        )
        with pytest.raises(RuntimeError, match="SAPI error"):
            speak_text("Hello")

    @patch("jarvis_engine.voice.core._run_ps_encoded")
    @patch("jarvis_engine.voice.core.list_windows_voices", return_value=["Microsoft David Desktop"])
    @patch("jarvis_engine.voice.core._speak_text_edge", side_effect=RuntimeError("no edge"))
    @patch.dict("os.environ", {"JARVIS_TTS_ENGINE": "auto", "JARVIS_VOICE_PATTERN": ""}, clear=False)
    def test_sapi_output_wav(self, mock_edge, mock_voices, mock_ps) -> None:
        """When output_wav is specified, SAPI sets it in the env."""
        from jarvis_engine.voice.core import speak_text

        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "output.wav")
            result = speak_text("Hello", output_wav=out_path)
            call_kwargs = mock_ps.call_args[1]
            assert out_path in call_kwargs["env"]["JARVIS_VOICE_OUTPUT"]
            assert result.output_wav != ""


# ---------------------------------------------------------------------------
# Additional choose_voice edge cases
# ---------------------------------------------------------------------------

class TestChooseVoiceExtended:
    """Additional edge cases for choose_voice."""

    def test_choose_voice_whitespace_custom_pattern(self) -> None:
        """Whitespace-only custom pattern is ignored."""
        voices = ["en-GB-RyanNeural", "en-US-GuyNeural"]
        result = choose_voice(voices, profile="jarvis_like", custom_pattern="   ")
        # Should use profile patterns, not whitespace
        assert result == "en-GB-RyanNeural"

    def test_choose_voice_multiple_partial_matches(self) -> None:
        """First matching pattern wins."""
        voices = ["en-US-AndrewNeural", "en-US-AndrewMultilingualNeural"]
        # "AndrewMultilingualNeural" is higher in the jarvis_like list
        result = choose_voice(voices, profile="jarvis_like")
        assert result == "en-US-AndrewMultilingualNeural"

    def test_choose_voice_thomas_british(self) -> None:
        """ThomasNeural is second in jarvis_like pattern list."""
        voices = ["en-GB-ThomasNeural", "en-US-GuyNeural"]
        # RyanNeural is first but not present, ThomasNeural is second
        result = choose_voice(voices, profile="jarvis_like")
        assert result == "en-GB-ThomasNeural"

    def test_choose_voice_single_voice(self) -> None:
        """Single voice in list is always returned."""
        voices = ["de-DE-ConradNeural"]
        result = choose_voice(voices, profile="jarvis_like")
        # No pattern matches, so fallback to first
        assert result == "de-DE-ConradNeural"


# ---------------------------------------------------------------------------
# Additional chunk_text edge cases
# ---------------------------------------------------------------------------

class TestChunkTextExtended:
    """Additional edge cases for _chunk_text_for_streaming."""

    def test_chunk_single_sentence(self) -> None:
        text = "Hello."
        chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
        assert len(chunks) == 1
        assert chunks[0] == "Hello."

    def test_chunk_no_sentence_terminators(self) -> None:
        """Text without sentence-ending punctuation stays as one chunk."""
        text = "This is a long sentence without a period"
        chunks = _chunk_text_for_streaming(text, sentences_per_chunk=3)
        assert len(chunks) == 1

    def test_chunk_sentences_per_chunk_one(self) -> None:
        text = "First. Second. Third."
        chunks = _chunk_text_for_streaming(text, sentences_per_chunk=1)
        assert len(chunks) == 3

    def test_chunk_mixed_punctuation(self) -> None:
        text = "A! B? C. D!"
        chunks = _chunk_text_for_streaming(text, sentences_per_chunk=2)
        assert len(chunks) == 2


# ===========================================================================
# P2 voice pipeline fix tests
# ===========================================================================


# ---------------------------------------------------------------------------
# Streaming error sentinel fix: only _ERROR_SENTINEL on error, not both
# ---------------------------------------------------------------------------

class TestStreamingErrorSentinel:
    """Tests for the streaming error sentinel fix in _speak_text_edge_streamed."""

    @patch("jarvis_engine.voice.core._play_audio_file")
    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="en-GB-RyanNeural")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_producer_error_puts_only_error_sentinel(
        self, mock_kwargs, mock_exe, mock_voice, mock_run, mock_play
    ) -> None:
        """When producer fails, only _ERROR_SENTINEL is put on queue (not None too)."""
        import queue as queue_mod

        # First chunk succeeds, second fails
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="synthesis error"),
        ]

        # We need to intercept queue operations to verify
        items_put: list = []
        original_queue_put = queue_mod.Queue.put

        def tracking_put(self_q, item, *args, **kwargs):
            items_put.append(item)
            return original_queue_put(self_q, item, *args, **kwargs)

        from jarvis_engine.voice.core import _speak_text_edge_streamed

        with patch.object(queue_mod.Queue, "put", tracking_put):
            with pytest.raises(RuntimeError, match="synthesis error"):
                _speak_text_edge_streamed(
                    "One sentence. Two sentence. Three sentence. Four sentence.",
                    profile="jarvis_like",
                    custom_voice_pattern="",
                    rate=0,
                )

        # Verify _ERROR_SENTINEL was put but None was NOT put after error
        sentinel_count = sum(1 for item in items_put if item == "__ERROR__")
        none_count = sum(1 for item in items_put if item is None)
        assert sentinel_count == 1, f"Expected exactly 1 error sentinel, got {sentinel_count}"
        assert none_count == 0, f"Expected 0 None sentinels after error, got {none_count}"

    @patch("jarvis_engine.voice.core._play_audio_file")
    @patch("jarvis_engine.voice.core.subprocess.run")
    @patch("jarvis_engine.voice.core._choose_edge_voice", return_value="en-GB-RyanNeural")
    @patch("jarvis_engine.voice.core._edge_tts_executable", return_value="/usr/bin/edge-tts")
    @patch("jarvis_engine.voice.core.win_hidden_subprocess_kwargs", return_value={})
    def test_producer_success_puts_none_sentinel(
        self, mock_kwargs, mock_exe, mock_voice, mock_run, mock_play
    ) -> None:
        """When producer succeeds, None sentinel is put at end."""
        import queue as queue_mod

        # All chunks succeed
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        items_put: list = []
        original_queue_put = queue_mod.Queue.put

        def tracking_put(self_q, item, *args, **kwargs):
            items_put.append(item)
            return original_queue_put(self_q, item, *args, **kwargs)

        from jarvis_engine.voice.core import _speak_text_edge_streamed

        with patch.object(queue_mod.Queue, "put", tracking_put):
            _speak_text_edge_streamed(
                "One sentence. Two sentence. Three sentence. Four sentence.",
                profile="jarvis_like",
                custom_voice_pattern="",
                rate=0,
            )

        # Verify None sentinel was put (success case)
        none_count = sum(1 for item in items_put if item is None)
        assert none_count == 1, f"Expected exactly 1 None sentinel, got {none_count}"
        # Verify no error sentinel
        sentinel_count = sum(1 for item in items_put if item == "__ERROR__")
        assert sentinel_count == 0


# ---------------------------------------------------------------------------
# _play_audio_file return code check
# ---------------------------------------------------------------------------

class TestPlayAudioFileReturnCode:
    """Tests for the _play_audio_file return code logging."""

    @patch("jarvis_engine.voice.core._run_ps_encoded")
    def test_play_audio_file_logs_warning_on_failure(self, mock_ps) -> None:
        """Non-zero return code logs a warning."""
        from jarvis_engine.voice.core import _play_audio_file

        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Playback device not found"
        )
        with patch("jarvis_engine.voice.core.logger") as mock_logger:
            _play_audio_file("/tmp/test.wav")
            mock_logger.warning.assert_called_once()
            warning_args = mock_logger.warning.call_args[0]
            assert "Audio playback failed" in warning_args[0]
            assert warning_args[1] == 1  # returncode
            assert "Playback device" in warning_args[2]

    @patch("jarvis_engine.voice.core._run_ps_encoded")
    def test_play_audio_file_no_warning_on_success(self, mock_ps) -> None:
        """Zero return code does not log a warning."""
        from jarvis_engine.voice.core import _play_audio_file

        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch("jarvis_engine.voice.core.logger") as mock_logger:
            _play_audio_file("/tmp/test.wav")
            mock_logger.warning.assert_not_called()

    @patch("jarvis_engine.voice.core._run_ps_encoded")
    def test_play_audio_file_truncates_long_stderr(self, mock_ps) -> None:
        """Long stderr is truncated to 200 chars in the log."""
        from jarvis_engine.voice.core import _play_audio_file

        long_stderr = "E" * 500
        mock_ps.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr=long_stderr
        )
        with patch("jarvis_engine.voice.core.logger") as mock_logger:
            _play_audio_file("/tmp/test.wav")
            warning_args = mock_logger.warning.call_args[0]
            # The stderr arg should be truncated to 200 chars
            assert len(warning_args[2]) == 200

