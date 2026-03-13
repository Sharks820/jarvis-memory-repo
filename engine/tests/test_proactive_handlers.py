"""Comprehensive tests for proactive handler classes in proactive_handlers.py.

Covers ProactiveCheckHandler, WakeWordStartHandler, CostReductionHandler,
and SelfTestHandler -- including all edge cases, error paths, and fallback
behaviour when dependencies are unavailable.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jarvis_engine.commands.proactive_commands import (
    CostReductionCommand,
    ProactiveCheckCommand,
    SelfTestCommand,
    WakeWordStartCommand,
)
from jarvis_engine.gateway.costs import CostTracker
from jarvis_engine.handlers.proactive_handlers import (
    CostReductionHandler,
    ProactiveCheckHandler,
    SelfTestHandler,
    WakeWordStartHandler,
)
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.proactive import ProactiveEngine
from jarvis_engine.proactive.self_test import AdversarialSelfTest
from jarvis_engine.voice.wakeword import WakeWordDetector


# ---------------------------------------------------------------------------
# ProactiveCheckHandler
# ---------------------------------------------------------------------------


class TestProactiveCheckHandler:
    """Tests for ProactiveCheckHandler."""

    def test_no_engine_returns_not_available(self, tmp_path: Path) -> None:
        handler = ProactiveCheckHandler(root=tmp_path, proactive_engine=None)
        result = handler.handle(ProactiveCheckCommand())
        assert result.message == "Proactive engine not available."
        assert result.alerts_fired == 0

    def test_default_snapshot_path_used_when_empty(self, tmp_path: Path) -> None:
        """When cmd.snapshot_path is empty, the handler constructs the default path."""
        planning = tmp_path / ".planning"
        planning.mkdir()
        snapshot = planning / "ops_snapshot.live.json"
        snapshot.write_text(json.dumps({"status": "ok"}), encoding="utf-8")

        engine = MagicMock(spec=ProactiveEngine)
        engine.evaluate.return_value = []

        handler = ProactiveCheckHandler(root=tmp_path, proactive_engine=engine)
        result = handler.handle(ProactiveCheckCommand(snapshot_path=""))

        assert "No alerts" in result.message
        assert result.alerts_fired == 0
        engine.evaluate.assert_called_once_with({"status": "ok"})

    def test_custom_snapshot_path(self, tmp_path: Path) -> None:
        """Handler reads snapshot from a custom path inside the project root."""
        custom = tmp_path / "custom_snap.json"
        custom.write_text(json.dumps({"data": 1}), encoding="utf-8")

        engine = MagicMock(spec=ProactiveEngine)
        engine.evaluate.return_value = []

        handler = ProactiveCheckHandler(root=tmp_path, proactive_engine=engine)
        result = handler.handle(ProactiveCheckCommand(snapshot_path=str(custom)))

        assert result.alerts_fired == 0
        engine.evaluate.assert_called_once_with({"data": 1})

    def test_snapshot_outside_project_root(self, tmp_path: Path) -> None:
        """Path traversal protection: snapshot path outside root is rejected."""
        handler = ProactiveCheckHandler(root=tmp_path / "sub", proactive_engine=MagicMock(spec=ProactiveEngine))
        (tmp_path / "sub").mkdir()
        result = handler.handle(ProactiveCheckCommand(snapshot_path="/etc/passwd"))
        assert "outside project root" in result.message.lower()

    def test_snapshot_file_not_found(self, tmp_path: Path) -> None:
        path = str(tmp_path / "nonexistent.json")
        handler = ProactiveCheckHandler(root=tmp_path, proactive_engine=MagicMock(spec=ProactiveEngine))
        result = handler.handle(ProactiveCheckCommand(snapshot_path=path))
        assert "not found" in result.message.lower()

    def test_snapshot_invalid_json(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid json", encoding="utf-8")

        handler = ProactiveCheckHandler(root=tmp_path, proactive_engine=MagicMock(spec=ProactiveEngine))
        result = handler.handle(ProactiveCheckCommand(snapshot_path=str(bad_file)))
        assert "invalid json" in result.message.lower()

    def test_alerts_fired_serialized(self, tmp_path: Path) -> None:
        """When evaluate returns alerts, they're serialised to JSON."""
        snap = tmp_path / "snap.json"
        snap.write_text(json.dumps({}), encoding="utf-8")

        alert = SimpleNamespace(
            rule_id="r1",
            message="High CPU",
            priority="high",
            timestamp="2026-02-25T00:00:00",
        )
        engine = MagicMock(spec=ProactiveEngine)
        engine.evaluate.return_value = [alert]

        handler = ProactiveCheckHandler(root=tmp_path, proactive_engine=engine)
        result = handler.handle(ProactiveCheckCommand(snapshot_path=str(snap)))

        assert result.alerts_fired == 1
        assert "Fired 1 alert" in result.message
        assert len(result.alerts) == 1
        assert result.alerts[0]["rule_id"] == "r1"
        assert result.alerts[0]["priority"] == "high"

    def test_multiple_alerts(self, tmp_path: Path) -> None:
        snap = tmp_path / "snap.json"
        snap.write_text(json.dumps({}), encoding="utf-8")

        alerts = [
            SimpleNamespace(rule_id=f"r{i}", message=f"m{i}", priority="low", timestamp="t")
            for i in range(3)
        ]
        engine = MagicMock(spec=ProactiveEngine)
        engine.evaluate.return_value = alerts

        handler = ProactiveCheckHandler(root=tmp_path, proactive_engine=engine)
        result = handler.handle(ProactiveCheckCommand(snapshot_path=str(snap)))

        assert result.alerts_fired == 3
        assert "3 alert" in result.message


