"""Tests for LEARN-05, LEARN-06, LEARN-07, LEARN-08: relevance in search,
tier management in consolidator, and learning metrics in dashboard."""

from __future__ import annotations

import math
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock


from jarvis_engine._compat import UTC


# ---------------------------------------------------------------------------
# LEARN-05: Frequency boost in hybrid_search
# ---------------------------------------------------------------------------


class TestHybridSearchFrequencyBoost:
    """Verify that access_count influences search scoring."""

    def test_frequency_boost_math(self):
        """Records with higher access_count get a larger boost factor."""
        # The formula: boosted *= (0.9 + 0.2 * min(log1p(access_count)/log1p(10), 1.0))
        # access_count=0: factor = 0.9 + 0.2 * 0.0 = 0.9
        # access_count=10: factor = 0.9 + 0.2 * 1.0 = 1.1
        # access_count=100: factor = 0.9 + 0.2 * min(log1p(100)/log1p(10), 1.0) = capped at 1.1

        factor_0 = 0.9 + 0.2 * min(math.log1p(0) / math.log1p(10), 1.0)
        factor_10 = 0.9 + 0.2 * min(math.log1p(10) / math.log1p(10), 1.0)
        factor_100 = 0.9 + 0.2 * min(math.log1p(100) / math.log1p(10), 1.0)

        assert abs(factor_0 - 0.9) < 0.001
        assert abs(factor_10 - 1.1) < 0.001
        assert factor_100 >= 1.1  # capped

    def test_hybrid_search_applies_frequency_boost(self):
        """High-access records score higher than zero-access records."""
        from jarvis_engine.memory.search import hybrid_search

        engine = MagicMock()
        engine._closed = False

        # FTS returns two records with same rank
        engine.search_fts.return_value = [("rec_high", 1.0), ("rec_low", 1.0)]
        engine.search_vec.return_value = [("rec_high", 0.1), ("rec_low", 0.1)]

        now = datetime.now(UTC).isoformat()
        engine.get_records_batch.return_value = [
            {"record_id": "rec_high", "ts": now, "access_count": 50},
            {"record_id": "rec_low", "ts": now, "access_count": 0},
        ]
        engine.update_access_batch.return_value = None

        results = hybrid_search(engine, "test", [0.1, 0.2], k=10)

        assert len(results) == 2
        # The high-access record should appear first
        assert results[0]["record_id"] == "rec_high"
        assert results[1]["record_id"] == "rec_low"

    def test_hybrid_search_graceful_with_missing_access_count(self):
        """Records missing access_count field still work (default 0)."""
        from jarvis_engine.memory.search import hybrid_search

        engine = MagicMock()
        engine._closed = False

        engine.search_fts.return_value = [("rec_1", 1.0)]
        engine.search_vec.return_value = []

        now = datetime.now(UTC).isoformat()
        engine.get_records_batch.return_value = [
            {"record_id": "rec_1", "ts": now},  # No access_count key
        ]
        engine.update_access_batch.return_value = None

        results = hybrid_search(engine, "test", [0.1], k=5)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# LEARN-06: Tier update in MemoryConsolidator
# ---------------------------------------------------------------------------


class TestConsolidatorTierUpdate:
    """Verify _update_tiers classifies records by relevance."""

    def _make_consolidator(self, engine=None):
        from jarvis_engine.learning.consolidator import MemoryConsolidator
        return MemoryConsolidator(engine or MagicMock())

    def test_update_tiers_hot(self):
        """Record with high access + recent access -> hot tier."""
        mock_engine = MagicMock()
        mock_engine.update_tiers_batch.return_value = None

        consolidator = self._make_consolidator(mock_engine)

        now = datetime.now(UTC).isoformat()
        records = [
            {
                "record_id": "rec_hot",
                "access_count": 50,
                "last_accessed": now,
                "ts": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
                "tier": "cold",
            },
        ]

        changed = consolidator._update_tiers(records)
        assert changed == 1
        # Verify update_tiers_batch was called with ("rec_hot", "hot")
        mock_engine.update_tiers_batch.assert_called_once()
        batch = mock_engine.update_tiers_batch.call_args[0][0]
        assert len(batch) == 1
        assert batch[0] == ("rec_hot", "hot")

    def test_update_tiers_archive(self):
        """Record with 0 access + very old -> archive tier."""
        mock_engine = MagicMock()
        mock_engine.update_tiers_batch.return_value = None

        consolidator = self._make_consolidator(mock_engine)

        old_date = (datetime.now(UTC) - timedelta(days=365)).isoformat()
        records = [
            {
                "record_id": "rec_archive",
                "access_count": 0,
                "last_accessed": old_date,
                "ts": old_date,
                "tier": "warm",
            },
        ]

        changed = consolidator._update_tiers(records)
        assert changed == 1
        batch = mock_engine.update_tiers_batch.call_args[0][0]
        assert len(batch) == 1
        assert batch[0] == ("rec_archive", "archive")

    def test_update_tiers_no_change(self):
        """Record whose tier already matches relevance -> no UPDATE."""
        mock_engine = MagicMock()
        consolidator = self._make_consolidator(mock_engine)

        now = datetime.now(UTC).isoformat()
        # access_count=50, recent access -> hot, tier already "hot"
        records = [
            {
                "record_id": "rec_same",
                "access_count": 50,
                "last_accessed": now,
                "ts": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
                "tier": "hot",
            },
        ]

        changed = consolidator._update_tiers(records)
        assert changed == 0
        # update_tiers_batch should NOT have been called (empty batch)
        mock_engine.update_tiers_batch.assert_not_called()


