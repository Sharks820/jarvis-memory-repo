"""Tests for jarvis_engine.memory.tiers -- Tier enum and TierManager.

Covers:
- Tier enum values
- Classification: HOT (recent), WARM (active/high-confidence), COLD (old/low-use)
- Edge cases: missing timestamp, invalid timestamp, Z-suffix, None values
- run_tier_maintenance: promotion, demotion, unchanged, batch update
- _compute_age_hours static method
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from jarvis_engine._compat import UTC
from jarvis_engine.memory.tiers import Tier, TierManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts_hours_ago(hours: float) -> str:
    """Return ISO timestamp for N hours in the past."""
    dt = datetime.now(UTC) - timedelta(hours=hours)
    return dt.isoformat()


def _make_record(
    record_id: str = "rec1",
    ts: str | None = None,
    access_count: int = 0,
    confidence: float = 0.5,
    tier: str = "warm",
) -> dict:
    """Create a minimal record dict for tier classification."""
    if ts is None:
        ts = datetime.now(UTC).isoformat()
    return {
        "record_id": record_id,
        "ts": ts,
        "access_count": access_count,
        "confidence": confidence,
        "tier": tier,
    }


# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------


class TestTierEnum:

    def test_hot_value(self):
        assert Tier.HOT.value == "hot"

    def test_warm_value(self):
        assert Tier.WARM.value == "warm"

    def test_cold_value(self):
        assert Tier.COLD.value == "cold"

    def test_tier_members_count(self):
        assert len(Tier) == 3


# ---------------------------------------------------------------------------
# TierManager.classify
# ---------------------------------------------------------------------------


class TestTierManagerClassify:

    def setup_method(self):
        self.mgr = TierManager()

    def test_recent_record_is_hot(self):
        """Record created 1 hour ago should be HOT."""
        rec = _make_record(ts=_ts_hours_ago(1.0))
        assert self.mgr.classify(rec) == Tier.HOT

    def test_just_within_hot_window(self):
        """Record exactly at the HOT window boundary is still HOT."""
        rec = _make_record(ts=_ts_hours_ago(48.0))
        assert self.mgr.classify(rec) == Tier.HOT

    def test_just_outside_hot_window(self):
        """Record just past HOT window becomes WARM by default."""
        rec = _make_record(ts=_ts_hours_ago(49.0), access_count=0, confidence=0.5)
        assert self.mgr.classify(rec) == Tier.WARM

    def test_high_access_count_stays_warm(self):
        """Old record with 3+ accesses stays WARM."""
        rec = _make_record(
            ts=_ts_hours_ago(3000),  # ~125 days
            access_count=3,
            confidence=0.3,
        )
        assert self.mgr.classify(rec) == Tier.WARM

    def test_high_confidence_stays_warm(self):
        """Old record with confidence >= 0.85 stays WARM."""
        rec = _make_record(
            ts=_ts_hours_ago(3000),
            access_count=0,
            confidence=0.85,
        )
        assert self.mgr.classify(rec) == Tier.WARM

    def test_old_low_use_record_is_cold(self):
        """Record older than 90 days with low access and confidence is COLD."""
        rec = _make_record(
            ts=_ts_hours_ago(91 * 24),  # 91 days
            access_count=0,
            confidence=0.3,
        )
        assert self.mgr.classify(rec) == Tier.COLD

    def test_missing_ts_returns_cold(self):
        """Record with empty timestamp gets infinite age -> COLD (if low stats)."""
        rec = _make_record(ts="", access_count=0, confidence=0.3)
        assert self.mgr.classify(rec) == Tier.COLD

    def test_invalid_ts_returns_cold(self):
        """Record with unparseable timestamp gets infinite age -> COLD."""
        rec = _make_record(ts="not-a-date", access_count=0, confidence=0.3)
        assert self.mgr.classify(rec) == Tier.COLD

    def test_z_suffix_timestamp_works(self):
        """Timestamp with Z suffix (UTC) should be parsed correctly."""
        dt = datetime.now(UTC) - timedelta(hours=1)
        ts_z = dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        rec = _make_record(ts=ts_z)
        assert self.mgr.classify(rec) == Tier.HOT

    def test_missing_access_count_defaults_to_zero(self):
        """Missing access_count key treated as 0."""
        rec = {"ts": _ts_hours_ago(100 * 24), "confidence": 0.3}
        # No access_count key at all
        assert self.mgr.classify(rec) == Tier.COLD


# ---------------------------------------------------------------------------
# TierManager._compute_age_hours
# ---------------------------------------------------------------------------


class TestComputeAgeHours:

    def test_empty_string_returns_inf(self):
        assert TierManager._compute_age_hours("") == float("inf")

    def test_valid_recent_timestamp(self):
        ts = datetime.now(UTC).isoformat()
        age = TierManager._compute_age_hours(ts)
        assert 0.0 <= age < 0.01  # Within seconds

    def test_invalid_timestamp_returns_inf(self):
        assert TierManager._compute_age_hours("garbage") == float("inf")

    def test_z_suffix_handled(self):
        ts = "2020-01-01T00:00:00Z"
        age = TierManager._compute_age_hours(ts)
        assert age > 0  # Definitely in the past


# ---------------------------------------------------------------------------
# TierManager.run_tier_maintenance
# ---------------------------------------------------------------------------


class TestRunTierMaintenance:

    def setup_method(self):
        self.mgr = TierManager()

    def test_no_records(self):
        """Maintenance on empty set returns zeros."""
        engine = MagicMock()
        engine.get_all_records_for_tier_maintenance.return_value = []
        result = self.mgr.run_tier_maintenance(engine)
        assert result["total"] == 0
        assert result["promoted"] == 0
        assert result["demoted"] == 0
        engine.update_tiers_batch.assert_not_called()

    def test_all_unchanged(self):
        """Records already in correct tier are unchanged."""
        engine = MagicMock()
        engine.get_all_records_for_tier_maintenance.return_value = [
            _make_record(record_id="r1", ts=_ts_hours_ago(1), tier="hot"),
        ]
        result = self.mgr.run_tier_maintenance(engine)
        assert result["unchanged"] == 1
        assert result["promoted"] == 0
        assert result["demoted"] == 0
        engine.update_tiers_batch.assert_not_called()

    def test_promotion_cold_to_hot(self):
        """A recent record currently marked cold should be promoted to hot."""
        engine = MagicMock()
        engine.get_all_records_for_tier_maintenance.return_value = [
            _make_record(record_id="r1", ts=_ts_hours_ago(1), tier="cold"),
        ]
        result = self.mgr.run_tier_maintenance(engine)
        assert result["promoted"] == 1
        engine.update_tiers_batch.assert_called_once()
        updates = engine.update_tiers_batch.call_args[0][0]
        assert updates == [("r1", "hot")]

    def test_demotion_hot_to_cold(self):
        """An old low-use record marked hot should be demoted to cold."""
        engine = MagicMock()
        engine.get_all_records_for_tier_maintenance.return_value = [
            _make_record(
                record_id="r2",
                ts=_ts_hours_ago(100 * 24),
                access_count=0,
                confidence=0.3,
                tier="hot",
            ),
        ]
        result = self.mgr.run_tier_maintenance(engine)
        assert result["demoted"] == 1
        engine.update_tiers_batch.assert_called_once()
        updates = engine.update_tiers_batch.call_args[0][0]
        assert updates == [("r2", "cold")]

    def test_skips_records_without_record_id(self):
        """Records missing record_id are silently skipped."""
        engine = MagicMock()
        engine.get_all_records_for_tier_maintenance.return_value = [
            {"ts": _ts_hours_ago(1), "access_count": 0, "confidence": 0.5, "tier": "cold"},
        ]
        result = self.mgr.run_tier_maintenance(engine)
        # No record_id so it's skipped entirely
        assert result["promoted"] == 0
        assert result["demoted"] == 0
        assert result["unchanged"] == 0
        engine.update_tiers_batch.assert_not_called()
