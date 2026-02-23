"""Typed command dataclasses for the Jarvis Command Bus."""

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
    OpsBriefCommand,
    OpsExportActionsCommand,
    OpsSyncCommand,
    OpsAutopilotCommand,
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

__all__ = [
    # Memory
    "BrainCompactCommand",
    "BrainContextCommand",
    "BrainRegressionCommand",
    "BrainStatusCommand",
    "IngestCommand",
    "MemoryMaintenanceCommand",
    "MemorySnapshotCommand",
    # Voice
    "VoiceEnrollCommand",
    "VoiceListCommand",
    "VoiceRunCommand",
    "VoiceSayCommand",
    "VoiceVerifyCommand",
    # System
    "DaemonRunCommand",
    "DesktopWidgetCommand",
    "GamingModeCommand",
    "LogCommand",
    "MigrateMemoryCommand",
    "MobileDesktopSyncCommand",
    "OpenWebCommand",
    "SelfHealCommand",
    "ServeMobileCommand",
    "StatusCommand",
    "WeatherCommand",
    # Task
    "RouteCommand",
    "RunTaskCommand",
    "WebResearchCommand",
    # Ops
    "AutomationRunCommand",
    "GrowthAuditCommand",
    "GrowthEvalCommand",
    "GrowthReportCommand",
    "IntelligenceDashboardCommand",
    "MissionCreateCommand",
    "MissionRunCommand",
    "MissionStatusCommand",
    "OpsBriefCommand",
    "OpsExportActionsCommand",
    "OpsSyncCommand",
    "OpsAutopilotCommand",
    # Security
    "ConnectBootstrapCommand",
    "ConnectGrantCommand",
    "ConnectStatusCommand",
    "OwnerGuardCommand",
    "PersonaConfigCommand",
    "PhoneActionCommand",
    "PhoneSpamGuardCommand",
    "RuntimeControlCommand",
]
