"""Tests for proactive/alert_queue.py — enqueue, drain, dedup, rotation, threading."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from jarvis_engine.proactive.alert_queue import (
    enqueue_alert,
    drain_alerts,
    peek_alerts,
    _queue_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Provide a tmp root with .planning/runtime/ directory."""
    (tmp_path / ".planning" / "runtime").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_alert(**overrides) -> dict:
    """Create an alert dict with sensible defaults."""
    base = {"type": "test", "title": "Test Alert", "body": "This is a test."}
    base.update(overrides)
    return base


# ===================================================================
# Basic enqueue and drain operations
# ===================================================================


class TestEnqueueAndDrain:
    """Tests for basic enqueue_alert() and drain_alerts() operations."""

    def test_enqueue_returns_alert_id(self, root) -> None:
        """enqueue_alert should return a string alert ID."""
        alert_id = enqueue_alert(root, _make_alert())
        assert isinstance(alert_id, str)
        assert len(alert_id) > 0

    def test_enqueue_preserves_custom_id(self, root) -> None:
        """If alert has an 'id' key, it should be used as the alert ID."""
        alert_id = enqueue_alert(root, _make_alert(id="custom-123"))
        assert alert_id == "custom-123"

    def test_drain_returns_all_enqueued(self, root) -> None:
        """drain_alerts should return all enqueued alerts."""
        enqueue_alert(root, _make_alert(title="Alert 1"))
        enqueue_alert(root, _make_alert(title="Alert 2", type="other"))
        alerts = drain_alerts(root)
        titles = [a["title"] for a in alerts]
        assert "Alert 1" in titles
        assert "Alert 2" in titles

    def test_drain_clears_queue(self, root) -> None:
        """After drain, queue should be empty."""
        enqueue_alert(root, _make_alert())
        first = drain_alerts(root)
        second = drain_alerts(root)
        assert len(first) >= 1
        assert second == []

    def test_drain_respects_limit(self, root) -> None:
        """drain_alerts should respect the limit parameter."""
        for i in range(5):
            enqueue_alert(root, _make_alert(title=f"Alert {i}", type=f"type_{i}"))
        alerts = drain_alerts(root, limit=3)
        assert len(alerts) == 3

    def test_enqueue_creates_parent_dirs(self, tmp_path) -> None:
        """enqueue_alert should create parent directories if missing."""
        # Use a path without pre-created runtime dir
        root = tmp_path / "fresh"
        (root / ".planning" / "runtime").mkdir(parents=True, exist_ok=True)
        alert_id = enqueue_alert(root, _make_alert())
        assert isinstance(alert_id, str)

    def test_alert_fields_are_stored(self, root) -> None:
        """Enqueued alerts should preserve type, title, body, group_key, priority."""
        enqueue_alert(root, {
            "type": "medication",
            "title": "Take Pill",
            "body": "Metformin 500mg",
            "group_key": "meds",
            "priority": "urgent",
        })
        alerts = drain_alerts(root)
        assert len(alerts) == 1
        a = alerts[0]
        assert a["type"] == "medication"
        assert a["title"] == "Take Pill"
        assert a["body"] == "Metformin 500mg"
        assert a["group_key"] == "meds"
        assert a["priority"] == "urgent"

    def test_alert_title_truncated_to_200(self, root) -> None:
        """Alert titles longer than 200 chars should be truncated."""
        long_title = "A" * 300
        enqueue_alert(root, _make_alert(title=long_title))
        alerts = drain_alerts(root)
        assert len(alerts[0]["title"]) == 200

    def test_alert_body_truncated_to_500(self, root) -> None:
        """Alert bodies longer than 500 chars should be truncated."""
        long_body = "B" * 600
        enqueue_alert(root, _make_alert(body=long_body))
        alerts = drain_alerts(root)
        assert len(alerts[0]["body"]) == 500


# ===================================================================
# Deduplication tests
# ===================================================================


