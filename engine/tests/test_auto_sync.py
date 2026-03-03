"""Tests for the auto-sync subsystem: config management, conflict resolution, relay support."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with core tables."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS records (
            record_id   TEXT PRIMARY KEY,
            ts          TEXT DEFAULT '',
            source      TEXT DEFAULT '',
            kind        TEXT DEFAULT '',
            task_id     TEXT DEFAULT '',
            branch      TEXT DEFAULT '',
            tags        TEXT DEFAULT '',
            summary     TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            confidence  REAL DEFAULT 0.0,
            tier        TEXT DEFAULT '',
            access_count INTEGER DEFAULT 0,
            last_accessed TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            node_id     TEXT PRIMARY KEY,
            label       TEXT DEFAULT '',
            node_type   TEXT DEFAULT '',
            confidence  REAL DEFAULT 0.0,
            locked      INTEGER DEFAULT 0,
            locked_at   TEXT DEFAULT '',
            locked_by   TEXT DEFAULT '',
            sources     TEXT DEFAULT '[]',
            history     TEXT DEFAULT '[]',
            created_at  TEXT DEFAULT '',
            updated_at  TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS kg_edges (
            edge_id     TEXT PRIMARY KEY,
            source_id   TEXT DEFAULT '',
            target_id   TEXT DEFAULT '',
            relation    TEXT DEFAULT '',
            confidence  REAL DEFAULT 0.0,
            source_record TEXT DEFAULT '',
            created_at  TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            category    TEXT NOT NULL,
            preference  TEXT NOT NULL,
            score       REAL DEFAULT 0.0,
            evidence_count INTEGER DEFAULT 0,
            last_observed TEXT DEFAULT '',
            PRIMARY KEY (category, preference)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS response_feedback (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            route       TEXT DEFAULT '',
            feedback    TEXT DEFAULT '',
            user_message_snippet TEXT DEFAULT '',
            recorded_at TEXT DEFAULT ''
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS usage_patterns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            hour        INTEGER DEFAULT 0,
            day_of_week INTEGER DEFAULT 0,
            route       TEXT DEFAULT '',
            topic       TEXT DEFAULT '',
            recorded_at TEXT DEFAULT ''
        )
    """)
    db.commit()
    return db


# ============================================================================
# AutoSyncConfig tests
# ============================================================================

