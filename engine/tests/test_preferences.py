"""Tests for PreferenceTracker."""

from __future__ import annotations

import sqlite3

import pytest

from jarvis_engine.learning.preferences import PreferenceTracker


class TestPreferenceTracker:
    """Tests for user preference extraction and storage."""

    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        yield conn
        conn.close()

    @pytest.fixture
    def tracker(self, db):
        return PreferenceTracker(db=db)

    def test_schema_creation(self, db, tracker):
        """Table user_preferences is created on init."""
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'"
        )
        assert cur.fetchone() is not None

    def test_schema_idempotent(self, db):
        """Creating tracker twice does not raise."""
        PreferenceTracker(db=db)
        PreferenceTracker(db=db)

    def test_observe_detects_concise_style(self, tracker):
        """'tldr' triggers communication_style=concise."""
        detected = tracker.observe("give me the tldr version")
        assert ("communication_style", "concise") in detected

    def test_observe_detects_verbose_style(self, tracker):
        """'explain in detail' triggers communication_style=verbose."""
        detected = tracker.observe("please explain in detail how this works")
        assert ("communication_style", "verbose") in detected

    def test_observe_detects_morning_person(self, tracker):
        """'morning routine' triggers time_preferences=morning_person."""
        detected = tracker.observe("what's on my morning routine today?")
        assert ("time_preferences", "morning_person") in detected

    def test_observe_detects_list_format(self, tracker):
        """'bullet points' triggers format_preferences=lists."""
        detected = tracker.observe("give me bullet points for the meeting")
        assert ("format_preferences", "lists") in detected

    def test_observe_detects_code_format(self, tracker):
        """'show me code' triggers format_preferences=code."""
        detected = tracker.observe("show me code for the api endpoint")
        assert ("format_preferences", "code") in detected

    def test_observe_no_match_returns_empty(self, tracker):
        """Regular messages don't trigger any preference."""
        detected = tracker.observe("what is the weather today?")
        assert detected == []

    def test_observe_empty_message(self, tracker):
        """Empty/None messages return empty list."""
        assert tracker.observe("") == []
        assert tracker.observe("   ") == []

    def test_observe_updates_score(self, tracker):
        """Repeated observations increase score."""
        tracker.observe("give me the tldr")
        tracker.observe("short version please")
        prefs = tracker.get_all_preferences()
        concise = [p for p in prefs if p["preference"] == "concise"]
        assert len(concise) == 1
        assert concise[0]["score"] == pytest.approx(1.1)
        assert concise[0]["evidence_count"] == 2

    def test_get_preferences_highest_per_category(self, tracker):
        """get_preferences returns highest-scored preference per category."""
        tracker.observe("give me the tldr")  # concise: 1.0
        tracker.observe("explain in detail how this works")  # verbose: 1.0
        tracker.observe("briefly summarize")  # concise: 1.1
        prefs = tracker.get_preferences()
        assert prefs["communication_style"] == "concise"

    def test_get_all_preferences(self, tracker):
        """get_all_preferences returns full detail list."""
        tracker.observe("give me the tldr")
        all_prefs = tracker.get_all_preferences()
        assert len(all_prefs) == 1
        assert all_prefs[0]["category"] == "communication_style"
        assert all_prefs[0]["preference"] == "concise"
        assert all_prefs[0]["score"] == pytest.approx(1.0)
        assert all_prefs[0]["evidence_count"] == 1

    def test_multiple_categories_detected(self, tracker):
        """A message can trigger preferences in multiple categories."""
        detected = tracker.observe("first thing in the morning give me bullet points for today")
        categories = [cat for cat, _ in detected]
        assert "time_preferences" in categories
        assert "format_preferences" in categories