# ---------------------------------------------------------------------------
# WakeWordStartHandler
# ---------------------------------------------------------------------------


class TestWakeWordStartHandler:
    """Tests for WakeWordStartHandler."""

    def test_import_error_returns_not_available(self, tmp_path: Path) -> None:
        """When wakeword module can't be imported, returns started=False."""
        handler = WakeWordStartHandler(root=tmp_path)
        with patch.dict("sys.modules", {"jarvis_engine.voice.wakeword": None}):
            result = handler.handle(WakeWordStartCommand())
        assert result.started is False
        assert "not available" in result.message.lower()

    @patch("jarvis_engine.handlers.proactive_handlers.threading")
    def test_start_creates_daemon_thread(self, mock_threading: MagicMock, tmp_path: Path) -> None:
        """Handler creates a daemon thread and starts detection."""
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        mock_threading.Thread.return_value = mock_thread
        mock_threading.Event.return_value = MagicMock(spec=threading.Event)
        mock_threading.Lock.return_value = MagicMock(spec=threading.Lock)

        mock_detector = MagicMock(spec=WakeWordDetector)
        with patch("jarvis_engine.handlers.proactive_handlers.threading", mock_threading):
            # We also need to mock the lazy import of WakeWordDetector
            mock_wakeword_module = MagicMock()
            mock_wakeword_module.WakeWordDetector.return_value = mock_detector
            with patch.dict("sys.modules", {"jarvis_engine.voice.wakeword": mock_wakeword_module}):
                handler = WakeWordStartHandler(root=tmp_path)
                result = handler.handle(WakeWordStartCommand(threshold=0.6))

        assert result.started is True
        assert "started" in result.message.lower()

    def test_duplicate_thread_prevention(self, tmp_path: Path) -> None:
        """If a thread is already alive, handler returns early."""
        handler = WakeWordStartHandler(root=tmp_path)
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        handler._thread = mock_thread

        result = handler.handle(WakeWordStartCommand())
        assert result.started is True
        assert "already running" in result.message.lower()

    def test_stop_sets_event_and_clears(self, tmp_path: Path) -> None:
        """stop() sets the stop event and clears thread references."""
        handler = WakeWordStartHandler(root=tmp_path)
        stop_event = MagicMock(spec=threading.Event)
        handler._stop_event = stop_event
        handler._thread = MagicMock(spec=threading.Thread)

        handler.stop()

        stop_event.set.assert_called_once()
        assert handler._thread is None
        assert handler._stop_event is None

    def test_stop_with_no_thread(self, tmp_path: Path) -> None:
        """stop() does nothing harmful when thread was never started."""
        handler = WakeWordStartHandler(root=tmp_path)
        handler.stop()  # Should not raise
        assert handler._thread is None
        assert handler._stop_event is None

    def test_threshold_passed_to_detector(self, tmp_path: Path) -> None:
        """Custom threshold is forwarded to WakeWordDetector."""
        mock_detector_cls = MagicMock()
        mock_detector_inst = MagicMock(spec=WakeWordDetector)
        mock_detector_cls.return_value = mock_detector_inst
        mock_wakeword = MagicMock()
        mock_wakeword.WakeWordDetector = mock_detector_cls

        with patch.dict("sys.modules", {"jarvis_engine.voice.wakeword": mock_wakeword}):
            handler = WakeWordStartHandler(root=tmp_path)
            handler.handle(WakeWordStartCommand(threshold=0.75))

        mock_detector_cls.assert_called_once_with(threshold=0.75)

    def test_detected_callback_records_follow_up_in_conversation_mode(self, tmp_path: Path) -> None:
        """Wakeword follow-up capture should allow sentence-length pauses."""
        captured_callback: dict[str, object] = {}

        class MockDetector:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self, on_detected, stop_event=None, mic_lock=None):
                captured_callback["fn"] = on_detected
                if stop_event:
                    stop_event.set()

            def pause(self):
                return None

            def resume(self, sd_module=None):
                return None

        fake_audio = MagicMock()
        mock_result = SimpleNamespace(
            text="Jarvis check brain status",
            language="en",
            confidence=0.94,
            backend="deepgram-nova3",
            duration_seconds=1.8,
            segments=[
                {
                    "start": 0.0,
                    "end": 1.8,
                    "text": "Jarvis check brain status",
                    "kind": "utterance",
                }
            ],
        )

        with patch("jarvis_engine.voice.wakeword.WakeWordDetector", MockDetector):
            handler = WakeWordStartHandler(root=tmp_path)
            handler.handle(WakeWordStartCommand(threshold=0.5))

        with patch("jarvis_engine.stt.record_from_microphone", return_value=fake_audio) as mock_record, \
             patch("jarvis_engine.stt.transcribe_smart", return_value=mock_result), \
             patch("jarvis_engine.stt_postprocess._load_personal_vocab", return_value=["Jarvis"]), \
             patch("jarvis_engine.handlers.proactive_handlers._time_mod") as mock_time, \
             patch("jarvis_engine.voice.intents.cmd_voice_run_impl") as mock_run, \
             patch("jarvis_engine.config.repo_root", return_value=tmp_path):
            mock_time.sleep = MagicMock()
            mock_time.time.return_value = 0.0
            callback = captured_callback["fn"]
            assert callable(callback)
            callback()

        mock_record.assert_called_once_with(
            max_duration_seconds=8.0,
            drain_seconds=0.3,
            mode="conversation",
        )
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["text"] == "check brain status"
        assert call_kwargs["utterance"] == {
            "raw_text": "Jarvis check brain status",
            "command_text": "check brain status",
            "language": "en",
            "confidence": 0.94,
            "backend": "deepgram-nova3",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.8,
                    "text": "Jarvis check brain status",
                    "kind": "utterance",
                }
            ],
        }


