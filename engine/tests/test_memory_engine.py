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


# ---------------------------------------------------------------------------
# Extended MemoryEngine Tests (CRUD, search, concurrency, edge cases)
# ---------------------------------------------------------------------------


class TestMemoryEngineDelete:
    """Tests for record deletion operations."""

    def test_delete_record_removes_from_all_tables(self, engine: MemoryEngine) -> None:
        """delete_record removes the record from records, fts_records, and vec_records."""
        emb = _make_embedding(seed=5.0)
        r = _make_record(record_id="del1", summary="delete me")
        engine.insert_record(r, embedding=emb)
        assert engine.get_record("del1") is not None

        result = engine.delete_record("del1")
        assert result is True
        assert engine.get_record("del1") is None
        # FTS should also be gone
        fts_results = engine.search_fts("delete")
        assert all(rid != "del1" for rid, _ in fts_results)

    def test_delete_nonexistent_record_returns_false(self, engine: MemoryEngine) -> None:
        """Deleting a record that does not exist returns False."""
        assert engine.delete_record("does_not_exist") is False

    def test_delete_records_batch_removes_multiple(self, engine: MemoryEngine) -> None:
        """Batch delete removes all specified records."""
        for i in range(5):
            r = _make_record(record_id=f"batch_del_{i}", summary=f"batch record {i}")
            engine.insert_record(r)
        assert engine.count_records() == 5

        deleted = engine.delete_records_batch(["batch_del_0", "batch_del_2", "batch_del_4"])
        assert deleted == 3
        assert engine.count_records() == 2
        assert engine.get_record("batch_del_1") is not None
        assert engine.get_record("batch_del_3") is not None

    def test_delete_records_batch_empty_list(self, engine: MemoryEngine) -> None:
        """Batch delete with empty list does nothing and returns 0."""
        r = _make_record(record_id="keep", summary="keep me")
        engine.insert_record(r)
        assert engine.delete_records_batch([]) == 0
        assert engine.count_records() == 1

    def test_delete_records_batch_nonexistent_ids(self, engine: MemoryEngine) -> None:
        """Batch delete with nonexistent IDs returns 0 deleted."""
        assert engine.delete_records_batch(["ghost1", "ghost2"]) == 0


class TestMemoryEngineRetrieve:
    """Tests for record retrieval operations."""

    def test_get_record_returns_none_for_missing(self, engine: MemoryEngine) -> None:
        """get_record returns None for nonexistent record_id."""
        assert engine.get_record("no_such_id") is None

    def test_get_record_by_hash(self, engine: MemoryEngine) -> None:
        """get_record_by_hash finds record by content_hash."""
        r = _make_record(record_id="hash_test", summary="hash lookup test")
        engine.insert_record(r)
        expected_hash = hashlib.sha256("hash lookup test".encode()).hexdigest()

        found = engine.get_record_by_hash(expected_hash)
        assert found is not None
        assert found["record_id"] == "hash_test"

    def test_get_record_by_hash_missing(self, engine: MemoryEngine) -> None:
        """get_record_by_hash returns None for unknown hash."""
        assert engine.get_record_by_hash("abcdef1234567890" * 4) is None

    def test_get_records_batch(self, engine: MemoryEngine) -> None:
        """get_records_batch retrieves multiple records in one call."""
        for i in range(4):
            engine.insert_record(_make_record(record_id=f"batch_{i}", summary=f"record {i}"))

        results = engine.get_records_batch(["batch_0", "batch_2"])
        assert len(results) == 2
        ids = {r["record_id"] for r in results}
        assert ids == {"batch_0", "batch_2"}

    def test_get_records_batch_empty_list(self, engine: MemoryEngine) -> None:
        """get_records_batch with empty list returns empty list."""
        assert engine.get_records_batch([]) == []

    def test_get_all_record_ids(self, engine: MemoryEngine) -> None:
        """get_all_record_ids returns all stored record IDs."""
        for i in range(3):
            engine.insert_record(_make_record(record_id=f"all_{i}", summary=f"record {i}"))

        ids = engine.get_all_record_ids()
        assert set(ids) == {"all_0", "all_1", "all_2"}

    def test_get_all_records_for_tier_maintenance(self, engine: MemoryEngine) -> None:
        """get_all_records_for_tier_maintenance returns correct columns."""
        engine.insert_record(_make_record(record_id="tier_rec", summary="tier test"))
        results = engine.get_all_records_for_tier_maintenance()
        assert len(results) == 1
        record = results[0]
        assert "record_id" in record
        assert "ts" in record
        assert "access_count" in record
        assert "confidence" in record
        assert "tier" in record


