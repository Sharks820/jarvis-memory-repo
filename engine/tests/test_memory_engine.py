"""Comprehensive tests for the SQLite + FTS5 + sqlite-vec memory engine.

Tests cover:
- MemoryEngine CRUD, schema, WAL mode, deduplication
- FTS5 keyword search
- sqlite-vec KNN search
- TierManager classification (hot/warm/cold)
- Hybrid search with RRF + recency boost
"""

from __future__ import annotations

import hashlib
import struct
import tempfile
from datetime import datetime, timedelta
from jarvis_engine._compat import UTC
from pathlib import Path

import pytest

from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.memory.search import hybrid_search
from jarvis_engine.memory.tiers import Tier, TierManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embedding(seed: float = 0.0, dim: int = 768) -> list[float]:
    """Create a deterministic mock embedding vector."""
    import math

    return [math.sin(seed + i * 0.1) for i in range(dim)]


def _make_record(
    record_id: str = "rec001",
    summary: str = "test record summary",
    ts: str | None = None,
    source: str = "test",
    kind: str = "note",
    confidence: float = 0.72,
    access_count: int = 0,
    tier: str = "warm",
    content_hash: str | None = None,
) -> dict:
    """Create a record dict for testing."""
    if ts is None:
        ts = datetime.now(UTC).isoformat()
    if content_hash is None:
        content_hash = hashlib.sha256(summary.encode()).hexdigest()
    return {
        "record_id": record_id,
        "ts": ts,
        "source": source,
        "kind": kind,
        "task_id": "",
        "branch": "general",
        "tags": "[]",
        "summary": summary,
        "content_hash": content_hash,
        "confidence": confidence,
        "tier": tier,
        "access_count": access_count,
        "last_accessed": "",
    }


@pytest.fixture
def engine(tmp_path: Path) -> MemoryEngine:
    """Create a MemoryEngine with a temporary database."""
    db_path = tmp_path / "test_memory.db"
    eng = MemoryEngine(db_path)
    yield eng
    eng.close()


# ---------------------------------------------------------------------------
# MemoryEngine Tests
# ---------------------------------------------------------------------------


