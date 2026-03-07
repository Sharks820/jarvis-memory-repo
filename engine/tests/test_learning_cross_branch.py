"""Comprehensive tests for jarvis_engine.learning.cross_branch module.

Covers keyword extraction, branch extraction, cross-branch querying,
and cross-branch edge creation.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import networkx as nx

from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine

from jarvis_engine.learning.cross_branch import (
    _extract_branch,
    _extract_keywords,
    create_cross_branch_edges,
    cross_branch_query,
)


# ---------------------------------------------------------------------------
# _extract_branch tests
# ---------------------------------------------------------------------------

class TestExtractBranch:
    def test_dot_separated_returns_first_part(self):
        assert _extract_branch("family.member.dad") == "family"

    def test_colon_separated_returns_first_part(self):
        assert _extract_branch("ingest:abc123") == "ingest"

    def test_single_word_returns_itself(self):
        assert _extract_branch("preference") == "preference"

    def test_empty_string_returns_none(self):
        assert _extract_branch("") is None

    def test_multiple_dots(self):
        assert _extract_branch("ops.schedule.monday") == "ops"

    def test_colon_with_dots(self):
        # Colon takes priority
        assert _extract_branch("ingest:family.member") == "ingest"


# ---------------------------------------------------------------------------
# _extract_keywords tests
# ---------------------------------------------------------------------------

class TestExtractKeywords:
    def test_empty_label(self):
        assert _extract_keywords("") == []

    def test_short_words_filtered(self):
        result = _extract_keywords("the cat sat on a mat")
        assert "the" not in result
        assert "cat" not in result  # 3 chars, below 4

    def test_stop_words_filtered(self):
        result = _extract_keywords("this is about their plans with family")
        assert "this" not in result
        assert "about" not in result
        assert "their" not in result
        assert "with" not in result
        assert "plans" in result
        assert "family" in result

    def test_deduplication(self):
        result = _extract_keywords("family family family plans")
        assert result.count("family") == 1

    def test_case_insensitive(self):
        result = _extract_keywords("Family PLANS")
        assert "family" in result
        assert "plans" in result

    def test_non_alpha_split(self):
        result = _extract_keywords("doctor-appointment 2026-schedule")
        assert "doctor" in result
        assert "appointment" in result
        assert "schedule" in result

    def test_preserves_order(self):
        result = _extract_keywords("exercise routine prescription medication")
        assert result == ["exercise", "routine", "prescription", "medication"]

    def test_min_keyword_length(self):
        result = _extract_keywords("at on it do go up")
        assert result == []  # all below 4 chars

    def test_exactly_4_chars_included(self):
        result = _extract_keywords("home work play")
        assert "home" in result
        assert "work" in result
        assert "play" in result


# ---------------------------------------------------------------------------
# cross_branch_query tests
# ---------------------------------------------------------------------------

class TestCrossBranchQuery:
    def _setup_mocks(self, vec_results=None, graph_nodes=None, graph_edges=None):
        """Create mock engine, kg, embed_service with configurable returns."""
        engine = MagicMock(spec=MemoryEngine)
        kg = MagicMock(spec=KnowledgeGraph)
        embed_service = MagicMock(spec=EmbeddingService)

        embed_service.embed.return_value = [0.1, 0.2, 0.3]
        engine.search_vec.return_value = vec_results or []

        # Build a mock NetworkX graph
        mock_G = MagicMock(spec=nx.DiGraph)
        kg.to_networkx.return_value = mock_G

        # Default: no nodes in graph
        mock_G.__contains__ = MagicMock(return_value=False)
        mock_G.neighbors.return_value = iter([])
        mock_G.predecessors.return_value = iter([])

        if graph_nodes:
            mock_G.__contains__ = MagicMock(side_effect=lambda x: x in graph_nodes)

        if graph_edges:
            mock_G.edges = graph_edges

        return engine, kg, embed_service, mock_G

    def test_empty_results(self):
        engine, kg, embed_svc, _ = self._setup_mocks()
        result = cross_branch_query("test query", engine, kg, embed_svc)
        assert result["direct_results"] == []
        assert result["cross_branch_connections"] == []
        assert result["branches_involved"] == []

    def test_embeds_query(self):
        engine, kg, embed_svc, _ = self._setup_mocks()
        cross_branch_query("what is my schedule", engine, kg, embed_svc)
        embed_svc.embed.assert_called_once_with("what is my schedule", prefix="search_query")

    def test_searches_with_doubled_limit(self):
        engine, kg, embed_svc, _ = self._setup_mocks()
        cross_branch_query("test", engine, kg, embed_svc, k=5)
        engine.search_vec.assert_called_once()
        _, kwargs = engine.search_vec.call_args
        assert kwargs["limit"] == 10  # k*2

    def test_direct_results_populated(self):
        engine, kg, embed_svc, _ = self._setup_mocks(
            vec_results=[("rec_1", 0.05), ("rec_2", 0.12)],
        )
        result = cross_branch_query("test", engine, kg, embed_svc, k=5)
        assert len(result["direct_results"]) == 2
        assert result["direct_results"][0]["record_id"] == "rec_1"
        assert result["direct_results"][0]["distance"] == 0.05

    def test_cross_branch_connections_found(self):
        """Test that cross-branch connections are detected via graph neighbors."""
        engine, kg, embed_svc, mock_G = self._setup_mocks(
            vec_results=[("rec_1", 0.05)],
            graph_nodes={"ingest:rec_1", "family.member.dad"},
            graph_edges={
                ("ingest:rec_1", "family.member.dad"): {"relation": "mentions"},
            },
        )
        # When checking for neighbors of ingest:rec_1
        mock_G.neighbors.return_value = iter(["family.member.dad"])
        mock_G.predecessors.return_value = iter([])

        result = cross_branch_query("test", engine, kg, embed_svc, k=5)
        assert len(result["cross_branch_connections"]) == 1
        conn = result["cross_branch_connections"][0]
        assert conn["source"] == "ingest:rec_1"
        assert conn["target"] == "family.member.dad"
        assert conn["target_branch"] == "family"

    def test_branches_involved_tracked(self):
        engine, kg, embed_svc, mock_G = self._setup_mocks(
            vec_results=[("rec_1", 0.05)],
            graph_nodes={"ingest:rec_1", "ops.schedule.monday"},
            graph_edges={
                ("ingest:rec_1", "ops.schedule.monday"): {"relation": "related"},
            },
        )
        mock_G.neighbors.return_value = iter(["ops.schedule.monday"])
        mock_G.predecessors.return_value = iter([])

        result = cross_branch_query("test", engine, kg, embed_svc, k=5)
        assert "ingest" in result["branches_involved"]
        assert "ops" in result["branches_involved"]

    def test_limits_to_k_results(self):
        engine, kg, embed_svc, _ = self._setup_mocks(
            vec_results=[(f"rec_{i}", 0.01 * i) for i in range(20)],
        )
        result = cross_branch_query("test", engine, kg, embed_svc, k=3)
        assert len(result["direct_results"]) == 3

    def test_predecessor_connections(self):
        """Test incoming edges (predecessors) from other branches."""
        engine, kg, embed_svc, mock_G = self._setup_mocks(
            vec_results=[("rec_1", 0.05)],
            graph_nodes={"ingest:rec_1", "health.medication.vitd"},
            graph_edges={
                ("health.medication.vitd", "ingest:rec_1"): {"relation": "sourced_from"},
            },
        )
        mock_G.neighbors.return_value = iter([])
        mock_G.predecessors.return_value = iter(["health.medication.vitd"])

        result = cross_branch_query("test", engine, kg, embed_svc, k=5)
        assert len(result["cross_branch_connections"]) == 1
        conn = result["cross_branch_connections"][0]
        assert conn["source"] == "health.medication.vitd"
        assert conn["source_branch"] == "health"


# ---------------------------------------------------------------------------
# create_cross_branch_edges tests
# ---------------------------------------------------------------------------

class TestCreateCrossBranchEdges:
    def test_no_node_returns_zero(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.get_node.return_value = None
        result = create_cross_branch_edges(kg, "family.test", "rec_1")
        assert result == 0

    def test_empty_label_returns_zero(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.get_node.return_value = {"label": ""}
        result = create_cross_branch_edges(kg, "family.test", "rec_1")
        assert result == 0

    def test_no_keywords_returns_zero(self):
        kg = MagicMock(spec=KnowledgeGraph)
        # Label with only short/stop words
        kg.get_node.return_value = {"label": "the cat is on it"}
        result = create_cross_branch_edges(kg, "family.test", "rec_1")
        assert result == 0

    def test_creates_edges_for_cross_branch_matches(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.get_node.return_value = {"label": "family doctor appointment scheduled"}

        # Mock DB query returning matches in different branches
        cursor = MagicMock(spec=sqlite3.Cursor)
        cursor.fetchall.return_value = [
            ("health.doctor.appt", "Regular doctor appointment"),
        ]
        kg.db.execute.return_value = cursor
        kg.add_edge.return_value = True

        result = create_cross_branch_edges(kg, "family.doctor.visit", "rec_1")
        assert result >= 1
        kg.add_edge.assert_called()
        # Check the edge parameters
        call_kwargs = kg.add_edge.call_args[1]
        assert call_kwargs["relation"] == "cross_branch_related"
        assert call_kwargs["confidence"] == 0.4

    def test_skips_same_branch_matches(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.get_node.return_value = {"label": "family dinner planning"}

        cursor = MagicMock(spec=sqlite3.Cursor)
        # Match is in the same branch (family)
        cursor.fetchall.return_value = [
            ("family.events.dinner", "Family dinner event"),
        ]
        kg.db.execute.return_value = cursor
        kg.add_edge.return_value = True

        result = create_cross_branch_edges(kg, "family.planning.dinner", "rec_1")
        assert result == 0

    def test_caps_keywords_at_5(self):
        kg = MagicMock(spec=KnowledgeGraph)
        # Label with many keywords
        kg.get_node.return_value = {
            "label": "family doctor appointment scheduled tuesday morning routine exercise"
        }
        cursor = MagicMock(spec=sqlite3.Cursor)
        cursor.fetchall.return_value = []
        kg.db.execute.return_value = cursor

        create_cross_branch_edges(kg, "family.test", "rec_1")
        # Should only query for up to 5 keywords
        assert kg.db.execute.call_count <= 5

    def test_handles_db_exception_gracefully(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.get_node.return_value = {"label": "family doctor appointment"}
        kg.db.execute.side_effect = sqlite3.OperationalError("DB error")

        # Should not raise, just return 0
        result = create_cross_branch_edges(kg, "family.test", "rec_1")
        assert result == 0

    def test_add_edge_returns_false_not_counted(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.get_node.return_value = {"label": "family doctor appointment"}

        cursor = MagicMock(spec=sqlite3.Cursor)
        cursor.fetchall.return_value = [
            ("health.doctor.appt", "Doctor appointment"),
        ]
        kg.db.execute.return_value = cursor
        kg.add_edge.return_value = False  # Edge already exists

        result = create_cross_branch_edges(kg, "family.test", "rec_1")
        assert result == 0

    def test_non_dict_node_returns_zero(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.get_node.return_value = "not a dict"
        result = create_cross_branch_edges(kg, "family.test", "rec_1")
        # label extraction from non-dict yields "", so no keywords
        assert result == 0

    def test_keywords_are_pure_alpha_safe_for_sql(self):
        """Verify keywords extracted are pure alpha (no SQL wildcards)."""
        kg = MagicMock(spec=KnowledgeGraph)
        kg.get_node.return_value = {"label": "family doctor appointment"}

        cursor = MagicMock(spec=sqlite3.Cursor)
        cursor.fetchall.return_value = []
        kg.db.execute.return_value = cursor

        create_cross_branch_edges(kg, "family.test", "rec_1")
        # Verify SQL queries were made with parameterized LIKE patterns
        assert kg.db.execute.call_count > 0
        for call in kg.db.execute.call_args_list:
            sql = call[0][0]
            params = call[0][1]
            # SQL should use parameterized queries, not string interpolation
            assert "LIKE ?" in sql
            # The keyword pattern should be wrapped in %..%
            assert params[0].startswith("%")
            assert params[0].endswith("%")