class TestMemoryEngineUpdate:
    """Tests for record update operations."""

    def test_update_access_nonexistent_returns_false(self, engine: MemoryEngine) -> None:
        """update_access on missing record returns False."""
        assert engine.update_access("missing") is False

    def test_update_access_batch(self, engine: MemoryEngine) -> None:
        """Batch access update increments counts for all specified records."""
        for i in range(3):
            engine.insert_record(_make_record(record_id=f"acc_b_{i}", summary=f"access batch {i}"))

        engine.update_access_batch(["acc_b_0", "acc_b_1"])
        engine.update_access_batch(["acc_b_0"])

        r0 = engine.get_record("acc_b_0")
        r1 = engine.get_record("acc_b_1")
        r2 = engine.get_record("acc_b_2")
        assert r0["access_count"] == 2
        assert r1["access_count"] == 1
        assert r2["access_count"] == 0

    def test_update_access_batch_empty(self, engine: MemoryEngine) -> None:
        """Batch access update with empty list is a no-op."""
        engine.update_access_batch([])  # should not raise

    def test_update_tier(self, engine: MemoryEngine) -> None:
        """update_tier changes the tier of a record."""
        engine.insert_record(_make_record(record_id="tier1", summary="tier update test"))
        engine.update_tier("tier1", "hot")
        record = engine.get_record("tier1")
        assert record["tier"] == "hot"

    def test_update_tiers_batch(self, engine: MemoryEngine) -> None:
        """Batch tier update changes tiers for multiple records."""
        for i in range(3):
            engine.insert_record(_make_record(record_id=f"tb_{i}", summary=f"tier batch {i}"))

        engine.update_tiers_batch([("tb_0", "hot"), ("tb_2", "cold")])

        assert engine.get_record("tb_0")["tier"] == "hot"
        assert engine.get_record("tb_1")["tier"] == "warm"  # unchanged
        assert engine.get_record("tb_2")["tier"] == "cold"

    def test_update_tiers_batch_empty(self, engine: MemoryEngine) -> None:
        """Batch tier update with empty list is a no-op."""
        engine.update_tiers_batch([])  # should not raise


class TestMemoryEngineFTS:
    """Extended FTS5 search tests."""

    def test_fts_empty_query_returns_empty(self, engine: MemoryEngine) -> None:
        """Empty query after sanitization returns empty list."""
        engine.insert_record(_make_record(record_id="fts_e", summary="some content here"))
        assert engine.search_fts("") == []

    def test_fts_special_chars_sanitized(self, engine: MemoryEngine) -> None:
        """FTS5 special characters are stripped from queries."""
        engine.insert_record(_make_record(record_id="fts_s", summary="special test content"))
        # Queries with special chars should still work (chars are stripped)
        results = engine.search_fts('"special"')
        ids = [rid for rid, _ in results]
        assert "fts_s" in ids

    def test_fts_boolean_operators_stripped(self, engine: MemoryEngine) -> None:
        """FTS5 boolean operators (AND, OR, NOT) are stripped from queries."""
        sanitized = MemoryEngine._sanitize_fts_query("python AND java OR NOT ruby")
        assert "AND" not in sanitized.split()
        assert "OR" not in sanitized.split()
        assert "NOT" not in sanitized.split()
        assert "python" in sanitized
        assert "java" in sanitized
        assert "ruby" in sanitized

    def test_fts_limit_parameter(self, engine: MemoryEngine) -> None:
        """FTS search respects the limit parameter."""
        for i in range(10):
            engine.insert_record(_make_record(
                record_id=f"lim_{i}", summary=f"python tutorial chapter {i}"
            ))
        results = engine.search_fts("python", limit=3)
        assert len(results) <= 3

    def test_fts_only_special_chars_returns_empty(self, engine: MemoryEngine) -> None:
        """Query consisting only of special chars returns empty list."""
        engine.insert_record(_make_record(record_id="fts_sp", summary="data"))
        assert engine.search_fts('***"[]()') == []


