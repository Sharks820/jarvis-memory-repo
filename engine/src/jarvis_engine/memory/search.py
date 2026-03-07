"""Hybrid search combining FTS5 keyword + sqlite-vec semantic + recency decay.

Uses Reciprocal Rank Fusion (RRF) to combine ranked lists from different
retrieval methods into a single unified ranking. Recency decay boosts
recent records over older ones with equivalent relevance.

Algorithm:
1. FTS5 keyword search for term matching
2. sqlite-vec KNN search for semantic similarity
3. RRF combination: score = sum(1/(rrf_k + rank_i + 1))
4. Recency boost: score *= (1.0 + recency_weight * exp(-age_hours/168))
5. Frequency boost: score *= (0.9 + 0.2 * min(log1p(access_count)/log1p(10), 1.0))
6. Return top-k records sorted by combined score
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime
from jarvis_engine._compat import UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Debounced access-count updater — avoids a DB write on every search call.
#  Flushes when the pending set reaches _ACCESS_BATCH_SIZE or when
#  _ACCESS_FLUSH_INTERVAL seconds have elapsed since the first pending entry.
# ---------------------------------------------------------------------------
_ACCESS_BATCH_SIZE = 100
_ACCESS_FLUSH_INTERVAL = 10.0  # seconds

_access_lock = threading.Lock()
_access_pending: set[str] = set()
_access_first_ts: float = 0.0


def _enqueue_access_updates(engine: "MemoryEngine", record_ids: list[str]) -> None:
    """Buffer record IDs and flush to DB when batch or time threshold is met."""
    global _access_first_ts
    flush_ids: list[str] | None = None
    with _access_lock:
        if not _access_pending:
            _access_first_ts = time.monotonic()
        _access_pending.update(record_ids)
        elapsed = time.monotonic() - _access_first_ts if _access_first_ts else 0.0
        if len(_access_pending) >= _ACCESS_BATCH_SIZE or elapsed >= _ACCESS_FLUSH_INTERVAL:
            flush_ids = list(_access_pending)
            _access_pending.clear()
            _access_first_ts = 0.0
    if flush_ids:
        try:
            engine.update_access_batch(flush_ids)
        except Exception:
            logger.debug("Failed to flush access count updates", exc_info=True)


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
    # Guard: engine must be open and valid
    if engine is None:
        raise ValueError("MemoryEngine is None — cannot perform hybrid search")
    if getattr(engine, "_closed", False):
        raise RuntimeError("MemoryEngine is closed — cannot perform hybrid search")

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

    # 4. Batch-fetch all candidate records (eliminates N+1 queries)
    candidate_ids = list(scores.keys())
    records_list = engine.get_records_batch(candidate_ids)
    records_by_id = {r["record_id"]: r for r in records_list}

    # 5. Recency + frequency boost
    scored_records: list[tuple[float, dict]] = []
    for rid, score in scores.items():
        record = records_by_id.get(rid)
        if record is None:
            continue
        ts = str(record.get("ts", ""))
        recency = _recency_weight(ts)
        boosted_score = score * (1.0 + recency_weight * recency)

        # Frequency boost: access_count via log1p, 0.9-1.1x range (LEARN-05)
        # Avoids double-counting recency (already handled above)
        access_count = record.get("access_count", 0) or 0
        freq_factor = math.log1p(max(access_count, 0)) / math.log1p(10)
        boosted_score *= (0.9 + 0.2 * min(freq_factor, 1.0))

        scored_records.append((boosted_score, record))

    # 6. Sort by combined score descending, take top-k
    scored_records.sort(key=lambda pair: pair[0], reverse=True)
    results = [record for _score, record in scored_records[:k]]

    # 7. Debounced access-count update (flushes every 100 IDs or 10 seconds)
    result_ids = [r["record_id"] for r in results if r.get("record_id")]
    if result_ids:
        _enqueue_access_updates(engine, result_ids)

    return results
