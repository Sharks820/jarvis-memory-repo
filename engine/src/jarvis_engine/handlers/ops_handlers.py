"""Ops / growth / mission handler classes -- adapter shims."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from jarvis_engine._shared import check_path_within_root as _check_path_within_root

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
    MissionCancelCommand,
    MissionCancelResult,
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

        try:
            _check_path_within_root(cmd.snapshot_path, self._root, "snapshot_path")
        except ValueError as exc:
            return OpsBriefResult(brief=str(exc))
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
            try:
                _check_path_within_root(cmd.output_path, self._root, "output_path")
            except ValueError as exc:
                return OpsBriefResult(brief=str(exc))
            cmd.output_path.parent.mkdir(parents=True, exist_ok=True)
            cmd.output_path.write_text(brief, encoding="utf-8")
            saved = str(cmd.output_path)
        return OpsBriefResult(brief=brief, saved_path=saved)


class OpsExportActionsHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: OpsExportActionsCommand) -> OpsExportActionsResult:
        from jarvis_engine.life_ops import export_actions_json, load_snapshot, suggest_actions

        try:
            _check_path_within_root(cmd.snapshot_path, self._root, "snapshot_path")
            _check_path_within_root(cmd.actions_path, self._root, "actions_path")
        except ValueError as exc:
            logger.warning("OpsExportActions path check failed: %s", exc)
            return OpsExportActionsResult()
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

        try:
            _check_path_within_root(cmd.output_path, self._root, "output_path")
        except ValueError as exc:
            logger.warning("OpsSyncHandler path check failed: %s", exc)
            return OpsSyncResult()
        summary = build_live_snapshot(self._root, cmd.output_path)
        return OpsSyncResult(summary=summary)


class OpsAutopilotHandler:
    """Delegates to existing cmd_* composition logic."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: OpsAutopilotCommand) -> OpsAutopilotResult:
        from jarvis_engine import main as _main_mod

        try:
            _check_path_within_root(cmd.snapshot_path, self._root, "snapshot_path")
            _check_path_within_root(cmd.actions_path, self._root, "actions_path")
        except ValueError:
            return OpsAutopilotResult(return_code=2)
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
        self._store: Any = None

    def _get_store(self) -> Any:
        """Lazily create and cache the MemoryStore."""
        if self._store is None:
            from jarvis_engine.memory_store import MemoryStore

            self._store = MemoryStore(self._root)
        return self._store

    def handle(self, cmd: AutomationRunCommand) -> AutomationRunResult:
        from jarvis_engine.automation import AutomationExecutor, load_actions

        try:
            _check_path_within_root(cmd.actions_path, self._root, "actions_path")
        except ValueError:
            return AutomationRunResult()
        store = self._get_store()
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


class MissionCancelHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionCancelCommand) -> MissionCancelResult:
        from jarvis_engine.learning_missions import cancel_mission

        try:
            mission = cancel_mission(self._root, mission_id=cmd.mission_id)
        except ValueError as exc:
            return MissionCancelResult(error=str(exc))
        return MissionCancelResult(cancelled=True, mission=mission)


class MissionRunHandler:
    def __init__(self, root: Path, enriched_pipeline: Any = None) -> None:
        self._root = root
        self._enriched_pipeline = enriched_pipeline
        self._store: Any = None
        self._pipeline: Any = None

    def _get_ingest_pipeline(self) -> Any:
        """Return enriched pipeline if available, else lazily create legacy pipeline."""
        if self._enriched_pipeline is not None:
            return self._enriched_pipeline
        if self._pipeline is None:
            from jarvis_engine.ingest import IngestionPipeline
            from jarvis_engine.memory_store import MemoryStore

            self._store = MemoryStore(self._root)
            self._pipeline = IngestionPipeline(self._store)
        return self._pipeline

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

        ingested_ids: list[str] = []
        verified = report.get("verified_findings", [])
        if cmd.auto_ingest and isinstance(verified, list) and verified:
            pipeline = self._get_ingest_pipeline()
            # Ingest each finding individually for better KG fact extraction
            for finding in verified[:20]:
                if not isinstance(finding, dict):
                    continue
                statement = str(finding.get("statement", "")).strip()
                domains = ",".join(str(x) for x in finding.get("source_domains", []))
                if not statement:
                    continue
                content = f"{statement} [sources: {domains}]"
                try:
                    result = pipeline.ingest(
                        source="mission",
                        kind="semantic",
                        task_id=f"mission-{report.get('mission_id', '')}",
                        content=content[:4000],
                        tags=["mission", "verified"],
                    )
                    # EnrichedIngestPipeline returns list of IDs; legacy returns record obj
                    if isinstance(result, list):
                        ingested_ids.extend(result)
                    elif hasattr(result, "record_id"):
                        ingested_ids.append(result.record_id)
                except Exception as exc:
                    logger.warning("Mission auto-ingest failed for finding: %s", exc)
        return MissionRunResult(
            report=report, return_code=0,
            ingested_record_id=ingested_ids[0] if ingested_ids else "",
        )


class GrowthEvalHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthEvalCommand) -> GrowthEvalResult:
        from jarvis_engine.growth_tracker import append_history, load_golden_tasks, run_eval

        try:
            _check_path_within_root(cmd.tasks_path, self._root, "tasks_path")
            _check_path_within_root(cmd.history_path, self._root, "history_path")
        except ValueError:
            return GrowthEvalResult()
        try:
            tasks = load_golden_tasks(cmd.tasks_path)
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Growth eval task loading failed: %s", exc)
            return GrowthEvalResult()

        try:
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
        except (RuntimeError, ConnectionError, TimeoutError, OSError) as exc:
            logger.warning("Growth eval execution failed: %s", exc)
            return GrowthEvalResult()
        return GrowthEvalResult(run=run)


class GrowthReportHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthReportCommand) -> GrowthReportResult:
        from jarvis_engine.growth_tracker import read_history, summarize_history

        try:
            _check_path_within_root(cmd.history_path, self._root, "history_path")
        except ValueError:
            return GrowthReportResult()
        rows = read_history(cmd.history_path)
        summary = summarize_history(rows, last=cmd.last)
        return GrowthReportResult(summary=summary)


class GrowthAuditHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthAuditCommand) -> GrowthAuditResult:
        from jarvis_engine.growth_tracker import audit_run, read_history

        try:
            _check_path_within_root(cmd.history_path, self._root, "history_path")
        except ValueError:
            return GrowthAuditResult()
        rows = read_history(cmd.history_path)
        try:
            run = audit_run(rows, run_index=cmd.run_index)
        except (RuntimeError, IndexError):
            return GrowthAuditResult()
        return GrowthAuditResult(run=run)


class IntelligenceDashboardHandler:
    def __init__(
        self,
        root: Path,
        pref_tracker: Any = None,
        feedback_tracker: Any = None,
        usage_tracker: Any = None,
        kg: Any = None,
        engine: Any = None,
    ) -> None:
        self._root = root
        self._pref_tracker = pref_tracker
        self._feedback_tracker = feedback_tracker
        self._usage_tracker = usage_tracker
        self._kg = kg
        self._engine = engine

    def handle(self, cmd: IntelligenceDashboardCommand) -> IntelligenceDashboardResult:
        from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard

        dashboard = build_intelligence_dashboard(
            self._root,
            last_runs=cmd.last_runs,
            pref_tracker=self._pref_tracker,
            feedback_tracker=self._feedback_tracker,
            usage_tracker=self._usage_tracker,
            kg=self._kg,
            engine=self._engine,
        )
        return IntelligenceDashboardResult(dashboard=dashboard)
