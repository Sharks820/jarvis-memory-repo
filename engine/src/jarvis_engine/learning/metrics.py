"""Knowledge growth metrics capture.

Provides a snapshot of the current growth state: record counts, fact counts,
branch distribution, and temporal distribution from the knowledge graph.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def capture_knowledge_metrics(kg: Any, engine: Any) -> dict:
    """Capture a snapshot of knowledge growth metrics.

    Args:
        kg: KnowledgeGraph instance (uses count_nodes, count_edges, count_locked, .db).
        engine: MemoryEngine instance (uses ._db for direct SQL on records table).

    Returns:
        Dict with: total_records, total_facts, total_edges, locked_facts,
        branches_populated, branch_distribution, temporal_distribution, captured_at.
    """
    captured_at = datetime.now(UTC).isoformat()

    # -- Record counts from engine --
    total_records = 0
    branch_distribution: dict[str, int] = {}

    if engine is not None:
        try:
            db = engine._db  # noqa: SLF001
            total_records = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]

            rows = db.execute(
                "SELECT branch, COUNT(*) as cnt FROM records GROUP BY branch"
            ).fetchall()
            for row in rows:
                branch_distribution[row[0]] = row[1]
        except Exception as exc:
            logger.warning("Failed to query records for metrics: %s", exc)

    # -- KG counts --
    total_facts = 0
    total_edges = 0
    locked_facts = 0

    if kg is not None:
        try:
            total_facts = kg.count_nodes()
        except Exception as exc:
            logger.warning("Failed to count KG nodes: %s", exc)
        try:
            total_edges = kg.count_edges()
        except Exception as exc:
            logger.warning("Failed to count KG edges: %s", exc)
        try:
            locked_facts = kg.count_locked()
        except Exception as exc:
            logger.warning("Failed to count locked facts: %s", exc)

    # -- Temporal distribution from kg_nodes (graceful for missing columns) --
    temporal_distribution: dict[str, int] = {}
    if kg is not None:
        try:
            db = kg.db  # type: ignore[attr-defined]
            rows = db.execute(
                "SELECT temporal_type, COUNT(*) as cnt FROM kg_nodes "
                "WHERE temporal_type IS NOT NULL GROUP BY temporal_type"
            ).fetchall()
            for row in rows:
                temporal_distribution[row[0]] = row[1]
        except Exception:
            # Column may not exist if migration has not run yet
            pass

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
