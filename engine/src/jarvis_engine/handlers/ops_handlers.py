"""Ops / growth / mission handler classes -- adapter shims."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from jarvis_engine.commands.ops_commands import (
    AutomationRunCommand,
    AutomationRunResult,
    GrowthAuditCommand,
    GrowthAuditResult,
    GrowthEvalCommand,
    GrowthEvalResult,
    GrowthReportCommand,
    GrowthReportResult,
    IntelligenceDashboardCommand,
    IntelligenceDashboardResult,
    MissionCreateCommand,
    MissionCreateResult,
    MissionRunCommand,
    MissionRunResult,
    MissionStatusCommand,
    MissionStatusResult,
    OpsAutopilotCommand,
    OpsAutopilotResult,
    OpsBriefCommand,
    OpsBriefResult,
    OpsExportActionsCommand,
    OpsExportActionsResult,
    OpsSyncCommand,
    OpsSyncResult,
)


class OpsBriefHandler:
    def __init__(self, root: Path, gateway: Any = None) -> None:
        self._root = root
        self._gateway = gateway

    def handle(self, cmd: OpsBriefCommand) -> OpsBriefResult:
        from jarvis_engine.life_ops import build_daily_brief, build_narrative_brief, load_snapshot

        snapshot = load_snapshot(cmd.snapshot_path)
        brief = ""
        if self._gateway is not None:
            try:
                brief = build_narrative_brief(snapshot, gateway=self._gateway)
            except Exception as exc:
                logger.warning("Narrative brief generation failed in handler: %s", exc)
                brief = ""
        if not brief:
            brief = build_daily_brief(snapshot)
        saved = ""
        if cmd.output_path:
            cmd.output_path.parent.mkdir(parents=True, exist_ok=True)
            cmd.output_path.write_text(brief, encoding="utf-8")
            saved = str(cmd.output_path)
        return OpsBriefResult(brief=brief, saved_path=saved)


class OpsExportActionsHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: OpsExportActionsCommand) -> OpsExportActionsResult:
        from jarvis_engine.life_ops import export_actions_json, load_snapshot, suggest_actions

        snapshot = load_snapshot(cmd.snapshot_path)
        actions = suggest_actions(snapshot)
        export_actions_json(actions, cmd.actions_path)
        return OpsExportActionsResult(
            actions_path=str(cmd.actions_path),
            action_count=len(actions),
        )


class OpsSyncHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: OpsSyncCommand) -> OpsSyncResult:
        from jarvis_engine.ops_sync import build_live_snapshot

        summary = build_live_snapshot(self._root, cmd.output_path)
        return OpsSyncResult(summary=summary)


class OpsAutopilotHandler:
    """Delegates to existing cmd_* composition logic."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: OpsAutopilotCommand) -> OpsAutopilotResult:
        from jarvis_engine import main as _main_mod

        rc = _main_mod._cmd_ops_autopilot_impl(
            snapshot_path=cmd.snapshot_path,
            actions_path=cmd.actions_path,
            execute=cmd.execute,
            approve_privileged=cmd.approve_privileged,
            auto_open_connectors=cmd.auto_open_connectors,
        )
        return OpsAutopilotResult(return_code=rc)


class AutomationRunHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: AutomationRunCommand) -> AutomationRunResult:
        from jarvis_engine.automation import AutomationExecutor, load_actions
        from jarvis_engine.memory_store import MemoryStore

        store = MemoryStore(self._root)
        executor = AutomationExecutor(store)
        actions = load_actions(cmd.actions_path)
        outcomes = executor.run(
            actions,
            has_explicit_approval=cmd.approve_privileged,
            execute=cmd.execute,
        )
        return AutomationRunResult(outcomes=outcomes)


class MissionCreateHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionCreateCommand) -> MissionCreateResult:
        from jarvis_engine.learning_missions import create_learning_mission

        try:
            mission = create_learning_mission(
                self._root,
                topic=cmd.topic,
                objective=cmd.objective,
                sources=cmd.sources,
            )
        except ValueError:
            return MissionCreateResult(return_code=2)
        return MissionCreateResult(mission=mission, return_code=0)


class MissionStatusHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionStatusCommand) -> MissionStatusResult:
        from jarvis_engine.learning_missions import load_missions

        missions = load_missions(self._root)
        return MissionStatusResult(
            missions=missions[-max(1, cmd.last) :],
            total_count=len(missions),
        )


class MissionRunHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionRunCommand) -> MissionRunResult:
        from jarvis_engine.learning_missions import run_learning_mission

        try:
            report = run_learning_mission(
                self._root,
                mission_id=cmd.mission_id,
                max_search_results=cmd.max_results,
                max_pages=cmd.max_pages,
            )
        except ValueError:
            return MissionRunResult(return_code=2)

        ingested_id = ""
        verified = report.get("verified_findings", [])
        if cmd.auto_ingest and isinstance(verified, list) and verified:
            try:
                from jarvis_engine.ingest import IngestionPipeline
                from jarvis_engine.memory_store import MemoryStore

                lines = []
                for finding in verified[:20]:
                    if not isinstance(finding, dict):
                        continue
                    statement = str(finding.get("statement", "")).strip()
                    domains = ",".join(str(x) for x in finding.get("source_domains", []))
                    if statement:
                        lines.append(f"- {statement} [sources:{domains}]")
                content = "Verified learning mission findings:\n" + "\n".join(lines)
                store = MemoryStore(self._root)
                pipeline = IngestionPipeline(store)
                rec = pipeline.ingest(
                    source="task_outcome",
                    kind="semantic",
                    task_id=f"mission-{report.get('mission_id', '')}",
                    content=content[:18000],
                )
                ingested_id = rec.record_id
            except Exception as exc:
                logger.debug("Auto-ingest failed for mission: %s", exc)
        return MissionRunResult(report=report, return_code=0, ingested_record_id=ingested_id)


class GrowthEvalHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthEvalCommand) -> GrowthEvalResult:
        from jarvis_engine.growth_tracker import append_history, load_golden_tasks, run_eval

        tasks = load_golden_tasks(cmd.tasks_path)
        run = run_eval(
            endpoint=cmd.endpoint,
            model=cmd.model,
            tasks=tasks,
            num_predict=cmd.num_predict,
            temperature=cmd.temperature,
            think=cmd.think,
            accept_thinking=cmd.accept_thinking,
            timeout_s=cmd.timeout_s,
        )
        append_history(cmd.history_path, run)
        return GrowthEvalResult(run=run)


class GrowthReportHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthReportCommand) -> GrowthReportResult:
        from jarvis_engine.growth_tracker import read_history, summarize_history

        rows = read_history(cmd.history_path)
        summary = summarize_history(rows, last=cmd.last)
        return GrowthReportResult(summary=summary)


class GrowthAuditHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthAuditCommand) -> GrowthAuditResult:
        from jarvis_engine.growth_tracker import audit_run, read_history

        rows = read_history(cmd.history_path)
        run = audit_run(rows, run_index=cmd.run_index)
        return GrowthAuditResult(run=run)


class IntelligenceDashboardHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: IntelligenceDashboardCommand) -> IntelligenceDashboardResult:
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard

        dashboard = build_intelligence_dashboard(self._root, last_runs=cmd.last_runs)
        return IntelligenceDashboardResult(dashboard=dashboard)
