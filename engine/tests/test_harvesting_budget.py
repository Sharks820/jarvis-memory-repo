"""Tests for budget manager limits and enforcement.

Covers table creation, default budgets, spend tracking, daily/monthly limits,
request count limits (Gemini), thread safety, close/cleanup, and handler integration.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jarvis_engine.harvesting.budget import BudgetManager, _DEFAULT_BUDGETS
from jarvis_engine.handlers.harvest_handlers import HarvestBudgetHandler, HarvestHandler
from jarvis_engine.commands.harvest_commands import (
    HarvestBudgetCommand,
    HarvestTopicCommand,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_bm(tmp_path: Path) -> BudgetManager:
    """BudgetManager with defaults cleared so tests control all budgets."""
    manager = BudgetManager(tmp_path / "empty_budget.db")
    manager._db.execute("DELETE FROM harvest_budgets")
    manager._db.commit()
    yield manager
    manager.close()


# ---------------------------------------------------------------------------
# BudgetManager tests
# ---------------------------------------------------------------------------


class TestBudgetManagerSchema:
    """Tests for table creation and default budgets."""

    def test_creates_tables(self, tmp_path):
        """BudgetManager creates harvest_budgets and harvest_spend tables."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)

        # Check tables exist
        tables = bm._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        assert "harvest_budgets" in table_names
        assert "harvest_spend" in table_names
        bm.close()

    def test_default_budgets_set_on_init(self, tmp_path):
        """Default budgets for minimax, kimi, gemini, kimi_nvidia are inserted."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)

        rows = bm._db.execute(
            "SELECT provider, period, limit_usd, limit_requests FROM harvest_budgets ORDER BY provider, period"
        ).fetchall()

        providers_seen = set()
        for row in rows:
            providers_seen.add(row["provider"])

        assert "minimax" in providers_seen
        assert "kimi" in providers_seen
        assert "gemini" in providers_seen
        assert "kimi_nvidia" in providers_seen

        # Verify specific limits
        gemini_row = bm._db.execute(
            "SELECT limit_usd, limit_requests FROM harvest_budgets WHERE provider='gemini' AND period='daily'"
        ).fetchone()
        assert gemini_row["limit_usd"] == 0.0
        assert gemini_row["limit_requests"] == 50

        bm.close()

    def test_default_budgets_not_overwritten_on_reinit(self, tmp_path):
        """Re-initializing BudgetManager on existing DB does not overwrite custom budgets."""
        db_path = tmp_path / "test.db"
        bm1 = BudgetManager(db_path)
        bm1.set_budget("minimax", "daily", 5.00)
        bm1.close()

        bm2 = BudgetManager(db_path)
        row = bm2._db.execute(
            "SELECT limit_usd FROM harvest_budgets WHERE provider='minimax' AND period='daily'"
        ).fetchone()
        assert row["limit_usd"] == 5.00  # Custom value preserved
        bm2.close()


class TestBudgetManagerCanSpend:
    """Tests for budget limit enforcement."""

    def test_returns_true_under_limit(self, tmp_path):
        """No spend recorded: can_spend returns True."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        assert bm.can_spend("minimax") is True
        bm.close()

    def test_returns_false_over_daily_limit(self, tmp_path):
        """Spend exceeding daily limit: can_spend returns False."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        # Default daily limit for minimax is $1.00
        bm.record_spend("minimax", 1.01)
        assert bm.can_spend("minimax") is False
        bm.close()

    def test_returns_false_over_monthly_limit(self, tmp_path):
        """Spend exceeding monthly limit: can_spend returns False."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        # Default monthly limit for minimax is $10.00
        # But daily limit is $1.00, so we need to set a high daily limit first
        bm.set_budget("minimax", "daily", 100.00)
        bm.record_spend("minimax", 10.01)
        assert bm.can_spend("minimax") is False
        bm.close()

    def test_checks_request_count_for_gemini(self, tmp_path):
        """Over 50 daily requests for Gemini: can_spend returns False."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        # Default: gemini daily limit_requests=50
        for i in range(51):
            bm.record_spend("gemini", 0.0, topic=f"topic_{i}")
        assert bm.can_spend("gemini") is False
        bm.close()

    def test_returns_true_for_unknown_provider(self, tmp_path):
        """No budget configured for a provider: can_spend returns True."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        assert bm.can_spend("unknown_provider") is True
        bm.close()


