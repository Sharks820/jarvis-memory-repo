from __future__ import annotations

from jarvis_engine.desktop_controller import (
    DesktopInteractionController,
    DesktopWidgetState,
)


def test_begin_command_transitions_to_processing() -> None:
    seen_states: list[DesktopWidgetState] = []
    controller = DesktopInteractionController(on_state_change=seen_states.append)

    generation = controller.begin_command()

    assert generation == 1
    assert controller.state is DesktopWidgetState.PROCESSING
    assert controller.command_generation == 1
    assert seen_states == [DesktopWidgetState.PROCESSING]


def test_complete_command_rejects_stale_generation() -> None:
    controller = DesktopInteractionController()

    generation = controller.begin_command()
    assert generation == 1
    assert controller.complete_command(999) is False
    assert controller.state is DesktopWidgetState.PROCESSING


def test_cancel_command_sets_cancel_flag_until_next_command() -> None:
    controller = DesktopInteractionController()
    controller.begin_command()

    controller.cancel_command()

    assert controller.cancel_event.is_set()
    assert controller.state is DesktopWidgetState.IDLE

    controller.begin_command()
    assert not controller.cancel_event.is_set()


def test_begin_dictation_blocks_during_processing() -> None:
    controller = DesktopInteractionController()
    controller.begin_command()

    assert controller.begin_dictation() is False
    assert controller.state is DesktopWidgetState.PROCESSING


def test_hotword_loop_guard_is_single_owner() -> None:
    controller = DesktopInteractionController()

    assert controller.try_start_hotword_loop() is True
    assert controller.try_start_hotword_loop() is False

    controller.finish_hotword_loop()

    assert controller.try_start_hotword_loop() is True


def test_invalid_state_coerces_to_idle() -> None:
    controller = DesktopInteractionController()

    result = controller.set_state("bogus")

    assert result is DesktopWidgetState.IDLE
    assert controller.state is DesktopWidgetState.IDLE


def test_apply_health_snapshot_tracks_mission_and_intelligence() -> None:
    controller = DesktopInteractionController()

    new_events = controller.apply_health_snapshot(
        online=True,
        intel_data={"score": 0.82, "regression": False},
        growth_data={
            "metrics": {
                "facts_total": 18,
                "facts_last_7d": 4,
                "kg_nodes": 21,
                "kg_edges": 34,
                "memory_records": 11,
                "last_self_test_score": 0.73,
                "growth_trend": "increasing",
                "mission_count": 2,
                "active_missions": [
                    {"topic": "Desktop redesign", "status": "running"},
                    {"topic": "Voice continuity", "status": "pending"},
                ],
            }
        },
        recent_events=[
            {
                "event_id": "evt-1",
                "timestamp": "2026-03-12T10:15:30Z",
                "category": "voice",
                "summary": "Listening cycle armed",
            }
        ],
        now_working_on={
            "mission_topic": "Desktop redesign",
            "current_step": "Animating live capsule",
            "progress_pct": 42,
            "artifacts_so_far": 3,
        },
        clear_missing=True,
    )

    snapshot = controller.snapshot()
    assert len(new_events) == 1
    assert snapshot.online is True
    assert snapshot.intelligence_score_pct == 82
    assert snapshot.self_test_score_pct == 73
    assert snapshot.growth_trend == "increasing"
    assert snapshot.mission.count == 2
    assert snapshot.mission.current_topic == "Desktop redesign"
    assert snapshot.mission.current_step == "Animating live capsule"
    assert snapshot.activity.summary == "Listening cycle armed"


def test_apply_health_snapshot_dedupes_activity_ids() -> None:
    controller = DesktopInteractionController()
    events = [
        {
            "event_id": "evt-1",
            "timestamp": "2026-03-12T10:15:30Z",
            "category": "voice",
            "summary": "Listening cycle armed",
        }
    ]

    first = controller.apply_health_snapshot(online=True, recent_events=events)
    second = controller.apply_health_snapshot(online=True, recent_events=events)

    assert len(first) == 1
    assert second == []


def test_apply_session_snapshot_tracks_route_and_security_posture() -> None:
    controller = DesktopInteractionController()

    controller.apply_session_snapshot(
        route_label="Claude CLI",
        route_accent="#d946ef",
        route_family="cli",
        control_armed=True,
        auto_approve=False,
        wakeword_enabled=True,
        speech_enabled=False,
    )

    snapshot = controller.snapshot()
    assert snapshot.session.route_label == "Claude CLI"
    assert snapshot.session.route_family == "cli"
    assert snapshot.session.control_mode == "Desktop control armed"
    assert snapshot.session.approval_mode == "Approval required"
    assert snapshot.session.voice_mode == "Wake word live"
    assert snapshot.session.speech_mode == "Silent replies"


def test_apply_continuity_snapshot_tracks_entities_goals_and_decisions() -> None:
    controller = DesktopInteractionController()

    controller.apply_continuity_snapshot(
        rolling_summary="Jarvis is comparing local model options for the desktop refresh.",
        anchor_entities=["Qwen 3.5 9B", "Desktop redesign", "March 12, 2026"],
        unresolved_goals=["Need to tighten the transcript surface", "Still need to improve motion quality"],
        prior_decisions=["Use compact layout on short displays"],
        timeline_count=18,
    )

    snapshot = controller.snapshot()
    assert snapshot.continuity.rolling_summary.startswith("Jarvis is comparing")
    assert snapshot.continuity.anchor_entities == (
        "Qwen 3.5 9B",
        "Desktop redesign",
        "March 12, 2026",
    )
    assert snapshot.continuity.unresolved_goals == (
        "Need to tighten the transcript surface",
        "Still need to improve motion quality",
    )
    assert snapshot.continuity.prior_decisions == ("Use compact layout on short displays",)
    assert snapshot.continuity.timeline_count == 18


def test_apply_diagnostics_snapshot_tracks_health_score_and_top_issue() -> None:
    controller = DesktopInteractionController()

    controller.apply_diagnostics_snapshot(
        score=68,
        healthy=False,
        issues=[{
            "id": "diag-1",
            "description": "WAL file is 82.1 MB (threshold: 50 MB)",
        }],
    )

    snapshot = controller.snapshot()
    assert snapshot.diagnostics.score == 68
    assert snapshot.diagnostics.healthy is False
    assert snapshot.diagnostics.issue_count == 1
    assert snapshot.diagnostics.top_issue == "WAL file is 82.1 MB (threshold: 50 MB)"