# ---------------------------------------------------------------------------
# CostReductionHandler
# ---------------------------------------------------------------------------


class TestCostReductionHandler:
    """Tests for CostReductionHandler."""

    def test_no_tracker_returns_not_available(self, tmp_path: Path) -> None:
        handler = CostReductionHandler(root=tmp_path, cost_tracker=None)
        result = handler.handle(CostReductionCommand())
        assert result.message == "Cost tracker not available."

    def test_import_error_returns_not_available(self, tmp_path: Path) -> None:
        tracker = MagicMock(spec=CostTracker)
        handler = CostReductionHandler(root=tmp_path, cost_tracker=tracker)

        with patch.dict("sys.modules", {"jarvis_engine.proactive.cost_tracking": None}):
            result = handler.handle(CostReductionCommand())
        assert "not available" in result.message.lower()

    def test_days_parameter_forwarded(self, tmp_path: Path) -> None:
        """Custom days param is forwarded to cost_tracker.local_vs_cloud_summary."""
        tracker = MagicMock(spec=CostTracker)
        tracker.local_vs_cloud_summary.return_value = {
            "local_pct": 80.0,
            "cloud_cost_usd": 0.05,
            "local_count": 80,
            "total_count": 100,
        }

        mock_cost_mod = MagicMock()
        mock_cost_mod.cost_reduction_snapshot.return_value = None
        mock_cost_mod.load_cost_history.return_value = []
        mock_cost_mod.cost_reduction_trend.return_value = {"trend": "improving"}

        with patch.dict("sys.modules", {"jarvis_engine.proactive.cost_tracking": mock_cost_mod}):
            handler = CostReductionHandler(root=tmp_path, cost_tracker=tracker)
            handler.handle(CostReductionCommand(days=7))

        tracker.local_vs_cloud_summary.assert_called_once_with(days=7)

    def test_successful_cost_reduction(self, tmp_path: Path) -> None:
        """Full happy path: cost_tracker returns stats, trend computed."""
        tracker = MagicMock(spec=CostTracker)
        tracker.local_vs_cloud_summary.return_value = {
            "local_pct": 75.0,
            "cloud_cost_usd": 0.1234,
            "local_count": 75,
            "total_count": 100,
        }

        mock_cost_mod = MagicMock()
        mock_cost_mod.cost_reduction_snapshot.return_value = None
        mock_cost_mod.load_cost_history.return_value = []
        mock_cost_mod.cost_reduction_trend.return_value = {"trend": "stable"}

        with patch.dict("sys.modules", {"jarvis_engine.proactive.cost_tracking": mock_cost_mod}):
            handler = CostReductionHandler(root=tmp_path, cost_tracker=tracker)
            result = handler.handle(CostReductionCommand(days=30))

        assert result.local_pct == 75.0
        assert result.cloud_cost_usd == 0.1234
        assert result.trend == "stable"
        assert "75.0% local" in result.message
        assert "stable" in result.message

    def test_cost_reduction_improving_trend(self, tmp_path: Path) -> None:
        tracker = MagicMock(spec=CostTracker)
        tracker.local_vs_cloud_summary.return_value = {
            "local_pct": 90.0,
            "cloud_cost_usd": 0.01,
            "local_count": 90,
            "total_count": 100,
        }

        mock_cost_mod = MagicMock()
        mock_cost_mod.cost_reduction_snapshot.return_value = None
        mock_cost_mod.load_cost_history.return_value = [{"ts": 1}]
        mock_cost_mod.cost_reduction_trend.return_value = {"trend": "improving"}

        with patch.dict("sys.modules", {"jarvis_engine.proactive.cost_tracking": mock_cost_mod}):
            handler = CostReductionHandler(root=tmp_path, cost_tracker=tracker)
            result = handler.handle(CostReductionCommand())

        assert result.trend == "improving"


