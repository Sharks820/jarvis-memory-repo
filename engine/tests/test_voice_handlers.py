"""Comprehensive tests for voice handler classes in voice_handlers.py.

Covers VoiceListHandler, VoiceSayHandler, VoiceEnrollHandler,
VoiceVerifyHandler, VoiceRunHandler, VoiceListenHandler, and
PersonaComposeHandler -- including all edge cases, error paths,
and fallback behaviour when dependencies are unavailable.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from jarvis_engine.commands.voice_commands import (
    PersonaComposeCommand,
    VoiceEnrollCommand,
    VoiceListCommand,
    VoiceListenCommand,
    VoiceRunCommand,
    VoiceSayCommand,
    VoiceVerifyCommand,
)
from jarvis_engine.handlers.voice_handlers import (
    PersonaComposeHandler,
    VoiceEnrollHandler,
    VoiceListHandler,
    VoiceListenHandler,
    VoiceRunHandler,
    VoiceSayHandler,
    VoiceVerifyHandler,
    _load_voice_auth_impl,
)


# ---------------------------------------------------------------------------
# VoiceListHandler
# ---------------------------------------------------------------------------


class TestVoiceListHandler:
    """Tests for VoiceListHandler."""

    def test_import_error_returns_empty(self, tmp_path: Path) -> None:
        handler = VoiceListHandler(root=tmp_path)
        with patch.dict("sys.modules", {"jarvis_engine.voice": None}):
            result = handler.handle(VoiceListCommand())
        assert result.windows_voices == []
        assert result.edge_voices == []

    def test_successful_list(self, tmp_path: Path) -> None:
        mock_voice = MagicMock()
        mock_voice.list_windows_voices.return_value = ["Voice1", "Voice2"]
        mock_voice.list_edge_voices.return_value = [
            "en-GB-SoniaNeural",
            "en-GB-RyanNeural",
            "en-US-JennyNeural",
            "fr-FR-DeniseNeural",
        ]

        with patch.dict("sys.modules", {"jarvis_engine.voice": mock_voice}):
            handler = VoiceListHandler(root=tmp_path)
            result = handler.handle(VoiceListCommand())

        assert result.windows_voices == ["Voice1", "Voice2"]
        # Only en-GB- voices are returned
        assert result.edge_voices == ["en-GB-SoniaNeural", "en-GB-RyanNeural"]

    def test_empty_voices(self, tmp_path: Path) -> None:
        mock_voice = MagicMock()
        mock_voice.list_windows_voices.return_value = []
        mock_voice.list_edge_voices.return_value = []

        with patch.dict("sys.modules", {"jarvis_engine.voice": mock_voice}):
            handler = VoiceListHandler(root=tmp_path)
            result = handler.handle(VoiceListCommand())

        assert result.windows_voices == []
        assert result.edge_voices == []

    def test_edge_filter_case_insensitive(self, tmp_path: Path) -> None:
        """Edge voice filter should be case-insensitive (uses .lower())."""
        mock_voice = MagicMock()
        mock_voice.list_windows_voices.return_value = []
        mock_voice.list_edge_voices.return_value = ["EN-GB-TestNeural"]

        with patch.dict("sys.modules", {"jarvis_engine.voice": mock_voice}):
            handler = VoiceListHandler(root=tmp_path)
            result = handler.handle(VoiceListCommand())

        assert result.edge_voices == ["EN-GB-TestNeural"]


# ---------------------------------------------------------------------------
# VoiceSayHandler
# ---------------------------------------------------------------------------


class TestVoiceSayHandler:
    """Tests for VoiceSayHandler."""

    def test_import_error(self, tmp_path: Path) -> None:
        handler = VoiceSayHandler(root=tmp_path)
        with patch.dict("sys.modules", {"jarvis_engine.voice": None}):
            result = handler.handle(VoiceSayCommand(text="hello"))
        assert "not available" in result.message.lower()

    def test_successful_say(self, tmp_path: Path) -> None:
        mock_voice = MagicMock()
        mock_voice.speak_text.return_value = SimpleNamespace(
            voice_name="Microsoft David",
            output_wav="out.wav",
            message="ok",
        )

        with patch.dict("sys.modules", {"jarvis_engine.voice": mock_voice}):
            handler = VoiceSayHandler(root=tmp_path)
            result = handler.handle(
                VoiceSayCommand(
                    text="Hello world",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="out.wav",
                    rate=-1,
                )
            )

        assert result.voice_name == "Microsoft David"
        assert result.output_wav == "out.wav"
        assert result.message == "ok"
        mock_voice.speak_text.assert_called_once_with(
            text="Hello world",
            profile="jarvis_like",
            custom_voice_pattern="",
            output_wav="out.wav",
            rate=-1,
        )

    def test_say_with_custom_rate(self, tmp_path: Path) -> None:
        mock_voice = MagicMock()
        mock_voice.speak_text.return_value = SimpleNamespace(
            voice_name="Test", output_wav="", message="ok"
        )

        with patch.dict("sys.modules", {"jarvis_engine.voice": mock_voice}):
            handler = VoiceSayHandler(root=tmp_path)
            handler.handle(VoiceSayCommand(text="fast", rate=200))

        mock_voice.speak_text.assert_called_once()
        call_kwargs = mock_voice.speak_text.call_args
        assert call_kwargs.kwargs.get("rate") == 200 or call_kwargs[1].get("rate") == 200


# ---------------------------------------------------------------------------
# VoiceEnrollHandler
# ---------------------------------------------------------------------------


class TestVoiceEnrollHandler:
    """Tests for VoiceEnrollHandler."""

    def test_module_not_available(self, tmp_path: Path) -> None:
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(None, None, "numpy not found"),
        ):
            handler = VoiceEnrollHandler(root=tmp_path)
            result = handler.handle(VoiceEnrollCommand(user_id="conner", wav_path="test.wav"))
        assert "dependency missing" in result.message.lower()
        assert "numpy not found" in result.message

    def test_successful_enrollment(self, tmp_path: Path) -> None:
        enroll_fn = MagicMock(
            return_value=SimpleNamespace(
                user_id="conner",
                profile_path="/path/to/profile",
                samples=3,
                message="Enrolled successfully.",
            )
        )
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(enroll_fn, MagicMock(), ""),
        ):
            handler = VoiceEnrollHandler(root=tmp_path)
            result = handler.handle(
                VoiceEnrollCommand(user_id="conner", wav_path="sample.wav", replace=False)
            )

        assert result.user_id == "conner"
        assert result.profile_path == "/path/to/profile"
        assert result.samples == 3
        assert result.message == "Enrolled successfully."
        enroll_fn.assert_called_once_with(
            tmp_path, user_id="conner", wav_path="sample.wav", replace=False
        )

    def test_enrollment_replace_flag(self, tmp_path: Path) -> None:
        enroll_fn = MagicMock(
            return_value=SimpleNamespace(
                user_id="u", profile_path="p", samples=1, message="ok"
            )
        )
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(enroll_fn, MagicMock(), ""),
        ):
            handler = VoiceEnrollHandler(root=tmp_path)
            handler.handle(VoiceEnrollCommand(user_id="u", wav_path="w", replace=True))

        enroll_fn.assert_called_once_with(tmp_path, user_id="u", wav_path="w", replace=True)

    def test_enrollment_value_error(self, tmp_path: Path) -> None:
        enroll_fn = MagicMock(side_effect=ValueError("bad wav"))
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(enroll_fn, MagicMock(), ""),
        ):
            handler = VoiceEnrollHandler(root=tmp_path)
            result = handler.handle(VoiceEnrollCommand(user_id="u", wav_path="bad.wav"))
        assert "failed" in result.message.lower()

    def test_enrollment_os_error(self, tmp_path: Path) -> None:
        enroll_fn = MagicMock(side_effect=OSError("file missing"))
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(enroll_fn, MagicMock(), ""),
        ):
            handler = VoiceEnrollHandler(root=tmp_path)
            result = handler.handle(VoiceEnrollCommand(user_id="u", wav_path="missing.wav"))
        assert "failed" in result.message.lower()


# ---------------------------------------------------------------------------
# VoiceVerifyHandler
# ---------------------------------------------------------------------------


class TestVoiceVerifyHandler:
    """Tests for VoiceVerifyHandler."""

    def test_module_not_available(self, tmp_path: Path) -> None:
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(None, None, "scipy not found"),
        ):
            handler = VoiceVerifyHandler(root=tmp_path)
            result = handler.handle(VoiceVerifyCommand(user_id="u", wav_path="w"))
        assert "dependency missing" in result.message.lower()

    def test_successful_verify_matched(self, tmp_path: Path) -> None:
        verify_fn = MagicMock(
            return_value=SimpleNamespace(
                user_id="conner",
                score=0.95,
                threshold=0.82,
                matched=True,
                message="Voice matched.",
            )
        )
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(MagicMock(), verify_fn, ""),
        ):
            handler = VoiceVerifyHandler(root=tmp_path)
            result = handler.handle(
                VoiceVerifyCommand(user_id="conner", wav_path="test.wav", threshold=0.82)
            )

        assert result.matched is True
        assert result.score == 0.95
        assert result.user_id == "conner"

    def test_verify_not_matched(self, tmp_path: Path) -> None:
        verify_fn = MagicMock(
            return_value=SimpleNamespace(
                user_id="conner",
                score=0.5,
                threshold=0.82,
                matched=False,
                message="Voice not matched.",
            )
        )
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(MagicMock(), verify_fn, ""),
        ):
            handler = VoiceVerifyHandler(root=tmp_path)
            result = handler.handle(VoiceVerifyCommand(user_id="conner", wav_path="test.wav"))

        assert result.matched is False
        assert result.score == 0.5

    def test_verify_value_error(self, tmp_path: Path) -> None:
        verify_fn = MagicMock(side_effect=ValueError("bad format"))
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(MagicMock(), verify_fn, ""),
        ):
            handler = VoiceVerifyHandler(root=tmp_path)
            result = handler.handle(VoiceVerifyCommand(user_id="u", wav_path="bad.wav"))
        assert "failed" in result.message.lower()

    def test_verify_os_error(self, tmp_path: Path) -> None:
        verify_fn = MagicMock(side_effect=OSError("no profile"))
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(MagicMock(), verify_fn, ""),
        ):
            handler = VoiceVerifyHandler(root=tmp_path)
            result = handler.handle(VoiceVerifyCommand(user_id="u", wav_path="w"))
        assert "failed" in result.message.lower()

    def test_custom_threshold_forwarded(self, tmp_path: Path) -> None:
        verify_fn = MagicMock(
            return_value=SimpleNamespace(
                user_id="u", score=0.9, threshold=0.9, matched=True, message="ok"
            )
        )
        with patch(
            "jarvis_engine.handlers.voice_handlers._load_voice_auth_impl",
            return_value=(MagicMock(), verify_fn, ""),
        ):
            handler = VoiceVerifyHandler(root=tmp_path)
            handler.handle(VoiceVerifyCommand(user_id="u", wav_path="w", threshold=0.9))

        verify_fn.assert_called_once_with(
            tmp_path, user_id="u", wav_path="w", threshold=0.9
        )


# ---------------------------------------------------------------------------
# VoiceRunHandler
# ---------------------------------------------------------------------------


class TestVoiceRunHandler:
    """Tests for VoiceRunHandler."""

    @patch("jarvis_engine.main._cmd_voice_run_impl", return_value=0)
    def test_delegates_to_main(self, mock_impl: MagicMock, tmp_path: Path) -> None:
        """VoiceRunHandler delegates to main._cmd_voice_run_impl."""
        handler = VoiceRunHandler(root=tmp_path)
        result = handler.handle(
            VoiceRunCommand(
                text="turn on lights",
                execute=True,
                speak=True,
            )
        )

        assert result.return_code == 0
        mock_impl.assert_called_once()

    @patch("jarvis_engine.main._cmd_voice_run_impl", return_value=1)
    def test_nonzero_return_code(self, mock_impl: MagicMock, tmp_path: Path) -> None:
        handler = VoiceRunHandler(root=tmp_path)
        result = handler.handle(VoiceRunCommand(text="bad command"))

        assert result.return_code == 1

    @patch("jarvis_engine.main._cmd_voice_run_impl", return_value=0)
    def test_all_parameters_forwarded(self, mock_impl: MagicMock, tmp_path: Path) -> None:
        """All VoiceRunCommand fields are forwarded to _cmd_voice_run_impl."""
        snap = Path("snap.json")
        actions = Path("actions.json")

        handler = VoiceRunHandler(root=tmp_path)
        handler.handle(
            VoiceRunCommand(
                text="schedule meeting",
                execute=True,
                approve_privileged=True,
                speak=False,
                snapshot_path=snap,
                actions_path=actions,
                voice_user="admin",
                voice_auth_wav="auth.wav",
                voice_threshold=0.9,
                master_password="secret",
                model_override="kimi-k2",
                skip_voice_auth_guard=True,
            )
        )

        mock_impl.assert_called_once_with(
            text="schedule meeting",
            execute=True,
            approve_privileged=True,
            speak=False,
            snapshot_path=snap,
            actions_path=actions,
            voice_user="admin",
            voice_auth_wav="auth.wav",
            voice_threshold=0.9,
            master_password="secret",
            model_override="kimi-k2",
            skip_voice_auth_guard=True,
        )


# ---------------------------------------------------------------------------
# VoiceListenHandler
# ---------------------------------------------------------------------------


class TestVoiceListenHandler:
    """Tests for VoiceListenHandler."""

    def test_import_error(self, tmp_path: Path) -> None:
        handler = VoiceListenHandler(root=tmp_path)
        with patch.dict("sys.modules", {"jarvis_engine.stt": None}):
            result = handler.handle(VoiceListenCommand())
        assert "not available" in result.message.lower()

    def test_successful_listen(self, tmp_path: Path) -> None:
        mock_stt = MagicMock()
        mock_stt.listen_and_transcribe.return_value = SimpleNamespace(
            text="hello world",
            confidence=0.95,
            duration_seconds=3.2,
        )

        with patch.dict("sys.modules", {"jarvis_engine.stt": mock_stt}):
            handler = VoiceListenHandler(root=tmp_path)
            result = handler.handle(VoiceListenCommand())

        assert result.text == "hello world"
        assert result.confidence == 0.95
        assert result.duration_seconds == 3.2

    def test_parameters_forwarded(self, tmp_path: Path) -> None:
        mock_stt = MagicMock()
        mock_stt.listen_and_transcribe.return_value = SimpleNamespace(
            text="", confidence=0.0, duration_seconds=0.0
        )

        with patch.dict("sys.modules", {"jarvis_engine.stt": mock_stt}):
            handler = VoiceListenHandler(root=tmp_path)
            handler.handle(
                VoiceListenCommand(
                    max_duration_seconds=10.0,
                    language="fr",
                )
            )

        mock_stt.listen_and_transcribe.assert_called_once_with(
            max_duration_seconds=10.0,
            language="fr",
            root_dir=tmp_path,
            gateway=None,
        )

    def test_exception_during_listen(self, tmp_path: Path) -> None:
        """Generic exception during listen returns error message."""
        mock_stt = MagicMock()
        mock_stt.listen_and_transcribe.side_effect = RuntimeError("mic busy")

        with patch.dict("sys.modules", {"jarvis_engine.stt": mock_stt}):
            handler = VoiceListenHandler(root=tmp_path)
            result = handler.handle(VoiceListenCommand())

        assert "failed" in result.message.lower()
        assert result.text == ""


# ---------------------------------------------------------------------------
# PersonaComposeHandler
# ---------------------------------------------------------------------------


class TestPersonaComposeHandler:
    """Tests for PersonaComposeHandler."""

    def test_no_gateway_returns_error(self, tmp_path: Path) -> None:
        handler = PersonaComposeHandler(root=tmp_path, gateway=None)
        result = handler.handle(PersonaComposeCommand(query="hello"))
        assert "not available" in result.message.lower()

    def test_import_error_returns_error(self, tmp_path: Path) -> None:
        handler = PersonaComposeHandler(root=tmp_path, gateway=MagicMock())
        with patch.dict("sys.modules", {"jarvis_engine.persona": None}):
            result = handler.handle(PersonaComposeCommand(query="hello"))
        assert "not available" in result.message.lower()

    def test_successful_compose(self, tmp_path: Path) -> None:
        """Full happy path: persona config loaded, gateway called."""
        mock_gateway = MagicMock()
        mock_resp = SimpleNamespace(text="Hello, Conner. How can I help?")
        mock_gateway.complete.return_value = mock_resp

        mock_persona = MagicMock()
        mock_persona.load_persona_config.return_value = {"name": "Jarvis"}
        mock_persona._resolve_tone.return_value = "professional"
        mock_persona.compose_persona_system_prompt.return_value = "You are Jarvis."

        mock_gw_models = MagicMock()
        mock_gw_models.ModelGateway = type(mock_gateway)
        mock_gw_models.GatewayResponse = type(mock_resp)

        with patch.dict(
            "sys.modules",
            {
                "jarvis_engine.persona": mock_persona,
                "jarvis_engine.gateway.models": mock_gw_models,
            },
        ):
            handler = PersonaComposeHandler(root=tmp_path, gateway=mock_gateway)
            result = handler.handle(
                PersonaComposeCommand(query="What's the weather?", branch="general")
            )

        assert result.text == "Hello, Conner. How can I help?"
        assert result.branch == "general"
        assert result.tone == "professional"

    def test_default_model_used(self, tmp_path: Path) -> None:
        """When cmd.model is empty, default model is used."""
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = SimpleNamespace(text="response")

        mock_persona = MagicMock()
        mock_persona.load_persona_config.return_value = {}
        mock_persona._resolve_tone.return_value = "casual"
        mock_persona.compose_persona_system_prompt.return_value = ""

        mock_gw_models = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "jarvis_engine.persona": mock_persona,
                "jarvis_engine.gateway.models": mock_gw_models,
            },
        ):
            handler = PersonaComposeHandler(root=tmp_path, gateway=mock_gateway)
            handler.handle(PersonaComposeCommand(query="hi", model=""))

        call_kwargs = mock_gateway.complete.call_args
        # Default model should be kimi-k2
        assert call_kwargs.kwargs.get("model") == "kimi-k2" or \
            call_kwargs[1].get("model") == "kimi-k2"

    def test_custom_model_used(self, tmp_path: Path) -> None:
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = SimpleNamespace(text="response")

        mock_persona = MagicMock()
        mock_persona.load_persona_config.return_value = {}
        mock_persona._resolve_tone.return_value = "casual"
        mock_persona.compose_persona_system_prompt.return_value = ""

        mock_gw_models = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "jarvis_engine.persona": mock_persona,
                "jarvis_engine.gateway.models": mock_gw_models,
            },
        ):
            handler = PersonaComposeHandler(root=tmp_path, gateway=mock_gateway)
            handler.handle(PersonaComposeCommand(query="hi", model="gpt-4"))

        call_kwargs = mock_gateway.complete.call_args
        assert call_kwargs.kwargs.get("model") == "gpt-4" or \
            call_kwargs[1].get("model") == "gpt-4"

    def test_gateway_connection_error(self, tmp_path: Path) -> None:
        mock_gateway = MagicMock()
        mock_gateway.complete.side_effect = ConnectionError("timeout")

        mock_persona = MagicMock()
        mock_persona.load_persona_config.return_value = {}
        mock_persona._resolve_tone.return_value = "formal"
        mock_persona.compose_persona_system_prompt.return_value = "sys"

        mock_gw_models = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "jarvis_engine.persona": mock_persona,
                "jarvis_engine.gateway.models": mock_gw_models,
            },
        ):
            handler = PersonaComposeHandler(root=tmp_path, gateway=mock_gateway)
            result = handler.handle(PersonaComposeCommand(query="hi", branch="tech"))

        assert "failed" in result.message.lower()
        assert result.branch == "tech"
        assert result.tone == "formal"

    def test_gateway_runtime_error(self, tmp_path: Path) -> None:
        mock_gateway = MagicMock()
        mock_gateway.complete.side_effect = RuntimeError("model unavailable")

        mock_persona = MagicMock()
        mock_persona.load_persona_config.return_value = {}
        mock_persona._resolve_tone.return_value = "casual"
        mock_persona.compose_persona_system_prompt.return_value = ""

        mock_gw_models = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "jarvis_engine.persona": mock_persona,
                "jarvis_engine.gateway.models": mock_gw_models,
            },
        ):
            handler = PersonaComposeHandler(root=tmp_path, gateway=mock_gateway)
            result = handler.handle(PersonaComposeCommand(query="hi"))

        assert "failed" in result.message.lower()

    def test_system_prompt_included_when_present(self, tmp_path: Path) -> None:
        """When compose_persona_system_prompt returns content, it's in messages."""
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = SimpleNamespace(text="ok")

        mock_persona = MagicMock()
        mock_persona.load_persona_config.return_value = {}
        mock_persona._resolve_tone.return_value = "formal"
        mock_persona.compose_persona_system_prompt.return_value = "Be formal."

        mock_gw_models = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "jarvis_engine.persona": mock_persona,
                "jarvis_engine.gateway.models": mock_gw_models,
            },
        ):
            handler = PersonaComposeHandler(root=tmp_path, gateway=mock_gateway)
            handler.handle(PersonaComposeCommand(query="hello"))

        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        # System prompt now includes datetime grounding prepended to persona prompt
        assert "Be formal." in messages[0]["content"]
        assert "Current date/time" in messages[0]["content"]
        assert messages[1]["role"] == "user"

    def test_system_prompt_omitted_when_empty(self, tmp_path: Path) -> None:
        """When compose_persona_system_prompt returns empty, datetime-only system message."""
        mock_gateway = MagicMock()
        mock_gateway.complete.return_value = SimpleNamespace(text="ok")

        mock_persona = MagicMock()
        mock_persona.load_persona_config.return_value = {}
        mock_persona._resolve_tone.return_value = "neutral"
        mock_persona.compose_persona_system_prompt.return_value = ""

        mock_gw_models = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "jarvis_engine.persona": mock_persona,
                "jarvis_engine.gateway.models": mock_gw_models,
            },
        ):
            handler = PersonaComposeHandler(root=tmp_path, gateway=mock_gateway)
            handler.handle(PersonaComposeCommand(query="hello"))

        call_kwargs = mock_gateway.complete.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        # Even with empty persona prompt, datetime grounding is injected
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "Current date/time" in messages[0]["content"]
        assert messages[1]["role"] == "user"


# ---------------------------------------------------------------------------
# _load_voice_auth_impl helper
# ---------------------------------------------------------------------------


class TestLoadVoiceAuthImpl:
    """Tests for the _load_voice_auth_impl helper function."""

    def test_module_not_found(self) -> None:
        with patch.dict("sys.modules", {"jarvis_engine.voice_auth": None}):
            enroll, verify, err = _load_voice_auth_impl()
        assert enroll is None
        assert verify is None
        assert err != ""

    def test_module_available(self) -> None:
        mock_module = MagicMock()
        mock_module.enroll_voiceprint = MagicMock(name="enroll")
        mock_module.verify_voiceprint = MagicMock(name="verify")

        with patch.dict("sys.modules", {"jarvis_engine.voice_auth": mock_module}):
            enroll, verify, err = _load_voice_auth_impl()

        assert enroll is not None
        assert verify is not None
        assert err == ""
