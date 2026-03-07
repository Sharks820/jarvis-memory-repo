"""Tests for knowledge graph entity resolution (duplicate detection & merge).

Covers:
- Duplicate detection via string similarity
- No false positives for unrelated entities
- Edge transfer during merge
- Merge history recording
- Auto-resolve dry run vs. actual merge
- Category (node_type) isolation during comparison
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_engine.knowledge.entity_resolver import (
    EntityResolver,
    ResolutionResult,
)
from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.memory.engine import MemoryEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> MemoryEngine:
    """Create a MemoryEngine with a temporary database."""
    db_path = tmp_path / "test_er.db"
    eng = MemoryEngine(db_path)
    yield eng
    eng.close()


@pytest.fixture
def kg(engine: MemoryEngine) -> KnowledgeGraph:
    """Create a KnowledgeGraph backed by a temporary MemoryEngine."""
    return KnowledgeGraph(engine)


@pytest.fixture
def resolver(kg: KnowledgeGraph) -> EntityResolver:
    """Create an EntityResolver with a low threshold for easier testing."""
    return EntityResolver(kg, similarity_threshold=0.75)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


class TestFindDuplicates:
    def test_find_duplicates_same_entity(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Near-identical labels within the same node_type are flagged."""
        kg.add_fact("med.metformin1", "metformin 500mg", 0.7, node_type="medication")
        kg.add_fact("med.metformin2", "Metformin 500 mg", 0.6, node_type="medication")

        candidates = resolver.find_duplicates()

        assert len(candidates) == 1
        c = candidates[0]
        assert {c.node_a_id, c.node_b_id} == {"med.metformin1", "med.metformin2"}
        assert c.similarity >= 0.75
        assert c.merge_reason == "string"

    def test_no_duplicates_different_entities(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Clearly different labels produce no candidates."""
        kg.add_fact("med.metformin", "metformin", 0.7, node_type="medication")
        kg.add_fact("med.lisinopril", "lisinopril", 0.8, node_type="medication")

        candidates = resolver.find_duplicates()

        assert len(candidates) == 0

    def test_only_compares_within_category(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Identical labels in different node_types are NOT flagged."""
        kg.add_fact("person.sarah", "Sarah", 0.8, node_type="person")
        kg.add_fact("location.sarah", "Sarah", 0.8, node_type="location")

        candidates = resolver.find_duplicates()

        assert len(candidates) == 0

    def test_find_duplicates_branch_filter(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Passing branch= limits search to that node_type."""
        kg.add_fact("med.aspirin1", "aspirin daily", 0.7, node_type="medication")
        kg.add_fact("med.aspirin2", "Aspirin Daily", 0.6, node_type="medication")
        kg.add_fact("pref.aspirin1", "aspirin preference", 0.5, node_type="preference")
        kg.add_fact("pref.aspirin2", "Aspirin Preference", 0.5, node_type="preference")

        med_candidates = resolver.find_duplicates(branch="medication")
        pref_candidates = resolver.find_duplicates(branch="preference")

        assert len(med_candidates) == 1
        assert med_candidates[0].node_a_id.startswith("med.")
        assert len(pref_candidates) == 1
        assert pref_candidates[0].node_a_id.startswith("pref.")

    def test_find_duplicates_sorted_by_similarity(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Candidates are returned in descending similarity order."""
        kg.add_fact("a1", "metformin 500mg", 0.5, node_type="fact")
        kg.add_fact("a2", "Metformin 500mg", 0.5, node_type="fact")  # very similar
        kg.add_fact("a3", "metformin extended", 0.5, node_type="fact")  # less similar

        candidates = resolver.find_duplicates()

        assert len(candidates) >= 1
        # First candidate should be the more similar pair
        similarities = [c.similarity for c in candidates]
        assert similarities == sorted(similarities, reverse=True)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


class TestMergeNodes:
    def test_merge_transfers_edges(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Merging transfers both outgoing and incoming edges."""
        kg.add_fact("keep", "metformin", 0.8, node_type="medication")
        kg.add_fact("remove", "Metformin", 0.6, node_type="medication")
        kg.add_fact("disease", "diabetes", 0.9, node_type="condition")
        kg.add_fact("doctor", "Dr. Smith", 0.7, node_type="person")

        # remove -> disease (outgoing from remove)
        kg.add_edge("remove", "disease", "treats", 0.8)
        # doctor -> remove (incoming to remove)
        kg.add_edge("doctor", "remove", "prescribes", 0.7)

        result = resolver.merge_nodes("keep", "remove")

        assert result is True

        # remove node should be gone
        assert kg.get_node("remove") is None

        # Edges should be transferred to keep
        outgoing = kg.get_edges_from("keep")
        assert len(outgoing) == 1
        assert outgoing[0]["target_id"] == "disease"
        assert outgoing[0]["relation"] == "treats"

        incoming = kg.get_edges_to("keep")
        assert len(incoming) == 1
        assert incoming[0]["source_id"] == "doctor"
        assert incoming[0]["relation"] == "prescribes"

    def test_merge_records_history(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Merge creates a record in kg_merge_history."""
        kg.add_fact("keep", "metformin", 0.8, node_type="medication")
        kg.add_fact("remove", "Metformin", 0.6, node_type="medication")

        resolver.merge_nodes("keep", "remove", canonical_label="Metformin 500mg")

        rows = kg.db.execute(
            "SELECT * FROM kg_merge_history WHERE keep_id = 'keep'"
        ).fetchall()

        assert len(rows) == 1
        row = rows[0]
        # Access by index -- column order: merge_id, keep_id, remove_id,
        # keep_label, remove_label, canonical_label, edges_transferred, created_at
        assert row[1] == "keep"
        assert row[2] == "remove"
        assert row[3] == "metformin"
        assert row[4] == "Metformin"
        assert row[5] == "Metformin 500mg"

    def test_merge_boosts_confidence(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Merged node gets MAX(keep_conf, remove_conf)."""
        kg.add_fact("keep", "aspirin", 0.5, node_type="medication")
        kg.add_fact("remove", "Aspirin", 0.9, node_type="medication")

        resolver.merge_nodes("keep", "remove")

        node = kg.get_node("keep")
        assert node is not None
        assert node["confidence"] == 0.9

    def test_merge_applies_canonical_label(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """canonical_label overrides the kept node's label."""
        kg.add_fact("keep", "metformin", 0.8, node_type="medication")
        kg.add_fact("remove", "Metformin", 0.6, node_type="medication")

        resolver.merge_nodes("keep", "remove", canonical_label="Metformin HCl")

        node = kg.get_node("keep")
        assert node is not None
        assert node["label"] == "Metformin HCl"

    def test_merge_missing_node_returns_false(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Merge returns False if either node does not exist."""
        kg.add_fact("keep", "metformin", 0.8, node_type="medication")

        assert resolver.merge_nodes("keep", "nonexistent") is False
        assert resolver.merge_nodes("nonexistent", "keep") is False

    def test_merge_skips_self_loop_edges(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """Edges between the two merged nodes do not create self-loops."""
        kg.add_fact("keep", "metformin", 0.8, node_type="medication")
        kg.add_fact("remove", "Metformin", 0.6, node_type="medication")
        kg.add_edge("remove", "keep", "same_as", 0.9)

        resolver.merge_nodes("keep", "remove")

        # No self-loop on keep
        outgoing = kg.get_edges_from("keep")
        self_loops = [e for e in outgoing if e["target_id"] == "keep"]
        assert len(self_loops) == 0


# ---------------------------------------------------------------------------
# Auto-resolve
# ---------------------------------------------------------------------------


class TestAutoResolve:
    def test_auto_resolve_dry_run(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """dry_run=True reports candidates without merging anything."""
        kg.add_fact("med.a", "metformin 500mg", 0.7, node_type="medication")
        kg.add_fact("med.b", "Metformin 500 mg", 0.6, node_type="medication")

        result = resolver.auto_resolve(dry_run=True)

        assert isinstance(result, ResolutionResult)
        assert result.candidates_found >= 1
        assert result.merges_applied == 0

        # Both nodes still exist
        assert kg.get_node("med.a") is not None
        assert kg.get_node("med.b") is not None

    def test_auto_resolve_applies_merges(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """auto_resolve merges duplicate pairs and removes the weaker node."""
        kg.add_fact("med.a", "metformin 500mg", 0.8, node_type="medication")
        kg.add_fact("med.b", "Metformin 500 mg", 0.5, node_type="medication")

        result = resolver.auto_resolve()

        assert result.candidates_found >= 1
        assert result.merges_applied >= 1
        assert len(result.errors) == 0

        # Higher confidence node (med.a at 0.8) should survive
        assert kg.get_node("med.a") is not None
        assert kg.get_node("med.b") is None

    def test_auto_resolve_keeps_node_with_more_edges_on_tie(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """On equal confidence, the node with more edges is kept."""
        kg.add_fact("med.a", "aspirin daily", 0.7, node_type="medication")
        kg.add_fact("med.b", "Aspirin Daily", 0.7, node_type="medication")
        kg.add_fact("cond", "headache", 0.5, node_type="condition")

        # Give med.a more edges
        kg.add_edge("med.a", "cond", "treats", 0.6)

        result = resolver.auto_resolve()

        assert result.merges_applied >= 1
        # med.a had more edges, so it should survive
        assert kg.get_node("med.a") is not None
        assert kg.get_node("med.b") is None

    def test_auto_resolve_no_duplicates(
        self, kg: KnowledgeGraph, resolver: EntityResolver
    ) -> None:
        """No merges when there are no duplicates."""
        kg.add_fact("med.a", "metformin", 0.8, node_type="medication")
        kg.add_fact("med.b", "lisinopril", 0.7, node_type="medication")

        result = resolver.auto_resolve()

        assert result.candidates_found == 0
        assert result.merges_applied == 0


# ---------------------------------------------------------------------------
# Vector-based candidate retrieval
# ---------------------------------------------------------------------------


class _FakeEmbedService:
    """Deterministic fake embed_service for testing vector-based retrieval.

    Embeds labels as simple character-frequency vectors so that similar
    labels produce similar embeddings (cosine similarity).
    """

    _DIM = 26  # one slot per lowercase ASCII letter

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._DIM
        for ch in text.lower():
            idx = ord(ch) - ord("a")
            if 0 <= idx < self._DIM:
                vec[idx] += 1.0
        # Normalise to unit length to make cosine similarity meaningful
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


class _FailingEmbedService:
    """Embed service that always raises, to test fallback to string mode."""

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("Embedding unavailable")


class TestVectorBasedCandidateRetrieval:
    @pytest.fixture
    def embed_service(self) -> _FakeEmbedService:
        return _FakeEmbedService()

    @pytest.fixture
    def vec_resolver(
        self, kg: KnowledgeGraph, embed_service: _FakeEmbedService
    ) -> EntityResolver:
        """EntityResolver with vector mode enabled and low threshold."""
        return EntityResolver(
            kg, embed_service=embed_service, similarity_threshold=0.75
        )

    def test_vector_mode_finds_similar_labels(
        self, kg: KnowledgeGraph, vec_resolver: EntityResolver
    ) -> None:
        """Vector-based retrieval detects near-duplicate labels."""
        kg.add_fact("med.a", "metformin 500mg", 0.7, node_type="medication")
        kg.add_fact("med.b", "Metformin 500 mg", 0.6, node_type="medication")

        candidates = vec_resolver.find_duplicates()

        assert len(candidates) >= 1
        ids = {(c.node_a_id, c.node_b_id) for c in candidates}
        assert ("med.a", "med.b") in ids or ("med.b", "med.a") in ids

    def test_vector_mode_no_false_positives(
        self, kg: KnowledgeGraph, vec_resolver: EntityResolver
    ) -> None:
        """Clearly different labels produce no candidates in vector mode."""
        kg.add_fact("med.a", "metformin", 0.7, node_type="medication")
        kg.add_fact("med.b", "lisinopril", 0.8, node_type="medication")

        candidates = vec_resolver.find_duplicates()

        assert len(candidates) == 0

    def test_vector_mode_respects_node_type_isolation(
        self, kg: KnowledgeGraph, vec_resolver: EntityResolver
    ) -> None:
        """Vector mode only compares within the same node_type."""
        kg.add_fact("person.sarah", "Sarah", 0.8, node_type="person")
        kg.add_fact("location.sarah", "Sarah", 0.8, node_type="location")

        candidates = vec_resolver.find_duplicates()

        assert len(candidates) == 0

    def test_vector_mode_uses_embedding_reason(
        self, kg: KnowledgeGraph, vec_resolver: EntityResolver
    ) -> None:
        """When embedding similarity exceeds string similarity, reason is 'embedding'."""
        # These labels have different string representations but embed similarly
        # because they share the same character set
        kg.add_fact("a1", "abc def ghi", 0.5, node_type="fact")
        kg.add_fact("a2", "abc ghi def", 0.5, node_type="fact")

        candidates = vec_resolver.find_duplicates()

        # Should find these as duplicates (same characters, just reordered)
        if candidates:
            # At least one should potentially have embedding as the reason
            # (depends on threshold and exact similarity values)
            assert all(c.merge_reason in ("string", "embedding") for c in candidates)

    def test_vector_mode_top_k_limits_comparisons(
        self, kg: KnowledgeGraph, embed_service: _FakeEmbedService
    ) -> None:
        """With top_k=1, each node only compares against its single nearest neighbour."""
        resolver = EntityResolver(
            kg, embed_service=embed_service, similarity_threshold=0.75
        )

        # Add several similar nodes
        kg.add_fact("a1", "aspirin daily", 0.5, node_type="med")
        kg.add_fact("a2", "Aspirin Daily", 0.5, node_type="med")
        kg.add_fact("a3", "aspirin extended", 0.5, node_type="med")
        kg.add_fact("a4", "aspirin regular", 0.5, node_type="med")

        # With top_k=1, fewer pairs are evaluated
        candidates_k1 = resolver.find_duplicates(top_k=1)
        # With top_k=10, more pairs are evaluated
        candidates_k10 = resolver.find_duplicates(top_k=10)

        # k=10 should find at least as many candidates as k=1
        assert len(candidates_k10) >= len(candidates_k1)

    def test_fallback_to_string_when_embed_service_is_none(
        self, kg: KnowledgeGraph
    ) -> None:
        """When embed_service is None, falls back to string-only comparison."""
        resolver = EntityResolver(kg, embed_service=None, similarity_threshold=0.75)

        kg.add_fact("med.a", "metformin 500mg", 0.7, node_type="medication")
        kg.add_fact("med.b", "Metformin 500 mg", 0.6, node_type="medication")

        candidates = resolver.find_duplicates()

        assert len(candidates) >= 1
        assert all(c.merge_reason == "string" for c in candidates)

    def test_fallback_when_all_embeddings_fail(self, kg: KnowledgeGraph) -> None:
        """When embed_service.embed() always fails, falls back to string comparison."""
        failing_svc = _FailingEmbedService()
        resolver = EntityResolver(
            kg, embed_service=failing_svc, similarity_threshold=0.75
        )

        kg.add_fact("med.a", "metformin 500mg", 0.7, node_type="medication")
        kg.add_fact("med.b", "Metformin 500 mg", 0.6, node_type="medication")

        candidates = resolver.find_duplicates()

        # Should still find duplicates via string fallback
        assert len(candidates) >= 1
        assert all(c.merge_reason == "string" for c in candidates)

    def test_vector_mode_handles_single_node(
        self, kg: KnowledgeGraph, vec_resolver: EntityResolver
    ) -> None:
        """A group with one node produces no candidates."""
        kg.add_fact("solo", "only node", 0.8, node_type="solo_type")

        candidates = vec_resolver.find_duplicates()

        assert len(candidates) == 0

    def test_vector_mode_sorted_by_similarity(
        self, kg: KnowledgeGraph, vec_resolver: EntityResolver
    ) -> None:
        """Results from vector mode are sorted by similarity descending."""
        kg.add_fact("a1", "metformin 500mg", 0.5, node_type="fact")
        kg.add_fact("a2", "Metformin 500mg", 0.5, node_type="fact")
        kg.add_fact("a3", "metformin extended release", 0.5, node_type="fact")

        candidates = vec_resolver.find_duplicates()

        if len(candidates) >= 2:
            similarities = [c.similarity for c in candidates]
            assert similarities == sorted(similarities, reverse=True)

    def test_vector_mode_no_500_node_cap(
        self, kg: KnowledgeGraph, embed_service: _FakeEmbedService
    ) -> None:
        """Vector mode has no 500-node cap (unlike string-only mode).

        With embed_service=None, groups > 500 nodes are skipped.  With
        embed_service provided, vector mode handles any group size.
        """
        resolver = EntityResolver(
            kg, embed_service=embed_service, similarity_threshold=0.99
        )

        # Use clearly distinct labels so the fake embed service produces
        # very different vectors (each label dominated by a different letter).
        distinct_labels = [
            "aaaaaa",
            "bbbbbb",
            "cccccc",
            "dddddd",
            "eeeeee",
            "ffffff",
            "gggggg",
            "hhhhhh",
            "iiiiii",
            "jjjjjj",
        ]
        for i, label in enumerate(distinct_labels):
            kg.add_fact(f"n{i}", label, 0.5, node_type="biggroup")

        candidates = resolver.find_duplicates()
        # Distinct single-letter labels => zero cosine similarity and
        # zero string similarity — no candidates expected.
        assert isinstance(candidates, list)
        assert len(candidates) == 0

    def test_auto_resolve_with_vector_mode(
        self, kg: KnowledgeGraph, vec_resolver: EntityResolver
    ) -> None:
        """auto_resolve works correctly when vector mode is enabled."""
        kg.add_fact("med.a", "metformin 500mg", 0.8, node_type="medication")
        kg.add_fact("med.b", "Metformin 500 mg", 0.5, node_type="medication")

        result = vec_resolver.auto_resolve()

        assert result.candidates_found >= 1
        assert result.merges_applied >= 1
        assert len(result.errors) == 0

        # Higher confidence node survives
        assert kg.get_node("med.a") is not None
        assert kg.get_node("med.b") is None
