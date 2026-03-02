"""Knowledge graph integrity and growth metrics for anti-regression monitoring."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from jarvis_engine._compat import UTC

logger = logging.getLogger(__name__)


def collect_kg_metrics(kg) -> dict:
    """Collect quantitative metrics from the knowledge graph.

    Args:
        kg: A KnowledgeGraph instance (from jarvis_engine.knowledge.graph)

    Returns dict with:
        - node_count: total KG nodes
        - edge_count: total KG edges
        - branch_counts: dict mapping branch name -> node count
        - cross_branch_edges: count of cross_branch_related edges
        - avg_confidence: average node confidence score
        - confidence_distribution: {high: >0.8, medium: 0.5-0.8, low: <0.5}
        - locked_facts: count of locked (verified) facts
        - expired_facts: count of expired temporal facts
        - temporal_breakdown: {permanent: N, time_sensitive: N, expired: N, unknown: N}
    """
    metrics: dict = {
        "ts": datetime.now(UTC).isoformat(),
        "node_count": 0,
        "edge_count": 0,
        "branch_counts": {},
        "cross_branch_edges": 0,
        "avg_confidence": 0.0,
        "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
        "locked_facts": 0,
        "expired_facts": 0,
        "temporal_breakdown": {"permanent": 0, "time_sensitive": 0, "expired": 0, "unknown": 0},
    }

    try:
        db = kg.db

        # Wrap all metric queries in a single transaction for consistent reads
        db.execute("BEGIN DEFERRED")

        # Node count
        row = db.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()
        metrics["node_count"] = row[0] if row else 0

        # Edge count
        row = db.execute("SELECT COUNT(*) FROM kg_edges").fetchone()
        metrics["edge_count"] = row[0] if row else 0

        # Branch counts (first segment of node_id before first dot)
        rows = db.execute(
            "SELECT SUBSTR(node_id, 1, INSTR(node_id || '.', '.') - 1) as branch, "
            "COUNT(*) FROM kg_nodes GROUP BY branch"
        ).fetchall()
        metrics["branch_counts"] = {r[0]: r[1] for r in rows if r[0]}

        # Cross-branch edges
        row = db.execute(
            "SELECT COUNT(*) FROM kg_edges WHERE relation = 'cross_branch_related'"
        ).fetchone()
        metrics["cross_branch_edges"] = row[0] if row else 0

        # Confidence stats
        row = db.execute(
            "SELECT AVG(confidence), "
            "COUNT(CASE WHEN confidence > 0.8 THEN 1 END), "
            "COUNT(CASE WHEN confidence BETWEEN 0.5 AND 0.8 THEN 1 END), "
            "COUNT(CASE WHEN confidence < 0.5 THEN 1 END) "
            "FROM kg_nodes"
        ).fetchone()
        if row:
            metrics["avg_confidence"] = round(row[0] or 0.0, 3)
            metrics["confidence_distribution"] = {
                "high": row[1],
                "medium": row[2],
                "low": row[3],
            }

        # Locked facts
        row = db.execute(
            "SELECT COUNT(*) FROM kg_nodes WHERE locked = 1"
        ).fetchone()
        metrics["locked_facts"] = row[0] if row else 0

        # Temporal breakdown (temporal_type column may not exist)
        try:
            rows = db.execute(
                "SELECT COALESCE(temporal_type, 'unknown'), COUNT(*) "
                "FROM kg_nodes GROUP BY temporal_type"
            ).fetchall()
            tb = {"permanent": 0, "time_sensitive": 0, "expired": 0, "unknown": 0}
            for r in rows:
                key = r[0] if r[0] in tb else "unknown"
                tb[key] += r[1]
            metrics["temporal_breakdown"] = tb
            metrics["expired_facts"] = tb.get("expired", 0)
        except Exception:
            # Column may not exist if temporal migration has not run
            pass

        db.execute("COMMIT")

    except Exception as exc:
        try:
            db.execute("ROLLBACK")
        except Exception:
            pass
        logger.warning("Failed to collect KG metrics: %s", exc)

    return metrics


def append_kg_metrics(metrics: dict, history_path: Path) -> None:
    """Append KG metrics snapshot to JSONL history file."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(metrics) + "\n")


def load_kg_history(history_path: Path, limit: int = 100) -> list[dict]:
    """Load last N KG metric snapshots."""
    if not history_path.exists():
        return []

    entries: list[dict] = []
    with history_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue

    return entries[-limit:]


def kg_growth_trend(history: list[dict]) -> dict:
    """Analyze KG growth trend from history snapshots.

    Returns dict with trend direction and deltas across the history window.
    """
    if len(history) < 2:
        return {
            "trend": "insufficient_data",
            "node_growth": 0,
            "edge_growth": 0,
            "cross_branch_growth": 0,
            "confidence_change": 0.0,
        }

    first = history[0]
    last = history[-1]

    node_growth = last.get("node_count", 0) - first.get("node_count", 0)
    edge_growth = last.get("edge_count", 0) - first.get("edge_count", 0)
    conf_change = round(
        (last.get("avg_confidence", 0) - first.get("avg_confidence", 0)), 3
    )
    xb_growth = last.get("cross_branch_edges", 0) - first.get("cross_branch_edges", 0)

    if node_growth > 0 and edge_growth > 0:
        trend = "growing"
    elif node_growth == 0 and edge_growth == 0:
        trend = "stable"
    else:
        trend = "declining"

    return {
        "trend": trend,
        "node_growth": node_growth,
        "edge_growth": edge_growth,
        "cross_branch_growth": xb_growth,
        "confidence_change": conf_change,
        "first_snapshot": first.get("ts", ""),
        "last_snapshot": last.get("ts", ""),
        "snapshots_analyzed": len(history),
    }
