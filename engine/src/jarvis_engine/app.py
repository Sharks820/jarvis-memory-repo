"""Application bootstrap: creates and wires the Command Bus (DI composition root)."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable, cast

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
    DiagnosticRunCommand,
    GrowthAuditCommand,
    GrowthEvalCommand,
    GrowthReportCommand,
    IntelligenceDashboardCommand,
    MemoryHygieneCommand,
    MissionActiveCommand,
    MissionCancelCommand,
    MissionCreateCommand,
    MissionPauseCommand,
    MissionRestartCommand,
    MissionResumeCommand,
    MissionRunCommand,
    MissionStatusCommand,
    MissionStepsCommand,
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

# Handler imports are lazy — each _register_* function imports its own handlers
# to reduce startup overhead when only a subset of subsystems is needed.
from jarvis_engine.commands.harvest_commands import (
    HarvestBudgetCommand,
    HarvestTopicCommand,
    IngestSessionCommand,
)
from jarvis_engine.commands.proactive_commands import (
    CostReductionCommand,
    ProactiveCheckCommand,
    SelfTestCommand,
    WakeWordStartCommand,
)
from jarvis_engine.commands.learning_commands import (
    ConsolidateMemoryCommand,
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
    LearnInteractionCommand,
)
from jarvis_engine.commands.sync_commands import (
    SyncPullCommand,
    SyncPushCommand,
    SyncStatusCommand,
)

logger = logging.getLogger(__name__)


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
    except (ImportError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.warning(
            "Handler factory for %s failed, using fallback: %s",
            command_type.__name__,
            exc,
        )
        handler = fallback_factory()
    bus.register(command_type, handler)


def _init_memory_subsystem(
    db_path: Path,
) -> tuple[Any, Any, Any, Any]:
    """Initialize memory engine, embedding service, ingest pipeline, and KG.

    Returns ``(engine, embed_service, pipeline, kg)`` — all ``None`` on failure.
    """
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
        try:
            from jarvis_engine.learning.temporal import migrate_temporal_metadata

            migrate_temporal_metadata(engine.db, engine.write_lock)
        except (ImportError, sqlite3.Error, OSError) as exc_tm:
            logger.warning("Temporal metadata migration skipped: %s", exc_tm)
        pipeline = EnrichedIngestPipeline(
            engine,
            embed_service,
            classifier,
            knowledge_graph=kg,
        )
        return engine, embed_service, pipeline, kg
    except (ImportError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to initialize MemoryEngine, falling back to adapter shims: %s", exc
        )
        return None, None, None, None


def _init_gateway(
    root: Path,
    db_path: Path,
) -> tuple[Any, Any, Any]:
    """Initialize the intelligence gateway.

    Returns ``(gateway, intent_classifier, cost_tracker)`` — all ``None`` on failure.
    """
    from jarvis_engine._constants import GATEWAY_AUDIT_LOG
    from jarvis_engine._shared import runtime_dir

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
            audit_path=runtime_dir(root) / GATEWAY_AUDIT_LOG,
        )
        return gateway, None, cost_tracker
    except (ImportError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to initialize Intelligence Gateway, continuing without: %s", exc
        )
        return None, None, None


def _register_memory_handlers(
    bus: CommandBus,
    root: Path,
    engine: Any,
    embed_service: Any,
    kg: Any,
    pipeline: Any,
) -> None:
    from jarvis_engine.handlers.memory_handlers import (
        BrainCompactHandler,
        BrainContextHandler,
        BrainRegressionHandler,
        BrainStatusHandler,
        IngestHandler,
        MemoryMaintenanceHandler,
        MemorySnapshotHandler,
    )

    bus.register(
        BrainStatusCommand, BrainStatusHandler(root, engine=engine, kg=kg).handle
    )
    bus.register(
        BrainContextCommand,
        BrainContextHandler(root, engine=engine, embed_service=embed_service).handle,
    )
    bus.register(BrainCompactCommand, BrainCompactHandler(root).handle)
    bus.register(BrainRegressionCommand, BrainRegressionHandler(root).handle)
    bus.register(IngestCommand, IngestHandler(root, pipeline=pipeline).handle)
    bus.register(MemorySnapshotCommand, MemorySnapshotHandler(root).handle)
    bus.register(MemoryMaintenanceCommand, MemoryMaintenanceHandler(root).handle)


def _register_voice_handlers(bus: CommandBus, root: Path, gateway: Any) -> None:
    from jarvis_engine.handlers.voice_handlers import (
        VoiceEnrollHandler,
        VoiceListHandler,
        VoiceListenHandler,
        VoiceRunHandler,
        VoiceSayHandler,
        VoiceVerifyHandler,
    )

    bus.register(VoiceListCommand, VoiceListHandler(root).handle)
    bus.register(VoiceSayCommand, VoiceSayHandler(root).handle)
    bus.register(VoiceEnrollCommand, VoiceEnrollHandler(root).handle)
    bus.register(VoiceVerifyCommand, VoiceVerifyHandler(root).handle)
    bus.register(VoiceRunCommand, VoiceRunHandler(root).handle)
    bus.register(VoiceListenCommand, VoiceListenHandler(root, gateway=gateway).handle)


def _register_system_handlers(bus: CommandBus, root: Path) -> None:
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


def _register_task_handlers(
    bus: CommandBus,
    root: Path,
    gateway: Any,
    intent_classifier: Any,
) -> None:
    from jarvis_engine.handlers.task_handlers import (
        QueryHandler,
        RouteHandler,
        RunTaskHandler,
        WebResearchHandler,
    )
    from jarvis_engine.handlers.voice_handlers import PersonaComposeHandler

    bus.register(RunTaskCommand, RunTaskHandler(root).handle)
    bus.register(
        RouteCommand,
        RouteHandler(root, classifier=intent_classifier, gateway=gateway).handle,
    )
    if gateway is not None:
        bus.register(
            QueryCommand, QueryHandler(gateway, classifier=intent_classifier).handle
        )
        bus.register(
            PersonaComposeCommand, PersonaComposeHandler(root, gateway=gateway).handle
        )
    else:
        from jarvis_engine.commands.task_commands import QueryResult
        from jarvis_engine.commands.voice_commands import PersonaComposeResult

        def _gateway_unavailable_handler(cmd: QueryCommand) -> QueryResult:
            return QueryResult(text="Gateway not initialized", return_code=2)

        def _persona_gateway_unavailable(
            cmd: PersonaComposeCommand,
        ) -> PersonaComposeResult:
            return PersonaComposeResult(message="error: gateway not available")

        bus.register(QueryCommand, _gateway_unavailable_handler)
        bus.register(PersonaComposeCommand, _persona_gateway_unavailable)
    bus.register(WebResearchCommand, WebResearchHandler(root).handle)


def _register_ops_handlers(
    bus: CommandBus,
    root: Path,
    gateway: Any,
    pipeline: Any,
    engine: Any = None,
) -> None:
    from jarvis_engine.handlers.ops_handlers import (
        AutomationRunHandler,
        DiagnosticRunHandler,
        GrowthAuditHandler,
        GrowthEvalHandler,
        GrowthReportHandler,
        MemoryHygieneHandler,
        MissionActiveHandler,
        MissionCancelHandler,
        MissionCreateHandler,
        MissionPauseHandler,
        MissionRestartHandler,
        MissionResumeHandler,
        MissionRunHandler,
        MissionStatusHandler,
        MissionStepsHandler,
        OpsAutopilotHandler,
        OpsBriefHandler,
        OpsExportActionsHandler,
        OpsSyncHandler,
    )

    bus.register(OpsBriefCommand, OpsBriefHandler(root, gateway=gateway).handle)
    bus.register(OpsExportActionsCommand, OpsExportActionsHandler(root).handle)
    bus.register(OpsSyncCommand, OpsSyncHandler(root).handle)
    bus.register(OpsAutopilotCommand, OpsAutopilotHandler(root).handle)
    bus.register(AutomationRunCommand, AutomationRunHandler(root).handle)
    bus.register(MissionCreateCommand, MissionCreateHandler(root).handle)
    bus.register(MissionCancelCommand, MissionCancelHandler(root).handle)
    bus.register(MissionStatusCommand, MissionStatusHandler(root).handle)
    bus.register(
        MissionRunCommand, MissionRunHandler(root, enriched_pipeline=pipeline).handle
    )
    bus.register(MissionPauseCommand, MissionPauseHandler(root).handle)
    bus.register(MissionResumeCommand, MissionResumeHandler(root).handle)
    bus.register(MissionRestartCommand, MissionRestartHandler(root).handle)
    bus.register(MissionStepsCommand, MissionStepsHandler(root).handle)
    bus.register(MissionActiveCommand, MissionActiveHandler(root).handle)
    bus.register(MemoryHygieneCommand, MemoryHygieneHandler(root, engine=engine).handle)
    bus.register(GrowthEvalCommand, GrowthEvalHandler(root).handle)
    bus.register(GrowthReportCommand, GrowthReportHandler(root).handle)
    bus.register(GrowthAuditCommand, GrowthAuditHandler(root).handle)
    bus.register(DiagnosticRunCommand, DiagnosticRunHandler(root).handle)


def _register_security_handlers(bus: CommandBus, root: Path) -> None:
    """Register security CQRS handlers (core + defense)."""
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

    bus.register(RuntimeControlCommand, RuntimeControlHandler(root).handle)
    bus.register(OwnerGuardCommand, OwnerGuardHandler(root).handle)
    bus.register(ConnectStatusCommand, ConnectStatusHandler(root).handle)
    bus.register(ConnectGrantCommand, ConnectGrantHandler(root).handle)
    bus.register(ConnectBootstrapCommand, ConnectBootstrapHandler(root).handle)
    bus.register(PhoneActionCommand, PhoneActionHandler(root).handle)
    bus.register(PhoneSpamGuardCommand, PhoneSpamGuardHandler(root).handle)
    bus.register(PersonaConfigCommand, PersonaConfigHandler(root).handle)

    _register_defense_handlers(bus, root)


def _register_defense_handlers(bus: CommandBus, root: Path) -> None:
    """Register defense command handlers with shared SecurityOrchestrator."""
    from jarvis_engine._shared import runtime_dir

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

        import threading

        _sec_db_path = root / ".planning" / "brain" / "security.db"
        _sec_db_path.parent.mkdir(parents=True, exist_ok=True)
        from jarvis_engine._db_pragmas import connect_db

        _sec_db = connect_db(_sec_db_path, check_same_thread=False)
        _sec_lock = threading.Lock()
        _sec_log_dir = runtime_dir(root) / "forensic"

        _shared_orch = None
        try:
            from jarvis_engine.security.orchestrator import SecurityOrchestrator

            _shared_orch = SecurityOrchestrator(
                db=_sec_db,
                write_lock=_sec_lock,
                log_dir=_sec_log_dir,
            )
        except (ImportError, OSError, sqlite3.Error) as exc:
            logger.warning(
                "Shared SecurityOrchestrator init failed (handlers will retry): %s", exc
            )

        _defense_registrations: list[tuple[type[object], Callable[..., Any]]] = [
            (
                SecurityStatusCommand,
                SecurityStatusHandler(
                    root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch
                ).handle,
            ),
            (
                ThreatReportCommand,
                ThreatReportHandler(
                    root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch
                ).handle,
            ),
            (
                ExportForensicsCommand,
                ExportForensicsHandler(
                    root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch
                ).handle,
            ),
            (
                ContainmentOverrideCommand,
                ContainmentOverrideHandler(
                    root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch
                ).handle,
            ),
            (
                BlockIPCommand,
                BlockIPHandler(
                    root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch
                ).handle,
            ),
            (
                UnblockIPCommand,
                UnblockIPHandler(
                    root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch
                ).handle,
            ),
            (
                ReviewQuarantineCommand,
                ReviewQuarantineHandler(
                    root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch
                ).handle,
            ),
            (
                SecurityBriefingCommand,
                SecurityBriefingHandler(
                    root, _sec_db, _sec_lock, _sec_log_dir, orchestrator=_shared_orch
                ).handle,
            ),
        ]
        for _cmd_cls, _handler in _defense_registrations:
            try:
                bus.register(_cmd_cls, cast(Callable[..., Any], _handler))
            except TypeError as exc:
                logger.warning("Failed to register %s: %s", _cmd_cls.__name__, exc)
    except (ImportError, OSError, sqlite3.Error) as exc:
        logger.warning("Failed to import defense commands: %s", exc)


def _register_knowledge_handlers(bus: CommandBus, root: Path, kg: Any) -> None:
    from jarvis_engine.handlers.knowledge_handlers import (
        ContradictionListHandler,
        ContradictionResolveHandler,
        FactLockHandler,
        KnowledgeRegressionHandler,
        KnowledgeStatusHandler,
    )

    bus.register(KnowledgeStatusCommand, KnowledgeStatusHandler(root, kg=kg).handle)
    bus.register(ContradictionListCommand, ContradictionListHandler(root, kg=kg).handle)
    bus.register(
        ContradictionResolveCommand, ContradictionResolveHandler(root, kg=kg).handle
    )
    bus.register(FactLockCommand, FactLockHandler(root, kg=kg).handle)
    bus.register(
        KnowledgeRegressionCommand, KnowledgeRegressionHandler(root, kg=kg).handle
    )


def _init_learning_subsystem(
    bus: CommandBus,
    root: Path,
    engine: Any,
    pipeline: Any,
    kg: Any,
    gateway: Any,
    embed_service: Any,
    intent_classifier: Any,
) -> tuple[Any, Any, Any, Any]:
    """Initialize learning subsystem and register handlers.

    Returns ``(learning_engine, pref_tracker, feedback_tracker, usage_tracker)``.
    """
    learning_engine = None
    pref_tracker = None
    feedback_tracker = None
    usage_tracker = None
    try:
        if engine is None:
            raise RuntimeError(
                "MemoryEngine not available — skipping Learning subsystem"
            )

        from jarvis_engine.learning.engine import ConversationLearningEngine
        from jarvis_engine.learning.feedback import ResponseFeedbackTracker
        from jarvis_engine.learning.preferences import PreferenceTracker
        from jarvis_engine.learning.usage_patterns import UsagePatternTracker

        pref_tracker = PreferenceTracker(
            db=engine.db, write_lock=engine.write_lock, db_lock=engine.db_lock
        )
        feedback_tracker = ResponseFeedbackTracker(
            db=engine.db, write_lock=engine.write_lock, db_lock=engine.db_lock
        )
        usage_tracker = UsagePatternTracker(
            db=engine.db, write_lock=engine.write_lock, db_lock=engine.db_lock
        )
        learning_engine = ConversationLearningEngine(
            pipeline=pipeline,
            kg=kg,
            preference_tracker=pref_tracker,
            feedback_tracker=feedback_tracker,
            usage_tracker=usage_tracker,
        )

        bus.ctx.pref_tracker = pref_tracker
        bus.ctx.feedback_tracker = feedback_tracker
        bus.ctx.usage_tracker = usage_tracker
        bus.ctx.learning_engine = learning_engine

        if intent_classifier is not None:
            intent_classifier.set_feedback_tracker(feedback_tracker)
    except (ImportError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to initialize Learning subsystem, continuing without: %s", exc
        )

    from jarvis_engine.handlers.learning_handlers import (
        ConsolidateMemoryHandler,
        CrossBranchQueryHandler,
        FlagExpiredFactsHandler,
        LearnInteractionHandler,
    )
    from jarvis_engine.handlers.ops_handlers import IntelligenceDashboardHandler

    _register_with_fallback(
        bus,
        LearnInteractionCommand,
        lambda: LearnInteractionHandler(root, learning_engine=learning_engine).handle,
        lambda: LearnInteractionHandler(root).handle,
    )
    _register_with_fallback(
        bus,
        CrossBranchQueryCommand,
        lambda: (
            CrossBranchQueryHandler(
                root, engine=engine, kg=kg, embed_service=embed_service
            ).handle
        ),
        lambda: CrossBranchQueryHandler(root).handle,
    )
    _register_with_fallback(
        bus,
        FlagExpiredFactsCommand,
        lambda: FlagExpiredFactsHandler(root, kg=kg).handle,
        lambda: FlagExpiredFactsHandler(root).handle,
    )
    _register_with_fallback(
        bus,
        ConsolidateMemoryCommand,
        lambda: (
            ConsolidateMemoryHandler(
                root,
                engine=engine,
                gateway=gateway,
                embed_service=embed_service,
                kg=kg,
            ).handle
        ),
        lambda: ConsolidateMemoryHandler(root).handle,
    )
    _register_with_fallback(
        bus,
        IntelligenceDashboardCommand,
        lambda: (
            IntelligenceDashboardHandler(
                root,
                pref_tracker=pref_tracker,
                feedback_tracker=feedback_tracker,
                usage_tracker=usage_tracker,
                kg=kg,
                engine=engine,
            ).handle
        ),
        lambda: IntelligenceDashboardHandler(root).handle,
    )
    return learning_engine, pref_tracker, feedback_tracker, usage_tracker


def _init_sync_subsystem(bus: CommandBus, root: Path, engine: Any) -> None:
    """Initialize sync subsystem and register handlers."""
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
                from jarvis_engine.sync.transport import SyncTransport

                salt_path = root / ".planning" / "brain" / "sync_salt.bin"
                sync_transport = SyncTransport(signing_key, salt_path)
    except BaseException as exc:
        # NOTE: BaseException needed because cryptography's pyo3 bindings can
        # raise PanicException (a BaseException subclass) on ABI mismatch.
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        logger.warning(
            "Failed to initialize Sync subsystem, continuing without: %s", exc
        )

    from jarvis_engine.handlers.sync_handlers import (
        SyncPullHandler,
        SyncPushHandler,
        SyncStatusHandler,
    )

    _register_with_fallback(
        bus,
        SyncPullCommand,
        lambda: (
            SyncPullHandler(
                root, sync_engine=sync_engine, transport=sync_transport
            ).handle
        ),
        lambda: SyncPullHandler(root).handle,
    )
    _register_with_fallback(
        bus,
        SyncPushCommand,
        lambda: (
            SyncPushHandler(
                root, sync_engine=sync_engine, transport=sync_transport
            ).handle
        ),
        lambda: SyncPushHandler(root).handle,
    )
    _register_with_fallback(
        bus,
        SyncStatusCommand,
        lambda: SyncStatusHandler(root, sync_engine=sync_engine).handle,
        lambda: SyncStatusHandler(root).handle,
    )


def _init_harvesting_subsystem(
    bus: CommandBus,
    root: Path,
    db_path: Path,
    pipeline: Any,
    cost_tracker: Any,
) -> None:
    """Initialize harvesting subsystem and register handlers."""
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

        all_providers = [
            MiniMaxProvider(),
            KimiProvider(),
            KimiNvidiaProvider(),
            GeminiProvider(),
        ]
        available_providers = [p for p in all_providers if p.is_available]

        harvester = KnowledgeHarvester(
            providers=available_providers,
            pipeline=pipeline,
            cost_tracker=cost_tracker,
            budget_manager=budget_manager,
        )
    except (ImportError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to initialize Harvesting subsystem, continuing without: %s", exc
        )

    from jarvis_engine.handlers.harvest_handlers import (
        HarvestBudgetHandler,
        HarvestTopicHandler,
        IngestSessionHandler,
    )

    _register_with_fallback(
        bus,
        HarvestTopicCommand,
        lambda: HarvestTopicHandler(harvester=harvester).handle,
        lambda: HarvestTopicHandler().handle,
    )
    _register_with_fallback(
        bus,
        IngestSessionCommand,
        lambda: IngestSessionHandler(pipeline=pipeline).handle,
        lambda: IngestSessionHandler().handle,
    )
    _register_with_fallback(
        bus,
        HarvestBudgetCommand,
        lambda: HarvestBudgetHandler(budget_manager=budget_manager).handle,
        lambda: HarvestBudgetHandler().handle,
    )


def _init_proactive_subsystem(
    bus: CommandBus,
    root: Path,
    gateway: Any,
    engine: Any,
    embed_service: Any,
    cost_tracker: Any,
) -> None:
    """Initialize proactive intelligence and register handlers."""
    proactive_engine = None
    try:
        from jarvis_engine.proactive import (
            DEFAULT_TRIGGER_RULES,
            Notifier,
            ProactiveEngine,
        )

        notifier = Notifier()
        proactive_engine = ProactiveEngine(
            rules=DEFAULT_TRIGGER_RULES, notifier=notifier, root=root
        )
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to initialize Proactive subsystem, continuing without: %s", exc
        )

    from jarvis_engine.handlers.proactive_handlers import (
        CostReductionHandler,
        ProactiveCheckHandler,
        SelfTestHandler,
        WakeWordStartHandler,
    )

    _register_with_fallback(
        bus,
        ProactiveCheckCommand,
        lambda: ProactiveCheckHandler(root, proactive_engine=proactive_engine).handle,
        lambda: ProactiveCheckHandler(root).handle,
    )

    bus.register(
        WakeWordStartCommand, WakeWordStartHandler(root, gateway=gateway).handle
    )

    bus.register(
        CostReductionCommand,
        CostReductionHandler(root, cost_tracker=cost_tracker).handle,
    )
    bus.register(
        SelfTestCommand,
        SelfTestHandler(root, engine=engine, embed_service=embed_service).handle,
    )


def create_app(root: Path) -> CommandBus:
    """Build and wire the full Command Bus.  This is the DI composition root."""
    bus = CommandBus()

    # Ensure required directories
    (root / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    from jarvis_engine._shared import runtime_dir

    (runtime_dir(root) / "pids").mkdir(parents=True, exist_ok=True)
    (root / ".planning" / "logs").mkdir(parents=True, exist_ok=True)

    from jarvis_engine._shared import memory_db_path

    db_path = memory_db_path(root)

    # Core subsystem init
    engine, embed_service, pipeline, kg = _init_memory_subsystem(db_path)
    gateway, intent_classifier, cost_tracker = _init_gateway(root, db_path)

    # Create IntentClassifier if embedding service is available
    if intent_classifier is None and embed_service is not None:
        try:
            from jarvis_engine.gateway.classifier import IntentClassifier

            intent_classifier = IntentClassifier(embed_service)
        except (ImportError, RuntimeError, OSError) as exc:
            logger.debug("IntentClassifier init failed: %s", exc)

    if pipeline is not None and gateway is not None:
        pipeline.set_gateway(gateway)

    # Register all handler groups
    _register_memory_handlers(bus, root, engine, embed_service, kg, pipeline)
    _register_voice_handlers(bus, root, gateway)
    _register_system_handlers(bus, root)
    _register_task_handlers(bus, root, gateway, intent_classifier)
    _register_ops_handlers(bus, root, gateway, pipeline, engine=engine)
    _register_security_handlers(bus, root)
    _register_knowledge_handlers(bus, root, kg)

    # Subsystems with internal state
    _init_learning_subsystem(
        bus,
        root,
        engine,
        pipeline,
        kg,
        gateway,
        embed_service,
        intent_classifier,
    )
    _init_sync_subsystem(bus, root, engine)
    _init_harvesting_subsystem(bus, root, db_path, pipeline, cost_tracker)
    _init_proactive_subsystem(bus, root, gateway, engine, embed_service, cost_tracker)

    # Expose subsystem references via typed AppContext
    bus.ctx.engine = engine
    bus.ctx.embed_service = embed_service
    bus.ctx.intent_classifier = intent_classifier
    bus.ctx.kg = kg
    bus.ctx.gateway = gateway

    # Warm embedding model in background (skip in test environments to avoid
    # loading the full nomic-bert model in every xdist worker).
    if embed_service is not None and not os.environ.get("JARVIS_SKIP_EMBED_WARMUP"):

        def _warm_embeddings() -> None:
            try:
                embed_service.embed("warmup", prefix="search_document")
                logger.info("Embedding model warmed up")
            except Exception as exc:  # noqa: BLE001 — background warmup must not crash
                logger.debug(
                    "Embedding warm-up failed (will load on first use): %s", exc
                )

        import threading as _threading

        _threading.Thread(
            target=_warm_embeddings, daemon=True, name="embed-warmup"
        ).start()

    return bus