class TestAutoSyncConfig:
    """Tests for sync config management, persistence, and device tracking."""

    def test_default_config_values(self):
        from jarvis_engine.sync.auto_sync import AutoSyncConfig, DEFAULT_SYNC_CONFIG
        config = AutoSyncConfig()
        assert config.get("enabled") is True
        assert config.get("conflict_strategy") == "most_recent"
        assert config.get("sync_interval_connected") == 60
        assert config.get("sync_interval_disconnected") == 300
        assert config.get("relay_url") == ""
        assert config.get("phone_cache_responses") is True

    def test_set_and_get(self):
        from jarvis_engine.sync.auto_sync import AutoSyncConfig
        config = AutoSyncConfig()
        config.set("relay_url", "https://my-tunnel.example.com")
        assert config.get("relay_url") == "https://my-tunnel.example.com"

    def test_bulk_update(self):
        from jarvis_engine.sync.auto_sync import AutoSyncConfig
        config = AutoSyncConfig()
        config.update({
            "relay_url": "https://relay.example.com",
            "sync_interval_connected": 30,
            "conflict_strategy": "desktop_wins",
        })
        assert config.get("relay_url") == "https://relay.example.com"
        assert config.get("sync_interval_connected") == 30
        assert config.get("conflict_strategy") == "desktop_wins"

    def test_persist_and_reload(self, tmp_path):
        from jarvis_engine.sync.auto_sync import AutoSyncConfig
        config_path = tmp_path / "sync" / "config.json"

        config1 = AutoSyncConfig(config_path)
        config1.set("relay_url", "https://persistent.example.com")
        config1.set("sync_interval_connected", 45)

        # Reload from disk
        config2 = AutoSyncConfig(config_path)
        assert config2.get("relay_url") == "https://persistent.example.com"
        assert config2.get("sync_interval_connected") == 45
        # New defaults should be merged in
        assert config2.get("phone_cache_responses") is True

    def test_device_config_payload(self):
        from jarvis_engine.sync.auto_sync import AutoSyncConfig
        config = AutoSyncConfig()
        config.set("relay_url", "https://relay.example.com")
        config.set("lan_url", "https://192.168.1.100:8787")

        payload = config.get_sync_config_for_device("galaxy_s25_primary")
        assert payload["relay_url"] == "https://relay.example.com"
        assert payload["lan_url"] == "https://192.168.1.100:8787"
        assert payload["enabled"] is True
        assert "server_time" in payload
        assert isinstance(payload["server_time"], int)

    def test_heartbeat_tracking(self):
        from jarvis_engine.sync.auto_sync import AutoSyncConfig
        config = AutoSyncConfig()

        # No heartbeat yet
        status = config.get_device_status("galaxy_s25_primary")
        assert status["online"] is False
        assert status["last_seen"] is None

        # Record heartbeat
        config.record_heartbeat("galaxy_s25_primary")
        status = config.get_device_status("galaxy_s25_primary")
        assert status["online"] is True
        assert status["last_seen"] is not None
        assert status["seconds_ago"] < 5

    def test_all_device_statuses(self):
        from jarvis_engine.sync.auto_sync import AutoSyncConfig
        config = AutoSyncConfig()
        config.record_heartbeat("device_a")
        config.record_heartbeat("device_b")

        statuses = config.get_all_device_statuses()
        assert len(statuses) == 2
        device_ids = {s["device_id"] for s in statuses}
        assert "device_a" in device_ids
        assert "device_b" in device_ids

    def test_get_all_returns_copy(self):
        from jarvis_engine.sync.auto_sync import AutoSyncConfig
        config = AutoSyncConfig()
        all_config = config.get_all()
        all_config["relay_url"] = "MODIFIED"
        # Original should be unchanged
        assert config.get("relay_url") == ""


# ============================================================================
# Most-recent-wins conflict resolution tests
# ============================================================================

