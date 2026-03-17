"""Tests for voice-to-agent intent routing.

Tests:
  - "build a Unity scene with a rotating cube" routes to AgentRunCommand
  - "create a unity project for my game" routes to AgentRunCommand
  - "use Mixamo for animations" routes to AgentRegisterToolCommand
  - "use Blender for rendering" routes to AgentRegisterToolCommand
  - Non-agent phrases fall through to other handlers (no agent dispatch)
  - _handle_agent_task returns ("agent_task", 0) on success
  - _handle_register_tool returns ("register_tool", 0) on success
  - _DISPATCH_RULES contains the agent rules
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(text: str, **overrides: Any) -> Any:
    """Create a minimal _DispatchCtx-like object for testing."""
    from jarvis_engine.voice.intents import _DispatchCtx

    noop = MagicMock(return_value=0)
    respond = MagicMock()

    class _FakeCtx:
        pass

    ctx = _FakeCtx()
    # Required __slots__ fields
    ctx.text = text
    ctx.lowered = text.lower()
    ctx.execute = True
    ctx.approve_privileged = False
    ctx.speak = False
    ctx.snapshot_path = Path(".")
    ctx.actions_path = Path(".")
    ctx.voice_user = ""
    ctx.voice_auth_wav = ""
    ctx.voice_threshold = 0.82
    ctx.master_password = ""
    ctx.model_override = ""
    ctx.skip_voice_auth_guard = False
    ctx.master_password_ok = False
    ctx.phone_queue = Path(".")
    ctx.phone_report = Path(".")
    ctx.phone_call_log = Path(".")
    ctx.repo_root_fn = lambda: Path(".")
    ctx._respond = respond
    ctx._require_state_mutation_voice_auth = MagicMock(return_value=True)
    ctx._web_augmented_llm_conversation = noop
    ctx.cmd_voice_say = noop
    ctx.cmd_voice_verify = noop
    ctx.cmd_connect_bootstrap = noop
    ctx.cmd_runtime_control = noop
    ctx.cmd_gaming_mode = noop
    ctx.cmd_weather = noop
    ctx.cmd_open_web = noop
    ctx.cmd_mobile_desktop_sync = noop
    ctx.cmd_self_heal = noop
    ctx.cmd_ops_autopilot = noop
    ctx.cmd_phone_spam_guard = noop
    ctx.cmd_phone_action = noop
    ctx.cmd_ops_sync = noop
    ctx.cmd_ops_brief = noop
    ctx.cmd_automation_run = noop
    ctx.cmd_run_task = noop
    ctx.cmd_brain_context = noop
    ctx.cmd_ingest = noop
    ctx.cmd_brain_status = noop
    ctx.cmd_mission_create = noop
    ctx.cmd_mission_cancel = noop
    ctx.cmd_mission_status = noop
    ctx.cmd_status = noop

    for k, v in overrides.items():
        setattr(ctx, k, v)

    return ctx


# ---------------------------------------------------------------------------
# _DISPATCH_RULES contains agent rules
# ---------------------------------------------------------------------------


class TestDispatchRulesContainAgentRules:
    def test_dispatch_rules_has_agent_task_matcher(self):
        from jarvis_engine.voice.intents import _DISPATCH_RULES

        matchers = [m for m, _ in _DISPATCH_RULES]
        # At least one matcher should match "build a unity scene"
        assert any(m("build a unity scene") for m in matchers), \
            "No dispatch rule matches 'build a unity scene'"

    def test_dispatch_rules_has_register_tool_matcher(self):
        from jarvis_engine.voice.intents import _DISPATCH_RULES

        matchers = [m for m, _ in _DISPATCH_RULES]
        # At least one matcher should match "use mixamo for animations"
        assert any(m("use mixamo for animations") for m in matchers), \
            "No dispatch rule matches 'use mixamo for animations'"

    def test_dispatch_rules_non_agent_doesnt_match_agent(self):
        from jarvis_engine.voice.intents import _DISPATCH_RULES
        from jarvis_engine.voice.intents import _handle_agent_task

        # "check the weather" should not route to agent_task handler
        agent_task_rules = [(m, h) for m, h in _DISPATCH_RULES if h is _handle_agent_task]
        for matcher, _ in agent_task_rules:
            assert not matcher("check the weather"), \
                "Agent task rule incorrectly matched 'check the weather'"


# ---------------------------------------------------------------------------
# _handle_agent_task tests
# ---------------------------------------------------------------------------


class TestHandleAgentTask:
    def test_unity_scene_dispatches_agent_run(self):
        from jarvis_engine.voice.intents import _handle_agent_task
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        dispatched = []

        def mock_dispatch(cmd):
            dispatched.append(cmd)
            return MagicMock(return_code=0, task_id="t1")

        mock_bus = MagicMock()
        mock_bus.dispatch = mock_dispatch

        ctx = _make_ctx("build a Unity scene with a rotating cube")

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            intent, rc = _handle_agent_task(ctx)

        assert intent == "agent_task"
        assert rc == 0
        assert len(dispatched) == 1
        cmd = dispatched[0]
        assert isinstance(cmd, AgentRunCommand)
        assert "unity" in cmd.goal.lower() or "rotating cube" in cmd.goal.lower() or len(cmd.goal) > 0

    def test_unity_project_dispatches_agent_run(self):
        from jarvis_engine.voice.intents import _handle_agent_task
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        dispatched = []

        mock_bus = MagicMock()
        mock_bus.dispatch = lambda cmd: dispatched.append(cmd) or MagicMock(return_code=0)

        ctx = _make_ctx("create a unity project for my game")

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            intent, rc = _handle_agent_task(ctx)

        assert intent == "agent_task"
        assert rc == 0
        assert len(dispatched) == 1
        assert isinstance(dispatched[0], AgentRunCommand)

    def test_responds_with_starting_message(self):
        from jarvis_engine.voice.intents import _handle_agent_task

        mock_bus = MagicMock()
        mock_bus.dispatch.return_value = MagicMock(return_code=0)
        respond = MagicMock()

        ctx = _make_ctx("build a Unity scene with fog")
        ctx._respond = respond

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            _handle_agent_task(ctx)

        respond.assert_called_once()
        msg = respond.call_args[0][0].lower()
        assert "agent" in msg or "task" in msg or "starting" in msg

    def test_returns_rc1_on_exception(self):
        from jarvis_engine.voice.intents import _handle_agent_task

        mock_bus = MagicMock()
        mock_bus.dispatch.side_effect = RuntimeError("bus failure")

        ctx = _make_ctx("build a unity scene")

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            intent, rc = _handle_agent_task(ctx)

        assert intent == "agent_task"
        assert rc == 1

    def test_goal_not_empty(self):
        from jarvis_engine.voice.intents import _handle_agent_task
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        dispatched = []
        mock_bus = MagicMock()
        mock_bus.dispatch = lambda cmd: dispatched.append(cmd) or MagicMock(return_code=0)

        ctx = _make_ctx("make a unity game object")

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            _handle_agent_task(ctx)

        assert len(dispatched) == 1
        assert isinstance(dispatched[0], AgentRunCommand)
        assert len(dispatched[0].goal) > 0


# ---------------------------------------------------------------------------
# _handle_register_tool tests
# ---------------------------------------------------------------------------


class TestHandleRegisterTool:
    def test_use_mixamo_for_animations(self):
        from jarvis_engine.voice.intents import _handle_register_tool
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        dispatched = []
        mock_bus = MagicMock()
        mock_bus.dispatch = lambda cmd: dispatched.append(cmd) or MagicMock(return_code=0)

        ctx = _make_ctx("use Mixamo for animations")

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            intent, rc = _handle_register_tool(ctx)

        assert intent == "register_tool"
        assert rc == 0
        assert len(dispatched) == 1
        cmd = dispatched[0]
        assert isinstance(cmd, AgentRegisterToolCommand)
        assert "mixamo" in cmd.name.lower()

    def test_use_blender_for_rendering(self):
        from jarvis_engine.voice.intents import _handle_register_tool
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        dispatched = []
        mock_bus = MagicMock()
        mock_bus.dispatch = lambda cmd: dispatched.append(cmd) or MagicMock(return_code=0)

        ctx = _make_ctx("use Blender for rendering")

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            intent, rc = _handle_register_tool(ctx)

        assert intent == "register_tool"
        assert rc == 0
        cmd = dispatched[0]
        assert "blender" in cmd.name.lower()

    def test_responds_with_registered_message(self):
        from jarvis_engine.voice.intents import _handle_register_tool

        mock_bus = MagicMock()
        mock_bus.dispatch.return_value = MagicMock(return_code=0)
        respond = MagicMock()

        ctx = _make_ctx("use Mixamo for animations")
        ctx._respond = respond

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            _handle_register_tool(ctx)

        respond.assert_called_once()
        msg = respond.call_args[0][0].lower()
        assert "registered" in msg or "mixamo" in msg

    def test_description_contains_for_phrase(self):
        """Description should contain what the tool is for."""
        from jarvis_engine.voice.intents import _handle_register_tool
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        dispatched = []
        mock_bus = MagicMock()
        mock_bus.dispatch = lambda cmd: dispatched.append(cmd) or MagicMock(return_code=0)

        ctx = _make_ctx("use Mixamo for character animations")

        with patch("jarvis_engine.voice.intents.get_bus", return_value=mock_bus):
            _handle_register_tool(ctx)

        cmd = dispatched[0]
        assert isinstance(cmd, AgentRegisterToolCommand)
        # Description should mention what the tool is for
        assert len(cmd.description) > 0


# ---------------------------------------------------------------------------
# Matcher isolation (lambda predicates)
# ---------------------------------------------------------------------------


class TestMatcherIsolation:
    def test_agent_task_matcher_matches_build_unity(self):
        from jarvis_engine.voice.intents import _DISPATCH_RULES, _handle_agent_task

        agent_rules = [(m, h) for m, h in _DISPATCH_RULES if h is _handle_agent_task]
        assert len(agent_rules) >= 1, "Expected at least one agent_task rule"

        for phrase in ("build a unity scene", "create a unity project", "unity scene"):
            matched = any(m(phrase) for m, _ in agent_rules)
            assert matched, f"Agent task rule did not match: {phrase!r}"

    def test_register_tool_matcher_matches_use_x_for_y(self):
        from jarvis_engine.voice.intents import _DISPATCH_RULES, _handle_register_tool

        tool_rules = [(m, h) for m, h in _DISPATCH_RULES if h is _handle_register_tool]
        assert len(tool_rules) >= 1, "Expected at least one register_tool rule"

        for phrase in ("use mixamo for animations", "use blender for rendering", "use Photoshop for textures"):
            matched = any(m(phrase.lower()) for m, _ in tool_rules)
            assert matched, f"Register tool rule did not match: {phrase!r}"

    def test_register_tool_does_not_match_non_use_for(self):
        from jarvis_engine.voice.intents import _DISPATCH_RULES, _handle_register_tool

        tool_rules = [(m, h) for m, h in _DISPATCH_RULES if h is _handle_register_tool]
        # "use tools" without " for " shouldn't match
        for phrase in ("use the search engine", "useful info", "use everything"):
            matched = any(m(phrase) for m, _ in tool_rules)
            assert not matched, f"Register tool rule incorrectly matched: {phrase!r}"
