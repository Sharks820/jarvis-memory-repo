"""Tests for voice_intents.py — the voice command intent dispatch router.

Tests verify that cmd_voice_run_impl routes commands to the correct intent
handlers.  All external dependencies (CLI commands, bus, voice_pipeline) are
mocked.
"""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_MAIN = "jarvis_engine.main"
_VI = "jarvis_engine.voice.intents"
_VP = "jarvis_engine.voice.pipeline"


def _call_impl(
    text: str,
    *,
    execute: bool = False,
    approve_privileged: bool = False,
    speak: bool = False,
    tmp_path: Path | None = None,
    skip_voice_auth_guard: bool = True,
    master_password: str = "",
    model_override: str = "",
    voice_user: str = "conner",
    voice_auth_wav: str = "",
    voice_threshold: float = 0.5,
    utterance: dict | None = None,
    # Extra mocks
    owner_guard: dict | None = None,
    web_aug_rc: int = 0,
    cmd_return: int = 0,
) -> tuple[int, dict]:
    """Helper that calls cmd_voice_run_impl with heavy mocking.

    Returns (return_code, captured_mock_calls_dict).
    """
    from jarvis_engine.voice.intents import cmd_voice_run_impl

    root = tmp_path or Path("C:/fake/jarvis")
    snapshot = root / ".planning" / "ops_snapshot.json"
    actions = root / ".planning" / "ops_actions.json"

    calls: dict[str, list] = {}

    def _track(name: str, rc: int = cmd_return):
        def _fn(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return rc
        return _fn

    if owner_guard is None:
        owner_guard = {}

    patches = {
        f"{_VP}.repo_root": MagicMock(return_value=root),
        f"{_VP}._web_augmented_llm_conversation": MagicMock(return_value=web_aug_rc),
        f"{_VI}.read_owner_guard": MagicMock(return_value=owner_guard),
        f"{_VI}.verify_master_password": MagicMock(return_value=False),
        f"{_VI}.load_persona_config": MagicMock(return_value={}),
        f"{_VI}.compose_persona_reply": MagicMock(return_value="OK"),
        f"{_VI}._auto_ingest_memory": MagicMock(return_value=""),
        f"{_VI}.get_bus": MagicMock(),
    }

    cmd_patches = {
        "cmd_voice_say": _track("cmd_voice_say"),
        "cmd_voice_verify": _track("cmd_voice_verify"),
        "cmd_connect_bootstrap": _track("cmd_connect_bootstrap"),
        "cmd_runtime_control": _track("cmd_runtime_control"),
        "cmd_gaming_mode": _track("cmd_gaming_mode"),
        "cmd_weather": _track("cmd_weather"),
        "cmd_open_web": _track("cmd_open_web"),
        "cmd_mobile_desktop_sync": _track("cmd_mobile_desktop_sync"),
        "cmd_self_heal": _track("cmd_self_heal"),
        "cmd_ops_autopilot": _track("cmd_ops_autopilot"),
        "cmd_phone_spam_guard": _track("cmd_phone_spam_guard"),
        "cmd_phone_action": _track("cmd_phone_action"),
        "cmd_ops_sync": _track("cmd_ops_sync"),
        "cmd_ops_brief": _track("cmd_ops_brief"),
        "cmd_automation_run": _track("cmd_automation_run"),
        "cmd_run_task": _track("cmd_run_task"),
        "cmd_brain_context": _track("cmd_brain_context"),
        "cmd_ingest": _track("cmd_ingest"),
        "cmd_brain_status": _track("cmd_brain_status"),
        "cmd_mission_cancel": _track("cmd_mission_cancel"),
        "cmd_mission_status": _track("cmd_mission_status"),
        "cmd_status": _track("cmd_status"),
    }

    # Map command names to their source modules after re-export removal
    _CLI_OPS = "jarvis_engine.cli_ops"
    _CLI_KNOWLEDGE = "jarvis_engine.cli_knowledge"
    _CMD_MODULE = {
        "cmd_ops_autopilot": _CLI_OPS, "cmd_ops_sync": _CLI_OPS,
        "cmd_ops_brief": _CLI_OPS, "cmd_automation_run": _CLI_OPS,
        "cmd_mission_cancel": _CLI_OPS, "cmd_mission_status": _CLI_OPS,
        "cmd_brain_context": _CLI_KNOWLEDGE, "cmd_brain_status": _CLI_KNOWLEDGE,
    }

    with ExitStack() as stack:
        for target, mock_val in patches.items():
            stack.enter_context(patch(target, mock_val))
        for cmd_name, cmd_fn in cmd_patches.items():
            mod = _CMD_MODULE.get(cmd_name, _MAIN)
            stack.enter_context(patch(f"{mod}.{cmd_name}", cmd_fn))

        rc = cmd_voice_run_impl(
            text=text,
            utterance=utterance,
            execute=execute,
            approve_privileged=approve_privileged,
            speak=speak,
            snapshot_path=snapshot,
            actions_path=actions,
            voice_user=voice_user,
            voice_auth_wav=voice_auth_wav,
            voice_threshold=voice_threshold,
            master_password=master_password,
            model_override=model_override,
            skip_voice_auth_guard=skip_voice_auth_guard,
        )

    return rc, calls


# ===========================================================================
# Intent routing tests
# ===========================================================================


class TestWeatherIntent:
    def test_weather_routes_to_cmd_weather(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("weather in Austin", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_weather" in calls
        output = capsys.readouterr().out
        assert "intent=weather" in output

    def test_forecast_routes_to_cmd_weather(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("forecast for New York", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_weather" in calls


class TestWebResearchIntent:
    def test_search_web_for(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl("search the web for python", tmp_path=tmp_path)
        assert rc == 0
        output = capsys.readouterr().out
        assert "intent=web_research" in output

    def test_google_keyword(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl("google python tutorials", tmp_path=tmp_path)
        assert rc == 0
        output = capsys.readouterr().out
        assert "intent=web_research" in output

    def test_look_up(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl("look up rust programming", tmp_path=tmp_path)
        assert rc == 0
        output = capsys.readouterr().out
        assert "intent=web_research" in output


class TestOpsBriefIntent:
    def test_daily_brief(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("daily brief", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_ops_brief" in calls
        output = capsys.readouterr().out
        assert "intent=ops_brief" in output

    def test_my_schedule(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("my schedule", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_ops_brief" in calls

    def test_my_tasks(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("my tasks", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_ops_brief" in calls


class TestSystemStatusIntent:
    def test_system_status(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("system status", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_status" in calls
        output = capsys.readouterr().out
        assert "intent=system_status" in output

    def test_how_are_you(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("how are you", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_status" in calls

    def test_sentence_shaped_status_execute_path_stays_read_only(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl(
            "jarvis are you still running okay right now",
            tmp_path=tmp_path,
            execute=True,
            skip_voice_auth_guard=False,
            voice_auth_wav="",
        )
        assert rc == 0
        assert "cmd_status" in calls
        output = capsys.readouterr().out
        assert "voice_auth_required" not in output


class TestBrainStatusIntent:
    def test_sentence_shaped_brain_status_execute_path_stays_read_only(
        self, tmp_path: Path, capsys
    ) -> None:
        rc, calls = _call_impl(
            "hey jarvis can you check how your memory is holding up today",
            tmp_path=tmp_path,
            execute=True,
            skip_voice_auth_guard=False,
            voice_auth_wav="",
        )
        assert rc == 0
        assert "cmd_brain_status" in calls
        output = capsys.readouterr().out
        assert "voice_auth_required" not in output

    def test_optional_utterance_sidecar_does_not_change_routing(
        self, tmp_path: Path, capsys
    ) -> None:
        rc, calls = _call_impl(
            "brain status",
            tmp_path=tmp_path,
            utterance={
                "raw_text": "Jarvis brain status",
                "command_text": "brain status",
                "language": "en",
                "confidence": 0.92,
                "backend": "deepgram-nova3",
            },
        )
        assert rc == 0
        assert "cmd_brain_status" in calls
        output = capsys.readouterr().out
        assert "intent=brain_status" in output


class TestMemoryIntent:
    def test_brain_context_query(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("what do you know about python", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_brain_context" in calls
        output = capsys.readouterr().out
        assert "intent=brain_context" in output

    def test_remember_that(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("remember that I like coffee", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_ingest" in calls
        output = capsys.readouterr().out
        assert "intent=memory_ingest" in output

    def test_brain_status(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("brain status", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_brain_status" in calls
        output = capsys.readouterr().out
        assert "intent=brain_status" in output


class TestLLMConversationFallback:
    def test_unknown_text_falls_to_llm(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl("tell me a joke about cats", tmp_path=tmp_path)
        assert rc == 0
        output = capsys.readouterr().out
        assert "intent=llm_conversation" in output


class TestRuntimeControlIntents:
    def test_pause_jarvis(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("pause jarvis", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_runtime_control" in calls
        output = capsys.readouterr().out
        assert "intent=runtime_pause" in output

    def test_resume_jarvis(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("resume jarvis", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_runtime_control" in calls
        output = capsys.readouterr().out
        assert "intent=runtime_resume" in output

    def test_safe_mode_on(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("safe mode on", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_runtime_control" in calls
        output = capsys.readouterr().out
        assert "intent=runtime_safe_on" in output

    def test_runtime_status(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("runtime status", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_runtime_control" in calls
        output = capsys.readouterr().out
        assert "intent=runtime_status" in output


class TestGamingModeIntents:
    def test_gaming_mode_enable(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("gaming mode on", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_gaming_mode" in calls
        output = capsys.readouterr().out
        assert "intent=gaming_mode_enable" in output

    def test_gaming_mode_status(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("gaming mode status", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_gaming_mode" in calls
        output = capsys.readouterr().out
        assert "intent=gaming_mode_status" in output


class TestOpenWebIntent:
    def test_open_website_requires_execute(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl(
            "open website https://example.com",
            tmp_path=tmp_path,
            execute=False,
        )
        assert rc == 2
        output = capsys.readouterr().out
        assert "--execute" in output

    def test_open_website_with_execute(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl(
            "open website https://example.com",
            tmp_path=tmp_path,
            execute=True,
        )
        assert rc == 0
        assert "cmd_open_web" in calls

    def test_open_website_no_url(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl(
            "open website",
            tmp_path=tmp_path,
            execute=True,
        )
        assert rc == 2
        output = capsys.readouterr().out
        assert "No valid URL" in output


class TestConnectBootstrap:
    def test_connect_email(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("connect email", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_connect_bootstrap" in calls
        output = capsys.readouterr().out
        assert "intent=connect_bootstrap" in output

    def test_setup_calendar(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("setup calendar", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_connect_bootstrap" in calls


class TestMissionIntents:
    def test_mission_status(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("mission status", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_mission_status" in calls
        output = capsys.readouterr().out
        assert "intent=mission_status" in output


class TestGenerateIntents:
    def test_generate_code(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("generate code python hello world", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_run_task" in calls
        output = capsys.readouterr().out
        assert "intent=generate_code" in output

    def test_generate_image(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("generate image sunset", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_run_task" in calls
        output = capsys.readouterr().out
        assert "intent=generate_image" in output


class TestOwnerGuard:
    def test_owner_guard_blocks_wrong_user(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl(
            "pause jarvis",
            tmp_path=tmp_path,
            owner_guard={"enabled": True, "owner_user_id": "conner"},
            voice_user="intruder",
            skip_voice_auth_guard=False,
        )
        assert rc == 2
        output = capsys.readouterr().out
        assert "intent=owner_guard_blocked" in output

    def test_owner_guard_allows_correct_user(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl(
            "weather in Austin",
            tmp_path=tmp_path,
            owner_guard={"enabled": True, "owner_user_id": "conner"},
            voice_user="conner",
            skip_voice_auth_guard=True,
        )
        assert rc == 0


class TestVoiceAuth:
    def test_execute_requires_voice_auth(self, tmp_path: Path, capsys) -> None:
        rc, _ = _call_impl(
            "pause jarvis",
            tmp_path=tmp_path,
            execute=True,
            skip_voice_auth_guard=False,
            voice_auth_wav="",
        )
        assert rc == 2
        output = capsys.readouterr().out
        assert "voice_auth_required" in output


class TestSyncIntents:
    def test_sync_mobile(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("sync mobile", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_mobile_desktop_sync" in calls
        output = capsys.readouterr().out
        assert "intent=mobile_desktop_sync" in output

    def test_sync_calendar(self, tmp_path: Path, capsys) -> None:
        rc, calls = _call_impl("sync calendar", tmp_path=tmp_path)
        assert rc == 0
        assert "cmd_ops_sync" in calls
        output = capsys.readouterr().out
        assert "intent=ops_sync" in output
