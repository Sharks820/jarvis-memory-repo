"""End-to-end integration tests for the agent loop.

Uses real AgentStateStore (in-memory SQLite), real ReflectionLoop/StepExecutor/
TaskPlanner instances, but mocked ModelGateway and tool execute functions.

All tests use asyncio.run() pattern (no pytest-asyncio) to match project convention.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _make_store(db: sqlite3.Connection | None = None):
    from jarvis_engine.agent.state_store import AgentStateStore, AgentTask

    if db is None:
        db = _make_db()
    return AgentStateStore(db), AgentTask


def _make_bus():
    from jarvis_engine.agent.progress_bus import ProgressEventBus

    return ProgressEventBus()


def _make_gate(bus=None):
    from jarvis_engine.agent.approval_gate import ApprovalGate

    if bus is None:
        bus = _make_bus()
    return ApprovalGate(bus)


@dataclass
class _FakeGatewayResponse:
    text: str
    model: str = "fake"
    provider: str = "fake"
    input_tokens: int = 10
    output_tokens: int = 15


def _step_plan_json(steps: list[dict]) -> str:
    """Format a list of step dicts as the JSON the LLM would return."""
    return json.dumps(steps)


def _make_two_step_plan() -> str:
    """Return LLM JSON for a simple 2-step plan."""
    return _step_plan_json([
        {
            "step_index": 0,
            "tool_name": "mock_tool",
            "description": "step one",
            "params": {"action": "one"},
            "depends_on": [],
        },
        {
            "step_index": 1,
            "tool_name": "mock_tool",
            "description": "step two",
            "params": {"action": "two"},
            "depends_on": [0],
        },
    ])


def _make_gateway(plan_json: str, *, input_tokens: int = 10, output_tokens: int = 15):
    gw = MagicMock()
    gw.complete.return_value = _FakeGatewayResponse(
        text=plan_json, input_tokens=input_tokens, output_tokens=output_tokens
    )
    return gw


def _make_registry_with_mock_tool(*, fail: bool = False, error_msg: str = "tool error"):
    """Build a ToolRegistry with a 'mock_tool' that always succeeds or always fails."""
    from jarvis_engine.agent.tool_registry import ToolRegistry, ToolSpec

    registry = ToolRegistry()

    async def _execute(**kwargs: Any) -> str:
        if fail:
            raise RuntimeError(error_msg)
        return f"ok: {kwargs}"

    spec = ToolSpec(
        name="mock_tool",
        description="Mock tool for testing",
        parameters={"type": "object", "properties": {}},
        execute=_execute,
        requires_approval=False,
        is_destructive=False,
    )
    registry.register(spec)
    return registry


def _make_destructive_registry():
    """Build a ToolRegistry with a destructive tool that requires approval."""
    from jarvis_engine.agent.tool_registry import ToolRegistry, ToolSpec

    registry = ToolRegistry()

    async def _execute(**kwargs: Any) -> str:
        return "destructive action done"

    spec = ToolSpec(
        name="mock_tool",
        description="Destructive mock tool",
        parameters={"type": "object", "properties": {}},
        execute=_execute,
        requires_approval=True,
        is_destructive=True,
    )
    registry.register(spec)
    return registry


def _build_full_loop(
    plan_json: str,
    registry=None,
    *,
    replan_json: str | None = None,
    input_tokens: int = 10,
    output_tokens: int = 15,
    bus=None,
    gate=None,
):
    """Build ReflectionLoop + TaskPlanner + StepExecutor with shared store and bus."""
    from jarvis_engine.agent.executor import StepExecutor
    from jarvis_engine.agent.planner import TaskPlanner
    from jarvis_engine.agent.reflection import ReflectionLoop
    from jarvis_engine.agent.state_store import AgentStateStore

    db = _make_db()
    store = AgentStateStore(db)

    if bus is None:
        bus = _make_bus()
    if gate is None:
        gate = _make_gate(bus)
    if registry is None:
        registry = _make_registry_with_mock_tool()

    # Mock gateway: first call returns plan_json, subsequent calls return replan_json if provided
    gw = MagicMock()
    call_count = [0]

    def _complete(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1 or replan_json is None:
            return _FakeGatewayResponse(
                text=plan_json, input_tokens=input_tokens, output_tokens=output_tokens
            )
        return _FakeGatewayResponse(
            text=replan_json, input_tokens=input_tokens, output_tokens=output_tokens
        )

    gw.complete.side_effect = _complete

    planner = TaskPlanner(gw, registry)
    executor = StepExecutor(registry, gate, store, bus)
    loop = ReflectionLoop(executor, planner, store, bus)

    return loop, planner, store, bus, gate, registry


def _make_task(task_id: str = "t1", token_budget: int = 10000):
    from jarvis_engine.agent.state_store import AgentTask

    return AgentTask(task_id=task_id, goal="complete the task", token_budget=token_budget)


# ---------------------------------------------------------------------------
# Test 1: Happy path -- 2-step plan completes successfully
# ---------------------------------------------------------------------------


def test_happy_path():
    """Full lifecycle: plan 2 steps, execute both, task reaches 'done'."""
    loop, planner, store, bus, gate, registry = _build_full_loop(_make_two_step_plan())
    task = _make_task("happy-01")
    store.checkpoint(task)

    # Plan steps
    steps, tokens = planner.plan(task.goal)
    task.tokens_used += tokens
    task.status = "running"
    store.checkpoint(task)

    completed_task = asyncio.run(loop.run_loop(task, steps))

    assert completed_task.status == "done"
    assert completed_task.step_index == 2  # both steps completed
    assert completed_task.tokens_used > 0

    # Verify persisted in store
    persisted = store.load("happy-01")
    assert persisted is not None
    assert persisted.status == "done"


def test_happy_path_emits_progress_events():
    """Progress events are emitted for step execution and task completion."""
    bus = _make_bus()
    loop, planner, store, _bus, gate, registry = _build_full_loop(
        _make_two_step_plan(), bus=bus
    )
    task = _make_task("happy-events")
    store.checkpoint(task)

    # Subscribe to events BEFORE running
    event_queue = bus.subscribe()

    steps, tokens = planner.plan(task.goal)
    task.tokens_used += tokens
    task.status = "running"
    store.checkpoint(task)

    completed_task = asyncio.run(loop.run_loop(task, steps))

    # Collect all emitted events from the queue
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())

    event_types = [e["type"] for e in events]
    assert "task_done" in event_types

    # step_start and step_done events for each step
    assert "step_start" in event_types
    assert "step_done" in event_types


# ---------------------------------------------------------------------------
# Test 2: Failure and replan -- tool fails, replan succeeds
# ---------------------------------------------------------------------------


def test_failure_and_replan():
    """First plan has a failing step; replan produces a working step."""
    from jarvis_engine.agent.state_store import AgentStateStore

    # First plan: one step that fails
    fail_plan = _step_plan_json([
        {
            "step_index": 0,
            "tool_name": "mock_tool",
            "description": "failing step",
            "params": {"action": "fail"},
            "depends_on": [],
        }
    ])
    # Replan: one step that succeeds (different tool invocation)
    replan = _step_plan_json([
        {
            "step_index": 0,
            "tool_name": "mock_tool",
            "description": "recovery step",
            "params": {"action": "recover"},
            "depends_on": [],
        }
    ])

    db = _make_db()
    store = AgentStateStore(db)
    bus = _make_bus()
    gate = _make_gate(bus)

    # Registry: fail on first call, succeed on subsequent calls
    from jarvis_engine.agent.tool_registry import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    call_count = [0]

    async def _execute(**kwargs: Any) -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("first call fails")
        return "recovery success"

    spec = ToolSpec(
        name="mock_tool",
        description="conditional mock",
        parameters={"type": "object", "properties": {}},
        execute=_execute,
    )
    registry.register(spec)

    loop, planner, *_ = _build_full_loop(
        fail_plan, registry=registry, replan_json=replan, bus=bus, gate=gate
    )
    # Replace store with the one attached to loop (via executor)
    # We need to use the loop's own store since it was built inside _build_full_loop
    loop2, planner2, store2, bus2, gate2, registry2 = _build_full_loop(
        fail_plan, registry=registry, replan_json=replan
    )

    task = _make_task("replan-01")
    store2.checkpoint(task)
    steps, tokens = planner2.plan(task.goal)
    task.tokens_used += tokens
    task.status = "running"
    store2.checkpoint(task)

    result = asyncio.run(loop2.run_loop(task, steps))
    # After recovery, task should be done
    assert result.status == "done"


# ---------------------------------------------------------------------------
# Test 3: 3 consecutive same errors -> escalation
# ---------------------------------------------------------------------------


def test_escalation_after_3_same_errors():
    """Tool always fails with same error; task escalates to 'failed' after 3 attempts."""
    fail_plan = _step_plan_json([
        {
            "step_index": 0,
            "tool_name": "mock_tool",
            "description": "always fails",
            "params": {},
            "depends_on": [],
        }
    ])

    # Replan always returns same step (perpetual failure)
    same_replan = fail_plan

    db = _make_db()
    from jarvis_engine.agent.executor import StepExecutor
    from jarvis_engine.agent.planner import TaskPlanner
    from jarvis_engine.agent.reflection import ReflectionLoop
    from jarvis_engine.agent.state_store import AgentStateStore

    store = AgentStateStore(db)
    bus = _make_bus()
    gate = _make_gate(bus)
    registry = _make_registry_with_mock_tool(fail=True, error_msg="always the same error")

    gw = MagicMock()
    gw.complete.return_value = _FakeGatewayResponse(text=same_replan)

    planner = TaskPlanner(gw, registry)
    executor = StepExecutor(registry, gate, store, bus)
    reflection = ReflectionLoop(executor, planner, store, bus)

    task = _make_task("escalate-01")
    store.checkpoint(task)
    steps, tokens = planner.plan(task.goal)
    task.tokens_used += tokens
    task.status = "running"
    store.checkpoint(task)

    result = asyncio.run(reflection.run_loop(task, steps))

    assert result.status == "failed"
    assert result.last_error != ""
    # error_count should be >= 3 (3 consecutive same errors)
    assert result.error_count >= 3


# ---------------------------------------------------------------------------
# Test 4: Token budget enforcement
# ---------------------------------------------------------------------------


def test_token_budget_enforcement():
    """Task fails with 'Token budget exceeded' when planning tokens exceed budget."""
    # Plan that uses many tokens
    plan = _step_plan_json([
        {
            "step_index": 0,
            "tool_name": "mock_tool",
            "description": "step",
            "params": {},
            "depends_on": [],
        }
    ])

    db = _make_db()
    from jarvis_engine.agent.executor import StepExecutor
    from jarvis_engine.agent.planner import TaskPlanner
    from jarvis_engine.agent.reflection import ReflectionLoop
    from jarvis_engine.agent.state_store import AgentStateStore, AgentTask

    store = AgentStateStore(db)
    bus = _make_bus()
    gate = _make_gate(bus)
    registry = _make_registry_with_mock_tool()

    # Gateway returns 80 tokens per call (input=40, output=40)
    gw = MagicMock()
    gw.complete.return_value = _FakeGatewayResponse(text=plan, input_tokens=40, output_tokens=40)

    planner = TaskPlanner(gw, registry)
    executor = StepExecutor(registry, gate, store, bus)
    reflection = ReflectionLoop(executor, planner, store, bus)

    # Token budget = 50; planning alone costs 80 tokens
    task = AgentTask(task_id="budget-01", goal="budget test", token_budget=50)
    store.checkpoint(task)

    steps, tokens = planner.plan(task.goal)
    task.tokens_used += tokens  # = 80, already exceeds budget of 50
    task.status = "running"
    store.checkpoint(task)

    result = asyncio.run(reflection.run_loop(task, steps))

    # Loop should detect budget exceeded immediately
    assert result.status == "failed"
    assert "budget" in result.last_error.lower() or result.tokens_used >= task.token_budget


# ---------------------------------------------------------------------------
# Test 5: Approval gate blocks until approved
# ---------------------------------------------------------------------------


def test_approval_gate_blocks_and_resumes():
    """Destructive tool blocks at approval gate; approve() lets it continue.

    The test subscribes to progress events to detect the approval_needed event,
    then calls gate.approve() from within the same event loop to avoid
    cross-thread asyncio.Event complications.
    """
    db = _make_db()
    from jarvis_engine.agent.executor import StepExecutor
    from jarvis_engine.agent.planner import TaskPlanner
    from jarvis_engine.agent.reflection import ReflectionLoop
    from jarvis_engine.agent.state_store import AgentStateStore, AgentTask

    store = AgentStateStore(db)
    bus = _make_bus()
    gate = _make_gate(bus)
    registry = _make_destructive_registry()

    plan = _step_plan_json([
        {
            "step_index": 0,
            "tool_name": "mock_tool",
            "description": "destructive step",
            "params": {},
            "depends_on": [],
        }
    ])

    gw = MagicMock()
    gw.complete.return_value = _FakeGatewayResponse(text=plan)

    planner = TaskPlanner(gw, registry)
    executor = StepExecutor(registry, gate, store, bus)
    reflection = ReflectionLoop(executor, planner, store, bus)

    task = AgentTask(task_id="approval-01", goal="approval test", token_budget=10000)
    store.checkpoint(task)

    steps, tokens = planner.plan(task.goal)
    task.tokens_used += tokens
    task.status = "running"
    store.checkpoint(task)

    async def _run_with_approval():
        """Run loop and approve as soon as the approval_needed event arrives."""
        q = bus.subscribe()
        loop_task = asyncio.create_task(reflection.run_loop(task, steps))

        # Wait for approval_needed event, then approve
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=5.0)
                if event.get("type") == "approval_needed":
                    gate.approve(task.task_id)
                    break
            except asyncio.TimeoutError:
                break

        return await loop_task

    result = asyncio.run(_run_with_approval())
    assert result.status == "done"


# ---------------------------------------------------------------------------
# Test 6: Full handler integration (AgentRunHandler -> loop -> done)
# ---------------------------------------------------------------------------


def test_agent_run_handler_full_integration():
    """AgentRunHandler.handle() submits task; background loop completes it."""
    from jarvis_engine.handlers.agent_handlers import AgentRunHandler
    from jarvis_engine.commands.agent_commands import AgentRunCommand

    db = _make_db()
    from jarvis_engine.agent.state_store import AgentStateStore

    store = AgentStateStore(db)
    bus = _make_bus()
    gate = _make_gate(bus)
    registry = _make_registry_with_mock_tool()

    gw = _make_gateway(_make_two_step_plan())

    from pathlib import Path

    handler = AgentRunHandler(
        Path("/fake"),
        gateway=gw,
        registry=registry,
        store=store,
        gate=gate,
        bus=bus,
    )

    cmd = AgentRunCommand(goal="integration test", task_id="int-01")
    result = handler.handle(cmd)

    assert result.return_code == 0
    assert result.task_id == "int-01"
    assert result.status == "pending"

    # Wait for background thread to complete
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        task = store.load("int-01")
        if task and task.status in ("done", "failed"):
            break
        time.sleep(0.1)

    task = store.load("int-01")
    assert task is not None
    assert task.status == "done", f"Expected 'done', got '{task.status}': {task.last_error}"
