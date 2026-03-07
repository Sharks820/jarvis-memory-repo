"""Tests for engine/src/jarvis_engine/learning/temporal.py — temporal metadata."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from conftest import make_test_db
from jarvis_engine._compat import UTC
from jarvis_engine.learning.temporal import (
    _extract_date,
    classify_temporal,
    flag_expired_facts,
    migrate_temporal_metadata,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with a minimal kg_nodes table."""
    db = make_test_db(row_factory=False)
    db.execute(
        "CREATE TABLE kg_nodes (id TEXT PRIMARY KEY, label TEXT)"
    )
    db.commit()
    return db


def _make_db_with_temporal() -> sqlite3.Connection:
    """Create an in-memory DB with kg_nodes including temporal columns."""
    db = make_test_db(row_factory=False)
    db.execute(
        """CREATE TABLE kg_nodes (
            id TEXT PRIMARY KEY,
            label TEXT,
            temporal_type TEXT DEFAULT 'unknown',
            expires_at TEXT DEFAULT NULL
        )"""
    )
    db.commit()
    return db


# ── _extract_date() ─────────────────────────────────────────────────────────

class TestExtractDate:
    def test_expires_keyword(self) -> None:
        assert _extract_date("expires 2026-03-15") == "2026-03-15T00:00:00Z"

    def test_expire_keyword(self) -> None:
        assert _extract_date("expire 2026-04-01") == "2026-04-01T00:00:00Z"

    def test_due_keyword(self) -> None:
        assert _extract_date("due 2026-06-30") == "2026-06-30T00:00:00Z"

    def test_until_keyword(self) -> None:
        assert _extract_date("valid until 2026-12-31") == "2026-12-31T00:00:00Z"

    def test_case_insensitive(self) -> None:
        assert _extract_date("EXPIRES 2026-01-01") == "2026-01-01T00:00:00Z"

    def test_fallback_bare_iso_date(self) -> None:
        assert _extract_date("appointment on 2026-07-04") == "2026-07-04T00:00:00Z"

    def test_no_date_returns_none(self) -> None:
        assert _extract_date("no dates here") is None

    def test_empty_string(self) -> None:
        assert _extract_date("") is None


# ── classify_temporal() ─────────────────────────────────────────────────────

