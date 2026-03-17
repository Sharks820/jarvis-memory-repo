"""AgentStateStore -- crash-safe SQLite checkpointing for agent tasks.

Accepts an existing sqlite3.Connection (shared with MemoryEngine) and never
opens its own connection.  All writes call self._db.commit() so state is
durable even if the process exits immediately after.

Schema:
    agent_tasks(task_id TEXT PK, goal TEXT, status TEXT, plan_json TEXT,
                step_index INTEGER, checkpoint_json TEXT, token_budget INTEGER,
                tokens_used INTEGER, error_count INTEGER, last_error TEXT,
                approval_needed INTEGER, created_at TEXT, updated_at TEXT)
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jarvis_engine._shared import now_iso

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id          TEXT    PRIMARY KEY,
    goal             TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL DEFAULT 'pending',
    plan_json        TEXT    NOT NULL DEFAULT '[]',
    step_index       INTEGER NOT NULL DEFAULT 0,
    checkpoint_json  TEXT    NOT NULL DEFAULT '{}',
    token_budget     INTEGER NOT NULL DEFAULT 4000,
    tokens_used      INTEGER NOT NULL DEFAULT 0,
    error_count      INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT    NOT NULL DEFAULT '',
    approval_needed  INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL DEFAULT '',
    updated_at       TEXT    NOT NULL DEFAULT ''
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status
    ON agent_tasks (status);
"""

_UPSERT = """
INSERT OR REPLACE INTO agent_tasks
    (task_id, goal, status, plan_json, step_index, checkpoint_json,
     token_budget, tokens_used, error_count, last_error, approval_needed,
     created_at, updated_at)
VALUES
    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
        (SELECT created_at FROM agent_tasks WHERE task_id = ?), ?),
     ?)
"""

_SELECT_ONE = "SELECT * FROM agent_tasks WHERE task_id = ?"
_SELECT_STATUS = "SELECT * FROM agent_tasks WHERE status = ?"
_DELETE = "DELETE FROM agent_tasks WHERE task_id = ?"


@dataclass
class AgentTask:
    """Mutable snapshot of an in-flight agent task."""

    task_id: str
    goal: str = ""
    status: str = "pending"
    plan_json: str = "[]"
    step_index: int = 0
    checkpoint_json: str = "{}"
    token_budget: int = 4000
    tokens_used: int = 0
    error_count: int = 0
    last_error: str = ""
    approval_needed: bool = False
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


class AgentStateStore:
    """Persist and restore AgentTask state using an existing SQLite connection."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        self._db.execute(_CREATE_TABLE)
        self._db.execute(_CREATE_INDEX)
        self._db.commit()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> AgentTask:
        return AgentTask(
            task_id=row["task_id"],
            goal=row["goal"],
            status=row["status"],
            plan_json=row["plan_json"],
            step_index=row["step_index"],
            checkpoint_json=row["checkpoint_json"],
            token_budget=row["token_budget"],
            tokens_used=row["tokens_used"],
            error_count=row["error_count"],
            last_error=row["last_error"],
            approval_needed=bool(row["approval_needed"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def checkpoint(self, task: AgentTask) -> None:
        """Insert or replace *task* in the agent_tasks table."""
        now = now_iso()
        task.updated_at = now
        self._db.execute(
            _UPSERT,
            (
                task.task_id,
                task.goal,
                task.status,
                task.plan_json,
                task.step_index,
                task.checkpoint_json,
                task.token_budget,
                task.tokens_used,
                task.error_count,
                task.last_error,
                int(task.approval_needed),
                # COALESCE sub-select placeholder (task_id again)
                task.task_id,
                # fallback created_at when row is new
                task.created_at,
                # updated_at
                now,
            ),
        )
        self._db.commit()

    def load(self, task_id: str) -> AgentTask | None:
        """Return AgentTask for *task_id*, or None if not found."""
        row = self._db.execute(_SELECT_ONE, (task_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def list_by_status(self, status: str) -> list[AgentTask]:
        """Return all tasks with *status*."""
        rows = self._db.execute(_SELECT_STATUS, (status,)).fetchall()
        return [self._row_to_task(r) for r in rows]

    def delete(self, task_id: str) -> bool:
        """Delete task by *task_id*. Returns True if a row was removed."""
        cursor = self._db.execute(_DELETE, (task_id,))
        self._db.commit()
        return cursor.rowcount > 0
