"""Tests for the enriched ingestion pipeline and branch classifier.

All tests use a MockEmbeddingService that returns deterministic vectors
so no real model download is required.
"""

from __future__ import annotations

import hashlib
import math
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jarvis_engine.memory.classify import BRANCH_DESCRIPTIONS, BranchClassifier, _cosine_similarity
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.memory.ingest import EnrichedIngestPipeline


# ---------------------------------------------------------------------------
# Mock Embedding Service
# ---------------------------------------------------------------------------

class MockEmbeddingService:
    """Deterministic embedding service for testing.

    Returns sin-based vectors seeded from a hash of the input text.
    This ensures the same text always produces the same embedding,
    and different texts produce different embeddings.
    """

    def __init__(self, dim: int = 768) -> None:
        self._dim = dim
        self.embed_calls: list[str] = []

    def embed(self, text: str, prefix: str = "search_document") -> list[float]:
        self.embed_calls.append(text)
        seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16) / 1e8
        return [math.sin(seed + i * 0.1) for i in range(self._dim)]

    def embed_query(self, query: str) -> list[float]:
        return self.embed(query, prefix="search_query")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_ingest.db"


@pytest.fixture
def engine(tmp_db: Path) -> MemoryEngine:
    eng = MemoryEngine(tmp_db)
    yield eng
    eng.close()


@pytest.fixture
def embed_service() -> MockEmbeddingService:
    return MockEmbeddingService()


@pytest.fixture
def classifier(embed_service: MockEmbeddingService) -> BranchClassifier:
    return BranchClassifier(embed_service)


@pytest.fixture
def pipeline(
    engine: MemoryEngine,
    embed_service: MockEmbeddingService,
    classifier: BranchClassifier,
) -> EnrichedIngestPipeline:
    return EnrichedIngestPipeline(engine, embed_service, classifier)


# ---------------------------------------------------------------------------
# EnrichedIngestPipeline Tests
# ---------------------------------------------------------------------------

