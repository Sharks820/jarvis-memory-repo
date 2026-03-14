"""Command dataclasses for ops / growth / mission operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis_engine.commands.base import ResultBase

if TYPE_CHECKING:
    from jarvis_engine.ops.automation import ActionOutcome
    from jarvis_engine.learning.growth_tracker import EvalRun
    from jarvis_engine.ops.sync import SyncSummary


@dataclass(frozen=True)
class OpsBriefCommand:
    snapshot_path: Path
    output_path: Path | None = None


@dataclass
class OpsBriefResult(ResultBase):
    brief: str = ""
    saved_path: str = ""


@dataclass(frozen=True)
class OpsExportActionsCommand:
    snapshot_path: Path
    actions_path: Path


@dataclass
class OpsExportActionsResult(ResultBase):
    actions_path: str = ""
    action_count: int = 0


@dataclass(frozen=True)
class OpsSyncCommand:
    output_path: Path


@dataclass
class OpsSyncResult(ResultBase):
    summary: SyncSummary | None = None


@dataclass(frozen=True)
class OpsAutopilotCommand:
    snapshot_path: Path
    actions_path: Path
    execute: bool = False
    approve_privileged: bool = False
    auto_open_connectors: bool = False


@dataclass
class OpsAutopilotResult(ResultBase):
    pass


@dataclass(frozen=True)
class AutomationRunCommand:
    actions_path: Path
    approve_privileged: bool = False
    execute: bool = False


@dataclass
class AutomationRunResult(ResultBase):
    outcomes: list[ActionOutcome] = field(default_factory=list)


@dataclass(frozen=True)
class MissionCreateCommand:
    topic: str
    objective: str = ""
    sources: list[str] = field(default_factory=list)
    origin: str = "desktop-manual"
    delivery_method: str = "notification"


@dataclass
class MissionCreateResult(ResultBase):
    mission: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionStatusCommand:
    last: int = 10


@dataclass
class MissionStatusResult(ResultBase):
    missions: list[dict[str, Any]] = field(default_factory=list)
    total_count: int = 0


@dataclass(frozen=True)
class MissionRunCommand:
    mission_id: str
    max_results: int = 8
    max_pages: int = 12
    auto_ingest: bool = True


@dataclass
class MissionRunResult(ResultBase):
    report: dict[str, Any] = field(default_factory=dict)
    ingested_record_id: str = ""


@dataclass(frozen=True)
class GrowthEvalCommand:
    model: str
    endpoint: str = "http://127.0.0.1:11434"
    tasks_path: Path = Path("golden_tasks.json")
    history_path: Path = Path("capability_history.jsonl")
    num_predict: int = 256
    temperature: float = 0.0
    think: bool | None = None
    accept_thinking: bool = False
    timeout_s: int = 120


@dataclass
class GrowthEvalResult(ResultBase):
    run: EvalRun | None = None


@dataclass(frozen=True)
class GrowthReportCommand:
    history_path: Path = Path("capability_history.jsonl")
    last: int = 10


@dataclass
class GrowthReportResult(ResultBase):
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GrowthAuditCommand:
    history_path: Path = Path("capability_history.jsonl")
    run_index: int = -1


@dataclass
class GrowthAuditResult(ResultBase):
    run: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionCancelCommand:
    mission_id: str


@dataclass
class MissionCancelResult(ResultBase):
    cancelled: bool = False
    mission: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionPauseCommand:
    mission_id: str


@dataclass
class MissionPauseResult(ResultBase):
    mission: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionResumeCommand:
    mission_id: str


@dataclass
class MissionResumeResult(ResultBase):
    mission: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionRestartCommand:
    mission_id: str


@dataclass
class MissionRestartResult(ResultBase):
    mission: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionStepsCommand:
    mission_id: str


@dataclass
class MissionStepsResult(ResultBase):
    steps: list[dict[str, Any]] = field(default_factory=list)
    mission_id: str = ""


@dataclass(frozen=True)
class MissionActiveCommand:
    pass


@dataclass
class MissionActiveResult(ResultBase):
    missions: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0


@dataclass(frozen=True)
class MemoryHygieneCommand:
    dry_run: bool = False


@dataclass
class MemoryHygieneResult(ResultBase):
    scanned: int = 0
    classified: int = 0
    distribution: dict[str, int] = field(default_factory=dict)
    cleanup_candidates: int = 0
    archived: int = 0
    protected: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntelligenceDashboardCommand:
    last_runs: int = 20
    output_path: str = ""
    as_json: bool = False


@dataclass
class IntelligenceDashboardResult(ResultBase):
    dashboard: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiagnosticRunCommand:
    full_scan: bool = True
    categories: list[str] = field(default_factory=list)


@dataclass
class DiagnosticRunResult(ResultBase):
    issues: list[dict[str, Any]] = field(default_factory=list)
    healthy: bool = True
    score: int = 100
