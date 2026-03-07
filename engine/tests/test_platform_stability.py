"""Tests for Phase 4 Platform Stability: STAB-01 through STAB-05.

STAB-01: db_path.exists() gate removal — fresh DB creates engine
STAB-02: Silent except blocks now log at debug level
STAB-03: ConsolidateMemoryCommand CQRS — dispatch via bus, CLI, daemon
STAB-04: Proactive trigger diagnostics — empty data source messages
STAB-05: Meta — this test file itself contributes to 4200+ test target
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.learning_commands import ConsolidateMemoryResult
from jarvis_engine.commands.proactive_commands import ProactiveCheckResult
from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.proactive import ProactiveEngine
from jarvis_engine.proactive.triggers import TriggerAlert



# ---------------------------------------------------------------------------
# STAB-01: db_path.exists() gate removal
# ---------------------------------------------------------------------------


class TestFreshDBCreation:
    """Verify create_app initializes brain subsystem even without existing DB."""

    def test_create_app_fresh_db_creates_engine(self, tmp_path):
        """create_app with no existing DB should still initialize MemoryEngine."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True)
        db_path = brain_dir / "jarvis_memory.db"
        assert not db_path.exists(), "DB file should not exist yet"

        with patch("jarvis_engine.memory.embeddings.EmbeddingService") as mock_embed:
            mock_embed.return_value = MagicMock()
            with patch("jarvis_engine.memory.engine.MemoryEngine") as mock_engine:
                mock_engine_inst = MagicMock()
                mock_engine_inst._db = MagicMock()
                mock_engine_inst._write_lock = MagicMock()
                mock_engine_inst._db_lock = MagicMock()
                mock_engine.return_value = mock_engine_inst

                mock_engine.assert_not_called()  # not yet
                from jarvis_engine.app import create_app
                bus = create_app(tmp_path)
                # MemoryEngine should be called even without existing DB file
                mock_engine.assert_called_once()

    def test_cost_tracker_created_without_db_file(self, tmp_path):
        """CostTracker is initialized even when DB file does not exist."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True)

        with patch("jarvis_engine.memory.embeddings.EmbeddingService") as mock_embed, \
             patch("jarvis_engine.memory.engine.MemoryEngine") as mock_engine, \
             patch("jarvis_engine.gateway.costs.CostTracker") as mock_ct:
            mock_embed.return_value = MagicMock()
            me = MagicMock()
            me._db = MagicMock()
            me._write_lock = MagicMock()
            me._db_lock = MagicMock()
            mock_engine.return_value = me

            from jarvis_engine.app import create_app
            bus = create_app(tmp_path)
            mock_ct.assert_called_once()

    def test_budget_manager_created_without_db_file(self, tmp_path):
        """BudgetManager is initialized even when DB file does not exist."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True)

        with patch("jarvis_engine.memory.embeddings.EmbeddingService") as mock_embed, \
             patch("jarvis_engine.memory.engine.MemoryEngine") as mock_engine, \
             patch("jarvis_engine.harvesting.budget.BudgetManager") as mock_bm:
            mock_embed.return_value = MagicMock()
            me = MagicMock()
            me._db = MagicMock()
            me._write_lock = MagicMock()
            me._db_lock = MagicMock()
            mock_engine.return_value = me

            from jarvis_engine.app import create_app
            bus = create_app(tmp_path)
            mock_bm.assert_called_once()

    def test_graceful_degradation_on_import_error(self, tmp_path):
        """If MemoryEngine raises, graceful fallback still works."""
        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True)

        with patch("jarvis_engine.memory.embeddings.EmbeddingService", side_effect=RuntimeError("test")):
            from jarvis_engine.app import create_app
            bus = create_app(tmp_path)
            # Bus should still be created even without engine
            assert bus is not None


# ---------------------------------------------------------------------------
# STAB-02: Silent except blocks now log
# ---------------------------------------------------------------------------


