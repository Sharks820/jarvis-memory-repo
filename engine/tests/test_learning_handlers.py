"""Comprehensive tests for learning handler classes in learning_handlers.py.

Covers LearnInteractionHandler, CrossBranchQueryHandler, and
FlagExpiredFactsHandler -- including all edge cases, error paths,
and fallback behaviour when dependencies are unavailable.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from jarvis_engine.commands.learning_commands import (
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
    LearnInteractionCommand,
)
from jarvis_engine.handlers.learning_handlers import (
    CrossBranchQueryHandler,
    FlagExpiredFactsHandler,
    LearnInteractionHandler,
)
from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.learning.engine import ConversationLearningEngine
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine


# ---------------------------------------------------------------------------
# LearnInteractionHandler
# ---------------------------------------------------------------------------


class TestLearnInteractionHandler:
    """Tests for LearnInteractionHandler."""

    def test_no_engine_returns_not_available(self, tmp_path: Path) -> None:
        handler = LearnInteractionHandler(root=tmp_path, learning_engine=None)
        result = handler.handle(LearnInteractionCommand())
        assert result.message == "Learning engine not available."
        assert result.records_created == 0

    def test_successful_learning(self, tmp_path: Path) -> None:
        engine = MagicMock(spec=ConversationLearningEngine)
        engine.learn_from_interaction.return_value = {
            "records_created": 3,
        }
        handler = LearnInteractionHandler(root=tmp_path, learning_engine=engine)
        result = handler.handle(
            LearnInteractionCommand(
                user_message="What is Jarvis?",
                assistant_response="Jarvis is your personal AI assistant.",
                task_id="t1",
            )
        )
        assert result.records_created == 3
        assert result.message == "ok"
        engine.learn_from_interaction.assert_called_once_with(
            user_message="What is Jarvis?",
            assistant_response="Jarvis is your personal AI assistant.",
            task_id="t1",
            route="",
            topic="",
        )

    def test_learning_returns_error(self, tmp_path: Path) -> None:
        engine = MagicMock(spec=ConversationLearningEngine)
        engine.learn_from_interaction.return_value = {
            "error": "failed to extract facts",
        }
        handler = LearnInteractionHandler(root=tmp_path, learning_engine=engine)
        result = handler.handle(LearnInteractionCommand(user_message="test"))
        assert result.records_created == 0
        assert result.message == "failed to extract facts"

    def test_empty_interaction(self, tmp_path: Path) -> None:
        """Handler processes empty strings without crashing."""
        engine = MagicMock(spec=ConversationLearningEngine)
        engine.learn_from_interaction.return_value = {"records_created": 0}
        handler = LearnInteractionHandler(root=tmp_path, learning_engine=engine)
        result = handler.handle(LearnInteractionCommand())
        assert result.records_created == 0
        assert result.message == "ok"

    def test_records_created_defaults_to_zero(self, tmp_path: Path) -> None:
        """When the engine returns no 'records_created' key, default is 0."""
        engine = MagicMock(spec=ConversationLearningEngine)
        engine.learn_from_interaction.return_value = {}
        handler = LearnInteractionHandler(root=tmp_path, learning_engine=engine)
        result = handler.handle(LearnInteractionCommand(user_message="hi"))
        assert result.records_created == 0

    def test_task_id_forwarded(self, tmp_path: Path) -> None:
        engine = MagicMock(spec=ConversationLearningEngine)
        engine.learn_from_interaction.return_value = {"records_created": 1}
        handler = LearnInteractionHandler(root=tmp_path, learning_engine=engine)
        handler.handle(
            LearnInteractionCommand(
                user_message="q",
                assistant_response="a",
                task_id="task_xyz",
            )
        )
        engine.learn_from_interaction.assert_called_once_with(
            user_message="q",
            assistant_response="a",
            task_id="task_xyz",
            route="",
            topic="",
        )

    def test_large_records_created(self, tmp_path: Path) -> None:
        """Handler correctly passes through large record counts."""
        engine = MagicMock(spec=ConversationLearningEngine)
        engine.learn_from_interaction.return_value = {"records_created": 100}
        handler = LearnInteractionHandler(root=tmp_path, learning_engine=engine)
        result = handler.handle(LearnInteractionCommand(user_message="batch"))
        assert result.records_created == 100


# ---------------------------------------------------------------------------
# CrossBranchQueryHandler
# ---------------------------------------------------------------------------


class TestCrossBranchQueryHandler:
    """Tests for CrossBranchQueryHandler."""

    def test_no_engine_returns_error(self, tmp_path: Path) -> None:
        handler = CrossBranchQueryHandler(root=tmp_path, engine=None, kg=MagicMock(spec=KnowledgeGraph), embed_service=MagicMock(spec=EmbeddingService))
        result = handler.handle(CrossBranchQueryCommand(query="test"))
        assert "requires engine" in result.message.lower()

    def test_no_kg_returns_error(self, tmp_path: Path) -> None:
        handler = CrossBranchQueryHandler(root=tmp_path, engine=MagicMock(spec=MemoryEngine), kg=None, embed_service=MagicMock(spec=EmbeddingService))
        result = handler.handle(CrossBranchQueryCommand(query="test"))
        assert "requires" in result.message.lower()

    def test_no_embed_returns_error(self, tmp_path: Path) -> None:
        handler = CrossBranchQueryHandler(root=tmp_path, engine=MagicMock(spec=MemoryEngine), kg=MagicMock(spec=KnowledgeGraph), embed_service=None)
        result = handler.handle(CrossBranchQueryCommand(query="test"))
        assert "requires" in result.message.lower()

    def test_all_none_returns_error(self, tmp_path: Path) -> None:
        handler = CrossBranchQueryHandler(root=tmp_path)
        result = handler.handle(CrossBranchQueryCommand(query="test"))
        assert "requires" in result.message.lower()
        assert result.direct_results == []
        assert result.cross_branch_connections == []
        assert result.branches_involved == []

    def test_import_error_returns_not_available(self, tmp_path: Path) -> None:
        handler = CrossBranchQueryHandler(
            root=tmp_path,
            engine=MagicMock(spec=MemoryEngine),
            kg=MagicMock(spec=KnowledgeGraph),
            embed_service=MagicMock(spec=EmbeddingService),
        )
        with patch.dict("sys.modules", {"jarvis_engine.learning.cross_branch": None}):
            result = handler.handle(CrossBranchQueryCommand(query="test"))
        assert "not available" in result.message.lower()

    def test_successful_cross_branch_query(self, tmp_path: Path) -> None:
        """Full happy path: cross_branch_query returns results."""
        mock_module = MagicMock()
        mock_module.cross_branch_query.return_value = {
            "direct_results": [{"id": "r1", "text": "result 1"}],
            "cross_branch_connections": [{"from": "b1", "to": "b2"}],
            "branches_involved": ["memory", "knowledge"],
        }

        engine = MagicMock(spec=MemoryEngine)
        kg = MagicMock(spec=KnowledgeGraph)
        embed = MagicMock(spec=EmbeddingService)

        with patch.dict("sys.modules", {"jarvis_engine.learning.cross_branch": mock_module}):
            handler = CrossBranchQueryHandler(
                root=tmp_path, engine=engine, kg=kg, embed_service=embed
            )
            result = handler.handle(CrossBranchQueryCommand(query="quantum computing", k=5))

        assert result.message == "ok"
        assert len(result.direct_results) == 1
        assert result.direct_results[0]["id"] == "r1"
        assert len(result.cross_branch_connections) == 1
        assert result.branches_involved == ["memory", "knowledge"]
        mock_module.cross_branch_query.assert_called_once_with(
            query="quantum computing",
            engine=engine,
            kg=kg,
            embed_service=embed,
            k=5,
        )

    def test_k_parameter_forwarded(self, tmp_path: Path) -> None:
        mock_module = MagicMock()
        mock_module.cross_branch_query.return_value = {
            "direct_results": [],
            "cross_branch_connections": [],
            "branches_involved": [],
        }

        with patch.dict("sys.modules", {"jarvis_engine.learning.cross_branch": mock_module}):
            handler = CrossBranchQueryHandler(
                root=tmp_path,
                engine=MagicMock(spec=MemoryEngine),
                kg=MagicMock(spec=KnowledgeGraph),
                embed_service=MagicMock(spec=EmbeddingService),
            )
            handler.handle(CrossBranchQueryCommand(query="test", k=20))

        call_kwargs = mock_module.cross_branch_query.call_args
        assert call_kwargs.kwargs.get("k") == 20 or call_kwargs[1].get("k") == 20

    def test_empty_results(self, tmp_path: Path) -> None:
        """Handler handles empty result dict gracefully."""
        mock_module = MagicMock()
        mock_module.cross_branch_query.return_value = {}

        with patch.dict("sys.modules", {"jarvis_engine.learning.cross_branch": mock_module}):
            handler = CrossBranchQueryHandler(
                root=tmp_path,
                engine=MagicMock(spec=MemoryEngine),
                kg=MagicMock(spec=KnowledgeGraph),
                embed_service=MagicMock(spec=EmbeddingService),
            )
            result = handler.handle(CrossBranchQueryCommand(query="nothing"))

        assert result.direct_results == []
        assert result.cross_branch_connections == []
        assert result.branches_involved == []
        assert result.message == "ok"

    def test_default_k_is_10(self, tmp_path: Path) -> None:
        """Default k param from the command dataclass is 10."""
        cmd = CrossBranchQueryCommand(query="test")
        assert cmd.k == 10


# ---------------------------------------------------------------------------
# FlagExpiredFactsHandler
# ---------------------------------------------------------------------------


class TestFlagExpiredFactsHandler:
    """Tests for FlagExpiredFactsHandler."""

    def test_no_kg_returns_not_available(self, tmp_path: Path) -> None:
        handler = FlagExpiredFactsHandler(root=tmp_path, kg=None)
        result = handler.handle(FlagExpiredFactsCommand())
        assert result.message == "Knowledge graph not available."
        assert result.expired_count == 0

    def test_import_error_returns_not_available(self, tmp_path: Path) -> None:
        handler = FlagExpiredFactsHandler(root=tmp_path, kg=MagicMock(spec=KnowledgeGraph))
        with patch.dict("sys.modules", {"jarvis_engine.learning.temporal": None}):
            result = handler.handle(FlagExpiredFactsCommand())
        assert "not available" in result.message.lower()

    def test_successful_flagging(self, tmp_path: Path) -> None:
        """Happy path: flag_expired_facts returns a count."""
        mock_module = MagicMock()
        mock_module.flag_expired_facts.return_value = 7

        with patch.dict("sys.modules", {"jarvis_engine.learning.temporal": mock_module}):
            kg = MagicMock(spec=KnowledgeGraph)
            handler = FlagExpiredFactsHandler(root=tmp_path, kg=kg)
            result = handler.handle(FlagExpiredFactsCommand())

        assert result.expired_count == 7
        assert "7 expired" in result.message
        mock_module.flag_expired_facts.assert_called_once_with(kg)

    def test_zero_expired(self, tmp_path: Path) -> None:
        mock_module = MagicMock()
        mock_module.flag_expired_facts.return_value = 0

        with patch.dict("sys.modules", {"jarvis_engine.learning.temporal": mock_module}):
            handler = FlagExpiredFactsHandler(root=tmp_path, kg=MagicMock(spec=KnowledgeGraph))
            result = handler.handle(FlagExpiredFactsCommand())

        assert result.expired_count == 0
        assert "0 expired" in result.message

    def test_large_expired_count(self, tmp_path: Path) -> None:
        mock_module = MagicMock()
        mock_module.flag_expired_facts.return_value = 500

        with patch.dict("sys.modules", {"jarvis_engine.learning.temporal": mock_module}):
            handler = FlagExpiredFactsHandler(root=tmp_path, kg=MagicMock(spec=KnowledgeGraph))
            result = handler.handle(FlagExpiredFactsCommand())

        assert result.expired_count == 500
        assert "500 expired" in result.message

    def test_kg_passed_to_function(self, tmp_path: Path) -> None:
        """Verifies the kg instance is passed correctly to flag_expired_facts."""
        mock_module = MagicMock()
        mock_module.flag_expired_facts.return_value = 1

        kg = MagicMock(spec=KnowledgeGraph, name="my_kg")

        with patch.dict("sys.modules", {"jarvis_engine.learning.temporal": mock_module}):
            handler = FlagExpiredFactsHandler(root=tmp_path, kg=kg)
            handler.handle(FlagExpiredFactsCommand())

        mock_module.flag_expired_facts.assert_called_once_with(kg)
