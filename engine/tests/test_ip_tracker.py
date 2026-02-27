"""Tests for jarvis_engine.security.ip_tracker."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

from jarvis_engine._compat import UTC
from jarvis_engine.security.ip_tracker import IPTracker


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------


@pytest.fixture()
def tracker() -> IPTracker:
    """Return an IPTracker backed by an in-memory SQLite database."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.Lock()
    return IPTracker(db, lock)


@pytest.fixture()
def db_and_lock() -> tuple[sqlite3.Connection, threading.Lock]:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.Lock()
    return db, lock


# ---------------------------------------------------------------
# Schema
# ---------------------------------------------------------------


class TestSchema:
    def test_table_created(self, tracker: IPTracker) -> None:
        row = tracker._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='threat_ips'"
        ).fetchone()
        assert row is not None

    def test_idempotent_schema(self, db_and_lock: tuple) -> None:
        db, lock = db_and_lock
        t1 = IPTracker(db, lock)
        t2 = IPTracker(db, lock)  # should not raise
        assert t2 is not t1


# ---------------------------------------------------------------
# Escalation ladder
# ---------------------------------------------------------------


class TestEscalationLadder:
    def test_first_attempt_allow(self, tracker: IPTracker) -> None:
        action = tracker.record_attempt("1.2.3.4", "scan")
        assert action == "ALLOW"

    def test_two_attempts_allow(self, tracker: IPTracker) -> None:
        tracker.record_attempt("1.2.3.4", "scan")
        action = tracker.record_attempt("1.2.3.4", "scan")
        assert action == "ALLOW"

    def test_three_attempts_throttle(self, tracker: IPTracker) -> None:
        for _ in range(2):
            tracker.record_attempt("1.2.3.4", "scan")
        action = tracker.record_attempt("1.2.3.4", "scan")
        assert action == "THROTTLE"

    def test_four_attempts_throttle(self, tracker: IPTracker) -> None:
        for _ in range(3):
            tracker.record_attempt("1.2.3.4", "scan")
        action = tracker.record_attempt("1.2.3.4", "scan")
        assert action == "THROTTLE"

    def test_five_attempts_block_1h(self, tracker: IPTracker) -> None:
        for _ in range(4):
            tracker.record_attempt("1.2.3.4", "scan")
        action = tracker.record_attempt("1.2.3.4", "scan")
        assert action == "BLOCK"
        assert tracker.is_blocked("1.2.3.4")

    def test_ten_attempts_block_24h(self, tracker: IPTracker) -> None:
        for _ in range(9):
            tracker.record_attempt("1.2.3.4", "scan")
        action = tracker.record_attempt("1.2.3.4", "scan")
        assert action == "BLOCK"

    def test_twenty_attempts_permanent_block(self, tracker: IPTracker) -> None:
        for _ in range(19):
            tracker.record_attempt("1.2.3.4", "scan")
        action = tracker.record_attempt("1.2.3.4", "scan")
        assert action == "BLOCK"
        report = tracker.get_threat_report("1.2.3.4")
        assert report is not None
        assert report["blocked_until"] == "permanent"

    def test_beyond_twenty_stays_permanent(self, tracker: IPTracker) -> None:
        for _ in range(25):
            tracker.record_attempt("1.2.3.4", "scan")
        report = tracker.get_threat_report("1.2.3.4")
        assert report is not None
        assert report["blocked_until"] == "permanent"


# ---------------------------------------------------------------
# Blocking / unblocking
# ---------------------------------------------------------------


class TestBlocking:
    def test_manual_block_permanent(self, tracker: IPTracker) -> None:
        tracker.block_ip("10.0.0.1")
        assert tracker.is_blocked("10.0.0.1")
        report = tracker.get_threat_report("10.0.0.1")
        assert report is not None
        assert report["blocked_until"] == "permanent"

    def test_manual_block_with_duration(self, tracker: IPTracker) -> None:
        tracker.block_ip("10.0.0.2", duration_hours=2)
        assert tracker.is_blocked("10.0.0.2")

    def test_unblock_clears_block(self, tracker: IPTracker) -> None:
        tracker.block_ip("10.0.0.3")
        assert tracker.is_blocked("10.0.0.3")
        tracker.unblock_ip("10.0.0.3")
        assert not tracker.is_blocked("10.0.0.3")

    def test_unblock_nonexistent_ip_noop(self, tracker: IPTracker) -> None:
        tracker.unblock_ip("9.9.9.9")  # should not raise

    def test_manual_block_creates_record(self, tracker: IPTracker) -> None:
        tracker.block_ip("10.0.0.4", duration_hours=1)
        report = tracker.get_threat_report("10.0.0.4")
        assert report is not None
        assert report["total_attempts"] == 0
        assert report["notes"] == "manual block"

    def test_manual_block_updates_existing(self, tracker: IPTracker) -> None:
        tracker.record_attempt("10.0.0.5", "scan")
        tracker.block_ip("10.0.0.5")
        report = tracker.get_threat_report("10.0.0.5")
        assert report is not None
        assert report["blocked_until"] == "permanent"
        assert report["total_attempts"] == 1  # preserved from record_attempt


# ---------------------------------------------------------------
# is_blocked with expired blocks
# ---------------------------------------------------------------


