"""Tests for handlers/agent_handlers.py -- real AgentRun/Status/Approve handlers.

TDD: RED phase -- tests written before real implementation.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------


@dataclass
class _FakeGatewayResponse:
    text: str
    model: str = "fake"
    provider: str = "fake"
    input_tokens: int = 5
    output_tokens: int = 10


def _make_db() -> sqlite3.Connection:
    """In-memory SQLite with row_factory for store."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _make_store(db: sqlite3.Connection | None = None):
    from jarvis_engine.agent.state_store import AgentStateStore

    if db is None:
        db = _make_db()
    return AgentStateStore(db)


def _make_bus():
    from jarvis_engine.agent.progress_bus import ProgressEventBus

    return ProgressEventBus()


def _make_gate(bus=None):
    from jarvis_engine.agent.approval_gate import ApprovalGate

    if bus is None:
        bus = _make_bus()
    return ApprovalGate(bus)


def _make_registry():
    from jarvis_engine.agent.tool_registry import ToolRegistry

    return ToolRegistry()


def _make_gateway(plan_json: str = "[]") -> MagicMock:
    gateway = MagicMock()
    gateway.complete.return_value = _FakeGatewayResponse(text=plan_json)
    return gateway


def _make_handlers(*, plan_json: str = "[]"):
    """Build all three handlers sharing the same store/gate/bus/gateway."""
    from jarvis_engine.handlers.agent_handlers import (
        AgentApproveHandler,
        AgentRunHandler,
        AgentStatusHandler,
    )

    root = Path("/fake/root")
    db = _make_db()
    store = _make_store(db)
    bus = _make_bus()
    gate = _make_gate(bus)
    registry = _make_registry()
    gateway = _make_gateway(plan_json)

    run_h = AgentRunHandler(root, gateway=gateway, registry=registry, store=store, gate=gate, bus=bus)
    status_h = AgentStatusHandler(root, store=store)
    approve_h = AgentApproveHandler(root, gate=gate)
    return run_h, status_h, approve_h, store, gate, bus


# ---------------------------------------------------------------------------
# AgentRunHandler tests
# ---------------------------------------------------------------------------


class TestAgentRunHandler:
    def test_run_returns_pending_immediately(self):
        """handle() returns AgentRunResult with status=pending before loop completes."""
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        run_h, *_ = _make_handlers()
        cmd = AgentRunCommand(goal="test goal", task_id="task-001")
        result = run_h.handle(cmd)
        assert result.return_code == 0
        assert result.task_id == "task-001"
        assert result.status == "pending"

    def test_run_generates_task_id_if_empty(self):
        """handle() auto-generates task_id when cmd.task_id is empty."""
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        run_h, *_ = _make_handlers()
        cmd = AgentRunCommand(goal="generate id test", task_id="")
        result = run_h.handle(cmd)
        assert result.task_id != ""
        assert len(result.task_id) >= 8

    def test_run_checkpoints_task_to_store(self):
        """handle() saves task to store with pending status."""
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        run_h, status_h, _, store, _, _ = _make_handlers()
        cmd = AgentRunCommand(goal="store test", task_id="task-store-01")
        run_h.handle(cmd)

        # Task should be in store immediately (before loop completes)
        task = store.load("task-store-01")
        assert task is not None
        assert task.goal == "store test"

    def test_run_uses_token_budget_from_command(self):
        """handle() respects the token_budget from the command."""
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        run_h, _, _, store, _, _ = _make_handlers()
        cmd = AgentRunCommand(goal="budget test", task_id="task-budget", token_budget=1234)
        run_h.handle(cmd)

        task = store.load("task-budget")
        assert task is not None
        assert task.token_budget == 1234

    def test_run_is_non_blocking(self):
        """handle() returns quickly before the agent loop completes."""
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        # Use a plan with 0 steps so loop completes without LLM calls
        run_h, *_ = _make_handlers(plan_json="[]")
        cmd = AgentRunCommand(goal="non-blocking", task_id="task-nb")
        start = time.monotonic()
        result = run_h.handle(cmd)
        elapsed = time.monotonic() - start
        assert result.return_code == 0
        # Should return in well under 5 seconds
        assert elapsed < 5.0

    def test_run_loop_updates_status_to_done_for_empty_plan(self):
        """Background loop transitions status to 'done' when plan has no steps."""
        from jarvis_engine.commands.agent_commands import AgentRunCommand

        run_h, _, _, store, _, _ = _make_handlers(plan_json="[]")
        cmd = AgentRunCommand(goal="empty plan", task_id="task-empty")
        run_h.handle(cmd)

        # Allow background thread time to complete
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            task = store.load("task-empty")
            if task and task.status in ("done", "failed"):
                break
            time.sleep(0.05)

        task = store.load("task-empty")
        assert task is not None
        assert task.status == "done"


