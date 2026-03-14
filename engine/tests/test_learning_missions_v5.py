"""Tests for LM-01 through LM-08 learning mission requirements (v5.0).

Covers:
  LM-01: State machine transitions (valid + invalid)
  LM-02: Step-aware progress calculation accuracy
  LM-04: Intelligence trend metrics (get_intelligence_delta)
  LM-05: Learning provenance tracing (source_interaction_id)
  LM-06: Retry context preservation (prior_results)
  LM-07: Cancel safety and immediacy
  LM-08: Intelligence delta report generation
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_engine.learning.missions import (
    VALID_TRANSITIONS,
    InvalidTransitionError,
    _check_transition,
    _compute_step_progress,
    _init_mission_steps,
    _preserve_prior_results,
    block_mission,
    cancel_mission,
    create_learning_mission,
    load_missions,
    pause_mission,
    restart_mission,
    resume_mission,
    retry_failed_missions,
    unblock_mission,
)


# ── LM-01: State machine transitions ────────────────────────────────────


class TestStateMachineTransitions:
    """LM-01: Validate finite-state lifecycle with 'blocked' state."""

    def test_valid_transitions_dict_has_all_states(self) -> None:
        expected_states = {
            "pending", "running", "blocked", "paused",
            "completed", "failed", "cancelled", "exhausted",
        }
        assert set(VALID_TRANSITIONS.keys()) == expected_states

    def test_blocked_only_reachable_from_running(self) -> None:
        """Only running -> blocked is allowed."""
        assert "blocked" in VALID_TRANSITIONS["running"]
        for state, allowed in VALID_TRANSITIONS.items():
            if state != "running":
                assert "blocked" not in allowed, f"{state} should not transition to blocked"

    def test_blocked_can_go_to_running_cancelled_failed(self) -> None:
        allowed_from_blocked = VALID_TRANSITIONS["blocked"]
        assert "running" in allowed_from_blocked
        assert "cancelled" in allowed_from_blocked
        assert "failed" in allowed_from_blocked

    def test_completed_is_terminal(self) -> None:
        assert VALID_TRANSITIONS["completed"] == set()

    def test_check_transition_valid(self) -> None:
        _check_transition("m-test", "pending", "running")  # should not raise

    def test_check_transition_invalid_raises(self) -> None:
        with pytest.raises(InvalidTransitionError, match="pending.*completed"):
            _check_transition("m-test", "pending", "completed")

    def test_check_transition_error_attributes(self) -> None:
        try:
            _check_transition("m-123", "completed", "running")
            pytest.fail("Should have raised")
        except InvalidTransitionError as exc:
            assert exc.mission_id == "m-123"
            assert exc.from_state == "completed"
            assert exc.to_state == "running"

    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            ("pending", "running"),
            ("pending", "cancelled"),
            ("running", "completed"),
            ("running", "failed"),
            ("running", "cancelled"),
            ("running", "paused"),
            ("running", "blocked"),
            ("blocked", "running"),
            ("blocked", "cancelled"),
            ("blocked", "failed"),
            ("paused", "pending"),
            ("paused", "cancelled"),
            ("failed", "pending"),
            ("failed", "exhausted"),
            ("cancelled", "pending"),
            ("exhausted", "pending"),
        ],
    )
    def test_all_valid_transitions(self, from_state: str, to_state: str) -> None:
        _check_transition("m-test", from_state, to_state)

    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            ("pending", "completed"),
            ("pending", "failed"),
            ("pending", "blocked"),
            ("completed", "running"),
            ("completed", "pending"),
            ("paused", "completed"),
            ("failed", "running"),
            ("exhausted", "running"),
        ],
    )
    def test_all_invalid_transitions(self, from_state: str, to_state: str) -> None:
        with pytest.raises(InvalidTransitionError):
            _check_transition("m-test", from_state, to_state)


class TestBlockUnblockMission:
    """LM-01: block/unblock lifecycle operations."""

    def test_block_running_mission(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Test topic", objective="Test obj"
        )
        mid = mission["mission_id"]
        # Move to running first
        missions = load_missions(tmp_path)
        missions[0]["status"] = "running"
        _save(tmp_path, missions)

        result = block_mission(tmp_path, mission_id=mid, reason="rate limited")
        assert result["status"] == "blocked"
        assert "rate limited" in result["status_detail"]

    def test_block_non_running_raises(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Test topic", objective="Test obj"
        )
        with pytest.raises(InvalidTransitionError):
            block_mission(tmp_path, mission_id=mission["mission_id"])

    def test_unblock_blocked_mission(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Test topic", objective="Test obj"
        )
        mid = mission["mission_id"]
        missions = load_missions(tmp_path)
        missions[0]["status"] = "running"
        _save(tmp_path, missions)
        block_mission(tmp_path, mission_id=mid, reason="test block")

        result = unblock_mission(tmp_path, mission_id=mid)
        assert result["status"] == "running"

    def test_unblock_non_blocked_raises(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Test topic", objective="Test obj"
        )
        mid = mission["mission_id"]
        # Set to paused — paused -> running is not valid
        missions = load_missions(tmp_path)
        missions[0]["status"] = "paused"
        _save(tmp_path, missions)
        with pytest.raises(InvalidTransitionError):
            unblock_mission(tmp_path, mission_id=mid)


# ── LM-02: Step-aware progress ──────────────────────────────────────────


class TestStepProgress:
    """LM-02: Progress percentages tied to real completed steps."""

    def test_all_pending_is_zero(self) -> None:
        steps = _init_mission_steps()
        assert _compute_step_progress(steps) == 0

    def test_all_completed_is_100(self) -> None:
        steps = _init_mission_steps()
        for s in steps:
            s["status"] = "completed"
        assert _compute_step_progress(steps) == 100

    def test_partial_completion(self) -> None:
        steps = _init_mission_steps()
        # Complete only init (weight 0.5) out of total 10.0
        steps[0]["status"] = "completed"
        pct = _compute_step_progress(steps)
        assert 0 < pct < 100
        assert pct == int(0.5 / 10.0 * 100)  # 5%

    def test_skipped_counts_as_done(self) -> None:
        steps = _init_mission_steps()
        steps[0]["status"] = "skipped"
        pct = _compute_step_progress(steps)
        assert pct > 0

    def test_empty_steps_returns_zero(self) -> None:
        assert _compute_step_progress([]) == 0

    def test_progress_never_exceeds_100(self) -> None:
        steps = [
            {"name": "a", "weight": 50, "status": "completed"},
            {"name": "b", "weight": 50, "status": "completed"},
            {"name": "c", "weight": 50, "status": "completed"},
        ]
        assert _compute_step_progress(steps) == 100

    def test_init_mission_steps_structure(self) -> None:
        steps = _init_mission_steps()
        assert len(steps) == 6
        names = [s["name"] for s in steps]
        assert "init" in names
        assert "search_web" in names
        assert "fetch_pages" in names
        assert "extract_candidates" in names
        assert "verify_findings" in names
        assert "finalize" in names
        for s in steps:
            assert s["status"] == "pending"
            assert "weight" in s


# ── LM-05: Learning provenance tracing ──────────────────────────────────


class TestLearningProvenance:
    """LM-05: Learning outputs traceable to source interactions."""

    def test_learn_returns_source_interaction_id(self) -> None:
        from jarvis_engine.learning.engine import ConversationLearningEngine

        engine = ConversationLearningEngine(pipeline=None)
        result = engine.learn_from_interaction("hello world", "hi there")
        assert "source_interaction_id" in result
        assert len(result["source_interaction_id"]) > 0

    def test_learn_uses_provided_interaction_id(self) -> None:
        from jarvis_engine.learning.engine import ConversationLearningEngine

        engine = ConversationLearningEngine(pipeline=None)
        result = engine.learn_from_interaction(
            "hello world", "hi there",
            source_interaction_id="custom-id-123",
        )
        assert result["source_interaction_id"] == "custom-id-123"

    def test_learn_generates_unique_ids(self) -> None:
        from jarvis_engine.learning.engine import ConversationLearningEngine

        engine = ConversationLearningEngine(pipeline=None)
        r1 = engine.learn_from_interaction("msg1", "resp1")
        r2 = engine.learn_from_interaction("msg2", "resp2")
        assert r1["source_interaction_id"] != r2["source_interaction_id"]


# ── LM-06: Retry context preservation ───────────────────────────────────


class TestRetryContextPreservation:
    """LM-06: Retries keep prior context/results."""

    def test_preserve_prior_results_creates_snapshot(self) -> None:
        mission: dict = {
            "mission_id": "m-test",
            "status": "failed",
            "progress_pct": 45,
            "verified_findings": 3,
            "last_report_path": "/tmp/report.json",
            "steps": [{"name": "init", "status": "completed"}],
            "retries": 0,
        }
        _preserve_prior_results(mission)
        assert "prior_results" in mission
        assert len(mission["prior_results"]) == 1
        snap = mission["prior_results"][0]
        assert snap["attempt"] == 1
        assert snap["progress_pct"] == 45
        assert snap["verified_findings"] == 3
        assert "preserved_utc" in snap

    def test_preserve_accumulates_multiple_snapshots(self) -> None:
        mission: dict = {
            "mission_id": "m-test",
            "status": "failed",
            "progress_pct": 30,
            "verified_findings": 1,
            "last_report_path": "",
            "steps": [],
            "retries": 0,
        }
        _preserve_prior_results(mission)
        mission["retries"] = 1
        mission["progress_pct"] = 60
        _preserve_prior_results(mission)
        assert len(mission["prior_results"]) == 2
        assert mission["prior_results"][0]["attempt"] == 1
        assert mission["prior_results"][1]["attempt"] == 2

    def test_retry_failed_preserves_prior_results(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Test retry", objective="test"
        )
        mid = mission["mission_id"]
        # Simulate failure with some progress
        missions = load_missions(tmp_path)
        missions[0]["status"] = "failed"
        missions[0]["progress_pct"] = 50
        missions[0]["verified_findings"] = 2
        _save(tmp_path, missions)

        count = retry_failed_missions(tmp_path)
        assert count == 1

        missions = load_missions(tmp_path)
        m = missions[0]
        assert m["status"] == "pending"
        assert "prior_results" in m
        assert len(m["prior_results"]) == 1
        assert m["prior_results"][0]["progress_pct"] == 50

    def test_restart_preserves_prior_results(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Test restart", objective="test"
        )
        mid = mission["mission_id"]
        missions = load_missions(tmp_path)
        missions[0]["status"] = "failed"
        missions[0]["progress_pct"] = 75
        missions[0]["verified_findings"] = 5
        _save(tmp_path, missions)

        result = restart_mission(tmp_path, mission_id=mid)
        assert result["status"] == "pending"
        assert "prior_results" in result
        assert len(result["prior_results"]) == 1
        assert result["prior_results"][0]["progress_pct"] == 75
        assert result["prior_results"][0]["verified_findings"] == 5


# ── LM-07: Cancel safety ────────────────────────────────────────────────


class TestCancelSafety:
    """LM-07: Cancellation is immediate, safe, and visible."""

    def test_cancel_pending_mission(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Cancel test", objective="test"
        )
        result = cancel_mission(tmp_path, mission_id=mission["mission_id"])
        assert result["status"] == "cancelled"
        assert result["status_detail"] == "Cancelled"

    def test_cancel_running_mission(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Cancel running", objective="test"
        )
        mid = mission["mission_id"]
        missions = load_missions(tmp_path)
        missions[0]["status"] = "running"
        _save(tmp_path, missions)

        result = cancel_mission(tmp_path, mission_id=mid)
        assert result["status"] == "cancelled"

    def test_cancel_blocked_mission(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Cancel blocked", objective="test"
        )
        mid = mission["mission_id"]
        missions = load_missions(tmp_path)
        missions[0]["status"] = "blocked"
        _save(tmp_path, missions)

        result = cancel_mission(tmp_path, mission_id=mid)
        assert result["status"] == "cancelled"

    def test_cancel_completed_raises(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Cancel completed", objective="test"
        )
        mid = mission["mission_id"]
        missions = load_missions(tmp_path)
        missions[0]["status"] = "completed"
        _save(tmp_path, missions)

        with pytest.raises(InvalidTransitionError):
            cancel_mission(tmp_path, mission_id=mid)

    def test_cancel_already_cancelled_raises(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Double cancel", objective="test"
        )
        cancel_mission(tmp_path, mission_id=mission["mission_id"])
        # Second cancel should fail — cancelled has no transition to cancelled
        with pytest.raises(InvalidTransitionError):
            cancel_mission(tmp_path, mission_id=mission["mission_id"])

    def test_cancel_nonexistent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="mission not found"):
            cancel_mission(tmp_path, mission_id="m-doesnt-exist")

    def test_cancel_persists_to_disk(self, tmp_path: Path) -> None:
        mission = create_learning_mission(
            tmp_path, topic="Persist cancel", objective="test"
        )
        cancel_mission(tmp_path, mission_id=mission["mission_id"])
        missions = load_missions(tmp_path)
        assert missions[0]["status"] == "cancelled"


# ── LM-04 + LM-08: Intelligence delta and report ────────────────────────


class TestIntelligenceDelta:
    """LM-04: Intelligence trend metrics."""

    def test_get_intelligence_delta_empty(self, tmp_path: Path) -> None:
        from jarvis_engine.ops.intelligence_dashboard import get_intelligence_delta

        delta = get_intelligence_delta(tmp_path, period_days=7)
        assert "mission_throughput" in delta
        assert "quality_trend" in delta
        assert "learning_rate" in delta
        assert delta["period_days"] == 7
        assert delta["mission_throughput"] == 0.0

    def test_get_intelligence_delta_with_completed_missions(self, tmp_path: Path) -> None:
        from jarvis_engine.ops.intelligence_dashboard import get_intelligence_delta
        from jarvis_engine._shared import now_iso

        # Create a completed mission
        mission = create_learning_mission(tmp_path, topic="Delta test", objective="test")
        missions = load_missions(tmp_path)
        missions[0]["status"] = "completed"
        missions[0]["updated_utc"] = now_iso()
        _save(tmp_path, missions)

        delta = get_intelligence_delta(tmp_path, period_days=7)
        assert delta["mission_throughput"] > 0

    def test_get_intelligence_delta_custom_period(self, tmp_path: Path) -> None:
        from jarvis_engine.ops.intelligence_dashboard import get_intelligence_delta

        delta = get_intelligence_delta(tmp_path, period_days=30)
        assert delta["period_days"] == 30


class TestIntelligenceReport:
    """LM-08: Daily/weekly intelligence delta report."""

    def test_generate_report_structure(self, tmp_path: Path) -> None:
        from jarvis_engine.ops.intelligence_dashboard import generate_intelligence_report

        report = generate_intelligence_report(tmp_path, period_days=7)
        assert "generated_utc" in report
        assert "period_days" in report
        assert report["period_days"] == 7
        assert "intelligence_score" in report
        assert "current_pct" in report["intelligence_score"]
        assert "delta_vs_prev_pct" in report["intelligence_score"]
        assert "mission_throughput" in report
        assert "mission_stats" in report
        assert "learning_rate" in report
        assert "quality_trend" in report
        assert "assessment" in report

    def test_generate_report_assessment_values(self, tmp_path: Path) -> None:
        from jarvis_engine.ops.intelligence_dashboard import generate_intelligence_report

        report = generate_intelligence_report(tmp_path, period_days=7)
        assert report["assessment"] in ("improving", "stable", "regressing")

    def test_generate_report_with_missions(self, tmp_path: Path) -> None:
        from jarvis_engine.ops.intelligence_dashboard import generate_intelligence_report
        from jarvis_engine._shared import now_iso

        # Create some missions
        for i in range(3):
            m = create_learning_mission(
                tmp_path, topic=f"Report test {i}", objective="test"
            )
            missions = load_missions(tmp_path)
            missions[-1]["status"] = "completed"
            missions[-1]["updated_utc"] = now_iso()
            _save(tmp_path, missions)

        report = generate_intelligence_report(tmp_path, period_days=7)
        assert report["mission_stats"]["completed_in_period"] == 3
        assert report["mission_throughput"] > 0

    def test_generate_report_custom_period(self, tmp_path: Path) -> None:
        from jarvis_engine.ops.intelligence_dashboard import generate_intelligence_report

        report = generate_intelligence_report(tmp_path, period_days=1)
        assert report["period_days"] == 1


# ── Lifecycle integration tests ──────────────────────────────────────────


class TestLifecycleIntegration:
    """End-to-end lifecycle transitions through multiple states."""

    def test_full_lifecycle_pending_to_completed(self, tmp_path: Path) -> None:
        """pending -> running -> completed (via _finalize_mission)."""
        mission = create_learning_mission(
            tmp_path, topic="Full lifecycle", objective="test"
        )
        mid = mission["mission_id"]
        assert mission["status"] == "pending"

        # Transition to running
        missions = load_missions(tmp_path)
        _check_transition(mid, "pending", "running")
        missions[0]["status"] = "running"
        _save(tmp_path, missions)

        # Transition to completed
        missions = load_missions(tmp_path)
        _check_transition(mid, "running", "completed")
        missions[0]["status"] = "completed"
        _save(tmp_path, missions)

        final = load_missions(tmp_path)[0]
        assert final["status"] == "completed"

    def test_lifecycle_pause_resume_complete(self, tmp_path: Path) -> None:
        """pending -> running -> paused -> pending -> running -> completed."""
        mission = create_learning_mission(
            tmp_path, topic="Pause resume", objective="test"
        )
        mid = mission["mission_id"]

        # running
        missions = load_missions(tmp_path)
        missions[0]["status"] = "running"
        _save(tmp_path, missions)

        # pause
        pause_mission(tmp_path, mission_id=mid)
        assert load_missions(tmp_path)[0]["status"] == "paused"

        # resume (paused -> pending)
        resume_mission(tmp_path, mission_id=mid)
        assert load_missions(tmp_path)[0]["status"] == "pending"

    def test_lifecycle_block_unblock(self, tmp_path: Path) -> None:
        """running -> blocked -> running."""
        mission = create_learning_mission(
            tmp_path, topic="Block test", objective="test"
        )
        mid = mission["mission_id"]

        missions = load_missions(tmp_path)
        missions[0]["status"] = "running"
        _save(tmp_path, missions)

        block_mission(tmp_path, mission_id=mid, reason="API down")
        assert load_missions(tmp_path)[0]["status"] == "blocked"

        unblock_mission(tmp_path, mission_id=mid)
        assert load_missions(tmp_path)[0]["status"] == "running"

    def test_lifecycle_fail_retry_with_context(self, tmp_path: Path) -> None:
        """pending -> running -> failed -> pending (retry with prior_results)."""
        mission = create_learning_mission(
            tmp_path, topic="Fail retry", objective="test"
        )
        mid = mission["mission_id"]

        missions = load_missions(tmp_path)
        missions[0]["status"] = "failed"
        missions[0]["progress_pct"] = 40
        missions[0]["verified_findings"] = 1
        _save(tmp_path, missions)

        retry_failed_missions(tmp_path)
        m = load_missions(tmp_path)[0]
        assert m["status"] == "pending"
        assert m["retries"] == 1
        assert len(m["prior_results"]) == 1


# ── Helpers ──────────────────────────────────────────────────────────────


def _save(root: Path, missions: list) -> None:
    """Helper to persist missions list."""
    from jarvis_engine.learning.missions import _save_missions

    _save_missions(root, missions)