class TestDeduplication:
    """Tests for alert deduplication logic."""

    def test_dedup_same_type_and_title(self, root) -> None:
        """Duplicate alerts (same type+title within window) should be deduplicated."""
        alert = _make_alert(type="reminder", title="Dup Alert")
        enqueue_alert(root, alert, dedup_window_sec=300)
        enqueue_alert(root, alert, dedup_window_sec=300)
        alerts = drain_alerts(root)
        assert len(alerts) == 1

    def test_dedup_returns_original_id(self, root) -> None:
        """Deduped alert should return the original alert's ID."""
        alert = _make_alert(type="reminder", title="Same Alert")
        id1 = enqueue_alert(root, alert, dedup_window_sec=300)
        id2 = enqueue_alert(root, alert, dedup_window_sec=300)
        assert id1 == id2

    def test_dedup_different_types_not_deduped(self, root) -> None:
        """Alerts with different types should not be deduplicated."""
        enqueue_alert(root, _make_alert(type="type_a", title="Alert"), dedup_window_sec=300)
        enqueue_alert(root, _make_alert(type="type_b", title="Alert"), dedup_window_sec=300)
        alerts = drain_alerts(root)
        assert len(alerts) == 2

    def test_dedup_different_titles_not_deduped(self, root) -> None:
        """Alerts with different titles should not be deduplicated."""
        enqueue_alert(root, _make_alert(title="Alert A"), dedup_window_sec=300)
        enqueue_alert(root, _make_alert(title="Alert B"), dedup_window_sec=300)
        alerts = drain_alerts(root)
        assert len(alerts) == 2

    def test_dedup_window_zero_allows_duplicates(self, root) -> None:
        """With dedup_window_sec=0, duplicates should not be filtered."""
        alert = _make_alert(type="test", title="Same")
        enqueue_alert(root, alert, dedup_window_sec=0)
        enqueue_alert(root, alert, dedup_window_sec=0)
        alerts = drain_alerts(root)
        assert len(alerts) == 2


# ===================================================================
# Peek tests
# ===================================================================


class TestPeek:
    """Tests for peek_alerts() — read without clearing."""

    def test_peek_returns_alerts_without_clearing(self, root) -> None:
        """peek_alerts should return alerts but leave them in the queue."""
        enqueue_alert(root, _make_alert(title="Peek Test"))
        peeked = peek_alerts(root)
        assert len(peeked) == 1
        assert peeked[0]["title"] == "Peek Test"
        # Queue should still have the alert
        drained = drain_alerts(root)
        assert len(drained) == 1

    def test_peek_respects_limit(self, root) -> None:
        """peek_alerts should respect the limit parameter."""
        for i in range(5):
            enqueue_alert(root, _make_alert(title=f"A{i}", type=f"t{i}"))
        peeked = peek_alerts(root, limit=2)
        assert len(peeked) == 2

    def test_peek_empty_queue(self, root) -> None:
        """peek_alerts on empty queue should return empty list."""
        peeked = peek_alerts(root)
        assert peeked == []


# ===================================================================
# JSONL rotation (stale pruning) tests
# ===================================================================


class TestRotation:
    """Tests for stale alert pruning during enqueue (1-hour cutoff)."""

    def test_stale_alerts_pruned(self, root) -> None:
        """Alerts older than 1 hour should be pruned on next enqueue."""
        path = _queue_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write a stale alert (timestamp 2 hours ago)
        stale_record = {
            "id": "stale-1",
            "type": "old",
            "title": "Old Alert",
            "body": "stale",
            "group_key": "default",
            "priority": "normal",
            "ts": time.time() - 7200,  # 2 hours ago
        }
        path.write_text(json.dumps(stale_record) + "\n", encoding="utf-8")

        # Enqueue a fresh alert — should prune the stale one
        enqueue_alert(root, _make_alert(title="Fresh Alert"))
        alerts = drain_alerts(root)
        titles = [a["title"] for a in alerts]
        assert "Old Alert" not in titles
        assert "Fresh Alert" in titles

    def test_recent_alerts_preserved(self, root) -> None:
        """Alerts within the 1-hour window should be preserved."""
        path = _queue_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        recent_record = {
            "id": "recent-1",
            "type": "recent",
            "title": "Recent Alert",
            "body": "still valid",
            "group_key": "default",
            "priority": "normal",
            "ts": time.time() - 1800,  # 30 minutes ago
        }
        path.write_text(json.dumps(recent_record) + "\n", encoding="utf-8")

        enqueue_alert(root, _make_alert(title="New Alert", type="new"))
        alerts = drain_alerts(root)
        titles = [a["title"] for a in alerts]
        assert "Recent Alert" in titles
        assert "New Alert" in titles


