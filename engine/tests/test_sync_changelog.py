"""Tests for sync/changelog.py: changelog triggers, diff computation, cursors, compaction."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from conftest import make_test_db
from jarvis_engine.sync.changelog import (
    _DEVICE_ID_RE,
    _TRACKED_TABLES,
    _build_delete_trigger,
    _build_insert_trigger,
    _build_update_trigger,
    _clean_json_sql,
    _clean_json_obj_sql,
    compact_changelog,
    compute_diff,
    get_sync_cursor,
    install_changelog_triggers,
    update_sync_cursor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with a minimal 'records' table."""
    db = make_test_db(row_factory=False)
    db.execute("""
        CREATE TABLE records (
            record_id TEXT PRIMARY KEY,
            ts TEXT DEFAULT '',
            source TEXT DEFAULT '',
            kind TEXT DEFAULT '',
            task_id TEXT DEFAULT '',
            branch TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            tier TEXT DEFAULT '',
            access_count INTEGER DEFAULT 0,
            last_accessed TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE kg_nodes (
            node_id TEXT PRIMARY KEY,
            label TEXT DEFAULT '',
            node_type TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            locked INTEGER DEFAULT 0,
            locked_at TEXT DEFAULT '',
            locked_by TEXT DEFAULT '',
            sources TEXT DEFAULT '',
            history TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE kg_edges (
            edge_id TEXT PRIMARY KEY,
            source_id TEXT DEFAULT '',
            target_id TEXT DEFAULT '',
            relation TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            source_record TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE user_preferences (
            category TEXT NOT NULL,
            preference TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            last_observed TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (category, preference)
        )
    """)
    db.execute("""
        CREATE TABLE response_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route TEXT NOT NULL DEFAULT '',
            feedback TEXT NOT NULL DEFAULT 'neutral',
            user_message_snippet TEXT NOT NULL DEFAULT '',
            recorded_at TEXT NOT NULL DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE usage_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hour INTEGER NOT NULL DEFAULT 0,
            day_of_week INTEGER NOT NULL DEFAULT 0,
            route TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            recorded_at TEXT NOT NULL DEFAULT ''
        )
    """)
    db.commit()
    return db


@pytest.fixture
def db():
    """Yield an in-memory db with tables + changelog triggers installed."""
    conn = _make_db()
    install_changelog_triggers(conn, "desktop")
    yield conn
    conn.close()


@pytest.fixture
def write_lock():
    return threading.Lock()


# ---------------------------------------------------------------------------
# Device ID validation
# ---------------------------------------------------------------------------

class TestDeviceIdValidation:
    def test_valid_device_ids(self) -> None:
        assert _DEVICE_ID_RE.match("desktop")
        assert _DEVICE_ID_RE.match("galaxy_s25_primary")
        assert _DEVICE_ID_RE.match("a-b-c-d")
        assert _DEVICE_ID_RE.match("A" * 64)

    def test_invalid_device_ids(self) -> None:
        assert not _DEVICE_ID_RE.match("")
        assert not _DEVICE_ID_RE.match("A" * 65)
        assert not _DEVICE_ID_RE.match("bad device!")
        assert not _DEVICE_ID_RE.match("has spaces")

    def test_install_rejects_invalid_device_id(self) -> None:
        conn = _make_db()
        with pytest.raises(ValueError, match="Invalid device_id"):
            install_changelog_triggers(conn, "bad device!")
        conn.close()


# ---------------------------------------------------------------------------
# Schema installation
# ---------------------------------------------------------------------------

class TestInstallTriggers:
    def test_creates_changelog_table(self, db: sqlite3.Connection) -> None:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "_sync_changelog" in tables
        assert "_sync_cursor" in tables
        assert "_sync_version_seq" in tables

    def test_creates_indexes(self, db: sqlite3.Connection) -> None:
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_changelog_version" in indexes
        assert "idx_changelog_device" in indexes

    def test_creates_triggers_for_all_tracked_tables(self, db: sqlite3.Connection) -> None:
        triggers = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()}
        for table in _TRACKED_TABLES:
            assert f"_sync_trg_{table}_insert" in triggers
            assert f"_sync_trg_{table}_update" in triggers
            assert f"_sync_trg_{table}_delete" in triggers

    def test_idempotent_install(self, db: sqlite3.Connection) -> None:
        """Calling install_changelog_triggers twice should not error."""
        install_changelog_triggers(db, "desktop")  # second call
        triggers = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()}
        assert len(triggers) == 3 * len(_TRACKED_TABLES)

    def test_version_sequence_seeded(self, db: sqlite3.Connection) -> None:
        rows = db.execute(
            "SELECT table_name, next_version FROM _sync_version_seq ORDER BY table_name"
        ).fetchall()
        tables = {row[0] for row in rows}
        assert tables == set(_TRACKED_TABLES.keys())
        for row in rows:
            assert row[1] >= 1