# ---------------------------------------------------------------------------
# AgentStatusHandler tests
# ---------------------------------------------------------------------------


class TestAgentStatusHandler:
    def test_status_returns_not_found_for_unknown_id(self):
        """handle() returns return_code=1 for unknown task_id."""
        from jarvis_engine.commands.agent_commands import AgentStatusCommand
        from jarvis_engine.handlers.agent_handlers import AgentStatusHandler

        store = _make_store()
        handler = AgentStatusHandler(Path("/fake"), store=store)
        cmd = AgentStatusCommand(task_id="nonexistent")
        result = handler.handle(cmd)
        assert result.return_code == 1

    def test_status_returns_task_fields(self):
        """handle() returns status, step_index, tokens_used, last_error from stored task."""
        from jarvis_engine.agent.state_store import AgentTask
        from jarvis_engine.commands.agent_commands import AgentStatusCommand
        from jarvis_engine.handlers.agent_handlers import AgentStatusHandler

        store = _make_store()
        task = AgentTask(
            task_id="task-status-01",
            goal="test",
            status="running",
            step_index=2,
            tokens_used=100,
            last_error="some error",
        )
        store.checkpoint(task)

        handler = AgentStatusHandler(Path("/fake"), store=store)
        cmd = AgentStatusCommand(task_id="task-status-01")
        result = handler.handle(cmd)

        assert result.return_code == 0
        assert result.task_id == "task-status-01"
        assert result.status == "running"
        assert result.step_index == 2
        assert result.tokens_used == 100
        assert result.last_error == "some error"


# ---------------------------------------------------------------------------
# AgentApproveHandler tests
# ---------------------------------------------------------------------------


class TestAgentApproveHandler:
    def test_approve_calls_gate_approve(self):
        """handle() with approved=True calls gate.approve(task_id)."""
        from jarvis_engine.commands.agent_commands import AgentApproveCommand
        from jarvis_engine.handlers.agent_handlers import AgentApproveHandler

        gate = MagicMock()
        handler = AgentApproveHandler(Path("/fake"), gate=gate)
        cmd = AgentApproveCommand(task_id="task-approve", approved=True)
        result = handler.handle(cmd)

        gate.approve.assert_called_once_with("task-approve")
        assert result.return_code == 0
        assert "approved" in result.action_taken.lower()

    def test_reject_calls_gate_reject(self):
        """handle() with approved=False calls gate.reject(task_id)."""
        from jarvis_engine.commands.agent_commands import AgentApproveCommand
        from jarvis_engine.handlers.agent_handlers import AgentApproveHandler

        gate = MagicMock()
        handler = AgentApproveHandler(Path("/fake"), gate=gate)
        cmd = AgentApproveCommand(task_id="task-reject", approved=False)
        result = handler.handle(cmd)

        gate.reject.assert_called_once_with("task-reject")
        assert result.return_code == 0
        assert "rejected" in result.action_taken.lower()

    def test_approve_returns_task_id(self):
        """handle() returns the task_id in the result."""
        from jarvis_engine.commands.agent_commands import AgentApproveCommand
        from jarvis_engine.handlers.agent_handlers import AgentApproveHandler

        gate = MagicMock()
        handler = AgentApproveHandler(Path("/fake"), gate=gate)
        cmd = AgentApproveCommand(task_id="task-check-id", approved=True)
        result = handler.handle(cmd)
        assert result.task_id == "task-check-id"


# ---------------------------------------------------------------------------
# AgentRoutesMixin tests
# ---------------------------------------------------------------------------


class TestAgentRoutesMixin:
    """Verify AgentRoutesMixin is exported and importable."""

    def test_mixin_importable(self):
        from jarvis_engine.mobile_routes.agent import AgentRoutesMixin  # noqa: F401

        assert AgentRoutesMixin is not None

    def test_mixin_in_init_exports(self):
        from jarvis_engine.mobile_routes import AgentRoutesMixin  # noqa: F401

        assert AgentRoutesMixin is not None

    def test_mixin_has_required_methods(self):
        from jarvis_engine.mobile_routes.agent import AgentRoutesMixin

        assert hasattr(AgentRoutesMixin, "handle_agent_stream")
        assert hasattr(AgentRoutesMixin, "handle_agent_run")
        assert hasattr(AgentRoutesMixin, "handle_agent_status")
        assert hasattr(AgentRoutesMixin, "handle_agent_approve")

    def test_server_inherits_agent_mixin(self):
        """MobileIngestHandler inherits AgentRoutesMixin."""
        from jarvis_engine.mobile_routes.agent import AgentRoutesMixin
        from jarvis_engine.mobile_routes.server import MobileIngestHandler

        assert issubclass(MobileIngestHandler, AgentRoutesMixin)
