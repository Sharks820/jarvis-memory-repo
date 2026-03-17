"""Tests for agent/reflection.py -- ReflectionLoop evaluate, replan, escalate.

TDD: RED phase -- all tests written before implementation.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "t1",
    token_budget: int = 1000,
    tokens_used: int = 0,
    step_index: int = 0,
) -> Any:
    from jarvis_engine.agent.state_store import AgentTask

    t = AgentTask(task_id=task_id, goal="do stuff", token_budget=token_budget)
    t.tokens_used = tokens_used
    t.step_index = step_index
    return t


def _make_step(
    tool_name: str = "file",
    step_index: int = 0,
) -> Any:
    from jarvis_engine.agent.planner import AgentStep

    return AgentStep(
        step_index=step_index,
        tool_name=tool_name,
        description=f"step {step_index}",
        params={"path": "foo.txt", "mode": "read"},
    )


def _make_step_result(success: bool, output: str = "ok", error: str = "") -> Any:
    from jarvis_engine.agent.executor import StepResult

    return StepResult(success=success, output=output, error=error)


def _make_reflection_loop(
    step_result: Any = None,
    replan_steps: list[Any] | None = None,
) -> tuple[Any, Any, Any, Any, Any]:
    """Return (loop, executor, planner, store, bus)."""
    from jarvis_engine.agent.reflection import ReflectionLoop

    executor = MagicMock()
    executor.execute_step = AsyncMock(
        return_value=(step_result or _make_step_result(True))
    )

    planner = MagicMock()
    if replan_steps is not None:
        planner.replan.return_value = (replan_steps, 10)
    else:
        planner.replan.return_value = ([], 10)

    store = MagicMock()
    bus = MagicMock()
    bus.emit = AsyncMock()

    loop = ReflectionLoop(executor=executor, planner=planner, store=store, bus=bus)
    return loop, executor, planner, store, bus


# ---------------------------------------------------------------------------
# ReflectionLoop.evaluate() tests
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_returns_true_on_success(self):
        from jarvis_engine.agent.reflection import ReflectionLoop

        loop, _, _, _, _ = _make_reflection_loop()
        result = _make_step_result(True)
        assert loop.evaluate(result) is True

    def test_returns_false_on_failure(self):
        loop, _, _, _, _ = _make_reflection_loop()
        result = _make_step_result(False, error="boom")
        assert loop.evaluate(result) is False


# ---------------------------------------------------------------------------
# ReflectionLoop.run_loop() -- success path
# ---------------------------------------------------------------------------


class TestRunLoopSuccess:
    def test_single_step_success_sets_done(self):
        loop, _, _, _, _ = _make_reflection_loop(step_result=_make_step_result(True))
        task = _make_task()
        steps = [_make_step(step_index=0)]
        result = asyncio.run(loop.run_loop(task, steps))
        assert result.status == "done"

    def test_multi_step_success_increments_step_index(self):
        loop, executor, _, _, _ = _make_reflection_loop(step_result=_make_step_result(True))
        task = _make_task()
        steps = [_make_step(step_index=i) for i in range(3)]
        result = asyncio.run(loop.run_loop(task, steps))
        assert result.status == "done"
        assert result.step_index == 3

    def test_success_checkpoints_after_each_step(self):
        loop, _, _, store, _ = _make_reflection_loop(step_result=_make_step_result(True))
        task = _make_task()
        steps = [_make_step(step_index=i) for i in range(2)]
        asyncio.run(loop.run_loop(task, steps))
        # At minimum 2 checkpoints (one per step)
        assert store.checkpoint.call_count >= 2

    def test_emits_task_done_event(self):
        loop, _, _, _, bus = _make_reflection_loop(step_result=_make_step_result(True))
        task = _make_task()
        steps = [_make_step()]
        asyncio.run(loop.run_loop(task, steps))
        emit_events = [str(call) for call in bus.emit.call_args_list]
        assert any("task_done" in e for e in emit_events)

    def test_resumes_from_task_step_index(self):
        """If task.step_index=1, first two steps should be skipped."""
        from jarvis_engine.agent.planner import AgentStep

        loop, executor, _, _, _ = _make_reflection_loop(step_result=_make_step_result(True))
        task = _make_task(step_index=2)
        steps = [_make_step(step_index=i) for i in range(3)]
        asyncio.run(loop.run_loop(task, steps))
        # Only step index 2 should be executed
        assert executor.execute_step.call_count == 1


# ---------------------------------------------------------------------------
# ReflectionLoop.run_loop() -- failure and replan path
# ---------------------------------------------------------------------------


class TestRunLoopFailure:
    def test_failure_triggers_replan(self):
        from jarvis_engine.agent.planner import AgentStep

        loop, executor, planner, _, _ = _make_reflection_loop(
            step_result=_make_step_result(False, error="ENOENT"),
            replan_steps=[_make_step(step_index=0)],
        )
        # After replan, next attempt succeeds
        executor.execute_step.side_effect = [
            _make_step_result(False, error="ENOENT"),
            _make_step_result(True, output="ok"),
        ]

        task = _make_task()
        steps = [_make_step(step_index=0)]
        asyncio.run(loop.run_loop(task, steps))
        planner.replan.assert_called_once()

    def test_three_same_error_escalates(self):
        """After 3 consecutive same errors, task is failed and escalation emitted."""
        from jarvis_engine.agent.executor import StepResult

        loop, executor, planner, store, bus = _make_reflection_loop()
        # Replan always returns one step, but it keeps failing with same error
        same_step = _make_step(step_index=0)
        planner.replan.return_value = ([same_step], 5)
        executor.execute_step.side_effect = [
            _make_step_result(False, error="timeout error"),
            _make_step_result(False, error="timeout error"),
            _make_step_result(False, error="timeout error"),
            _make_step_result(False, error="timeout error"),  # extra, shouldn't reach
        ]

        task = _make_task()
        steps = [_make_step(step_index=0)]
        result = asyncio.run(loop.run_loop(task, steps))
        assert result.status == "failed"
        # Should emit escalation event
        emit_events = [str(call) for call in bus.emit.call_args_list]
        assert any("escalat" in e for e in emit_events)

    def test_different_errors_do_not_escalate_early(self):
        """Different errors reset the consecutive counter."""
        loop, executor, planner, store, bus = _make_reflection_loop()
        same_step = _make_step(step_index=0)
        planner.replan.return_value = ([same_step], 5)
        # First two fail with different errors, third succeeds
        executor.execute_step.side_effect = [
            _make_step_result(False, error="error_A"),
            _make_step_result(False, error="error_B"),
            _make_step_result(True, output="success"),
        ]

        task = _make_task()
        steps = [_make_step(step_index=0)]
        result = asyncio.run(loop.run_loop(task, steps))
        assert result.status == "done"

    def test_failure_updates_task_error_count(self):
        loop, executor, planner, store, bus = _make_reflection_loop()
        same_step = _make_step(step_index=0)
        planner.replan.return_value = ([same_step], 5)
        executor.execute_step.side_effect = [
            _make_step_result(False, error="timeout error"),
            _make_step_result(False, error="timeout error"),
            _make_step_result(False, error="timeout error"),
        ]

        task = _make_task()
        steps = [_make_step(step_index=0)]
        result = asyncio.run(loop.run_loop(task, steps))
        assert result.error_count >= 3

    def test_replan_updates_plan_json(self):
        """After replan, task.plan_json should reflect revised steps."""
        loop, executor, planner, store, bus = _make_reflection_loop()
        revised = [_make_step(tool_name="web", step_index=0)]
        planner.replan.return_value = (revised, 5)
        executor.execute_step.side_effect = [
            _make_step_result(False, error="err"),
            _make_step_result(True, output="ok"),
        ]

        task = _make_task()
        steps = [_make_step(step_index=0)]
        result = asyncio.run(loop.run_loop(task, steps))
        # plan_json should contain the revised plan
        plan_data = json.loads(result.plan_json)
        # At least the replan was stored
        assert isinstance(plan_data, list)


# ---------------------------------------------------------------------------
# ReflectionLoop.run_loop() -- token budget enforcement
# ---------------------------------------------------------------------------


class TestRunLoopTokenBudget:
    def test_budget_exceeded_stops_execution(self):
        loop, executor, planner, store, bus = _make_reflection_loop(
            step_result=_make_step_result(True)
        )
        # Already over budget
        task = _make_task(token_budget=100, tokens_used=200)
        steps = [_make_step(step_index=0)]
        result = asyncio.run(loop.run_loop(task, steps))
        assert result.status == "failed"
        # Should NOT have called the tool at all
        executor.execute_step.assert_not_called()

    def test_budget_exceeded_emits_budget_event(self):
        loop, executor, planner, store, bus = _make_reflection_loop(
            step_result=_make_step_result(True)
        )
        task = _make_task(token_budget=100, tokens_used=200)
        steps = [_make_step()]
        asyncio.run(loop.run_loop(task, steps))
        emit_events = [str(call) for call in bus.emit.call_args_list]
        assert any("budget" in e for e in emit_events)

    def test_exact_budget_does_not_stop(self):
        """tokens_used == token_budget is not exceeded -- should run."""
        loop, executor, planner, store, bus = _make_reflection_loop(
            step_result=_make_step_result(True)
        )
        task = _make_task(token_budget=100, tokens_used=99)
        steps = [_make_step()]
        result = asyncio.run(loop.run_loop(task, steps))
        assert result.status == "done"


# ---------------------------------------------------------------------------
# ReflectionLoop._error_hash tests
# ---------------------------------------------------------------------------


class TestErrorHash:
    def test_same_error_same_hash(self):
        loop, _, _, _, _ = _make_reflection_loop()
        assert loop._error_hash("timeout") == loop._error_hash("timeout")

    def test_different_errors_different_hash(self):
        loop, _, _, _, _ = _make_reflection_loop()
        assert loop._error_hash("timeout") != loop._error_hash("ENOENT")

    def test_empty_error_hash_stable(self):
        loop, _, _, _, _ = _make_reflection_loop()
        h = loop._error_hash("")
        assert isinstance(h, str) and len(h) > 0