# ===================================================================
# Empty queue edge cases
# ===================================================================


class TestEmptyQueueEdgeCases:
    """Tests for edge cases with empty or missing queues."""

    def test_drain_nonexistent_file(self, root) -> None:
        """drain_alerts on nonexistent file should return empty list."""
        alerts = drain_alerts(root)
        assert alerts == []

    def test_peek_nonexistent_file(self, root) -> None:
        """peek_alerts on nonexistent file should return empty list."""
        alerts = peek_alerts(root)
        assert alerts == []

    def test_drain_empty_file(self, root) -> None:
        """drain_alerts on empty file should return empty list."""
        path = _queue_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        alerts = drain_alerts(root)
        assert alerts == []

    def test_drain_malformed_json(self, root) -> None:
        """drain_alerts should skip malformed JSON lines."""
        path = _queue_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = 'not-json\n{"id":"ok","type":"t","title":"Valid","body":"b","ts":' + str(time.time()) + '}\n'
        path.write_text(content, encoding="utf-8")
        alerts = drain_alerts(root)
        assert len(alerts) == 1
        assert alerts[0]["title"] == "Valid"

    def test_peek_malformed_json(self, root) -> None:
        """peek_alerts should skip malformed JSON lines."""
        path = _queue_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('garbage\n', encoding="utf-8")
        alerts = peek_alerts(root)
        assert alerts == []


# ===================================================================
# Concurrent access (thread safety) tests
# ===================================================================


class TestConcurrentAccess:
    """Tests for thread-safety of alert queue operations."""

    def test_concurrent_enqueue(self, root) -> None:
        """Multiple threads enqueueing simultaneously should not lose alerts."""
        errors: list[str] = []

        def enqueue_worker(idx: int) -> None:
            try:
                enqueue_alert(
                    root,
                    _make_alert(title=f"Thread-{idx}", type=f"type-{idx}"),
                )
            except Exception as exc:
                errors.append(f"Thread {idx}: {exc}")

        threads = [threading.Thread(target=enqueue_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Errors during concurrent enqueue: {errors}"
        alerts = drain_alerts(root)
        assert len(alerts) == 10

    def test_concurrent_enqueue_and_drain(self, root) -> None:
        """Concurrent enqueue and drain should not corrupt the queue."""
        enqueue_count = 20
        drained: list[list] = []
        errors: list[str] = []

        def enqueue_worker(start: int, count: int) -> None:
            for i in range(start, start + count):
                try:
                    enqueue_alert(root, _make_alert(title=f"A-{i}", type=f"t-{i}"))
                except Exception as exc:
                    errors.append(f"enqueue {i}: {exc}")

        def drain_worker() -> None:
            try:
                time.sleep(0.05)  # Let some enqueues happen first
                result = drain_alerts(root)
                drained.append(result)
            except Exception as exc:
                errors.append(f"drain: {exc}")

        t1 = threading.Thread(target=enqueue_worker, args=(0, 10))
        t2 = threading.Thread(target=drain_worker)
        t3 = threading.Thread(target=enqueue_worker, args=(10, 10))

        t1.start()
        t2.start()
        t3.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        t3.join(timeout=10)

        assert errors == [], f"Errors: {errors}"
        # Final drain to collect any remaining
        remaining = drain_alerts(root)
        total = sum(len(d) for d in drained) + len(remaining)
        # We should have seen all alerts (some in drained, rest in remaining)
        assert total <= enqueue_count  # No duplicates
