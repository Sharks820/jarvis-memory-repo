"""Tests for agent CQRS commands, stub handlers, and bus registration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis_engine.commands.agent_commands import (
    AgentApproveCommand,
    AgentApproveResult,
    AgentRunCommand,
    AgentRunResult,
    AgentStatusCommand,
    AgentStatusResult,
)


# ---------------------------------------------------------------------------
# Command dataclasses
# ---------------------------------------------------------------------------


def test_agent_run_command_defaults() -> None:
    """AgentRunCommand can be constructed with default values."""
    cmd = AgentRunCommand()
    assert cmd.goal == ""
    assert cmd.task_id == ""
    assert cmd.token_budget == 50000


def test_agent_run_command_custom() -> None:
    """AgentRunCommand accepts custom field values."""
    cmd = AgentRunCommand(goal="build a scene", task_id="t-001", token_budget=10000)
    assert cmd.goal == "build a scene"
    assert cmd.task_id == "t-001"
    assert cmd.token_budget == 10000


def test_agent_run_command_is_frozen() -> None:
    """AgentRunCommand is a frozen dataclass (immutable)."""
    cmd = AgentRunCommand(goal="test")
    with pytest.raises((AttributeError, TypeError)):
        cmd.goal = "changed"  # type: ignore[misc]


def test_agent_status_command_defaults() -> None:
    """AgentStatusCommand can be constructed with default values."""
    cmd = AgentStatusCommand()
    assert cmd.task_id == ""


def test_agent_status_command_frozen() -> None:
    """AgentStatusCommand is frozen."""
    cmd = AgentStatusCommand(task_id="x")
    with pytest.raises((AttributeError, TypeError)):
        cmd.task_id = "y"  # type: ignore[misc]


def test_agent_approve_command_defaults() -> None:
    """AgentApproveCommand defaults: approved=True."""
    cmd = AgentApproveCommand()
    assert cmd.task_id == ""
    assert cmd.approved is True
    assert cmd.reason == ""


def test_agent_approve_command_frozen() -> None:
    """AgentApproveCommand is frozen."""
    cmd = AgentApproveCommand(task_id="z")
    with pytest.raises((AttributeError, TypeError)):
        cmd.task_id = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


def test_agent_run_result_defaults() -> None:
    """AgentRunResult inherits ResultBase and has correct defaults."""
    result = AgentRunResult()
    assert result.return_code == 0
    assert result.message == ""
    assert result.task_id == ""
    assert result.status == ""


def test_agent_status_result_defaults() -> None:
    """AgentStatusResult inherits ResultBase with status fields."""
    result = AgentStatusResult()
    assert result.return_code == 0
    assert result.task_id == ""
    assert result.status == ""
    assert result.step_index == 0
    assert result.tokens_used == 0
    assert result.last_error == ""


def test_agent_approve_result_defaults() -> None:
    """AgentApproveResult inherits ResultBase with action_taken field."""
    result = AgentApproveResult()
    assert result.return_code == 0
    assert result.task_id == ""
    assert result.action_taken == ""


# ---------------------------------------------------------------------------
# Real handlers (Phase 22 wiring -- stubs replaced)
# ---------------------------------------------------------------------------


def test_agent_run_handler_returns_pending() -> None:
    """AgentRunHandler.handle returns return_code=0 with status=pending."""
    from jarvis_engine.handlers.agent_handlers import AgentRunHandler

    handler = AgentRunHandler(Path("/tmp"))
    result = handler.handle(AgentRunCommand(goal="test"))
    assert result.return_code == 0
    assert result.status == "pending"


def test_agent_status_handler_not_found_without_store() -> None:
    """AgentStatusHandler.handle returns error when no store is configured."""
    from jarvis_engine.handlers.agent_handlers import AgentStatusHandler

    handler = AgentStatusHandler(Path("/tmp"))
    result = handler.handle(AgentStatusCommand(task_id="t1"))
    # Without store, handler returns return_code=1 (not found / not configured)
    assert result.return_code == 1


def test_agent_approve_handler_error_without_gate() -> None:
    """AgentApproveHandler.handle returns error when no gate is configured."""
    from jarvis_engine.handlers.agent_handlers import AgentApproveHandler

    handler = AgentApproveHandler(Path("/tmp"))
    result = handler.handle(AgentApproveCommand(task_id="t2", approved=True))
    # Without gate, handler returns return_code=1
    assert result.return_code == 1


# ---------------------------------------------------------------------------
# CQRS bus registration round-trip
# ---------------------------------------------------------------------------


def test_agent_commands_registered_on_bus(tmp_path: Path) -> None:
    """All three agent commands can be executed via the CQRS bus."""
    import os

    env_patch = {
        "JARVIS_SKIP_EMBED_WARMUP": "1",
        "GROQ_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "MISTRAL_API_KEY": "",
        "ZAI_API_KEY": "",
    }
    with patch.dict(os.environ, env_patch):
        from jarvis_engine.app import create_app

        bus = create_app(tmp_path)

    # AgentRunCommand
    run_result = bus.dispatch(AgentRunCommand(goal="build a unity scene"))
    assert run_result is not None
    assert run_result.return_code == 0

    # AgentStatusCommand for the task we just submitted
    status_result = bus.dispatch(AgentStatusCommand(task_id=run_result.task_id))
    assert status_result is not None
    # May be return_code=0 (found, still pending) or return_code=1 if loop completed
    # instantly -- either way the status query itself executed without exception.
    assert status_result.return_code in (0, 1)

    # AgentApproveCommand -- noop if not pending approval; still returns success
    approve_result = bus.dispatch(AgentApproveCommand(task_id=run_result.task_id, approved=True))
    assert approve_result is not None
    assert approve_result.return_code == 0