class TestMostRecentWinsConflict:
    """Tests for the improved conflict resolution strategy."""

    def test_most_recent_wins_remote_newer(self):
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        local_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "desktop version"},
            "ts": "2026-03-01 10:00:00",
        }
        remote_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "phone version"},
            "ts": "2026-03-01 11:00:00",  # Newer
        }

        resolved = engine._resolve_conflict(local_entry, remote_entry, desktop_is_local=True)
        assert resolved["new_values"]["summary"] == "phone version"

    def test_most_recent_wins_local_newer(self):
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        local_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "desktop version"},
            "ts": "2026-03-01 15:00:00",  # Newer
        }
        remote_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "phone version"},
            "ts": "2026-03-01 10:00:00",
        }

        resolved = engine._resolve_conflict(local_entry, remote_entry, desktop_is_local=True)
        assert resolved["new_values"]["summary"] == "desktop version"

    def test_same_timestamp_desktop_wins_tiebreaker(self):
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        local_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "desktop version"},
            "ts": "2026-03-01 10:00:00",
        }
        remote_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "phone version"},
            "ts": "2026-03-01 10:00:00",  # Same timestamp
        }

        resolved = engine._resolve_conflict(local_entry, remote_entry, desktop_is_local=True)
        # Desktop wins as tiebreaker when timestamps are equal
        assert resolved["new_values"]["summary"] == "desktop version"

    def test_delete_always_wins_regardless_of_strategy(self):
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        local_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "updated"},
            "ts": "2026-03-01 15:00:00",  # Even though newer
        }
        remote_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "DELETE",
            "ts": "2026-03-01 10:00:00",
        }

        resolved = engine._resolve_conflict(local_entry, remote_entry, desktop_is_local=True)
        assert resolved["operation"] == "DELETE"

    def test_legacy_desktop_wins_strategy(self):
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="desktop_wins")

        local_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "desktop version"},
            "ts": "2026-03-01 10:00:00",  # Older
        }
        remote_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "phone version"},
            "ts": "2026-03-01 15:00:00",  # Newer but doesn't matter
        }

        resolved = engine._resolve_conflict(local_entry, remote_entry, desktop_is_local=True)
        # Desktop always wins with legacy strategy, regardless of timestamp
        assert resolved["new_values"]["summary"] == "desktop version"

    def test_field_level_merge_non_conflicting(self):
        """Non-conflicting fields should be merged from both sides."""
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        local_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "new_values": {"summary": "desktop summary"},
            "ts": "2026-03-01 10:00:00",
        }
        remote_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["tags"],
            "new_values": {"tags": "phone,context"},
            "ts": "2026-03-01 10:00:00",
        }

        resolved = engine._resolve_conflict(local_entry, remote_entry, desktop_is_local=True)
        # Both fields should be present — no conflict
        assert resolved["new_values"]["summary"] == "desktop summary"
        assert resolved["new_values"]["tags"] == "phone,context"

    def test_bidirectional_sync_phone_data_respected(self):
        """Phone changes should be applied to desktop when no conflict exists."""
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers, update_sync_cursor
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        # Insert a record on desktop
        db.execute(
            "INSERT INTO records (record_id, summary) VALUES (?, ?)",
            ("r1", "original"),
        )
        db.commit()

        # Advance the phone's cursor past the initial INSERT so it's not a conflict
        # (simulates the phone having already synced the INSERT)
        update_sync_cursor(db, "galaxy_s25_primary", "records", 1, lock)

        # Simulate incoming phone update (no local conflict since cursor is advanced)
        incoming = {
            "changes": {
                "records": [{
                    "row_id": "r1",
                    "operation": "UPDATE",
                    "fields_changed": ["summary"],
                    "new_values": {"summary": "updated by phone"},
                    "__version": 2,
                }],
            },
            "cursors": {"records": 2},
        }

        result = engine.apply_incoming(incoming, "galaxy_s25_primary")
        assert result["applied"] == 1
        assert result["errors"] == []

        # Verify the phone's change was applied
        row = db.execute("SELECT summary FROM records WHERE record_id = 'r1'").fetchone()
        assert row[0] == "updated by phone"

    def test_phone_insert_synced_to_desktop(self):
        """Phone can INSERT new records that appear on desktop."""
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        # Phone sends an INSERT for a new record
        incoming = {
            "changes": {
                "records": [{
                    "row_id": "phone_r1",
                    "operation": "INSERT",
                    "fields_changed": ["summary", "source"],
                    "new_values": {
                        "record_id": "phone_r1",
                        "summary": "learned from phone context",
                        "source": "user",
                    },
                    "__version": 1,
                }],
            },
            "cursors": {"records": 1},
        }

        result = engine.apply_incoming(incoming, "galaxy_s25_primary")
        assert result["applied"] == 1

        row = db.execute("SELECT summary FROM records WHERE record_id = 'phone_r1'").fetchone()
        assert row is not None
        assert row[0] == "learned from phone context"


# ============================================================================
# SyncEngine with most_recent strategy integration tests
# ============================================================================

class TestSyncEngineIntegration:
    """Full integration tests for compute_outgoing + apply_incoming."""

    def test_compute_outgoing_returns_changes(self):
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        # Insert a record — should appear in changelog
        db.execute(
            "INSERT INTO records (record_id, summary, source) VALUES (?, ?, ?)",
            ("r1", "test record", "user"),
        )
        db.commit()

        outgoing = engine.compute_outgoing("galaxy_s25_primary")
        assert "records" in outgoing["changes"]
        assert len(outgoing["changes"]["records"]) == 1
        assert outgoing["changes"]["records"][0]["row_id"] == "r1"

    def test_sync_status_includes_all_info(self):
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.changelog import install_changelog_triggers
        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db, device_id="desktop")
        engine = SyncEngine(db, lock, device_id="desktop", conflict_strategy="most_recent")

        status = engine.sync_status()
        assert "cursors" in status
        assert "changelog_size" in status
        assert isinstance(status["changelog_size"], int)