class TestBudgetManagerSpendTracking:
    """Tests for spend recording and summary."""

    def test_set_budget_updates_limit(self, tmp_path):
        """set_budget updates the limit for an existing provider/period."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        bm.set_budget("minimax", "daily", 5.00)

        row = bm._db.execute(
            "SELECT limit_usd FROM harvest_budgets WHERE provider='minimax' AND period='daily'"
        ).fetchone()
        assert row["limit_usd"] == 5.00

        # Verify the new limit is enforced
        bm.record_spend("minimax", 4.99)
        assert bm.can_spend("minimax") is True
        bm.record_spend("minimax", 0.02)
        assert bm.can_spend("minimax") is False
        bm.close()

    def test_record_spend_increments_correctly(self, tmp_path):
        """Multiple spend records sum correctly."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        bm.record_spend("kimi", 0.50, topic="topic_a")
        bm.record_spend("kimi", 0.30, topic="topic_b")

        row = bm._db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM harvest_spend WHERE provider='kimi'"
        ).fetchone()
        assert abs(row["total"] - 0.80) < 1e-10
        bm.close()

    def test_get_spend_summary_returns_per_provider_breakdown(self, tmp_path):
        """Summary groups spend by provider correctly."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        bm.record_spend("minimax", 0.50, topic="topic_1")
        bm.record_spend("kimi", 0.30, topic="topic_2")
        bm.record_spend("minimax", 0.20, topic="topic_3")

        summary = bm.get_spend_summary()
        assert summary["period_days"] == 30

        providers = {p["provider"]: p for p in summary["providers"]}
        assert abs(providers["minimax"]["total_cost_usd"] - 0.70) < 1e-10
        assert abs(providers["kimi"]["total_cost_usd"] - 0.30) < 1e-10
        assert abs(summary["total_cost_usd"] - 1.00) < 1e-10
        bm.close()

    def test_get_spend_summary_filters_by_provider(self, tmp_path):
        """Summary filters by provider when specified."""
        db_path = tmp_path / "test.db"
        bm = BudgetManager(db_path)
        bm.record_spend("minimax", 0.50)
        bm.record_spend("kimi", 0.30)

        summary = bm.get_spend_summary(provider="minimax")
        assert len(summary["providers"]) == 1
        assert summary["providers"][0]["provider"] == "minimax"
        bm.close()


# ---------------------------------------------------------------------------
# Extended BudgetManager tests (added for full coverage)
# ---------------------------------------------------------------------------


class TestSetBudgetValidation:
    """Tests for set_budget validation and edge cases."""

    def test_invalid_period_rejected(self, empty_bm: BudgetManager) -> None:
        with pytest.raises(ValueError, match="Invalid period"):
            empty_bm.set_budget("prov", "yearly", 100.0)

    def test_set_budget_both_periods(self, empty_bm: BudgetManager) -> None:
        empty_bm.set_budget("prov", "daily", 1.0)
        empty_bm.set_budget("prov", "monthly", 10.0)
        rows = empty_bm._db.execute(
            "SELECT period FROM harvest_budgets WHERE provider = 'prov' ORDER BY period"
        ).fetchall()
        periods = [r["period"] for r in rows]
        assert periods == ["daily", "monthly"]

    def test_set_budget_with_request_limit(self, empty_bm: BudgetManager) -> None:
        empty_bm.set_budget("test_prov", "daily", 5.0, limit_requests=100)
        row = empty_bm._db.execute(
            "SELECT * FROM harvest_budgets WHERE provider = ? AND period = ?",
            ("test_prov", "daily"),
        ).fetchone()
        assert row["limit_usd"] == 5.0
        assert row["limit_requests"] == 100


class TestRecordSpendExtended:
    """Extended tests for record_spend."""

    def test_default_topic_empty_string(self, empty_bm: BudgetManager) -> None:
        empty_bm.record_spend("prov", 0.01)
        row = empty_bm._db.execute(
            "SELECT topic FROM harvest_spend WHERE provider = 'prov'"
        ).fetchone()
        assert row["topic"] == ""

    def test_request_count_defaults_to_one(self, empty_bm: BudgetManager) -> None:
        empty_bm.record_spend("prov", 0.10, "topic_a")
        row = empty_bm._db.execute(
            "SELECT request_count FROM harvest_spend WHERE provider = 'prov'"
        ).fetchone()
        assert row["request_count"] == 1


class TestCanSpendExtended:
    """Extended tests for can_spend edge cases."""

    def test_at_exact_budget_blocks(self, empty_bm: BudgetManager) -> None:
        """Spend exactly == limit should block (>= check)."""
        empty_bm.set_budget("prov", "daily", 1.00)
        empty_bm.record_spend("prov", 1.00)
        assert empty_bm.can_spend("prov") is False

    def test_monthly_blocks_independently(self, empty_bm: BudgetManager) -> None:
        """Monthly limit can block even when daily is fine."""
        empty_bm.set_budget("prov", "daily", 10.0)
        empty_bm.set_budget("prov", "monthly", 2.0)
        empty_bm.record_spend("prov", 1.50)
        assert empty_bm.can_spend("prov") is True
        empty_bm.record_spend("prov", 0.60)
        # Monthly total is 2.10 >= 2.0
        assert empty_bm.can_spend("prov") is False

    def test_zero_usd_limit_ignores_cost(self, empty_bm: BudgetManager) -> None:
        """If limit_usd == 0, cost check is skipped."""
        empty_bm.set_budget("prov", "daily", 0.0, limit_requests=10)
        empty_bm.record_spend("prov", 999.0)
        # Only 1 request so far, under the 10 request limit
        assert empty_bm.can_spend("prov") is True


class TestSpendSummaryExtended:
    """Extended tests for get_spend_summary."""

    def test_summary_respects_days_parameter(self, empty_bm: BudgetManager) -> None:
        """Old records outside the day window should be excluded."""
        empty_bm.record_spend("prov", 0.50)
        # Backdate the first record
        empty_bm._db.execute(
            "UPDATE harvest_spend SET ts = datetime('now', '-60 days') WHERE rowid = 1"
        )
        empty_bm._db.commit()
        empty_bm.record_spend("prov", 0.25)
        result = empty_bm.get_spend_summary("prov", days=30)
        assert abs(result["total_cost_usd"] - 0.25) < 1e-9

    def test_empty_summary(self, empty_bm: BudgetManager) -> None:
        result = empty_bm.get_spend_summary()
        assert result["providers"] == []
        assert result["total_cost_usd"] == 0.0

    def test_multi_provider_summary(self, empty_bm: BudgetManager) -> None:
        empty_bm.record_spend("alpha", 1.00)
        empty_bm.record_spend("beta", 2.00)
        result = empty_bm.get_spend_summary()
        assert len(result["providers"]) == 2
        assert abs(result["total_cost_usd"] - 3.00) < 1e-9


class TestDefaultBudgetsContent:
    """Verify default budget configuration values."""

    def test_default_budgets_count(self, tmp_path: Path) -> None:
        bm = BudgetManager(tmp_path / "defaults.db")
        row = bm._db.execute("SELECT COUNT(*) AS cnt FROM harvest_budgets").fetchone()
        assert row["cnt"] == len(_DEFAULT_BUDGETS)
        bm.close()

    def test_defaults_not_reinserted_when_populated(self, tmp_path: Path) -> None:
        """If budgets already exist, defaults are not re-inserted."""
        db_path = tmp_path / "defaults2.db"
        bm1 = BudgetManager(db_path)
        bm1.set_budget("minimax", "daily", 99.0)
        bm1.close()

        bm2 = BudgetManager(db_path)
        row = bm2._db.execute(
            "SELECT limit_usd FROM harvest_budgets WHERE provider='minimax' AND period='daily'"
        ).fetchone()
        assert row["limit_usd"] == 99.0
        bm2.close()


class TestCloseCleanup:
    """Tests for close and __del__ methods."""

    def test_close_is_safe(self, tmp_path: Path) -> None:
        bm = BudgetManager(tmp_path / "close_test.db")
        bm.close()
        # Double close should not raise
        bm.close()
        assert True  # reached without exception

    def test_del_calls_close(self, tmp_path: Path) -> None:
        bm = BudgetManager(tmp_path / "del_test.db")
        bm.__del__()
        assert True  # destructor completed without error


class TestThreadSafety:
    """Tests for concurrent access patterns."""

    def test_concurrent_record_spend(self, tmp_path: Path) -> None:
        """Multiple threads recording spend should not corrupt data."""
        bm = BudgetManager(tmp_path / "thread_test.db")
        errors: list[Exception] = []

        def spend_loop(provider: str, n: int) -> None:
            try:
                for _ in range(n):
                    bm.record_spend(provider, 0.01)
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as e:
                errors.append(e)

        threads = [
            threading.Thread(target=spend_loop, args=("prov", 20)) for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        bm.close()

        assert len(errors) == 0
        db = sqlite3.connect(str(tmp_path / "thread_test.db"))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT COUNT(*) AS cnt FROM harvest_spend").fetchone()
        assert row["cnt"] == 80
        db.close()

    def test_concurrent_can_spend_and_record(self, tmp_path: Path) -> None:
        """can_spend and record_spend concurrently should not deadlock."""
        bm = BudgetManager(tmp_path / "conc_test.db")
        bm.set_budget("prov", "daily", 100.0)
        errors: list[Exception] = []

        def check_loop() -> None:
            try:
                for _ in range(20):
                    bm.can_spend("prov")
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as e:
                errors.append(e)

        def spend_loop() -> None:
            try:
                for _ in range(20):
                    bm.record_spend("prov", 0.01)
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as e:
                errors.append(e)

        t1 = threading.Thread(target=check_loop)
        t2 = threading.Thread(target=spend_loop)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        bm.close()
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------


class TestHarvestHandler:
    """Tests for HarvestHandler command handler."""

    def test_dispatches_correctly(self):
        """HarvestHandler translates HarvestTopicCommand to internal HarvestCommand."""
        mock_harvester = MagicMock()
        mock_harvester.harvest.return_value = {
            "topic": "test topic",
            "results": [
                {
                    "provider": "minimax",
                    "status": "ok",
                    "records_created": 2,
                    "cost_usd": 0.001,
                }
            ],
        }

        handler = HarvestHandler(harvester=mock_harvester)
        cmd = HarvestTopicCommand(topic="test topic", providers=["minimax"])
        result = handler.handle(cmd)

        assert result.topic == "test topic"
        assert result.return_code == 0
        assert len(result.results) == 1
        assert result.results[0]["provider"] == "minimax"

        mock_harvester.harvest.assert_called_once()

    def test_returns_error_without_harvester(self):
        """HarvestHandler returns error result when harvester is None."""
        handler = HarvestHandler(harvester=None)
        cmd = HarvestTopicCommand(topic="test")
        result = handler.handle(cmd)
        assert result.return_code == 2


class TestHarvestBudgetHandler:
    """Tests for HarvestBudgetHandler command handler."""

    def test_status_calls_get_spend_summary(self):
        """Status action calls budget_manager.get_spend_summary."""
        mock_bm = MagicMock()
        mock_bm.get_spend_summary.return_value = {
            "period_days": 30,
            "providers": [],
            "total_cost_usd": 0.0,
        }

        handler = HarvestBudgetHandler(budget_manager=mock_bm)
        cmd = HarvestBudgetCommand(action="status")
        result = handler.handle(cmd)

        assert result.return_code == 0
        mock_bm.get_spend_summary.assert_called_once()

    def test_set_calls_set_budget(self):
        """Set action calls budget_manager.set_budget with correct params."""
        mock_bm = MagicMock()

        handler = HarvestBudgetHandler(budget_manager=mock_bm)
        cmd = HarvestBudgetCommand(
            action="set",
            provider="minimax",
            period="daily",
            limit_usd=2.0,
            limit_requests=0,
        )
        result = handler.handle(cmd)

        assert result.return_code == 0
        mock_bm.set_budget.assert_called_once_with(
            provider="minimax",
            period="daily",
            limit_usd=2.0,
            limit_requests=0,
        )

    def test_returns_error_without_budget_manager(self):
        """Handler returns error when budget_manager is None."""
        handler = HarvestBudgetHandler(budget_manager=None)
        cmd = HarvestBudgetCommand(action="status")
        result = handler.handle(cmd)
        assert result.return_code == 2
