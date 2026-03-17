"""Tests for agent/executor.py -- StepExecutor tool dispatch with checkpointing.

TDD: RED phase -- all tests written before implementation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(task_id: str = "t1", token_budget: int = 1000) -> Any:
    """Create an AgentTask via the real dataclass."""
    from jarvis_engine.agent.state_store import AgentTask

    return AgentTask(task_id=task_id, goal="do stuff", token_budget=token_budget)


def _make_step(
    tool_name: str = "file",
    params: dict | None = None,
    step_index: int = 0,
) -> Any:
    from jarvis_engine.agent.planner import AgentStep

    return AgentStep(
        step_index=step_index,
        tool_name=tool_name,
        description=f"step {step_index}",
        params=params or {"path": "foo.txt", "mode": "read"},
    )


def _make_executor(
    tool_name: str = "file",
    tool_result: Any = "file contents",
    requires_approval: bool = False,
    is_destructive: bool = False,
    approval_decision: str = "AUTO",
) -> tuple[Any, Any, Any, Any, Any]:
    """Return (executor, registry, gate, store, bus)."""
    from jarvis_engine.agent.approval_gate import ApprovalDecision
    from jarvis_engine.agent.executor import StepExecutor

    # Build a fake async tool execute
    async def _tool_exec(**kwargs: Any) -> Any:
        return tool_result

    # ToolSpec mock
    spec = MagicMock()
    spec.name = tool_name
    spec.execute = _tool_exec
    spec.requires_approval = requires_approval
    spec.is_destructive = is_destructive

    registry = MagicMock()
    registry.get.return_value = spec

    gate = MagicMock()
    if approval_decision == "AUTO":
        gate.check.return_value = ApprovalDecision.AUTO
    else:
        gate.check.return_value = ApprovalDecision.REQUIRES_APPROVAL

    store = MagicMock()
    bus = MagicMock()
    bus.emit = AsyncMock()

    executor = StepExecutor(registry=registry, gate=gate, store=store, bus=bus)
    return executor, registry, gate, store, bus


# ---------------------------------------------------------------------------
# StepResult dataclass tests
# ---------------------------------------------------------------------------


class TestStepResult:
    def test_success_fields(self):
        from jarvis_engine.agent.executor import StepResult

        r = StepResult(success=True, output="hello")
        assert r.success is True
        assert r.output == "hello"
        assert r.error == ""
        assert r.tokens_used == 0

    def test_failure_fields(self):
        from jarvis_engine.agent.executor import StepResult

        r = StepResult(success=False, error="ENOENT")
        assert r.success is False
        assert r.error == "ENOENT"
        assert r.output == ""


# ---------------------------------------------------------------------------
# StepExecutor tests
# ---------------------------------------------------------------------------


class TestStepExecutorBasic:
    def test_returns_step_result_success(self):
        executor, _, _, _, _ = _make_executor(tool_result="ok content")
        task = _make_task()
        step = _make_step()
        result = asyncio.run(executor.execute_step(step, task))
        assert result.success is True
        assert "ok content" in result.output

    def test_unknown_tool_returns_failure(self):
        from jarvis_engine.agent.executor import StepExecutor

        registry = MagicMock()
        registry.get.return_value = None
        gate = MagicMock()
        store = MagicMock()
        bus = MagicMock()
        bus.emit = AsyncMock()
        executor = StepExecutor(registry=registry, gate=gate, store=store, bus=bus)

        task = _make_task()
        step = _make_step(tool_name="nonexistent")
        result = asyncio.run(executor.execute_step(step, task))
        assert result.success is False
        assert "Unknown tool" in result.error or "nonexistent" in result.error

    def test_tool_exception_returns_failure(self):
        async def _broken_tool(**kwargs: Any) -> Any:
            raise RuntimeError("disk full")

        spec = MagicMock()
        spec.name = "file"
        spec.execute = _broken_tool
        spec.requires_approval = False
        spec.is_destructive = False

        registry = MagicMock()
        registry.get.return_value = spec

        from jarvis_engine.agent.approval_gate import ApprovalDecision
        from jarvis_engine.agent.executor import StepExecutor

        gate = MagicMock()
        gate.check.return_value = ApprovalDecision.AUTO
        store = MagicMock()
        bus = MagicMock()
        bus.emit = AsyncMock()

        executor = StepExecutor(registry=registry, gate=gate, store=store, bus=bus)
        task = _make_task()
        step = _make_step()
        result = asyncio.run(executor.execute_step(step, task))
        assert result.success is False
        assert "disk full" in result.error


class TestStepExecutorCheckpointing:
    def test_checkpoints_before_tool_call(self):
        executor, _, _, store, _ = _make_executor()
        task = _make_task()
        step = _make_step()
        asyncio.run(executor.execute_step(step, task))
        store.checkpoint.assert_called()

    def test_task_status_set_running_before_call(self):
        """Capture task status at checkpoint time."""
        statuses_at_checkpoint: list[str] = []

        def _capture(t: Any) -> None:
            statuses_at_checkpoint.append(t.status)

        executor, _, _, store, _ = _make_executor()
        store.checkpoint.side_effect = _capture
        task = _make_task()
        step = _make_step()
        asyncio.run(executor.execute_step(step, task))
        assert "running" in statuses_at_checkpoint


class TestStepExecutorProgressEvents:
    def test_emits_step_start_event(self):
        executor, _, _, _, bus = _make_executor()
        task = _make_task()
        step = _make_step()
        asyncio.run(executor.execute_step(step, task))
        emit_calls = [str(call) for call in bus.emit.call_args_list]
        assert any("step_start" in c for c in emit_calls)

    def test_emits_step_done_event(self):
        executor, _, _, _, bus = _make_executor()
        task = _make_task()
        step = _make_step()
        asyncio.run(executor.execute_step(step, task))
        emit_calls = [str(call) for call in bus.emit.call_args_list]
        assert any("step_done" in c for c in emit_calls)


class TestStepExecutorApproval:
    def test_requires_approval_sets_task_blocked(self):
        from jarvis_engine.agent.approval_gate import ApprovalDecision
        from jarvis_engine.agent.executor import StepExecutor

        async def _tool(**kwargs: Any) -> Any:
            return "done"

        spec = MagicMock()
        spec.name = "shell"
        spec.execute = _tool
        spec.requires_approval = True
        spec.is_destructive = True

        registry = MagicMock()
        registry.get.return_value = spec

        gate = MagicMock()
        gate.check.return_value = ApprovalDecision.REQUIRES_APPROVAL
        gate.wait_for_approval = AsyncMock(return_value=True)

        store = MagicMock()
        bus = MagicMock()
        bus.emit = AsyncMock()

        executor = StepExecutor(registry=registry, gate=gate, store=store, bus=bus)
        task = _make_task()
        step = _make_step(tool_name="shell", params={"command": "ls"})

        captured_statuses: list[str] = []

        def _capture_status(t: Any) -> None:
            captured_statuses.append(t.status)

        store.checkpoint.side_effect = _capture_status
        asyncio.run(executor.execute_step(step, task))
        # The first checkpoint should occur while status is "blocked"
        assert "blocked" in captured_statuses

    def test_approval_rejected_returns_failure(self):
        from jarvis_engine.agent.approval_gate import ApprovalDecision
        from jarvis_engine.agent.executor import StepExecutor

        async def _tool(**kwargs: Any) -> Any:
            return "done"

        spec = MagicMock()
        spec.name = "shell"
        spec.execute = _tool
        spec.requires_approval = True
        spec.is_destructive = True

        registry = MagicMock()
        registry.get.return_value = spec

        gate = MagicMock()
        gate.check.return_value = ApprovalDecision.REQUIRES_APPROVAL
        gate.wait_for_approval = AsyncMock(return_value=False)

        store = MagicMock()
        bus = MagicMock()
        bus.emit = AsyncMock()

        executor = StepExecutor(registry=registry, gate=gate, store=store, bus=bus)
        task = _make_task()
        step = _make_step(tool_name="shell", params={"command": "rm -rf /"})

        result = asyncio.run(executor.execute_step(step, task))
        assert result.success is False
        assert "reject" in result.error.lower() or "approv" in result.error.lower()

    def test_auto_decision_skips_approval_wait(self):
        from jarvis_engine.agent.approval_gate import ApprovalDecision
        from jarvis_engine.agent.executor import StepExecutor

        async def _tool(**kwargs: Any) -> Any:
            return "safe result"

        spec = MagicMock()
        spec.name = "file"
        spec.execute = _tool
        spec.requires_approval = False
        spec.is_destructive = False

        registry = MagicMock()
        registry.get.return_value = spec

        gate = MagicMock()
        gate.check.return_value = ApprovalDecision.AUTO
        gate.wait_for_approval = AsyncMock()

        store = MagicMock()
        bus = MagicMock()
        bus.emit = AsyncMock()

        executor = StepExecutor(registry=registry, gate=gate, store=store, bus=bus)
        task = _make_task()
        step = _make_step()
        result = asyncio.run(executor.execute_step(step, task))
        assert result.success is True
        gate.wait_for_approval.assert_not_called()


class TestStepExecutorSyncTool:
    def test_sync_tool_is_handled(self):
        """Tools that return non-coroutines should also work."""
        from jarvis_engine.agent.approval_gate import ApprovalDecision
        from jarvis_engine.agent.executor import StepExecutor

        def _sync_tool(**kwargs: Any) -> str:
            return "sync result"

        spec = MagicMock()
        spec.name = "sync_tool"
        spec.execute = _sync_tool
        spec.requires_approval = False
        spec.is_destructive = False

        registry = MagicMock()
        registry.get.return_value = spec

        gate = MagicMock()
        gate.check.return_value = ApprovalDecision.AUTO
        store = MagicMock()
        bus = MagicMock()
        bus.emit = AsyncMock()

        executor = StepExecutor(registry=registry, gate=gate, store=store, bus=bus)
        task = _make_task()
        step = _make_step()
        result = asyncio.run(executor.execute_step(step, task))
        assert result.success is True
        assert "sync result" in result.output
