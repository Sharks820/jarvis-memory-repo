"""Tests for the activity_feed module."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jarvis_engine._compat import UTC
from jarvis_engine.activity_feed import (
    ActivityCategory,
    ActivityFeed,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def feed(tmp_path: Path) -> ActivityFeed:
    """Create a temporary ActivityFeed for each test."""
    f = ActivityFeed(db_path=tmp_path / "activity.db")
    yield f
    f.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestActivityFeed:
    def test_log_and_query(self, feed: ActivityFeed) -> None:
        """Logging an event returns an id and query retrieves it."""
        eid = feed.log(ActivityCategory.LLM_ROUTING, "Chose claude-sonnet", {"model": "sonnet"})
        assert isinstance(eid, str) and len(eid) == 32  # uuid4 hex

        events = feed.query(limit=10)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_id == eid
        assert ev.category == ActivityCategory.LLM_ROUTING
        assert ev.summary == "Chose claude-sonnet"
        assert ev.details == {"model": "sonnet"}
        # timestamp should be a valid ISO 8601 string
        datetime.fromisoformat(ev.timestamp)

    def test_query_by_category(self, feed: ActivityFeed) -> None:
        """Query filters correctly by category."""
        feed.log(ActivityCategory.ERROR, "Something broke")
        feed.log(ActivityCategory.VOICE, "Heard wake word")
        feed.log(ActivityCategory.ERROR, "Another failure")

        errors = feed.query(category=ActivityCategory.ERROR)
        assert len(errors) == 2
        assert all(e.category == ActivityCategory.ERROR for e in errors)

        voice = feed.query(category=ActivityCategory.VOICE)
        assert len(voice) == 1

    def test_query_since_timestamp(self, feed: ActivityFeed) -> None:
        """Query respects the 'since' ISO timestamp filter."""
        # Insert an event with a timestamp well in the past
        old_ts = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        feed._db.execute(
            "INSERT INTO activity_log (id, timestamp, category, summary, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("old_id", old_ts, ActivityCategory.DAEMON_CYCLE, "old cycle", "{}", time.time() - 18000),
        )
        feed._db.commit()

        # Insert a recent event through the public API
        feed.log(ActivityCategory.DAEMON_CYCLE, "new cycle")

        cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        recent = feed.query(since=cutoff)
        assert len(recent) == 1
        assert recent[0].summary == "new cycle"

        # Without filter, both show up
        all_events = feed.query()
        assert len(all_events) == 2

    def test_auto_prune_old_events(self, feed: ActivityFeed) -> None:
        """clear_old removes events older than keep_days."""
        old_ts = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        feed._db.execute(
            "INSERT INTO activity_log (id, timestamp, category, summary, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ancient", old_ts, ActivityCategory.HARVEST, "old harvest", "{}", time.time() - 86400 * 60),
        )
        feed._db.commit()

        feed.log(ActivityCategory.HARVEST, "fresh harvest")

        deleted = feed.clear_old(keep_days=30)
        assert deleted == 1

        remaining = feed.query()
        assert len(remaining) == 1
        assert remaining[0].summary == "fresh harvest"

    def test_stats_last_24h(self, feed: ActivityFeed) -> None:
        """stats() returns per-category counts for the last 24 hours."""
        feed.log(ActivityCategory.LLM_ROUTING, "route 1")
        feed.log(ActivityCategory.LLM_ROUTING, "route 2")
        feed.log(ActivityCategory.ERROR, "oops")

        # Insert an old event that should NOT count
        old_ts = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
        feed._db.execute(
            "INSERT INTO activity_log (id, timestamp, category, summary, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("stale", old_ts, ActivityCategory.LLM_ROUTING, "ancient route", "{}", time.time() - 86400 * 2),
        )
        feed._db.commit()

        s = feed.stats()
        assert s[ActivityCategory.LLM_ROUTING] == 2
        assert s[ActivityCategory.ERROR] == 1
        assert ActivityCategory.VOICE not in s  # no voice events logged

    def test_thread_safety(self, feed: ActivityFeed) -> None:
        """Concurrent logging from 5 threads must not raise or lose events."""
        errors: list[Exception] = []
        per_thread = 20

        def worker(cat: str) -> None:
            try:
                for i in range(per_thread):
                    feed.log(cat, f"event {i}")
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(exc)

        categories = [
            ActivityCategory.LLM_ROUTING,
            ActivityCategory.FACT_EXTRACTED,
            ActivityCategory.ERROR,
            ActivityCategory.VOICE,
            ActivityCategory.DAEMON_CYCLE,
        ]
        threads = [threading.Thread(target=worker, args=(c,)) for c in categories]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"Thread errors: {errors}"

        total = feed.query(limit=per_thread * len(categories) + 10)
        assert len(total) == per_thread * len(categories)

    def test_max_events_pruning(self, tmp_path: Path) -> None:
        """When event count exceeds max_events, oldest are pruned."""
        max_ev = 10
        f = ActivityFeed(db_path=tmp_path / "prune.db", max_events=max_ev)
        try:
            for i in range(max_ev + 5):
                f.log(ActivityCategory.DAEMON_CYCLE, f"cycle {i}")

            events = f.query(limit=max_ev + 10)
            assert len(events) == max_ev

            # The newest events should survive (highest cycle numbers)
            summaries = {e.summary for e in events}
            # The first 5 events (cycle 0..4) should have been pruned
            for i in range(5):
                assert f"cycle {i}" not in summaries, f"cycle {i} should have been pruned"
            # The last max_ev events should remain
            for i in range(5, max_ev + 5):
                assert f"cycle {i}" in summaries, f"cycle {i} should still exist"
        finally:
            f.close()
