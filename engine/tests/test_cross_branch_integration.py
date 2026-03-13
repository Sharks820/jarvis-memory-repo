"""Integration tests for cross-branch reasoning wired into production pipelines.

Covers:
1. _extract_facts in EnrichedIngestPipeline now calls create_cross_branch_edges
2. _build_smart_context returns a 4-tuple (memory_lines, fact_lines, cross_branch_lines, preference_lines)
3. Cross-branch connections appear in context when available
4. Graceful degradation when cross-branch operations fail
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis_engine.command_bus import CommandBus
from jarvis_engine.knowledge.facts import FactExtractor, FactTriple
from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.memory.classify import BranchClassifier
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine


# ---------------------------------------------------------------------------
# Task 1 tests: cross-branch edges wired into _extract_facts
# ---------------------------------------------------------------------------


class TestExtractFactsCrossBranch:
    """Verify that _extract_facts calls create_cross_branch_edges after fact creation."""

    def _make_pipeline(self, kg=None):
        """Create an EnrichedIngestPipeline with mocked dependencies."""
        from jarvis_engine.memory.ingest import EnrichedIngestPipeline

        engine = MagicMock(spec=MemoryEngine)
        embed_service = MagicMock(spec=EmbeddingService)
        classifier = MagicMock(spec=BranchClassifier)
        return EnrichedIngestPipeline(engine, embed_service, classifier, knowledge_graph=kg)

    def _make_triple(self, subject, predicate, object_val, confidence=0.8):
        """Create a mock triple object."""
        triple = MagicMock(spec=FactTriple)
        triple.subject = subject
        triple.predicate = predicate
        triple.object_val = object_val
        triple.confidence = confidence
        return triple

    def test_cross_branch_edges_called_after_fact_creation(self):
        """After facts are created, create_cross_branch_edges is called for each."""
        kg = MagicMock(spec=KnowledgeGraph)
        pipeline = self._make_pipeline(kg=kg)

        triples = [
            self._make_triple("health.medication.metformin", "takes", "Metformin daily"),
            self._make_triple("health.condition.diabetes", "has", "Type 2 diabetes"),
        ]

        # Mock the fact extractor
        mock_extractor = MagicMock(spec=FactExtractor)
        mock_extractor.extract.return_value = triples
        pipeline._fact_extractor = mock_extractor

        with patch(
            "jarvis_engine.learning.cross_branch.create_cross_branch_edges"
        ) as mock_cb:
            mock_cb.return_value = 1
            pipeline._extract_facts("User takes metformin", "user", "health", "rec_123")

        # Verify create_cross_branch_edges was called for each fact
        assert mock_cb.call_count == 2
        mock_cb.assert_any_call(kg, "health.medication.metformin", "rec_123")
        mock_cb.assert_any_call(kg, "health.condition.diabetes", "rec_123")

    def test_cross_branch_failure_does_not_break_fact_creation(self):
        """If create_cross_branch_edges raises, facts are still created."""
        kg = MagicMock(spec=KnowledgeGraph)
        pipeline = self._make_pipeline(kg=kg)

        triples = [
            self._make_triple("family.member.dad", "name", "Robert"),
        ]

        mock_extractor = MagicMock(spec=FactExtractor)
        mock_extractor.extract.return_value = triples
        pipeline._fact_extractor = mock_extractor

        with patch(
            "jarvis_engine.learning.cross_branch.create_cross_branch_edges",
            side_effect=RuntimeError("DB connection lost"),
        ):
            # Should NOT raise
            pipeline._extract_facts("Dad's name is Robert", "user", "family", "rec_456")

        # Facts were still added to KG
        kg.add_fact.assert_called()
        kg.add_edge.assert_called()

    def test_no_triples_means_no_cross_branch_call(self):
        """When no triples are extracted, cross-branch edges are not attempted."""
        kg = MagicMock(spec=KnowledgeGraph)
        pipeline = self._make_pipeline(kg=kg)

        mock_extractor = MagicMock(spec=FactExtractor)
        mock_extractor.extract.return_value = []
        pipeline._fact_extractor = mock_extractor

        with patch(
            "jarvis_engine.learning.cross_branch.create_cross_branch_edges"
        ) as mock_cb:
            pipeline._extract_facts("nothing useful here", "user", "general", "rec_789")

        mock_cb.assert_not_called()

    def test_cross_branch_called_with_correct_record_id(self):
        """create_cross_branch_edges receives the correct record_id for provenance."""
        kg = MagicMock(spec=KnowledgeGraph)
        pipeline = self._make_pipeline(kg=kg)

        triples = [
            self._make_triple("ops.schedule.monday", "event", "Team meeting"),
        ]

        mock_extractor = MagicMock(spec=FactExtractor)
        mock_extractor.extract.return_value = triples
        pipeline._fact_extractor = mock_extractor

        with patch(
            "jarvis_engine.learning.cross_branch.create_cross_branch_edges"
        ) as mock_cb:
            mock_cb.return_value = 0
            pipeline._extract_facts("Monday team meeting", "user", "ops", "rec_abc")

        mock_cb.assert_called_once_with(kg, "ops.schedule.monday", "rec_abc")


# ---------------------------------------------------------------------------
# Task 2 tests: _build_smart_context returns 4-tuple with cross-branch + preference lines
# ---------------------------------------------------------------------------


class TestBuildSmartContextCrossBranch:
    """Verify _build_smart_context returns a 4-tuple and includes cross-branch lines."""

    def test_returns_four_tuple(self):
        """_build_smart_context returns (memory, facts, cross_branch, preferences)."""
        import jarvis_engine.voice.pipeline as voice_pipeline_mod

        from jarvis_engine.command_bus import AppContext
        bus = MagicMock(spec=[])  # No engine attribute
        bus.ctx = AppContext()  # All None defaults
        with patch(
            "jarvis_engine.brain_memory.build_context_packet",
            return_value={"selected": []},
        ):
            result = voice_pipeline_mod._build_smart_context(bus, "test query")

        assert isinstance(result, tuple)
        assert len(result) == 4
        memory_lines, fact_lines, cross_branch_lines, preference_lines = result
        assert isinstance(memory_lines, list)
        assert isinstance(fact_lines, list)
        assert isinstance(cross_branch_lines, list)
        assert isinstance(preference_lines, list)

    def test_cross_branch_lines_populated_when_connections_found(self):
        """Cross-branch connections are formatted and included when available."""
        import jarvis_engine.voice.pipeline as voice_pipeline_mod
        from jarvis_engine.command_bus import AppContext

        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed_query.return_value = [0.1, 0.2]
        bus = MagicMock(spec=CommandBus)
        bus.ctx = AppContext(engine=MagicMock(spec=MemoryEngine), embed_service=mock_embed)

        mock_kg_instance = MagicMock(spec=KnowledgeGraph)
        mock_kg_instance.query_relevant_facts.return_value = []

        cb_result = {
            "direct_results": [],
            "cross_branch_connections": [
                {
                    "source": "health.medication.metformin",
                    "target": "family.member.dad",
                    "source_branch": "health",
                    "target_branch": "family",
                    "relation": "cross_branch_related",
                },
            ],
            "branches_involved": ["health", "family"],
        }

        with patch("jarvis_engine.memory.search.hybrid_search", return_value=[]), \
             patch("jarvis_engine.knowledge.graph.KnowledgeGraph", return_value=mock_kg_instance), \
             patch("jarvis_engine.learning.cross_branch.cross_branch_query", return_value=cb_result):
            _, _, cross_branch_lines, _ = voice_pipeline_mod._build_smart_context(bus, "dad medication")

        assert len(cross_branch_lines) == 1
        line = cross_branch_lines[0]
        assert "[health]" in line
        assert "[family]" in line
        assert "cross_branch_related" in line

    def test_cross_branch_empty_when_no_connections(self):
        """Cross-branch lines are empty when no connections found."""
        import jarvis_engine.voice.pipeline as voice_pipeline_mod
        from jarvis_engine.command_bus import AppContext

        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed_query.return_value = [0.1, 0.2]
        bus = MagicMock(spec=CommandBus)
        bus.ctx = AppContext(engine=MagicMock(spec=MemoryEngine), embed_service=mock_embed)

        mock_kg_instance = MagicMock(spec=KnowledgeGraph)
        mock_kg_instance.query_relevant_facts.return_value = []

        cb_result = {
            "direct_results": [],
            "cross_branch_connections": [],
            "branches_involved": [],
        }

        with patch("jarvis_engine.memory.search.hybrid_search", return_value=[]), \
             patch("jarvis_engine.knowledge.graph.KnowledgeGraph", return_value=mock_kg_instance), \
             patch("jarvis_engine.learning.cross_branch.cross_branch_query", return_value=cb_result):
            _, _, cross_branch_lines, _ = voice_pipeline_mod._build_smart_context(bus, "random query")

        assert cross_branch_lines == []

    def test_cross_branch_query_failure_returns_empty_lines(self):
        """When cross_branch_query raises, cross_branch_lines is empty (graceful degradation)."""
        import jarvis_engine.voice.pipeline as voice_pipeline_mod
        from jarvis_engine.command_bus import AppContext

        mock_embed = MagicMock(spec=EmbeddingService)
        mock_embed.embed_query.return_value = [0.1, 0.2]
        bus = MagicMock(spec=CommandBus)
        bus.ctx = AppContext(engine=MagicMock(spec=MemoryEngine), embed_service=mock_embed)

        mock_kg_instance = MagicMock(spec=KnowledgeGraph)
        mock_kg_instance.query_relevant_facts.return_value = []

        with patch("jarvis_engine.memory.search.hybrid_search", return_value=[]), \
             patch("jarvis_engine.knowledge.graph.KnowledgeGraph", return_value=mock_kg_instance), \
             patch(
                 "jarvis_engine.learning.cross_branch.cross_branch_query",
                 side_effect=RuntimeError("cross-branch DB error"),
             ):
            _, _, cross_branch_lines, _ = voice_pipeline_mod._build_smart_context(bus, "query")

        assert cross_branch_lines == []

    def test_cross_branch_skipped_when_no_engine(self):
        """Cross-branch query is not attempted when engine is unavailable."""
        import jarvis_engine.voice.pipeline as voice_pipeline_mod
        from jarvis_engine.command_bus import AppContext

        bus = MagicMock(spec=[])
        bus.ctx = AppContext()  # engine=None by default

        with patch(
            "jarvis_engine.brain_memory.build_context_packet",
            return_value={"selected": []},
        ), patch(
            "jarvis_engine.learning.cross_branch.cross_branch_query"
        ) as mock_cb_query:
            _, _, cross_branch_lines, _ = voice_pipeline_mod._build_smart_context(bus, "test")

        mock_cb_query.assert_not_called()
        assert cross_branch_lines == []

    def test_cross_branch_skipped_when_no_embed_service(self):
        """Cross-branch query is not attempted when embed_service is unavailable."""
        import jarvis_engine.voice.pipeline as voice_pipeline_mod
        from jarvis_engine.command_bus import AppContext

        bus = MagicMock(spec=CommandBus)
        bus.ctx = AppContext(engine=MagicMock(spec=MemoryEngine), embed_service=None)

        mock_kg_instance = MagicMock(spec=KnowledgeGraph)
        mock_kg_instance.query_relevant_facts.return_value = []

        with patch(
            "jarvis_engine.brain_memory.build_context_packet",
            return_value={"selected": []},
        ), patch(
            "jarvis_engine.knowledge.graph.KnowledgeGraph",
            return_value=mock_kg_instance,
        ), patch(
            "jarvis_engine.learning.cross_branch.cross_branch_query"
        ) as mock_cb_query:
            _, _, cross_branch_lines, _ = voice_pipeline_mod._build_smart_context(bus, "test")

        mock_cb_query.assert_not_called()
        assert cross_branch_lines == []