class TestClassifyTemporal:
    # -- permanent prefixes --

    def test_family_member_permanent(self) -> None:
        t, exp = classify_temporal("family.member.sister", "Sarah is my sister")
        assert t == "permanent"
        assert exp is None

    def test_preference_permanent(self) -> None:
        t, exp = classify_temporal("preference.food", "Likes sushi")
        assert t == "permanent"
        assert exp is None

    def test_ops_location_permanent(self) -> None:
        t, exp = classify_temporal("ops.location.home", "123 Main St")
        assert t == "permanent"
        assert exp is None

    def test_finance_income_permanent(self) -> None:
        t, exp = classify_temporal("finance.income.salary", "Annual salary")
        assert t == "permanent"
        assert exp is None

    # -- time-sensitive prefixes --

    def test_schedule_with_date(self) -> None:
        t, exp = classify_temporal("ops.schedule.meeting", "meeting due 2026-05-01")
        assert t == "time_sensitive"
        assert exp == "2026-05-01T00:00:00Z"

    def test_schedule_without_date_defaults_30_days(self) -> None:
        t, exp = classify_temporal("ops.schedule.meeting", "weekly standup")
        assert t == "time_sensitive"
        assert exp is not None
        # Should be roughly 30 days from now
        parsed = datetime.strptime(exp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        delta = parsed - datetime.now(UTC)
        assert 29 <= delta.days <= 31

    def test_medication_time_sensitive(self) -> None:
        t, exp = classify_temporal("health.medication.aspirin", "expires 2026-08-01")
        assert t == "time_sensitive"
        assert exp == "2026-08-01T00:00:00Z"

    # -- unknown node_id but label has date --

    def test_unknown_prefix_with_date_in_label(self) -> None:
        t, exp = classify_temporal("random.thing", "due 2026-09-15 important")
        assert t == "time_sensitive"
        assert exp == "2026-09-15T00:00:00Z"

    def test_unknown_prefix_bare_iso_date_in_label(self) -> None:
        t, exp = classify_temporal("misc.note", "Created on 2026-02-14")
        assert t == "time_sensitive"
        assert exp == "2026-02-14T00:00:00Z"

    # -- fully unknown --

    def test_fully_unknown(self) -> None:
        t, exp = classify_temporal("random.thing", "no temporal info")
        assert t == "unknown"
        assert exp is None

    # -- case insensitivity on node_id --

    def test_node_id_case_insensitive(self) -> None:
        t, exp = classify_temporal("Family.Member.Brother", "John")
        assert t == "permanent"


# ── migrate_temporal_metadata() ─────────────────────────────────────────────

class TestMigrate:
    def test_adds_columns(self) -> None:
        db = _make_db()
        lock = threading.Lock()
        migrate_temporal_metadata(db, lock)

        cols = {row[1] for row in db.execute("PRAGMA table_info(kg_nodes)").fetchall()}
        assert "temporal_type" in cols
        assert "expires_at" in cols

    def test_idempotent(self) -> None:
        """Running migration twice should not raise."""
        db = _make_db()
        lock = threading.Lock()
        migrate_temporal_metadata(db, lock)
        migrate_temporal_metadata(db, lock)

        cols = {row[1] for row in db.execute("PRAGMA table_info(kg_nodes)").fetchall()}
        assert "temporal_type" in cols

    def test_creates_indexes(self) -> None:
        db = _make_db()
        lock = threading.Lock()
        migrate_temporal_metadata(db, lock)

        indexes = {
            row[1]
            for row in db.execute(
                "SELECT * FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_kg_nodes_temporal_type" in indexes
        assert "idx_kg_nodes_expires_at" in indexes

    def test_default_values(self) -> None:
        db = _make_db()
        lock = threading.Lock()
        migrate_temporal_metadata(db, lock)

        db.execute("INSERT INTO kg_nodes (id, label) VALUES ('test.1', 'hello')")
        db.commit()
        row = db.execute(
            "SELECT temporal_type, expires_at FROM kg_nodes WHERE id='test.1'"
        ).fetchone()
        assert row[0] == "unknown"
        assert row[1] is None


# ── flag_expired_facts() ───────────────────────────────────────────────────

class TestFlagExpired:
    def test_flags_expired(self) -> None:
        db = _make_db_with_temporal()
        # Insert one expired and one not-yet-expired
        past = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        future = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute(
            "INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
            ("n1", "old fact", "time_sensitive", past),
        )
        db.execute(
            "INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
            ("n2", "future fact", "time_sensitive", future),
        )
        db.commit()

        kg = MagicMock()
        kg.db = db
        kg.write_lock = threading.Lock()

        count = flag_expired_facts(kg)
        assert count == 1

        row = db.execute(
            "SELECT temporal_type FROM kg_nodes WHERE id='n1'"
        ).fetchone()
        assert row[0] == "expired"

        row2 = db.execute(
            "SELECT temporal_type FROM kg_nodes WHERE id='n2'"
        ).fetchone()
        assert row2[0] == "time_sensitive"

    def test_already_expired_not_double_counted(self) -> None:
        db = _make_db_with_temporal()
        past = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute(
            "INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
            ("n1", "already expired", "expired", past),
        )
        db.commit()

        kg = MagicMock()
        kg.db = db
        kg.write_lock = threading.Lock()

        count = flag_expired_facts(kg)
        assert count == 0

    def test_null_expires_at_ignored(self) -> None:
        db = _make_db_with_temporal()
        db.execute(
            "INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
            ("n1", "permanent fact", "permanent", None),
        )
        db.commit()

        kg = MagicMock()
        kg.db = db
        kg.write_lock = threading.Lock()

        count = flag_expired_facts(kg)
        assert count == 0

    def test_flags_multiple(self) -> None:
        db = _make_db_with_temporal()
        past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(5):
            db.execute(
                "INSERT INTO kg_nodes VALUES (?, ?, ?, ?)",
                (f"n{i}", f"fact {i}", "time_sensitive", past),
            )
        db.commit()

        kg = MagicMock()
        kg.db = db
        kg.write_lock = threading.Lock()

        count = flag_expired_facts(kg)
        assert count == 5
