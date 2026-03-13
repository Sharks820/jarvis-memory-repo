"""Tests for ops_handlers -- OpsAutopilot, AutomationRun, GrowthEval,
IntelligenceDashboard, MissionCreate, MissionRun handler classes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jarvis_engine.automation import AutomationExecutor
from jarvis_engine.memory.ingest import EnrichedIngestPipeline
from jarvis_engine.commands.ops_commands import (
    AutomationRunCommand,
    GrowthEvalCommand,
    IntelligenceDashboardCommand,
    MissionCreateCommand,
    MissionRunCommand,
    OpsAutopilotCommand,
)
from jarvis_engine.handlers.ops_handlers import (
    AutomationRunHandler,
    GrowthEvalHandler,
    IntelligenceDashboardHandler,
    MissionCreateHandler,
    MissionRunHandler,
    OpsAutopilotHandler,
)


# ---------------------------------------------------------------------------
# OpsAutopilotHandler
# ---------------------------------------------------------------------------


class TestOpsAutopilotHandler:
    """Tests for OpsAutopilotHandler."""

    @patch("jarvis_engine.ops.autopilot.run_ops_autopilot", return_value=0)
    def test_handle_successful(
        self, mock_impl: MagicMock, tmp_path: Path
    ) -> None:
        """OpsAutopilotHandler delegates to run_ops_autopilot and returns rc=0."""
        snap = tmp_path / ".planning" / "snapshot.json"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text("{}", encoding="utf-8")
        actions = tmp_path / ".planning" / "actions.json"
        actions.write_text("[]", encoding="utf-8")

        handler = OpsAutopilotHandler(root=tmp_path)
        cmd = OpsAutopilotCommand(
            snapshot_path=snap,
            actions_path=actions,
            execute=True,
            approve_privileged=False,
            auto_open_connectors=False,
        )
        result = handler.handle(cmd)

        assert result.return_code == 0
        mock_impl.assert_called_once_with(
            snapshot_path=snap,
            actions_path=actions,
            execute=True,
            approve_privileged=False,
            auto_open_connectors=False,
        )

    @patch("jarvis_engine.ops.autopilot.run_ops_autopilot", return_value=3)
    def test_handle_nonzero_rc_propagated(
        self, mock_impl: MagicMock, tmp_path: Path
    ) -> None:
        """Non-zero return code from impl is propagated through."""
        snap = tmp_path / ".planning" / "snapshot.json"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text("{}", encoding="utf-8")
        actions = tmp_path / ".planning" / "actions.json"
        actions.write_text("[]", encoding="utf-8")

        handler = OpsAutopilotHandler(root=tmp_path)
        cmd = OpsAutopilotCommand(
            snapshot_path=snap,
            actions_path=actions,
        )
        result = handler.handle(cmd)

        assert result.return_code == 3

    def test_handle_invalid_root_returns_code_2(self, tmp_path: Path) -> None:
        """Paths outside the project root trigger return_code=2."""
        handler = OpsAutopilotHandler(root=tmp_path)
        # Use paths that definitely resolve outside tmp_path
        cmd = OpsAutopilotCommand(
            snapshot_path=Path("/etc/snapshot.json"),
            actions_path=Path("/etc/actions.json"),
        )
        result = handler.handle(cmd)
        assert result.return_code == 2

    def test_handle_actions_path_outside_root(self, tmp_path: Path) -> None:
        """If only actions_path is outside root, still returns code 2."""
        snap = tmp_path / "snapshot.json"
        snap.write_text("{}", encoding="utf-8")
        handler = OpsAutopilotHandler(root=tmp_path)
        cmd = OpsAutopilotCommand(
            snapshot_path=snap,
            actions_path=Path("/etc/actions.json"),
        )
        result = handler.handle(cmd)
        assert result.return_code == 2


# ---------------------------------------------------------------------------
# AutomationRunHandler
# ---------------------------------------------------------------------------


class TestAutomationRunHandler:
    """Tests for AutomationRunHandler."""

    @patch("jarvis_engine.automation.load_actions", return_value=[])
    @patch("jarvis_engine.automation.AutomationExecutor")
    @patch("jarvis_engine.memory.store.MemoryStore")
    def test_handle_successful_run(
        self,
        mock_store_cls: MagicMock,
        mock_executor_cls: MagicMock,
        mock_load_actions: MagicMock,
        tmp_path: Path,
    ) -> None:
        """AutomationRunHandler loads actions, runs executor, returns outcomes."""
        actions_path = tmp_path / "actions.json"
        actions_path.write_text("[]", encoding="utf-8")

        mock_executor_instance = MagicMock(spec=AutomationExecutor)
        mock_executor_instance.run.return_value = [{"action": "test", "status": "ok"}]
        mock_executor_cls.return_value = mock_executor_instance

        handler = AutomationRunHandler(root=tmp_path)
        cmd = AutomationRunCommand(
            actions_path=actions_path,
            approve_privileged=True,
            execute=True,
        )
        result = handler.handle(cmd)

        assert result.outcomes == [{"action": "test", "status": "ok"}]
        mock_executor_instance.run.assert_called_once_with(
            [],
            has_explicit_approval=True,
            execute=True,
        )

    def test_handle_path_outside_root_returns_default(self, tmp_path: Path) -> None:
        """Actions path outside root returns empty AutomationRunResult."""
        handler = AutomationRunHandler(root=tmp_path)
        cmd = AutomationRunCommand(actions_path=Path("/etc/evil_actions.json"))
        result = handler.handle(cmd)
        assert result.outcomes == []

    @patch("jarvis_engine.automation.load_actions")
    @patch("jarvis_engine.memory.store.MemoryStore")
    def test_handle_load_actions_called_with_correct_path(
        self,
        mock_store_cls: MagicMock,
        mock_load_actions: MagicMock,
        tmp_path: Path,
    ) -> None:
        """load_actions receives the exact actions_path from the command."""
        actions_path = tmp_path / "my_actions.json"
        actions_path.write_text("[]", encoding="utf-8")
        mock_load_actions.return_value = []

        mock_executor = MagicMock(spec=AutomationExecutor)
        mock_executor.run.return_value = []
        with patch(
            "jarvis_engine.automation.AutomationExecutor", return_value=mock_executor
        ):
            handler = AutomationRunHandler(root=tmp_path)
            cmd = AutomationRunCommand(actions_path=actions_path)
            handler.handle(cmd)

        mock_load_actions.assert_called_once_with(actions_path)


# ---------------------------------------------------------------------------
# GrowthEvalHandler
# ---------------------------------------------------------------------------


class TestGrowthEvalHandler:
    """Tests for GrowthEvalHandler."""

    def test_handle_returns_valid_result(self, tmp_path: Path) -> None:
        """GrowthEvalHandler loads tasks, runs eval, appends history."""
        tasks_path = tmp_path / "golden_tasks.json"
        history_path = tmp_path / "capability_history.jsonl"
        tasks_path.write_text(
            json.dumps(
                [
                    {
                        "task_id": "t1",
                        "prompt": "2+2",
                        "expected_contains": ["4"],
                        "score_weight": 1.0,
                    }
                ]
            ),
            encoding="utf-8",
        )
        history_path.write_text("", encoding="utf-8")

        fake_run = {"model": "test-model", "score_pct": 75.0, "ts": "2026-02-20T00:00:00+00:00"}

        handler = GrowthEvalHandler(root=tmp_path)
        cmd = GrowthEvalCommand(
            model="test-model",
            tasks_path=tasks_path,
            history_path=history_path,
        )

        with patch(
            "jarvis_engine.growth_tracker.load_golden_tasks",
            return_value=[{"task_id": "t1"}],
        ) as mock_load, patch(
            "jarvis_engine.growth_tracker.run_eval",
            return_value=fake_run,
        ) as mock_run_eval, patch(
            "jarvis_engine.growth_tracker.append_history"
        ) as mock_append:
            result = handler.handle(cmd)

        assert result.run == fake_run
        mock_load.assert_called_once_with(tasks_path)
        mock_run_eval.assert_called_once()
        mock_append.assert_called_once_with(history_path, fake_run)

    def test_handle_invalid_tasks_path_returns_empty(self, tmp_path: Path) -> None:
        """Path outside root returns empty GrowthEvalResult."""
        handler = GrowthEvalHandler(root=tmp_path)
        cmd = GrowthEvalCommand(
            model="test",
            tasks_path=Path("/etc/tasks.json"),
            history_path=tmp_path / "history.jsonl",
        )
        result = handler.handle(cmd)
        assert result.run is None

    def test_handle_file_not_found_returns_empty(self, tmp_path: Path) -> None:
        """Missing tasks file returns empty GrowthEvalResult."""
        tasks_path = tmp_path / "nonexistent_tasks.json"
        history_path = tmp_path / "history.jsonl"

        handler = GrowthEvalHandler(root=tmp_path)
        cmd = GrowthEvalCommand(
            model="test",
            tasks_path=tasks_path,
            history_path=history_path,
        )

        with patch(
            "jarvis_engine.growth_tracker.load_golden_tasks",
            side_effect=FileNotFoundError("not found"),
        ):
            result = handler.handle(cmd)

        assert result.run is None

    def test_handle_runtime_error_during_eval(self, tmp_path: Path) -> None:
        """RuntimeError during run_eval returns empty GrowthEvalResult."""
        tasks_path = tmp_path / "tasks.json"
        history_path = tmp_path / "history.jsonl"
        tasks_path.write_text("[]", encoding="utf-8")

        handler = GrowthEvalHandler(root=tmp_path)
        cmd = GrowthEvalCommand(
            model="test",
            tasks_path=tasks_path,
            history_path=history_path,
        )

        with patch(
            "jarvis_engine.growth_tracker.load_golden_tasks", return_value=[]
        ), patch(
            "jarvis_engine.growth_tracker.run_eval",
            side_effect=RuntimeError("connection refused"),
        ):
            result = handler.handle(cmd)

        assert result.run is None


# ---------------------------------------------------------------------------
# IntelligenceDashboardHandler
# ---------------------------------------------------------------------------


class TestIntelligenceDashboardHandler:
    """Tests for IntelligenceDashboardHandler."""

    @patch("jarvis_engine.intelligence_dashboard.build_intelligence_dashboard")
    def test_handle_returns_dashboard_dict(
        self, mock_build: MagicMock, tmp_path: Path
    ) -> None:
        """Handler returns the dict produced by build_intelligence_dashboard."""
        fake_dashboard = {
            "generated_utc": "2026-02-20T00:00:00+00:00",
            "jarvis": {"score_pct": 65.0},
            "ranking": [],
            "etas": [],
        }
        mock_build.return_value = fake_dashboard

        handler = IntelligenceDashboardHandler(root=tmp_path)
        cmd = IntelligenceDashboardCommand(last_runs=10)
        result = handler.handle(cmd)

        assert result.dashboard == fake_dashboard
        mock_build.assert_called_once_with(
            tmp_path, last_runs=10,
            pref_tracker=None, feedback_tracker=None, usage_tracker=None,
            kg=None, engine=None,
        )

    @patch("jarvis_engine.intelligence_dashboard.build_intelligence_dashboard")
    def test_handle_default_last_runs(
        self, mock_build: MagicMock, tmp_path: Path
    ) -> None:
        """Handler uses default last_runs=20 from command defaults."""
        mock_build.return_value = {}

        handler = IntelligenceDashboardHandler(root=tmp_path)
        cmd = IntelligenceDashboardCommand()
        handler.handle(cmd)

        mock_build.assert_called_once_with(
            tmp_path, last_runs=20,
            pref_tracker=None, feedback_tracker=None, usage_tracker=None,
            kg=None, engine=None,
        )

    @patch("jarvis_engine.intelligence_dashboard.build_intelligence_dashboard")
    def test_handle_result_is_intelligence_dashboard_result(
        self, mock_build: MagicMock, tmp_path: Path
    ) -> None:
        """Returned object is an IntelligenceDashboardResult."""
        mock_build.return_value = {"test": True}

        handler = IntelligenceDashboardHandler(root=tmp_path)
        cmd = IntelligenceDashboardCommand()
        result = handler.handle(cmd)

        from jarvis_engine.commands.ops_commands import IntelligenceDashboardResult

        assert isinstance(result, IntelligenceDashboardResult)
        assert result.dashboard == {"test": True}


# ---------------------------------------------------------------------------
# MissionCreateHandler
# ---------------------------------------------------------------------------


class TestMissionCreateHandler:
    """Tests for MissionCreateHandler."""

    @patch("jarvis_engine.learning.missions.create_learning_mission")
    def test_handle_creates_mission(
        self, mock_create: MagicMock, tmp_path: Path
    ) -> None:
        """Successful mission creation returns rc=0 with mission dict."""
        fake_mission = {
            "mission_id": "m-20260220120000",
            "topic": "Quantum computing",
            "objective": "Learn basics",
            "sources": ["wikipedia", "arxiv"],
        }
        mock_create.return_value = fake_mission

        handler = MissionCreateHandler(root=tmp_path)
        cmd = MissionCreateCommand(
            topic="Quantum computing",
            objective="Learn basics",
            sources=["wikipedia"],
        )
        result = handler.handle(cmd)

        assert result.return_code == 0
        assert result.mission == fake_mission
        mock_create.assert_called_once_with(
            tmp_path,
            topic="Quantum computing",
            objective="Learn basics",
            sources=["wikipedia"],
            origin="desktop-manual",
        )

    @patch("jarvis_engine.learning.missions.create_learning_mission")
    def test_handle_empty_topic_returns_code_2(
        self, mock_create: MagicMock, tmp_path: Path
    ) -> None:
        """Empty topic causes ValueError -> return_code=2."""
        mock_create.side_effect = ValueError("topic is required")

        handler = MissionCreateHandler(root=tmp_path)
        cmd = MissionCreateCommand(topic="")
        result = handler.handle(cmd)

        assert result.return_code == 2
        assert result.mission == {}

    @patch("jarvis_engine.learning.missions.create_learning_mission")
    def test_handle_no_sources_uses_defaults(
        self, mock_create: MagicMock, tmp_path: Path
    ) -> None:
        """When no sources are provided, command passes empty list (domain defaults apply)."""
        fake_mission = {"mission_id": "m-123", "topic": "AI Safety"}
        mock_create.return_value = fake_mission

        handler = MissionCreateHandler(root=tmp_path)
        cmd = MissionCreateCommand(topic="AI Safety")
        result = handler.handle(cmd)

        assert result.return_code == 0
        mock_create.assert_called_once_with(
            tmp_path,
            topic="AI Safety",
            objective="",
            sources=[],
            origin="desktop-manual",
        )


# ---------------------------------------------------------------------------
# MissionRunHandler
# ---------------------------------------------------------------------------


class TestMissionRunHandler:
    """Tests for MissionRunHandler."""

    @patch("jarvis_engine.learning.missions.run_learning_mission")
    def test_handle_empty_mission_name_raises_value_error(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """ValueError from run_learning_mission (missing mission) -> rc=2."""
        mock_run.side_effect = ValueError("mission not found: ")

        handler = MissionRunHandler(root=tmp_path)
        cmd = MissionRunCommand(mission_id="")
        result = handler.handle(cmd)

        assert result.return_code == 2
        assert result.report == {}
        assert result.ingested_record_id == ""

    @patch("jarvis_engine.learning.missions.run_learning_mission")
    def test_handle_successful_run_without_ingest(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Successful run with auto_ingest=False skips ingestion."""
        fake_report = {
            "mission_id": "m-123",
            "verified_findings": [
                {"statement": "Test finding", "source_domains": ["example.com"]}
            ],
        }
        mock_run.return_value = fake_report

        handler = MissionRunHandler(root=tmp_path)
        cmd = MissionRunCommand(mission_id="m-123", auto_ingest=False)
        result = handler.handle(cmd)

        assert result.return_code == 0
        assert result.report == fake_report
        assert result.ingested_record_id == ""

    @patch("jarvis_engine.learning.missions.run_learning_mission")
    def test_handle_successful_run_with_auto_ingest(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Successful run with auto_ingest=True ingests each verified finding individually."""
        fake_report = {
            "mission_id": "m-456",
            "verified_findings": [
                {"statement": "Water is H2O", "source_domains": ["chem.org"]},
                {"statement": "Fire is hot", "source_domains": ["science.org"]},
            ],
        }
        mock_run.return_value = fake_report

        mock_pipeline = MagicMock(spec=EnrichedIngestPipeline)
        mock_record = SimpleNamespace(record_id="rec-xyz")
        mock_pipeline.ingest.return_value = mock_record

        handler = MissionRunHandler(root=tmp_path)
        cmd = MissionRunCommand(mission_id="m-456", auto_ingest=True)

        with patch(
            "jarvis_engine.memory.store.MemoryStore"
        ), patch(
            "jarvis_engine.ingest.IngestionPipeline", return_value=mock_pipeline
        ):
            result = handler.handle(cmd)

        assert result.return_code == 0
        assert result.ingested_record_id == "rec-xyz"
        # Each finding is ingested individually for better KG fact extraction
        assert mock_pipeline.ingest.call_count == 2
        first_call = mock_pipeline.ingest.call_args_list[0]
        assert first_call.kwargs["source"] == "mission"
        assert first_call.kwargs["kind"] == "semantic"
        assert "Water is H2O" in first_call.kwargs["content"]

    @patch("jarvis_engine.learning.missions.run_learning_mission")
    def test_handle_no_verified_findings_skips_ingest(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """No verified findings means no ingestion even with auto_ingest=True."""
        fake_report = {
            "mission_id": "m-789",
            "verified_findings": [],
        }
        mock_run.return_value = fake_report

        handler = MissionRunHandler(root=tmp_path)
        cmd = MissionRunCommand(mission_id="m-789", auto_ingest=True)
        result = handler.handle(cmd)

        assert result.return_code == 0
        assert result.ingested_record_id == ""

    @patch("jarvis_engine.learning.missions.run_learning_mission")
    def test_handle_ingest_failure_still_returns_report(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """If auto-ingest raises, result still contains the report with empty ingested_id."""
        fake_report = {
            "mission_id": "m-err",
            "verified_findings": [
                {"statement": "something", "source_domains": ["a.com"]}
            ],
        }
        mock_run.return_value = fake_report

        mock_pipeline = MagicMock(spec=EnrichedIngestPipeline)
        mock_pipeline.ingest.side_effect = RuntimeError("db locked")

        handler = MissionRunHandler(root=tmp_path)
        cmd = MissionRunCommand(mission_id="m-err", auto_ingest=True)

        with patch(
            "jarvis_engine.memory.store.MemoryStore"
        ), patch(
            "jarvis_engine.ingest.IngestionPipeline", return_value=mock_pipeline
        ):
            result = handler.handle(cmd)

        assert result.return_code == 0
        assert result.report == fake_report
        assert result.ingested_record_id == ""
