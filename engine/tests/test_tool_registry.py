"""Tests for ToolRegistry -- pluggable tool discovery."""
from __future__ import annotations

import pytest

from jarvis_engine.agent.tool_registry import ToolRegistry, ToolSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_execute(**kwargs: object) -> str:
    return "ok"


def _make_spec(
    name: str = "run_tests",
    requires_approval: bool = False,
    is_destructive: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Run the test suite",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        },
        execute=_noop_execute,
        requires_approval=requires_approval,
        is_destructive=is_destructive,
    )


# ---------------------------------------------------------------------------
# Basic register / get / list
# ---------------------------------------------------------------------------


def test_register_and_get() -> None:
    reg = ToolRegistry()
    spec = _make_spec()
    reg.register(spec)
    retrieved = reg.get("run_tests")
    assert retrieved is spec


def test_get_unknown_returns_none() -> None:
    reg = ToolRegistry()
    assert reg.get("unknown") is None


def test_list_tools_returns_all() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("tool_a"))
    reg.register(_make_spec("tool_b"))
    tools = reg.list_tools()
    names = {t.name for t in tools}
    assert names == {"tool_a", "tool_b"}


def test_list_tools_empty() -> None:
    reg = ToolRegistry()
    assert reg.list_tools() == []


def test_len() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("a"))
    reg.register(_make_spec("b"))
    assert len(reg) == 2


# ---------------------------------------------------------------------------
# Overwrite / warning
# ---------------------------------------------------------------------------


def test_register_overwrite_replaces(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    reg = ToolRegistry()
    old = _make_spec("tool_x")
    new = ToolSpec(
        name="tool_x",
        description="New description",
        parameters={},
        execute=_noop_execute,
    )
    reg.register(old)
    with caplog.at_level(logging.WARNING):
        reg.register(new)
    assert reg.get("tool_x") is new
    assert any("tool_x" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# is_destructive forces requires_approval
# ---------------------------------------------------------------------------


def test_is_destructive_forces_requires_approval() -> None:
    spec = _make_spec(is_destructive=True, requires_approval=False)
    assert spec.requires_approval is True


def test_non_destructive_does_not_force_approval() -> None:
    spec = _make_spec(is_destructive=False, requires_approval=False)
    assert spec.requires_approval is False


def test_non_destructive_explicit_approval() -> None:
    spec = _make_spec(is_destructive=False, requires_approval=True)
    assert spec.requires_approval is True


# ---------------------------------------------------------------------------
# schemas_for_prompt
# ---------------------------------------------------------------------------


def test_schemas_for_prompt_structure() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("tool_a"))
    schemas = reg.schemas_for_prompt()
    assert len(schemas) == 1
    schema = schemas[0]
    assert schema["name"] == "tool_a"
    assert "description" in schema
    assert "parameters" in schema
    assert "requires_approval" in schema


def test_schemas_for_prompt_empty() -> None:
    reg = ToolRegistry()
    assert reg.schemas_for_prompt() == []


def test_schemas_for_prompt_approval_flag() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("safe_tool", requires_approval=False))
    reg.register(_make_spec("danger_tool", is_destructive=True))
    schemas = {s["name"]: s for s in reg.schemas_for_prompt()}
    assert schemas["safe_tool"]["requires_approval"] is False
    assert schemas["danger_tool"]["requires_approval"] is True


# ---------------------------------------------------------------------------
# Optional callable defaults
# ---------------------------------------------------------------------------


def test_toolspec_validate_default_returns_true() -> None:
    spec = _make_spec()
    assert spec.validate(path="test") is True


def test_toolspec_estimate_cost_default_returns_zero() -> None:
    spec = _make_spec()
    assert spec.estimate_cost(path="test") == 0.0
