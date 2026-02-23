"""Handler classes for the Jarvis Command Bus (adapter-shim pattern)."""

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
    OpsBriefHandler,
    OpsExportActionsHandler,
    OpsSyncHandler,
    OpsAutopilotHandler,
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
from jarvis_engine.handlers.harvest_handlers import (
    HarvestBudgetHandler,
    HarvestHandler,
    IngestSessionHandler,
)

__all__ = [
    # Memory
    "BrainCompactHandler",
    "BrainContextHandler",
    "BrainRegressionHandler",
    "BrainStatusHandler",
    "IngestHandler",
    "MemoryMaintenanceHandler",
    "MemorySnapshotHandler",
    # Voice
    "VoiceEnrollHandler",
    "VoiceListHandler",
    "VoiceRunHandler",
    "VoiceSayHandler",
    "VoiceVerifyHandler",
    # System
    "DaemonRunHandler",
    "DesktopWidgetHandler",
    "GamingModeHandler",
    "LogHandler",
    "MigrateMemoryHandler",
    "MobileDesktopSyncHandler",
    "OpenWebHandler",
    "SelfHealHandler",
    "ServeMobileHandler",
    "StatusHandler",
    "WeatherHandler",
    # Task
    "RouteHandler",
    "RunTaskHandler",
    "WebResearchHandler",
    # Ops
    "AutomationRunHandler",
    "GrowthAuditHandler",
    "GrowthEvalHandler",
    "GrowthReportHandler",
    "IntelligenceDashboardHandler",
    "MissionCreateHandler",
    "MissionRunHandler",
    "MissionStatusHandler",
    "OpsBriefHandler",
    "OpsExportActionsHandler",
    "OpsSyncHandler",
    "OpsAutopilotHandler",
    # Security
    "ConnectBootstrapHandler",
    "ConnectGrantHandler",
    "ConnectStatusHandler",
    "OwnerGuardHandler",
    "PersonaConfigHandler",
    "PhoneActionHandler",
    "PhoneSpamGuardHandler",
    "RuntimeControlHandler",
    # Knowledge
    "ContradictionListHandler",
    "ContradictionResolveHandler",
    "FactLockHandler",
    "KnowledgeRegressionHandler",
    "KnowledgeStatusHandler",
    # Harvesting
    "HarvestBudgetHandler",
    "HarvestHandler",
    "IngestSessionHandler",
]
