"""Tests for learning.provenance — SQLite-backed provenance storage."""

from __future__ import annotations

import json
import sqlite3
import threading
from unittest.mock import patch

import pytest

from jarvis_engine.learning.provenance import (
    LearningProvenanceStore,
    _serialize_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metadata() -> dict:
    """Build a minimal LearningTrustMetadata dict for testing."""
    return {
        "learning_lane": "observed",
        "trust_level": "T1_observed",
        "promotion_state": "observed",
        "source_type": "conversation",
        "source_channel": "cli",
        "source_uri": "",
        "source_hash": "abc123hash",
        "artifact_kind": "text",
        "mime_type": "text/plain",
        "scanner_verdict": "",
        "scanner_details": "",
        "approved_by_owner": False,
        "approved_at": "",
        "correlation_id": "corr-1",
        "mission_id": "",
        "first_seen_at": "2026-03-01T00:00:00Z",
        "last_used_at": "2026-03-01T00:00:00Z",
        "promotion_reason": "",
        "blocked_reason": "",
        "derived_from_artifact": False,
        "policy_mode": "audit_only",
    }


@pytest.fixture
def db():
    """In-memory SQLite connection."""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def store(db):
    """LearningProvenanceStore with provenance enabled."""
    with patch("jarvis_engine.learning.provenance.learning_provenance_enabled", return_value=True):
        return LearningProvenanceStore(db)


@pytest.fixture
def disabled_store(db):
    """LearningProvenanceStore with provenance disabled."""
    with patch("jarvis_engine.learning.provenance.learning_provenance_enabled", return_value=False):
        return LearningProvenanceStore(db)


# ---------------------------------------------------------------------------
# _serialize_payload
# ---------------------------------------------------------------------------


class TestSerializePayload:
    def test_none_returns_empty(self):
        assert _serialize_payload(None) == ""

    def test_string_passthrough(self):
        assert _serialize_payload("hello") == "hello"

    def test_dict_to_json(self):
        result = _serialize_payload({"a": 1, "b": 2})
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_list_to_json(self):
        result = _serialize_payload([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_non_serializable_falls_back_to_str(self):
        result = _serialize_payload(object())
        assert result  # should be str(object()) representation


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------


class TestSchemaInit:
    def test_tables_created_when_enabled(self, store, db):
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur.fetchall()}
        assert "learning_provenance" in tables
        assert "trust_policy_events" in tables
        assert "artifact_quarantine" in tables
        assert "threat_memory_indicators" in tables

    def test_tables_not_created_when_disabled(self, disabled_store, db):
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur.fetchall()}
        assert "learning_provenance" not in tables

    def test_indexes_created(self, store, db):
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = {row[0] for row in cur.fetchall()}
        assert "idx_learning_provenance_lane" in indexes
        assert "idx_trust_policy_events_subject" in indexes
        assert "idx_artifact_quarantine_expires" in indexes
        assert "idx_threat_indicators_subject" in indexes


# ---------------------------------------------------------------------------
# record_subject
# ---------------------------------------------------------------------------


class TestRecordSubject:
    def test_insert_new_subject(self, store, db):
        meta = _make_metadata()
        store.record_subject(
            subject_type="memory",
            subject_id="rec-001",
            metadata=meta,
        )
        row = db.execute(
            "SELECT subject_type, subject_id, learning_lane, trust_level FROM learning_provenance"
        ).fetchone()
        assert row is not None
        assert row[0] == "memory"
        assert row[1] == "rec-001"
        assert row[2] == "observed"
        assert row[3] == "T1_observed"

    def test_upsert_updates_existing(self, store, db):
        meta = _make_metadata()
        store.record_subject(subject_type="memory", subject_id="rec-001", metadata=meta)

        meta2 = _make_metadata()
        meta2["trust_level"] = "T2_verified"
        meta2["promotion_state"] = "verified"
        store.record_subject(subject_type="memory", subject_id="rec-001", metadata=meta2)

        row = db.execute(
            "SELECT trust_level, promotion_state FROM learning_provenance WHERE subject_id='rec-001'"
        ).fetchone()
        assert row[0] == "T2_verified"
        assert row[1] == "verified"

    def test_noop_when_disabled(self, disabled_store, db):
        meta = _make_metadata()
        disabled_store.record_subject(
            subject_type="memory", subject_id="rec-001", metadata=meta
        )
        cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "learning_provenance" not in tables

    def test_approved_by_owner_stored_as_int(self, store, db):
        meta = _make_metadata()
        meta["approved_by_owner"] = True
        store.record_subject(subject_type="memory", subject_id="rec-002", metadata=meta)

        row = db.execute(
            "SELECT approved_by_owner FROM learning_provenance WHERE subject_id='rec-002'"
        ).fetchone()
        assert row[0] == 1

    def test_derived_from_artifact_stored_as_int(self, store, db):
        meta = _make_metadata()
        meta["derived_from_artifact"] = True
        store.record_subject(subject_type="memory", subject_id="rec-003", metadata=meta)

        row = db.execute(
            "SELECT derived_from_artifact FROM learning_provenance WHERE subject_id='rec-003'"
        ).fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# record_policy_event
# ---------------------------------------------------------------------------


class TestRecordPolicyEvent:
    def test_insert_event(self, store, db):
        store.record_policy_event(
            subject_type="memory",
            subject_id="rec-001",
            action="promote",
            verdict="allow",
            policy_mode="audit_only",
            reason="passed scanner",
        )
        row = db.execute(
            "SELECT action, verdict, policy_mode, reason FROM trust_policy_events"
        ).fetchone()
        assert row is not None
        assert row[0] == "promote"
        assert row[1] == "allow"
        assert row[2] == "audit_only"
        assert row[3] == "passed scanner"

    def test_multiple_events_for_same_subject(self, store, db):
        for action in ("ingest", "scan", "promote"):
            store.record_policy_event(
                subject_type="memory",
                subject_id="rec-001",
                action=action,
                verdict="allow",
                policy_mode="audit_only",
            )
        count = db.execute("SELECT COUNT(*) FROM trust_policy_events").fetchone()[0]
        assert count == 3

    def test_metadata_serialized(self, store, db):
        store.record_policy_event(
            subject_type="memory",
            subject_id="rec-001",
            action="scan",
            verdict="warn",
            policy_mode="warn_only",
            metadata={"scanner": "v2", "score": 0.42},
        )
        row = db.execute("SELECT metadata_json FROM trust_policy_events").fetchone()
        parsed = json.loads(row[0])
        assert parsed["scanner"] == "v2"
        assert parsed["score"] == 0.42

    def test_noop_when_disabled(self, disabled_store, db):
        disabled_store.record_policy_event(
            subject_type="memory",
            subject_id="rec-001",
            action="promote",
            verdict="allow",
            policy_mode="audit_only",
        )
        # No tables created, no crash
        cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        assert len(cur.fetchall()) == 0


# ---------------------------------------------------------------------------
# quarantine_artifact
# ---------------------------------------------------------------------------


class TestQuarantineArtifact:
    def test_insert_quarantine(self, store, db):
        store.quarantine_artifact(
            subject_type="memory",
            subject_id="rec-005",
            source_hash="hash123",
            source_channel="web_harvest",
            artifact_kind="code",
            safe_summary="Suspicious code snippet",
            quarantine_reason="shell injection pattern",
        )
        row = db.execute(
            "SELECT subject_id, artifact_kind, quarantine_reason FROM artifact_quarantine"
        ).fetchone()
        assert row is not None
        assert row[0] == "rec-005"
        assert row[1] == "code"
        assert row[2] == "shell injection pattern"

    def test_upsert_updates_quarantine(self, store, db):
        store.quarantine_artifact(
            subject_type="memory",
            subject_id="rec-005",
            source_hash="hash123",
            source_channel="web_harvest",
            artifact_kind="code",
            safe_summary="First summary",
            quarantine_reason="first reason",
        )
        store.quarantine_artifact(
            subject_type="memory",
            subject_id="rec-005",
            source_hash="hash456",
            source_channel="api",
            artifact_kind="text",
            safe_summary="Updated summary",
            quarantine_reason="updated reason",
        )
        count = db.execute("SELECT COUNT(*) FROM artifact_quarantine").fetchone()[0]
        assert count == 1
        row = db.execute(
            "SELECT safe_summary, quarantine_reason FROM artifact_quarantine"
        ).fetchone()
        assert row[0] == "Updated summary"
        assert row[1] == "updated reason"

    def test_expires_at_set(self, store, db):
        store.quarantine_artifact(
            subject_type="memory",
            subject_id="rec-006",
            source_hash="h",
            source_channel="cli",
            artifact_kind="text",
            safe_summary="test",
            quarantine_reason="test",
            ttl_days=7,
        )
        row = db.execute("SELECT expires_at FROM artifact_quarantine").fetchone()
        assert row[0]  # non-empty ISO timestamp

    def test_noop_when_disabled(self, disabled_store, db):
        disabled_store.quarantine_artifact(
            subject_type="memory",
            subject_id="rec-005",
            source_hash="h",
            source_channel="cli",
            artifact_kind="text",
            safe_summary="test",
            quarantine_reason="test",
        )
        cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        assert len(cur.fetchall()) == 0


# ---------------------------------------------------------------------------
# record_threat_indicator
# ---------------------------------------------------------------------------


class TestRecordThreatIndicator:
    def test_insert_indicator(self, store, db):
        store.record_threat_indicator(
            indicator_type="ip_address",
            indicator_value="192.168.1.100",
            subject_type="memory",
            subject_id="rec-010",
            source_hash="h999",
            reason="known scanner IP",
        )
        row = db.execute(
            "SELECT indicator_type, indicator_value, reason FROM threat_memory_indicators"
        ).fetchone()
        assert row is not None
        assert row[0] == "ip_address"
        assert row[1] == "192.168.1.100"
        assert row[2] == "known scanner IP"

    def test_multiple_indicators(self, store, db):
        for i in range(5):
            store.record_threat_indicator(
                indicator_type="hash",
                indicator_value=f"bad_hash_{i}",
                subject_type="memory",
                subject_id=f"rec-{i}",
                source_hash=f"src_{i}",
                reason="suspicious",
            )
        count = db.execute("SELECT COUNT(*) FROM threat_memory_indicators").fetchone()[0]
        assert count == 5

    def test_noop_when_disabled(self, disabled_store, db):
        disabled_store.record_threat_indicator(
            indicator_type="ip",
            indicator_value="10.0.0.1",
            subject_type="memory",
            subject_id="rec-010",
            source_hash="h",
            reason="test",
        )
        cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        assert len(cur.fetchall()) == 0


# ---------------------------------------------------------------------------
# get_subject
# ---------------------------------------------------------------------------


class TestGetSubject:
    def test_returns_none_for_unknown(self, store):
        assert store.get_subject("memory", "nonexistent") is None

    def test_returns_dict_for_known(self, store):
        meta = _make_metadata()
        store.record_subject(subject_type="memory", subject_id="rec-001", metadata=meta)
        result = store.get_subject("memory", "rec-001")
        assert result is not None
        assert result["subject_id"] == "rec-001"
        assert result["learning_lane"] == "observed"
        assert result["trust_level"] == "T1_observed"

    def test_returns_none_when_disabled(self, disabled_store):
        assert disabled_store.get_subject("memory", "rec-001") is None


# ---------------------------------------------------------------------------
# get_policy_events
# ---------------------------------------------------------------------------


class TestGetPolicyEvents:
    def test_returns_empty_for_unknown(self, store):
        events = store.get_policy_events("memory", "nonexistent")
        assert events == []

    def test_returns_events_in_order(self, store):
        for action in ("ingest", "scan", "promote"):
            store.record_policy_event(
                subject_type="memory",
                subject_id="rec-001",
                action=action,
                verdict="allow",
                policy_mode="audit_only",
            )
        events = store.get_policy_events("memory", "rec-001")
        assert len(events) == 3
        assert events[0]["action"] == "ingest"
        assert events[1]["action"] == "scan"
        assert events[2]["action"] == "promote"

    def test_event_fields_present(self, store):
        store.record_policy_event(
            subject_type="memory",
            subject_id="rec-001",
            action="block",
            verdict="deny",
            policy_mode="hard_block",
            reason="injection detected",
        )
        events = store.get_policy_events("memory", "rec-001")
        assert len(events) == 1
        evt = events[0]
        assert "id" in evt
        assert evt["subject_type"] == "memory"
        assert evt["subject_id"] == "rec-001"
        assert evt["action"] == "block"
        assert evt["verdict"] == "deny"
        assert evt["policy_mode"] == "hard_block"
        assert evt["reason"] == "injection detected"
        assert evt["recorded_at"]  # non-empty timestamp
        assert evt["metadata_json"]  # at least "{}"

    def test_returns_empty_when_disabled(self, disabled_store):
        events = disabled_store.get_policy_events("memory", "rec-001")
        assert events == []

    def test_filters_by_subject(self, store):
        store.record_policy_event(
            subject_type="memory", subject_id="rec-A",
            action="ingest", verdict="allow", policy_mode="audit_only",
        )
        store.record_policy_event(
            subject_type="memory", subject_id="rec-B",
            action="scan", verdict="warn", policy_mode="warn_only",
        )
        events_a = store.get_policy_events("memory", "rec-A")
        events_b = store.get_policy_events("memory", "rec-B")
        assert len(events_a) == 1
        assert len(events_b) == 1
        assert events_a[0]["action"] == "ingest"
        assert events_b[0]["action"] == "scan"


# ---------------------------------------------------------------------------
# Thread safety: custom locks are used
# ---------------------------------------------------------------------------


class TestCustomLocks:
    def test_custom_write_lock(self, db):
        lock = threading.Lock()
        with patch("jarvis_engine.learning.provenance.learning_provenance_enabled", return_value=True):
            store = LearningProvenanceStore(db, write_lock=lock)
        meta = _make_metadata()
        store.record_subject(subject_type="memory", subject_id="r1", metadata=meta)
        result = store.get_subject("memory", "r1")
        assert result is not None

    def test_custom_db_lock(self, db):
        lock = threading.Lock()
        with patch("jarvis_engine.learning.provenance.learning_provenance_enabled", return_value=True):
            store = LearningProvenanceStore(db, db_lock=lock)
        meta = _make_metadata()
        store.record_subject(subject_type="memory", subject_id="r1", metadata=meta)
        result = store.get_subject("memory", "r1")
        assert result is not None
