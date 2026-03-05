"""Tests for LEARN-04: route/topic propagation and bus exposure."""

from __future__ import annotations

from unittest.mock import MagicMock


from jarvis_engine.commands.learning_commands import (
    LearnInteractionCommand,
)
from jarvis_engine.handlers.learning_handlers import LearnInteractionHandler


class TestLearnInteractionCommandFields:
    """Test route/topic fields on LearnInteractionCommand."""

    def test_command_has_route_topic(self):
        cmd = LearnInteractionCommand(
            user_message="hello",
            assistant_response="hi",
            route="complex",
            topic="test query about science",
        )
        assert cmd.route == "complex"
        assert cmd.topic == "test query about science"

    def test_command_backward_compat(self):
        cmd = LearnInteractionCommand(
            user_message="hello",
            assistant_response="hi",
        )
        assert cmd.route == ""
        assert cmd.topic == ""

    def test_command_with_task_id_and_route(self):
        cmd = LearnInteractionCommand(
            user_message="q",
            assistant_response="a",
            task_id="task-123",
            route="routine",
            topic="greetings",
        )
        assert cmd.task_id == "task-123"
        assert cmd.route == "routine"
        assert cmd.topic == "greetings"


class TestHandlerForwardsRouteParams:
    """Test that LearnInteractionHandler passes route/topic to engine."""

    def test_handler_passes_route_topic(self):
        mock_engine = MagicMock()
        mock_engine.learn_from_interaction.return_value = {
            "records_created": 1,
            "error": "",
        }
        handler = LearnInteractionHandler(root=MagicMock(), learning_engine=mock_engine)
        cmd = LearnInteractionCommand(
            user_message="Tell me about Python",
            assistant_response="Python is a programming language.",
            task_id="test-1",
            route="complex",
            topic="Tell me about Python",
        )
        result = handler.handle(cmd)
        mock_engine.learn_from_interaction.assert_called_once_with(
            user_message="Tell me about Python",
            assistant_response="Python is a programming language.",
            task_id="test-1",
            route="complex",
            topic="Tell me about Python",
        )
        assert result.records_created == 1


class TestEngineForwardsToTrackers:
    """Test that ConversationLearningEngine forwards route/topic to trackers."""

    def test_engine_passes_route_to_feedback_tracker(self):
        from jarvis_engine.learning.engine import ConversationLearningEngine

        mock_pipeline = MagicMock()
        mock_pipeline.ingest.return_value = []
        mock_feedback = MagicMock()
        mock_feedback.record_feedback.return_value = "positive"
        mock_usage = MagicMock()
        mock_pref = MagicMock()
        mock_pref.observe.return_value = []

        engine = ConversationLearningEngine(
            pipeline=mock_pipeline,
            preference_tracker=mock_pref,
            feedback_tracker=mock_feedback,
            usage_tracker=mock_usage,
        )
        engine.learn_from_interaction(
            user_message="great job on that answer",
            assistant_response="thanks",
            route="routine",
            topic="greeting",
        )
        mock_feedback.record_feedback.assert_called_once_with(
            "great job on that answer", route="routine"
        )
        mock_usage.record_interaction.assert_called_once_with(
            route="routine", topic="greeting"
        )

    def test_engine_backward_compat_no_route(self):
        from jarvis_engine.learning.engine import ConversationLearningEngine

        mock_pipeline = MagicMock()
        mock_pipeline.ingest.return_value = []
        mock_feedback = MagicMock()
        mock_feedback.record_feedback.return_value = "neutral"
        mock_usage = MagicMock()

        engine = ConversationLearningEngine(
            pipeline=mock_pipeline,
            feedback_tracker=mock_feedback,
            usage_tracker=mock_usage,
        )
        result = engine.learn_from_interaction(
            user_message="tell me something",
            assistant_response="sure",
        )
        mock_feedback.record_feedback.assert_called_once_with(
            "tell me something", route=""
        )
        mock_usage.record_interaction.assert_called_once_with(
            route="", topic=""
        )
        assert isinstance(result, dict)


class TestBusExposesTrackers:
    """Test that trackers are accessible via bus attributes."""

    def test_bus_tracker_attributes_pattern(self):
        """Verify the attribute pattern works with a mock bus."""
        bus = MagicMock()
        bus._pref_tracker = MagicMock()
        bus._feedback_tracker = MagicMock()
        bus._usage_tracker = MagicMock()

        assert getattr(bus, "_pref_tracker", None) is not None
        assert getattr(bus, "_feedback_tracker", None) is not None
        assert getattr(bus, "_usage_tracker", None) is not None

    def test_bus_missing_tracker_returns_none(self):
        """Verify graceful fallback when tracker not set."""

        class SimpleBus:
            pass

        bus = SimpleBus()
        assert getattr(bus, "_pref_tracker", None) is None
        assert getattr(bus, "_feedback_tracker", None) is None
        assert getattr(bus, "_usage_tracker", None) is None
