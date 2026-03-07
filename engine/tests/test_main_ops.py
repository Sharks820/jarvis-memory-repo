"""Tests for ops-related CLI commands.

Covers: ops-brief, ops-sync, ops-export-actions, ops-autopilot, daemon-run,
daemon self-test, gaming mode, runtime control, missions, growth eval/report/audit,
intelligence dashboard, automation-run.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine import main as main_mod
from jarvis_engine import voice_pipeline as voice_pipeline_mod
from jarvis_engine import daemon_loop as daemon_loop_mod
from jarvis_engine import _bus as bus_mod
from jarvis_engine.command_bus import AppContext


# ===========================================================================
# Daemon run tests
# ===========================================================================


def test_cmd_daemon_run_uses_active_interval_when_active(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 10.0)

    calls: dict[str, object] = {"ops": 0, "sleep": []}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        calls["ops"] = int(calls["ops"]) + 1
        return 0

    def fake_sleep(seconds: int) -> None:
        sleeps = calls["sleep"]
        assert isinstance(sleeps, list)
        sleeps.append(seconds)

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)
    monkeypatch.setattr(daemon_loop_mod.time, "sleep", fake_sleep)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert calls["ops"] == 2
    assert calls["sleep"] == [120]


def test_cmd_daemon_run_skips_autopilot_while_gaming_mode_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    main_mod.cmd_gaming_mode(enable=True, reason="session", auto_detect="")
    monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 0.0)

    calls: dict[str, object] = {"ops": 0, "sleep": []}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        calls["ops"] = int(calls["ops"]) + 1
        return 0

    def fake_sleep(seconds: int) -> None:
        sleeps = calls["sleep"]
        assert isinstance(sleeps, list)
        sleeps.append(seconds)

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)
    monkeypatch.setattr(daemon_loop_mod.time, "sleep", fake_sleep)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert calls["ops"] == 0
    assert calls["sleep"] == [900]


def test_cmd_daemon_run_skips_autopilot_when_auto_detect_finds_game(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    main_mod.cmd_gaming_mode(enable=False, reason="auto", auto_detect="on")
    monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(daemon_loop_mod, "detect_active_game_process", lambda: (True, "fortniteclient-win64-shipping.exe"))

    calls: dict[str, object] = {"ops": 0, "sleep": []}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        calls["ops"] = int(calls["ops"]) + 1
        return 0

    def fake_sleep(seconds: int) -> None:
        sleeps = calls["sleep"]
        assert isinstance(sleeps, list)
        sleeps.append(seconds)

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)
    monkeypatch.setattr(daemon_loop_mod.time, "sleep", fake_sleep)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert calls["ops"] == 0
    assert calls["sleep"] == [900]


def test_cmd_daemon_run_skips_autopilot_when_runtime_paused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    main_mod.cmd_runtime_control(
        pause=True,
        resume=False,
        safe_on=False,
        safe_off=False,
        reset=False,
        reason="pause test",
    )
    monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(daemon_loop_mod, "detect_active_game_process", lambda: (False, ""))

    calls: dict[str, object] = {"ops": 0, "sleep": []}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        calls["ops"] = int(calls["ops"]) + 1
        return 0

    def fake_sleep(seconds: int) -> None:
        sleeps = calls["sleep"]
        assert isinstance(sleeps, list)
        sleeps.append(seconds)

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)
    monkeypatch.setattr(daemon_loop_mod.time, "sleep", fake_sleep)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert calls["ops"] == 0
    assert calls["sleep"] == [900]


def test_cmd_daemon_run_safe_mode_forces_non_execute_cycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    main_mod.cmd_runtime_control(
        pause=False,
        resume=False,
        safe_on=True,
        safe_off=False,
        reset=False,
        reason="safe",
    )
    monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(daemon_loop_mod, "detect_active_game_process", lambda: (False, ""))

    observed: dict[str, bool] = {"execute": True, "approve_privileged": True}

    def fake_ops_autopilot(*args, **kwargs) -> int:
        observed["execute"] = bool(kwargs.get("execute"))
        observed["approve_privileged"] = bool(kwargs.get("approve_privileged"))
        return 0

    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", fake_ops_autopilot)

    rc = main_mod.cmd_daemon_run(
        interval_s=120,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        execute=True,
        approve_privileged=True,
        auto_open_connectors=False,
        max_cycles=1,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
    )
    assert rc == 0
    assert observed["execute"] is False
    assert observed["approve_privileged"] is False


def test_cmd_gaming_mode_persists_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc = main_mod.cmd_gaming_mode(enable=True, reason="gaming", auto_detect="")
    assert rc == 0

    state_path = tmp_path / ".planning" / "runtime" / "gaming_mode.json"
    assert state_path.exists()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["enabled"] is True
    assert raw["reason"] == "gaming"

    rc2 = main_mod.cmd_gaming_mode(enable=False, reason="", auto_detect="")
    assert rc2 == 0
    raw2 = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw2["enabled"] is False


def test_cmd_runtime_control_persists_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc = main_mod.cmd_runtime_control(
        pause=True,
        resume=False,
        safe_on=True,
        safe_off=False,
        reset=False,
        reason="maintenance",
    )
    assert rc == 0
    state_path = tmp_path / ".planning" / "runtime" / "control.json"
    assert state_path.exists()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["daemon_paused"] is True
    assert raw["safe_mode"] is True
    assert raw["reason"] == "maintenance"


# ===========================================================================
# Ops commands via mock bus
# ===========================================================================


class TestOpsBrief:
    """Tests for cmd_ops_brief."""

    def test_ops_brief_basic(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import OpsBriefResult
        result = OpsBriefResult(brief="Good morning, Conner. Here's your brief.", saved_path="")
        bus = mock_bus(result)
        rc = main_mod.cmd_ops_brief(snapshot_path=Path("/tmp/snap.json"), output_path=None)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Good morning" in out

    def test_ops_brief_with_save(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import OpsBriefResult
        result = OpsBriefResult(brief="Brief.", saved_path="/tmp/brief.txt")
        bus = mock_bus(result)
        rc = main_mod.cmd_ops_brief(snapshot_path=Path("/tmp/snap.json"),
                                     output_path=Path("/tmp/brief.txt"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "brief_saved=" in out


class TestOpsExportActions:
    """Tests for cmd_ops_export_actions."""

    def test_export_actions(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import OpsExportActionsResult
        result = OpsExportActionsResult(actions_path="/tmp/actions.json", action_count=3)
        bus = mock_bus(result)
        rc = main_mod.cmd_ops_export_actions(snapshot_path=Path("/tmp/snap.json"),
                                              actions_path=Path("/tmp/actions.json"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "action_count=3" in out


class TestOpsSync:
    """Tests for cmd_ops_sync."""

    def test_ops_sync_success(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import OpsSyncResult
        summary = MagicMock()
        summary.snapshot_path = "/tmp/snap.json"
        summary.tasks = 5
        summary.calendar_events = 2
        summary.emails = 10
        summary.bills = 1
        summary.subscriptions = 3
        summary.medications = 0
        summary.school_items = 0
        summary.family_items = 1
        summary.projects = 2
        summary.connectors_ready = 2
        summary.connectors_pending = 0
        summary.connector_prompts = 0
        result = OpsSyncResult(summary=summary)
        bus = mock_bus(result)
        rc = main_mod.cmd_ops_sync(output_path=Path("/tmp/snap.json"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "tasks=5" in out
        assert "emails=10" in out

    def test_ops_sync_fail(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import OpsSyncResult
        result = OpsSyncResult(summary=None)
        bus = mock_bus(result)
        rc = main_mod.cmd_ops_sync(output_path=Path("/tmp/snap.json"))
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out


class TestAutomationRun:
    """Tests for cmd_automation_run."""

    def test_automation_run_basic(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import AutomationRunResult
        outcome = MagicMock()
        outcome.title = "Send email"
        outcome.allowed = True
        outcome.executed = True
        outcome.return_code = 0
        outcome.reason = "ok"
        outcome.stderr = ""
        result = AutomationRunResult(outcomes=[outcome])
        bus = mock_bus(result)
        rc = main_mod.cmd_automation_run(actions_path=Path("/tmp/actions.json"),
                                          approve_privileged=False, execute=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Send email" in out
        assert "allowed=True" in out

    def test_automation_run_with_stderr(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import AutomationRunResult
        outcome = MagicMock()
        outcome.title = "Failing action"
        outcome.allowed = False
        outcome.executed = False
        outcome.return_code = 1
        outcome.reason = "denied"
        outcome.stderr = "Permission denied"
        result = AutomationRunResult(outcomes=[outcome])
        bus = mock_bus(result)
        rc = main_mod.cmd_automation_run(actions_path=Path("/tmp/actions.json"),
                                          approve_privileged=False, execute=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Permission denied" in out


# ===========================================================================
# Mission commands
# ===========================================================================


class TestMissionCreate:
    """Tests for cmd_mission_create."""

    def test_create_success(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import MissionCreateResult
        result = MissionCreateResult(
            mission={"mission_id": "m-1", "topic": "Python async", "sources": ["google", "reddit"]},
            return_code=0,
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_mission_create(topic="Python async", objective="", sources=["google", "reddit"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "mission_id=m-1" in out
        assert "learning_mission_created=true" in out

    def test_create_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import MissionCreateResult
        result = MissionCreateResult(return_code=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_mission_create(topic="", objective="", sources=[])
        assert rc == 2


class TestMissionStatus:
    """Tests for cmd_mission_status."""

    def test_status_empty(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import MissionStatusResult
        result = MissionStatusResult(missions=[], total_count=0)
        bus = mock_bus(result)
        rc = main_mod.cmd_mission_status(last=10)
        assert rc == 0
        out = capsys.readouterr().out
        assert "learning_missions=none" in out
        assert "learning_missions_active=false" in out
        assert "learning_mission_count=0" in out

    def test_status_with_missions(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import MissionStatusResult
        missions = [
            {"mission_id": "m1", "status": "completed", "topic": "AI safety",
             "verified_findings": 3, "updated_utc": "2026-01-01", "status_detail": "Finalized report"},
            {"mission_id": "m2", "status": "running", "topic": "Realtime STT",
             "verified_findings": 1, "updated_utc": "2026-01-02", "progress_pct": 45, "status_detail": "Scanning 8 pages"},
        ]
        result = MissionStatusResult(missions=missions, total_count=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_mission_status(last=5)
        assert rc == 0
        out = capsys.readouterr().out
        assert "mission_id=m1" in out
        assert "mission_id=m2" in out
        assert "AI safety" in out
        assert "learning_missions_active=true" in out
        assert "learning_missions_active_count=1" in out
        assert "learning_missions_completed=1" in out
        assert "learning_missions_running=1" in out
        assert "mission_status_detail=Scanning 8 pages" in out
        assert "response=Learning missions (2 total, 1 active):" in out


class TestMissionRun:
    """Tests for cmd_mission_run."""

    def test_run_success(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import MissionRunResult
        result = MissionRunResult(
            report={"mission_id": "m1", "candidate_count": 10, "verified_count": 3,
                    "verified_findings": [
                        {"statement": "AI can reason", "source_domains": ["arxiv.org"]},
                    ]},
            return_code=0,
            ingested_record_id="rec-42",
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_mission_run(mission_id="m1", max_results=8, max_pages=12, auto_ingest=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "learning_mission_completed=true" in out
        assert "verified_count=3" in out
        assert "mission_ingested_record_id=rec-42" in out

    def test_run_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import MissionRunResult
        result = MissionRunResult(return_code=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_mission_run(mission_id="bad", max_results=8, max_pages=12, auto_ingest=True)
        assert rc == 2


# ===========================================================================
# Growth commands (eval, report, audit, intelligence dashboard)
# ===========================================================================


class TestGrowthEval:
    """Tests for cmd_growth_eval."""

    def test_eval_success(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import GrowthEvalResult
        task_result = MagicMock()
        task_result.task_id = "t1"
        task_result.coverage = 0.85
        task_result.matched = 3
        task_result.total = 4
        task_result.response_sha256 = "sha256abc"
        run = MagicMock()
        run.model = "gemma3:4b"
        run.score_pct = 82.5
        run.avg_tps = 45.2
        run.avg_latency_s = 1.1
        run.results = [task_result]
        result = GrowthEvalResult(run=run)
        bus = mock_bus(result)
        rc = main_mod.cmd_growth_eval(
            model="gemma3:4b", endpoint="http://127.0.0.1:11434",
            tasks_path=Path("/tmp/tasks.json"), history_path=Path("/tmp/hist.jsonl"),
            num_predict=256, temperature=0.0, think=None, accept_thinking=False, timeout_s=120,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "growth_eval_completed=true" in out
        assert "score_pct=82.5" in out

    def test_eval_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import GrowthEvalResult
        result = GrowthEvalResult(run=None)
        bus = mock_bus(result)
        rc = main_mod.cmd_growth_eval(
            model="bad", endpoint="x", tasks_path=Path("x"), history_path=Path("x"),
            num_predict=256, temperature=0.0, think=None, accept_thinking=False, timeout_s=120,
        )
        assert rc == 2


class TestGrowthReport:
    """Tests for cmd_growth_report."""

    def test_report(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import GrowthReportResult
        result = GrowthReportResult(summary={
            "runs": 10, "latest_model": "gemma3:4b", "latest_score_pct": 80.0,
            "delta_vs_prev_pct": 2.5, "window_avg_pct": 78.0, "latest_ts": "2026-01-01",
        })
        bus = mock_bus(result)
        rc = main_mod.cmd_growth_report(history_path=Path("/tmp/hist.jsonl"), last=10)
        assert rc == 0
        out = capsys.readouterr().out
        assert "runs=10" in out
        assert "latest_score_pct=80.0" in out


class TestGrowthAudit:
    """Tests for cmd_growth_audit."""

    def test_audit(self, capsys, mock_bus):
        from jarvis_engine.commands.ops_commands import GrowthAuditResult
        result = GrowthAuditResult(run={
            "model": "gemma3:4b", "ts": "2026-01-01", "score_pct": 80.0,
            "tasks": 5, "prev_run_sha256": "prev", "run_sha256": "cur",
            "results": [
                {"task_id": "t1", "matched_tokens": ["a", "b"], "required_tokens": ["a", "b", "c"],
                 "prompt_sha256": "p", "response_sha256": "r",
                 "response_source": "live", "response": "answer"},
            ],
        })
        bus = mock_bus(result)
        rc = main_mod.cmd_growth_audit(history_path=Path("/tmp/hist.jsonl"), run_index=-1)
        assert rc == 0
        out = capsys.readouterr().out
        assert "growth_audit" in out
        assert "task=t1" in out


class TestIntelligenceDashboard:
    """Tests for cmd_intelligence_dashboard."""

    def test_dashboard_json(self, capsys, monkeypatch, mock_bus, tmp_path):
        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult
        dashboard = {
            "generated_utc": "2026-01-01",
            "jarvis": {"score_pct": 80.0, "delta_vs_prev_pct": 1.0, "window_avg_pct": 78.0, "latest_model": "gemma3:4b"},
            "methodology": {"history_runs": 10, "slope_score_pct_per_run": 0.5, "avg_days_per_run": 3.0},
            "ranking": [{"name": "GPT-4", "score_pct": 90.0}],
            "etas": [],
            "achievements": {"new": []},
        }
        result = IntelligenceDashboardResult(dashboard=dashboard)
        bus = mock_bus(result)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        rc = main_mod.cmd_intelligence_dashboard(last_runs=20, output_path="", as_json=True)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["jarvis"]["score_pct"] == 80.0

    def test_dashboard_text(self, capsys, monkeypatch, mock_bus, tmp_path):
        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult
        dashboard = {
            "generated_utc": "2026-01-01",
            "jarvis": {"score_pct": 80.0, "delta_vs_prev_pct": 1.0, "window_avg_pct": 78.0, "latest_model": "gemma3:4b"},
            "methodology": {"history_runs": 10, "slope_score_pct_per_run": 0.5, "avg_days_per_run": 3.0},
            "ranking": [{"name": "GPT-4", "score_pct": 90.0}],
            "etas": [{"target_name": "GPT-4", "target_score_pct": 90.0, "eta": {"status": "on_track", "runs": 20, "days": 60}}],
            "achievements": {"new": [{"label": "First run completed"}]},
        }
        result = IntelligenceDashboardResult(dashboard=dashboard)
        bus = mock_bus(result)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        rc = main_mod.cmd_intelligence_dashboard(last_runs=20, output_path="", as_json=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "intelligence_dashboard" in out
        assert "jarvis_score_pct=80.0" in out
        assert "rank_1=GPT-4:90.0" in out
        assert "achievement_unlocked=First run completed" in out


class TestIntelligenceDashboardOutputPath:
    """Tests for intelligence dashboard output path restrictions."""

    def test_output_path_outside_repo_json(self, capsys, monkeypatch, mock_bus, tmp_path):
        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult
        dashboard = {"jarvis": {}, "methodology": {}, "ranking": [], "etas": [], "achievements": {}}
        result = IntelligenceDashboardResult(dashboard=dashboard)
        bus = mock_bus(result)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        # Use a path clearly outside repo root
        rc = main_mod.cmd_intelligence_dashboard(
            last_runs=5, output_path="/tmp/totally/outside/dashboard.json", as_json=True,
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "error" in out

    def test_output_path_inside_repo_json(self, capsys, monkeypatch, mock_bus, tmp_path):
        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult
        dashboard = {"jarvis": {}, "methodology": {}, "ranking": [], "etas": [], "achievements": {}}
        result = IntelligenceDashboardResult(dashboard=dashboard)
        bus = mock_bus(result)
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        out_path = str(tmp_path / "output" / "dash.json")
        rc = main_mod.cmd_intelligence_dashboard(last_runs=5, output_path=out_path, as_json=True)
        assert rc == 0
        assert (tmp_path / "output" / "dash.json").exists()


# ===========================================================================
# Daemon self-test cycle integration
# ===========================================================================


class TestDaemonSelfTest:
    """Tests for adversarial self-test integration within daemon run loop."""

    def _run_daemon_impl(self, tmp_path, monkeypatch, *,
                         self_test_every_cycles=1, max_cycles=1):
        """Helper: run cmd_daemon_run_impl with heavy mocking."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(main_mod, "cmd_ops_autopilot", lambda **kw: 0)
        monkeypatch.setattr(daemon_loop_mod.time, "sleep", lambda s: None)
        # Ensure runtime dir exists for self_test_history.jsonl
        (tmp_path / ".planning" / "runtime").mkdir(parents=True, exist_ok=True)
        return daemon_loop_mod.cmd_daemon_run_impl(
            interval_s=120,
            snapshot_path=tmp_path / "ops_snapshot.live.json",
            actions_path=tmp_path / "actions.generated.json",
            execute=False,
            approve_privileged=False,
            auto_open_connectors=False,
            max_cycles=max_cycles,
            idle_interval_s=900,
            idle_after_s=300,
            run_missions=False,
            sync_every_cycles=0,
            self_heal_every_cycles=0,
            self_test_every_cycles=self_test_every_cycles,
        )

    def test_self_test_runs_at_correct_cycle(self, capsys, monkeypatch, tmp_path):
        """Verify self-test fires when cycles % self_test_every_cycles == 0."""
        mock_tester = MagicMock()
        mock_tester.run_memory_quiz.return_value = {"average_score": 0.92, "tasks_run": 4}
        mock_tester.check_regression.return_value = {"regression_detected": False}

        mock_engine = MagicMock()
        mock_embed = MagicMock()
        mock_bus_obj = MagicMock()
        mock_bus_obj.ctx = AppContext(engine=mock_engine, embed_service=mock_embed)
        monkeypatch.setattr(daemon_loop_mod, "_get_daemon_bus", lambda: mock_bus_obj)

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest",
                    return_value=mock_tester) as mock_cls:
            rc = self._run_daemon_impl(tmp_path, monkeypatch,
                                       self_test_every_cycles=1, max_cycles=1)

        assert rc == 0
        mock_cls.assert_called_once_with(mock_engine, mock_embed,
                                          score_threshold=0.5)
        mock_tester.run_memory_quiz.assert_called_once()
        mock_tester.save_quiz_result.assert_called_once()
        mock_tester.check_regression.assert_called_once()
        out = capsys.readouterr().out
        assert "self_test_score=0.9200" in out
        assert "self_test_tasks=4" in out

    def test_self_test_skipped_when_disabled(self, capsys, monkeypatch, tmp_path):
        """Verify no self-test activity when self_test_every_cycles=0."""
        mock_bus_obj = MagicMock()
        mock_bus_obj.ctx = AppContext(engine=MagicMock(), embed_service=MagicMock())
        monkeypatch.setattr(daemon_loop_mod, "_get_daemon_bus", lambda: mock_bus_obj)

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest") as mock_cls:
            rc = self._run_daemon_impl(tmp_path, monkeypatch,
                                       self_test_every_cycles=0, max_cycles=1)

        assert rc == 0
        mock_cls.assert_not_called()
        out = capsys.readouterr().out
        assert "self_test_score" not in out
        assert "self_test_skipped" not in out

    def test_self_test_handles_missing_engine(self, capsys, monkeypatch, tmp_path):
        """Verify 'skipped' message when engine or embed_svc is None on bus."""
        mock_bus_obj = MagicMock()
        mock_bus_obj.ctx = AppContext(engine=None, embed_service=None)
        monkeypatch.setattr(daemon_loop_mod, "_get_daemon_bus", lambda: mock_bus_obj)

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest") as mock_cls:
            rc = self._run_daemon_impl(tmp_path, monkeypatch,
                                       self_test_every_cycles=1, max_cycles=1)

        assert rc == 0
        mock_cls.assert_not_called()
        out = capsys.readouterr().out
        assert "self_test_skipped=engine_not_initialized" in out

    def test_self_test_handles_error(self, capsys, monkeypatch, tmp_path):
        """Verify error is caught and printed, daemon continues running."""
        mock_bus_obj = MagicMock()
        mock_bus_obj.ctx = AppContext(engine=MagicMock(), embed_service=MagicMock())
        monkeypatch.setattr(daemon_loop_mod, "_get_daemon_bus", lambda: mock_bus_obj)

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest",
                    side_effect=RuntimeError("quiz DB corrupt")):
            rc = self._run_daemon_impl(tmp_path, monkeypatch,
                                       self_test_every_cycles=1, max_cycles=1)

        assert rc == 0  # daemon did NOT crash
        out = capsys.readouterr().out
        assert "self_test_error=" in out
        assert "quiz DB corrupt" in out

    def test_self_test_every_cycles_in_command(self):
        """Verify DaemonRunCommand dataclass has self_test_every_cycles with default 20."""
        from jarvis_engine.commands.system_commands import DaemonRunCommand
        cmd = DaemonRunCommand()
        assert hasattr(cmd, "self_test_every_cycles")
        assert cmd.self_test_every_cycles == 20
