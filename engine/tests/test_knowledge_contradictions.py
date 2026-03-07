"""Tests for jarvis_engine.knowledge.contradictions -- ContradictionManager.

Uses a real in-memory SQLite database with the kg_nodes and kg_contradictions
schema to test list_pending, list_all, and resolve operations.

Covers:
- list_pending: empty, ordering, limit
- list_all: no filter, status filter
- resolve: accept_new, keep_old, merge
- Error paths: invalid resolution, empty merge_value, not found, already resolved
- History tracking on resolution
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from jarvis_engine.knowledge.contradictions import ContradictionManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE kg_nodes (
            node_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            node_type TEXT NOT NULL DEFAULT 'fact',
            confidence REAL NOT NULL DEFAULT 0.5,
            locked INTEGER NOT NULL DEFAULT 0,
            locked_at TEXT DEFAULT NULL,
            locked_by TEXT DEFAULT NULL,
            sources TEXT NOT NULL DEFAULT '[]',
            history TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE kg_contradictions (
            contradiction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            existing_value TEXT NOT NULL,
            incoming_value TEXT NOT NULL,
            existing_confidence REAL NOT NULL,
            incoming_confidence REAL NOT NULL,
            incoming_source TEXT DEFAULT NULL,
            record_id TEXT DEFAULT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_at TEXT DEFAULT NULL,
            resolution TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (node_id) REFERENCES kg_nodes(node_id)
        );
    """)
    return db


def _insert_node(
    db: sqlite3.Connection,
    node_id: str,
    label: str,
    confidence: float = 0.7,
    locked: int = 1,
) -> None:
    db.execute(
        "INSERT INTO kg_nodes (node_id, label, confidence, locked) VALUES (?, ?, ?, ?)",
        (node_id, label, confidence, locked),
    )
    db.commit()


