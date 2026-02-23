"""Application bootstrap: creates and wires the Command Bus (DI composition root)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.memory_commands import (
    BrainCompactCommand,
    BrainContextCommand,
    BrainRegressionCommand,
    BrainStatusCommand,
    IngestCommand,
    MemoryMaintenanceCommand,
    MemorySnapshotCommand,
)
from jarvis_engine.commands.voice_commands import (
    VoiceEnrollCommand,
    VoiceListCommand,
    VoiceRunCommand,
    VoiceSayCommand,
    VoiceVerifyCommand,
)
from jarvis_engine.commands.system_commands import (
    DaemonRunCommand,
    DesktopWidgetCommand,
    GamingModeCommand,
    LogCommand,
    MigrateMemoryCommand,
    MobileDesktopSyncCommand,
    OpenWebCommand,
    SelfHealCommand,
    ServeMobileCommand,
    StatusCommand,
    WeatherCommand,
)
from jarvis_engine.commands.task_commands import (
    RouteCommand,
    RunTaskCommand,
    WebResearchCommand,
)
from jarvis_engine.commands.ops_commands import (
    AutomationRunCommand,
    GrowthAuditCommand,
    GrowthEvalCommand,
    GrowthReportCommand,
    IntelligenceDashboardCommand,
    MissionCreateCommand,
    MissionRunCommand,
    MissionStatusCommand,
    OpsAutopilotCommand,
    OpsBriefCommand,
    OpsExportActionsCommand,
    OpsSyncCommand,
)
from jarvis_engine.commands.security_commands import (
    ConnectBootstrapCommand,
    ConnectGrantCommand,
    ConnectStatusCommand,
    OwnerGuardCommand,
    PersonaConfigCommand,
    PhoneActionCommand,
    PhoneSpamGuardCommand,
    RuntimeControlCommand,
)

from jarvis_engine.handlers.memory_handlers import (
    BrainCompactHandler,
    BrainContextHandler,
    BrainRegressionHandler,
    BrainStatusHandler,
    IngestHandler,
    MemoryMaintenanceHandler,
    MemorySnapshotHandler,
)
from jarvis_engine.handlers.voice_handlers import (
    VoiceEnrollHandler,
    VoiceListHandler,
    VoiceRunHandler,
    VoiceSayHandler,
    VoiceVerifyHandler,
)
from jarvis_engine.handlers.system_handlers import (
    DaemonRunHandler,
    DesktopWidgetHandler,
    GamingModeHandler,
    LogHandler,
    MigrateMemoryHandler,
    MobileDesktopSyncHandler,
    OpenWebHandler,
    SelfHealHandler,
    ServeMobileHandler,
    StatusHandler,
    WeatherHandler,
)
from jarvis_engine.handlers.task_handlers import (
    RouteHandler,
    RunTaskHandler,
    WebResearchHandler,
)
from jarvis_engine.handlers.ops_handlers import (
    AutomationRunHandler,
    GrowthAuditHandler,
    GrowthEvalHandler,
    GrowthReportHandler,
    IntelligenceDashboardHandler,
    MissionCreateHandler,
    MissionRunHandler,
    MissionStatusHandler,
    OpsAutopilotHandler,
    OpsBriefHandler,
    OpsExportActionsHandler,
    OpsSyncHandler,
)
from jarvis_engine.handlers.security_handlers import (
    ConnectBootstrapHandler,
    ConnectGrantHandler,
    ConnectStatusHandler,
    OwnerGuardHandler,
    PersonaConfigHandler,
    PhoneActionHandler,
    PhoneSpamGuardHandler,
    RuntimeControlHandler,
)


def create_app(root: Path) -> CommandBus:
    """Build and wire the full Command Bus.  This is the DI composition root.

    If a SQLite memory database exists at .planning/brain/jarvis_memory.db,
    memory handlers use MemoryEngine for queries and ingestion. Otherwise,
    they fall back to the adapter shim path (JSONL-based).
    """
    bus = CommandBus()

    # -- Check for SQLite memory engine --
    db_path = root / ".planning" / "brain" / "jarvis_memory.db"
    engine = None
    embed_service = None
    pipeline = None

    if db_path.exists():
        try:
            from jarvis_engine.memory.classify import BranchClassifier
            from jarvis_engine.memory.embeddings import EmbeddingService
            from jarvis_engine.memory.engine import MemoryEngine
            from jarvis_engine.memory.ingest import EnrichedIngestPipeline
            from jarvis_engine.knowledge.graph import KnowledgeGraph

            embed_service = EmbeddingService()
            engine = MemoryEngine(db_path, embed_service=embed_service)
            classifier = BranchClassifier(embed_service)
            kg = KnowledgeGraph(engine)
            pipeline = EnrichedIngestPipeline(
                engine, embed_service, classifier, knowledge_graph=kg
            )
        except Exception as exc:
            # Graceful degradation: if SQLite engine fails, fall back to adapter shims
            logger.warning("Failed to initialize MemoryEngine, falling back to adapter shims: %s", exc)
            engine = None
            embed_service = None
            pipeline = None

    # -- Memory (dual-path: MemoryEngine or adapter shim) --
    bus.register(BrainStatusCommand, BrainStatusHandler(root, engine=engine).handle)
    bus.register(BrainContextCommand, BrainContextHandler(root, engine=engine, embed_service=embed_service).handle)
    bus.register(BrainCompactCommand, BrainCompactHandler(root).handle)
    bus.register(BrainRegressionCommand, BrainRegressionHandler(root).handle)
    bus.register(IngestCommand, IngestHandler(root, pipeline=pipeline).handle)
    bus.register(MemorySnapshotCommand, MemorySnapshotHandler(root).handle)
    bus.register(MemoryMaintenanceCommand, MemoryMaintenanceHandler(root).handle)

    # -- Voice --
    bus.register(VoiceListCommand, VoiceListHandler(root).handle)
    bus.register(VoiceSayCommand, VoiceSayHandler(root).handle)
    bus.register(VoiceEnrollCommand, VoiceEnrollHandler(root).handle)
    bus.register(VoiceVerifyCommand, VoiceVerifyHandler(root).handle)
    bus.register(VoiceRunCommand, VoiceRunHandler(root).handle)

    # -- System --
    bus.register(StatusCommand, StatusHandler(root).handle)
    bus.register(LogCommand, LogHandler(root).handle)
    bus.register(ServeMobileCommand, ServeMobileHandler(root).handle)
    bus.register(DaemonRunCommand, DaemonRunHandler(root).handle)
    bus.register(MobileDesktopSyncCommand, MobileDesktopSyncHandler(root).handle)
    bus.register(SelfHealCommand, SelfHealHandler(root).handle)
    bus.register(DesktopWidgetCommand, DesktopWidgetHandler(root).handle)
    bus.register(GamingModeCommand, GamingModeHandler(root).handle)
    bus.register(OpenWebCommand, OpenWebHandler(root).handle)
    bus.register(WeatherCommand, WeatherHandler(root).handle)
    bus.register(MigrateMemoryCommand, MigrateMemoryHandler(root).handle)

    # -- Task --
    bus.register(RunTaskCommand, RunTaskHandler(root).handle)
    bus.register(RouteCommand, RouteHandler(root).handle)
    bus.register(WebResearchCommand, WebResearchHandler(root).handle)

    # -- Ops --
    bus.register(OpsBriefCommand, OpsBriefHandler(root).handle)
    bus.register(OpsExportActionsCommand, OpsExportActionsHandler(root).handle)
    bus.register(OpsSyncCommand, OpsSyncHandler(root).handle)
    bus.register(OpsAutopilotCommand, OpsAutopilotHandler(root).handle)
    bus.register(AutomationRunCommand, AutomationRunHandler(root).handle)
    bus.register(MissionCreateCommand, MissionCreateHandler(root).handle)
    bus.register(MissionStatusCommand, MissionStatusHandler(root).handle)
    bus.register(MissionRunCommand, MissionRunHandler(root).handle)
    bus.register(GrowthEvalCommand, GrowthEvalHandler(root).handle)
    bus.register(GrowthReportCommand, GrowthReportHandler(root).handle)
    bus.register(GrowthAuditCommand, GrowthAuditHandler(root).handle)
    bus.register(IntelligenceDashboardCommand, IntelligenceDashboardHandler(root).handle)

    # -- Security --
    bus.register(RuntimeControlCommand, RuntimeControlHandler(root).handle)
    bus.register(OwnerGuardCommand, OwnerGuardHandler(root).handle)
    bus.register(ConnectStatusCommand, ConnectStatusHandler(root).handle)
    bus.register(ConnectGrantCommand, ConnectGrantHandler(root).handle)
    bus.register(ConnectBootstrapCommand, ConnectBootstrapHandler(root).handle)
    bus.register(PhoneActionCommand, PhoneActionHandler(root).handle)
    bus.register(PhoneSpamGuardCommand, PhoneSpamGuardHandler(root).handle)
    bus.register(PersonaConfigCommand, PersonaConfigHandler(root).handle)

    return bus