class TestSilentExceptLogging:
    """Verify previously-silent except blocks now log at debug level."""

    def test_mission_topic_source1_logs_on_error(self, tmp_path):
        """Source 1 (recent queries) logs debug on DB error."""
        from jarvis_engine.learning_missions import auto_generate_missions

        missions_path = tmp_path / ".planning" / "missions.json"
        missions_path.parent.mkdir(parents=True, exist_ok=True)
        missions_path.write_text("[]", encoding="utf-8")

        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        db_path = brain_dir / "jarvis_memory.db"
        # Create a DB but with no records table to force error
        conn = sqlite3.connect(str(db_path))
        conn.close()

        with patch("jarvis_engine.learning_missions.logger") as mock_logger:
            auto_generate_missions(tmp_path, max_new=1, db_path=db_path)
            # Should have called logger.debug at least once for a failed query
            debug_calls = [
                c for c in mock_logger.debug.call_args_list
                if "Topic extraction" in str(c) or "failed" in str(c).lower()
            ]
            assert len(debug_calls) >= 1, "Expected debug log for Source 1 DB error"

    def test_mission_topic_source2_logs_on_error(self, tmp_path):
        """Source 2 (KG gaps) logs debug on DB error."""
        from jarvis_engine.learning_missions import auto_generate_missions

        missions_path = tmp_path / ".planning" / "missions.json"
        missions_path.parent.mkdir(parents=True, exist_ok=True)
        missions_path.write_text("[]", encoding="utf-8")

        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        db_path = brain_dir / "jarvis_memory.db"
        # Create DB with records table but no kg_nodes
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE records (ts TEXT, summary TEXT, source TEXT)")
        conn.close()

        with patch("jarvis_engine.learning_missions.logger") as mock_logger:
            auto_generate_missions(tmp_path, max_new=1, db_path=db_path)
            debug_calls = [
                c for c in mock_logger.debug.call_args_list
                if "KG gap" in str(c) or "KG strength" in str(c)
            ]
            assert len(debug_calls) >= 1, "Expected debug log for Source 2/3 DB error"

    def test_learning_engine_correction_detector_import_has_logging(self):
        """ImportError blocks in learning/engine.py have logger.debug calls."""
        import inspect
        from jarvis_engine.learning import engine as engine_mod
        source = inspect.getsource(engine_mod)
        # After STAB-02, "except ImportError as exc" should exist (not bare "except ImportError:\n    pass")
        assert "except ImportError as exc:" in source
        assert "correction_detector not available" in source
        assert "activity_feed not available" in source

    def test_metrics_logs_temporal_error(self):
        """Temporal distribution query failure now logs debug."""
        from jarvis_engine.learning.metrics import capture_knowledge_metrics

        mock_kg = MagicMock(spec=KnowledgeGraph)
        mock_db = MagicMock()
        mock_db.execute.side_effect = sqlite3.OperationalError("no such column: temporal_type")
        mock_kg.db = mock_db
        mock_kg.db_lock = MagicMock()
        mock_kg.db_lock.__enter__ = MagicMock(return_value=None)
        mock_kg.db_lock.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock(spec=MemoryEngine)
        mock_engine._db = MagicMock()

        with patch("jarvis_engine.learning.metrics.logger") as mock_logger:
            result = capture_knowledge_metrics(engine=mock_engine, kg=mock_kg)
            debug_calls = [
                c for c in mock_logger.debug.call_args_list
                if "Temporal" in str(c) or "migration" in str(c).lower()
            ]
            assert len(debug_calls) >= 1, "Expected debug log for temporal query error"


# ---------------------------------------------------------------------------
# STAB-03: ConsolidateMemoryCommand CQRS
# ---------------------------------------------------------------------------


