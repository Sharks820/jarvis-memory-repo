"""Tests for ResponseFeedbackTracker."""

from __future__ import annotations

import sqlite3

import pytest

from jarvis_engine.learning.feedback import ResponseFeedbackTracker


class TestResponseFeedbackTracker:
    """Tests for implicit feedback detection and route quality tracking."""

    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        yield conn
        conn.close()

    @pytest.fixture
    def tracker(self, db):
        return ResponseFeedbackTracker(db=db)

    def test_schema_creation(self, db, tracker):
        """Table response_feedback is created on init."""
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='response_feedback'"
        )
        assert cur.fetchone() is not None

    def test_schema_idempotent(self, db):
        """Creating tracker twice does not raise."""
        t1 = ResponseFeedbackTracker(db=db)
        t2 = ResponseFeedbackTracker(db=db)
        assert t1 is not None
        assert t2 is not None

    def test_detect_negative_correction(self, tracker):
        """Correction signals are detected as negative."""
        assert tracker.detect_feedback("no, i meant the other file") == "negative"
        assert tracker.detect_feedback("that's not what i asked for") == "negative"
        assert tracker.detect_feedback("wrong, try again") == "negative"
        assert tracker.detect_feedback("incorrect answer") == "negative"

    def test_detect_positive_satisfaction(self, tracker):
        """Satisfaction signals are detected as positive."""
        assert tracker.detect_feedback("perfect, that's exactly what I needed") == "positive"
        assert tracker.detect_feedback("great work") == "positive"
        assert tracker.detect_feedback("thanks for the help") == "positive"
        assert tracker.detect_feedback("exactly what I wanted") == "positive"

    def test_detect_neutral(self, tracker):
        """Regular messages return neutral."""
        assert tracker.detect_feedback("what is the weather today?") == "neutral"
        assert tracker.detect_feedback("tell me about python decorators") == "neutral"

    def test_detect_empty_message(self, tracker):
        """Empty messages return neutral."""
        assert tracker.detect_feedback("") == "neutral"
        assert tracker.detect_feedback("   ") == "neutral"

    def test_record_feedback_stores_negative(self, db, tracker):
        """Negative feedback is recorded in the database."""
        result = tracker.record_feedback("no, i meant something else", route="routine")
        assert result == "negative"
        cur = db.execute("SELECT * FROM response_feedback")
        rows = cur.fetchall()
        assert len(rows) == 1

    def test_record_feedback_stores_positive(self, db, tracker):
        """Positive feedback is recorded in the database."""
        result = tracker.record_feedback("perfect answer, thanks!", route="complex")
        assert result == "positive"
        cur = db.execute("SELECT * FROM response_feedback")
        rows = cur.fetchall()
        assert len(rows) == 1

    def test_record_feedback_skips_neutral(self, db, tracker):
        """Neutral feedback is not stored."""
        result = tracker.record_feedback("what time is it?", route="simple")
        assert result == "neutral"
        cur = db.execute("SELECT * FROM response_feedback")
        rows = cur.fetchall()
        assert len(rows) == 0

    def test_get_route_quality(self, tracker):
        """Route quality metrics are computed correctly."""
        tracker.record_feedback("perfect", route="routine")
        tracker.record_feedback("great", route="routine")
        tracker.record_feedback("wrong answer", route="routine")

        quality = tracker.get_route_quality("routine")
        assert quality["positive_count"] == 2
        assert quality["negative_count"] == 1
        assert quality["total"] == 3
        assert quality["satisfaction_rate"] == pytest.approx(2 / 3)

    def test_get_route_quality_empty(self, tracker):
        """Empty route returns zero metrics."""
        quality = tracker.get_route_quality("nonexistent")
        assert quality["total"] == 0
        assert quality["satisfaction_rate"] == 0.0

    def test_get_all_route_quality(self, tracker):
        """All route metrics are returned."""
        tracker.record_feedback("perfect", route="routine")
        tracker.record_feedback("wrong", route="complex")

        all_quality = tracker.get_all_route_quality()
        assert "routine" in all_quality
        assert "complex" in all_quality
        assert all_quality["routine"]["satisfaction_rate"] == 1.0
        assert all_quality["complex"]["satisfaction_rate"] == 0.0

    def test_message_snippet_truncated(self, db, tracker):
        """Long messages are truncated to 200 chars in snippet."""
        long_msg = "wrong " + "x" * 300
        tracker.record_feedback(long_msg, route="test")
        cur = db.execute("SELECT user_message_snippet FROM response_feedback")
        snippet = cur.fetchone()[0]
        assert len(snippet) == 200
