"""Three-tier memory hierarchy: hot/warm/cold.

Classifies memory records based on recency, access count, and confidence.
Records are promoted or demoted by periodic tier maintenance.

- HOT: Recently created (within 48 hours)
- WARM: Actively used or high-confidence records
- COLD: Old, rarely accessed, low-confidence records
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)


class Tier(Enum):
    """Memory tier classification."""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class TierManager:
    """Classifies and manages memory record tiers."""

    # Records created within this window are HOT
    HOT_WINDOW_HOURS: float = 48.0

    # Records older than this are eligible for COLD
    WARM_MAX_DAYS: int = 90

    # High-confidence records stay WARM regardless of age
    HIGH_CONFIDENCE_THRESHOLD: float = 0.85

    # Frequently accessed records stay WARM regardless of age
    HIGH_ACCESS_THRESHOLD: int = 3

    def classify(self, record: dict) -> Tier:
        """Classify a record into a tier based on recency, access, and confidence.

        Args:
            record: Dict with at least 'ts', 'access_count', and 'confidence' keys.

        Returns:
            Tier enum value.
        """
        ts_str = str(record.get("ts", "")).strip()
        age_hours = self._compute_age_hours(ts_str)

        # Recent records are HOT
        if age_hours <= self.HOT_WINDOW_HOURS:
            return Tier.HOT

        access_count = int(record.get("access_count", 0))
        confidence = float(record.get("confidence", 0.0))

        # Frequently accessed or high-confidence records stay WARM
        if access_count > self.HIGH_ACCESS_THRESHOLD:
            return Tier.WARM
        if confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
            return Tier.WARM

        # Old records go COLD
        if age_hours > self.WARM_MAX_DAYS * 24:
            return Tier.COLD

        # Default: WARM
        return Tier.WARM

    def run_tier_maintenance(self, engine: "MemoryEngine") -> dict:
        """Iterate all records, classify, and update tier if changed.

        Returns a summary of changes made.
        """
        record_ids = engine.get_all_record_ids()
        changes = {"total": len(record_ids), "promoted": 0, "demoted": 0, "unchanged": 0}

        for rid in record_ids:
            record = engine.get_record(rid)
            if record is None:
                continue

            new_tier = self.classify(record)
            current_tier_str = str(record.get("tier", "warm"))

            if current_tier_str != new_tier.value:
                engine.update_tier(rid, new_tier.value)
                # Promotion = moving to a "hotter" tier
                tier_order = {"cold": 0, "warm": 1, "hot": 2}
                old_order = tier_order.get(current_tier_str, 1)
                new_order = tier_order.get(new_tier.value, 1)
                if new_order > old_order:
                    changes["promoted"] += 1
                else:
                    changes["demoted"] += 1
            else:
                changes["unchanged"] += 1

        logger.info(
            "Tier maintenance complete: %d records, %d promoted, %d demoted",
            changes["total"],
            changes["promoted"],
            changes["demoted"],
        )
        return changes

    @staticmethod
    def _compute_age_hours(ts_str: str) -> float:
        """Compute age in hours from a timestamp string."""
        if not ts_str:
            return float("inf")
        raw = ts_str
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return float("inf")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - parsed.astimezone(UTC)
        return max(0.0, delta.total_seconds() / 3600.0)