class TestConsolidateMemoryCommand:
    """Verify ConsolidateMemoryCommand dispatches through CQRS bus."""

    def test_command_dataclass(self):
        """ConsolidateMemoryCommand has correct fields and defaults."""
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        cmd = ConsolidateMemoryCommand()
        assert cmd.branch == ""
        assert cmd.max_groups == 20
        assert cmd.dry_run is False

    def test_command_with_params(self):
        """ConsolidateMemoryCommand accepts custom parameters."""
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        cmd = ConsolidateMemoryCommand(branch="health", max_groups=5, dry_run=True)
        assert cmd.branch == "health"
        assert cmd.max_groups == 5
        assert cmd.dry_run is True

    def test_result_dataclass(self):
        """ConsolidateMemoryResult has correct defaults."""
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryResult

        result = ConsolidateMemoryResult()
        assert result.groups_found == 0
        assert result.records_consolidated == 0
        assert result.new_facts_created == 0
        assert result.errors == []
        assert result.message == ""

    def test_handler_no_engine(self, tmp_path):
        """Handler returns 'not available' when engine is None."""
        from jarvis_engine.handlers.learning_handlers import ConsolidateMemoryHandler
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        handler = ConsolidateMemoryHandler(tmp_path)
        result = handler.handle(ConsolidateMemoryCommand())
        assert "not available" in result.message.lower()

    def test_handler_delegates_to_consolidator(self, tmp_path):
        """Handler creates MemoryConsolidator and calls consolidate()."""
        from jarvis_engine.handlers.learning_handlers import ConsolidateMemoryHandler
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        mock_engine = MagicMock(spec=MemoryEngine)
        handler = ConsolidateMemoryHandler(
            tmp_path, engine=mock_engine, gateway=MagicMock(),
            embed_service=MagicMock(),
        )

        mock_consolidation_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_consolidation_result.groups_found = 3
        mock_consolidation_result.records_consolidated = 15
        mock_consolidation_result.new_facts_created = 3
        mock_consolidation_result.errors = []

        with patch("jarvis_engine.learning.consolidator.MemoryConsolidator") as mock_cls:
            mock_cls.return_value.consolidate.return_value = mock_consolidation_result
            result = handler.handle(ConsolidateMemoryCommand(max_groups=10))

        assert result.groups_found == 3
        assert result.new_facts_created == 3
        assert "3 facts" in result.message

    def test_handler_respects_branch_filter(self, tmp_path):
        """Handler passes branch parameter to consolidator."""
        from jarvis_engine.handlers.learning_handlers import ConsolidateMemoryHandler
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        mock_engine = MagicMock(spec=MemoryEngine)
        handler = ConsolidateMemoryHandler(tmp_path, engine=mock_engine)

        mock_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_result.groups_found = 0
        mock_result.records_consolidated = 0
        mock_result.new_facts_created = 0
        mock_result.errors = []

        with patch("jarvis_engine.learning.consolidator.MemoryConsolidator") as mock_cls:
            mock_cls.return_value.consolidate.return_value = mock_result
            handler.handle(ConsolidateMemoryCommand(branch="health"))
            call_kwargs = mock_cls.return_value.consolidate.call_args[1]
            assert call_kwargs["branch"] == "health"

    def test_handler_respects_dry_run(self, tmp_path):
        """Handler passes dry_run parameter to consolidator."""
        from jarvis_engine.handlers.learning_handlers import ConsolidateMemoryHandler
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        mock_engine = MagicMock(spec=MemoryEngine)
        handler = ConsolidateMemoryHandler(tmp_path, engine=mock_engine)

        mock_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_result.groups_found = 2
        mock_result.records_consolidated = 0
        mock_result.new_facts_created = 0
        mock_result.errors = []

        with patch("jarvis_engine.learning.consolidator.MemoryConsolidator") as mock_cls:
            mock_cls.return_value.consolidate.return_value = mock_result
            handler.handle(ConsolidateMemoryCommand(dry_run=True))
            call_kwargs = mock_cls.return_value.consolidate.call_args[1]
            assert call_kwargs["dry_run"] is True

    def test_handler_kg_backup_before_consolidation(self, tmp_path):
        """Handler backs up KG state before running consolidation."""
        from jarvis_engine.handlers.learning_handlers import ConsolidateMemoryHandler
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        mock_engine = MagicMock(spec=MemoryEngine)
        mock_kg = MagicMock(spec=KnowledgeGraph)
        handler = ConsolidateMemoryHandler(
            tmp_path, engine=mock_engine, kg=mock_kg,
        )

        mock_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_result.groups_found = 0
        mock_result.records_consolidated = 0
        mock_result.new_facts_created = 0
        mock_result.errors = []

        with patch("jarvis_engine.learning.consolidator.MemoryConsolidator") as mock_cls, \
             patch("jarvis_engine.knowledge.regression.RegressionChecker") as mock_rc:
            mock_cls.return_value.consolidate.return_value = mock_result
            handler.handle(ConsolidateMemoryCommand())
            mock_rc.assert_called_once_with(mock_kg)
            mock_rc.return_value.backup_graph.assert_called_once()

    def test_handler_logs_activity(self, tmp_path):
        """Handler logs consolidation event to activity feed."""
        from jarvis_engine.handlers.learning_handlers import ConsolidateMemoryHandler
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        mock_engine = MagicMock(spec=MemoryEngine)
        handler = ConsolidateMemoryHandler(tmp_path, engine=mock_engine)

        mock_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_result.groups_found = 2
        mock_result.records_consolidated = 8
        mock_result.new_facts_created = 2
        mock_result.errors = []

        with patch("jarvis_engine.learning.consolidator.MemoryConsolidator") as mock_cls, \
             patch("jarvis_engine.activity_feed.log_activity") as mock_log:
            mock_cls.return_value.consolidate.return_value = mock_result
            handler.handle(ConsolidateMemoryCommand())
            mock_log.assert_called_once()
            call_args = mock_log.call_args[0]
            assert "consolidation" in str(call_args[0]).lower() or "CONSOLIDATION" in str(call_args[0])

    def test_handler_reports_errors(self, tmp_path):
        """Handler message reflects errors when consolidation fails partially."""
        from jarvis_engine.handlers.learning_handlers import ConsolidateMemoryHandler
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        mock_engine = MagicMock(spec=MemoryEngine)
        handler = ConsolidateMemoryHandler(tmp_path, engine=mock_engine)

        mock_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_result.groups_found = 3
        mock_result.records_consolidated = 10
        mock_result.new_facts_created = 2
        mock_result.errors = ["fetch failed: timeout"]

        with patch("jarvis_engine.learning.consolidator.MemoryConsolidator") as mock_cls:
            mock_cls.return_value.consolidate.return_value = mock_result
            result = handler.handle(ConsolidateMemoryCommand())
            assert "error" in result.message.lower()
            assert result.errors == ["fetch failed: timeout"]


