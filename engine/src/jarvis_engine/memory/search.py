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

import atexit
import logging
import math
import os
import sqlite3
import threading
import time
import weakref
from typing import TYPE_CHECKING, Any, TypedDict, cast

from jarvis_engine._shared import recency_weight as _recency_weight_core

if TYPE_CHECKING:
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)


class MemoryRecord(TypedDict, total=False):
    """Shape of a memory record dict returned from the records table."""

    record_id: str
    ts: str
    source: str
    kind: str
    task_id: str
    branch: str
    tags: str
    summary: str
    content_hash: str
    confidence: float
    tier: str
    access_count: int
    last_accessed: str
    created_at: str


__all__ = [
    "MemoryRecord",
    "hybrid_search",
]

#  RRF weight constants — tune these to adjust FTS5 vs. semantic balance.
#  fts_weight + vec_weight need not sum to 1; they are independent multipliers
#  on each retrieval method's RRF contribution.
RRF_FTS_WEIGHT: float = 0.4  # keyword (FTS5) contribution
RRF_VEC_WEIGHT: float = 0.6  # semantic (sqlite-vec) contribution

#  Debounced access-count updater — avoids a DB write on every search call.
#  Flushes when the pending set reaches _ACCESS_BATCH_SIZE or when
#  _ACCESS_FLUSH_INTERVAL seconds have elapsed since the first pending entry.
_ACCESS_BATCH_SIZE = 100
_ACCESS_FLUSH_INTERVAL = 10.0  # seconds

_access_lock = threading.Lock()
_access_pending: dict[int, set[str]] = {}
_access_first_ts: dict[int, float] = {}
_access_engines: dict[int, weakref.ref] = {}  # weakrefs for shutdown flush


def _enqueue_access_updates(engine: "MemoryEngine", record_ids: list[str]) -> None:
    """Buffer record IDs and flush to DB when batch or time threshold is met."""
    flush_ids: list[str] | None = None
    eid = id(engine)
    with _access_lock:
        bucket = _access_pending.get(eid)
        if bucket is None:
            bucket = set()
            _access_pending[eid] = bucket
            _access_engines[eid] = weakref.ref(engine)
        if not bucket:
            _access_first_ts[eid] = time.monotonic()
        bucket.update(record_ids)
        first_ts = _access_first_ts.get(eid, 0.0)
        elapsed = time.monotonic() - first_ts if first_ts else 0.0
        if (
            len(bucket) >= _ACCESS_BATCH_SIZE
            or elapsed >= _ACCESS_FLUSH_INTERVAL
        ):
            flush_ids = list(bucket)
            bucket.clear()
            _access_first_ts[eid] = 0.0
    if flush_ids:
        try:
            engine.update_access_batch(flush_ids)
        except (sqlite3.Error, OSError, RuntimeError):
            logger.debug("Failed to flush access count updates", exc_info=True)


def flush_all_access_updates() -> None:
    """Drain all pending access count updates to their engines.

    Called on shutdown to prevent losing buffered access counts.
    """
    with _access_lock:
        snapshot = [
            (_access_engines.get(eid), list(bucket))
            for eid, bucket in _access_pending.items()
            if bucket
        ]
        _access_pending.clear()
        _access_first_ts.clear()
        _access_engines.clear()
    for engine_ref, ids in snapshot:
        if engine_ref is None:
            continue
        engine = engine_ref()
        if engine is None:
            continue  # Engine was garbage collected
        try:
            engine.update_access_batch(ids)
        except (sqlite3.Error, OSError, RuntimeError, AttributeError):
            logger.debug("Failed to flush access counts on shutdown", exc_info=True)


atexit.register(flush_all_access_updates)


def _recency_weight(ts_str: str) -> float:
    """Compute exponential recency decay (7-day / 168-hour half-life).

    Returns a value between 0.0 and 1.0, where 1.0 means just created.
    Returns 0.0 for empty or unparseable timestamps.
    """
    return _recency_weight_core(ts_str, default=0.0, decay_hours=168.0)


def _trust_retrieval_mode() -> str:
    raw = os.getenv("JARVIS_TRUST_RETRIEVAL_MODE", "shadow").strip().lower()
    return raw or "shadow"


def _trust_shadow_penalty(trust_level: str) -> float:
    return {
        "T4_blocked": 0.0,
        "T1_observed": 0.7,
        "T0_untrusted": 0.5,
        "T2_verified": 1.0,
        "T3_trusted": 1.0,
        "unknown": 1.0,
    }.get(trust_level, 1.0)


def hybrid_search(
    engine: "MemoryEngine",
    query: str,
    query_embedding: list[float],
    k: int = 10,
    rrf_k: int = 60,
    recency_weight: float = 0.3,
) -> list[MemoryRecord]:
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

    # 3. Reciprocal Rank Fusion with configurable weights
    scores: dict[str, float] = {}

    for i, (rid, _rank) in enumerate(fts_results):
        scores[rid] = scores.get(rid, 0.0) + RRF_FTS_WEIGHT / (rrf_k + i + 1)

    for i, (rid, _distance) in enumerate(vec_results):
        scores[rid] = scores.get(rid, 0.0) + RRF_VEC_WEIGHT / (rrf_k + i + 1)

    if not scores:
        return []

    # 4. Batch-fetch all candidate records (eliminates N+1 queries)
    candidate_ids = list(scores.keys())
    records_list = engine.get_records_batch(candidate_ids)
    records_by_id = {r["record_id"]: r for r in records_list}
    provenance_by_id = engine.get_learning_provenance_batch(candidate_ids)
    retrieval_mode = _trust_retrieval_mode()

    # 5. Recency + frequency boost
    scored_records: list[tuple[float, dict[str, Any]]] = []
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
        trust_meta = provenance_by_id.get(rid, {})
        trust_level = str(trust_meta.get("trust_level", "unknown"))
        shadow_penalty = _trust_shadow_penalty(trust_level)
        shadow_score = boosted_score * shadow_penalty
        record["trust_level"] = trust_level
        record["learning_lane"] = str(trust_meta.get("learning_lane", "unknown"))
        record["promotion_state"] = str(trust_meta.get("promotion_state", "unknown"))
        record["artifact_kind"] = str(trust_meta.get("artifact_kind", ""))
        record["trust_policy_mode"] = str(trust_meta.get("policy_mode", retrieval_mode))
        record["_trust_shadow_penalty"] = round(shadow_penalty, 4)
        record["_trust_shadow_score"] = round(shadow_score, 4)
        record["_trust_would_downrank"] = shadow_score < boosted_score

        scored_records.append((boosted_score, record))

    # 6. Sort by combined score descending, take top-k
    scored_records.sort(key=lambda pair: pair[0], reverse=True)
    results = []
    for score, record in scored_records[:k]:
        record["_search_score"] = round(score, 4)
        results.append(record)

    # 7. Debounced access-count update (flushes every 100 IDs or 10 seconds)
    result_ids = [r["record_id"] for r in results if r.get("record_id")]
    if result_ids:
        _enqueue_access_updates(engine, result_ids)

    return cast(list[MemoryRecord], results)