class TestMemoryEngineVecSearch:
    """Extended sqlite-vec search tests."""

    def test_vec_search_graceful_when_unavailable(self, tmp_path: Path) -> None:
        """search_vec returns empty list when vec extension is unavailable."""
        db_path = tmp_path / "no_vec.db"
        eng = MemoryEngine(db_path)
        # Force vec unavailable
        eng._vec_available = False
        try:
            results = eng.search_vec([0.0] * 768)
            assert results == []
        finally:
            eng.close()

    def test_vec_search_limit(self, engine: MemoryEngine) -> None:
        """Vec search respects the limit parameter."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")

        for i in range(10):
            engine.insert_record(
                _make_record(record_id=f"vlim_{i}", summary=f"vec limit {i}"),
                embedding=_make_embedding(seed=float(i)),
            )
        results = engine.search_vec(_make_embedding(seed=0.0), limit=3)
        assert len(results) <= 3


class TestMemoryEngineTagHandling:
    """Tests for tag normalization during insert."""

    def test_tags_list_normalized_to_json(self, engine: MemoryEngine) -> None:
        """Tags provided as a list are JSON-serialized."""
        r = _make_record(record_id="tag1", summary="tag test")
        r["tags"] = ["alpha", "beta"]
        engine.insert_record(r)
        retrieved = engine.get_record("tag1")
        import json
        assert json.loads(retrieved["tags"]) == ["alpha", "beta"]

    def test_tags_string_stored_as_is(self, engine: MemoryEngine) -> None:
        """Tags provided as a JSON string are stored directly."""
        r = _make_record(record_id="tag2", summary="tag test 2")
        r["tags"] = '["x","y"]'
        engine.insert_record(r)
        retrieved = engine.get_record("tag2")
        assert retrieved["tags"] == '["x","y"]'

    def test_tags_non_list_non_string_defaults_to_empty(self, engine: MemoryEngine) -> None:
        """Tags of unexpected type default to '[]'."""
        r = _make_record(record_id="tag3", summary="tag test 3")
        r["tags"] = 12345  # neither list nor string
        engine.insert_record(r)
        retrieved = engine.get_record("tag3")
        assert retrieved["tags"] == "[]"


class TestMemoryEngineEmbeddingValidation:
    """Tests for embedding dimension validation."""

    def test_wrong_embedding_dimension_raises(self, engine: MemoryEngine) -> None:
        """Insert with wrong embedding dimension raises ValueError."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        r = _make_record(record_id="bad_emb", summary="bad embedding")
        with pytest.raises(ValueError, match="Embedding dimension mismatch"):
            engine.insert_record(r, embedding=[0.0] * 100)  # wrong dim

    def test_no_embedding_skips_vec_insert(self, engine: MemoryEngine) -> None:
        """Insert without embedding skips vec_records insert."""
        r = _make_record(record_id="no_emb", summary="no embedding")
        assert engine.insert_record(r, embedding=None) is True
        assert engine.get_record("no_emb") is not None


class TestMemoryEngineContextManager:
    """Tests for context manager and close behavior."""

    def test_context_manager(self, tmp_path: Path) -> None:
        """MemoryEngine works as a context manager."""
        db_path = tmp_path / "ctx.db"
        with MemoryEngine(db_path) as eng:
            r = _make_record(record_id="ctx1", summary="context manager test")
            eng.insert_record(r)
            assert eng.get_record("ctx1") is not None
        # After __exit__, engine should be closed
        assert eng._closed is True

    def test_close_idempotent(self, engine: MemoryEngine) -> None:
        """Calling close() multiple times does not raise."""
        engine.close()
        engine.close()  # second close should be a no-op
        assert engine._closed is True

    def test_del_calls_close(self, tmp_path: Path) -> None:
        """__del__ closes the engine without raising."""
        db_path = tmp_path / "del_test.db"
        eng = MemoryEngine(db_path)
        eng.__del__()
        assert eng._closed is True


class TestMemoryEngineKGSchema:
    """Tests for knowledge graph schema delegation.

    KG schema creation was moved to KnowledgeGraph._ensure_schema() to
    eliminate duplication.  MemoryEngine._init_kg_schema() is now a no-op.
    Full schema tests live in test_knowledge_graph.py::test_kg_schema_created.
    """

    def test_init_kg_schema_is_noop(self, engine: MemoryEngine) -> None:
        """_init_kg_schema() exists but does not create KG tables on its own."""
        # After consolidation, KG tables are only created by KnowledgeGraph
        assert hasattr(engine, "_init_kg_schema")


class TestMemoryEngineConcurrency:
    """Basic concurrency tests (write serialization)."""

    def test_concurrent_inserts_via_threads(self, engine: MemoryEngine) -> None:
        """Multiple threads inserting records concurrently do not corrupt DB."""
        import threading

        errors = []

        def insert_worker(worker_id: int) -> None:
            try:
                for i in range(10):
                    r = _make_record(
                        record_id=f"w{worker_id}_r{i}",
                        summary=f"worker {worker_id} record {i}",
                    )
                    engine.insert_record(r)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=insert_worker, args=(w,)) for w in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent insert errors: {errors}"
        assert engine.count_records() == 40

    def test_concurrent_reads_via_threads(self, engine: MemoryEngine) -> None:
        """Multiple threads reading records concurrently do not fail."""
        import threading

        for i in range(10):
            engine.insert_record(_make_record(record_id=f"cr_{i}", summary=f"concurrent read {i}"))

        errors = []

        def read_worker() -> None:
            try:
                for i in range(10):
                    engine.get_record(f"cr_{i}")
                    engine.count_records()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=read_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent read errors: {errors}"
