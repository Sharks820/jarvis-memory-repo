"""Tests for ingest module — validation, deduplication, cache eviction, redaction."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from jarvis_engine.ingest import (
    IngestRecord,
    IngestionPipeline,
    _VALID_KINDS,
    _VALID_SOURCES,
)


@pytest.fixture
def mock_store() -> MagicMock:
    """Provide a mocked MemoryStore."""
    store = MagicMock()
    store.append = MagicMock()
    return store


@pytest.fixture
def pipeline(mock_store: MagicMock) -> IngestionPipeline:
    """Provide an IngestionPipeline with a mocked store."""
    return IngestionPipeline(mock_store)


# ---------------------------------------------------------------------------
# Source and kind validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_sources_accepted(self, pipeline: IngestionPipeline) -> None:
        """Every valid source should be accepted without error."""
        for source in _VALID_SOURCES:
            rec = pipeline.ingest(source, "episodic", "t1", "hello")
            assert rec.source == source
            # Reset dedup cache between iterations
            pipeline._seen_hashes.clear()

    def test_valid_kinds_accepted(self, pipeline: IngestionPipeline) -> None:
        """Every valid kind should be accepted without error."""
        for kind in _VALID_KINDS:
            rec = pipeline.ingest("user", kind, "t1", f"content-{kind}")
            assert rec.kind == kind

    def test_invalid_source_raises(self, pipeline: IngestionPipeline) -> None:
        with pytest.raises(ValueError, match="Invalid source"):
            pipeline.ingest("unknown_source", "episodic", "t1", "content")

    def test_invalid_kind_raises(self, pipeline: IngestionPipeline) -> None:
        with pytest.raises(ValueError, match="Invalid kind"):
            pipeline.ingest("user", "invalid_kind", "t1", "content")

    def test_empty_source_raises(self, pipeline: IngestionPipeline) -> None:
        with pytest.raises(ValueError, match="Invalid source"):
            pipeline.ingest("", "episodic", "t1", "content")

    def test_empty_kind_raises(self, pipeline: IngestionPipeline) -> None:
        with pytest.raises(ValueError, match="Invalid kind"):
            pipeline.ingest("user", "", "t1", "content")

    def test_case_sensitive_source(self, pipeline: IngestionPipeline) -> None:
        """Source names should be case-sensitive (e.g., 'User' is invalid)."""
        with pytest.raises(ValueError, match="Invalid source"):
            pipeline.ingest("User", "episodic", "t1", "content")

    def test_case_sensitive_kind(self, pipeline: IngestionPipeline) -> None:
        """Kind names should be case-sensitive (e.g., 'Episodic' is invalid)."""
        with pytest.raises(ValueError, match="Invalid kind"):
            pipeline.ingest("user", "Episodic", "t1", "content")


# ---------------------------------------------------------------------------
# Content hash deduplication
# ---------------------------------------------------------------------------


class TestContentDedup:
    def test_identical_content_is_deduplicated(
        self, pipeline: IngestionPipeline
    ) -> None:
        """Ingesting the same content twice should return deduplicated=True."""
        rec1 = pipeline.ingest("user", "episodic", "t1", "same content")
        rec2 = pipeline.ingest("user", "episodic", "t1", "same content")
        assert rec1.deduplicated is False
        assert rec2.deduplicated is True
        assert rec1.record_id == rec2.record_id

    def test_different_content_not_deduplicated(
        self, pipeline: IngestionPipeline
    ) -> None:
        rec1 = pipeline.ingest("user", "episodic", "t1", "content A")
        rec2 = pipeline.ingest("user", "episodic", "t1", "content B")
        assert rec1.deduplicated is False
        assert rec2.deduplicated is False
        assert rec1.record_id != rec2.record_id

    def test_different_source_not_deduplicated(
        self, pipeline: IngestionPipeline
    ) -> None:
        """Same content from different sources should produce different hashes."""
        rec1 = pipeline.ingest("user", "episodic", "t1", "shared content")
        rec2 = pipeline.ingest("claude", "episodic", "t1", "shared content")
        assert rec2.deduplicated is False
        assert rec1.record_id != rec2.record_id

    def test_different_kind_not_deduplicated(
        self, pipeline: IngestionPipeline
    ) -> None:
        """Same content with different kinds should produce different hashes."""
        rec1 = pipeline.ingest("user", "episodic", "t1", "shared content")
        rec2 = pipeline.ingest("user", "semantic", "t1", "shared content")
        assert rec2.deduplicated is False
        assert rec1.record_id != rec2.record_id

    def test_different_task_id_not_deduplicated(
        self, pipeline: IngestionPipeline
    ) -> None:
        """Same content with different task_ids should produce different hashes."""
        rec1 = pipeline.ingest("user", "episodic", "task_a", "shared content")
        rec2 = pipeline.ingest("user", "episodic", "task_b", "shared content")
        assert rec2.deduplicated is False

    def test_dedup_does_not_call_store(
        self, pipeline: IngestionPipeline, mock_store: MagicMock
    ) -> None:
        """Deduplicated records should not call store.append."""
        pipeline.ingest("user", "episodic", "t1", "stored once")
        mock_store.append.reset_mock()
        pipeline.ingest("user", "episodic", "t1", "stored once")
        mock_store.append.assert_not_called()

    def test_non_dedup_calls_store(
        self, pipeline: IngestionPipeline, mock_store: MagicMock
    ) -> None:
        """Non-deduplicated records should call store.append exactly once."""
        pipeline.ingest("user", "episodic", "t1", "unique content")
        mock_store.append.assert_called_once()


# ---------------------------------------------------------------------------
# Dedup cache eviction at 50k entries
# ---------------------------------------------------------------------------


class TestDedupCacheEviction:
    def test_cache_evicts_oldest_half_at_50k(
        self, pipeline: IngestionPipeline, mock_store: MagicMock
    ) -> None:
        """When cache exceeds 50k entries, oldest 25k should be evicted."""
        # Pre-populate the cache with 50,000 entries
        for i in range(50_000):
            pipeline._seen_hashes[f"hash_{i:06d}"] = f"id_{i:06d}"

        # The next ingest should trigger eviction (cache goes to 50,001 then evicts)
        pipeline.ingest("user", "episodic", "t1", "trigger eviction")

        # After eviction: 50,001 - 25,000 = 25,001 entries
        assert len(pipeline._seen_hashes) == 25_001

    def test_cache_preserves_newest_entries(
        self, pipeline: IngestionPipeline
    ) -> None:
        """After eviction, the newest entries should still be present."""
        # Pre-populate with 50,000 entries
        for i in range(50_000):
            pipeline._seen_hashes[f"hash_{i:06d}"] = f"id_{i:06d}"

        pipeline.ingest("user", "episodic", "t1", "trigger eviction")

        # The newest pre-populated entries (25000-49999) should survive
        assert "hash_049999" in pipeline._seen_hashes
        assert "hash_025000" in pipeline._seen_hashes
        # The oldest entries (0-24999) should be evicted
        assert "hash_000000" not in pipeline._seen_hashes
        assert "hash_024999" not in pipeline._seen_hashes

    def test_cache_below_threshold_no_eviction(
        self, pipeline: IngestionPipeline
    ) -> None:
        """Below 50k, no eviction should occur."""
        for i in range(100):
            pipeline._seen_hashes[f"hash_{i}"] = f"id_{i}"
        pipeline.ingest("user", "episodic", "t1", "no eviction needed")
        # 100 pre-populated + 1 new = 101
        assert len(pipeline._seen_hashes) == 101


# ---------------------------------------------------------------------------
# Thread safety of _seen_hashes
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_ingests_no_crash(
        self, mock_store: MagicMock
    ) -> None:
        """Multiple threads ingesting concurrently should not raise exceptions."""
        pipeline = IngestionPipeline(mock_store)
        errors: list[Exception] = []

        def ingest_batch(thread_id: int) -> None:
            try:
                for i in range(50):
                    pipeline.ingest(
                        "user", "episodic", f"t{thread_id}", f"content-{thread_id}-{i}"
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=ingest_batch, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"Concurrent ingest errors: {errors}"

    def test_concurrent_dedup_consistent(
        self, mock_store: MagicMock
    ) -> None:
        """Concurrent identical ingests should all see the same record_id."""
        pipeline = IngestionPipeline(mock_store)
        results: list[IngestRecord] = []
        lock = threading.Lock()

        def ingest_same() -> None:
            rec = pipeline.ingest("user", "episodic", "t1", "identical content")
            with lock:
                results.append(rec)

        threads = [threading.Thread(target=ingest_same) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # All results should have the same record_id
        ids = {r.record_id for r in results}
        assert len(ids) == 1


# ---------------------------------------------------------------------------
# Content redaction in logs
# ---------------------------------------------------------------------------


class TestContentRedaction:
    def test_short_content_not_truncated(
        self, pipeline: IngestionPipeline
    ) -> None:
        """Content <= 200 chars should appear in full without truncation marker."""
        short = "x" * 200
        result = pipeline._redacted_content(short)
        assert result == short
        assert "truncated" not in result

    def test_long_content_truncated(self, pipeline: IngestionPipeline) -> None:
        """Content > 200 chars should be truncated with a suffix."""
        long_content = "x" * 500
        result = pipeline._redacted_content(long_content)
        assert len(result) < len(long_content)
        assert result.endswith("...(truncated)")
        assert result.startswith("x" * 200)

    def test_exact_boundary_200_chars(
        self, pipeline: IngestionPipeline
    ) -> None:
        """Exactly 200 chars should NOT be truncated."""
        exact = "a" * 200
        result = pipeline._redacted_content(exact)
        assert result == exact

    def test_one_over_boundary_truncated(
        self, pipeline: IngestionPipeline
    ) -> None:
        """201 chars should be truncated."""
        content = "a" * 201
        result = pipeline._redacted_content(content)
        assert result == "a" * 200 + "...(truncated)"

    def test_empty_content(self, pipeline: IngestionPipeline) -> None:
        """Empty string should pass through without error."""
        assert pipeline._redacted_content("") == ""


# ---------------------------------------------------------------------------
# IngestRecord dataclass
# ---------------------------------------------------------------------------


class TestIngestRecord:
    def test_default_deduplicated_false(self) -> None:
        """IngestRecord.deduplicated should default to False."""
        rec = IngestRecord(
            record_id="abc",
            ts="2026-01-01T00:00:00",
            source="user",
            kind="episodic",
            task_id="t1",
            content="test",
        )
        assert rec.deduplicated is False

    def test_record_fields(self, pipeline: IngestionPipeline) -> None:
        """Ingested record should have all expected fields populated."""
        rec = pipeline.ingest("claude", "semantic", "task99", "some content")
        assert rec.source == "claude"
        assert rec.kind == "semantic"
        assert rec.task_id == "task99"
        assert rec.content == "some content"
        assert rec.ts  # non-empty timestamp
        assert rec.record_id  # non-empty ID


# ---------------------------------------------------------------------------
# Store event_type format
# ---------------------------------------------------------------------------


class TestStoreEventType:
    def test_event_type_format(
        self, pipeline: IngestionPipeline, mock_store: MagicMock
    ) -> None:
        """store.append should be called with event_type='ingest:{source}:{kind}'."""
        pipeline.ingest("user", "semantic", "t1", "content")
        mock_store.append.assert_called_once()
        call_args = mock_store.append.call_args
        assert call_args[1]["event_type"] == "ingest:user:semantic"

    def test_event_message_is_json(
        self, pipeline: IngestionPipeline, mock_store: MagicMock
    ) -> None:
        """store.append message should be valid JSON."""
        import json

        pipeline.ingest("opus", "procedural", "t1", "content")
        call_args = mock_store.append.call_args
        message = call_args[1]["message"]
        data = json.loads(message)
        assert data["source"] == "opus"
        assert data["kind"] == "procedural"

    def test_event_message_has_redacted_content(
        self, pipeline: IngestionPipeline, mock_store: MagicMock
    ) -> None:
        """The logged message should contain redacted (truncated) content, not full."""
        import json

        long_content = "s" * 500
        pipeline.ingest("user", "episodic", "t1", long_content)
        call_args = mock_store.append.call_args
        data = json.loads(call_args[1]["message"])
        assert data["content"].endswith("...(truncated)")
        assert len(data["content"]) < len(long_content)


# ---------------------------------------------------------------------------
# Content hash determinism
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_hash_deterministic(self, pipeline: IngestionPipeline) -> None:
        """Same inputs should always produce the same hash."""
        h1 = pipeline._content_hash("user", "episodic", "t1", "hello")
        h2 = pipeline._content_hash("user", "episodic", "t1", "hello")
        assert h1 == h2

    def test_hash_changes_with_content(
        self, pipeline: IngestionPipeline
    ) -> None:
        h1 = pipeline._content_hash("user", "episodic", "t1", "hello")
        h2 = pipeline._content_hash("user", "episodic", "t1", "world")
        assert h1 != h2

    def test_hash_length(self, pipeline: IngestionPipeline) -> None:
        """Hash should be truncated to 32 hex chars."""
        h = pipeline._content_hash("user", "episodic", "t1", "content")
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)
