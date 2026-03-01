"""Application bootstrap: creates and wires the Command Bus (DI composition root)."""

from __future__ import annotations

import logging
import os
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
    PersonaComposeCommand,
    VoiceEnrollCommand,
    VoiceListCommand,
    VoiceListenCommand,
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
    QueryCommand,
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
from jarvis_engine.commands.knowledge_commands import (
    ContradictionListCommand,
    ContradictionResolveCommand,
    FactLockCommand,
    KnowledgeRegressionCommand,
    KnowledgeStatusCommand,
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
    PersonaComposeHandler,
    VoiceEnrollHandler,
    VoiceListHandler,
    VoiceListenHandler,
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
    QueryHandler,
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
from jarvis_engine.handlers.knowledge_handlers import (
    ContradictionListHandler,
    ContradictionResolveHandler,
    FactLockHandler,
    KnowledgeRegressionHandler,
    KnowledgeStatusHandler,
)
from jarvis_engine.commands.harvest_commands import (
    HarvestBudgetCommand,
    HarvestTopicCommand,
    IngestSessionCommand,
)
from jarvis_engine.handlers.harvest_handlers import (
    HarvestBudgetHandler,
    HarvestHandler,
    IngestSessionHandler,
)
from jarvis_engine.commands.proactive_commands import (
    CostReductionCommand,
    ProactiveCheckCommand,
    SelfTestCommand,
    WakeWordStartCommand,
)
from jarvis_engine.handlers.proactive_handlers import (
    CostReductionHandler,
    ProactiveCheckHandler,
    SelfTestHandler,
    WakeWordStartHandler,
)
from jarvis_engine.commands.learning_commands import (
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
    LearnInteractionCommand,
)
from jarvis_engine.handlers.learning_handlers import (
    CrossBranchQueryHandler,
    FlagExpiredFactsHandler,
    LearnInteractionHandler,
)
from jarvis_engine.commands.sync_commands import (
    SyncPullCommand,
    SyncPushCommand,
    SyncStatusCommand,
)
from jarvis_engine.handlers.sync_handlers import (
    SyncPullHandler,
    SyncPushHandler,
    SyncStatusHandler,
)


def create_app(root: Path) -> CommandBus:
    """Build and wire the full Command Bus.  This is the DI composition root.

    If a SQLite memory database exists at .planning/brain/jarvis_memory.db,
    memory handlers use MemoryEngine for queries and ingestion. Otherwise,
    they fall back to the adapter shim path (JSONL-based).
    """
    bus = CommandBus()

    # -- Ensure required directories exist --
    brain_dir = root / ".planning" / "brain"
    brain_dir.mkdir(parents=True, exist_ok=True)
    (root / ".planning" / "runtime" / "pids").mkdir(parents=True, exist_ok=True)
    (root / ".planning" / "logs").mkdir(parents=True, exist_ok=True)

    # -- Check for SQLite memory engine --
    db_path = brain_dir / "jarvis_memory.db"
    engine = None
    embed_service = None
    pipeline = None
    kg = None

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
            # Run temporal metadata migration (idempotent)
            try:
                from jarvis_engine.learning.temporal import migrate_temporal_metadata
                migrate_temporal_metadata(engine._db, engine._write_lock)
            except Exception as exc_tm:
                logger.warning("Temporal metadata migration skipped: %s", exc_tm)
            pipeline = EnrichedIngestPipeline(
                engine, embed_service, classifier, knowledge_graph=kg,
            )
        except Exception as exc:
            # Graceful degradation: if SQLite engine fails, fall back to adapter shims
            logger.warning("Failed to initialize MemoryEngine, falling back to adapter shims: %s", exc)
            engine = None
            embed_service = None
            pipeline = None

    # -- Intelligence Gateway --
    gateway = None
    intent_classifier = None
    cost_tracker = None

    try:
        from jarvis_engine.gateway.costs import CostTracker
        from jarvis_engine.gateway.models import ModelGateway
        from jarvis_engine.gateway.classifier import IntentClassifier

        if db_path.exists():
            cost_tracker = CostTracker(db_path)
        gateway = ModelGateway(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            cost_tracker=cost_tracker,
            groq_api_key=os.environ.get("GROQ_API_KEY"),
            mistral_api_key=os.environ.get("MISTRAL_API_KEY"),
            zai_api_key=os.environ.get("ZAI_API_KEY"),
            audit_path=root / ".planning" / "runtime" / "gateway_audit.jsonl",
        )
        if embed_service is not None:
            intent_classifier = IntentClassifier(embed_service)
    except Exception as exc:
        logger.warning("Failed to initialize Intelligence Gateway, continuing without: %s", exc)
        gateway = None
        intent_classifier = None
        cost_tracker = None

    # Wire gateway into ingest pipeline for LLM fact extraction
    if pipeline is not None and gateway is not None:
        pipeline._gateway = gateway

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
    bus.register(VoiceListenCommand, VoiceListenHandler(root, gateway=gateway).handle)

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
    bus.register(RouteCommand, RouteHandler(root, classifier=intent_classifier).handle)
    if gateway is not None:
        bus.register(QueryCommand, QueryHandler(gateway, classifier=intent_classifier).handle)
        bus.register(PersonaComposeCommand, PersonaComposeHandler(root, gateway=gateway).handle)
    else:
        from jarvis_engine.commands.task_commands import QueryResult
        from jarvis_engine.commands.voice_commands import PersonaComposeResult

        def _gateway_unavailable_handler(cmd: QueryCommand) -> QueryResult:
            return QueryResult(text="Gateway not initialized", return_code=2)

        def _persona_gateway_unavailable(cmd: PersonaComposeCommand) -> PersonaComposeResult:
            return PersonaComposeResult(message="error: gateway not available")

        bus.register(QueryCommand, _gateway_unavailable_handler)
        bus.register(PersonaComposeCommand, _persona_gateway_unavailable)
    bus.register(WebResearchCommand, WebResearchHandler(root).handle)

    # -- Ops --
    bus.register(OpsBriefCommand, OpsBriefHandler(root, gateway=gateway).handle)
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

    # -- Knowledge --
    bus.register(KnowledgeStatusCommand, KnowledgeStatusHandler(root, kg=kg).handle)
    bus.register(ContradictionListCommand, ContradictionListHandler(root, kg=kg).handle)
    bus.register(ContradictionResolveCommand, ContradictionResolveHandler(root, kg=kg).handle)
    bus.register(FactLockCommand, FactLockHandler(root, kg=kg).handle)
    bus.register(KnowledgeRegressionCommand, KnowledgeRegressionHandler(root, kg=kg).handle)

    # -- Learning --
    try:
        if engine is None:
            raise RuntimeError("MemoryEngine not available — skipping Learning subsystem")

        from jarvis_engine.learning.engine import ConversationLearningEngine
        from jarvis_engine.learning.feedback import ResponseFeedbackTracker
        from jarvis_engine.learning.preferences import PreferenceTracker
        from jarvis_engine.learning.usage_patterns import UsagePatternTracker

        pref_tracker = PreferenceTracker(db=engine._db, write_lock=engine._write_lock, db_lock=engine._db_lock)
        feedback_tracker = ResponseFeedbackTracker(db=engine._db, write_lock=engine._write_lock, db_lock=engine._db_lock)
        usage_tracker = UsagePatternTracker(db=engine._db, write_lock=engine._write_lock, db_lock=engine._db_lock)
        learning_engine = ConversationLearningEngine(
            pipeline=pipeline, kg=kg, preference_tracker=pref_tracker,
            feedback_tracker=feedback_tracker, usage_tracker=usage_tracker,
        )

        bus.register(
            LearnInteractionCommand,
            LearnInteractionHandler(root, learning_engine=learning_engine).handle,
        )
        bus.register(
            CrossBranchQueryCommand,
            CrossBranchQueryHandler(
                root, engine=engine, kg=kg, embed_service=embed_service
            ).handle,
        )
        bus.register(
            FlagExpiredFactsCommand,
            FlagExpiredFactsHandler(root, kg=kg).handle,
        )
    except Exception as exc:
        logger.warning("Failed to initialize Learning subsystem, continuing without: %s", exc)
        bus.register(
            LearnInteractionCommand,
            LearnInteractionHandler(root).handle,
        )
        bus.register(
            CrossBranchQueryCommand,
            CrossBranchQueryHandler(root).handle,
        )
        bus.register(
            FlagExpiredFactsCommand,
            FlagExpiredFactsHandler(root).handle,
        )

    # -- Sync --
    try:
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        sync_engine = None
        sync_transport = None

        if engine is not None:
            install_changelog_triggers(engine._db, device_id="desktop")
            sync_engine = SyncEngine(engine._db, engine._write_lock, device_id="desktop")

            signing_key = os.environ.get("JARVIS_SIGNING_KEY", "")
            if signing_key:
                # Lazy import: cryptography may crash with pyo3 ABI mismatch on
                # some systems.  Deferring the import keeps the rest of the bus
                # functional even when the crypto library is broken.
                from jarvis_engine.sync.transport import SyncTransport
                salt_path = root / ".planning" / "brain" / "sync_salt.bin"
                sync_transport = SyncTransport(signing_key, salt_path)

        bus.register(
            SyncPullCommand,
            SyncPullHandler(root, sync_engine=sync_engine, transport=sync_transport).handle,
        )
        bus.register(
            SyncPushCommand,
            SyncPushHandler(root, sync_engine=sync_engine, transport=sync_transport).handle,
        )
        bus.register(
            SyncStatusCommand,
            SyncStatusHandler(root, sync_engine=sync_engine).handle,
        )
    except BaseException as exc:
        # NOTE: BaseException needed because cryptography's pyo3 bindings can
        # raise PanicException (a BaseException subclass) on ABI mismatch.
        # Always re-raise signal-level exceptions so Ctrl+C / sys.exit() work.
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        logger.warning("Failed to initialize Sync subsystem, continuing without: %s", exc)
        bus.register(SyncPullCommand, SyncPullHandler(root).handle)
        bus.register(SyncPushCommand, SyncPushHandler(root).handle)
        bus.register(SyncStatusCommand, SyncStatusHandler(root).handle)

    # -- Harvesting --
    try:
        from jarvis_engine.harvesting.budget import BudgetManager
        from jarvis_engine.harvesting.providers import (
            GeminiProvider,
            KimiNvidiaProvider,
            KimiProvider,
            MiniMaxProvider,
        )
        from jarvis_engine.harvesting.harvester import KnowledgeHarvester

        budget_manager = None
        if db_path.exists():
            budget_manager = BudgetManager(db_path)

        all_providers = [MiniMaxProvider(), KimiProvider(), KimiNvidiaProvider(), GeminiProvider()]
        available_providers = [p for p in all_providers if p.is_available]

        harvester = KnowledgeHarvester(
            providers=available_providers,
            pipeline=pipeline,
            cost_tracker=cost_tracker,
            budget_manager=budget_manager,
        )

        bus.register(HarvestTopicCommand, HarvestHandler(harvester=harvester).handle)
        bus.register(IngestSessionCommand, IngestSessionHandler(pipeline=pipeline).handle)
        bus.register(HarvestBudgetCommand, HarvestBudgetHandler(budget_manager=budget_manager).handle)
    except Exception as exc:
        logger.warning("Failed to initialize Harvesting subsystem, continuing without: %s", exc)
        bus.register(HarvestTopicCommand, HarvestHandler().handle)
        bus.register(IngestSessionCommand, IngestSessionHandler().handle)
        bus.register(HarvestBudgetCommand, HarvestBudgetHandler().handle)

    # -- Proactive Intelligence --
    try:
        from jarvis_engine.proactive import (
            DEFAULT_TRIGGER_RULES,
            Notifier,
            ProactiveEngine,
        )

        notifier = Notifier()
        proactive_engine = ProactiveEngine(rules=DEFAULT_TRIGGER_RULES, notifier=notifier)
        bus.register(
            ProactiveCheckCommand,
            ProactiveCheckHandler(root, proactive_engine=proactive_engine).handle,
        )
    except Exception as exc:
        logger.warning("Failed to initialize Proactive subsystem, continuing without: %s", exc)
        bus.register(ProactiveCheckCommand, ProactiveCheckHandler(root).handle)

    bus.register(WakeWordStartCommand, WakeWordStartHandler(root, gateway=gateway).handle)

    # -- Cost Reduction & Self-Testing --
    bus.register(
        CostReductionCommand,
        CostReductionHandler(root, cost_tracker=cost_tracker).handle,
    )
    bus.register(
        SelfTestCommand,
        SelfTestHandler(root, engine=engine, embed_service=embed_service).handle,
    )

    # Expose subsystem references for daemon self-test and smart context access
    bus._engine = engine  # type: ignore[attr-defined]
    bus._embed_service = embed_service  # type: ignore[attr-defined]
    bus._intent_classifier = intent_classifier  # type: ignore[attr-defined]
    bus._kg = kg  # type: ignore[attr-defined]
    bus._gateway = gateway  # type: ignore[attr-defined]

    return bus
