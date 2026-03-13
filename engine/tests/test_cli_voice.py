"""Tests for cli_voice.py — Voice CLI command handlers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_bus(dispatch_return):
    """Create a mock bus whose dispatch() returns *dispatch_return*."""
    bus = MagicMock()
    bus.dispatch.return_value = dispatch_return
    return bus


# ---------------------------------------------------------------------------
# cmd_voice_list
# ---------------------------------------------------------------------------


class TestCmdVoiceList:

    def test_voices_found(self, capsys):
        result = SimpleNamespace(
            windows_voices=["Microsoft David", "Microsoft Zira"],
            edge_voices=["en-GB-RyanNeural"],
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_list
            rc = cmd_voice_list()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Microsoft David" in out
        assert "en-GB-RyanNeural" in out

    def test_no_voices(self, capsys):
        result = SimpleNamespace(windows_voices=[], edge_voices=[])
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_list
            rc = cmd_voice_list()
        assert rc == 1
        out = capsys.readouterr().out
        assert "- none" in out

    def test_only_windows_voices(self, capsys):
        result = SimpleNamespace(windows_voices=["Microsoft David"], edge_voices=[])
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_list
            rc = cmd_voice_list()
        assert rc == 0


# ---------------------------------------------------------------------------
# cmd_voice_say
# ---------------------------------------------------------------------------


class TestCmdVoiceSay:

    def test_say_text(self, capsys):
        result = SimpleNamespace(voice_name="David", output_wav="", message="Spoken successfully")
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_say
            rc = cmd_voice_say("Hello world")
        assert rc == 0
        out = capsys.readouterr().out
        assert "voice=David" in out
        assert "Spoken successfully" in out

    def test_say_with_wav_output(self, capsys):
        result = SimpleNamespace(voice_name="Zira", output_wav="/tmp/out.wav", message="Saved to file")
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_say
            rc = cmd_voice_say("Test", output_wav="/tmp/out.wav")
        assert rc == 0
        out = capsys.readouterr().out
        assert "wav=/tmp/out.wav" in out

    def test_say_url_shortening(self, capsys):
        """URLs should be shortened for speech via shorten_urls_for_speech."""
        result = SimpleNamespace(voice_name="David", output_wav="", message="done")
        bus = _mock_bus(result)
        with patch("jarvis_engine.cli.voice._get_bus", return_value=bus):
            from jarvis_engine.cli.voice import cmd_voice_say
            cmd_voice_say("Visit https://example.com/very/long/path for details")
        # The bus dispatch should have been called with shortened text
        call_args = bus.dispatch.call_args
        cmd = call_args[0][0]
        # URL should be shortened (not the full original URL)
        assert "https://example.com/very/long/path" not in cmd.text


# ---------------------------------------------------------------------------
# cmd_voice_enroll
# ---------------------------------------------------------------------------


class TestCmdVoiceEnroll:

    def test_enroll_success(self, capsys):
        result = SimpleNamespace(
            user_id="conner", profile_path="/tmp/profiles/conner",
            samples=3, message="Enrolled successfully",
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_enroll
            rc = cmd_voice_enroll("conner", "/tmp/audio.wav", False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "user_id=conner" in out
        assert "samples=3" in out

    def test_enroll_error(self, capsys):
        result = SimpleNamespace(
            user_id="", profile_path="", samples=0,
            message="error: WAV file not found",
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_enroll
            rc = cmd_voice_enroll("conner", "/tmp/missing.wav", False)
        assert rc == 2
        out = capsys.readouterr().out
        assert "error:" in out


# ---------------------------------------------------------------------------
# cmd_voice_verify
# ---------------------------------------------------------------------------


class TestCmdVoiceVerify:

    def test_verify_match(self, capsys):
        result = SimpleNamespace(
            user_id="conner", score=0.92, threshold=0.82,
            matched=True, message="Voice match confirmed",
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_verify
            rc = cmd_voice_verify("conner", "/tmp/audio.wav", 0.82)
        assert rc == 0
        out = capsys.readouterr().out
        assert "matched=True" in out
        assert "score=0.92" in out

    def test_verify_no_match(self, capsys):
        result = SimpleNamespace(
            user_id="conner", score=0.45, threshold=0.82,
            matched=False, message="Voice mismatch",
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_verify
            rc = cmd_voice_verify("conner", "/tmp/audio.wav", 0.82)
        assert rc == 2

    def test_verify_error(self, capsys):
        result = SimpleNamespace(
            user_id="", score=0.0, threshold=0.82,
            matched=False, message="error: no profile found",
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_verify
            rc = cmd_voice_verify("unknown", "/tmp/audio.wav", 0.82)
        assert rc == 2


# ---------------------------------------------------------------------------
# cmd_voice_listen
# ---------------------------------------------------------------------------


class TestCmdVoiceListen:

    def test_listen_no_speech(self, capsys):
        result = SimpleNamespace(
            text="", confidence=0.0, duration_seconds=5.0,
            message="No speech detected", utterance=None,
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            with patch("jarvis_engine.cli.voice.log_activity"):
                from jarvis_engine.cli.voice import cmd_voice_listen
                rc = cmd_voice_listen(5.0, "en", False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "no speech detected" in out

    def test_listen_transcription(self, capsys):
        result = SimpleNamespace(
            text="hello jarvis", confidence=0.95, duration_seconds=2.5,
            message="OK", utterance=None,
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            with patch("jarvis_engine.cli.voice.log_activity"):
                from jarvis_engine.cli.voice import cmd_voice_listen
                rc = cmd_voice_listen(5.0, "en", False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "transcription=hello jarvis" in out
        assert "confidence=0.95" in out

    def test_listen_error(self, capsys):
        result = SimpleNamespace(
            text="", confidence=0.0, duration_seconds=0.0,
            message="error: microphone unavailable", utterance=None,
        )
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            with patch("jarvis_engine.cli.voice.log_activity"):
                from jarvis_engine.cli.voice import cmd_voice_listen
                rc = cmd_voice_listen(5.0, "en", False)
        assert rc == 2


# ---------------------------------------------------------------------------
# cmd_voice_run
# ---------------------------------------------------------------------------


class TestCmdVoiceRun:

    def test_voice_run_success(self):
        result = SimpleNamespace(return_code=0)
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_run
            rc = cmd_voice_run(
                "what is the weather",
                execute=True,
                approve_privileged=False,
                speak=False,
                snapshot_path=Path("/tmp/snapshot.json"),
                actions_path=Path("/tmp/actions.json"),
                voice_user="conner",
                voice_auth_wav="",
                voice_threshold=0.82,
                master_password="",
            )
        assert rc == 0

    def test_voice_run_failure(self):
        result = SimpleNamespace(return_code=2)
        with patch("jarvis_engine.cli.voice._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli.voice import cmd_voice_run
            rc = cmd_voice_run(
                "do something dangerous",
                execute=True,
                approve_privileged=False,
                speak=False,
                snapshot_path=Path("/tmp/snapshot.json"),
                actions_path=Path("/tmp/actions.json"),
                voice_user="conner",
                voice_auth_wav="",
                voice_threshold=0.82,
                master_password="",
            )
        assert rc == 2


# ---------------------------------------------------------------------------
# _emit_voice_listen_state
# ---------------------------------------------------------------------------


class TestEmitVoiceListenState:

    def test_emits_to_stdout(self, capsys):
        with patch("jarvis_engine.cli.voice.log_activity"):
            from jarvis_engine.cli.voice import _emit_voice_listen_state
            _emit_voice_listen_state("arming", details={"duration_s": 5.0})
        out = capsys.readouterr().out
        assert "listening_state=arming" in out

    def test_handles_activity_log_failure(self, capsys):
        with patch("jarvis_engine.cli.voice.log_activity", side_effect=OSError("fail")):
            from jarvis_engine.cli.voice import _emit_voice_listen_state
            _emit_voice_listen_state("error")
        # Should not crash
        out = capsys.readouterr().out
        assert "listening_state=error" in out
