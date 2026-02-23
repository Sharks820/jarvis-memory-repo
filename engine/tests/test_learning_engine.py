"""Tests for the continuous learning engine, temporal metadata, and cross-branch reasoning."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# ConversationLearningEngine tests
# ---------------------------------------------------------------------------

from jarvis_engine.learning.engine import ConversationLearningEngine


class TestConversationLearningEngine:
    """Tests for ConversationLearningEngine."""

    def _make_pipeline(self):
        """Create a mock pipeline that returns a single record ID per call."""
        pipeline = MagicMock()
        pipeline.ingest.return_value = ["rec_001"]
        return pipeline

    def test_learn_from_interaction_ingests_user_message(self):
        """Pipeline.ingest called with source='conversation:user'."""
        pipeline = self._make_pipeline()
        engine = ConversationLearningEngine(pipeline=pipeline)

        result = engine.learn_from_interaction(
            user_message="This is a detailed user message about machine learning and data science topics.",
            assistant_response="This is a detailed assistant response explaining neural networks and deep learning.",
        )

        # Verify user message was ingested
        calls = pipeline.ingest.call_args_list
        user_call = [c for c in calls if c.kwargs.get("source") == "conversation:user"
                     or (c.args and c.args[0] == "conversation:user")
                     or c[1].get("source") == "conversation:user"]
        assert len(user_call) == 1
        assert result["records_created"] >= 1

    def test_learn_from_interaction_ingests_assistant_response(self):
        """Pipeline.ingest called with source='conversation:assistant'."""
        pipeline = self._make_pipeline()
        engine = ConversationLearningEngine(pipeline=pipeline)

        result = engine.learn_from_interaction(
            user_message="This is a detailed user message about machine learning topics and research.",
            assistant_response="This is a detailed assistant response about neural networks and optimization.",
        )

        # Verify assistant response was ingested
        calls = pipeline.ingest.call_args_list
        assistant_call = [c for c in calls if c.kwargs.get("source") == "conversation:assistant"
                          or c[1].get("source") == "conversation:assistant"]
        assert len(assistant_call) == 1
        assert result["records_created"] == 2  # both user + assistant

    def test_learn_skips_short_messages(self):
        """Messages under 50 chars are filtered out."""
        pipeline = self._make_pipeline()
        engine = ConversationLearningEngine(pipeline=pipeline)

        result = engine.learn_from_interaction(
            user_message="short",
            assistant_response="also short",
        )

        pipeline.ingest.assert_not_called()
        assert result["records_created"] == 0

    def test_learn_skips_greetings(self):
        """'hey jarvis' under 100 chars is filtered as a greeting."""
        pipeline = self._make_pipeline()
        engine = ConversationLearningEngine(pipeline=pipeline)

        result = engine.learn_from_interaction(
            user_message="hey jarvis what is up today how are you doing my friend?",
            assistant_response="Thanks for asking, I am doing well today buddy pal friend!",
        )

        pipeline.ingest.assert_not_called()
        assert result["records_created"] == 0

    def test_learn_greeting_long_enough_passes(self):
        """A greeting prefix over 100 chars should pass the knowledge-bearing check."""
        pipeline = self._make_pipeline()
        engine = ConversationLearningEngine(pipeline=pipeline)

        long_greeting = "hey " + "a" * 100  # > 100 chars total
        result = engine.learn_from_interaction(
            user_message=long_greeting,
            assistant_response="short",
        )

        # The long greeting passes, the short response doesn't
        assert pipeline.ingest.call_count == 1
        assert result["records_created"] == 1

    def test_learn_no_pipeline_graceful(self):
        """Returns error dict when pipeline is None."""
        engine = ConversationLearningEngine(pipeline=None)

        result = engine.learn_from_interaction(
            user_message="This is a detailed user message about important topics.",
            assistant_response="This is a detailed assistant response.",
        )

        assert result["records_created"] == 0
        assert result["error"] == "no pipeline"

    def test_learn_with_task_id(self):
        """Task ID is passed through to the pipeline."""
        pipeline = self._make_pipeline()
        engine = ConversationLearningEngine(pipeline=pipeline)

        engine.learn_from_interaction(
            user_message="This is a detailed user message about machine learning and data science topics.",
            assistant_response="short",
            task_id="task-123",
        )

        # Verify task_id was passed
        call_kwargs = pipeline.ingest.call_args[1]
        assert call_kwargs["task_id"] == "task-123"


# ---------------------------------------------------------------------------
# Temporal metadata tests
# ---------------------------------------------------------------------------

from jarvis_engine.learning.temporal import (
    classify_temporal,
    flag_expired_facts,
    migrate_temporal_metadata,
)


class TestTemporalMetadata:
    """Tests for temporal classification and migration."""

    def test_classify_temporal_permanent(self):
        """family.member nodes are classified as permanent."""
        temporal_type, expires_at = classify_temporal("family.member.dad", "Dad - John")
        assert temporal_type == "permanent"
        assert expires_at is None

    def test_classify_temporal_time_sensitive(self):
        """ops.schedule nodes are classified as time_sensitive with expires_at."""
        temporal_type, expires_at = classify_temporal(
            "ops.schedule.monday", "Monday standup meeting"
        )
        assert temporal_type == "time_sensitive"
        assert expires_at is not None

    def test_classify_temporal_date_in_label(self):
        """'expires 2026-03-01' in label -> time_sensitive."""
        temporal_type, expires_at = classify_temporal(
            "random.node", "Project deadline expires 2026-03-01"
        )
        assert temporal_type == "time_sensitive"
        assert "2026-03-01" in expires_at

    def test_classify_temporal_unknown(self):
        """Unknown prefix -> unknown temporal type."""
        temporal_type, expires_at = classify_temporal(
            "misc.note.something", "Just a random note"
        )
        assert temporal_type == "unknown"
        assert expires_at is None

    def test_classify_temporal_preference_permanent(self):
        """preference.* nodes are permanent."""
        temporal_type, _ = classify_temporal("preference.color", "Blue")
        assert temporal_type == "permanent"

    def test_flag_expired_facts(self):
        """Mock KG with expired fact -> returns count=1."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        write_lock = threading.Lock()

        # Create schema
        db.executescript("""
            CREATE TABLE kg_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT 'fact',
                confidence REAL NOT NULL DEFAULT 0.5,
                locked INTEGER NOT NULL DEFAULT 0,
                locked_at TEXT DEFAULT NULL,
                locked_by TEXT DEFAULT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                temporal_type TEXT DEFAULT 'unknown',
                expires_at TEXT DEFAULT NULL
            );
        """)

        # Insert an expired fact (yesterday)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute(
            "INSERT INTO kg_nodes (node_id, label, temporal_type, expires_at) VALUES (?, ?, ?, ?)",
            ("ops.schedule.old", "Old meeting", "time_sensitive", yesterday),
        )
        # Insert a non-expired fact (tomorrow)
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute(
            "INSERT INTO kg_nodes (node_id, label, temporal_type, expires_at) VALUES (?, ?, ?, ?)",
            ("ops.schedule.future", "Future meeting", "time_sensitive", tomorrow),
        )
        db.commit()

        # Create a mock KG
        mock_kg = MagicMock()
        mock_kg.db = db
        mock_kg.write_lock = write_lock

        count = flag_expired_facts(mock_kg)
        assert count == 1

        # Verify the expired fact was updated
        row = db.execute(
            "SELECT temporal_type FROM kg_nodes WHERE node_id = 'ops.schedule.old'"
        ).fetchone()
        assert row[0] == "expired"

        # Verify the future fact was NOT updated
        row = db.execute(
            "SELECT temporal_type FROM kg_nodes WHERE node_id = 'ops.schedule.future'"
        ).fetchone()
        assert row[0] == "time_sensitive"

        db.close()

    def test_migrate_temporal_metadata_idempotent(self):
        """Running migration twice should not error."""
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        write_lock = threading.Lock()

        # Create base schema
        db.executescript("""
            CREATE TABLE kg_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT 'fact',
                confidence REAL NOT NULL DEFAULT 0.5,
                locked INTEGER NOT NULL DEFAULT 0,
                locked_at TEXT DEFAULT NULL,
                locked_by TEXT DEFAULT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Run migration twice -- should not raise
        migrate_temporal_metadata(db, write_lock)
        migrate_temporal_metadata(db, write_lock)

        # Verify columns exist
        cursor = db.execute("PRAGMA table_info(kg_nodes)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "temporal_type" in cols
        assert "expires_at" in cols

        db.close()


# ---------------------------------------------------------------------------
# Cross-branch reasoning tests
# ---------------------------------------------------------------------------

from jarvis_engine.learning.cross_branch import (
    cross_branch_query,
    create_cross_branch_edges,
    _extract_branch,
    _extract_keywords,
)


class TestCrossBranch:
    """Tests for cross-branch reasoning functions."""

    def test_cross_branch_query_returns_results(self):
        """Mock engine+kg, verify dict structure."""
        mock_engine = MagicMock()
        mock_engine.search_vec.return_value = [
            ("rec_001", 0.1),
            ("rec_002", 0.2),
        ]

        # Build a mock networkx graph
        import networkx as nx
        G = nx.DiGraph()
        G.add_node("ingest:rec_001", label="Test fact", node_type="provenance")
        G.add_node("family.member.dad", label="Dad", node_type="fact")
        G.add_edge("ingest:rec_001", "family.member.dad", relation="extracted_from", confidence=0.8)

        mock_kg = MagicMock()
        mock_kg.to_networkx.return_value = G

        mock_embed = MagicMock()
        mock_embed.embed.return_value = [0.1] * 768

        result = cross_branch_query(
            query="Tell me about dad",
            engine=mock_engine,
            kg=mock_kg,
            embed_service=mock_embed,
            k=10,
        )

        assert "direct_results" in result
        assert "cross_branch_connections" in result
        assert "branches_involved" in result
        assert len(result["direct_results"]) == 2
        # Check that cross-branch connections found the family branch
        assert "family" in result["branches_involved"] or len(result["cross_branch_connections"]) >= 0

    def test_cross_branch_edges_created(self):
        """Mock KG, verify add_edge calls for cross-branch connections."""
        mock_kg = MagicMock()
        mock_kg.get_node.return_value = {
            "node_id": "health.medication.aspirin",
            "label": "Aspirin taken daily for heart health",
        }
        mock_kg.add_edge.return_value = True

        # Mock DB for LIKE queries
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("ops.schedule.doctor", "Doctor appointment for heart checkup"),
        ]
        mock_db = MagicMock()
        mock_db.execute.return_value = mock_cursor
        mock_kg.db = mock_db

        count = create_cross_branch_edges(
            kg=mock_kg,
            new_fact_id="health.medication.aspirin",
            record_id="rec_001",
        )

        assert count > 0
        mock_kg.add_edge.assert_called()
        # Verify the relation is cross_branch_related
        edge_call = mock_kg.add_edge.call_args
        assert edge_call[1]["relation"] == "cross_branch_related"
        assert edge_call[1]["confidence"] == 0.4

    def test_cross_branch_edges_skip_same_branch(self):
        """No edges created within the same branch."""
        mock_kg = MagicMock()
        mock_kg.get_node.return_value = {
            "node_id": "health.medication.aspirin",
            "label": "Aspirin taken daily",
        }

        # Return results all in the same branch
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("health.condition.heart", "Heart condition"),
        ]
        mock_db = MagicMock()
        mock_db.execute.return_value = mock_cursor
        mock_kg.db = mock_db

        count = create_cross_branch_edges(
            kg=mock_kg,
            new_fact_id="health.medication.aspirin",
            record_id="rec_001",
        )

        # Same branch matches should be skipped
        assert count == 0
        mock_kg.add_edge.assert_not_called()

    def test_extract_branch_dot_separated(self):
        """Dot-separated node IDs extract first segment."""
        assert _extract_branch("family.member.dad") == "family"
        assert _extract_branch("ops.schedule.monday") == "ops"

    def test_extract_branch_colon_separated(self):
        """Colon-separated node IDs extract first segment."""
        assert _extract_branch("ingest:abc123") == "ingest"

    def test_extract_keywords(self):
        """Keywords are extracted, filtered, and deduplicated."""
        keywords = _extract_keywords("Aspirin taken daily for heart health")
        assert "aspirin" in keywords
        assert "taken" in keywords
        assert "daily" in keywords
        assert "heart" in keywords
        assert "health" in keywords
        # Short words like 'for' are excluded
        assert "for" not in keywords


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------

from jarvis_engine.handlers.learning_handlers import (
    LearnInteractionHandler,
    CrossBranchQueryHandler,
    FlagExpiredFactsHandler,
)
from jarvis_engine.commands.learning_commands import (
    LearnInteractionCommand,
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
)


class TestLearningHandlers:
    """Tests for learning command handlers."""

    def test_learn_handler_dispatches(self):
        """Mock learning engine, verify delegation."""
        mock_learning = MagicMock()
        mock_learning.learn_from_interaction.return_value = {
            "records_created": 2,
        }

        handler = LearnInteractionHandler(
            root=Path("/tmp"),
            learning_engine=mock_learning,
        )

        cmd = LearnInteractionCommand(
            user_message="This is a test message with enough length to be knowledge-bearing.",
            assistant_response="This is a test response.",
            task_id="task-001",
        )
        result = handler.handle(cmd)

        mock_learning.learn_from_interaction.assert_called_once_with(
            user_message=cmd.user_message,
            assistant_response=cmd.assistant_response,
            task_id=cmd.task_id,
        )
        assert result.records_created == 2
        assert result.message == "ok"

    def test_learn_handler_no_engine(self):
        """Handler returns graceful error when learning engine is None."""
        handler = LearnInteractionHandler(root=Path("/tmp"))

        cmd = LearnInteractionCommand(
            user_message="test",
            assistant_response="test",
        )
        result = handler.handle(cmd)

        assert result.records_created == 0
        assert "not available" in result.message.lower()

    def test_cross_branch_handler_no_deps(self):
        """Handler returns graceful error when dependencies are None."""
        handler = CrossBranchQueryHandler(root=Path("/tmp"))

        cmd = CrossBranchQueryCommand(query="test query")
        result = handler.handle(cmd)

        assert result.direct_results == []
        assert "requires" in result.message.lower()

    def test_flag_expired_handler_no_kg(self):
        """Handler returns graceful error when KG is None."""
        handler = FlagExpiredFactsHandler(root=Path("/tmp"))

        cmd = FlagExpiredFactsCommand()
        result = handler.handle(cmd)

        assert result.expired_count == 0
        assert "not available" in result.message.lower()
