"""Knowledge growth metrics capture.

Provides a snapshot of the current growth state: record counts, fact counts,
branch distribution, and temporal distribution from the knowledge graph.
"""

from __future__ import annotations

import logging
import sqlite3
from jarvis_engine._shared import now_iso as _now_iso
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class KnowledgeMetrics(TypedDict):
    """Snapshot of knowledge growth metrics."""

    total_records: int
    total_facts: int
    total_edges: int
    locked_facts: int
    branches_populated: int
    branch_distribution: dict[str, int]
    temporal_distribution: dict[str, int]
    captured_at: str


def capture_knowledge_metrics(kg: Any, engine: Any) -> KnowledgeMetrics:
    """Capture a snapshot of knowledge growth metrics.

    Args:
        kg: KnowledgeGraph instance (uses count_nodes, count_edges, count_locked, .db).
        engine: MemoryEngine instance (uses ._db for direct SQL on records table).

    Returns:
        Dict with: total_records, total_facts, total_edges, locked_facts,
        branches_populated, branch_distribution, temporal_distribution, captured_at.
    """
    captured_at = _now_iso()

    # -- Record counts from engine --
    total_records = 0
    branch_distribution: dict[str, int] = {}

    if engine is not None:
        try:
            db = engine.db
            db_lock = engine.db_lock
            with db_lock:
                total_records = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]

                rows = db.execute(
                    "SELECT branch, COUNT(*) as cnt FROM records GROUP BY branch"
                ).fetchall()
            for row in rows:
                branch_distribution[row[0]] = row[1]
        except (sqlite3.Error, OSError, AttributeError) as exc:
            logger.warning("Failed to query records for metrics: %s", exc)

    # -- KG counts --
    total_facts = 0
    total_edges = 0
    locked_facts = 0

    if kg is not None:
        try:
            total_facts = kg.count_nodes()
        except (sqlite3.Error, OSError, AttributeError) as exc:
            logger.warning("Failed to count KG nodes: %s", exc)
        try:
            total_edges = kg.count_edges()
        except (sqlite3.Error, OSError, AttributeError) as exc:
            logger.warning("Failed to count KG edges: %s", exc)
        try:
            locked_facts = kg.count_locked()
        except (sqlite3.Error, OSError, AttributeError) as exc:
            logger.warning("Failed to count locked facts: %s", exc)

    # -- Temporal distribution from kg_nodes (graceful for missing columns) --
    temporal_distribution: dict[str, int] = {}
    if kg is not None:
        try:
            db = kg.db  # type: ignore[attr-defined]
            db_lock = kg.db_lock  # type: ignore[attr-defined]
            with db_lock:
                rows = db.execute(
                    "SELECT temporal_type, COUNT(*) as cnt FROM kg_nodes "
                    "WHERE temporal_type IS NOT NULL GROUP BY temporal_type"
                ).fetchall()
            for row in rows:
                temporal_distribution[row[0]] = row[1]
        except (sqlite3.Error, OSError, AttributeError) as exc:
            logger.debug("Temporal distribution query failed (migration may not have run): %s", exc)

    return {
        "total_records": total_records,
        "total_facts": total_facts,
        "total_edges": total_edges,
        "locked_facts": locked_facts,
        "branches_populated": len(branch_distribution),
        "branch_distribution": branch_distribution,
        "temporal_distribution": temporal_distribution,
        "captured_at": captured_at,
    }
