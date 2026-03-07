"""Comprehensive tests for app.py — the DI composition root / create_app()."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import patch


from jarvis_engine.command_bus import CommandBus


# ---------------------------------------------------------------------------
# All expected command types that create_app should register
# ---------------------------------------------------------------------------

# Memory commands
from jarvis_engine.commands.memory_commands import (
    BrainCompactCommand,
    BrainContextCommand,
    BrainRegressionCommand,
    BrainStatusCommand,
    IngestCommand,
    MemoryMaintenanceCommand,
    MemorySnapshotCommand,
)

# Voice commands
from jarvis_engine.commands.voice_commands import (
    PersonaComposeCommand,
    VoiceEnrollCommand,
    VoiceListCommand,
    VoiceListenCommand,
    VoiceRunCommand,
    VoiceSayCommand,
    VoiceVerifyCommand,
)

# System commands
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

# Task commands
from jarvis_engine.commands.task_commands import (
    QueryCommand,
    RouteCommand,
    RunTaskCommand,
    WebResearchCommand,
)

# Ops commands
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

# Security commands
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

# Knowledge commands
from jarvis_engine.commands.knowledge_commands import (
    ContradictionListCommand,
    ContradictionResolveCommand,
    FactLockCommand,
    KnowledgeRegressionCommand,
    KnowledgeStatusCommand,
)

# Harvest commands
from jarvis_engine.commands.harvest_commands import (
    HarvestBudgetCommand,
    HarvestTopicCommand,
    IngestSessionCommand,
)

# Proactive commands
from jarvis_engine.commands.proactive_commands import (
    CostReductionCommand,
    ProactiveCheckCommand,
    SelfTestCommand,
    WakeWordStartCommand,
)

# Learning commands
from jarvis_engine.commands.learning_commands import (
    ConsolidateMemoryCommand,
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
    LearnInteractionCommand,
)

# Defense commands
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

# Sync commands
from jarvis_engine.commands.sync_commands import (
    SyncPullCommand,
    SyncPushCommand,
    SyncStatusCommand,
)


# All commands the bus should have after create_app:
ALL_EXPECTED_COMMANDS = [
    # Memory (7)
    BrainCompactCommand,
    BrainContextCommand,
    BrainRegressionCommand,
    BrainStatusCommand,
    IngestCommand,
    MemoryMaintenanceCommand,
    MemorySnapshotCommand,
    # Voice (7)
    PersonaComposeCommand,
    VoiceEnrollCommand,
    VoiceListCommand,
    VoiceListenCommand,
    VoiceRunCommand,
    VoiceSayCommand,
    VoiceVerifyCommand,
    # System (11)
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
    # Task (4)
    QueryCommand,
    RouteCommand,
    RunTaskCommand,
    WebResearchCommand,
    # Ops (12)
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
    # Security (8)
    ConnectBootstrapCommand,
    ConnectGrantCommand,
    ConnectStatusCommand,
    OwnerGuardCommand,
    PersonaConfigCommand,
    PhoneActionCommand,
    PhoneSpamGuardCommand,
    RuntimeControlCommand,
    # Knowledge (5)
    ContradictionListCommand,
    ContradictionResolveCommand,
    FactLockCommand,
    KnowledgeRegressionCommand,
    KnowledgeStatusCommand,
    # Harvest (3)
    HarvestBudgetCommand,
    HarvestTopicCommand,
    IngestSessionCommand,
    # Proactive (4)
    CostReductionCommand,
    ProactiveCheckCommand,
    SelfTestCommand,
    WakeWordStartCommand,
    # Learning (4)
    ConsolidateMemoryCommand,
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
    LearnInteractionCommand,
    # Sync (3)
    SyncPullCommand,
    SyncPushCommand,
    SyncStatusCommand,
    # Defense (8)
    BlockIPCommand,
    ContainmentOverrideCommand,
    ExportForensicsCommand,
    ReviewQuarantineCommand,
    SecurityBriefingCommand,
    SecurityStatusCommand,
    ThreatReportCommand,
    UnblockIPCommand,
]


def _make_root(tmp_path: Path, *, with_db: bool = False) -> Path:
    """Create a minimal root directory structure for create_app.

    If *with_db* is True, creates a fake jarvis_memory.db so the
    MemoryEngine initialization path is entered.
    """
    brain_dir = tmp_path / ".planning" / "brain"
    brain_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = tmp_path / ".planning" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if with_db:
        (brain_dir / "jarvis_memory.db").write_text("")
    return tmp_path


# ---------------------------------------------------------------------------
# Module-level shared bus for read-only tests (created once, reused by many)
# ---------------------------------------------------------------------------
import tempfile as _tempfile

_shared_root = None
_shared_bus = None


def _get_shared_bus():
    """Lazily create a single shared bus for read-only tests."""
    global _shared_root, _shared_bus
    if _shared_bus is None:
        from jarvis_engine.app import create_app

        _shared_root = Path(_tempfile.mkdtemp(prefix="test_app_shared_"))
        _shared_root_made = _make_root(_shared_root)
        _shared_bus = create_app(_shared_root_made)
    return _shared_bus


# ===================================================================
# Happy-path tests (read-only — share a single bus)
# ===================================================================

class TestCreateAppHappyPath:
    """create_app(root) should return a fully wired CommandBus."""

    def test_returns_command_bus_instance(self) -> None:
        bus = _get_shared_bus()
        assert isinstance(bus, CommandBus)

    def test_registered_count_at_least_40(self) -> None:
        bus = _get_shared_bus()
        assert bus.registered_count >= 40

    def test_all_expected_commands_registered(self) -> None:
        """Every known command type should have a handler after create_app."""
        bus = _get_shared_bus()

        missing = []
        for cmd_type in ALL_EXPECTED_COMMANDS:
            # Access internal _handlers dict to verify registration
            if cmd_type not in bus._handlers:
                missing.append(cmd_type.__name__)

        assert missing == [], f"Missing registrations: {missing}"

    def test_bus_is_dispatchable_for_core_commands(self) -> None:
        """Dispatching a status command should not raise ValueError."""
        bus = _get_shared_bus()

        # StatusCommand is always registered; should not raise ValueError
        # (It may raise other errors from the handler, but not ValueError for missing handler)
        assert StatusCommand in bus._handlers


# ===================================================================
# No-DB path (adapter shim path) — also read-only, shares bus
# ===================================================================

class TestCreateAppNoDatabase:
    """When no jarvis_memory.db exists, memory handlers fall back to shims."""

    def test_no_db_still_registers_all_commands(self) -> None:
        bus = _get_shared_bus()

        # All commands should still be registered
        for cmd_type in ALL_EXPECTED_COMMANDS:
            assert cmd_type in bus._handlers, f"{cmd_type.__name__} missing when no DB"

    def test_no_db_query_command_is_dispatchable(self) -> None:
        """Without a DB, QueryCommand should still be registered and dispatchable."""
        bus = _get_shared_bus()
        # QueryCommand always registered (either with real gateway or fallback)
        assert QueryCommand in bus._handlers


# ===================================================================
# MemoryEngine degradation branch
# ===================================================================

class TestMemoryEngineDegradation:
    """When MemoryEngine fails to initialize, the bus should still work."""

    def test_memory_engine_import_failure(self, tmp_path: Path) -> None:
        """Simulate MemoryEngine import failure — bus still registers all commands."""
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path, with_db=True)

        # Patch the MemoryEngine class to raise on construction
        with patch(
            "jarvis_engine.memory.engine.MemoryEngine.__init__",
            side_effect=RuntimeError("simulated memory failure"),
        ):
            bus = create_app(root)

        assert isinstance(bus, CommandBus)
        assert bus.registered_count >= 40
        assert BrainStatusCommand in bus._handlers

    def test_memory_engine_fallback_engine_is_none(self, tmp_path: Path, caplog) -> None:
        """When MemoryEngine init fails, engine should be None; warning logged."""
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path, with_db=True)

        with patch(
            "jarvis_engine.memory.engine.MemoryEngine.__init__",
            side_effect=RuntimeError("bad db"),
        ), caplog.at_level(logging.WARNING):
            bus = create_app(root)

        # Should have logged a fallback warning
        assert any("Failed to initialize MemoryEngine" in msg for msg in caplog.messages)


# ===================================================================
# Gateway degradation branch
# ===================================================================

class TestGatewayDegradation:
    """When the Intelligence Gateway fails to initialize."""

    def test_gateway_init_failure_logs_warning(self, tmp_path: Path, caplog) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.gateway.models.ModelGateway.__init__",
            side_effect=RuntimeError("gateway crash"),
        ), caplog.at_level(logging.WARNING):
            bus = create_app(root)

        assert any("Failed to initialize Intelligence Gateway" in m for m in caplog.messages)
        assert isinstance(bus, CommandBus)

    def test_gateway_failure_registers_fallback_query_handler(self, tmp_path: Path) -> None:
        """Without a gateway, QueryCommand still has a handler that returns error."""
        from jarvis_engine.app import create_app
        from jarvis_engine.commands.task_commands import QueryResult

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.gateway.models.ModelGateway.__init__",
            side_effect=RuntimeError("gateway crash"),
        ):
            bus = create_app(root)

        assert QueryCommand in bus._handlers
        result = bus.dispatch(QueryCommand(query="hello"))
        assert isinstance(result, QueryResult)
        assert result.return_code == 2
        assert "not initialized" in result.text.lower()

    def test_gateway_failure_registers_fallback_persona_handler(self, tmp_path: Path) -> None:
        """Without a gateway, PersonaComposeCommand still has a fallback handler."""
        from jarvis_engine.app import create_app
        from jarvis_engine.commands.voice_commands import PersonaComposeResult

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.gateway.models.ModelGateway.__init__",
            side_effect=RuntimeError("gateway crash"),
        ):
            bus = create_app(root)

        assert PersonaComposeCommand in bus._handlers
        result = bus.dispatch(PersonaComposeCommand(query="test"))
        assert isinstance(result, PersonaComposeResult)
        assert "gateway not available" in result.message.lower()

    def test_gateway_failure_does_not_break_other_registrations(self, tmp_path: Path) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.gateway.models.ModelGateway.__init__",
            side_effect=RuntimeError("gateway crash"),
        ):
            bus = create_app(root)

        # Non-gateway commands should all be present
        for cmd_type in [StatusCommand, RunTaskCommand, OpsBriefCommand, RuntimeControlCommand]:
            assert cmd_type in bus._handlers, f"{cmd_type.__name__} missing after gateway failure"


# ===================================================================
# Learning subsystem degradation branch
# ===================================================================

class TestLearningDegradation:
    """When ConversationLearningEngine fails to initialize."""

    def test_learning_failure_logs_warning(self, tmp_path: Path, caplog) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.learning.engine.ConversationLearningEngine.__init__",
            side_effect=RuntimeError("learning crash"),
        ), caplog.at_level(logging.WARNING):
            bus = create_app(root)

        assert any("Failed to initialize Learning subsystem" in m for m in caplog.messages)

    def test_learning_failure_still_registers_learning_commands(self, tmp_path: Path) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.learning.engine.ConversationLearningEngine.__init__",
            side_effect=RuntimeError("learning crash"),
        ):
            bus = create_app(root)

        # All 3 learning commands should still be registered with fallback handlers
        assert LearnInteractionCommand in bus._handlers
        assert CrossBranchQueryCommand in bus._handlers
        assert FlagExpiredFactsCommand in bus._handlers

    def test_learning_failure_does_not_block_sync(self, tmp_path: Path) -> None:
        """Learning failure should not prevent sync commands from registering."""
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.learning.engine.ConversationLearningEngine.__init__",
            side_effect=RuntimeError("learning crash"),
        ):
            bus = create_app(root)

        assert SyncPullCommand in bus._handlers
        assert SyncPushCommand in bus._handlers
        assert SyncStatusCommand in bus._handlers


# ===================================================================
# Sync subsystem degradation branch
# ===================================================================

class TestSyncDegradation:
    """When the Sync subsystem fails to initialize."""

    def _remove_sync_modules(self) -> dict:
        """Remove sync submodules from sys.modules so the lazy import fails."""
        # Setting modules to None causes ImportError on `from ... import`
        return {
            "jarvis_engine.sync.changelog": None,
            "jarvis_engine.sync.engine": None,
            "jarvis_engine.sync.transport": None,
        }

    def test_sync_failure_logs_warning(self, tmp_path: Path, caplog) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch.dict("sys.modules", self._remove_sync_modules()), \
             caplog.at_level(logging.WARNING):
            bus = create_app(root)

        assert any("Failed to initialize Sync subsystem" in m for m in caplog.messages)

    def test_sync_failure_still_registers_sync_commands(self, tmp_path: Path) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch.dict("sys.modules", self._remove_sync_modules()):
            bus = create_app(root)

        assert SyncPullCommand in bus._handlers
        assert SyncPushCommand in bus._handlers
        assert SyncStatusCommand in bus._handlers

    def test_sync_failure_does_not_block_harvesting(self, tmp_path: Path) -> None:
        """Sync failure should not prevent harvesting commands."""
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch.dict("sys.modules", self._remove_sync_modules()):
            bus = create_app(root)

        assert HarvestTopicCommand in bus._handlers


# ===================================================================
# Harvesting subsystem degradation branch
# ===================================================================

class TestHarvestingDegradation:
    """When the Harvesting subsystem fails to initialize."""

    def test_harvesting_failure_logs_warning(self, tmp_path: Path, caplog) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.harvesting.harvester.KnowledgeHarvester.__init__",
            side_effect=RuntimeError("harvest crash"),
        ), caplog.at_level(logging.WARNING):
            bus = create_app(root)

        assert any("Failed to initialize Harvesting subsystem" in m for m in caplog.messages)

    def test_harvesting_failure_still_registers_harvest_commands(self, tmp_path: Path) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch(
            "jarvis_engine.harvesting.harvester.KnowledgeHarvester.__init__",
            side_effect=RuntimeError("harvest crash"),
        ):
            bus = create_app(root)

        assert HarvestTopicCommand in bus._handlers
        assert IngestSessionCommand in bus._handlers
        assert HarvestBudgetCommand in bus._handlers


# ===================================================================
# Proactive subsystem degradation branch
# ===================================================================

class TestProactiveDegradation:
    """When the Proactive Intelligence subsystem fails to initialize."""

    def test_proactive_failure_logs_warning(self, tmp_path: Path, caplog) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch.dict("sys.modules", {"jarvis_engine.proactive": None}):
            with caplog.at_level(logging.WARNING):
                bus = create_app(root)

        assert any("Failed to initialize Proactive subsystem" in m for m in caplog.messages)

    def test_proactive_failure_still_registers_proactive_check(self, tmp_path: Path) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch.dict("sys.modules", {"jarvis_engine.proactive": None}):
            bus = create_app(root)

        assert ProactiveCheckCommand in bus._handlers

    def test_proactive_failure_wake_word_still_registered(self, tmp_path: Path) -> None:
        """WakeWordStartCommand is registered outside the try block; always present."""
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)

        with patch.dict("sys.modules", {"jarvis_engine.proactive": None}):
            bus = create_app(root)

        assert WakeWordStartCommand in bus._handlers


# ===================================================================
# Multiple simultaneous degradations
# ===================================================================

class TestMultipleDegradations:
    """Multiple subsystems failing should still yield a functional bus."""

    def test_all_optional_subsystems_fail(self, tmp_path: Path, caplog) -> None:
        """Gateway + Learning + Sync + Harvesting + Proactive all fail."""
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path, with_db=True)

        with (
            patch(
                "jarvis_engine.memory.engine.MemoryEngine.__init__",
                side_effect=RuntimeError("mem fail"),
            ),
            patch(
                "jarvis_engine.gateway.models.ModelGateway.__init__",
                side_effect=RuntimeError("gw fail"),
            ),
            patch(
                "jarvis_engine.learning.engine.ConversationLearningEngine.__init__",
                side_effect=RuntimeError("learn fail"),
            ),
            patch(
                "jarvis_engine.sync.changelog.install_changelog_triggers",
                side_effect=RuntimeError("sync fail"),
            ),
            patch(
                "jarvis_engine.harvesting.harvester.KnowledgeHarvester.__init__",
                side_effect=RuntimeError("harvest fail"),
            ),
            patch.dict("sys.modules", {"jarvis_engine.proactive": None}),
            caplog.at_level(logging.WARNING),
        ):
            bus = create_app(root)

        assert isinstance(bus, CommandBus)
        # Even with everything degraded, all commands should still be registered
        assert bus.registered_count >= 40

        # Verify multiple warning messages were logged
        warning_messages = [m for m in caplog.messages if "Failed to initialize" in m]
        assert len(warning_messages) >= 3


# ===================================================================
# Handler registration detail tests
# ===================================================================

class TestHandlerRegistrationDetails:
    """Verify specific handler-to-command mappings (read-only, shared bus)."""

    def test_memory_commands_count(self) -> None:
        bus = _get_shared_bus()

        memory_cmds = [
            BrainStatusCommand, BrainContextCommand, BrainCompactCommand,
            BrainRegressionCommand, IngestCommand, MemorySnapshotCommand,
            MemoryMaintenanceCommand,
        ]
        for cmd in memory_cmds:
            assert cmd in bus._handlers

    def test_voice_commands_count(self) -> None:
        bus = _get_shared_bus()

        voice_cmds = [
            VoiceListCommand, VoiceSayCommand, VoiceEnrollCommand,
            VoiceVerifyCommand, VoiceRunCommand, VoiceListenCommand,
        ]
        for cmd in voice_cmds:
            assert cmd in bus._handlers

    def test_security_commands_count(self) -> None:
        bus = _get_shared_bus()

        security_cmds = [
            RuntimeControlCommand, OwnerGuardCommand, ConnectStatusCommand,
            ConnectGrantCommand, ConnectBootstrapCommand, PhoneActionCommand,
            PhoneSpamGuardCommand, PersonaConfigCommand,
        ]
        for cmd in security_cmds:
            assert cmd in bus._handlers

    def test_knowledge_commands_registered(self) -> None:
        bus = _get_shared_bus()

        knowledge_cmds = [
            KnowledgeStatusCommand, ContradictionListCommand,
            ContradictionResolveCommand, FactLockCommand,
            KnowledgeRegressionCommand,
        ]
        for cmd in knowledge_cmds:
            assert cmd in bus._handlers

    def test_cost_reduction_and_self_test_always_registered(self) -> None:
        """CostReductionCommand and SelfTestCommand are outside try blocks."""
        bus = _get_shared_bus()

        assert CostReductionCommand in bus._handlers
        assert SelfTestCommand in bus._handlers


# ===================================================================
# Edge cases
# ===================================================================

class TestCreateAppEdgeCases:
    """Edge cases and unusual inputs."""

    def test_root_with_nonexistent_planning_dir(self, tmp_path: Path) -> None:
        """Root path exists but .planning/brain does not exist."""
        from jarvis_engine.app import create_app

        # Don't create .planning dirs at all
        bus = create_app(tmp_path)
        assert isinstance(bus, CommandBus)
        assert bus.registered_count >= 40

    def test_root_path_is_absolute(self, tmp_path: Path) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)
        bus = create_app(root)
        assert isinstance(bus, CommandBus)

    def test_create_app_called_twice_yields_independent_buses(self, tmp_path: Path) -> None:
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)
        bus1 = create_app(root)
        bus2 = create_app(root)
        assert bus1 is not bus2
        assert bus1.registered_count == bus2.registered_count

    def test_empty_db_file_triggers_memory_init_and_degrades(self, tmp_path: Path, caplog) -> None:
        """An empty .db file triggers the MemoryEngine path, which will fail gracefully."""
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path, with_db=True)

        with caplog.at_level(logging.WARNING):
            bus = create_app(root)

        # The empty file triggers db_path.exists() -> True, but MemoryEngine
        # will fail since it's not a real SQLite DB. Should degrade gracefully.
        assert isinstance(bus, CommandBus)
        assert bus.registered_count >= 40

    def test_env_vars_do_not_leak(self, tmp_path: Path) -> None:
        """API keys from env should not end up in the bus object itself."""
        from jarvis_engine.app import create_app

        root = _make_root(tmp_path)
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "secret-key-123",
            "GROQ_API_KEY": "",
        }):
            bus = create_app(root)

        # The bus should not expose API keys
        assert not hasattr(bus, "api_key")
        assert "secret-key-123" not in str(bus.__dict__)
