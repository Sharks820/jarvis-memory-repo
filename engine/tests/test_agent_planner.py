"""Tests for agent/planner.py -- TaskPlanner goal decomposition via LLM.

TDD: RED phase -- all tests written before implementation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal stubs (avoid importing heavy gateway in unit tests)
# ---------------------------------------------------------------------------


@dataclass
class _FakeGatewayResponse:
    text: str
    model: str = "fake-model"
    provider: str = "fake"
    input_tokens: int = 10
    output_tokens: int = 20


@dataclass
class _FakeToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    is_destructive: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_planner(llm_response: str = "[]", input_tokens: int = 5, output_tokens: int = 15):
    """Create a TaskPlanner with mocked gateway and registry."""
    from jarvis_engine.agent.planner import TaskPlanner

    gateway = MagicMock()
    gateway.complete.return_value = _FakeGatewayResponse(
        text=llm_response,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    registry = MagicMock()
    registry.schemas_for_prompt.return_value = [
        {"name": "file", "description": "Read/write files", "parameters": {}},
        {"name": "shell", "description": "Run shell commands", "parameters": {}},
    ]

    return TaskPlanner(gateway=gateway, registry=registry), gateway, registry


def _steps_json(steps: list[dict[str, Any]]) -> str:
    return json.dumps(steps)


_VALID_STEP = {
    "step_index": 0,
    "tool_name": "file",
    "description": "Read README",
    "params": {"path": "README.md", "mode": "read"},
    "depends_on": [],
}

_TWO_STEPS = [
    _VALID_STEP,
    {
        "step_index": 1,
        "tool_name": "shell",
        "description": "List directory",
        "params": {"command": "ls -la"},
        "depends_on": [0],
    },
]


# ---------------------------------------------------------------------------
# AgentStep dataclass tests
# ---------------------------------------------------------------------------


class TestAgentStep:
    def test_dataclass_fields(self):
        from jarvis_engine.agent.planner import AgentStep

        step = AgentStep(
            step_index=0,
            tool_name="file",
            description="Read a file",
            params={"path": "foo.txt", "mode": "read"},
        )
        assert step.step_index == 0
        assert step.tool_name == "file"
        assert step.description == "Read a file"
        assert step.params == {"path": "foo.txt", "mode": "read"}
        assert step.depends_on == []

    def test_depends_on_default_is_empty_list(self):
        from jarvis_engine.agent.planner import AgentStep

        s1 = AgentStep(0, "file", "desc", {})
        s2 = AgentStep(1, "shell", "desc", {})
        # Mutable default must not be shared
        s1.depends_on.append(99)
        assert s2.depends_on == []

    def test_depends_on_explicit(self):
        from jarvis_engine.agent.planner import AgentStep

        step = AgentStep(2, "web", "Fetch page", {"url": "http://x.com"}, depends_on=[0, 1])
        assert step.depends_on == [0, 1]


# ---------------------------------------------------------------------------
# TaskPlanner.plan() tests
# ---------------------------------------------------------------------------


class TestTaskPlannerPlan:
    def test_calls_gateway_complete(self):
        planner, gateway, registry = _make_planner(_steps_json([_VALID_STEP]))
        planner.plan("Do something")
        gateway.complete.assert_called_once()

    def test_includes_tool_schemas_in_prompt(self):
        planner, gateway, registry = _make_planner(_steps_json([_VALID_STEP]))
        planner.plan("Do something")
        # System prompt should contain tool schema info
        call_args = gateway.complete.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        system_msg = next(m for m in messages if m["role"] == "system")
        assert "file" in system_msg["content"]

    def test_includes_goal_in_user_message(self):
        planner, gateway, registry = _make_planner(_steps_json([_VALID_STEP]))
        planner.plan("Build something cool")
        call_args = gateway.complete.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Build something cool" in user_msg["content"]

    def test_returns_list_of_agent_steps(self):
        from jarvis_engine.agent.planner import AgentStep

        planner, _, _ = _make_planner(_steps_json([_VALID_STEP]))
        steps, _ = planner.plan("goal")
        assert len(steps) == 1
        assert isinstance(steps[0], AgentStep)

    def test_returns_token_count(self):
        planner, _, _ = _make_planner(_steps_json([_VALID_STEP]), input_tokens=7, output_tokens=13)
        _, tokens = planner.plan("goal")
        assert tokens == 20  # 7 + 13

    def test_parses_two_steps(self):
        planner, _, _ = _make_planner(_steps_json(_TWO_STEPS))
        steps, _ = planner.plan("goal")
        assert len(steps) == 2
        assert steps[0].tool_name == "file"
        assert steps[1].tool_name == "shell"
        assert steps[1].depends_on == [0]

    def test_raises_on_invalid_json(self):
        planner, _, _ = _make_planner("not valid json")
        with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|parse|invalid"):
            planner.plan("goal")

    def test_raises_on_missing_tool_name(self):
        bad = [{"step_index": 0, "description": "oops", "params": {}}]
        planner, _, _ = _make_planner(_steps_json(bad))
        with pytest.raises(ValueError):
            planner.plan("goal")

    def test_raises_on_missing_description(self):
        bad = [{"step_index": 0, "tool_name": "file", "params": {}}]
        planner, _, _ = _make_planner(_steps_json(bad))
        with pytest.raises(ValueError):
            planner.plan("goal")

    def test_raises_on_missing_params(self):
        bad = [{"step_index": 0, "tool_name": "file", "description": "Read"}]
        planner, _, _ = _make_planner(_steps_json(bad))
        with pytest.raises(ValueError):
            planner.plan("goal")

    def test_handles_markdown_code_fence(self):
        """LLM often wraps JSON in ```json ... ``` blocks."""
        from jarvis_engine.agent.planner import AgentStep

        raw = "```json\n" + _steps_json([_VALID_STEP]) + "\n```"
        planner, _, _ = _make_planner(raw)
        steps, _ = planner.plan("goal")
        assert isinstance(steps[0], AgentStep)

    def test_handles_backtick_fence_no_lang(self):
        raw = "```\n" + _steps_json([_VALID_STEP]) + "\n```"
        planner, _, _ = _make_planner(raw)
        steps, _ = planner.plan("goal")
        assert len(steps) == 1

    def test_uses_route_reason_agent_planning(self):
        planner, gateway, _ = _make_planner(_steps_json([_VALID_STEP]))
        planner.plan("goal")
        call_kwargs = gateway.complete.call_args[1] if gateway.complete.call_args[1] else {}
        call_args_positional = gateway.complete.call_args[0]
        # route_reason should be "agent_planning"
        route_reason = call_kwargs.get("route_reason", "")
        if not route_reason:
            # might be positional -- check all args
            all_args = str(call_args_positional) + str(call_kwargs)
            assert "agent_planning" in all_args

    def test_empty_step_list_is_valid(self):
        planner, _, _ = _make_planner("[]")
        steps, _ = planner.plan("goal")
        assert steps == []

    def test_step_index_defaults_to_position_if_missing(self):
        """step_index is optional -- falls back to list position."""
        no_index = [
            {"tool_name": "file", "description": "Read", "params": {}},
        ]
        planner, _, _ = _make_planner(_steps_json(no_index))
        steps, _ = planner.plan("goal")
        assert steps[0].step_index == 0

    def test_depends_on_defaults_to_empty_if_missing(self):
        no_dep = [{"step_index": 0, "tool_name": "file", "description": "d", "params": {}}]
        planner, _, _ = _make_planner(_steps_json(no_dep))
        steps, _ = planner.plan("goal")
        assert steps[0].depends_on == []

    def test_response_not_a_list_raises(self):
        planner, _, _ = _make_planner('{"step": "oops"}')
        with pytest.raises(ValueError):
            planner.plan("goal")


# ---------------------------------------------------------------------------
# TaskPlanner.replan() tests
# ---------------------------------------------------------------------------


class TestTaskPlannerReplan:
    def test_replan_returns_revised_steps(self):
        from jarvis_engine.agent.planner import AgentStep

        revised = [
            {
                "step_index": 0,
                "tool_name": "web",
                "description": "Revised step",
                "params": {"url": "http://example.com"},
                "depends_on": [],
            }
        ]
        planner, gateway, _ = _make_planner(_steps_json(revised))

        from jarvis_engine.agent.planner import AgentStep

        remaining = [AgentStep(0, "file", "Old step", {})]
        steps, tokens = planner.replan(remaining, "FileNotFound error", "original goal")
        assert len(steps) == 1
        assert steps[0].tool_name == "web"
        assert tokens > 0

    def test_replan_includes_error_in_prompt(self):
        revised = [_VALID_STEP]
        planner, gateway, _ = _make_planner(_steps_json(revised))

        from jarvis_engine.agent.planner import AgentStep

        remaining = [AgentStep(0, "file", "desc", {})]
        planner.replan(remaining, "ENOENT: no such file", "goal")
        call_args = gateway.complete.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        combined = " ".join(m["content"] for m in messages)
        assert "ENOENT" in combined

    def test_replan_includes_original_goal(self):
        revised = [_VALID_STEP]
        planner, gateway, _ = _make_planner(_steps_json(revised))

        from jarvis_engine.agent.planner import AgentStep

        remaining = [AgentStep(0, "file", "desc", {})]
        planner.replan(remaining, "error", "Build the reactor")
        call_args = gateway.complete.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        combined = " ".join(m["content"] for m in messages)
        assert "Build the reactor" in combined

    def test_replan_empty_remaining(self):
        planner, gateway, _ = _make_planner("[]")
        steps, _ = planner.replan([], "error", "goal")
        assert steps == []

    def test_replan_raises_on_invalid_json(self):
        planner, gateway, _ = _make_planner("bad json")
        from jarvis_engine.agent.planner import AgentStep

        remaining = [AgentStep(0, "file", "desc", {})]
        with pytest.raises(ValueError):
            planner.replan(remaining, "error", "goal")
