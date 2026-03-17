"""Tests for AgentStateStore -- crash-safe task checkpointing."""
from __future__ import annotations

import sqlite3

import pytest

from jarvis_engine.agent.state_store import AgentStateStore, AgentTask


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def store(db: sqlite3.Connection) -> AgentStateStore:
    return AgentStateStore(db)


def _make_task(task_id: str = "task-001", status: str = "running") -> AgentTask:
    return AgentTask(
        task_id=task_id,
        goal="Build a Unity scene",
        status=status,
        plan_json='["step1", "step2", "step3"]',
        step_index=0,
        checkpoint_json="{}",
        token_budget=4000,
        tokens_used=0,
        error_count=0,
        last_error="",
        approval_needed=False,
    )


# ---------------------------------------------------------------------------
# Schema / basic round-trip
# ---------------------------------------------------------------------------


def test_checkpoint_and_load_roundtrip(store: AgentStateStore) -> None:
    task = _make_task()
    store.checkpoint(task)

    loaded = store.load("task-001")
    assert loaded is not None
    assert loaded.task_id == "task-001"
    assert loaded.goal == "Build a Unity scene"
    assert loaded.status == "running"


def test_load_returns_none_for_unknown(store: AgentStateStore) -> None:
    result = store.load("does-not-exist")
    assert result is None


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


def test_crash_recovery_step_index(store: AgentStateStore) -> None:
    """Checkpoint at step 3, reload, step_index must equal 3."""
    task = _make_task()
    task.step_index = 3
    task.checkpoint_json = '{"last_output": "foo"}'
    store.checkpoint(task)

    # Simulate crash: recreate store on same db
    store2 = AgentStateStore(store._db)
    recovered = store2.load("task-001")
    assert recovered is not None
    assert recovered.step_index == 3
    assert recovered.checkpoint_json == '{"last_output": "foo"}'


def test_checkpoint_overwrites_on_second_call(store: AgentStateStore) -> None:
    task = _make_task()
    store.checkpoint(task)

    task.step_index = 5
    task.status = "completed"
    store.checkpoint(task)

    loaded = store.load("task-001")
    assert loaded is not None
    assert loaded.step_index == 5
    assert loaded.status == "completed"


# ---------------------------------------------------------------------------
# list_by_status
# ---------------------------------------------------------------------------


def test_list_by_status_filters_correctly(store: AgentStateStore) -> None:
    store.checkpoint(_make_task("t1", "running"))
    store.checkpoint(_make_task("t2", "running"))
    store.checkpoint(_make_task("t3", "completed"))

    running = store.list_by_status("running")
    assert len(running) == 2
    assert all(t.status == "running" for t in running)


def test_list_by_status_empty(store: AgentStateStore) -> None:
    result = store.list_by_status("pending")
    assert result == []


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_existing_task(store: AgentStateStore) -> None:
    store.checkpoint(_make_task())
    deleted = store.delete("task-001")
    assert deleted is True
    assert store.load("task-001") is None


def test_delete_nonexistent_task(store: AgentStateStore) -> None:
    deleted = store.delete("ghost-task")
    assert deleted is False


# ---------------------------------------------------------------------------
# Field types preserved
# ---------------------------------------------------------------------------


def test_all_fields_round_trip(store: AgentStateStore) -> None:
    task = AgentTask(
        task_id="full-task",
        goal="Full field test",
        status="paused",
        plan_json='["a", "b"]',
        step_index=7,
        checkpoint_json='{"x": 1}',
        token_budget=8000,
        tokens_used=1234,
        error_count=2,
        last_error="something broke",
        approval_needed=True,
    )
    store.checkpoint(task)
    loaded = store.load("full-task")
    assert loaded is not None
    assert loaded.step_index == 7
    assert loaded.token_budget == 8000
    assert loaded.tokens_used == 1234
    assert loaded.error_count == 2
    assert loaded.last_error == "something broke"
    assert loaded.approval_needed is True
