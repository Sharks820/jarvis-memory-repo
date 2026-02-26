"""Tests for UsagePatternTracker."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from jarvis_engine._compat import UTC

import pytest

from jarvis_engine.learning.usage_patterns import UsagePatternTracker


class TestUsagePatternTracker:
    """Tests for temporal usage pattern tracking."""

    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        yield conn
        conn.close()

    @pytest.fixture
    def tracker(self, db):
        return UsagePatternTracker(db=db)

    def test_schema_creation(self, db, tracker):
        """Table usage_patterns is created on init."""
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usage_patterns'"
        )
        assert cur.fetchone() is not None

    def test_schema_idempotent(self, db):
        """Creating tracker twice does not raise."""
        UsagePatternTracker(db=db)
        UsagePatternTracker(db=db)

    def test_record_interaction(self, db, tracker):
        """Interactions are stored with correct hour and day_of_week."""
        ts = datetime(2026, 2, 26, 9, 30, 0, tzinfo=UTC)  # Thursday=3
        tracker.record_interaction(route="routine", topic="calendar", timestamp=ts)
        cur = db.execute("SELECT hour, day_of_week, route, topic FROM usage_patterns")
        row = cur.fetchone()
        assert row[0] == 9
        assert row[1] == 3  # Thursday
        assert row[2] == "routine"
        assert row[3] == "calendar"

    def test_record_multiple_interactions(self, db, tracker):
        """Multiple interactions are stored."""
        for i in range(5):
            ts = datetime(2026, 2, 26, 9 + i, 0, 0, tzinfo=UTC)
            tracker.record_interaction(route=f"route_{i}", timestamp=ts)
        cur = db.execute("SELECT COUNT(*) FROM usage_patterns")
        assert cur.fetchone()[0] == 5

    def test_predict_context_with_data(self, tracker):
        """Prediction returns most common route and topics for time slot."""
        ts_base = datetime(2026, 2, 26, 9, 0, 0, tzinfo=UTC)  # Thursday 9am
        # Record several interactions at Thursday 9am
        tracker.record_interaction(route="routine", topic="calendar", timestamp=ts_base)
        tracker.record_interaction(route="routine", topic="email", timestamp=ts_base)
        tracker.record_interaction(route="complex", topic="code", timestamp=ts_base)

        ctx = tracker.predict_context(hour=9, day_of_week=3)
        assert ctx["likely_route"] == "routine"
        assert "calendar" in ctx["common_topics"]
        assert ctx["interaction_count"] == 3

    def test_predict_context_no_data(self, tracker):
        """Prediction with no data returns empty defaults."""
        ctx = tracker.predict_context(hour=3, day_of_week=0)
        assert ctx["likely_route"] == ""
        assert ctx["common_topics"] == []
        assert ctx["interaction_count"] == 0

    def test_get_hourly_distribution(self, tracker):
        """Hourly distribution shows count per hour."""
        for h in [9, 9, 9, 14, 14, 20]:
            ts = datetime(2026, 2, 26, h, 0, 0, tzinfo=UTC)
            tracker.record_interaction(route="test", timestamp=ts)
        dist = tracker.get_hourly_distribution()
        assert dist[9] == 3
        assert dist[14] == 2
        assert dist[20] == 1

    def test_get_peak_hours(self, tracker):
        """Peak hours returns top N busiest hours."""
        for h in [9, 9, 9, 14, 14, 20]:
            ts = datetime(2026, 2, 26, h, 0, 0, tzinfo=UTC)
            tracker.record_interaction(route="test", timestamp=ts)
        peaks = tracker.get_peak_hours(top_n=2)
        assert peaks[0] == 9  # Most interactions
        assert peaks[1] == 14
        assert len(peaks) == 2

    def test_get_peak_hours_empty(self, tracker):
        """Peak hours on empty DB returns empty list."""
        peaks = tracker.get_peak_hours()
        assert peaks == []

    def test_record_default_timestamp(self, db, tracker):
        """Recording without timestamp uses current time."""
        tracker.record_interaction(route="test")
        cur = db.execute("SELECT COUNT(*) FROM usage_patterns")
        assert cur.fetchone()[0] == 1