def _insert_contradiction(
    db: sqlite3.Connection,
    node_id: str,
    existing_value: str,
    incoming_value: str,
    existing_confidence: float = 0.7,
    incoming_confidence: float = 0.8,
    status: str = "pending",
) -> int:
    cur = db.execute(
        """INSERT INTO kg_contradictions
           (node_id, existing_value, incoming_value,
            existing_confidence, incoming_confidence, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            node_id,
            existing_value,
            incoming_value,
            existing_confidence,
            incoming_confidence,
            status,
        ),
    )
    db.commit()
    return cur.lastrowid


@pytest.fixture
def db():
    return _create_test_db()


@pytest.fixture
def mgr(db):
    write_lock = threading.Lock()
    db_lock = threading.Lock()
    return ContradictionManager(db, write_lock, db_lock)


# ---------------------------------------------------------------------------
# list_pending
# ---------------------------------------------------------------------------


class TestListPending:
    def test_empty_returns_empty(self, mgr, db):
        assert mgr.list_pending() == []

    def test_returns_only_pending(self, mgr, db):
        _insert_node(db, "n1", "old_val")
        _insert_contradiction(db, "n1", "old", "new", status="pending")
        _insert_contradiction(db, "n1", "old", "new2", status="resolved")
        result = mgr.list_pending()
        assert len(result) == 1
        assert result[0]["status"] == "pending"

    def test_limit_respected(self, mgr, db):
        _insert_node(db, "n1", "val")
        for i in range(5):
            _insert_contradiction(db, "n1", "old", f"new{i}")
        result = mgr.list_pending(limit=3)
        assert len(result) == 3

    def test_returns_multiple_pending(self, mgr, db):
        """Multiple pending contradictions are all returned."""
        _insert_node(db, "n1", "val")
        _insert_contradiction(db, "n1", "old", "first")
        _insert_contradiction(db, "n1", "old", "second")
        result = mgr.list_pending()
        assert len(result) == 2
        # All results are pending
        assert all(r["status"] == "pending" for r in result)


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


class TestListAll:
    def test_no_filter_returns_all(self, mgr, db):
        _insert_node(db, "n1", "val")
        _insert_contradiction(db, "n1", "old", "new1", status="pending")
        _insert_contradiction(db, "n1", "old", "new2", status="resolved")
        result = mgr.list_all()
        assert len(result) == 2

    def test_filter_by_status(self, mgr, db):
        _insert_node(db, "n1", "val")
        _insert_contradiction(db, "n1", "old", "new1", status="pending")
        _insert_contradiction(db, "n1", "old", "new2", status="resolved")
        result = mgr.list_all(status="resolved")
        assert len(result) == 1
        assert result[0]["status"] == "resolved"

    def test_limit_applied(self, mgr, db):
        _insert_node(db, "n1", "val")
        for i in range(10):
            _insert_contradiction(db, "n1", "old", f"new{i}")
        result = mgr.list_all(limit=4)
        assert len(result) == 4


# ---------------------------------------------------------------------------
# resolve - validation errors
# ---------------------------------------------------------------------------


class TestResolveValidation:
    def test_invalid_resolution_returns_failure(self, mgr, db):
        result = mgr.resolve(1, "bad_option")
        assert result["success"] is False
        assert "Invalid resolution" in result["message"]

    def test_merge_requires_merge_value(self, mgr, db):
        result = mgr.resolve(1, "merge", merge_value="")
        assert result["success"] is False
        assert "merge_value is required" in result["message"]

    def test_merge_whitespace_only_rejected(self, mgr, db):
        result = mgr.resolve(1, "merge", merge_value="   ")
        assert result["success"] is False

    def test_not_found_returns_failure(self, mgr, db):
        result = mgr.resolve(999, "accept_new")
        assert result["success"] is False
        assert "not found" in result["message"]

    def test_already_resolved_returns_failure(self, mgr, db):
        _insert_node(db, "n1", "old_val")
        cid = _insert_contradiction(db, "n1", "old_val", "new_val", status="resolved")
        result = mgr.resolve(cid, "accept_new")
        assert result["success"] is False
        assert "already resolved" in result["message"]


# ---------------------------------------------------------------------------
# resolve - accept_new
# ---------------------------------------------------------------------------


class TestResolveAcceptNew:
    def test_accept_new_updates_node_label(self, mgr, db):
        _insert_node(db, "n1", "old_label", confidence=0.7, locked=1)
        cid = _insert_contradiction(
            db, "n1", "old_label", "new_label", incoming_confidence=0.9
        )
        result = mgr.resolve(cid, "accept_new")
        assert result["success"] is True
        assert result["node_id"] == "n1"
        # Verify node updated
        node = db.execute(
            "SELECT label, locked FROM kg_nodes WHERE node_id = ?", ("n1",)
        ).fetchone()
        assert node["label"] == "new_label"
        assert node["locked"] == 0  # unlocked

    def test_accept_new_marks_resolved(self, mgr, db):
        _insert_node(db, "n1", "old")
        cid = _insert_contradiction(db, "n1", "old", "new")
        mgr.resolve(cid, "accept_new")
        row = db.execute(
            "SELECT status, resolution FROM kg_contradictions WHERE contradiction_id = ?",
            (cid,),
        ).fetchone()
        assert row["status"] == "resolved"
        assert row["resolution"] == "accept_new"

    def test_accept_new_updates_history(self, mgr, db):
        _insert_node(db, "n1", "old")
        cid = _insert_contradiction(db, "n1", "old", "new")
        mgr.resolve(cid, "accept_new")
        node = db.execute(
            "SELECT history FROM kg_nodes WHERE node_id = ?", ("n1",)
        ).fetchone()
        history = json.loads(node["history"])
        assert len(history) == 1
        assert history[0]["action"] == "accept_new"
        assert history[0]["new_value"] == "new"


# ---------------------------------------------------------------------------
# resolve - keep_old
# ---------------------------------------------------------------------------


class TestResolveKeepOld:
    def test_keep_old_does_not_change_node(self, mgr, db):
        _insert_node(db, "n1", "original")
        cid = _insert_contradiction(db, "n1", "original", "incoming")
        mgr.resolve(cid, "keep_old")
        node = db.execute(
            "SELECT label FROM kg_nodes WHERE node_id = ?", ("n1",)
        ).fetchone()
        assert node["label"] == "original"

    def test_keep_old_marks_resolved(self, mgr, db):
        _insert_node(db, "n1", "val")
        cid = _insert_contradiction(db, "n1", "val", "other")
        result = mgr.resolve(cid, "keep_old")
        assert result["success"] is True
        row = db.execute(
            "SELECT status, resolution FROM kg_contradictions WHERE contradiction_id = ?",
            (cid,),
        ).fetchone()
        assert row["resolution"] == "keep_old"

    def test_keep_old_appends_history(self, mgr, db):
        _insert_node(db, "n1", "val")
        cid = _insert_contradiction(db, "n1", "val", "alt")
        mgr.resolve(cid, "keep_old")
        node = db.execute(
            "SELECT history FROM kg_nodes WHERE node_id = ?", ("n1",)
        ).fetchone()
        history = json.loads(node["history"])
        assert history[0]["action"] == "keep_old"


# ---------------------------------------------------------------------------
# resolve - merge
# ---------------------------------------------------------------------------


class TestResolveMerge:
    def test_merge_sets_custom_value(self, mgr, db):
        _insert_node(db, "n1", "old")
        cid = _insert_contradiction(db, "n1", "old", "new")
        result = mgr.resolve(cid, "merge", merge_value="merged_val")
        assert result["success"] is True
        node = db.execute(
            "SELECT label FROM kg_nodes WHERE node_id = ?", ("n1",)
        ).fetchone()
        assert node["label"] == "merged_val"

    def test_merge_appends_history(self, mgr, db):
        _insert_node(db, "n1", "old")
        cid = _insert_contradiction(db, "n1", "old", "new")
        mgr.resolve(cid, "merge", merge_value="combo")
        node = db.execute(
            "SELECT history FROM kg_nodes WHERE node_id = ?", ("n1",)
        ).fetchone()
        history = json.loads(node["history"])
        assert history[0]["action"] == "merge"
        assert history[0]["new_value"] == "combo"
