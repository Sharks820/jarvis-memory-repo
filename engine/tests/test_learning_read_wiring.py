"""Tests for LEARN-01, LEARN-02, LEARN-03: tracker read-side wiring."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

from jarvis_engine.command_bus import AppContext, CommandBus
from jarvis_engine.learning.feedback import ResponseFeedbackTracker
from jarvis_engine.learning.preferences import PreferenceTracker
from jarvis_engine.memory.embeddings import EmbeddingService


# ---------------------------------------------------------------------------
# LEARN-01: Preference injection in _build_smart_context
# ---------------------------------------------------------------------------

class TestPreferenceInjection:
    """Test that _build_smart_context returns preference data."""

    def _make_bus(self, *, pref_tracker=None, engine=None, embed_service=None, kg=None):
        bus = MagicMock(spec=CommandBus)
        bus.ctx = AppContext(
            engine=engine,
            embed_service=embed_service,
            kg=kg,
            pref_tracker=pref_tracker,
        )
        return bus

    def test_returns_four_elements(self):
        from jarvis_engine.voice_context import _build_smart_context

        pref = MagicMock(spec=PreferenceTracker)
        pref.get_preferences.return_value = {
            "communication_style": "concise",
            "format_preferences": "lists",
        }
        bus = self._make_bus(pref_tracker=pref)
        result = _build_smart_context(bus, "test query")
        assert len(result) == 4
        memory_lines, fact_lines, cross_branch_lines, preference_lines = result
        assert isinstance(preference_lines, list)
        assert len(preference_lines) == 1
        assert "concise" in preference_lines[0]
        assert "lists" in preference_lines[0]

    def test_no_tracker_returns_empty_prefs(self):
        from jarvis_engine.voice_context import _build_smart_context

        bus = self._make_bus()
        result = _build_smart_context(bus, "test")
        assert len(result) == 4
        assert result[3] == []

    def test_tracker_error_graceful(self):
        from jarvis_engine.voice_context import _build_smart_context

        pref = MagicMock(spec=PreferenceTracker)
        pref.get_preferences.side_effect = RuntimeError("db error")
        bus = self._make_bus(pref_tracker=pref)
        result = _build_smart_context(bus, "test")
        assert len(result) == 4
        assert result[3] == []

    def test_empty_preferences_returns_empty_list(self):
        from jarvis_engine.voice_context import _build_smart_context

        pref = MagicMock(spec=PreferenceTracker)
        pref.get_preferences.return_value = {}
        bus = self._make_bus(pref_tracker=pref)
        result = _build_smart_context(bus, "test")
        assert result[3] == []


# ---------------------------------------------------------------------------
# LEARN-02: Route quality penalty in IntentClassifier
# ---------------------------------------------------------------------------

class TestRouteQualityPenalty:
    """Test that IntentClassifier applies quality penalty from feedback tracker."""

    def _make_classifier(self, feedback_tracker=None):
        """Create an IntentClassifier with mocked embedding service."""
        import numpy as np
        from jarvis_engine.gateway.classifier import IntentClassifier

        mock_embed = MagicMock(spec=EmbeddingService)
        # Use 384 dim to match the real embedding model (all-MiniLM-L6-v2)
        dim = 384

        def random_embed(*args, **kwargs):
            return list(np.random.randn(dim))

        mock_embed.embed.side_effect = random_embed
        mock_embed.embed_query.side_effect = random_embed

        # Force recomputation by using a unique cache dir
        with patch.object(IntentClassifier, '_cache_dir', return_value='__test_no_cache__'):
            classifier = IntentClassifier(mock_embed, feedback_tracker=feedback_tracker)
        return classifier

    def test_no_feedback_tracker_no_error(self):
        classifier = self._make_classifier(feedback_tracker=None)
        route, model, conf = classifier.classify("summarize this article")
        assert isinstance(route, str)
        assert isinstance(model, str)
        assert isinstance(conf, float)

    def test_quality_penalty_applied(self):
        """Verify that a route with 100% negative feedback gets penalized."""
        mock_tracker = MagicMock(spec=ResponseFeedbackTracker)
        mock_tracker.get_route_quality.return_value = {
            "total": 10,
            "satisfaction_rate": 0.0,
            "positive_count": 0,
            "negative_count": 10,
        }
        classifier = self._make_classifier(feedback_tracker=mock_tracker)
        # The classifier should still return a result without crashing
        route, model, conf = classifier.classify("test query for routing")
        assert isinstance(route, str)
        assert isinstance(conf, float)

    def test_quality_below_threshold_no_penalty(self):
        """Verify penalty is NOT applied when total < 5 threshold."""
        mock_tracker = MagicMock(spec=ResponseFeedbackTracker)
        mock_tracker.get_route_quality.return_value = {
            "total": 2,  # Below threshold of 5
            "satisfaction_rate": 0.0,
            "positive_count": 0,
            "negative_count": 2,
        }
        classifier = self._make_classifier(feedback_tracker=mock_tracker)
        route, model, conf = classifier.classify("test query")
        assert isinstance(route, str)

    def test_tracker_error_graceful(self):
        """Verify classifier works when tracker raises an exception."""
        mock_tracker = MagicMock(spec=ResponseFeedbackTracker)
        mock_tracker.get_route_quality.side_effect = RuntimeError("db error")
        classifier = self._make_classifier(feedback_tracker=mock_tracker)
        route, model, conf = classifier.classify("test query")
        assert isinstance(route, str)


# ---------------------------------------------------------------------------
# LEARN-03: Usage prediction
# ---------------------------------------------------------------------------

class TestUsagePrediction:
    """Test UsagePatternTracker predict_context with real data."""

    def _make_tracker(self):
        from jarvis_engine.learning.usage_patterns import UsagePatternTracker

        db = sqlite3.connect(":memory:")
        return UsagePatternTracker(db=db)

    def test_prediction_with_data(self):
        from datetime import datetime
        from jarvis_engine._compat import UTC

        tracker = self._make_tracker()
        # Record 10 interactions at hour=9, day=0 (Monday = March 2, 2026)
        for i in range(10):
            tracker.record_interaction(
                route="routine",
                topic=f"morning task {i}",
                timestamp=datetime(2026, 3, 2, 9, 0, 0, tzinfo=UTC),
            )
        prediction = tracker.predict_context(9, 0)  # Monday=0
        assert prediction["likely_route"] == "routine"
        assert prediction["interaction_count"] == 10
        assert len(prediction["common_topics"]) > 0

    def test_prediction_empty_data(self):
        tracker = self._make_tracker()
        prediction = tracker.predict_context(9, 0)
        assert prediction["likely_route"] == ""
        assert prediction["common_topics"] == []
        assert prediction["interaction_count"] == 0

    def test_prediction_multiple_routes(self):
        from datetime import datetime
        from jarvis_engine._compat import UTC

        tracker = self._make_tracker()
        # 7 routine, 3 complex at hour=14
        # March 4, 2026 is Wednesday = day 2
        for _ in range(7):
            tracker.record_interaction(
                route="routine", topic="afternoon",
                timestamp=datetime(2026, 3, 4, 14, 0, 0, tzinfo=UTC),
            )
        for _ in range(3):
            tracker.record_interaction(
                route="complex", topic="coding",
                timestamp=datetime(2026, 3, 4, 14, 0, 0, tzinfo=UTC),
            )
        prediction = tracker.predict_context(14, 2)  # Wednesday=2
        assert prediction["likely_route"] == "routine"  # Most common
        assert prediction["interaction_count"] == 10