class TestBlockExpiry:
    def test_expired_block_returns_false(self, tracker: IPTracker) -> None:
        # Insert a record with a blocked_until in the past
        past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        tracker._db.execute(
            """
            INSERT INTO threat_ips
                (ip, first_seen, last_seen, total_attempts, attack_types,
                 threat_score, blocked_until)
            VALUES (?, ?, ?, 5, '["scan"]', 5.0, ?)
            """,
            ("5.5.5.5", datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), past),
        )
        tracker._db.commit()
        assert not tracker.is_blocked("5.5.5.5")

    def test_future_block_returns_true(self, tracker: IPTracker) -> None:
        future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        tracker._db.execute(
            """
            INSERT INTO threat_ips
                (ip, first_seen, last_seen, total_attempts, attack_types,
                 threat_score, blocked_until)
            VALUES (?, ?, ?, 5, '["scan"]', 5.0, ?)
            """,
            ("6.6.6.6", datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), future),
        )
        tracker._db.commit()
        assert tracker.is_blocked("6.6.6.6")

    def test_permanent_block_never_expires(self, tracker: IPTracker) -> None:
        tracker.block_ip("7.7.7.7")
        assert tracker.is_blocked("7.7.7.7")

    def test_unknown_ip_not_blocked(self, tracker: IPTracker) -> None:
        assert not tracker.is_blocked("8.8.8.8")

    def test_null_blocked_until_not_blocked(self, tracker: IPTracker) -> None:
        tracker.record_attempt("9.9.9.9", "scan")
        assert not tracker.is_blocked("9.9.9.9")


# ---------------------------------------------------------------
# get_threat_report
# ---------------------------------------------------------------


class TestGetThreatReport:
    def test_nonexistent_ip_returns_none(self, tracker: IPTracker) -> None:
        assert tracker.get_threat_report("1.1.1.1") is None

    def test_report_fields_present(self, tracker: IPTracker) -> None:
        tracker.record_attempt("2.2.2.2", "injection")
        report = tracker.get_threat_report("2.2.2.2")
        assert report is not None
        assert report["ip"] == "2.2.2.2"
        assert report["total_attempts"] == 1
        assert "injection" in report["attack_types"]
        assert report["threat_score"] >= 1.0
        assert "first_seen" in report
        assert "last_seen" in report

    def test_attack_types_accumulate(self, tracker: IPTracker) -> None:
        tracker.record_attempt("3.3.3.3", "scan")
        tracker.record_attempt("3.3.3.3", "injection")
        tracker.record_attempt("3.3.3.3", "scan")  # duplicate
        report = tracker.get_threat_report("3.3.3.3")
        assert report is not None
        assert set(report["attack_types"]) == {"scan", "injection"}

    def test_threat_score_increases(self, tracker: IPTracker) -> None:
        tracker.record_attempt("4.4.4.4", "scan")
        r1 = tracker.get_threat_report("4.4.4.4")
        for _ in range(4):
            tracker.record_attempt("4.4.4.4", "scan")
        r2 = tracker.get_threat_report("4.4.4.4")
        assert r1 is not None and r2 is not None
        assert r2["threat_score"] > r1["threat_score"]


# ---------------------------------------------------------------
# get_all_threats
# ---------------------------------------------------------------


class TestGetAllThreats:
    def test_empty_db_returns_empty(self, tracker: IPTracker) -> None:
        assert tracker.get_all_threats() == []

    def test_returns_all_tracked_ips(self, tracker: IPTracker) -> None:
        tracker.record_attempt("10.0.0.1", "scan")
        tracker.record_attempt("10.0.0.2", "injection")
        threats = tracker.get_all_threats()
        ips = {t["ip"] for t in threats}
        assert ips == {"10.0.0.1", "10.0.0.2"}

    def test_min_score_filter(self, tracker: IPTracker) -> None:
        tracker.record_attempt("10.0.0.1", "scan")  # score = 1.0
        for _ in range(9):
            tracker.record_attempt("10.0.0.2", "injection")  # score = 10.0
        threats = tracker.get_all_threats(min_score=5.0)
        ips = {t["ip"] for t in threats}
        assert "10.0.0.2" in ips
        assert "10.0.0.1" not in ips

    def test_ordered_by_score_desc(self, tracker: IPTracker) -> None:
        tracker.record_attempt("10.0.0.1", "a")
        for _ in range(5):
            tracker.record_attempt("10.0.0.2", "b")
        threats = tracker.get_all_threats()
        assert threats[0]["ip"] == "10.0.0.2"


# ---------------------------------------------------------------
# Independent IPs
# ---------------------------------------------------------------


class TestIPIsolation:
    def test_different_ips_independent_escalation(self, tracker: IPTracker) -> None:
        for _ in range(5):
            tracker.record_attempt("10.0.0.1", "scan")
        tracker.record_attempt("10.0.0.2", "scan")
        assert tracker.is_blocked("10.0.0.1")
        assert not tracker.is_blocked("10.0.0.2")


# ---------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------


class TestEdgeCases:
    def test_score_capped_at_100(self, tracker: IPTracker) -> None:
        for _ in range(150):
            tracker.record_attempt("1.1.1.1", "flood")
        report = tracker.get_threat_report("1.1.1.1")
        assert report is not None
        assert report["threat_score"] <= 100.0

    def test_concurrent_schema_creation(self) -> None:
        """Two trackers on the same DB should not conflict."""
        db = sqlite3.connect(":memory:", check_same_thread=False)
        lock = threading.Lock()
        t1 = IPTracker(db, lock)
        t2 = IPTracker(db, lock)
        t1.record_attempt("1.1.1.1", "a")
        t2.record_attempt("1.1.1.1", "b")
        report = t1.get_threat_report("1.1.1.1")
        assert report is not None
        assert report["total_attempts"] == 2