class TestEnrichedIngestPipeline:

    def test_ingest_stores_record_in_sqlite(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Ingest content, verify it appears in MemoryEngine."""
        ids = pipeline.ingest(
            source="user",
            kind="episodic",
            task_id="task-001",
            content="I went to the doctor today for a checkup",
        )
        assert len(ids) == 1
        record = engine.get_record(ids[0])
        assert record is not None
        assert record["source"] == "user"
        assert record["kind"] == "episodic"
        assert record["task_id"] == "task-001"

    def test_ingest_deduplicates_by_content_hash(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Ingest same content twice, verify only one record stored."""
        content = "This is a unique piece of content for dedup testing"
        ids1 = pipeline.ingest(source="user", kind="episodic", task_id="t1", content=content)
        ids2 = pipeline.ingest(source="user", kind="episodic", task_id="t1", content=content)
        assert len(ids1) == 1
        assert len(ids2) == 0  # duplicate, not stored
        assert engine.count_records() == 1

    def test_ingest_chunks_long_content(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Ingest 5000-char content, verify it produces multiple records."""
        # Build content > 2000 chars with sentence boundaries
        sentences = [f"This is test sentence number {i} with some extra padding words. " for i in range(100)]
        long_content = " ".join(sentences)
        assert len(long_content) > 2000

        ids = pipeline.ingest(source="user", kind="semantic", task_id="t2", content=long_content)
        assert len(ids) > 1, f"Expected multiple chunks but got {len(ids)}"
        assert engine.count_records() == len(ids)

    def test_ingest_classifies_branch_semantically(
        self,
        engine: MemoryEngine,
        embed_service: MockEmbeddingService,
    ) -> None:
        """Ingest health-related content, verify branch classification happens.

        Note: With mock embeddings, the exact branch may not be 'health' since
        the mock uses hash-based seeds (not real semantic meaning). The test
        verifies that the classifier RUNS and assigns SOME branch (not empty).
        """
        classifier = BranchClassifier(embed_service)
        pipeline = EnrichedIngestPipeline(engine, embed_service, classifier)

        ids = pipeline.ingest(
            source="user",
            kind="episodic",
            task_id="t3",
            content="medications prescriptions doctor appointments health",
        )
        assert len(ids) == 1
        record = engine.get_record(ids[0])
        assert record is not None
        # Branch should be assigned (either a real branch or "general")
        assert record["branch"] in list(BRANCH_DESCRIPTIONS.keys()) + ["general"]
        assert record["branch"] != ""

    def test_ingest_generates_embedding_for_each_chunk(
        self,
        engine: MemoryEngine,
        embed_service: MockEmbeddingService,
        classifier: BranchClassifier,
    ) -> None:
        """Ingest multi-chunk content, verify embed was called for each chunk."""
        # Reset call tracking
        embed_service.embed_calls.clear()

        # Build multi-chunk content
        sentences = [f"Sentence {i} about important research findings. " for i in range(80)]
        long_content = " ".join(sentences)
        assert len(long_content) > 2000

        pipeline = EnrichedIngestPipeline(engine, embed_service, classifier)
        ids = pipeline.ingest(source="claude", kind="semantic", task_id="t4", content=long_content)

        # embed() was called at least once per chunk (plus centroid calls)
        assert len(ids) > 1
        # Each chunk gets one embed() call for the content itself
        # (centroids add calls too, but there should be at least len(ids) calls for chunks)
        chunk_embed_calls = len(embed_service.embed_calls) - len(BRANCH_DESCRIPTIONS)
        assert chunk_embed_calls >= len(ids)

    def test_sanitize_redacts_passwords(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Ingest content with password patterns, verify stored content has [REDACTED]."""
        ids = pipeline.ingest(
            source="user",
            kind="episodic",
            task_id="t5",
            content="Login details: password: secret123 and token: abc-xyz-789",
        )
        assert len(ids) == 1
        record = engine.get_record(ids[0])
        assert record is not None
        assert "secret123" not in record["summary"]
        assert "abc-xyz-789" not in record["summary"]
        assert "[REDACTED]" in record["summary"]

    def test_ingest_empty_content_returns_empty(
        self, pipeline: EnrichedIngestPipeline
    ) -> None:
        """Empty content should return empty list."""
        ids = pipeline.ingest(source="user", kind="episodic", task_id="t6", content="")
        assert ids == []

    def test_ingest_whitespace_only_returns_empty(
        self, pipeline: EnrichedIngestPipeline
    ) -> None:
        """Whitespace-only content should return empty list."""
        ids = pipeline.ingest(source="user", kind="episodic", task_id="t7", content="   \n\n  ")
        assert ids == []


# ---------------------------------------------------------------------------
# BranchClassifier Tests
# ---------------------------------------------------------------------------

class TestBranchClassifier:

    def test_branch_classifier_returns_general_below_threshold(
        self, embed_service: MockEmbeddingService
    ) -> None:
        """With a zero-vector embedding, classifier should return 'general'."""
        classifier = BranchClassifier(embed_service)
        # Zero vector has 0 cosine similarity with everything
        zero_embedding = [0.0] * 768
        result = classifier.classify(zero_embedding, threshold=0.3)
        assert result == "general"

    def test_branch_classifier_computes_centroids_lazily(
        self, embed_service: MockEmbeddingService
    ) -> None:
        """Centroids are computed on first classify() call, not on construction."""
        classifier = BranchClassifier(embed_service)
        assert classifier._centroids is None
        # After calling classify, centroids should be populated
        embedding = embed_service.embed("some text")
        classifier.classify(embedding)
        assert classifier._centroids is not None
        assert len(classifier._centroids) == len(BRANCH_DESCRIPTIONS)

    def test_branch_classifier_same_input_same_output(
        self, embed_service: MockEmbeddingService
    ) -> None:
        """Same embedding should always classify to the same branch."""
        classifier = BranchClassifier(embed_service)
        embedding = embed_service.embed("test content about coding")
        result1 = classifier.classify(embedding)
        result2 = classifier.classify(embedding)
        assert result1 == result2

    def test_cosine_similarity_identical_vectors(self) -> None:
        """Cosine similarity of identical vectors should be ~1.0."""
        vec = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(vec, vec) - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal_vectors(self) -> None:
        """Cosine similarity of orthogonal vectors should be ~0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6
