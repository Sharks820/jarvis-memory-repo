"""Ops / growth / mission handler classes -- adapter shims."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from jarvis_engine.gateway.models import ModelGateway
    from jarvis_engine.memory.basic_ingest import IngestionPipeline
    from jarvis_engine.knowledge.graph import KnowledgeGraph
    from jarvis_engine.learning.feedback import ResponseFeedbackTracker
    from jarvis_engine.learning.preferences import PreferenceTracker
    from jarvis_engine.learning.usage_patterns import UsagePatternTracker
    from jarvis_engine.memory.engine import MemoryEngine
    from jarvis_engine.memory.ingest import EnrichedIngestPipeline
    from jarvis_engine.memory.store import MemoryStore

from jarvis_engine._constants import SUBSYSTEM_ERRORS, SUBSYSTEM_ERRORS_DB
from jarvis_engine._shared import check_path_within_root

from jarvis_engine.commands.ops_commands import (
    AutomationRunCommand,
    AutomationRunResult,
    DiagnosticRunCommand,
    DiagnosticRunResult,
    GrowthAuditCommand,
    GrowthAuditResult,
    GrowthEvalCommand,
    GrowthEvalResult,
    GrowthReportCommand,
    GrowthReportResult,
    IntelligenceDashboardCommand,
    IntelligenceDashboardResult,
    MissionActiveCommand,
    MissionActiveResult,
    MissionCancelCommand,
    MissionCancelResult,
    MissionCreateCommand,
    MissionCreateResult,
    MissionPauseCommand,
    MissionPauseResult,
    MissionRestartCommand,
    MissionRestartResult,
    MissionResumeCommand,
    MissionResumeResult,
    MissionRunCommand,
    MissionRunResult,
    MissionStatusCommand,
    MissionStatusResult,
    MemoryHygieneCommand,
    MemoryHygieneResult,
    MissionStepsCommand,
    MissionStepsResult,
    OpsAutopilotCommand,
    OpsAutopilotResult,
    OpsBriefCommand,
    OpsBriefResult,
    OpsExportActionsCommand,
    OpsExportActionsResult,
    OpsSyncCommand,
    OpsSyncResult,
)

logger = logging.getLogger(__name__)


class OpsBriefHandler:
    def __init__(self, root: Path, gateway: ModelGateway | None = None) -> None:
        self._root = root
        self._gateway = gateway

    def handle(self, cmd: OpsBriefCommand) -> OpsBriefResult:
        from jarvis_engine.ops.life_ops import (
            build_daily_brief,
            build_narrative_brief,
            load_snapshot,
        )

        try:
            check_path_within_root(cmd.snapshot_path, self._root, "snapshot_path")
        except ValueError as exc:
            logger.warning("OpsBrief snapshot path check failed: %s", exc)
            return OpsBriefResult(brief=str(exc))
        snapshot = load_snapshot(cmd.snapshot_path)
        brief = ""
        if self._gateway is not None:
            try:
                brief = build_narrative_brief(snapshot, gateway=self._gateway)
            except SUBSYSTEM_ERRORS as exc:
                logger.warning("Narrative brief generation failed in handler: %s", exc)
                brief = ""
        if not brief:
            brief = build_daily_brief(snapshot)
        saved = ""
        if cmd.output_path:
            try:
                check_path_within_root(cmd.output_path, self._root, "output_path")
            except ValueError as exc:
                logger.warning("OpsBrief output path check failed: %s", exc)
                return OpsBriefResult(brief=str(exc))
            cmd.output_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cmd.output_path.with_suffix(f"{cmd.output_path.suffix}.tmp.{os.getpid()}")
            tmp.write_text(brief, encoding="utf-8")
            os.replace(str(tmp), str(cmd.output_path))
            saved = str(cmd.output_path)
        return OpsBriefResult(brief=brief, saved_path=saved)


class OpsExportActionsHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: OpsExportActionsCommand) -> OpsExportActionsResult:
        from jarvis_engine.ops.life_ops import (
            export_actions_json,
            load_snapshot,
            suggest_actions,
        )

        try:
            check_path_within_root(cmd.snapshot_path, self._root, "snapshot_path")
            check_path_within_root(cmd.actions_path, self._root, "actions_path")
        except ValueError as exc:
            logger.warning("OpsExportActions path check failed: %s", exc)
            return OpsExportActionsResult(message=str(exc))
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
        from jarvis_engine.ops.sync import build_live_snapshot

        try:
            check_path_within_root(cmd.output_path, self._root, "output_path")
        except ValueError as exc:
            logger.warning("OpsSyncHandler path check failed: %s", exc)
            return OpsSyncResult(message=str(exc))
        summary = build_live_snapshot(self._root, cmd.output_path)
        return OpsSyncResult(summary=summary)


class OpsAutopilotHandler:
    """Delegates to existing cmd_* composition logic."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: OpsAutopilotCommand) -> OpsAutopilotResult:
        from jarvis_engine.ops.autopilot import run_ops_autopilot

        try:
            check_path_within_root(cmd.snapshot_path, self._root, "snapshot_path")
            check_path_within_root(cmd.actions_path, self._root, "actions_path")
        except ValueError as exc:
            logger.warning("OpsAutopilot path check failed: %s", exc)
            return OpsAutopilotResult(return_code=2, message=str(exc))
        rc = run_ops_autopilot(
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
        self._store: MemoryStore | None = None

    def _get_store(self) -> MemoryStore:
        """Lazily create and cache the MemoryStore."""
        if self._store is None:
            from jarvis_engine.memory.store import MemoryStore

            self._store = MemoryStore(self._root)
        return self._store

    def handle(self, cmd: AutomationRunCommand) -> AutomationRunResult:
        from jarvis_engine.ops.automation import AutomationExecutor, load_actions

        try:
            check_path_within_root(cmd.actions_path, self._root, "actions_path")
        except ValueError as exc:
            logger.warning("AutomationRun path check failed: %s", exc)
            return AutomationRunResult(message=str(exc))
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
        from jarvis_engine.learning.missions import create_learning_mission

        try:
            mission = create_learning_mission(
                self._root,
                topic=cmd.topic,
                objective=cmd.objective,
                sources=cmd.sources,
                origin=cmd.origin,
            )
        except ValueError as exc:
            logger.warning("Mission creation failed: %s", exc)
            return MissionCreateResult(return_code=2, message=str(exc))
        return MissionCreateResult(mission=cast(dict[str, Any], mission), return_code=0)


class MissionStatusHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionStatusCommand) -> MissionStatusResult:
        from jarvis_engine.learning.missions import load_missions

        missions = load_missions(self._root)
        return MissionStatusResult(
            missions=missions[-max(1, cmd.last) :],
            total_count=len(missions),
        )


class MissionCancelHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionCancelCommand) -> MissionCancelResult:
        from jarvis_engine.learning.missions import cancel_mission

        try:
            mission = cancel_mission(self._root, mission_id=cmd.mission_id)
        except ValueError as exc:
            logger.warning("Mission cancel failed: %s", exc)
            return MissionCancelResult(message=str(exc))
        return MissionCancelResult(cancelled=True, mission=mission)


class MissionRunHandler:
    def __init__(
        self, root: Path, enriched_pipeline: EnrichedIngestPipeline | None = None
    ) -> None:
        self._root = root
        self._enriched_pipeline = enriched_pipeline
        self._store: MemoryStore | None = None
        self._pipeline: IngestionPipeline | None = None

    def _get_ingest_pipeline(self) -> EnrichedIngestPipeline | IngestionPipeline:
        """Return enriched pipeline if available, else lazily create legacy pipeline."""
        if self._enriched_pipeline is not None:
            return self._enriched_pipeline
        if self._pipeline is None:
            from jarvis_engine.memory.basic_ingest import IngestionPipeline
            from jarvis_engine.memory.store import MemoryStore

            self._store = MemoryStore(self._root)
            self._pipeline = IngestionPipeline(self._store)
        return self._pipeline

    def handle(self, cmd: MissionRunCommand) -> MissionRunResult:
        from jarvis_engine.learning.missions import run_learning_mission

        try:
            report = run_learning_mission(
                self._root,
                mission_id=cmd.mission_id,
                max_search_results=cmd.max_results,
                max_pages=cmd.max_pages,
            )
        except ValueError as exc:
            logger.warning("Mission run failed: %s", exc)
            return MissionRunResult(return_code=2, message=str(exc))

        ingested_ids: list[str] = []
        verified = report.get("verified_findings", [])
        if cmd.auto_ingest and verified:
            pipeline = self._get_ingest_pipeline()
            # Ingest each finding individually for better KG fact extraction
            for finding in verified[:20]:
                statement = str(finding.get("statement", "")).strip()
                domains = ",".join(str(x) for x in finding.get("source_domains", []))
                if not statement:
                    continue
                content = f"{statement} [sources: {domains}]"
                try:
                    result = cast(Any, pipeline).ingest(
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
                except SUBSYSTEM_ERRORS_DB as exc:
                    logger.warning("Mission auto-ingest failed for finding: %s", exc)
        return MissionRunResult(
            report=cast(dict[str, Any], report),
            return_code=0,
            ingested_record_id=ingested_ids[0] if ingested_ids else "",
        )


class GrowthEvalHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthEvalCommand) -> GrowthEvalResult:
        from jarvis_engine.learning.growth_tracker import (
            append_history,
            load_golden_tasks,
            run_eval,
        )

        try:
            check_path_within_root(cmd.tasks_path, self._root, "tasks_path")
            check_path_within_root(cmd.history_path, self._root, "history_path")
        except ValueError as exc:
            logger.warning("GrowthEval path check failed: %s", exc)
            return GrowthEvalResult(message=str(exc))
        try:
            tasks = load_golden_tasks(cmd.tasks_path)
        except SUBSYSTEM_ERRORS as exc:
            logger.warning("Growth eval task loading failed: %s", exc)
            return GrowthEvalResult(message=str(exc))

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
        except SUBSYSTEM_ERRORS as exc:
            logger.warning("Growth eval execution failed: %s", exc)
            return GrowthEvalResult(message=str(exc))
        return GrowthEvalResult(run=run)


class GrowthReportHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthReportCommand) -> GrowthReportResult:
        from jarvis_engine.learning.growth_tracker import read_history, summarize_history

        try:
            check_path_within_root(cmd.history_path, self._root, "history_path")
        except ValueError as exc:
            logger.warning("GrowthReport path check failed: %s", exc)
            return GrowthReportResult(message=str(exc))
        rows = read_history(cmd.history_path)
        summary = summarize_history(rows, last=cmd.last)
        return GrowthReportResult(summary=cast(dict[str, Any], summary))


class GrowthAuditHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: GrowthAuditCommand) -> GrowthAuditResult:
        from jarvis_engine.learning.growth_tracker import audit_run, read_history

        try:
            check_path_within_root(cmd.history_path, self._root, "history_path")
        except ValueError as exc:
            logger.warning("GrowthAudit path check failed: %s", exc)
            return GrowthAuditResult(message=str(exc))
        rows = read_history(cmd.history_path)
        try:
            run = audit_run(rows, run_index=cmd.run_index)
        except (RuntimeError, IndexError) as exc:
            logger.warning("GrowthAudit run lookup failed: %s", exc)
            return GrowthAuditResult(message=str(exc))
        return GrowthAuditResult(run=cast(dict[str, Any], run))


class MissionPauseHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionPauseCommand) -> MissionPauseResult:
        from jarvis_engine.learning.missions import pause_mission

        try:
            mission = pause_mission(self._root, mission_id=cmd.mission_id)
        except ValueError as exc:
            logger.warning("Mission pause failed: %s", exc)
            return MissionPauseResult(return_code=2, message=str(exc))
        return MissionPauseResult(mission=mission, return_code=0)


class MissionResumeHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionResumeCommand) -> MissionResumeResult:
        from jarvis_engine.learning.missions import resume_mission

        try:
            mission = resume_mission(self._root, mission_id=cmd.mission_id)
        except ValueError as exc:
            logger.warning("Mission resume failed: %s", exc)
            return MissionResumeResult(return_code=2, message=str(exc))
        return MissionResumeResult(mission=mission, return_code=0)


class MissionRestartHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionRestartCommand) -> MissionRestartResult:
        from jarvis_engine.learning.missions import restart_mission

        try:
            mission = restart_mission(self._root, mission_id=cmd.mission_id)
        except ValueError as exc:
            logger.warning("Mission restart failed: %s", exc)
            return MissionRestartResult(return_code=2, message=str(exc))
        return MissionRestartResult(mission=mission, return_code=0)


class MissionStepsHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionStepsCommand) -> MissionStepsResult:
        from jarvis_engine.learning.missions import get_mission_steps

        steps = get_mission_steps(self._root, cmd.mission_id)
        return MissionStepsResult(steps=steps, mission_id=cmd.mission_id)


class MissionActiveHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MissionActiveCommand) -> MissionActiveResult:
        from jarvis_engine.learning.missions import get_active_missions

        missions = get_active_missions(self._root)
        return MissionActiveResult(missions=missions, count=len(missions))


class MemoryHygieneHandler:
    def __init__(self, root: Path, engine: MemoryEngine | None = None) -> None:
        self._root = root
        self._engine = engine

    def handle(self, cmd: MemoryHygieneCommand) -> MemoryHygieneResult:
        from jarvis_engine.memory.hygiene import MemoryHygieneEngine

        if self._engine is None:
            return MemoryHygieneResult(
                return_code=2, message="No memory engine available"
            )

        try:
            hygiene = MemoryHygieneEngine(self._root)
            report = hygiene.run_cleanup(self._engine, dry_run=cmd.dry_run)
            return MemoryHygieneResult(
                scanned=report.scanned,
                classified=report.classified,
                distribution=report.distribution,
                cleanup_candidates=report.cleanup_candidates,
                archived=report.archived,
                protected=report.protected,
                errors=report.errors,
                return_code=0,
            )
        except SUBSYSTEM_ERRORS as exc:
            logger.warning("Memory hygiene failed: %s", exc)
            return MemoryHygieneResult(return_code=2, message=str(exc))


class IntelligenceDashboardHandler:
    def __init__(
        self,
        root: Path,
        pref_tracker: PreferenceTracker | None = None,
        feedback_tracker: ResponseFeedbackTracker | None = None,
        usage_tracker: UsagePatternTracker | None = None,
        kg: KnowledgeGraph | None = None,
        engine: MemoryEngine | None = None,
    ) -> None:
        self._root = root
        self._pref_tracker = pref_tracker
        self._feedback_tracker = feedback_tracker
        self._usage_tracker = usage_tracker
        self._kg = kg
        self._engine = engine

    def handle(self, cmd: IntelligenceDashboardCommand) -> IntelligenceDashboardResult:
        from jarvis_engine.ops.intelligence_dashboard import build_intelligence_dashboard

        dashboard = build_intelligence_dashboard(
            self._root,
            last_runs=cmd.last_runs,
            pref_tracker=self._pref_tracker,
            feedback_tracker=self._feedback_tracker,
            usage_tracker=self._usage_tracker,
            kg=self._kg,
            engine=self._engine,
        )
        return IntelligenceDashboardResult(dashboard=cast(dict[str, Any], dashboard))


class DiagnosticRunHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: DiagnosticRunCommand) -> DiagnosticRunResult:
        from jarvis_engine.ops.self_diagnosis import DiagnosticEngine

        try:
            diag = DiagnosticEngine(self._root)
            if cmd.full_scan:
                issues = diag.run_full_scan()
            else:
                issues = diag.run_quick_scan()
            score = diag.health_score(issues)
            return DiagnosticRunResult(
                issues=[i.to_dict() for i in issues],
                healthy=score >= 70,
                score=score,
                return_code=0,
            )
        except SUBSYSTEM_ERRORS as exc:
            logger.warning("Diagnostic run failed: %s", exc)
            return DiagnosticRunResult(return_code=2, issues=[], healthy=False, score=0)