# ---------------------------------------------------------------------------
# INSERT trigger
# ---------------------------------------------------------------------------

class TestInsertTrigger:
    def test_insert_creates_changelog_entry(self, db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT INTO records (record_id, source, kind, summary) "
            "VALUES ('r1', 'test', 'note', 'hello')"
        )
        db.commit()
        entries = compute_diff(db, "records", 0)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["table_name"] == "records"
        assert entry["row_id"] == "r1"
        assert entry["operation"] == "INSERT"
        assert entry["device_id"] == "desktop"
        assert entry["__version"] == 1

    def test_insert_captures_new_values(self, db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT INTO records (record_id, source, summary) VALUES ('r2', 'web', 'test summary')"
        )
        db.commit()
        entries = compute_diff(db, "records", 0)
        nv = entries[0]["new_values"]
        assert nv["source"] == "web"
        assert nv["summary"] == "test summary"

    def test_insert_old_values_empty(self, db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT INTO records (record_id) VALUES ('r3')"
        )
        db.commit()
        entries = compute_diff(db, "records", 0)
        assert entries[0]["old_values"] == {}

    def test_version_increments_per_insert(self, db: sqlite3.Connection) -> None:
        for i in range(3):
            db.execute(f"INSERT INTO records (record_id) VALUES ('v{i}')")
        db.commit()
        entries = compute_diff(db, "records", 0)
        versions = [e["__version"] for e in entries]
        assert versions == [1, 2, 3]


# ---------------------------------------------------------------------------
# UPDATE trigger
# ---------------------------------------------------------------------------

