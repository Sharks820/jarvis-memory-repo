"""Three-tier memory hierarchy: hot/warm/cold.

Classifies memory records based on recency, access count, and confidence.
Records are promoted or demoted by periodic tier maintenance.

- HOT: Recently created (within 48 hours)
- WARM: Actively used or high-confidence records
- COLD: Old, rarely accessed, low-confidence records
"""

from __future__ import annotations

import logging
from datetime import datetime
from jarvis_engine._compat import UTC
from enum import Enum
from typing import TYPE_CHECKING, TypedDict

from jarvis_engine._shared import parse_iso_timestamp, safe_float as _safe_float
from jarvis_engine._shared import safe_int as _safe_int

if TYPE_CHECKING:
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)


class TierMaintenanceResult(TypedDict):
    """Result from :meth:`TierManager.run_tier_maintenance`."""

    total: int
    promoted: int
    demoted: int
    unchanged: int


class Tier(Enum):
    """Memory tier classification."""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    ARCHIVE = "archive"


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

        access_count = _safe_int(record.get("access_count", 0))
        confidence = _safe_float(record.get("confidence", 0.0))

        # Frequently accessed or high-confidence records stay WARM
        if access_count >= self.HIGH_ACCESS_THRESHOLD:
            return Tier.WARM
        if confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
            return Tier.WARM

        # Old records go COLD
        if age_hours > self.WARM_MAX_DAYS * 24:
            return Tier.COLD

        # Default: WARM
        return Tier.WARM

    def run_tier_maintenance(self, engine: "MemoryEngine") -> TierMaintenanceResult:
        """Classify all records and batch-update changed tiers.

        Uses a single bulk query to fetch all records, classifies in-memory,
        and batch-updates only the changed tiers in one transaction.
        """
        records = engine.get_all_records_for_tier_maintenance()
        changes = {"total": len(records), "promoted": 0, "demoted": 0, "unchanged": 0}
        tier_order = {"archive": -1, "cold": 0, "warm": 1, "hot": 2}
        updates: list[tuple[str, str]] = []

        for record in records:
            new_tier = self.classify(record)
            current_tier_str = str(record.get("tier", "warm"))

            rid = record.get("record_id", "")
            if not rid:
                continue
            if current_tier_str != new_tier.value:
                updates.append((rid, new_tier.value))
                old_order = tier_order.get(current_tier_str, 1)
                new_order = tier_order.get(new_tier.value, 1)
                if new_order > old_order:
                    changes["promoted"] += 1
                else:
                    changes["demoted"] += 1
            else:
                changes["unchanged"] += 1

        # Single batch update for all tier changes
        if updates:
            engine.update_tiers_batch(updates)

        logger.info(
            "Tier maintenance complete: %d records, %d promoted, %d demoted",
            changes["total"],
            changes["promoted"],
            changes["demoted"],
        )
        return changes

    # Threshold for promoting cold records back to warm when accessed
    COLD_PROMOTION_ACCESS_THRESHOLD: int = 2

    def promote_accessed(self, engine: "MemoryEngine", record_ids: list[str]) -> int:
        """Promote cold-tier records back to warm if they have been accessed enough.

        Checks each record in *record_ids*: if it is currently in the COLD tier
        and its ``access_count`` meets or exceeds
        :attr:`COLD_PROMOTION_ACCESS_THRESHOLD`, it is promoted to WARM.

        Returns the number of records promoted.
        """
        if not record_ids:
            return 0

        records = engine.get_records_batch(record_ids)
        updates: list[tuple[str, str]] = []

        for record in records:
            rid = record.get("record_id", "")
            if not rid:
                continue
            current_tier = str(record.get("tier", "warm"))
            if current_tier != Tier.COLD.value:
                continue
            access_count = _safe_int(record.get("access_count", 0))
            if access_count >= self.COLD_PROMOTION_ACCESS_THRESHOLD:
                updates.append((rid, Tier.WARM.value))

        if updates:
            engine.update_tiers_batch(updates)
            logger.info(
                "Promoted %d cold records to warm (access threshold %d)",
                len(updates),
                self.COLD_PROMOTION_ACCESS_THRESHOLD,
            )

        return len(updates)

    @staticmethod
    def _compute_age_hours(ts_str: str) -> float:
        """Compute age in hours from a timestamp string."""
        parsed = parse_iso_timestamp(ts_str)
        if parsed is None:
            return float("inf")
        delta = datetime.now(UTC) - parsed
        return max(0.0, delta.total_seconds() / 3600.0)