# ---------------------------------------------------------------------------
# SelfTestHandler
# ---------------------------------------------------------------------------


class TestSelfTestHandler:
    """Tests for SelfTestHandler."""

    def test_no_engine_returns_not_available(self, tmp_path: Path) -> None:
        handler = SelfTestHandler(root=tmp_path, engine=None, embed_service=MagicMock(spec=EmbeddingService))
        result = handler.handle(SelfTestCommand())
        assert "not available" in result.message.lower()

    def test_no_embed_service_returns_not_available(self, tmp_path: Path) -> None:
        handler = SelfTestHandler(root=tmp_path, engine=MagicMock(spec=MemoryEngine), embed_service=None)
        result = handler.handle(SelfTestCommand())
        assert "not available" in result.message.lower()

    def test_both_none_returns_not_available(self, tmp_path: Path) -> None:
        handler = SelfTestHandler(root=tmp_path, engine=None, embed_service=None)
        result = handler.handle(SelfTestCommand())
        assert "not available" in result.message.lower()

    def test_import_error_returns_not_available(self, tmp_path: Path) -> None:
        handler = SelfTestHandler(
            root=tmp_path, engine=MagicMock(spec=MemoryEngine), embed_service=MagicMock(spec=EmbeddingService)
        )
        with patch.dict("sys.modules", {"jarvis_engine.proactive.self_test": None}):
            result = handler.handle(SelfTestCommand())
        assert "not available" in result.message.lower()

    def test_successful_self_test(self, tmp_path: Path) -> None:
        """Full happy path: quiz runs, results saved, regression checked."""
        quiz_result = {
            "average_score": 0.85,
            "tasks_run": 5,
            "per_task_scores": [0.9, 0.8, 0.85, 0.9, 0.8],
        }
        regression_result = {"regression_detected": False}

        mock_self_test_mod = MagicMock()
        mock_tester = MagicMock(spec=AdversarialSelfTest)
        mock_tester.run_memory_quiz.return_value = quiz_result
        mock_tester.check_regression.return_value = regression_result
        mock_self_test_mod.AdversarialSelfTest.return_value = mock_tester

        with patch.dict(
            "sys.modules", {"jarvis_engine.proactive.self_test": mock_self_test_mod}
        ):
            handler = SelfTestHandler(
                root=tmp_path, engine=MagicMock(spec=MemoryEngine), embed_service=MagicMock(spec=EmbeddingService)
            )
            result = handler.handle(SelfTestCommand(score_threshold=0.6))

        assert result.average_score == 0.85
        assert result.tasks_run == 5
        assert result.regression_detected is False
        assert "0.85" in result.message
        assert "5 tasks" in result.message
        assert "no" in result.message.lower()

    def test_self_test_with_regression_detected(self, tmp_path: Path) -> None:
        quiz_result = {
            "average_score": 0.3,
            "tasks_run": 3,
            "per_task_scores": [0.2, 0.3, 0.4],
        }
        regression_result = {"regression_detected": True}

        mock_self_test_mod = MagicMock()
        mock_tester = MagicMock(spec=AdversarialSelfTest)
        mock_tester.run_memory_quiz.return_value = quiz_result
        mock_tester.check_regression.return_value = regression_result
        mock_self_test_mod.AdversarialSelfTest.return_value = mock_tester

        with patch.dict(
            "sys.modules", {"jarvis_engine.proactive.self_test": mock_self_test_mod}
        ):
            handler = SelfTestHandler(
                root=tmp_path, engine=MagicMock(spec=MemoryEngine), embed_service=MagicMock(spec=EmbeddingService)
            )
            result = handler.handle(SelfTestCommand())

        assert result.regression_detected is True
        assert "YES" in result.message

    def test_score_threshold_forwarded(self, tmp_path: Path) -> None:
        """Custom score_threshold is passed to AdversarialSelfTest."""
        mock_self_test_mod = MagicMock()
        mock_tester = MagicMock(spec=AdversarialSelfTest)
        mock_tester.run_memory_quiz.return_value = {
            "average_score": 0.5,
            "tasks_run": 1,
            "per_task_scores": [0.5],
        }
        mock_tester.check_regression.return_value = {"regression_detected": False}
        mock_self_test_mod.AdversarialSelfTest.return_value = mock_tester

        engine = MagicMock(spec=MemoryEngine)
        embed = MagicMock(spec=EmbeddingService)

        with patch.dict(
            "sys.modules", {"jarvis_engine.proactive.self_test": mock_self_test_mod}
        ):
            handler = SelfTestHandler(root=tmp_path, engine=engine, embed_service=embed)
            handler.handle(SelfTestCommand(score_threshold=0.9))

        mock_self_test_mod.AdversarialSelfTest.assert_called_once_with(
            engine, embed, score_threshold=0.9
        )

    def test_per_task_scores_included(self, tmp_path: Path) -> None:
        scores = [0.6, 0.7, 0.8]
        mock_self_test_mod = MagicMock()
        mock_tester = MagicMock(spec=AdversarialSelfTest)
        mock_tester.run_memory_quiz.return_value = {
            "average_score": 0.7,
            "tasks_run": 3,
            "per_task_scores": scores,
        }
        mock_tester.check_regression.return_value = {"regression_detected": False}
        mock_self_test_mod.AdversarialSelfTest.return_value = mock_tester

        with patch.dict(
            "sys.modules", {"jarvis_engine.proactive.self_test": mock_self_test_mod}
        ):
            handler = SelfTestHandler(
                root=tmp_path, engine=MagicMock(spec=MemoryEngine), embed_service=MagicMock(spec=EmbeddingService)
            )
            result = handler.handle(SelfTestCommand())

        assert result.per_task_scores == scores
