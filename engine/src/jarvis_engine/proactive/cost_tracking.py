"""Cost reduction trend tracking via JSONL snapshots.

Tracks local-vs-cloud query ratios over time to show progressive cost reduction
as Jarvis's local knowledge base grows.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path
from typing import Any, TypedDict, cast

logger = logging.getLogger(__name__)


# Functional-form TypedDict because keys like "7d_local_pct" start with a digit.
CostSnapshot = TypedDict(
    "CostSnapshot",
    {
        "date": str,
        # 7-day window
        "7d_local_pct": float,
        "7d_cloud_cost_usd": float,
        "7d_failed_count": int,
        "7d_failed_cost_usd": float,
        "7d_total_queries": int,
        # 30-day window
        "30d_local_pct": float,
        "30d_cloud_cost_usd": float,
        "30d_failed_count": int,
        "30d_failed_cost_usd": float,
        "30d_total_queries": int,
    },
)


class CostTrend(TypedDict):
    """Trend analysis across cost history snapshots."""

    first_date: str
    last_date: str
    first_local_pct: float
    last_local_pct: float
    change_pct: float
    trend: str


def cost_reduction_snapshot(cost_tracker: Any, history_path: Path) -> CostSnapshot:
    """Compute 7d and 30d local-vs-cloud summaries and append to JSONL history.

    Returns a snapshot dict with date, local_pct, cloud_cost_usd, failed metrics,
    and total_queries for both 7-day and 30-day windows.
    """
    try:
        summary_7d = cost_tracker.local_vs_cloud_summary(days=7)
        summary_30d = cost_tracker.local_vs_cloud_summary(days=30)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Cost tracker summary failed: %s", exc)
        summary_7d = {
            "local_pct": 0.0,
            "cloud_cost_usd": 0.0,
            "failed_count": 0,
            "failed_cost_usd": 0.0,
            "total_count": 0,
        }
        summary_30d = {
            "local_pct": 0.0,
            "cloud_cost_usd": 0.0,
            "failed_count": 0,
            "failed_cost_usd": 0.0,
            "total_count": 0,
        }

    snapshot: CostSnapshot = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "7d_local_pct": round(float(summary_7d.get("local_pct", 0.0)), 4),
        "30d_local_pct": round(float(summary_30d.get("local_pct", 0.0)), 4),
        "7d_cloud_cost_usd": round(float(summary_7d.get("cloud_cost_usd", 0.0)), 6),
        "30d_cloud_cost_usd": round(float(summary_30d.get("cloud_cost_usd", 0.0)), 6),
        "7d_failed_count": int(summary_7d.get("failed_count", 0) or 0),
        "30d_failed_count": int(summary_30d.get("failed_count", 0) or 0),
        "7d_failed_cost_usd": round(float(summary_7d.get("failed_cost_usd", 0.0)), 6),
        "30d_failed_cost_usd": round(float(summary_30d.get("failed_cost_usd", 0.0)), 6),
        "7d_total_queries": int(summary_7d.get("total_count", 0) or 0),
        "30d_total_queries": int(summary_30d.get("total_count", 0) or 0),
    }

    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=True) + "\n")

    return cast(CostSnapshot, snapshot)


def load_cost_history(history_path: Path, limit: int = 90) -> list[dict]:
    """Read the last N snapshot entries from the JSONL history file."""
    from jarvis_engine._shared import load_jsonl_tail

    return load_jsonl_tail(history_path, limit=limit)


def cost_reduction_trend(history: list[dict]) -> CostTrend:
    """Compute trend from cost history snapshots.

    Compares first and last entry's 30d_local_pct to determine if cost reduction
    is improving, stable, or declining.

    Returns dict with: first_date, last_date, first_local_pct, last_local_pct,
    change_pct, trend.
    """
    if not history:
        return {
            "first_date": "",
            "last_date": "",
            "first_local_pct": 0.0,
            "last_local_pct": 0.0,
            "change_pct": 0.0,
            "trend": "stable",
        }

    first = history[0]
    last = history[-1]
    first_pct = float(first.get("30d_local_pct", 0.0))
    last_pct = float(last.get("30d_local_pct", 0.0))
    change = round(last_pct - first_pct, 1)

    if change > 2.0:
        trend = "improving"
    elif change < -2.0:
        trend = "declining"
    else:
        trend = "stable"

    return {
        "first_date": first.get("date", ""),
        "last_date": last.get("date", ""),
        "first_local_pct": first_pct,
        "last_local_pct": last_pct,
        "change_pct": change,
        "trend": trend,
    }

