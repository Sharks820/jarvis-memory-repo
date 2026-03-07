"""Application bootstrap: creates and wires the Command Bus (DI composition root)."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, cast

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
    MissionCancelCommand,
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
    MissionCancelHandler,
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
    HarvestTopicHandler,
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
    ConsolidateMemoryCommand,
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
    LearnInteractionCommand,
)
from jarvis_engine.handlers.learning_handlers import (
    ConsolidateMemoryHandler,
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


def _register_with_fallback(
    bus: CommandBus,
    command_type: type,
    handler_factory: Callable[[], Any],
    fallback_factory: Callable[[], Any],
) -> None:
    """Register a command handler, falling back to a simpler handler on failure.

    Calls *handler_factory* to produce the handler callable.  If it raises,
    logs a warning and uses *fallback_factory* instead.
    """
    try:
        handler = handler_factory()
    except Exception as exc:
        logger.warning(
            "Handler factory for %s failed, using fallback: %s",
            command_type.__name__, exc,
        )
        handler = fallback_factory()
    bus.register(command_type, handler)


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
    from jarvis_engine._constants import GATEWAY_AUDIT_LOG, runtime_dir as _runtime_dir
    (_runtime_dir(root) / "pids").mkdir(parents=True, exist_ok=True)
    (root / ".planning" / "logs").mkdir(parents=True, exist_ok=True)

    # -- Check for SQLite memory engine --
    from jarvis_engine._constants import memory_db_path as _memory_db_path
    db_path = _memory_db_path(root)
    engine = None
    embed_service = None
    pipeline = None
    kg = None

    try:
        from jarvis_engine.memory.classify import BranchClassifier
        from jarvis_engine.memory.embeddings import EmbeddingService
        from jarvis_engine.memory.engine import MemoryEngine
        from jarvis_engine.memory.ingest import EnrichedIngestPipeline
        from jarvis_engine.knowledge.graph import KnowledgeGraph

        embed_service = EmbeddingService()
        engine = MemoryEngine(db_path, embed_service=embed_service)
        classifier = BranchClassifier(embed_service)
        kg = KnowledgeGraph(engine, embed_service=embed_service)
        # Run temporal metadata migration (idempotent)
        try:
            from jarvis_engine.learning.temporal import migrate_temporal_metadata
            migrate_temporal_metadata(engine.db, engine.write_lock)
        except (ImportError, sqlite3.Error, OSError) as exc_tm:
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
        kg = None

    # -- Intelligence Gateway --
    gateway = None
    intent_classifier = None
    cost_tracker = None

    try:
        from jarvis_engine.gateway.costs import CostTracker
        from jarvis_engine.gateway.models import ModelGateway

        cost_tracker = CostTracker(db_path)
        gateway = ModelGateway(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            cost_tracker=cost_tracker,
            groq_api_key=os.environ.get("GROQ_API_KEY"),
            mistral_api_key=os.environ.get("MISTRAL_API_KEY"),
            zai_api_key=os.environ.get("ZAI_API_KEY"),
            audit_path=_runtime_dir(root) / GATEWAY_AUDIT_LOG,
        )
        # Keep classifier lazy to avoid heavy startup latency in request paths.
        # It will be instantiated on-demand where needed (e.g. voice fallback).
        intent_classifier = None
    except Exception as exc:
        logger.warning("Failed to initialize Intelligence Gateway, continuing without: %s", exc)
        gateway = None
        intent_classifier = None
        cost_tracker = None

    # Wire gateway into ingest pipeline for LLM fact extraction
    # (pipeline is constructed before gateway is available; gateway is a
    # documented constructor parameter so this late-binding is intentional)
    if pipeline is not None and gateway is not None:
        pipeline.set_gateway(gateway)

    # -- Memory (dual-path: MemoryEngine or adapter shim) --
    bus.register(BrainStatusCommand, BrainStatusHandler(root, engine=engine, kg=kg).handle)
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
    bus.register(RouteCommand, RouteHandler(root, classifier=intent_classifier, gateway=gateway).handle)
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
    bus.register(MissionCancelCommand, MissionCancelHandler(root).handle)
    bus.register(MissionStatusCommand, MissionStatusHandler(root).handle)
    bus.register(MissionRunCommand, MissionRunHandler(root, enriched_pipeline=pipeline).handle)
    bus.register(GrowthEvalCommand, GrowthEvalHandler(root).handle)
    bus.register(GrowthReportCommand, GrowthReportHandler(root).handle)
    bus.register(GrowthAuditCommand, GrowthAuditHandler(root).handle)
    # IntelligenceDashboardHandler registered after learning subsystem init (LEARN-07/08)

    # -- Security --
    bus.register(RuntimeControlCommand, RuntimeControlHandler(root).handle)
    bus.register(OwnerGuardCommand, OwnerGuardHandler(root).handle)
    bus.register(ConnectStatusCommand, ConnectStatusHandler(root).handle)
    bus.register(ConnectGrantCommand, ConnectGrantHandler(root).handle)
    bus.register(ConnectBootstrapCommand, ConnectBootstrapHandler(root).handle)
    bus.register(PhoneActionCommand, PhoneActionHandler(root).handle)
    bus.register(PhoneSpamGuardCommand, PhoneSpamGuardHandler(root).handle)
    bus.register(PersonaConfigCommand, PersonaConfigHandler(root).handle)

    # Defense commands (Wave 9-13 security modules)
    # Each handler registered individually so one failure doesn't disable all.
    try:
        from jarvis_engine.commands.defense_commands import (
            BlockIPCommand,
            ContainmentOverrideCommand,
            ExportForensicsCommand,
            ReviewQuarantineCommand,
            SecurityBriefingCommand,
            SecurityStatusCommand,
            ThreatReportCommand,
            UnblockIPCommand,
        )
        from jarvis_engine.handlers.defense_handlers import (
            BlockIPHandler,
            ContainmentOverrideHandler,
            ExportForensicsHandler,
            ReviewQuarantineHandler,
            SecurityBriefingHandler,
            SecurityStatusHandler,
            ThreatReportHandler,
            UnblockIPHandler,
        )

        import sqlite3
        import threading

        _sec_db_path = root / ".planning" / "brain" / "security.db"
        _sec_db_path.parent.mkdir(parents=True, exist_ok=True)
        _sec_db = sqlite3.connect(str(_sec_db_path), check_same_thread=False)
        from jarvis_engine._db_pragmas import configure_sqlite
        configure_sqlite(_sec_db)
        _sec_lock = threading.Lock()
        _sec_log_dir = _runtime_dir(root) / "forensic"

        # Create a single shared orchestrator for all defense handlers
        # to avoid duplicating threat-response infrastructure.
        _shared_orch = None
        try:
            from jarvis_engine.security.orchestrator import SecurityOrchestrator
            _shared_orch = SecurityOrchestrator(
                db=_sec_db, write_lock=_sec_lock, log_dir=_sec_log_dir,
            )
        except (ImportError, OSError, sqlite3.Error) as exc:
            logger.warning("Shared SecurityOrchestrator init failed (handlers will retry): %s", exc)

        _defense_registrations: list[tuple[type[object], Callable[..., Any]]] = [
            (SecurityStatusCommand, SecurityStatusHandler(root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch).handle),
            (ThreatReportCommand, ThreatReportHandler(root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch).handle),
            (ExportForensicsCommand, ExportForensicsHandler(root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch).handle),
            (ContainmentOverrideCommand, ContainmentOverrideHandler(root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch).handle),
            (BlockIPCommand, BlockIPHandler(root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch).handle),
            (UnblockIPCommand, UnblockIPHandler(root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch).handle),
            (ReviewQuarantineCommand, ReviewQuarantineHandler(root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch).handle),
            (SecurityBriefingCommand, SecurityBriefingHandler(root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch).handle),
        ]
        for _cmd_cls, _handler in _defense_registrations:
            try:
                bus.register(_cmd_cls, cast(Callable[..., Any], _handler))
            except Exception as exc:
                logger.warning("Failed to register %s: %s", _cmd_cls.__name__, exc)
    except (ImportError, OSError, sqlite3.Error) as exc:
        logger.warning("Failed to import defense commands: %s", exc)

    # -- Knowledge --
    bus.register(KnowledgeStatusCommand, KnowledgeStatusHandler(root, kg=kg).handle)
    bus.register(ContradictionListCommand, ContradictionListHandler(root, kg=kg).handle)
    bus.register(ContradictionResolveCommand, ContradictionResolveHandler(root, kg=kg).handle)
    bus.register(FactLockCommand, FactLockHandler(root, kg=kg).handle)
    bus.register(KnowledgeRegressionCommand, KnowledgeRegressionHandler(root, kg=kg).handle)

    # -- Learning --
    learning_engine = None
    pref_tracker = None
    feedback_tracker = None
    usage_tracker = None
    try:
        if engine is None:
            raise RuntimeError("MemoryEngine not available — skipping Learning subsystem")

        from jarvis_engine.learning.engine import ConversationLearningEngine
        from jarvis_engine.learning.feedback import ResponseFeedbackTracker
        from jarvis_engine.learning.preferences import PreferenceTracker
        from jarvis_engine.learning.usage_patterns import UsagePatternTracker

        pref_tracker = PreferenceTracker(db=engine.db, write_lock=engine.write_lock, db_lock=engine.db_lock)
        feedback_tracker = ResponseFeedbackTracker(db=engine.db, write_lock=engine.write_lock, db_lock=engine.db_lock)
        usage_tracker = UsagePatternTracker(db=engine.db, write_lock=engine.write_lock, db_lock=engine.db_lock)
        learning_engine = ConversationLearningEngine(
            pipeline=pipeline, kg=kg, preference_tracker=pref_tracker,
            feedback_tracker=feedback_tracker, usage_tracker=usage_tracker,
        )

        # Expose learning trackers via typed AppContext
        bus.ctx.pref_tracker = pref_tracker
        bus.ctx.feedback_tracker = feedback_tracker
        bus.ctx.usage_tracker = usage_tracker
        bus.ctx.learning_engine = learning_engine

        # Wire feedback tracker into IntentClassifier for route quality penalty (LEARN-02)
        # Classifier is created before learning subsystem, so we set it after the fact
        if intent_classifier is not None:
            intent_classifier.set_feedback_tracker(feedback_tracker)
    except Exception as exc:
        logger.warning("Failed to initialize Learning subsystem, continuing without: %s", exc)

    _register_with_fallback(
        bus, LearnInteractionCommand,
        lambda: LearnInteractionHandler(root, learning_engine=learning_engine).handle,
        lambda: LearnInteractionHandler(root).handle,
    )
    _register_with_fallback(
        bus, CrossBranchQueryCommand,
        lambda: CrossBranchQueryHandler(
            root, engine=engine, kg=kg, embed_service=embed_service
        ).handle,
        lambda: CrossBranchQueryHandler(root).handle,
    )
    _register_with_fallback(
        bus, FlagExpiredFactsCommand,
        lambda: FlagExpiredFactsHandler(root, kg=kg).handle,
        lambda: FlagExpiredFactsHandler(root).handle,
    )
    _register_with_fallback(
        bus, ConsolidateMemoryCommand,
        lambda: ConsolidateMemoryHandler(
            root, engine=engine, gateway=gateway,
            embed_service=embed_service, kg=kg,
        ).handle,
        lambda: ConsolidateMemoryHandler(root).handle,
    )
    _register_with_fallback(
        bus, IntelligenceDashboardCommand,
        lambda: IntelligenceDashboardHandler(
            root,
            pref_tracker=pref_tracker,
            feedback_tracker=feedback_tracker,
            usage_tracker=usage_tracker,
            kg=kg,
            engine=engine,
        ).handle,
        lambda: IntelligenceDashboardHandler(root).handle,
    )

    # -- Sync --
    sync_engine = None
    sync_transport = None
    try:
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        if engine is not None:
            install_changelog_triggers(engine.db, device_id="desktop")
            sync_engine = SyncEngine(engine.db, engine.write_lock, device_id="desktop")

            signing_key = os.environ.get("JARVIS_SIGNING_KEY", "")
            if signing_key:
                # Lazy import: cryptography may crash with pyo3 ABI mismatch on
                # some systems.  Deferring the import keeps the rest of the bus
                # functional even when the crypto library is broken.
                from jarvis_engine.sync.transport import SyncTransport
                salt_path = root / ".planning" / "brain" / "sync_salt.bin"
                sync_transport = SyncTransport(signing_key, salt_path)
    except BaseException as exc:
        # NOTE: BaseException needed because cryptography's pyo3 bindings can
        # raise PanicException (a BaseException subclass) on ABI mismatch.
        # Always re-raise signal-level exceptions so Ctrl+C / sys.exit() work.
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        logger.warning("Failed to initialize Sync subsystem, continuing without: %s", exc)

    _register_with_fallback(
        bus, SyncPullCommand,
        lambda: SyncPullHandler(root, sync_engine=sync_engine, transport=sync_transport).handle,
        lambda: SyncPullHandler(root).handle,
    )
    _register_with_fallback(
        bus, SyncPushCommand,
        lambda: SyncPushHandler(root, sync_engine=sync_engine, transport=sync_transport).handle,
        lambda: SyncPushHandler(root).handle,
    )
    _register_with_fallback(
        bus, SyncStatusCommand,
        lambda: SyncStatusHandler(root, sync_engine=sync_engine).handle,
        lambda: SyncStatusHandler(root).handle,
    )

    # -- Harvesting --
    harvester = None
    budget_manager = None
    try:
        from jarvis_engine.harvesting.budget import BudgetManager
        from jarvis_engine.harvesting.providers import (
            GeminiProvider,
            KimiNvidiaProvider,
            KimiProvider,
            MiniMaxProvider,
        )
        from jarvis_engine.harvesting.harvester import KnowledgeHarvester

        budget_manager = BudgetManager(db_path)

        all_providers = [MiniMaxProvider(), KimiProvider(), KimiNvidiaProvider(), GeminiProvider()]
        available_providers = [p for p in all_providers if p.is_available]

        harvester = KnowledgeHarvester(
            providers=available_providers,
            pipeline=pipeline,
            cost_tracker=cost_tracker,
            budget_manager=budget_manager,
        )
    except Exception as exc:
        logger.warning("Failed to initialize Harvesting subsystem, continuing without: %s", exc)

    _register_with_fallback(
        bus, HarvestTopicCommand,
        lambda: HarvestTopicHandler(harvester=harvester).handle,
        lambda: HarvestTopicHandler().handle,
    )
    _register_with_fallback(
        bus, IngestSessionCommand,
        lambda: IngestSessionHandler(pipeline=pipeline).handle,
        lambda: IngestSessionHandler().handle,
    )
    _register_with_fallback(
        bus, HarvestBudgetCommand,
        lambda: HarvestBudgetHandler(budget_manager=budget_manager).handle,
        lambda: HarvestBudgetHandler().handle,
    )

    # -- Proactive Intelligence --
    proactive_engine = None
    try:
        from jarvis_engine.proactive import (
            DEFAULT_TRIGGER_RULES,
            Notifier,
            ProactiveEngine,
        )

        notifier = Notifier()
        proactive_engine = ProactiveEngine(rules=DEFAULT_TRIGGER_RULES, notifier=notifier, root=root)
    except Exception as exc:
        logger.warning("Failed to initialize Proactive subsystem, continuing without: %s", exc)

    _register_with_fallback(
        bus, ProactiveCheckCommand,
        lambda: ProactiveCheckHandler(root, proactive_engine=proactive_engine).handle,
        lambda: ProactiveCheckHandler(root).handle,
    )

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

    # Expose subsystem references via typed AppContext
    bus.ctx.engine = engine
    bus.ctx.embed_service = embed_service
    bus.ctx.intent_classifier = intent_classifier
    bus.ctx.kg = kg
    bus.ctx.gateway = gateway

    # Warm embedding model in background (first embed call loads the ~300MB model)
    if embed_service is not None:
        def _warm_embeddings() -> None:
            try:
                embed_service.embed("warmup", prefix="search_document")
                logger.info("Embedding model warmed up")
            except (OSError, RuntimeError, ValueError) as exc:
                logger.debug("Embedding warm-up failed (will load on first use): %s", exc)

        import threading as _threading
        _threading.Thread(target=_warm_embeddings, daemon=True, name="embed-warmup").start()

    return bus
