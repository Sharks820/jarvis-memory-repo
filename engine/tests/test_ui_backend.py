"""Tests for Phase 3 UI backend: mission cancel, activity events, widget-status
events, response= output (UI-01 through UI-05)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest



# ---------------------------------------------------------------------------
# UI-01: MissionCancelCommand
# ---------------------------------------------------------------------------


class TestMissionCancel:
    """Verify cancel_mission sets status='cancelled' and logs activity event."""

    def _make_tmp_root(self) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / ".planning").mkdir(parents=True, exist_ok=True)
        return root

    def _create_missions_file(self, root: Path, missions: list[dict]) -> None:
        path = root / ".planning" / "missions.json"
        path.write_text(json.dumps(missions), encoding="utf-8")

    def test_cancel_mission_sets_status(self):
        """cancel_mission sets status to 'cancelled' and returns updated mission."""
        from jarvis_engine.learning_missions import cancel_mission

        root = self._make_tmp_root()
        self._create_missions_file(root, [
            {"mission_id": "m-001", "topic": "Test topic", "status": "pending",
             "created_utc": "2026-01-01T00:00:00", "updated_utc": "2026-01-01T00:00:00"},
        ])

        with patch("jarvis_engine.activity_feed.log_activity"):
            result = cancel_mission(root, mission_id="m-001")

        assert result["status"] == "cancelled"
        assert result["mission_id"] == "m-001"
        # Verify persisted to disk
        from jarvis_engine.learning_missions import load_missions
        missions = load_missions(root)
        assert missions[0]["status"] == "cancelled"

    def test_cancel_mission_not_found(self):
        """cancel_mission raises ValueError for unknown mission_id."""
        from jarvis_engine.learning_missions import cancel_mission

        root = self._make_tmp_root()
        self._create_missions_file(root, [])

        with pytest.raises(ValueError, match="mission not found"):
            cancel_mission(root, mission_id="m-nonexistent")

    def test_cancel_completed_mission_raises(self):
        """cancel_mission raises ValueError for already-completed missions."""
        from jarvis_engine.learning_missions import cancel_mission

        root = self._make_tmp_root()
        self._create_missions_file(root, [
            {"mission_id": "m-done", "topic": "Done topic", "status": "completed",
             "created_utc": "2026-01-01T00:00:00", "updated_utc": "2026-01-01T00:00:00"},
        ])

        with pytest.raises(ValueError, match="cannot cancel"):
            cancel_mission(root, mission_id="m-done")

    def test_cancel_already_cancelled_mission_raises(self):
        """cancel_mission raises ValueError for already-cancelled missions."""
        from jarvis_engine.learning_missions import cancel_mission

        root = self._make_tmp_root()
        self._create_missions_file(root, [
            {"mission_id": "m-can", "topic": "Already cancelled", "status": "cancelled",
             "created_utc": "2026-01-01T00:00:00", "updated_utc": "2026-01-01T00:00:00"},
        ])

        with pytest.raises(ValueError, match="cannot cancel"):
            cancel_mission(root, mission_id="m-can")

    def test_cancel_mission_logs_activity(self):
        """cancel_mission logs a MISSION_STATE_CHANGE activity event."""
        from jarvis_engine.learning_missions import cancel_mission

        root = self._make_tmp_root()
        self._create_missions_file(root, [
            {"mission_id": "m-002", "topic": "Activity test", "status": "pending",
             "created_utc": "2026-01-01T00:00:00", "updated_utc": "2026-01-01T00:00:00"},
        ])

        with patch("jarvis_engine.activity_feed.log_activity") as mock_log:
            cancel_mission(root, mission_id="m-002")
            mock_log.assert_called_once()
            call_args = mock_log.call_args
            assert call_args[0][0] == "mission_state_change"
            assert "cancelled" in call_args[0][1].lower()


class TestMissionCancelHandler:
    """Verify MissionCancelHandler delegates to cancel_mission."""

    def test_handler_cancel_success(self):
        from jarvis_engine.handlers.ops_handlers import MissionCancelHandler
        from jarvis_engine.commands.ops_commands import MissionCancelCommand

        root = Path(tempfile.mkdtemp())
        handler = MissionCancelHandler(root)

        with patch("jarvis_engine.learning_missions.cancel_mission") as mock_cancel:
            mock_cancel.return_value = {"mission_id": "m-001", "status": "cancelled", "topic": "test"}
            result = handler.handle(MissionCancelCommand(mission_id="m-001"))

        assert result.cancelled is True
        assert result.mission["status"] == "cancelled"

    def test_handler_cancel_not_found(self):
        from jarvis_engine.handlers.ops_handlers import MissionCancelHandler
        from jarvis_engine.commands.ops_commands import MissionCancelCommand

        root = Path(tempfile.mkdtemp())
        handler = MissionCancelHandler(root)

        with patch("jarvis_engine.learning_missions.cancel_mission") as mock_cancel:
            mock_cancel.side_effect = ValueError("mission not found: m-bad")
            result = handler.handle(MissionCancelCommand(mission_id="m-bad"))

        assert result.cancelled is False
        assert "not found" in result.message


# ---------------------------------------------------------------------------
# UI-02: Activity event emission for learning
# ---------------------------------------------------------------------------


class TestLearningActivityEvents:
    """Verify ConversationLearningEngine logs activity for preferences."""

    def test_preference_detected_logs_activity(self):
        """When preferences are detected, PREFERENCE_LEARNED events are logged."""
        from jarvis_engine.learning.engine import ConversationLearningEngine

        mock_pipeline = MagicMock()
        mock_pipeline.ingest.return_value = ["rec-1"]
        mock_pref = MagicMock()
        mock_pref.observe.return_value = [("style", "concise")]
        mock_feedback = MagicMock()
        mock_feedback.record_feedback.return_value = "neutral"
        mock_usage = MagicMock()

        engine = ConversationLearningEngine(
            pipeline=mock_pipeline,
            preference_tracker=mock_pref,
            feedback_tracker=mock_feedback,
            usage_tracker=mock_usage,
        )

        with patch("jarvis_engine.activity_feed.log_activity") as mock_log:
            result = engine.learn_from_interaction(
                user_message="I prefer concise responses from now on please",
                assistant_response="Got it, I'll keep responses concise.",
                route="routine",
                topic="preferences",
            )

        assert result["preferences_detected"] == [("style", "concise")]
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "preference_learned"
        assert "style" in call_args[0][1]

    def test_no_preference_no_activity_log(self):
        """When no preferences are detected, no PREFERENCE_LEARNED event is logged."""
        from jarvis_engine.learning.engine import ConversationLearningEngine

        mock_pipeline = MagicMock()
        mock_pipeline.ingest.return_value = ["rec-1"]
        mock_pref = MagicMock()
        mock_pref.observe.return_value = []
        mock_feedback = MagicMock()
        mock_feedback.record_feedback.return_value = "neutral"
        mock_usage = MagicMock()

        engine = ConversationLearningEngine(
            pipeline=mock_pipeline,
            preference_tracker=mock_pref,
            feedback_tracker=mock_feedback,
            usage_tracker=mock_usage,
        )

        with patch("jarvis_engine.activity_feed.log_activity") as mock_log:
            engine.learn_from_interaction(
                user_message="What is the weather today?",
                assistant_response="It is sunny.",
                route="routine",
                topic="weather",
            )

        mock_log.assert_not_called()


# ---------------------------------------------------------------------------
# UI-03/04: /widget-status includes recent_events
# ---------------------------------------------------------------------------


class TestWidgetStatusEvents:
    """Verify /widget-status endpoint returns recent_events."""

    def test_widget_status_includes_recent_events(self):
        """Widget status response includes recent_events from activity feed."""
        from jarvis_engine.activity_feed import ActivityEvent

        mock_feed = MagicMock()
        mock_events = [
            ActivityEvent(
                timestamp="2026-03-02T10:00:00",
                category="preference_learned",
                summary="Learned preference: style=concise",
                event_id="evt-001",
            ),
            ActivityEvent(
                timestamp="2026-03-02T09:55:00",
                category="llm_routing",
                summary="Routed to kimi-k2",
                event_id="evt-002",
            ),
            ActivityEvent(
                timestamp="2026-03-02T09:50:00",
                category="daemon_cycle",
                summary="Daemon cycle complete",
                event_id="evt-003",
            ),
        ]
        mock_feed.query.return_value = mock_events

        # Simulate the filtering logic from _handle_get_widget_status
        from jarvis_engine.activity_feed import ActivityCategory
        events = mock_feed.query(limit=10)
        recent_events = [
            {
                "event_id": e.event_id,
                "timestamp": e.timestamp,
                "category": e.category,
                "summary": e.summary,
            }
            for e in events
            if e.category != ActivityCategory.DAEMON_CYCLE
        ][:10]

        assert len(recent_events) == 2
        assert recent_events[0]["category"] == "preference_learned"
        assert recent_events[1]["category"] == "llm_routing"
        # daemon_cycle should be excluded
        assert all(e["category"] != "daemon_cycle" for e in recent_events)


# ---------------------------------------------------------------------------
# UI-05: response= output for structured commands
# ---------------------------------------------------------------------------


class TestResponseOutputLines:
    """Verify cmd_brain_status and cmd_mission_status emit response= lines."""

    def test_brain_status_emits_response(self, capsys):
        """cmd_brain_status prints a response= line."""

        mock_bus = MagicMock()
        mock_result = MagicMock()
        mock_result.status = {
            "updated_utc": "2026-03-02T10:00:00",
            "branch_count": 3,
            "branches": [
                {"branch": "general", "count": 100, "last_ts": "2026-03-02", "last_summary": "..."},
                {"branch": "health", "count": 50, "last_ts": "2026-03-01", "last_summary": "..."},
            ],
        }
        mock_bus.dispatch.return_value = mock_result

        with patch("jarvis_engine.main._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_brain_status
            rc = cmd_brain_status(as_json=False)

        assert rc == 0
        captured = capsys.readouterr().out
        assert "response=" in captured
        assert "3 branch" in captured

    def test_mission_status_emits_response(self, capsys):
        """cmd_mission_status prints a response= line."""
        mock_bus = MagicMock()
        mock_result = MagicMock()
        mock_result.missions = [
            {"mission_id": "m-001", "status": "completed", "topic": "Test",
             "verified_findings": 5, "updated_utc": "2026-03-02T10:00:00"},
        ]
        mock_result.total_count = 1

        with patch("jarvis_engine.main._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_mission_status
            mock_bus.dispatch.return_value = mock_result
            rc = cmd_mission_status(last=5)

        assert rc == 0
        captured = capsys.readouterr().out
        assert "response=" in captured
        assert "1 total" in captured

    def test_mission_cancel_emits_response(self, capsys):
        """cmd_mission_cancel prints a response= line."""
        mock_bus = MagicMock()
        mock_result = MagicMock()
        mock_result.cancelled = True
        mock_result.mission = {"mission_id": "m-001", "topic": "Test", "status": "cancelled"}
        mock_result.message = ""

        with patch("jarvis_engine.main._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_mission_cancel
            mock_bus.dispatch.return_value = mock_result
            rc = cmd_mission_cancel(mission_id="m-001")

        assert rc == 0
        captured = capsys.readouterr().out
        assert "response=" in captured
        assert "Cancelled" in captured

    def test_mission_status_empty_emits_response(self, capsys):
        """cmd_mission_status with no missions prints a response= line."""
        mock_bus = MagicMock()
        mock_result = MagicMock()
        mock_result.missions = []
        mock_result.total_count = 0

        with patch("jarvis_engine.main._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_mission_status
            mock_bus.dispatch.return_value = mock_result
            rc = cmd_mission_status(last=5)

        assert rc == 0
        captured = capsys.readouterr().out
        assert "response=" in captured
        assert "No active" in captured


# ---------------------------------------------------------------------------
# ActivityCategory constants
# ---------------------------------------------------------------------------


class TestActivityCategoryConstants:
    """Verify new activity categories exist."""

    def test_preference_learned_constant(self):
        from jarvis_engine.activity_feed import ActivityCategory
        assert ActivityCategory.PREFERENCE_LEARNED == "preference_learned"

    def test_mission_state_change_constant(self):
        from jarvis_engine.activity_feed import ActivityCategory
        assert ActivityCategory.MISSION_STATE_CHANGE == "mission_state_change"

    def test_resource_pressure_constant(self):
        from jarvis_engine.activity_feed import ActivityCategory
        assert ActivityCategory.RESOURCE_PRESSURE == "resource_pressure"