class TestMemoryEngine:
    """Tests for MemoryEngine CRUD and schema."""

    def test_create_engine_initializes_schema(self, engine: MemoryEngine) -> None:
        """Engine creates all required tables on init."""
        cur = engine._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' OR type='view' ORDER BY name"
        )
        table_names = {row[0] for row in cur.fetchall()}
        assert "records" in table_names
        assert "fts_records" in table_names
        assert "facts" in table_names
        assert "schema_version" in table_names
        if engine._vec_available:
            assert "vec_records" in table_names

    def test_insert_record_stores_data(self, engine: MemoryEngine) -> None:
        """Insert a record and verify it can be retrieved by ID."""
        embedding = _make_embedding(seed=1.0)
        record = _make_record(record_id="rec_insert_01", summary="my test record")
        result = engine.insert_record(record, embedding=embedding)
        assert result is True

        retrieved = engine.get_record("rec_insert_01")
        assert retrieved is not None
        assert retrieved["record_id"] == "rec_insert_01"
        assert retrieved["summary"] == "my test record"

    def test_insert_duplicate_content_hash_ignored(self, engine: MemoryEngine) -> None:
        """Inserting two records with same content_hash stores only one."""
        shared_hash = hashlib.sha256(b"same content").hexdigest()
        r1 = _make_record(record_id="dup1", summary="first", content_hash=shared_hash)
        r2 = _make_record(record_id="dup2", summary="second", content_hash=shared_hash)

        assert engine.insert_record(r1) is True
        assert engine.insert_record(r2) is False
        assert engine.count_records() == 1

    def test_search_fts_returns_matching_records(self, engine: MemoryEngine) -> None:
        """FTS5 search finds records matching query terms."""
        r1 = _make_record(record_id="fts1", summary="python programming tutorial")
        r2 = _make_record(record_id="fts2", summary="cooking recipe for pasta")
        r3 = _make_record(record_id="fts3", summary="advanced python debugging tips")

        engine.insert_record(r1)
        engine.insert_record(r2)
        engine.insert_record(r3)

        results = engine.search_fts("python")
        record_ids = [rid for rid, _rank in results]
        assert "fts1" in record_ids
        assert "fts3" in record_ids
        assert "fts2" not in record_ids

    def test_search_vec_returns_similar_records(self, engine: MemoryEngine) -> None:
        """Vec search finds the record with the most similar embedding."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")

        # Create embeddings where e1 and query are similar, e2 is different
        query_emb = _make_embedding(seed=1.0)
        similar_emb = _make_embedding(seed=1.01)  # very close to query
        different_emb = _make_embedding(seed=100.0)  # far from query

        r1 = _make_record(record_id="vec1", summary="similar record")
        r2 = _make_record(record_id="vec2", summary="different record")

        engine.insert_record(r1, embedding=similar_emb)
        engine.insert_record(r2, embedding=different_emb)

        results = engine.search_vec(query_emb, limit=2)
        assert len(results) >= 1
        # The similar record should be first (closest distance)
        assert results[0][0] == "vec1"

    def test_wal_mode_enabled(self, engine: MemoryEngine) -> None:
        """Verify WAL journal mode is active."""
        result = engine._db.execute("PRAGMA journal_mode").fetchone()
        assert result[0] == "wal"

    def test_count_records(self, engine: MemoryEngine) -> None:
        """count_records returns correct count."""
        assert engine.count_records() == 0

        for i in range(5):
            r = _make_record(record_id=f"cnt{i}", summary=f"record {i}")
            engine.insert_record(r)

        assert engine.count_records() == 5

    def test_update_access_increments_count(self, engine: MemoryEngine) -> None:
        """update_access increments access_count and sets last_accessed."""
        r = _make_record(record_id="acc1", summary="access test")
        engine.insert_record(r)

        engine.update_access("acc1")
        engine.update_access("acc1")

        record = engine.get_record("acc1")
        assert record is not None
        assert record["access_count"] == 2
        assert record["last_accessed"] != ""


# ---------------------------------------------------------------------------
# TierManager Tests
# ---------------------------------------------------------------------------


class TestTierManager:
    """Tests for tier classification logic."""

    def test_recent_record_is_hot(self) -> None:
        """Record created within 48 hours is HOT."""
        tm = TierManager()
        record = _make_record(
            ts=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            access_count=0,
            confidence=0.5,
        )
        assert tm.classify(record) == Tier.HOT

    def test_old_low_access_record_is_cold(self) -> None:
        """Record older than 90 days with low access and low confidence is COLD."""
        tm = TierManager()
        record = _make_record(
            ts=(datetime.now(UTC) - timedelta(days=100)).isoformat(),
            access_count=1,
            confidence=0.5,
        )
        assert tm.classify(record) == Tier.COLD

    def test_high_confidence_stays_warm(self) -> None:
        """Old record with high confidence stays WARM."""
        tm = TierManager()
        record = _make_record(
            ts=(datetime.now(UTC) - timedelta(days=100)).isoformat(),
            access_count=0,
            confidence=0.90,
        )
        assert tm.classify(record) == Tier.WARM

    def test_frequently_accessed_stays_warm(self) -> None:
        """Old record with access_count > 3 stays WARM."""
        tm = TierManager()
        record = _make_record(
            ts=(datetime.now(UTC) - timedelta(days=100)).isoformat(),
            access_count=5,
            confidence=0.5,
        )
        assert tm.classify(record) == Tier.WARM


# ---------------------------------------------------------------------------
# Hybrid Search Tests
# ---------------------------------------------------------------------------


class TestHybridSearch:
    """Tests for hybrid search combining FTS5 + vec + recency."""

    def test_hybrid_search_combines_fts_and_vec(self, engine: MemoryEngine) -> None:
        """Record matching both FTS5 and vec ranks higher than vec-only match."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")

        query_emb = _make_embedding(seed=1.0)
        now = datetime.now(UTC).isoformat()

        # r_vec_only: matches embedding only (no keyword overlap at all)
        r1 = _make_record(record_id="vec_only", summary="cooking recipe tutorial guide", ts=now)
        engine.insert_record(r1, embedding=_make_embedding(seed=1.001))

        # r_both: matches BOTH keyword AND embedding
        r2 = _make_record(record_id="both", summary="python data science guide", ts=now)
        engine.insert_record(r2, embedding=_make_embedding(seed=1.002))

        # Add filler records to push kw-only out of top vec results
        for i in range(5):
            filler = _make_record(
                record_id=f"filler{i}",
                summary=f"irrelevant filler record number {i}",
                ts=now,
            )
            engine.insert_record(filler, embedding=_make_embedding(seed=200.0 + i))

        results = hybrid_search(engine, "python", query_emb, k=5)
        assert len(results) >= 1

        result_ids = [r["record_id"] for r in results]
        # "both" gets contributions from BOTH FTS5 (matches "python") and
        # vec search (seed 1.002 is close to query seed 1.0).
        # "vec_only" only gets contribution from vec search.
        # Therefore "both" should rank above "vec_only".
        assert "both" in result_ids
        if "vec_only" in result_ids:
            assert result_ids.index("both") < result_ids.index("vec_only"), (
                f"Expected 'both' above 'vec_only'. Order: {result_ids}"
            )

    def test_hybrid_search_recency_boost(self, engine: MemoryEngine) -> None:
        """Recent record ranks higher than old record when equally relevant."""
        now = datetime.now(UTC)
        old_ts = (now - timedelta(days=30)).isoformat()
        recent_ts = now.isoformat()

        # Both match keyword "database"
        old_emb = _make_embedding(seed=2.0)
        recent_emb = _make_embedding(seed=2.01)
        query_emb = _make_embedding(seed=2.0)

        r_old = _make_record(record_id="old1", summary="database optimization tips", ts=old_ts)
        r_recent = _make_record(record_id="new1", summary="database performance tuning", ts=recent_ts)

        engine.insert_record(r_old, embedding=old_emb)
        engine.insert_record(r_recent, embedding=recent_emb)

        results = hybrid_search(engine, "database", query_emb, k=2, recency_weight=0.5)
        assert len(results) == 2
        # Recent record should be first due to recency boost
        assert results[0]["record_id"] == "new1"

    def test_hybrid_search_returns_at_most_k(self, engine: MemoryEngine) -> None:
        """hybrid_search returns at most k results."""
        query_emb = _make_embedding(seed=0.0)
        now = datetime.now(UTC).isoformat()

        for i in range(20):
            r = _make_record(
                record_id=f"bulk{i:02d}",
                summary=f"memory record number {i} about testing",
                ts=now,
            )
            engine.insert_record(r, embedding=_make_embedding(seed=float(i)))

        results = hybrid_search(engine, "memory testing", query_emb, k=5)
        assert len(results) <= 5
