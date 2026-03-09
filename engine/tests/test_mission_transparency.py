"""Tests for Task D: Mission Transparency — step-driven progress, lifecycle, dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis_engine._shared import now_iso
from jarvis_engine.learning_missions import (
    _compute_step_progress,
    _init_mission_steps,
    _update_step,
    cancel_mission,
    create_learning_mission,
    get_active_missions,
    get_mission_steps,
    get_now_working_on,
    load_missions,
    mission_dashboard_metrics,
    pause_mission,
    restart_mission,
    resume_mission,
)


@pytest.fixture()
def mission_root(tmp_path: Path) -> Path:
    """Create a temporary mission root with .planning directory."""
    planning = tmp_path / ".planning"
    planning.mkdir()
    return tmp_path


def _create_test_mission(root: Path, topic: str = "test topic", **kwargs: Any) -> dict[str, Any]:
    """Helper to create a mission for testing."""
    return create_learning_mission(
        root, topic=topic, objective=kwargs.get("objective", "test objective"),
        sources=kwargs.get("sources", ["google"]),
        origin=kwargs.get("origin", "test"),
    )


# ---------------------------------------------------------------------------
# Step model tests
# ---------------------------------------------------------------------------

class TestMissionSteps:
    def test_init_mission_steps_returns_six_steps(self) -> None:
        steps = _init_mission_steps()
        assert len(steps) == 6
        names = [s["name"] for s in steps]
        assert names == ["init", "search_web", "fetch_pages", "extract_candidates", "verify_findings", "finalize"]

    def test_init_mission_steps_all_pending(self) -> None:
        steps = _init_mission_steps()
        for step in steps:
            assert step["status"] == "pending"
            assert step["elapsed_ms"] == 0
            assert step["artifacts_produced"] == 0

    def test_init_mission_steps_returns_independent_copies(self) -> None:
        steps1 = _init_mission_steps()
        steps2 = _init_mission_steps()
        steps1[0]["status"] = "completed"
        assert steps2[0]["status"] == "pending"

    def test_compute_step_progress_all_pending(self) -> None:
        steps = _init_mission_steps()
        assert _compute_step_progress(steps) == 0

    def test_compute_step_progress_all_completed(self) -> None:
        steps = _init_mission_steps()
        for s in steps:
            s["status"] = "completed"
        assert _compute_step_progress(steps) == 100

    def test_compute_step_progress_partial(self) -> None:
        steps = _init_mission_steps()
        # Complete init (weight=0.5) out of total ~10.0
        steps[0]["status"] = "completed"
        progress = _compute_step_progress(steps)
        assert 0 < progress < 100

    def test_compute_step_progress_skipped_counts(self) -> None:
        steps = _init_mission_steps()
        steps[0]["status"] = "skipped"
        progress = _compute_step_progress(steps)
        assert progress > 0

    def test_compute_step_progress_empty_list(self) -> None:
        assert _compute_step_progress([]) == 0

    def test_compute_step_progress_zero_weight(self) -> None:
        steps = [{"name": "a", "weight": 0.0, "status": "completed"}]
        assert _compute_step_progress(steps) == 0


# ---------------------------------------------------------------------------
# Step update tests
# ---------------------------------------------------------------------------

class TestUpdateStep:
    def test_update_step_marks_running(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        # Manually add steps to the mission
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["steps"] = _init_mission_steps()
                mi["status"] = "running"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        _update_step(mission_root, m["mission_id"], "init", status="running")

        steps = get_mission_steps(mission_root, m["mission_id"])
        init_step = next(s for s in steps if s["name"] == "init")
        assert init_step["status"] == "running"

    def test_update_step_records_elapsed_and_artifacts(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["steps"] = _init_mission_steps()
                mi["status"] = "running"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        _update_step(mission_root, m["mission_id"], "search_web",
                     status="completed", elapsed_ms=1500, artifacts_produced=8)

        steps = get_mission_steps(mission_root, m["mission_id"])
        web_step = next(s for s in steps if s["name"] == "search_web")
        assert web_step["elapsed_ms"] == 1500
        assert web_step["artifacts_produced"] == 8

    def test_update_step_recomputes_progress(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["steps"] = _init_mission_steps()
                mi["status"] = "running"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        _update_step(mission_root, m["mission_id"], "init", status="completed")

        missions_after = load_missions(mission_root)
        target = next(mi for mi in missions_after if mi["mission_id"] == m["mission_id"])
        assert target["progress_pct"] > 0

    def test_update_step_skips_cancelled_mission(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        cancel_mission(mission_root, mission_id=m["mission_id"])

        # Should not raise, just silently skip
        _update_step(mission_root, m["mission_id"], "init", status="running")

    def test_update_step_unknown_mission(self, mission_root: Path) -> None:
        # Should not raise for unknown mission
        _update_step(mission_root, "nonexistent", "init", status="running")


# ---------------------------------------------------------------------------
# get_mission_steps tests
# ---------------------------------------------------------------------------

class TestGetMissionSteps:
    def test_returns_steps_for_mission(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["steps"] = _init_mission_steps()
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        steps = get_mission_steps(mission_root, m["mission_id"])
        assert len(steps) == 6

    def test_returns_empty_for_unknown_mission(self, mission_root: Path) -> None:
        assert get_mission_steps(mission_root, "nonexistent") == []

    def test_returns_empty_when_no_steps(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        steps = get_mission_steps(mission_root, m["mission_id"])
        assert steps == []


# ---------------------------------------------------------------------------
# get_active_missions tests
# ---------------------------------------------------------------------------

class TestGetActiveMissions:
    def test_returns_pending_missions(self, mission_root: Path) -> None:
        _create_test_mission(mission_root, topic="active 1")
        active = get_active_missions(mission_root)
        assert len(active) == 1

    def test_excludes_completed_and_cancelled(self, mission_root: Path) -> None:
        m1 = _create_test_mission(mission_root, topic="will cancel")
        _create_test_mission(mission_root, topic="stays pending")
        cancel_mission(mission_root, mission_id=m1["mission_id"])
        active = get_active_missions(mission_root)
        assert len(active) == 1
        assert active[0]["topic"] == "stays pending"

    def test_returns_empty_when_no_missions(self, mission_root: Path) -> None:
        assert get_active_missions(mission_root) == []


# ---------------------------------------------------------------------------
# get_now_working_on tests
# ---------------------------------------------------------------------------

class TestGetNowWorkingOn:
    def test_returns_none_when_no_running(self, mission_root: Path) -> None:
        _create_test_mission(mission_root)  # pending, not running
        assert get_now_working_on(mission_root) is None

    def test_returns_running_mission(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "running"
                mi["steps"] = _init_mission_steps()
                mi["steps"][0]["status"] = "completed"
                mi["steps"][1]["status"] = "running"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        result = get_now_working_on(mission_root)
        assert result is not None
        assert result["mission_topic"] == "test topic"
        assert result["current_step"] == "Searching the web for sources"

    def test_includes_artifact_count(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "running"
                mi["steps"] = _init_mission_steps()
                mi["steps"][0]["status"] = "completed"
                mi["steps"][0]["artifacts_produced"] = 3
                mi["steps"][1]["status"] = "running"
                mi["steps"][1]["artifacts_produced"] = 5
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        result = get_now_working_on(mission_root)
        assert result is not None
        assert result["artifacts_so_far"] == 8


# ---------------------------------------------------------------------------
# Lifecycle: pause / resume / restart
# ---------------------------------------------------------------------------

class TestMissionPause:
    def test_pause_running_mission(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "running"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        result = pause_mission(mission_root, mission_id=m["mission_id"])
        assert result["status"] == "paused"

    def test_pause_non_running_raises(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        with pytest.raises(ValueError, match="can only pause a running mission"):
            pause_mission(mission_root, mission_id=m["mission_id"])

    def test_pause_unknown_mission_raises(self, mission_root: Path) -> None:
        with pytest.raises(ValueError, match="mission not found"):
            pause_mission(mission_root, mission_id="nonexistent")


class TestMissionResume:
    def test_resume_paused_mission(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "paused"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        result = resume_mission(mission_root, mission_id=m["mission_id"])
        assert result["status"] == "pending"
        assert "Resumed" in result.get("status_detail", "")

    def test_resume_non_paused_raises(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        with pytest.raises(ValueError, match="can only resume a paused mission"):
            resume_mission(mission_root, mission_id=m["mission_id"])


class TestMissionRestart:
    def test_restart_failed_mission(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "failed"
                mi["progress_pct"] = 45
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        result = restart_mission(mission_root, mission_id=m["mission_id"])
        assert result["status"] == "pending"
        assert result["progress_pct"] == 0
        assert result.get("steps") is not None
        assert len(result.get("steps", [])) == 6

    def test_restart_cancelled_mission(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        cancel_mission(mission_root, mission_id=m["mission_id"])
        result = restart_mission(mission_root, mission_id=m["mission_id"])
        assert result["status"] == "pending"

    def test_restart_exhausted_mission(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "exhausted"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        result = restart_mission(mission_root, mission_id=m["mission_id"])
        assert result["status"] == "pending"

    def test_restart_running_raises(self, mission_root: Path) -> None:
        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "running"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        with pytest.raises(ValueError, match="can only restart"):
            restart_mission(mission_root, mission_id=m["mission_id"])


# ---------------------------------------------------------------------------
# Dashboard metrics
# ---------------------------------------------------------------------------

class TestMissionDashboardMetrics:
    def test_empty_missions(self, mission_root: Path) -> None:
        metrics = mission_dashboard_metrics(mission_root)
        assert metrics["total_missions"] == 0
        assert metrics["active_count"] == 0
        assert metrics["missions_completed_7d"] == 0

    def test_counts_completed_and_failed(self, mission_root: Path) -> None:
        m1 = _create_test_mission(mission_root, topic="topic 1")
        m2 = _create_test_mission(mission_root, topic="topic 2")
        m3 = _create_test_mission(mission_root, topic="topic 3")

        missions = load_missions(mission_root)
        for mi in missions:
            mid = mi["mission_id"]
            if mid == m1["mission_id"]:
                mi["status"] = "completed"
                mi["updated_utc"] = now_iso()
            elif mid == m2["mission_id"]:
                mi["status"] = "failed"
                mi["updated_utc"] = now_iso()
            elif mid == m3["mission_id"]:
                mi["status"] = "running"
                mi["updated_utc"] = now_iso()
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        metrics = mission_dashboard_metrics(mission_root)
        assert metrics["total_missions"] == 3
        assert metrics["missions_completed_7d"] == 1
        assert metrics["missions_failed_7d"] == 1
        assert metrics["active_count"] == 1
        assert metrics["mission_success_rate"] > 0

    def test_top_topics_populated(self, mission_root: Path) -> None:
        for i in range(3):
            m = _create_test_mission(mission_root, topic=f"topic {i}")
            missions = load_missions(mission_root)
            for mi in missions:
                if mi["mission_id"] == m["mission_id"]:
                    mi["status"] = "completed"
                    mi["updated_utc"] = now_iso()
            from jarvis_engine.learning_missions import _save_missions
            _save_missions(mission_root, missions)

        metrics = mission_dashboard_metrics(mission_root)
        assert len(metrics["top_topics_learned"]) == 3


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------

class TestMissionHandlers:
    def test_pause_handler(self, mission_root: Path) -> None:
        from jarvis_engine.commands.ops_commands import MissionPauseCommand
        from jarvis_engine.handlers.ops_handlers import MissionPauseHandler

        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "running"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        handler = MissionPauseHandler(mission_root)
        result = handler.handle(MissionPauseCommand(mission_id=m["mission_id"]))
        assert result.return_code == 0
        assert result.mission.get("status") == "paused"

    def test_pause_handler_invalid(self, mission_root: Path) -> None:
        from jarvis_engine.commands.ops_commands import MissionPauseCommand
        from jarvis_engine.handlers.ops_handlers import MissionPauseHandler

        handler = MissionPauseHandler(mission_root)
        result = handler.handle(MissionPauseCommand(mission_id="nonexistent"))
        assert result.return_code == 2

    def test_resume_handler(self, mission_root: Path) -> None:
        from jarvis_engine.commands.ops_commands import MissionResumeCommand
        from jarvis_engine.handlers.ops_handlers import MissionResumeHandler

        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "paused"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        handler = MissionResumeHandler(mission_root)
        result = handler.handle(MissionResumeCommand(mission_id=m["mission_id"]))
        assert result.return_code == 0

    def test_restart_handler(self, mission_root: Path) -> None:
        from jarvis_engine.commands.ops_commands import MissionRestartCommand
        from jarvis_engine.handlers.ops_handlers import MissionRestartHandler

        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["status"] = "failed"
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        handler = MissionRestartHandler(mission_root)
        result = handler.handle(MissionRestartCommand(mission_id=m["mission_id"]))
        assert result.return_code == 0

    def test_steps_handler(self, mission_root: Path) -> None:
        from jarvis_engine.commands.ops_commands import MissionStepsCommand
        from jarvis_engine.handlers.ops_handlers import MissionStepsHandler

        m = _create_test_mission(mission_root)
        missions = load_missions(mission_root)
        for mi in missions:
            if mi["mission_id"] == m["mission_id"]:
                mi["steps"] = _init_mission_steps()
        from jarvis_engine.learning_missions import _save_missions
        _save_missions(mission_root, missions)

        handler = MissionStepsHandler(mission_root)
        result = handler.handle(MissionStepsCommand(mission_id=m["mission_id"]))
        assert len(result.steps) == 6
        assert result.mission_id == m["mission_id"]

    def test_active_handler(self, mission_root: Path) -> None:
        from jarvis_engine.commands.ops_commands import MissionActiveCommand
        from jarvis_engine.handlers.ops_handlers import MissionActiveHandler

        _create_test_mission(mission_root, topic="topic a")
        _create_test_mission(mission_root, topic="topic b")

        handler = MissionActiveHandler(mission_root)
        result = handler.handle(MissionActiveCommand())
        assert result.count == 2
        assert len(result.missions) == 2


# ---------------------------------------------------------------------------
# Intelligence dashboard integration
# ---------------------------------------------------------------------------

class TestDashboardMissionMetrics:
    def test_dashboard_includes_missions_key(self, mission_root: Path) -> None:
        from jarvis_engine.intelligence_dashboard import _safe_mission_metrics

        metrics = _safe_mission_metrics(mission_root)
        assert isinstance(metrics, dict)
        assert "total_missions" in metrics

    def test_dashboard_missions_graceful_on_error(self, tmp_path: Path) -> None:
        from jarvis_engine.intelligence_dashboard import _safe_mission_metrics

        # Non-existent root should not raise, returns empty
        metrics = _safe_mission_metrics(tmp_path / "nonexistent")
        assert isinstance(metrics, dict)