# ---------------------------------------------------------------------------
# LEARN-07, LEARN-08: Dashboard learning metrics
# ---------------------------------------------------------------------------


class TestDashboardLearningMetrics:
    """Verify build_intelligence_dashboard includes learning and knowledge sections."""

    def _make_tmp_root(self):
        """Create a temporary root dir for dashboard."""
        return Path(tempfile.mkdtemp())

    def test_dashboard_includes_learning_section(self):
        """Dashboard includes 'learning' key with tracker data."""
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard

        mock_feedback = MagicMock()
        mock_feedback.get_all_route_quality.return_value = {
            "routine": {"total": 10, "satisfaction_rate": 0.8},
        }
        mock_pref = MagicMock()
        mock_pref.get_preferences.return_value = {"style": "concise"}
        mock_pref.get_all_preferences.return_value = [{"key": "style", "value": "concise"}]
        mock_usage = MagicMock()
        mock_usage.get_peak_hours.return_value = [(9, 15), (14, 10)]
        mock_usage.get_hourly_distribution.return_value = {9: 15, 14: 10}

        root = self._make_tmp_root()
        dashboard = build_intelligence_dashboard(
            root,
            pref_tracker=mock_pref,
            feedback_tracker=mock_feedback,
            usage_tracker=mock_usage,
        )

        assert "learning" in dashboard
        learning = dashboard["learning"]
        assert "route_quality" in learning
        assert learning["route_quality"]["routine"]["total"] == 10
        assert "preferences" in learning
        assert learning["preferences"]["style"] == "concise"
        assert "peak_hours" in learning
        assert len(learning["peak_hours"]) == 2

    def test_dashboard_includes_knowledge_snapshot(self):
        """Dashboard includes 'knowledge_snapshot' with KG/engine data."""
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard

        mock_kg = MagicMock()
        mock_kg.count_nodes.return_value = 100
        mock_kg.count_edges.return_value = 250
        mock_kg.count_locked.return_value = 5
        mock_kg.db = MagicMock()
        mock_kg.db_lock = MagicMock()
        mock_kg.db.__enter__ = MagicMock(return_value=mock_kg.db)
        mock_kg.db.__exit__ = MagicMock(return_value=False)
        mock_kg.db_lock.__enter__ = MagicMock(return_value=None)
        mock_kg.db_lock.__exit__ = MagicMock(return_value=False)
        # Make the temporal query return empty
        mock_kg.db.execute.return_value.fetchall.return_value = []

        mock_engine = MagicMock()
        mock_engine.db_lock.__enter__ = MagicMock(return_value=None)
        mock_engine.db_lock.__exit__ = MagicMock(return_value=False)
        mock_engine.db.execute.return_value.fetchone.return_value = [42]
        mock_engine.db.execute.return_value.fetchall.return_value = [("general", 30), ("health", 12)]

        root = self._make_tmp_root()
        dashboard = build_intelligence_dashboard(root, kg=mock_kg, engine=mock_engine)

        assert "knowledge_snapshot" in dashboard
        snap = dashboard["knowledge_snapshot"]
        assert snap.get("total_facts") == 100
        assert snap.get("total_edges") == 250

    def test_dashboard_no_trackers_graceful(self):
        """Dashboard works with no trackers (all None)."""
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard

        root = self._make_tmp_root()
        dashboard = build_intelligence_dashboard(root)

        assert "learning" in dashboard
        assert dashboard["learning"] == {}
        assert "knowledge_snapshot" in dashboard
        assert dashboard["knowledge_snapshot"] == {}

    def test_dashboard_tracker_error_graceful(self):
        """Dashboard handles tracker exceptions gracefully."""
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard

        mock_feedback = MagicMock()
        mock_feedback.get_all_route_quality.side_effect = RuntimeError("db error")
        mock_pref = MagicMock()
        mock_pref.get_preferences.side_effect = RuntimeError("db error")
        mock_pref.get_all_preferences.side_effect = RuntimeError("db error")
        mock_usage = MagicMock()
        mock_usage.get_peak_hours.side_effect = RuntimeError("db error")
        mock_usage.get_hourly_distribution.side_effect = RuntimeError("db error")

        root = self._make_tmp_root()
        dashboard = build_intelligence_dashboard(
            root,
            pref_tracker=mock_pref,
            feedback_tracker=mock_feedback,
            usage_tracker=mock_usage,
        )

        assert "learning" in dashboard
        learning = dashboard["learning"]
        assert learning.get("route_quality") == {}
        assert learning.get("preferences") == {}
        assert learning.get("peak_hours") == []
