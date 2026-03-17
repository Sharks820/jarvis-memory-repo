"""Tests for ApprovalGate.

Uses asyncio.run() pattern (no pytest-asyncio) to match project convention.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_spec(
    *,
    requires_approval: bool = False,
    is_destructive: bool = False,
    estimate_cost_return: float = 0.0,
) -> MagicMock:
    """Build a mock ToolSpec with configurable flags."""
    spec = MagicMock()
    spec.requires_approval = requires_approval
    spec.is_destructive = is_destructive
    spec.estimate_cost = MagicMock(return_value=estimate_cost_return)
    return spec


def _make_gate():
    from jarvis_engine.agent.approval_gate import ApprovalGate
    from jarvis_engine.agent.progress_bus import ProgressEventBus

    bus = ProgressEventBus()
    return ApprovalGate(progress_bus=bus), bus


class TestApprovalDecision:
    def test_enum_values_exist(self) -> None:
        from jarvis_engine.agent.approval_gate import ApprovalDecision

        assert hasattr(ApprovalDecision, "AUTO")
        assert hasattr(ApprovalDecision, "REQUIRES_APPROVAL")


class TestApprovalGate:
    def test_safe_tool_returns_auto(self) -> None:
        gate, _ = _make_gate()
        from jarvis_engine.agent.approval_gate import ApprovalDecision

        spec = _make_spec(requires_approval=False, is_destructive=False)

        result = gate.check(spec, {})

        assert result == ApprovalDecision.AUTO

    def test_requires_approval_flag_returns_requires_approval(self) -> None:
        gate, _ = _make_gate()
        from jarvis_engine.agent.approval_gate import ApprovalDecision

        spec = _make_spec(requires_approval=True, is_destructive=False)

        result = gate.check(spec, {})

        assert result == ApprovalDecision.REQUIRES_APPROVAL

    def test_is_destructive_flag_returns_requires_approval(self) -> None:
        gate, _ = _make_gate()
        from jarvis_engine.agent.approval_gate import ApprovalDecision

        spec = _make_spec(requires_approval=False, is_destructive=True)

        result = gate.check(spec, {})

        assert result == ApprovalDecision.REQUIRES_APPROVAL

    def test_costly_tool_returns_requires_approval(self) -> None:
        gate, _ = _make_gate()
        from jarvis_engine.agent.approval_gate import ApprovalDecision

        spec = _make_spec(requires_approval=False, is_destructive=False, estimate_cost_return=1.5)

        result = gate.check(spec, {})

        assert result == ApprovalDecision.REQUIRES_APPROVAL

    def test_zero_cost_safe_tool_returns_auto(self) -> None:
        gate, _ = _make_gate()
        from jarvis_engine.agent.approval_gate import ApprovalDecision

        spec = _make_spec(estimate_cost_return=0.0)

        result = gate.check(spec, {})

        assert result == ApprovalDecision.AUTO

    def test_approve_resolves_wait(self) -> None:
        gate, _ = _make_gate()

        async def run() -> bool:
            task_id = "task-approve-1"
            # Start waiting in background
            wait_task = asyncio.create_task(gate.wait_for_approval(task_id, "test step"))
            await asyncio.sleep(0)  # yield to let task start
            gate.approve(task_id)
            return await wait_task

        result = asyncio.run(run())
        assert result is True

    def test_reject_resolves_wait_with_false(self) -> None:
        gate, _ = _make_gate()

        async def run() -> bool:
            task_id = "task-reject-1"
            wait_task = asyncio.create_task(gate.wait_for_approval(task_id, "test step"))
            await asyncio.sleep(0)
            gate.reject(task_id)
            return await wait_task

        result = asyncio.run(run())
        assert result is False

    def test_wait_for_approval_emits_event_on_bus(self) -> None:
        from jarvis_engine.agent.approval_gate import ApprovalGate
        from jarvis_engine.agent.progress_bus import ProgressEventBus

        bus = ProgressEventBus()
        queue = bus.subscribe()
        gate = ApprovalGate(progress_bus=bus)

        async def run() -> None:
            task_id = "task-emit-1"
            wait_task = asyncio.create_task(gate.wait_for_approval(task_id, "emit test"))
            await asyncio.sleep(0)
            gate.approve(task_id)
            await wait_task

        asyncio.run(run())

        # Bus should have received an approval_needed event
        assert not queue.empty()
        event = queue.get_nowait()
        assert event.get("type") == "approval_needed"
        assert event.get("task_id") == "task-emit-1"

    def test_approve_unknown_task_is_noop(self) -> None:
        gate, _ = _make_gate()
        # Should not raise
        gate.approve("nonexistent-task")

    def test_reject_unknown_task_is_noop(self) -> None:
        gate, _ = _make_gate()
        # Should not raise
        gate.reject("nonexistent-task")