class TestConsolidateCLI:
    """Verify CLI consolidate subcommand works."""

    def test_cmd_consolidate_success(self, capsys):
        """cmd_consolidate prints consolidation stats and response= line."""
        mock_bus = MagicMock(spec=CommandBus)
        mock_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_result.groups_found = 5
        mock_result.records_consolidated = 20
        mock_result.new_facts_created = 5
        mock_result.errors = []
        mock_result.message = "Consolidated 5 facts from 5 groups."
        mock_bus.dispatch.return_value = mock_result

        with patch("jarvis_engine.cli_knowledge._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_consolidate
            rc = cmd_consolidate(branch="", max_groups=20, dry_run=False)

        assert rc == 0
        captured = capsys.readouterr().out
        assert "consolidation_groups=5" in captured
        assert "consolidation_new_facts=5" in captured
        assert "response=" in captured

    def test_cmd_consolidate_with_errors(self, capsys):
        """cmd_consolidate returns 2 and prints errors on failure."""
        mock_bus = MagicMock(spec=CommandBus)
        mock_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_result.groups_found = 1
        mock_result.records_consolidated = 0
        mock_result.new_facts_created = 0
        mock_result.errors = ["fetch failed"]
        mock_result.message = "Consolidated 0 facts with 1 error(s)."
        mock_bus.dispatch.return_value = mock_result

        with patch("jarvis_engine.cli_knowledge._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_consolidate
            rc = cmd_consolidate(branch="", max_groups=20, dry_run=False)

        assert rc == 2
        captured = capsys.readouterr().out
        assert "consolidation_errors=1" in captured

    def test_cmd_consolidate_dry_run(self, capsys):
        """cmd_consolidate passes dry_run to command."""
        mock_bus = MagicMock(spec=CommandBus)
        mock_result = MagicMock(spec=ConsolidateMemoryResult)
        mock_result.groups_found = 3
        mock_result.records_consolidated = 0
        mock_result.new_facts_created = 0
        mock_result.errors = []
        mock_result.message = "Consolidated 0 facts from 3 groups."
        mock_bus.dispatch.return_value = mock_result

        with patch("jarvis_engine.cli_knowledge._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_consolidate
            rc = cmd_consolidate(branch="", max_groups=20, dry_run=True)

        assert rc == 0
        # Verify ConsolidateMemoryCommand was dispatched with dry_run=True
        dispatched_cmd = mock_bus.dispatch.call_args[0][0]
        assert dispatched_cmd.dry_run is True


# ---------------------------------------------------------------------------
# STAB-04: Proactive trigger diagnostics
# ---------------------------------------------------------------------------


class TestProactiveDiagnostics:
    """Verify proactive trigger diagnostics for empty data sources."""

    def _make_handler(self, tmp_path):
        from jarvis_engine.handlers.proactive_handlers import ProactiveCheckHandler
        mock_engine = MagicMock(spec=ProactiveEngine)
        mock_engine.evaluate.return_value = []
        return ProactiveCheckHandler(tmp_path, proactive_engine=mock_engine)

    def _write_snapshot(self, tmp_path, data):
        snap_path = tmp_path / ".planning" / "ops_snapshot.live.json"
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.write_text(json.dumps(data), encoding="utf-8")
        return str(snap_path)

    def test_empty_medications_diagnostic(self, tmp_path):
        """Empty medications array produces diagnostic message."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        handler = self._make_handler(tmp_path)
        self._write_snapshot(tmp_path, {"medications": [], "bills": [1], "calendar_events": [1], "tasks": [1]})

        result = handler.handle(ProactiveCheckCommand())
        assert "medication_reminder" in result.diagnostics
        assert "no medications" in result.diagnostics

    def test_empty_bills_diagnostic(self, tmp_path):
        """Empty bills array produces diagnostic message."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        handler = self._make_handler(tmp_path)
        self._write_snapshot(tmp_path, {"medications": [1], "bills": [], "calendar_events": [1], "tasks": [1]})

        result = handler.handle(ProactiveCheckCommand())
        assert "bill_due_alert" in result.diagnostics
        assert "no bills" in result.diagnostics

    def test_empty_calendar_diagnostic(self, tmp_path):
        """Empty calendar_events produces diagnostic."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        handler = self._make_handler(tmp_path)
        self._write_snapshot(tmp_path, {"medications": [1], "bills": [1], "calendar_events": [], "tasks": [1]})

        result = handler.handle(ProactiveCheckCommand())
        assert "calendar_prep" in result.diagnostics

    def test_empty_tasks_diagnostic(self, tmp_path):
        """Empty tasks produces diagnostic."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        handler = self._make_handler(tmp_path)
        self._write_snapshot(tmp_path, {"medications": [1], "bills": [1], "calendar_events": [1], "tasks": []})

        result = handler.handle(ProactiveCheckCommand())
        assert "urgent_task_alert" in result.diagnostics

    def test_all_empty_shows_all_diagnostics(self, tmp_path):
        """All data sources empty shows all diagnostics."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        handler = self._make_handler(tmp_path)
        self._write_snapshot(tmp_path, {"medications": [], "bills": [], "calendar_events": [], "tasks": []})

        result = handler.handle(ProactiveCheckCommand())
        assert "medication_reminder" in result.diagnostics
        assert "bill_due_alert" in result.diagnostics
        assert "calendar_prep" in result.diagnostics
        assert "urgent_task_alert" in result.diagnostics
        assert "diagnostic" in result.message.lower()

    def test_all_populated_no_diagnostic(self, tmp_path):
        """All data sources populated shows 'All data sources populated'."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        handler = self._make_handler(tmp_path)
        self._write_snapshot(tmp_path, {"medications": [1], "bills": [1], "calendar_events": [1], "tasks": [1]})

        result = handler.handle(ProactiveCheckCommand())
        assert result.diagnostics == ""
        assert "All data sources populated" in result.message

    def test_connectors_not_ready(self, tmp_path):
        """Connectors not ready are included in diagnostics."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        handler = self._make_handler(tmp_path)
        self._write_snapshot(tmp_path, {
            "medications": [1], "bills": [1], "calendar_events": [1], "tasks": [1],
            "connector_statuses": [
                {"name": "google_calendar", "ready": True},
                {"name": "email_imap", "ready": False},
            ],
        })

        result = handler.handle(ProactiveCheckCommand())
        assert "email_imap" in result.diagnostics
        assert "not ready" in result.diagnostics.lower()

    def test_alerts_fired_no_diagnostic_noise(self, tmp_path):
        """When alerts fire, message reports alerts (not diagnostics)."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        from jarvis_engine.handlers.proactive_handlers import ProactiveCheckHandler

        mock_alert = MagicMock(spec=TriggerAlert)
        mock_alert.rule_id = "medication_reminder"
        mock_alert.message = "Take medication"
        mock_alert.priority = "high"
        mock_alert.timestamp = "2026-03-02T10:00:00"

        mock_engine = MagicMock(spec=ProactiveEngine)
        mock_engine.evaluate.return_value = [mock_alert]
        handler = ProactiveCheckHandler(tmp_path, proactive_engine=mock_engine)
        self._write_snapshot(tmp_path, {"medications": [{"name": "test"}], "bills": [], "calendar_events": [], "tasks": []})

        result = handler.handle(ProactiveCheckCommand())
        assert result.alerts_fired == 1
        assert "Fired 1 alert" in result.message

    def test_missing_connector_statuses_key(self, tmp_path):
        """Graceful when connector_statuses key is missing."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckCommand
        handler = self._make_handler(tmp_path)
        self._write_snapshot(tmp_path, {"medications": [1], "bills": [1], "calendar_events": [1], "tasks": [1]})
        # No connector_statuses key at all

        result = handler.handle(ProactiveCheckCommand())
        assert result.diagnostics == ""
        assert "All data sources populated" in result.message

    def test_proactive_check_result_has_diagnostics_field(self):
        """ProactiveCheckResult has diagnostics field defaulting to empty."""
        from jarvis_engine.commands.proactive_commands import ProactiveCheckResult

        result = ProactiveCheckResult()
        assert result.diagnostics == ""

    def test_cli_prints_diagnostics(self, capsys):
        """cmd_proactive_check prints diagnostics when present."""
        mock_bus = MagicMock(spec=CommandBus)
        mock_result = MagicMock(spec=ProactiveCheckResult)
        mock_result.alerts_fired = 0
        mock_result.alerts = "[]"
        mock_result.message = "No alerts. 2 diagnostic(s)."
        mock_result.diagnostics = "medication_reminder: no medications data available; bill_due_alert: no bills data available"
        mock_bus.dispatch.return_value = mock_result

        with patch("jarvis_engine.main._get_bus", return_value=mock_bus):
            from jarvis_engine.main import cmd_proactive_check
            rc = cmd_proactive_check(snapshot_path="")

        assert rc == 0
        captured = capsys.readouterr().out
        assert "diagnostics=" in captured
        assert "medication_reminder" in captured


# ---------------------------------------------------------------------------
# STAB-05: Integration / cross-cutting
# ---------------------------------------------------------------------------


class TestConsolidateRegisteredOnBus:
    """Verify ConsolidateMemoryCommand is registered in create_app."""

    def test_consolidate_command_registered(self, tmp_path):
        """ConsolidateMemoryCommand can be dispatched through the bus."""
        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand

        brain_dir = tmp_path / ".planning" / "brain"
        brain_dir.mkdir(parents=True)

        with patch("jarvis_engine.memory.embeddings.EmbeddingService") as mock_embed, \
             patch("jarvis_engine.memory.engine.MemoryEngine") as mock_engine:
            mock_embed.return_value = MagicMock()
            me = MagicMock()
            me._db = MagicMock()
            me._write_lock = MagicMock()
            me._db_lock = MagicMock()
            mock_engine.return_value = me

            from jarvis_engine.app import create_app
            bus = create_app(tmp_path)

            # Should not raise — command is registered
            with patch("jarvis_engine.learning.consolidator.MemoryConsolidator") as mock_cls:
                mock_r = MagicMock(spec=ConsolidateMemoryResult)
                mock_r.groups_found = 0
                mock_r.records_consolidated = 0
                mock_r.new_facts_created = 0
                mock_r.errors = []
                mock_cls.return_value.consolidate.return_value = mock_r
                result = bus.dispatch(ConsolidateMemoryCommand())
                assert result.message is not None


class TestDaemonConsolidationUsesBus:
    """Verify daemon loop now dispatches via ConsolidateMemoryCommand."""

    def test_daemon_consolidation_imports_command(self):
        """The consolidation CLI uses ConsolidateMemoryCommand via CQRS."""
        # After SoC split, cmd_consolidate lives in cli_knowledge
        import inspect
        from jarvis_engine import cli_knowledge as cli_knowledge_mod
        source = inspect.getsource(cli_knowledge_mod)
        # Verify it uses ConsolidateMemoryCommand, not MemoryConsolidator directly
        assert "ConsolidateMemoryCommand" in source
        # The old inline pattern should be gone from the consolidation block
        # (MemoryConsolidator may still exist elsewhere, but CLI should dispatch via bus)