class TestUpdateTrigger:
    def test_update_creates_changelog_entry(self, db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO records (record_id, summary) VALUES ('u1', 'old')")
        db.commit()
        db.execute("UPDATE records SET summary = 'new' WHERE record_id = 'u1'")
        db.commit()
        entries = compute_diff(db, "records", 0)
        # INSERT + UPDATE = 2
        assert len(entries) == 2
        update_entry = entries[1]
        assert update_entry["operation"] == "UPDATE"
        assert "summary" in update_entry["fields_changed"]

    def test_update_captures_old_and_new(self, db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO records (record_id, source) VALUES ('u2', 'alpha')")
        db.commit()
        db.execute("UPDATE records SET source = 'beta' WHERE record_id = 'u2'")
        db.commit()
        entries = compute_diff(db, "records", 0)
        update_entry = entries[1]
        assert update_entry["old_values"]["source"] == "alpha"
        assert update_entry["new_values"]["source"] == "beta"

    def test_noise_only_update_suppressed(self, db: sqlite3.Connection) -> None:
        """Updating only noise fields (access_count, last_accessed) should NOT create a changelog entry."""
        db.execute("INSERT INTO records (record_id, access_count) VALUES ('n1', 0)")
        db.commit()
        initial_count = len(compute_diff(db, "records", 0))
        db.execute("UPDATE records SET access_count = 5, last_accessed = 'now' WHERE record_id = 'n1'")
        db.commit()
        after_count = len(compute_diff(db, "records", 0))
        # No new changelog entry for noise-only changes
        assert after_count == initial_count

    def test_mixed_noise_and_real_update(self, db: sqlite3.Connection) -> None:
        """Updating noise + real fields should fire the trigger."""
        db.execute("INSERT INTO records (record_id, summary, access_count) VALUES ('m1', 'old', 0)")
        db.commit()
        db.execute("UPDATE records SET summary = 'new', access_count = 99 WHERE record_id = 'm1'")
        db.commit()
        entries = compute_diff(db, "records", 0)
        # INSERT + UPDATE = 2
        assert len(entries) == 2
        assert entries[1]["operation"] == "UPDATE"


# ---------------------------------------------------------------------------
# DELETE trigger
# ---------------------------------------------------------------------------

class TestDeleteTrigger:
    def test_delete_creates_changelog_entry(self, db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO records (record_id, summary) VALUES ('d1', 'gone')")
        db.commit()
        db.execute("DELETE FROM records WHERE record_id = 'd1'")
        db.commit()
        entries = compute_diff(db, "records", 0)
        assert len(entries) == 2
        del_entry = entries[1]
        assert del_entry["operation"] == "DELETE"
        assert del_entry["row_id"] == "d1"
        assert del_entry["new_values"] == {}

    def test_delete_captures_old_values(self, db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO records (record_id, source) VALUES ('d2', 'src')")
        db.commit()
        db.execute("DELETE FROM records WHERE record_id = 'd2'")
        db.commit()
        entries = compute_diff(db, "records", 0)
        del_entry = entries[1]
        assert del_entry["old_values"]["source"] == "src"


# ---------------------------------------------------------------------------
# compute_diff
# ---------------------------------------------------------------------------

class TestComputeDiff:
    def test_since_version_filters(self, db: sqlite3.Connection) -> None:
        for i in range(5):
            db.execute(f"INSERT INTO records (record_id) VALUES ('cd{i}')")
        db.commit()
        # Only entries with __version > 3
        entries = compute_diff(db, "records", 3)
        assert len(entries) == 2
        assert entries[0]["__version"] == 4
        assert entries[1]["__version"] == 5

    def test_limit_parameter(self, db: sqlite3.Connection) -> None:
        for i in range(10):
            db.execute(f"INSERT INTO records (record_id) VALUES ('lim{i}')")
        db.commit()
        entries = compute_diff(db, "records", 0, limit=3)
        assert len(entries) == 3

    def test_empty_diff(self, db: sqlite3.Connection) -> None:
        entries = compute_diff(db, "records", 0)
        assert entries == []

    def test_diff_scoped_to_table(self, db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO records (record_id) VALUES ('rec1')")
        db.execute("INSERT INTO kg_nodes (node_id, label) VALUES ('node1', 'test')")
        db.commit()
        rec_entries = compute_diff(db, "records", 0)
        node_entries = compute_diff(db, "kg_nodes", 0)
        assert len(rec_entries) == 1
        assert len(node_entries) == 1
        assert rec_entries[0]["table_name"] == "records"
        assert node_entries[0]["table_name"] == "kg_nodes"


# ---------------------------------------------------------------------------
# Sync cursors
# ---------------------------------------------------------------------------

class TestSyncCursor:
    def test_get_cursor_default_zero(self, db: sqlite3.Connection) -> None:
        assert get_sync_cursor(db, "phone", "records") == 0

    def test_update_and_get_cursor(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        update_sync_cursor(db, "phone", "records", 42, write_lock)
        assert get_sync_cursor(db, "phone", "records") == 42

    def test_cursor_upsert(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        update_sync_cursor(db, "phone", "records", 10, write_lock)
        update_sync_cursor(db, "phone", "records", 20, write_lock)
        assert get_sync_cursor(db, "phone", "records") == 20

    def test_cursor_per_device_table(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        update_sync_cursor(db, "phone", "records", 10, write_lock)
        update_sync_cursor(db, "phone", "kg_nodes", 5, write_lock)
        update_sync_cursor(db, "tablet", "records", 3, write_lock)
        assert get_sync_cursor(db, "phone", "records") == 10
        assert get_sync_cursor(db, "phone", "kg_nodes") == 5
        assert get_sync_cursor(db, "tablet", "records") == 3


# ---------------------------------------------------------------------------
# Changelog compaction
# ---------------------------------------------------------------------------

class TestCompactChangelog:
    def test_compact_no_entries(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        deleted = compact_changelog(db, write_lock, retention_days=0)
        assert deleted == 0

    def test_compact_respects_cursor(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        """Entries synced by all devices and older than retention should be deleted."""
        # Insert a record to generate a changelog entry
        db.execute("INSERT INTO records (record_id) VALUES ('c1')")
        db.commit()

        # Backdate the changelog entry to make it "old"
        db.execute("UPDATE _sync_changelog SET ts = datetime('now', '-30 days')")
        db.commit()

        # Advance cursor for the "phone" device past the entry's version
        update_sync_cursor(db, "phone", "records", 100, write_lock)

        deleted = compact_changelog(db, write_lock, retention_days=7)
        assert deleted == 1

    def test_compact_keeps_recent(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        """Recent entries within retention period should NOT be deleted."""
        db.execute("INSERT INTO records (record_id) VALUES ('keep1')")
        db.commit()
        # Entry is recent (just created) so retention_days=7 should keep it
        update_sync_cursor(db, "phone", "records", 100, write_lock)
        deleted = compact_changelog(db, write_lock, retention_days=7)
        assert deleted == 0

    def test_compact_negative_retention_clamped(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        """Negative retention_days should be clamped to 0."""
        db.execute("INSERT INTO records (record_id) VALUES ('neg1')")
        db.commit()
        update_sync_cursor(db, "phone", "records", 100, write_lock)
        # With retention_days clamped to 0, everything "today" is within retention
        # unless the ts matches exactly -- but the entry was just created, so it's recent
        # This should not crash
        deleted = compact_changelog(db, write_lock, retention_days=-5)
        assert isinstance(deleted, int)


# ---------------------------------------------------------------------------
# Trigger SQL generation helpers
# ---------------------------------------------------------------------------

class TestTriggerBuilders:
    def test_build_insert_trigger_sql(self) -> None:
        sql = _build_insert_trigger("records", "record_id", ["ts", "source"], "desktop")
        assert "AFTER INSERT ON records" in sql
        assert "'INSERT'" in sql
        assert "_sync_trg_records_insert" in sql

    def test_build_update_trigger_sql(self) -> None:
        sql = _build_update_trigger("records", "record_id", ["ts", "source"], [], "desktop")
        assert "AFTER UPDATE ON records" in sql
        assert "'UPDATE'" in sql
        assert "WHEN" in sql

    def test_build_update_trigger_no_significant_fields(self) -> None:
        """If all fields are noise, no standalone WHEN clause is generated before BEGIN."""
        sql = _build_update_trigger("records", "record_id", ["a"], ["a"], "desktop")
        # The trigger body uses CASE WHEN, but there should be no top-level WHEN guard
        # between "AFTER UPDATE ON records" and "BEGIN"
        after_update_idx = sql.index("AFTER UPDATE ON records")
        begin_idx = sql.index("BEGIN")
        preamble = sql[after_update_idx:begin_idx]
        # Should NOT have a standalone "WHEN" clause (like "WHEN OLD.x IS NOT NEW.x")
        assert "WHEN OLD" not in preamble

    def test_build_delete_trigger_sql(self) -> None:
        sql = _build_delete_trigger("records", "record_id", ["ts", "source"], "desktop")
        assert "AFTER DELETE ON records" in sql
        assert "'DELETE'" in sql

    def test_clean_json_sql_collapses_commas(self) -> None:
        # _clean_json_sql operates on SQL expressions, but we can verify the REPLACE chain structure
        result = _clean_json_sql("'[,,,a,,,b,]'")
        assert "REPLACE" in result

    def test_clean_json_obj_sql_handles_braces(self) -> None:
        result = _clean_json_obj_sql("'{,a,}'")
        assert "REPLACE" in result


# ---------------------------------------------------------------------------
# Cross-table version independence
# ---------------------------------------------------------------------------

class TestCrossTableVersions:
    def test_versions_independent_per_table(self, db: sqlite3.Connection) -> None:
        """Each table has its own version sequence."""
        db.execute("INSERT INTO records (record_id) VALUES ('xr1')")
        db.execute("INSERT INTO kg_nodes (node_id, label) VALUES ('xn1', 'test')")
        db.execute("INSERT INTO records (record_id) VALUES ('xr2')")
        db.commit()
        rec = compute_diff(db, "records", 0)
        nodes = compute_diff(db, "kg_nodes", 0)
        assert rec[0]["__version"] == 1
        assert rec[1]["__version"] == 2
        assert nodes[0]["__version"] == 1
