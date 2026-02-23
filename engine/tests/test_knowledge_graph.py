"""Comprehensive tests for the knowledge graph subsystem.

Tests cover:
- KnowledgeGraph schema creation, node/edge CRUD, lock enforcement
- Contradiction quarantine for locked nodes
- NetworkX DiGraph reconstruction from SQLite
- FactExtractor pattern matching and normalization
- Pipeline integration: ingesting content extracts facts into kg_nodes
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.knowledge.facts import FactExtractor, FactTriple, _normalize
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.memory.ingest import EnrichedIngestPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockEmbeddingService:
    """Deterministic embedding service for testing."""

    def __init__(self, dim: int = 768) -> None:
        self._dim = dim

    def embed(self, text: str, prefix: str = "search_document") -> list[float]:
        seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16) / 1e8
        return [math.sin(seed + i * 0.1) for i in range(self._dim)]

    def embed_query(self, query: str) -> list[float]:
        return self.embed(query, prefix="search_query")


class MockClassifier:
    """Always returns 'general' for testing."""

    def classify(self, embedding: list[float], threshold: float = 0.3) -> str:
        return "general"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> MemoryEngine:
    """Create a MemoryEngine with a temporary database."""
    db_path = tmp_path / "test_kg.db"
    eng = MemoryEngine(db_path)
    yield eng
    eng.close()


@pytest.fixture
def kg(engine: MemoryEngine) -> KnowledgeGraph:
    """Create a KnowledgeGraph backed by a temporary MemoryEngine."""
    return KnowledgeGraph(engine)


@pytest.fixture
def pipeline_with_kg(engine: MemoryEngine, kg: KnowledgeGraph) -> EnrichedIngestPipeline:
    """Create a pipeline with fact extraction enabled."""
    embed_service = MockEmbeddingService()
    classifier = MockClassifier()
    return EnrichedIngestPipeline(
        engine, embed_service, classifier, knowledge_graph=kg
    )


# ---------------------------------------------------------------------------
# KnowledgeGraph Schema Tests
# ---------------------------------------------------------------------------


class TestKnowledgeGraphSchema:

    def test_kg_schema_created(self, engine: MemoryEngine, kg: KnowledgeGraph) -> None:
        """KnowledgeGraph creates kg_nodes, kg_edges, kg_contradictions tables."""
        cur = engine._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {row[0] for row in cur.fetchall()}
        assert "kg_nodes" in table_names
        assert "kg_edges" in table_names
        assert "kg_contradictions" in table_names

    def test_schema_version_bumped(self, engine: MemoryEngine, kg: KnowledgeGraph) -> None:
        """Schema version includes version 2 after KG init."""
        cur = engine._db.execute(
            "SELECT version FROM schema_version ORDER BY version"
        )
        versions = [row[0] for row in cur.fetchall()]
        assert 2 in versions


# ---------------------------------------------------------------------------
# KnowledgeGraph Node CRUD Tests
# ---------------------------------------------------------------------------


class TestKnowledgeGraphNodes:

    def test_add_fact_new_node(self, kg: KnowledgeGraph) -> None:
        """add_fact creates a new node with correct fields."""
        result = kg.add_fact(
            "health.medication.metformin",
            label="metformin",
            confidence=0.75,
            source_record="rec001",
            node_type="takes",
        )
        assert result is True

        node = kg.get_node("health.medication.metformin")
        assert node is not None
        assert node["label"] == "metformin"
        assert node["node_type"] == "takes"
        assert node["confidence"] == 0.75
        assert node["locked"] == 0
        sources = json.loads(node["sources"])
        assert "rec001" in sources

    def test_add_fact_update_existing(self, kg: KnowledgeGraph) -> None:
        """add_fact updates unlocked node, keeps MAX confidence, appends source."""
        kg.add_fact("pref.color", "blue", 0.5, source_record="src1")
        kg.add_fact("pref.color", "blue", 0.8, source_record="src2")

        node = kg.get_node("pref.color")
        assert node is not None
        # MAX(0.5, 0.8) = 0.8
        assert node["confidence"] == 0.8
        sources = json.loads(node["sources"])
        assert "src1" in sources
        assert "src2" in sources

    def test_add_fact_locked_same_value(self, kg: KnowledgeGraph) -> None:
        """add_fact on locked node with same label returns True (no-op)."""
        kg.add_fact("family.wife", "Sarah", 0.9, source_record="src1")
        # Lock the node
        kg._db.execute(
            "UPDATE kg_nodes SET locked = 1, locked_at = datetime('now'), locked_by = 'owner' WHERE node_id = ?",
            ("family.wife",),
        )
        kg._db.commit()

        result = kg.add_fact("family.wife", "Sarah", 0.5, source_record="src2")
        assert result is True
        assert kg.count_pending_contradictions() == 0

    def test_add_fact_locked_contradiction(self, kg: KnowledgeGraph) -> None:
        """add_fact on locked node with different label returns False and creates contradiction."""
        kg.add_fact("family.wife", "Sarah", 0.9, source_record="src1")
        # Lock the node
        kg._db.execute(
            "UPDATE kg_nodes SET locked = 1, locked_at = datetime('now'), locked_by = 'owner' WHERE node_id = ?",
            ("family.wife",),
        )
        kg._db.commit()

        result = kg.add_fact("family.wife", "Jessica", 0.5, source_record="src2")
        assert result is False
        assert kg.count_pending_contradictions() == 1

        # Verify contradiction record
        cur = kg._db.execute(
            "SELECT * FROM kg_contradictions WHERE node_id = ?",
            ("family.wife",),
        )
        contradiction = dict(cur.fetchone())
        assert contradiction["existing_value"] == "Sarah"
        assert contradiction["incoming_value"] == "Jessica"
        assert contradiction["status"] == "pending"


# ---------------------------------------------------------------------------
# KnowledgeGraph Edge Tests
# ---------------------------------------------------------------------------


class TestKnowledgeGraphEdges:

    def test_add_edge(self, kg: KnowledgeGraph) -> None:
        """add_edge creates edge with correct fields."""
        # Add nodes first (for FK, though SQLite doesn't enforce FK by default unless PRAGMA)
        kg.add_fact("med.metformin", "metformin", 0.75)
        kg.add_fact("cond.diabetes", "diabetes", 0.80)

        result = kg.add_edge(
            "med.metformin", "cond.diabetes", "treats", confidence=0.85, source_record="rec001"
        )
        assert result is True

        edges = kg.get_edges_from("med.metformin")
        assert len(edges) == 1
        assert edges[0]["target_id"] == "cond.diabetes"
        assert edges[0]["relation"] == "treats"
        assert edges[0]["confidence"] == 0.85

    def test_add_edge_dedup(self, kg: KnowledgeGraph) -> None:
        """add_edge with same (source_id, target_id, relation) is a no-op."""
        kg.add_fact("a", "node_a", 0.5)
        kg.add_fact("b", "node_b", 0.5)

        result1 = kg.add_edge("a", "b", "related_to", 0.5)
        result2 = kg.add_edge("a", "b", "related_to", 0.8)  # Same triple, different confidence

        assert result1 is True
        assert result2 is False  # UNIQUE constraint, not inserted
        assert kg.count_edges() == 1

    def test_get_edges_to(self, kg: KnowledgeGraph) -> None:
        """get_edges_to returns incoming edges to a node."""
        kg.add_fact("a", "a", 0.5)
        kg.add_fact("b", "b", 0.5)
        kg.add_fact("c", "c", 0.5)

        kg.add_edge("a", "c", "points_to", 0.5)
        kg.add_edge("b", "c", "also_points", 0.5)

        edges = kg.get_edges_to("c")
        assert len(edges) == 2
        source_ids = {e["source_id"] for e in edges}
        assert source_ids == {"a", "b"}


# ---------------------------------------------------------------------------
# NetworkX Bridge Tests
# ---------------------------------------------------------------------------


class TestNetworkXBridge:

    def test_to_networkx(self, kg: KnowledgeGraph) -> None:
        """to_networkx returns DiGraph with correct node/edge counts and attributes."""
        kg.add_fact("n1", "label_1", 0.8, node_type="type_a")
        kg.add_fact("n2", "label_2", 0.6, node_type="type_b")
        kg.add_fact("n3", "label_3", 0.9, node_type="type_c")
        kg.add_edge("n1", "n2", "rel_ab", 0.7)
        kg.add_edge("n2", "n3", "rel_bc", 0.5)

        G = kg.to_networkx()

        assert G.number_of_nodes() == 3
        assert G.number_of_edges() == 2

        # Verify node attributes
        assert G.nodes["n1"]["label"] == "label_1"
        assert G.nodes["n1"]["confidence"] == 0.8
        assert G.nodes["n1"]["node_type"] == "type_a"

        # Verify edge attributes
        assert G["n1"]["n2"]["relation"] == "rel_ab"
        assert G["n1"]["n2"]["confidence"] == 0.7

    def test_to_networkx_empty_graph(self, kg: KnowledgeGraph) -> None:
        """to_networkx on empty graph returns empty DiGraph."""
        G = kg.to_networkx()
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0


# ---------------------------------------------------------------------------
# FactExtractor Tests
# ---------------------------------------------------------------------------


class TestFactExtractor:

    def test_fact_extractor_health(self) -> None:
        """FactExtractor extracts medication facts from health text."""
        extractor = FactExtractor()
        facts = extractor.extract(
            "Owner takes metformin daily for diabetes management",
            source="test",
            branch="health",
        )
        assert len(facts) >= 1
        med_fact = facts[0]
        assert "metformin" in med_fact.subject
        assert med_fact.predicate == "takes"
        assert "metformin" in med_fact.object_val.lower()
        assert med_fact.confidence == 0.75

    def test_fact_extractor_family(self) -> None:
        """FactExtractor extracts family member facts."""
        extractor = FactExtractor()
        facts = extractor.extract(
            "My daughter named Emily started kindergarten",
            source="test",
            branch="family",
        )
        assert len(facts) >= 1
        family_fact = [f for f in facts if "family" in f.subject]
        assert len(family_fact) >= 1
        assert family_fact[0].predicate == "family_relation"
        assert "Emily" in family_fact[0].object_val

    def test_fact_extractor_preference(self) -> None:
        """FactExtractor extracts preference facts."""
        extractor = FactExtractor()
        facts = extractor.extract(
            "I prefer dark mode for all applications",
            source="test",
            branch="general",
        )
        assert len(facts) >= 1
        pref = [f for f in facts if "preference" in f.subject]
        assert len(pref) >= 1
        assert pref[0].confidence == 0.70

    def test_fact_extractor_cap(self) -> None:
        """FactExtractor caps extraction at 10 results."""
        extractor = FactExtractor()
        # Generate text with many potential matches
        text_parts = []
        for i in range(20):
            text_parts.append(f"He prefers option_{i} strongly.")
        text = " ".join(text_parts)

        facts = extractor.extract(text, source="test", branch="general")
        assert len(facts) <= 10

    def test_fact_extractor_skips_short_matches(self) -> None:
        """FactExtractor skips matches with object_val < 2 chars."""
        extractor = FactExtractor()
        facts = extractor.extract(
            "He likes X.",  # "X" is only 1 char
            source="test",
            branch="general",
        )
        # "X" is < 2 chars, should be filtered out
        pref_facts = [f for f in facts if f.predicate == "prefers"]
        assert len(pref_facts) == 0

    def test_normalize(self) -> None:
        """_normalize produces lowercase underscore-separated alphanumeric strings."""
        assert _normalize("Hello World") == "hello_world"
        assert _normalize("  Metformin 500mg ") == "metformin_500mg"
        assert _normalize("Special! @chars#") == "special_chars"

    def test_fact_extractor_location(self) -> None:
        """FactExtractor extracts location facts."""
        extractor = FactExtractor()
        facts = extractor.extract(
            "We live in San Francisco area",
            source="test",
            branch="general",
        )
        loc_facts = [f for f in facts if "location" in f.subject]
        assert len(loc_facts) >= 1
        assert loc_facts[0].predicate == "located_at"


# ---------------------------------------------------------------------------
# Aggregate Query Tests
# ---------------------------------------------------------------------------


class TestAggregateQueries:

    def test_count_nodes(self, kg: KnowledgeGraph) -> None:
        """count_nodes returns correct count."""
        assert kg.count_nodes() == 0
        kg.add_fact("a", "a", 0.5)
        kg.add_fact("b", "b", 0.5)
        assert kg.count_nodes() == 2

    def test_count_edges(self, kg: KnowledgeGraph) -> None:
        """count_edges returns correct count."""
        assert kg.count_edges() == 0
        kg.add_fact("a", "a", 0.5)
        kg.add_fact("b", "b", 0.5)
        kg.add_edge("a", "b", "rel", 0.5)
        assert kg.count_edges() == 1

    def test_count_locked(self, kg: KnowledgeGraph) -> None:
        """count_locked returns count of locked nodes."""
        kg.add_fact("a", "a", 0.9)
        kg.add_fact("b", "b", 0.9)
        assert kg.count_locked() == 0

        kg._db.execute(
            "UPDATE kg_nodes SET locked = 1 WHERE node_id = 'a'"
        )
        kg._db.commit()
        assert kg.count_locked() == 1


# ---------------------------------------------------------------------------
# Pipeline Integration Tests
# ---------------------------------------------------------------------------


class TestPipelineIntegration:

    def test_pipeline_extracts_facts(
        self, pipeline_with_kg: EnrichedIngestPipeline, engine: MemoryEngine, kg: KnowledgeGraph
    ) -> None:
        """End-to-end: ingest health content through pipeline, verify facts appear in kg_nodes."""
        ids = pipeline_with_kg.ingest(
            source="user",
            kind="episodic",
            task_id="task-001",
            content="Owner takes metformin daily for diabetes management. He also takes lisinopril daily for blood pressure.",
        )
        assert len(ids) >= 1

        # Facts should have been extracted into kg_nodes
        node_count = kg.count_nodes()
        assert node_count >= 1, "Expected at least 1 fact node from health content"

        # Check that a metformin-related node exists
        metformin_node = kg.get_node("health.medication.metformin")
        assert metformin_node is not None
        assert "metformin" in metformin_node["label"].lower()

    def test_pipeline_without_kg_still_works(
        self, engine: MemoryEngine
    ) -> None:
        """Pipeline with no knowledge_graph still ingests records normally."""
        embed_service = MockEmbeddingService()
        classifier = MockClassifier()
        pipeline = EnrichedIngestPipeline(engine, embed_service, classifier)

        ids = pipeline.ingest(
            source="user",
            kind="episodic",
            task_id="task-002",
            content="Owner takes aspirin daily for heart health",
        )
        assert len(ids) >= 1
        record = engine.get_record(ids[0])
        assert record is not None

    def test_pipeline_fact_extraction_failure_does_not_block_ingest(
        self, engine: MemoryEngine
    ) -> None:
        """If fact extraction raises an exception, the record is still stored."""
        embed_service = MockEmbeddingService()
        classifier = MockClassifier()

        # Create a mock KG that always raises on add_fact
        class BrokenKG:
            def add_fact(self, *args, **kwargs):
                raise RuntimeError("Simulated KG failure")
            def add_edge(self, *args, **kwargs):
                raise RuntimeError("Simulated KG failure")

        pipeline = EnrichedIngestPipeline(
            engine, embed_service, classifier, knowledge_graph=BrokenKG()
        )

        ids = pipeline.ingest(
            source="user",
            kind="episodic",
            task_id="task-003",
            content="Owner takes metformin daily for diabetes",
        )
        # Record should still be stored despite KG failure
        assert len(ids) >= 1
        record = engine.get_record(ids[0])
        assert record is not None
