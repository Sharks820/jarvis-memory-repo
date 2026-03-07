"""Tests for jarvis_engine.memory.search -- hybrid_search and _recency_weight.

Covers:
- _recency_weight: recent timestamp, old timestamp, empty, invalid, Z-suffix
- hybrid_search: RRF combination, recency boost, empty results, k limiting
- Guard checks: None engine, closed engine
- Batch update of access counts for returned results
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from jarvis_engine._compat import UTC
from jarvis_engine.memory.search import _recency_weight, hybrid_search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts_hours_ago(hours: float) -> str:
    dt = datetime.now(UTC) - timedelta(hours=hours)
    return dt.isoformat()


def _make_mock_engine(
    fts_results: list[tuple[str, float]] | None = None,
    vec_results: list[tuple[str, float]] | None = None,
    records: list[dict] | None = None,
    closed: bool = False,
) -> MagicMock:
    """Build a mock MemoryEngine with search and batch methods."""
    engine = MagicMock()
    engine._closed = closed
    engine.search_fts.return_value = fts_results or []
    engine.search_vec.return_value = vec_results or []
    engine.get_records_batch.return_value = records or []
    engine.update_access_batch.return_value = None
    return engine


# ---------------------------------------------------------------------------
# _recency_weight tests
# ---------------------------------------------------------------------------


class TestRecencyWeight:
    def test_recent_timestamp_near_one(self):
        """A just-created timestamp should have weight near 1.0."""
        ts = datetime.now(UTC).isoformat()
        w = _recency_weight(ts)
        assert 0.99 <= w <= 1.0

    def test_old_timestamp_near_zero(self):
        """A timestamp from months ago should have weight near 0.0."""
        ts = _ts_hours_ago(24 * 365)  # 1 year ago
        w = _recency_weight(ts)
        assert w < 0.01

    def test_seven_day_old_half_life(self):
        """At ~168 hours (7 days), weight should be roughly 1/e ≈ 0.368."""
        ts = _ts_hours_ago(168)
        w = _recency_weight(ts)
        assert abs(w - math.exp(-1.0)) < 0.02

    def test_empty_string_returns_zero(self):
        assert _recency_weight("") == 0.0

    def test_invalid_string_returns_zero(self):
        assert _recency_weight("not-a-date") == 0.0

    def test_z_suffix_handled(self):
        """Timestamp with Z suffix is handled correctly."""
        dt = datetime.now(UTC)
        ts_z = dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        w = _recency_weight(ts_z)
        assert 0.99 <= w <= 1.0

    def test_non_string_input_coerced(self):
        """Numeric input is coerced to string (likely returns 0.0)."""
        w = _recency_weight(12345)
        assert w == 0.0


# ---------------------------------------------------------------------------
# hybrid_search guard checks
# ---------------------------------------------------------------------------


class TestHybridSearchGuards:
    def test_none_engine_raises(self):
        """Passing None as engine raises ValueError."""
        with pytest.raises(ValueError, match="MemoryEngine is None"):
            hybrid_search(None, "query", [0.1, 0.2])

    def test_closed_engine_raises(self):
        """Passing a closed engine raises RuntimeError."""
        engine = _make_mock_engine(closed=True)
        with pytest.raises(RuntimeError, match="closed"):
            hybrid_search(engine, "query", [0.1, 0.2])


# ---------------------------------------------------------------------------
# hybrid_search core logic
# ---------------------------------------------------------------------------


class TestHybridSearch:
    def test_empty_results_returns_empty(self):
        """When both FTS and vec return nothing, result is empty list."""
        engine = _make_mock_engine()
        result = hybrid_search(engine, "test", [0.1, 0.2])
        assert result == []

    def test_fts_only_results(self):
        """Records found only via FTS are returned."""
        records = [{"record_id": "r1", "ts": datetime.now(UTC).isoformat()}]
        engine = _make_mock_engine(
            fts_results=[("r1", 1.0)],
            vec_results=[],
            records=records,
        )
        result = hybrid_search(engine, "test", [0.1, 0.2], k=5)
        assert len(result) == 1
        assert result[0]["record_id"] == "r1"

    def test_vec_only_results(self):
        """Records found only via vec search are returned."""
        records = [{"record_id": "r2", "ts": datetime.now(UTC).isoformat()}]
        engine = _make_mock_engine(
            fts_results=[],
            vec_results=[("r2", 0.5)],
            records=records,
        )
        result = hybrid_search(engine, "test", [0.1, 0.2], k=5)
        assert len(result) == 1
        assert result[0]["record_id"] == "r2"

    def test_rrf_boosts_records_in_both_lists(self):
        """Records appearing in both FTS and vec results get higher RRF score."""
        now = datetime.now(UTC).isoformat()
        records = [
            {"record_id": "both", "ts": now},
            {"record_id": "fts_only", "ts": now},
            {"record_id": "vec_only", "ts": now},
        ]
        engine = _make_mock_engine(
            fts_results=[("both", 1.0), ("fts_only", 0.5)],
            vec_results=[("both", 0.9), ("vec_only", 0.4)],
            records=records,
        )
        result = hybrid_search(
            engine, "test", [0.1], k=10, rrf_k=60, recency_weight=0.0
        )
        # "both" should be first since it appears in both lists
        assert result[0]["record_id"] == "both"

    def test_k_limits_results(self):
        """At most k results are returned."""
        now = datetime.now(UTC).isoformat()
        records = [{"record_id": f"r{i}", "ts": now} for i in range(20)]
        fts = [(f"r{i}", float(20 - i)) for i in range(20)]
        engine = _make_mock_engine(
            fts_results=fts,
            vec_results=[],
            records=records,
        )
        result = hybrid_search(engine, "test", [0.1], k=5)
        assert len(result) <= 5

    def test_recency_boost_favors_newer_records(self):
        """With recency_weight > 0, newer records are boosted over older ones."""
        now_ts = datetime.now(UTC).isoformat()
        old_ts = _ts_hours_ago(24 * 30)  # 30 days ago
        records = [
            {"record_id": "new", "ts": now_ts},
            {"record_id": "old", "ts": old_ts},
        ]
        # Give them equal RRF scores by putting them at same rank
        engine = _make_mock_engine(
            fts_results=[("new", 1.0), ("old", 0.9)],
            vec_results=[("old", 0.9), ("new", 1.0)],
            records=records,
        )
        result = hybrid_search(engine, "test", [0.1], k=10, recency_weight=0.5)
        # New record should be ranked first due to recency boost
        assert result[0]["record_id"] == "new"

    def test_no_recency_boost_when_weight_zero(self):
        """With recency_weight=0, recency does not affect ordering."""
        now_ts = datetime.now(UTC).isoformat()
        old_ts = _ts_hours_ago(24 * 30)
        records = [
            {"record_id": "r1", "ts": now_ts},
            {"record_id": "r2", "ts": old_ts},
        ]
        # Give r2 a better rank in FTS (rank 0)
        engine = _make_mock_engine(
            fts_results=[("r2", 1.0), ("r1", 0.5)],
            vec_results=[("r2", 0.9), ("r1", 0.4)],
            records=records,
        )
        result = hybrid_search(engine, "test", [0.1], k=10, recency_weight=0.0)
        # r2 should still be first because of better rank (no recency boost)
        assert result[0]["record_id"] == "r2"

    def test_access_counts_updated_for_returned_records(self):
        """update_access_batch is called with the IDs of returned records."""
        now_ts = datetime.now(UTC).isoformat()
        records = [
            {"record_id": "r1", "ts": now_ts},
            {"record_id": "r2", "ts": now_ts},
        ]
        engine = _make_mock_engine(
            fts_results=[("r1", 1.0), ("r2", 0.9)],
            records=records,
        )
        hybrid_search(engine, "test", [0.1], k=10)
        engine.update_access_batch.assert_called_once()
        called_ids = engine.update_access_batch.call_args[0][0]
        assert set(called_ids) == {"r1", "r2"}

    def test_missing_record_skipped(self):
        """If get_records_batch doesn't return a scored record, it's skipped."""
        engine = _make_mock_engine(
            fts_results=[("r1", 1.0), ("ghost", 0.5)],
            records=[{"record_id": "r1", "ts": datetime.now(UTC).isoformat()}],
        )
        result = hybrid_search(engine, "test", [0.1], k=10)
        assert len(result) == 1
        assert result[0]["record_id"] == "r1"

    def test_search_fts_called_with_3x_k(self):
        """FTS is called with limit = k * 3."""
        engine = _make_mock_engine()
        hybrid_search(engine, "my query", [0.1], k=7)
        engine.search_fts.assert_called_once_with("my query", limit=21)

    def test_search_vec_called_with_3x_k(self):
        """Vec search is called with limit = k * 3."""
        engine = _make_mock_engine()
        hybrid_search(engine, "query", [0.1, 0.2, 0.3], k=5)
        engine.search_vec.assert_called_once_with([0.1, 0.2, 0.3], limit=15)
