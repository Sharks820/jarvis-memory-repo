"""Hybrid search combining FTS5 keyword + sqlite-vec semantic + recency decay.

Uses Reciprocal Rank Fusion (RRF) to combine ranked lists from different
retrieval methods into a single unified ranking. Recency decay boosts
recent records over older ones with equivalent relevance.

Algorithm:
1. FTS5 keyword search for term matching
2. sqlite-vec KNN search for semantic similarity
3. RRF combination: score = sum(1/(rrf_k + rank_i + 1))
4. Recency boost: score *= (1.0 + recency_weight * exp(-age_hours/168))
5. Return top-k records sorted by combined score
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)


def _recency_weight(ts_str: str) -> float:
    """Compute exponential recency decay.

    Returns a value between 0.0 and 1.0, where 1.0 means just created
    and values decay with a half-life of approximately 7 days (168 hours).
    """
    raw = str(ts_str).strip()
    if not raw:
        return 0.0
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta_hours = max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600.0)
    return math.exp(-delta_hours / 168.0)


def hybrid_search(
    engine: "MemoryEngine",
    query: str,
    query_embedding: list[float],
    k: int = 10,
    rrf_k: int = 60,
    recency_weight: float = 0.3,
) -> list[dict]:
    """Combine FTS5 keyword + sqlite-vec semantic search with recency decay.

    Args:
        engine: MemoryEngine instance with FTS5 and sqlite-vec tables.
        query: Text query for FTS5 keyword matching.
        query_embedding: Embedding vector for semantic similarity search.
        k: Number of results to return.
        rrf_k: RRF constant (higher = more equal weighting of ranks).
        recency_weight: Weight for recency boost (0.0 = no boost).

    Returns:
        List of record dicts sorted by combined RRF + recency score, up to k items.
    """
    # 1. FTS5 keyword search
    fts_results = engine.search_fts(query, limit=k * 3)

    # 2. sqlite-vec KNN search
    vec_results = engine.search_vec(query_embedding, limit=k * 3)

    # 3. Reciprocal Rank Fusion
    scores: dict[str, float] = {}

    for i, (rid, _rank) in enumerate(fts_results):
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (rrf_k + i + 1)

    for i, (rid, _distance) in enumerate(vec_results):
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (rrf_k + i + 1)

    if not scores:
        return []

    # 4. Recency boost
    scored_records: list[tuple[float, dict]] = []
    for rid, score in scores.items():
        record = engine.get_record(rid)
        if record is None:
            continue
        ts = str(record.get("ts", ""))
        recency = _recency_weight(ts)
        boosted_score = score * (1.0 + recency_weight * recency)
        scored_records.append((boosted_score, record))

    # 5. Sort by combined score descending, take top-k
    scored_records.sort(key=lambda pair: pair[0], reverse=True)
    results = [record for _score, record in scored_records[:k]]

    # 6. Update access counts for returned records
    for record in results:
        rid = record.get("record_id", "")
        if rid:
            engine.update_access(rid)

    return results
