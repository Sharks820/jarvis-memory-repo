"""Tests for the sync subsystem: changelog, engine, transport."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with the core tables."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")

    # records table
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
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    # kg_nodes table
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
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    # kg_edges table
    db.execute("""
        CREATE TABLE IF NOT EXISTS kg_edges (
            edge_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id     TEXT DEFAULT '',
            target_id     TEXT DEFAULT '',
            relation      TEXT DEFAULT '',
            confidence    REAL DEFAULT 0.0,
            source_record TEXT DEFAULT '',
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)

    db.commit()
    return db


# ===========================================================================
# Changelog tests
# ===========================================================================


class TestChangelogTriggers:
    """Tests for install_changelog_triggers and trigger behavior."""

    def test_install_changelog_triggers_creates_tables(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        # Verify tables exist
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "_sync_changelog" in tables
        assert "_sync_cursor" in tables

    def test_install_changelog_triggers_idempotent(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)
        install_changelog_triggers(db)  # Should not raise

    def test_trigger_fires_on_insert(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO records (record_id, source, kind, summary) "
            "VALUES ('r1', 'user', 'episodic', 'test record')"
        )
        db.commit()

        rows = db.execute(
            "SELECT table_name, row_id, operation FROM _sync_changelog"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "records"
        assert rows[0][1] == "r1"
        assert rows[0][2] == "INSERT"

    def test_trigger_fires_on_update(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO records (record_id, source, kind, summary) "
            "VALUES ('r1', 'user', 'episodic', 'original')"
        )
        db.commit()

        db.execute("UPDATE records SET summary = 'updated' WHERE record_id = 'r1'")
        db.commit()

        rows = db.execute(
            "SELECT operation FROM _sync_changelog ORDER BY changelog_id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[1][0] == "UPDATE"

    def test_trigger_fires_on_delete(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO records (record_id, source, kind, summary) "
            "VALUES ('r1', 'user', 'episodic', 'test')"
        )
        db.commit()

        db.execute("DELETE FROM records WHERE record_id = 'r1'")
        db.commit()

        rows = db.execute(
            "SELECT operation FROM _sync_changelog ORDER BY changelog_id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[1][0] == "DELETE"

    def test_trigger_captures_new_values(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO records (record_id, source, kind, summary) "
            "VALUES ('r1', 'user', 'episodic', 'hello world')"
        )
        db.commit()

        row = db.execute(
            "SELECT new_values FROM _sync_changelog WHERE operation = 'INSERT'"
        ).fetchone()
        new_values = json.loads(row[0])
        assert new_values["source"] == "user"
        assert new_values["kind"] == "episodic"
        assert new_values["summary"] == "hello world"

    def test_trigger_captures_fields_changed(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO records (record_id, source, kind, summary) "
            "VALUES ('r1', 'user', 'episodic', 'original')"
        )
        db.commit()

        db.execute("UPDATE records SET summary = 'updated' WHERE record_id = 'r1'")
        db.commit()

        row = db.execute(
            "SELECT fields_changed FROM _sync_changelog WHERE operation = 'UPDATE'"
        ).fetchone()
        raw = row[0]
        # The fields_changed may contain empty strings from CASE expressions;
        # filter them out for verification
        fields = [f for f in json.loads(raw) if f]
        assert "summary" in fields

    def test_kg_node_trigger_fires(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO kg_nodes (node_id, label, node_type) "
            "VALUES ('n1', 'Python', 'concept')"
        )
        db.commit()

        rows = db.execute(
            "SELECT table_name, row_id, operation FROM _sync_changelog"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "kg_nodes"
        assert rows[0][1] == "n1"
        assert rows[0][2] == "INSERT"

    def test_kg_edge_trigger_fires(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO kg_edges (source_id, target_id, relation) "
            "VALUES ('n1', 'n2', 'related_to')"
        )
        db.commit()

        rows = db.execute(
            "SELECT table_name, operation FROM _sync_changelog"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "kg_edges"
        assert rows[0][1] == "INSERT"


class TestChangelogDiff:
    """Tests for compute_diff, cursors, and compaction."""

    def test_compute_diff_returns_changes_since_version(self):
        from jarvis_engine.sync.changelog import (
            compute_diff,
            install_changelog_triggers,
        )

        db = _make_db()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO records (record_id, source, kind) "
            "VALUES ('r1', 'user', 'episodic')"
        )
        db.execute(
            "INSERT INTO records (record_id, source, kind) "
            "VALUES ('r2', 'claude', 'semantic')"
        )
        db.commit()

        entries = compute_diff(db, "records", since_version=0)
        assert len(entries) == 2
        assert entries[0]["row_id"] == "r1"
        assert entries[1]["row_id"] == "r2"

    def test_compute_diff_respects_limit(self):
        from jarvis_engine.sync.changelog import (
            compute_diff,
            install_changelog_triggers,
        )

        db = _make_db()
        install_changelog_triggers(db)

        for i in range(5):
            db.execute(
                "INSERT INTO records (record_id, source, kind) "
                f"VALUES ('r{i}', 'user', 'episodic')"
            )
        db.commit()

        entries = compute_diff(db, "records", since_version=0, limit=2)
        assert len(entries) == 2

    def test_get_sync_cursor_default_zero(self):
        from jarvis_engine.sync.changelog import (
            get_sync_cursor,
            install_changelog_triggers,
        )

        db = _make_db()
        install_changelog_triggers(db)

        cursor = get_sync_cursor(db, "mobile-1", "records")
        assert cursor == 0

    def test_update_sync_cursor(self):
        from jarvis_engine.sync.changelog import (
            get_sync_cursor,
            install_changelog_triggers,
            update_sync_cursor,
        )

        db = _make_db()
        install_changelog_triggers(db)
        lock = threading.Lock()

        update_sync_cursor(db, "mobile-1", "records", 42, lock)
        cursor = get_sync_cursor(db, "mobile-1", "records")
        assert cursor == 42

    def test_compact_changelog_removes_old_entries(self):
        from jarvis_engine.sync.changelog import (
            compact_changelog,
            install_changelog_triggers,
            update_sync_cursor,
        )

        db = _make_db()
        install_changelog_triggers(db)
        lock = threading.Lock()

        # Insert a record (creates changelog entry)
        db.execute(
            "INSERT INTO records (record_id, source, kind) "
            "VALUES ('r1', 'user', 'episodic')"
        )
        db.commit()

        # Backdate the changelog entry
        db.execute(
            "UPDATE _sync_changelog SET ts = datetime('now', '-30 days')"
        )
        db.commit()

        # Mark cursor past that version
        update_sync_cursor(db, "mobile-1", "records", 999, lock)

        deleted = compact_changelog(db, lock, retention_days=7)
        assert deleted >= 1

        remaining = db.execute(
            "SELECT COUNT(*) FROM _sync_changelog"
        ).fetchone()[0]
        assert remaining == 0


# ===========================================================================
# SyncEngine tests
# ===========================================================================


class TestSyncEngine:
    """Tests for SyncEngine: outgoing, incoming, conflict resolution."""

    def test_sync_engine_compute_outgoing(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO records (record_id, source, kind, summary) "
            "VALUES ('r1', 'user', 'episodic', 'test')"
        )
        db.commit()

        engine = SyncEngine(db, lock, device_id="desktop")
        result = engine.compute_outgoing("mobile-1")

        assert "changes" in result
        assert "cursors" in result
        assert "records" in result["changes"]
        assert len(result["changes"]["records"]) == 1

    def test_sync_engine_apply_incoming_no_conflict(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db)

        engine = SyncEngine(db, lock, device_id="desktop")

        incoming = {
            "changes": {
                "records": [
                    {
                        "row_id": "r-mobile-1",
                        "operation": "INSERT",
                        "fields_changed": ["source", "kind", "summary"],
                        "old_values": {},
                        "new_values": {
                            "source": "user",
                            "kind": "episodic",
                            "summary": "from mobile",
                        },
                    }
                ]
            },
            "cursors": {"records": 1},
        }

        result = engine.apply_incoming(incoming, "mobile-1")
        assert result["applied"] == 1
        assert result["conflicts_resolved"] == 0
        assert result["errors"] == []

        # Verify the record was inserted
        row = db.execute(
            "SELECT summary FROM records WHERE record_id = 'r-mobile-1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "from mobile"

    def test_sync_engine_apply_incoming_with_conflict(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db)

        # Create a record on desktop
        db.execute(
            "INSERT INTO records (record_id, source, kind, summary, confidence) "
            "VALUES ('r1', 'user', 'episodic', 'desktop version', 0.5)"
        )
        db.commit()

        # Update it locally (this creates a changelog entry)
        db.execute("UPDATE records SET summary = 'desktop update' WHERE record_id = 'r1'")
        db.commit()

        engine = SyncEngine(db, lock, device_id="desktop")

        # Now mobile sends an update to the same record with different fields
        incoming = {
            "changes": {
                "records": [
                    {
                        "row_id": "r1",
                        "operation": "UPDATE",
                        "fields_changed": ["confidence"],
                        "old_values": {"confidence": 0.5},
                        "new_values": {"confidence": 0.9},
                    }
                ]
            },
            "cursors": {"records": 0},
        }

        result = engine.apply_incoming(incoming, "mobile-1")
        assert result["applied"] == 1
        assert result["conflicts_resolved"] == 1

    def test_conflict_resolution_desktop_wins_ties(self):
        from jarvis_engine.sync.engine import SyncEngine

        db = _make_db()
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        local_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "old_values": {"summary": "old"},
            "new_values": {"summary": "desktop-value"},
        }
        remote_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "old_values": {"summary": "old"},
            "new_values": {"summary": "mobile-value"},
        }

        resolved = engine._resolve_conflict(local_entry, remote_entry, desktop_is_local=True)
        assert resolved["new_values"]["summary"] == "desktop-value"

    def test_conflict_resolution_delete_wins(self):
        from jarvis_engine.sync.engine import SyncEngine

        db = _make_db()
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        local_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "UPDATE",
            "fields_changed": ["summary"],
            "old_values": {"summary": "old"},
            "new_values": {"summary": "updated"},
        }
        remote_entry = {
            "table_name": "records",
            "row_id": "r1",
            "operation": "DELETE",
            "fields_changed": [],
            "old_values": {},
            "new_values": {},
        }

        resolved = engine._resolve_conflict(local_entry, remote_entry, desktop_is_local=True)
        assert resolved["operation"] == "DELETE"

    def test_sync_status_returns_structure(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db)

        engine = SyncEngine(db, lock, device_id="desktop")
        status = engine.sync_status()

        assert "cursors" in status
        assert "changelog_size" in status
        assert isinstance(status["changelog_size"], int)


# ===========================================================================
# Transport tests
# ===========================================================================


class TestTransport:
    """Tests for Fernet encryption, key derivation, and salt management."""

    def test_derive_sync_key_deterministic(self):
        from jarvis_engine.sync.transport import derive_sync_key

        salt = b"0123456789abcdef"
        key1 = derive_sync_key("my-signing-key", salt)
        key2 = derive_sync_key("my-signing-key", salt)
        assert key1 == key2

    def test_encrypt_decrypt_roundtrip(self):
        from jarvis_engine.sync.transport import (
            decrypt_sync_payload,
            derive_sync_key,
            encrypt_sync_payload,
        )

        salt = b"0123456789abcdef"
        key = derive_sync_key("test-key", salt)

        payload = {"changes": {"records": [{"row_id": "r1"}]}, "cursors": {"records": 5}}
        encrypted = encrypt_sync_payload(payload, key)
        decrypted = decrypt_sync_payload(encrypted, key)

        assert decrypted == payload

    def test_encrypt_with_compression(self):
        from jarvis_engine.sync.transport import derive_sync_key, encrypt_sync_payload

        salt = b"0123456789abcdef"
        key = derive_sync_key("test-key", salt)

        # Large payload with repetitive data (should compress well)
        payload = {"data": "x" * 10_000}
        encrypted = encrypt_sync_payload(payload, key)

        # Encrypted size should be much smaller than 10000 + overhead
        assert len(encrypted) < 5_000

    def test_get_or_create_salt_creates_file(self):
        from jarvis_engine.sync.transport import get_or_create_salt

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "test_salt.bin"
            assert not salt_path.exists()

            salt = get_or_create_salt(salt_path)
            assert salt_path.exists()
            assert len(salt) == 16

    def test_get_or_create_salt_reuses_existing(self):
        from jarvis_engine.sync.transport import get_or_create_salt

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "test_salt.bin"

            salt1 = get_or_create_salt(salt_path)
            salt2 = get_or_create_salt(salt_path)
            assert salt1 == salt2

    def test_sync_transport_encrypt_decrypt(self):
        from jarvis_engine.sync.transport import SyncTransport

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "salt.bin"
            transport = SyncTransport("my-signing-key", salt_path)

            payload = {"test": True, "data": [1, 2, 3]}
            encrypted = transport.encrypt(payload)
            decrypted = transport.decrypt(encrypted)
            assert decrypted == payload


# ===========================================================================
# Command + handler tests
# ===========================================================================


class TestSyncCommands:
    """Tests for sync command/handler wiring."""

    def test_sync_pull_handler_no_engine(self):
        from jarvis_engine.handlers.sync_handlers import SyncPullHandler
        from jarvis_engine.commands.sync_commands import SyncPullCommand

        handler = SyncPullHandler(Path("."))
        result = handler.handle(SyncPullCommand(device_id="mobile-1"))
        assert "not available" in result.message

    def test_sync_push_handler_no_engine(self):
        from jarvis_engine.handlers.sync_handlers import SyncPushHandler
        from jarvis_engine.commands.sync_commands import SyncPushCommand

        handler = SyncPushHandler(Path("."))
        result = handler.handle(SyncPushCommand(device_id="mobile-1", encrypted_payload="abc"))
        assert "not available" in result.message

    def test_sync_status_handler_no_engine(self):
        from jarvis_engine.handlers.sync_handlers import SyncStatusHandler
        from jarvis_engine.commands.sync_commands import SyncStatusCommand

        handler = SyncStatusHandler(Path("."))
        result = handler.handle(SyncStatusCommand())
        assert "not available" in result.message

    def test_sync_pull_handler_full_flow(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.transport import SyncTransport
        from jarvis_engine.handlers.sync_handlers import SyncPullHandler
        from jarvis_engine.commands.sync_commands import SyncPullCommand

        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db)

        db.execute(
            "INSERT INTO records (record_id, source, kind, summary) "
            "VALUES ('r1', 'user', 'episodic', 'hello')"
        )
        db.commit()

        sync_engine = SyncEngine(db, lock)

        with tempfile.TemporaryDirectory() as tmpdir:
            transport = SyncTransport("test-key", Path(tmpdir) / "salt.bin")
            handler = SyncPullHandler(Path("."), sync_engine=sync_engine, transport=transport)
            result = handler.handle(SyncPullCommand(device_id="mobile-1"))

            assert result.message == "ok"
            assert result.encrypted_payload  # Non-empty
            # Verify we can decrypt it
            raw = base64.b64decode(result.encrypted_payload)
            decrypted = transport.decrypt(raw)
            assert "changes" in decrypted

    def test_sync_push_handler_full_flow(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.transport import SyncTransport
        from jarvis_engine.handlers.sync_handlers import SyncPushHandler
        from jarvis_engine.commands.sync_commands import SyncPushCommand

        db = _make_db()
        lock = threading.Lock()
        install_changelog_triggers(db)

        sync_engine = SyncEngine(db, lock)

        with tempfile.TemporaryDirectory() as tmpdir:
            transport = SyncTransport("test-key", Path(tmpdir) / "salt.bin")

            # Prepare encrypted payload
            changes = {
                "changes": {
                    "records": [
                        {
                            "row_id": "r-from-mobile",
                            "operation": "INSERT",
                            "fields_changed": ["source", "kind", "summary"],
                            "old_values": {},
                            "new_values": {"source": "user", "kind": "episodic", "summary": "mobile data"},
                        }
                    ]
                },
                "cursors": {"records": 1},
            }
            encrypted = transport.encrypt(changes)
            encoded = base64.b64encode(encrypted).decode("ascii")

            handler = SyncPushHandler(Path("."), sync_engine=sync_engine, transport=transport)
            result = handler.handle(SyncPushCommand(device_id="mobile-1", encrypted_payload=encoded))

            assert result.applied == 1
            assert "ok" in result.message


# ===========================================================================
# Version monotonicity test
# ===========================================================================


class TestVersionMonotonicity:
    """Verify __version increases monotonically within a table."""

    def test_version_increases(self):
        from jarvis_engine.sync.changelog import install_changelog_triggers

        db = _make_db()
        install_changelog_triggers(db)

        for i in range(5):
            db.execute(
                "INSERT INTO records (record_id, source, kind) "
                f"VALUES ('r{i}', 'user', 'episodic')"
            )
        db.commit()

        versions = [
            row[0]
            for row in db.execute(
                "SELECT __version FROM _sync_changelog ORDER BY changelog_id"
            ).fetchall()
        ]
        assert versions == sorted(versions)
        assert len(set(versions)) == len(versions)  # All unique


# ===========================================================================
# Transport edge-case tests
# ===========================================================================


class TestTransportEdgeCases:
    """Edge-case tests for transport: size limits, TTL, salt races, key locking."""

    # --- Payload size limit enforcement ---

    def test_encrypt_payload_exceeding_max_size_raises(self):
        """Payloads larger than MAX_SYNC_PAYLOAD_BYTES (16 MiB) must raise ValueError."""
        from jarvis_engine.sync.transport import (
            derive_sync_key,
            encrypt_sync_payload,
            MAX_SYNC_PAYLOAD_BYTES,
        )

        salt = b"0123456789abcdef"
        key = derive_sync_key("test-key", salt)

        # Build a payload whose JSON serialization exceeds 16 MiB.
        # A single string of (MAX + 1) chars plus JSON overhead is enough.
        huge_payload = {"data": "A" * (MAX_SYNC_PAYLOAD_BYTES + 1)}

        with pytest.raises(ValueError, match="too large"):
            encrypt_sync_payload(huge_payload, key)

    def test_encrypt_payload_exactly_at_limit_succeeds(self):
        """A payload exactly at MAX_SYNC_PAYLOAD_BYTES should NOT raise."""
        from jarvis_engine.sync.transport import (
            derive_sync_key,
            encrypt_sync_payload,
            MAX_SYNC_PAYLOAD_BYTES,
        )

        salt = b"0123456789abcdef"
        key = derive_sync_key("test-key", salt)

        # JSON overhead for {"d":"..."} with separators=(",",":") is 6 bytes
        # So we need a string of exactly MAX - 6 chars.
        overhead = len(json.dumps({"d": ""}, separators=(",", ":")).encode("utf-8"))
        filler_len = MAX_SYNC_PAYLOAD_BYTES - overhead
        payload = {"d": "B" * filler_len}

        # Should not raise
        encrypted = encrypt_sync_payload(payload, key)
        assert len(encrypted) > 0

    # --- Empty payload handling ---

    def test_encrypt_decrypt_empty_dict(self):
        """Encrypting and decrypting an empty dict should round-trip cleanly."""
        from jarvis_engine.sync.transport import (
            decrypt_sync_payload,
            derive_sync_key,
            encrypt_sync_payload,
        )

        salt = b"0123456789abcdef"
        key = derive_sync_key("test-key", salt)

        payload = {}
        encrypted = encrypt_sync_payload(payload, key)
        decrypted = decrypt_sync_payload(encrypted, key)
        assert decrypted == {}

    def test_encrypt_decrypt_empty_nested_structures(self):
        """Empty nested lists and dicts should round-trip correctly."""
        from jarvis_engine.sync.transport import (
            decrypt_sync_payload,
            derive_sync_key,
            encrypt_sync_payload,
        )

        salt = b"0123456789abcdef"
        key = derive_sync_key("test-key", salt)

        payload = {"changes": {}, "cursors": {}, "items": []}
        encrypted = encrypt_sync_payload(payload, key)
        decrypted = decrypt_sync_payload(encrypted, key)
        assert decrypted == payload

    # --- TTL rejection in decrypt_sync_payload ---

    def test_decrypt_rejects_expired_token(self):
        """A token encrypted more than ttl seconds ago must be rejected."""
        import struct
        import time
        from cryptography.fernet import Fernet, InvalidToken
        from jarvis_engine.sync.transport import (
            decrypt_sync_payload,
            derive_sync_key,
            encrypt_sync_payload,
        )

        salt = b"0123456789abcdef"
        key = derive_sync_key("test-key", salt)

        payload = {"msg": "hello"}
        encrypted = encrypt_sync_payload(payload, key)

        # Fernet tokens encode the timestamp in bytes 1-9 (big-endian uint64).
        # Rewrite the timestamp to 2 hours ago so that ttl=1 rejects it.
        token_bytes = base64.urlsafe_b64decode(encrypted)
        old_ts = int(time.time()) - 7200  # 2 hours ago
        tampered = (
            token_bytes[0:1]
            + struct.pack(">Q", old_ts)
            + token_bytes[9:]
        )
        old_token = base64.urlsafe_b64encode(tampered)

        # Must be rejected with a 1-second TTL — token is 2 hours old
        with pytest.raises(InvalidToken):
            decrypt_sync_payload(old_token, key, ttl=1)

    # --- Invalid key material ---

    def test_decrypt_with_wrong_key_raises(self):
        """Decrypting a token with a different key must fail gracefully."""
        from cryptography.fernet import InvalidToken
        from jarvis_engine.sync.transport import (
            decrypt_sync_payload,
            derive_sync_key,
            encrypt_sync_payload,
        )

        salt = b"0123456789abcdef"
        key_a = derive_sync_key("key-alpha", salt)
        key_b = derive_sync_key("key-bravo", salt)

        payload = {"secret": "data"}
        encrypted = encrypt_sync_payload(payload, key_a)

        with pytest.raises(InvalidToken):
            decrypt_sync_payload(encrypted, key_b)

    def test_decrypt_with_corrupted_token_raises(self):
        """A token with flipped bytes must fail with InvalidToken."""
        from cryptography.fernet import InvalidToken
        from jarvis_engine.sync.transport import (
            decrypt_sync_payload,
            derive_sync_key,
            encrypt_sync_payload,
        )

        salt = b"0123456789abcdef"
        key = derive_sync_key("test-key", salt)

        payload = {"data": "value"}
        encrypted = encrypt_sync_payload(payload, key)

        # Flip a byte near the middle of the ciphertext
        corrupted = bytearray(encrypted)
        mid = len(corrupted) // 2
        corrupted[mid] ^= 0xFF
        corrupted = bytes(corrupted)

        with pytest.raises(InvalidToken):
            decrypt_sync_payload(corrupted, key)

    # --- Salt file already exists ---

    def test_get_or_create_salt_returns_existing_without_overwrite(self):
        """If a salt file already exists, its contents are returned unchanged."""
        from jarvis_engine.sync.transport import get_or_create_salt

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "existing_salt.bin"
            known_salt = b"KNOWN_SALT_BYTES"
            salt_path.write_bytes(known_salt)

            result = get_or_create_salt(salt_path)
            assert result == known_salt
            # File was not overwritten
            assert salt_path.read_bytes() == known_salt

    # --- get_or_create_salt race condition (os.replace failure fallback) ---

    def test_get_or_create_salt_race_condition_fallback(self):
        """When os.replace fails but salt_path exists, the winner's salt is returned."""
        from unittest.mock import patch
        from jarvis_engine.sync.transport import get_or_create_salt

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "race_salt.bin"
            winner_salt = b"WINNER_SALT_BYTE"  # 16 bytes

            def mock_replace(src, dst):
                # Simulate another process winning the race: write the winner's
                # salt into place, then raise OSError as if our replace failed.
                Path(dst).write_bytes(winner_salt)
                raise OSError("simulated race loss")

            with patch("jarvis_engine.sync.transport.os.replace", side_effect=mock_replace):
                result = get_or_create_salt(salt_path)

            assert result == winner_salt

    def test_get_or_create_salt_race_replace_fails_and_no_file_reraises(self):
        """When os.replace fails and salt_path does not exist, OSError is re-raised."""
        from unittest.mock import patch
        from jarvis_engine.sync.transport import get_or_create_salt

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "missing_salt.bin"

            def mock_replace(src, dst):
                # Ensure the salt_path does NOT exist so the fallback re-raises
                raise OSError("simulated failure")

            with patch("jarvis_engine.sync.transport.os.replace", side_effect=mock_replace):
                with pytest.raises(OSError, match="simulated failure"):
                    get_or_create_salt(salt_path)

    # --- SyncTransport._ensure_key() double-checked locking ---

    def test_ensure_key_derived_only_once(self):
        """_ensure_key should derive the Fernet key exactly once, even when called many times."""
        from unittest.mock import patch
        from jarvis_engine.sync.transport import SyncTransport

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "salt.bin"
            transport = SyncTransport("test-key", salt_path)

            with patch(
                "jarvis_engine.sync.transport.derive_sync_key",
                wraps=__import__(
                    "jarvis_engine.sync.transport", fromlist=["derive_sync_key"]
                ).derive_sync_key,
            ) as mock_derive:
                # Call _ensure_key many times
                keys = [transport._ensure_key() for _ in range(10)]

                # All returned keys must be identical
                assert all(k == keys[0] for k in keys)
                # derive_sync_key was called exactly once
                assert mock_derive.call_count == 1

    def test_ensure_key_concurrent_threads_derive_once(self):
        """Multiple threads calling _ensure_key concurrently should still derive only once."""
        from unittest.mock import patch
        from jarvis_engine.sync.transport import SyncTransport

        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "salt.bin"
            transport = SyncTransport("test-key", salt_path)

            call_count = {"n": 0}
            original_derive = __import__(
                "jarvis_engine.sync.transport", fromlist=["derive_sync_key"]
            ).derive_sync_key

            def counting_derive(*args, **kwargs):
                call_count["n"] += 1
                return original_derive(*args, **kwargs)

            results = [None] * 20
            barrier = threading.Barrier(20)

            def worker(idx):
                barrier.wait()  # Synchronize all threads to start together
                results[idx] = transport._ensure_key()

            with patch(
                "jarvis_engine.sync.transport.derive_sync_key",
                side_effect=counting_derive,
            ):
                threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            # All threads got the same key
            assert all(r == results[0] for r in results)
            # Key was derived exactly once
            assert call_count["n"] == 1

    # --- Round-trip with various payload sizes ---

    @pytest.mark.parametrize(
        "size_label,payload_factory",
        [
            ("tiny", lambda: {"a": 1}),
            ("small_100B", lambda: {"data": "x" * 100}),
            ("medium_10KB", lambda: {"data": "y" * 10_000}),
            ("large_1MB", lambda: {"data": "z" * 1_000_000}),
        ],
        ids=["tiny", "small-100B", "medium-10KB", "large-1MB"],
    )
    def test_transport_roundtrip_various_sizes(self, size_label, payload_factory):
        """SyncTransport round-trips payloads of varying sizes correctly."""
        from jarvis_engine.sync.transport import SyncTransport

        payload = payload_factory()
        with tempfile.TemporaryDirectory() as tmpdir:
            salt_path = Path(tmpdir) / "salt.bin"
            transport = SyncTransport("test-key", salt_path)

            encrypted = transport.encrypt(payload)
            decrypted = transport.decrypt(encrypted)
            assert decrypted == payload
