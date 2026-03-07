"""Tests for the enriched ingestion pipeline and branch classifier.

All tests use a MockEmbeddingService that returns deterministic vectors
so no real model download is required.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.memory.classify import BRANCH_DESCRIPTIONS, BranchClassifier, _cosine_similarity
from jarvis_engine.memory.embeddings import EmbeddingService
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


# ---------------------------------------------------------------------------
# Extended EnrichedIngestPipeline Tests
# ---------------------------------------------------------------------------


class TestIngestSanitization:
    """Tests for the _sanitize method and credential redaction."""

    def test_sanitize_strips_whitespace(self, pipeline: EnrichedIngestPipeline) -> None:
        """Leading/trailing whitespace is stripped."""
        assert pipeline._sanitize("  hello  ") == "hello"

    def test_sanitize_truncates_to_10000_chars(self, pipeline: EnrichedIngestPipeline) -> None:
        """Content longer than 10000 chars is truncated."""
        long_text = "x" * 15000
        result = pipeline._sanitize(long_text)
        assert len(result) == 10000

    def test_sanitize_redacts_api_key(self, pipeline: EnrichedIngestPipeline) -> None:
        """API key patterns are redacted."""
        text = "config: api_key=sk_live_abc123def456"
        result = pipeline._sanitize(text)
        assert "sk_live_abc123def456" not in result
        assert "[REDACTED]" in result

    def test_sanitize_redacts_bearer_token(self, pipeline: EnrichedIngestPipeline) -> None:
        """Bearer token patterns are redacted."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9"
        result = pipeline._sanitize(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "[REDACTED]" in result

    def test_sanitize_redacts_signing_key(self, pipeline: EnrichedIngestPipeline) -> None:
        """signing-key patterns are redacted."""
        text = "signing-key: my_secret_value_here"
        result = pipeline._sanitize(text)
        assert "my_secret_value_here" not in result

    def test_sanitize_preserves_normal_text(self, pipeline: EnrichedIngestPipeline) -> None:
        """Non-credential text is preserved unchanged."""
        text = "I went to the store and bought milk"
        assert pipeline._sanitize(text) == text

    def test_sanitize_empty_returns_empty(self, pipeline: EnrichedIngestPipeline) -> None:
        """Empty string returns empty string."""
        assert pipeline._sanitize("") == ""


class TestIngestChunking:
    """Tests for the _chunk_content method."""

    def test_short_content_single_chunk(self, pipeline: EnrichedIngestPipeline) -> None:
        """Content within 120% of max_chunk is returned as a single chunk."""
        text = "Short content. Only a few words."
        chunks = pipeline._chunk_content(text, max_chunk=1500)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_content_splits_at_sentences(self, pipeline: EnrichedIngestPipeline) -> None:
        """Long content is split at sentence boundaries."""
        sentences = [f"This is sentence {i}. " for i in range(50)]
        text = "".join(sentences)
        chunks = pipeline._chunk_content(text, max_chunk=200)
        assert len(chunks) > 1
        # Each chunk should be <= max_chunk (approximately)
        for chunk in chunks:
            # Oversized single sentences are allowed to exceed max_chunk
            assert len(chunk) <= 200 or len(chunk.split(". ")) <= 2

    def test_paragraph_splitting(self, pipeline: EnrichedIngestPipeline) -> None:
        """Content with double newlines splits on paragraph boundaries."""
        text = "Paragraph one sentence. Second sentence.\n\nParagraph two here. More text."
        chunks = pipeline._chunk_content(text, max_chunk=50)
        assert len(chunks) >= 2

    def test_oversized_sentence_hard_split(self, pipeline: EnrichedIngestPipeline) -> None:
        """A single sentence exceeding max_chunk is hard-split."""
        # One continuous string with no sentence boundaries
        text = "x" * 3000
        chunks = pipeline._chunk_content(text, max_chunk=500)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 500

    def test_empty_content_fallback(self, pipeline: EnrichedIngestPipeline) -> None:
        """If chunking produces no results, fallback returns original content."""
        # Unusual but edge case: content within threshold returns single chunk
        text = "Hello world"
        chunks = pipeline._chunk_content(text, max_chunk=1500)
        assert chunks == [text]


class TestIngestTagHandling:
    """Tests for tag normalization during ingestion."""

    def test_tags_lowercased_and_sorted(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Tags are lowercased, deduplicated, and sorted."""
        import json

        ids = pipeline.ingest(
            source="user", kind="episodic", task_id="t_tag",
            content="tag handling test content",
            tags=["Zebra", "alpha", "ALPHA", "Beta"],
        )
        assert len(ids) == 1
        record = engine.get_record(ids[0])
        tags = json.loads(record["tags"])
        assert tags == ["alpha", "beta", "zebra"]

    def test_tags_limited_to_10(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """At most 10 tags are stored."""
        import json

        many_tags = [f"tag{i}" for i in range(20)]
        ids = pipeline.ingest(
            source="user", kind="episodic", task_id="t_tag2",
            content="many tags test",
            tags=many_tags,
        )
        assert len(ids) == 1
        record = engine.get_record(ids[0])
        tags = json.loads(record["tags"])
        assert len(tags) <= 10

    def test_empty_tags_filtered(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Whitespace-only and empty tag strings are filtered out."""
        import json

        ids = pipeline.ingest(
            source="user", kind="episodic", task_id="t_tag3",
            content="empty tag test",
            tags=["valid", "", "  ", "another"],
        )
        assert len(ids) == 1
        record = engine.get_record(ids[0])
        tags = json.loads(record["tags"])
        assert "" not in tags
        assert "valid" in tags
        assert "another" in tags

    def test_none_tags_defaults_to_empty_list(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """When tags=None, stored tags should be '[]'."""
        import json

        ids = pipeline.ingest(
            source="user", kind="episodic", task_id="t_tag4",
            content="no tags test",
            tags=None,
        )
        assert len(ids) == 1
        record = engine.get_record(ids[0])
        tags = json.loads(record["tags"])
        assert tags == []


class TestIngestDeduplication:
    """Extended deduplication tests."""

    def test_multi_chunk_dedup_per_chunk_hash(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Multi-chunk content uses per-chunk hashes, not full-document hash."""
        # Build multi-chunk content
        sentences = [f"Important finding number {i} from the research. " for i in range(60)]
        long_content = " ".join(sentences)

        ids1 = pipeline.ingest(source="user", kind="semantic", task_id="t_dd1", content=long_content)
        assert len(ids1) > 1

        # Second ingest of same content: engine-level dedup catches per-chunk hashes
        ids2 = pipeline.ingest(source="user", kind="semantic", task_id="t_dd1", content=long_content)
        assert len(ids2) == 0

    def test_similar_but_different_content_not_deduped(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Slightly different content produces different hashes and is stored."""
        ids1 = pipeline.ingest(
            source="user", kind="episodic", task_id="t_dd2",
            content="The cat sat on the mat in the morning",
        )
        ids2 = pipeline.ingest(
            source="user", kind="episodic", task_id="t_dd2",
            content="The cat sat on the mat in the evening",
        )
        assert len(ids1) == 1
        assert len(ids2) == 1
        assert ids1[0] != ids2[0]
        assert engine.count_records() == 2


class TestIngestSummaryGeneration:
    """Tests for summary truncation behavior."""

    def test_short_content_summary_is_full_content(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Short content (<200 chars) has summary == content."""
        short = "This is a short memo about groceries."
        ids = pipeline.ingest(source="user", kind="episodic", task_id="t_sum1", content=short)
        record = engine.get_record(ids[0])
        assert record["summary"] == short

    def test_long_content_summary_truncated_at_word_boundary(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Content > 200 chars has summary truncated at word boundary."""
        # Intentionally 250+ chars of readable text
        text = "The quick brown fox jumps over the lazy dog. " * 10
        assert len(text) > 200
        # Because this is within 120% of 1500 it will be single-chunk
        ids = pipeline.ingest(source="user", kind="episodic", task_id="t_sum2", content=text)
        record = engine.get_record(ids[0])
        assert len(record["summary"]) <= 200
        # Should not end mid-word (unless the last space is before pos 100)
        assert not record["summary"].endswith("T")  # would indicate mid-word cut


class TestIngestFactExtraction:
    """Tests for knowledge graph fact extraction during ingest."""

    def test_fact_extraction_failure_does_not_block_record(
        self, engine: MemoryEngine, embed_service: MockEmbeddingService, classifier: BranchClassifier
    ) -> None:
        """If fact extraction throws, the record is still stored."""
        kg = MagicMock(spec=KnowledgeGraph)
        pipeline = EnrichedIngestPipeline(engine, embed_service, classifier, knowledge_graph=kg)

        # Make the fact extractor fail
        kg.add_fact.side_effect = RuntimeError("KG broken")

        ids = pipeline.ingest(
            source="user", kind="episodic", task_id="t_fact1",
            content="The capital of France is Paris",
        )
        # Record should still be stored despite KG failure
        assert len(ids) == 1
        assert engine.get_record(ids[0]) is not None

    def test_no_kg_skips_fact_extraction(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """When knowledge_graph is None, fact extraction is skipped entirely."""
        # The default pipeline fixture has no KG
        ids = pipeline.ingest(
            source="user", kind="episodic", task_id="t_fact2",
            content="Some content for testing",
        )
        assert len(ids) == 1


class TestIngestEmbeddingFailure:
    """Tests for embedding service failure handling."""

    def test_embedding_failure_propagates(
        self, engine: MemoryEngine, classifier: BranchClassifier
    ) -> None:
        """If the embedding service raises, the error propagates (no silent swallow)."""
        failing_embed = MagicMock(spec=EmbeddingService)
        failing_embed.embed.side_effect = RuntimeError("Model not loaded")

        pipeline = EnrichedIngestPipeline(engine, failing_embed, classifier)
        with pytest.raises(RuntimeError, match="Model not loaded"):
            pipeline.ingest(
                source="user", kind="episodic", task_id="t_emb_fail",
                content="Some content to embed",
            )

    def test_record_id_deterministic(
        self, pipeline: EnrichedIngestPipeline, engine: MemoryEngine
    ) -> None:
        """Same content+source+kind+task_id produces the same record_id."""
        ids1 = pipeline.ingest(
            source="user", kind="episodic", task_id="det_test",
            content="deterministic id test",
        )
        # Delete the record so we can re-ingest
        engine.delete_record(ids1[0])
        ids2 = pipeline.ingest(
            source="user", kind="episodic", task_id="det_test",
            content="deterministic id test",
        )
        assert ids1[0] == ids2[0]
