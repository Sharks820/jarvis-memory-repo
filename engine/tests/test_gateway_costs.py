"""Tests for engine/src/jarvis_engine/gateway/costs.py — CostTracker."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis_engine.gateway.costs import CostTracker


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_tracker(tmp_path: Path) -> CostTracker:
    return CostTracker(tmp_path / "costs.db")


# ── initialisation ──────────────────────────────────────────────────────────

class TestInit:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_file = tmp_path / "costs.db"
        assert not db_file.exists()
        tracker = _make_tracker(tmp_path)
        assert db_file.exists()
        tracker.close()

    def test_schema_has_query_costs_table(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        cur = tracker._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='query_costs'"
        )
        assert cur.fetchone() is not None
        tracker.close()

    def test_schema_idempotent(self, tmp_path: Path) -> None:
        """Calling _init_schema twice must not raise."""
        tracker = _make_tracker(tmp_path)
        tracker._init_schema()  # second call
        tracker.close()

    def test_context_manager(self, tmp_path: Path) -> None:
        with CostTracker(tmp_path / "costs.db") as tracker:
            tracker.log("m", "p", 10, 10, cost_usd=0.001)
        # connection should be closed — a new tracker on the same file should work
        with CostTracker(tmp_path / "costs.db") as t2:
            s = t2.summary(days=1)
            assert s["total_cost_usd"] > 0


# ── log() ───────────────────────────────────────────────────────────────────

class TestLog:
    def test_log_explicit_cost(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.log("claude-sonnet", "anthropic", 100, 50, cost_usd=0.123)
        cur = tracker._db.execute("SELECT cost_usd FROM query_costs")
        assert cur.fetchone()["cost_usd"] == pytest.approx(0.123)
        tracker.close()

    def test_log_auto_cost(self, tmp_path: Path) -> None:
        """When cost_usd is None, calculate_cost is used."""
        tracker = _make_tracker(tmp_path)
        with patch("jarvis_engine.gateway.costs.calculate_cost", return_value=0.042) as mock_cc:
            tracker.log("claude-haiku", "anthropic", 200, 100)
            mock_cc.assert_called_once_with("claude-haiku", 200, 100)
        cur = tracker._db.execute("SELECT cost_usd FROM query_costs")
        assert cur.fetchone()["cost_usd"] == pytest.approx(0.042)
        tracker.close()

    def test_log_stores_all_fields(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.log(
            model="claude-opus",
            provider="anthropic",
            input_tokens=500,
            output_tokens=200,
            cost_usd=1.5,
            route_reason="complexity",
            fallback_used=True,
            query_hash="abc123",
        )
        row = tracker._db.execute("SELECT * FROM query_costs").fetchone()
        assert row["model"] == "claude-opus"
        assert row["provider"] == "anthropic"
        assert row["input_tokens"] == 500
        assert row["output_tokens"] == 200
        assert row["cost_usd"] == pytest.approx(1.5)
        assert row["route_reason"] == "complexity"
        assert row["fallback_used"] == 1
        assert row["query_hash"] == "abc123"
        tracker.close()

    def test_log_defaults(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.log("model", "prov", 10, 5, cost_usd=0.0)
        row = tracker._db.execute("SELECT * FROM query_costs").fetchone()
        assert row["route_reason"] == ""
        assert row["fallback_used"] == 0
        assert row["query_hash"] == ""
        tracker.close()


# ── summary() ───────────────────────────────────────────────────────────────

class TestSummary:
    def test_empty_db(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        s = tracker.summary()
        assert s["period_days"] == 30
        assert s["models"] == []
        assert s["total_cost_usd"] == 0.0
        tracker.close()

    def test_single_model(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.log("claude-haiku", "anthropic", 100, 50, cost_usd=0.01)
        tracker.log("claude-haiku", "anthropic", 200, 80, cost_usd=0.02)
        s = tracker.summary(days=1)
        assert len(s["models"]) == 1
        m = s["models"][0]
        assert m["model"] == "claude-haiku"
        assert m["count"] == 2
        assert m["input_tokens"] == 300
        assert m["output_tokens"] == 130
        assert m["cost_usd"] == pytest.approx(0.03)
        assert s["total_cost_usd"] == pytest.approx(0.03)
        tracker.close()

    def test_multiple_models_sorted_by_cost(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.log("cheap-model", "ollama", 100, 50, cost_usd=0.001)
        tracker.log("expensive-model", "anthropic", 100, 50, cost_usd=1.0)
        s = tracker.summary()
        assert s["models"][0]["model"] == "expensive-model"
        assert s["models"][1]["model"] == "cheap-model"
        tracker.close()

    def test_days_clamped_low(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        s = tracker.summary(days=-5)
        assert s["period_days"] == 1
        tracker.close()

    def test_days_clamped_high(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        s = tracker.summary(days=999999)
        assert s["period_days"] == 3650
        tracker.close()


# ── local_vs_cloud_summary() ───────────────────────────────────────────────

class TestLocalVsCloud:
    def test_empty(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        s = tracker.local_vs_cloud_summary()
        assert s["local_count"] == 0
        assert s["cloud_count"] == 0
        assert s["total_count"] == 0
        assert s["local_pct"] == 0.0
        assert s["cloud_cost_usd"] == 0.0
        tracker.close()

    def test_mixed_local_and_cloud(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.log("phi3", "ollama", 500, 200, cost_usd=0.0)
        tracker.log("phi3", "ollama", 300, 100, cost_usd=0.0)
        tracker.log("claude-haiku", "anthropic", 100, 50, cost_usd=0.05)
        s = tracker.local_vs_cloud_summary(days=1)
        assert s["local_count"] == 2
        assert s["cloud_count"] == 1
        assert s["total_count"] == 3
        assert s["local_pct"] == pytest.approx(66.7)
        assert s["cloud_cost_usd"] == pytest.approx(0.05)
        tracker.close()

    def test_all_local(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.log("phi3", "ollama", 100, 50, cost_usd=0.0)
        s = tracker.local_vs_cloud_summary()
        assert s["local_pct"] == 100.0
        assert s["cloud_cost_usd"] == 0.0
        tracker.close()


# ── thread safety ───────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_logs(self, tmp_path: Path) -> None:
        """Multiple threads logging concurrently must not raise."""
        tracker = _make_tracker(tmp_path)
        errors: list[Exception] = []

        def _log_n(n: int) -> None:
            try:
                for i in range(n):
                    tracker.log(f"model-{i}", "prov", i, i, cost_usd=float(i))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_log_n, args=(20,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        cur = tracker._db.execute("SELECT COUNT(*) as cnt FROM query_costs")
        assert cur.fetchone()["cnt"] == 80
        tracker.close()


# ── close / cleanup ─────────────────────────────────────────────────────────

class TestClose:
    def test_double_close_no_error(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.close()
        tracker.close()  # should not raise

    def test_del_no_error(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        tracker.__del__()  # explicit call — should not raise
