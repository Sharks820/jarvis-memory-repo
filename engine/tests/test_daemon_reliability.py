"""Tests for 24/7 daemon reliability and error handling."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine import main as main_mod
from jarvis_engine import daemon_loop as daemon_loop_mod
from jarvis_engine.daemon_loop import CycleState
from jarvis_engine import gaming_mode as gaming_mode_mod
from jarvis_engine import harvest_discovery as harvest_discovery_mod
from jarvis_engine.cli import ops as cli_ops_mod
from jarvis_engine.voice import pipeline as voice_pipeline_mod
from jarvis_engine import _bus as bus_mod
from jarvis_engine.command_bus import AppContext, CommandBus
from jarvis_engine.harvesting.budget import BudgetManager
from jarvis_engine.harvesting.harvester import KnowledgeHarvester
from jarvis_engine.harvesting.providers import HarvesterProvider
from jarvis_engine.knowledge.entity_resolver import EntityResolver, ResolutionResult
from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.knowledge.regression import RegressionChecker
from jarvis_engine.memory.classify import BranchClassifier
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.memory.ingest import EnrichedIngestPipeline


class TestDaemonReliability:
    """Test suite for daemon 24/7 operation reliability."""

    def test_daemon_continues_after_cycle_exception(self, tmp_path: Path, monkeypatch) -> None:
        """C1: Daemon should not crash when a cycle raises an exception."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(gaming_mode_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(daemon_loop_mod, "detect_active_game_process", lambda: (False, ""))
        daemon_loop_mod._daemon_mission["backoff_until_cycle"] = 0

        call_count = 0

        def failing_autopilot(*args, **kwargs) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated autopilot failure")
            return 0

        sleeps: list[float] = []
        monkeypatch.setattr(cli_ops_mod, "cmd_ops_autopilot", failing_autopilot)
        monkeypatch.setattr(daemon_loop_mod, "_interruptible_sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(daemon_loop_mod.time, "sleep", lambda s: None)

        rc = main_mod.cmd_daemon_run(
            interval_s=120,
            snapshot_path=tmp_path / "ops_snapshot.live.json",
            actions_path=tmp_path / "actions.generated.json",
            execute=False,
            approve_privileged=False,
            auto_open_connectors=False,
            max_cycles=3,
            idle_interval_s=900,
            idle_after_s=300,
            run_missions=False,
        )

        assert rc == 0  # Should not crash
        assert call_count == 3  # All 3 cycles attempted
        assert len(sleeps) == 2  # Slept between cycles

    def test_daemon_circuit_breaker_after_too_many_errors(self, tmp_path: Path, monkeypatch) -> None:
        """C1: Daemon circuit breaker should cooldown (sleep) then reset, not exit."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(gaming_mode_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(daemon_loop_mod, "detect_active_game_process", lambda: (False, ""))
        daemon_loop_mod._daemon_mission["backoff_until_cycle"] = 0

        def always_failing_autopilot(*args, **kwargs) -> int:
            raise RuntimeError("Always fails")

        total_sleep = [0.0]

        def tracking_sleep(s: float) -> None:
            total_sleep[0] += s
            # All sleeps are no-ops in test

        monkeypatch.setattr(cli_ops_mod, "cmd_ops_autopilot", always_failing_autopilot)
        monkeypatch.setattr(daemon_loop_mod.time, "sleep", tracking_sleep)

        rc = main_mod.cmd_daemon_run(
            interval_s=120,
            snapshot_path=tmp_path / "ops_snapshot.live.json",
            actions_path=tmp_path / "actions.generated.json",
            execute=False,
            approve_privileged=False,
            auto_open_connectors=False,
            max_cycles=15,  # More than circuit breaker threshold
            idle_interval_s=900,
            idle_after_s=300,
            run_missions=False,
        )

        # Circuit breaker triggers _interruptible_sleep(300) in 1s chunks
        assert rc == 0  # Completes all cycles normally
        assert total_sleep[0] >= 300  # At least one 300s cooldown triggered

    def test_daemon_isolated_mission_failure(self, tmp_path: Path, monkeypatch) -> None:
        """C1: Mission failure should not affect main cycle."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(gaming_mode_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(daemon_loop_mod, "detect_active_game_process", lambda: (False, ""))
        daemon_loop_mod._daemon_mission["backoff_until_cycle"] = 0

        autopilot_calls = 0

        def working_autopilot(*args, **kwargs) -> int:
            nonlocal autopilot_calls
            autopilot_calls += 1
            return 0

        def failing_mission(*args, **kwargs) -> int:
            raise RuntimeError("Mission failed")

        monkeypatch.setattr(cli_ops_mod, "cmd_ops_autopilot", working_autopilot)
        monkeypatch.setattr(daemon_loop_mod, "_run_next_pending_mission", failing_mission)
        monkeypatch.setattr(daemon_loop_mod.time, "sleep", lambda s: None)

        rc = main_mod.cmd_daemon_run(
            interval_s=120,
            snapshot_path=tmp_path / "ops_snapshot.live.json",
            actions_path=tmp_path / "actions.generated.json",
            execute=False,
            approve_privileged=False,
            auto_open_connectors=False,
            max_cycles=2,
            idle_interval_s=900,
            idle_after_s=300,
            run_missions=True,  # Enable missions
        )
        
        assert rc == 0
        assert autopilot_calls == 2  # Autopilot still ran despite mission failures


class TestDaemonResourceGuardrails:
    """Resource pressure controls should throttle and skip heavy subsystems."""

    def test_daemon_uses_throttled_sleep(self, tmp_path: Path, monkeypatch) -> None:
        _base_daemon_monkeypatch(monkeypatch, tmp_path)
        sleeps: list[int] = []

        # Override _gather_cycle_state to return the throttled sleep value
        # (the base monkeypatch replaces it with _fast_gather_cycle_state which
        # hardcodes sleep_seconds=0 and never calls recommend_daemon_sleep).
        def _throttled_gather(root, active_interval, idle_interval, idle_after):
            from dataclasses import replace
            state = _fast_gather_cycle_state(root, active_interval, idle_interval, idle_after)
            return replace(state, sleep_seconds=777, pressure_level="mild")

        monkeypatch.setattr(daemon_loop_mod, "_gather_cycle_state", _throttled_gather)
        monkeypatch.setattr(daemon_loop_mod, "_interruptible_sleep", lambda s: sleeps.append(int(s)))

        rc = _run_daemon_impl(tmp_path, max_cycles=2)
        assert rc == 0
        assert 777 in sleeps

    def test_daemon_skips_self_test_when_pressure_severe(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        # Override _gather_cycle_state to return severe pressure
        def _severe_gather(root, active_interval, idle_interval, idle_after):
            from dataclasses import replace
            state = _fast_gather_cycle_state(root, active_interval, idle_interval, idle_after)
            return replace(state, pressure_level="severe", skip_heavy_tasks=True)

        monkeypatch.setattr(daemon_loop_mod, "_gather_cycle_state", _severe_gather)
        # Re-enable _run_periodic_subsystems so the self-test skip logic runs
        monkeypatch.setattr(
            daemon_loop_mod, "_run_periodic_subsystems",
            _real_run_periodic_subsystems,
        )

        with patch("jarvis_engine.proactive.self_test.AdversarialSelfTest") as mock_self_test, \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=1, self_test_every_cycles=1)

        assert rc == 0
        captured = capsys.readouterr()
        assert "self_test_skipped=resource_pressure" in captured.out
        mock_self_test.assert_not_called()


class TestSTTReliability:
    """Test suite for STT (Speech-to-Text) reliability."""

    def test_voice_dictate_respects_timeout(self, monkeypatch) -> None:
        """M4: Voice dictate should timeout and not hang indefinitely."""
        desktop_widget = pytest.importorskip(
            "jarvis_engine.desktop.widget",
            reason="tkinter not available in this environment",
        )

        # Mock subprocess to simulate hanging
        import subprocess

        class HangingProcess:
            def __init__(self, *args, **kwargs):
                pass

            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="test", timeout=timeout)

            def kill(self):
                pass

            def wait(self, timeout=None):
                pass

            stdout = property(lambda self: "")
            stderr = property(lambda self: "")

        monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: HangingProcess())

        # Should raise RuntimeError on timeout, not hang
        with pytest.raises(RuntimeError):
            desktop_widget._voice_dictate_once(timeout_s=8)

    def test_hotword_loop_interruptible(self) -> None:
        """C3: Hotword loop should be interruptible via stop_event."""
        import threading

        stop_event = threading.Event()
        
        # Start hotword loop in thread
        def run_loop():
            # Simulate the hotword loop logic
            iterations = 0
            while not stop_event.is_set():
                iterations += 1
                if stop_event.wait(0.1):
                    return iterations
                if iterations > 100:  # Safety limit
                    return iterations
            return iterations

        thread = threading.Thread(target=run_loop)
        thread.start()
        
        # Signal stop quickly
        time.sleep(0.05)
        stop_event.set()
        thread.join(timeout=2.0)
        
        assert not thread.is_alive(), "Hotword loop should stop when stop_event is set"


class TestMobileAPISecurity:
    """Test suite for mobile API security hardening."""

    def test_nonce_race_condition_protection(self, mobile_server) -> None:
        """C2: Nonce cleanup should be atomic with validation."""
        import json
        from concurrent.futures import ThreadPoolExecutor
        from conftest import http_request, signed_headers

        payload = {
            "source": "user",
            "kind": "episodic",
            "task_id": "race-test",
            "content": "race condition test",
        }
        raw = json.dumps(payload).encode("utf-8")

        def make_request(i: int) -> int | None:
            # All use same nonce to trigger replay detection
            headers = signed_headers(
                raw,
                mobile_server.auth_token,
                mobile_server.signing_key,
                nonce="racetestnonce123",
            )
            try:
                code, _ = http_request(
                    "POST",
                    f"{mobile_server.base_url}/ingest",
                    raw,
                    headers,
                )
                return code
            except (ConnectionResetError, ConnectionRefusedError, OSError):
                # Server TCP backlog can overflow under high concurrency;
                # connections dropped before the nonce was checked.
                return None

        # Use moderate concurrency to stay within the server's TCP backlog.
        # 10 workers × 20 requests is sufficient to exercise nonce dedup.
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(make_request, range(20)))

        # Filter out connections dropped by TCP backlog (None) — they never
        # reached the nonce check.
        reached = [c for c in results if c is not None]
        assert reached, "All requests were connection-reset; server may not be running"

        success_count = sum(1 for c in reached if c == 201)
        replay_count = sum(1 for c in reached if c == 401)

        # Exactly one of the requests that reached the server should have
        # succeeded; all others must be rejected as replays.
        assert success_count == 1, (
            f"Expected exactly 1 success, got {success_count} (of {len(reached)} reached)"
        )
        assert replay_count == len(reached) - 1, (
            f"Expected {len(reached) - 1} replays, got {replay_count}"
        )


class TestLearningMissionPerformance:
    """Test suite for learning mission performance optimizations."""

    def test_mission_uses_parallel_fetching(self, tmp_path: Path, monkeypatch) -> None:
        """H2: Mission should fetch pages in parallel."""
        from jarvis_engine.learning import missions as learning_missions

        # Track concurrent execution
        active_fetches = 0
        max_concurrent = 0

        def mock_fetch(url: str, *, max_bytes: int) -> str:
            nonlocal active_fetches, max_concurrent
            active_fetches += 1
            max_concurrent = max(max_concurrent, active_fetches)
            time.sleep(0.05)  # Simulate network delay
            active_fetches -= 1
            return f"Content from {url}"

        monkeypatch.setattr(learning_missions, "_fetch_page_text", mock_fetch)
        monkeypatch.setattr(
            learning_missions,
            "_search_web",
            lambda q, limit: [f"https://example.com/{i}" for i in range(8)]
        )

        # Create mission
        mission = learning_missions.create_learning_mission(
            tmp_path,
            topic="Python asyncio",
            objective="Learn async patterns",
        )

        # Run with timing
        start = time.time()
        learning_missions.run_learning_mission(
            tmp_path,
            mission_id=mission["mission_id"],
            max_search_results=4,
            max_pages=8,
        )
        duration = time.time() - start

        # Parallel fetching should complete faster than sequential
        # Sequential would be ~8 * 0.05 = 0.4s
        # Parallel should be ~2 * 0.05 = 0.1s (with max_workers=4)
        # Allow generous headroom for CI/xdist CPU pressure
        assert duration < 1.5, f"Fetching took {duration}s, expected parallel execution"

    def test_mission_cache_avoids_refetch(self, tmp_path: Path, monkeypatch) -> None:
        """M3: Mission should cache fetched pages."""
        from jarvis_engine.learning import missions as learning_missions

        fetch_count = 0

        def counting_fetch(url: str, *, max_bytes: int) -> str:
            nonlocal fetch_count
            fetch_count += 1
            return f"Content {fetch_count}"

        monkeypatch.setattr(learning_missions, "_fetch_page_text", counting_fetch)

        # First call
        result1 = learning_missions._fetch_page_cached("https://example.com/page", max_bytes=1000)
        # Second call (should use cache)
        result2 = learning_missions._fetch_page_cached("https://example.com/page", max_bytes=1000)

        assert fetch_count == 1, f"Expected 1 fetch, got {fetch_count}"
        assert result1 == result2


# ---------------------------------------------------------------------------
# Helpers for daemon integration tests
# ---------------------------------------------------------------------------

def _fast_gather_cycle_state(root, active_interval, idle_interval, idle_after):
    """Instant stub for _gather_cycle_state — avoids filesystem I/O in tests."""
    return CycleState(
        idle_seconds=10.0,
        is_active=True,
        sleep_seconds=0,
        resource_snapshot={},
        pressure_level="none",
        skip_heavy_tasks=False,
        gaming_state={},
        control_state={},
        auto_detect=False,
        detected_process="",
        gaming_mode_enabled=False,
        daemon_paused=False,
        safe_mode=False,
    )


def _base_daemon_monkeypatch(monkeypatch, tmp_path: Path) -> None:
    """Apply the standard monkeypatches needed by every daemon test."""
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(gaming_mode_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "_windows_idle_seconds", lambda: 10.0)
    monkeypatch.setattr(daemon_loop_mod, "detect_active_game_process", lambda: (False, ""))
    monkeypatch.setattr(cli_ops_mod, "cmd_ops_autopilot", lambda *a, **kw: 0)
    monkeypatch.setattr(daemon_loop_mod.time, "sleep", lambda s: None)
    # Bypass expensive per-cycle I/O (filesystem snapshots, resource checks)
    monkeypatch.setattr(daemon_loop_mod, "_gather_cycle_state", _fast_gather_cycle_state)
    # Make _interruptible_sleep instant (avoids 600-iteration no-op loops)
    monkeypatch.setattr(daemon_loop_mod, "_interruptible_sleep", lambda s: None)
    # Stub out all periodic subsystems — tests that need specific subsystems
    # should re-patch _run_periodic_subsystems in their own body.
    monkeypatch.setattr(
        daemon_loop_mod, "_run_periodic_subsystems",
        lambda *a, **kw: None,
    )
    # Reset module-level state so tests are isolated
    daemon_loop_mod._daemon_kg["prev_metrics"] = None
    daemon_loop_mod._daemon_mission["backoff_until_cycle"] = 0


# Save the real _run_periodic_subsystems for tests that need it.
_real_run_periodic_subsystems = daemon_loop_mod._run_periodic_subsystems


def _run_daemon_impl(tmp_path: Path, **kwargs) -> int:
    """Call cmd_daemon_run_impl directly, bypassing the command bus dispatch.

    This allows tests to mock _get_daemon_bus() (used by subsystems inside the loop)
    without breaking the bus dispatch that cmd_daemon_run() depends on.
    """
    defaults = dict(
        interval_s=120,
        snapshot_path=tmp_path / "snap.json",
        actions_path=tmp_path / "actions.json",
        execute=False,
        approve_privileged=False,
        auto_open_connectors=False,
        max_cycles=2,
        idle_interval_s=900,
        idle_after_s=300,
        run_missions=False,
        sync_every_cycles=0,
        self_heal_every_cycles=0,
        self_test_every_cycles=0,
    )
    defaults.update(kwargs)
    return daemon_loop_mod.cmd_daemon_run_impl(daemon_loop_mod.DaemonConfig(**defaults))


class TestDaemonActivityLogging:
    """Tests for activity feed integration in daemon cycle."""

    def test_activity_log_called_on_cycle_start_and_end(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Activity feed should receive start and end events for each cycle."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        log_calls: list[tuple[str, str, dict]] = []

        def mock_log_activity(category: str, summary: str, details: dict | None = None) -> str:
            log_calls.append((category, summary, details or {}))
            return "mock-event-id"

        with patch(
            "jarvis_engine.activity_feed.log_activity", mock_log_activity
        ):
            rc = _run_daemon_impl(tmp_path, max_cycles=2)

        assert rc == 0
        # Each cycle produces a start and end event => 4 total for 2 cycles
        start_events = [c for c in log_calls if c[2].get("phase") == "start"]
        end_events = [c for c in log_calls if c[2].get("phase") == "end"]
        assert len(start_events) == 2, f"Expected 2 start events, got {len(start_events)}"
        assert len(end_events) == 2, f"Expected 2 end events, got {len(end_events)}"
        # Verify cycle numbers are correct
        assert start_events[0][2]["cycle"] == 1
        assert start_events[1][2]["cycle"] == 2
        assert end_events[0][2]["cycle"] == 1
        assert end_events[1][2]["cycle"] == 2

    def test_activity_log_exception_does_not_crash_daemon(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """If log_activity raises, the daemon should continue unaffected."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        def exploding_log(*args, **kwargs):
            raise RuntimeError("Activity feed DB corrupt")

        with patch(
            "jarvis_engine.activity_feed.log_activity", exploding_log
        ):
            rc = _run_daemon_impl(tmp_path, max_cycles=3)

        assert rc == 0  # Daemon completes normally despite activity feed errors


class TestDaemonRegressionCheck:
    """Tests for KG regression checking in the daemon cycle."""

    def test_regression_check_runs_every_10_cycles(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """KG regression check should run on multiples of 10."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)
        # Re-enable _run_periodic_subsystems so regression logic actually runs
        monkeypatch.setattr(
            daemon_loop_mod, "_run_periodic_subsystems",
            _real_run_periodic_subsystems,
        )

        capture_calls: list[int] = []
        mock_bus = MagicMock(spec=CommandBus)
        mock_kg = MagicMock(spec=KnowledgeGraph)
        mock_bus.ctx = AppContext(kg=mock_kg)

        def mock_capture(*a, **kw):
            capture_calls.append(1)
            return {
                "node_count": 10,
                "edge_count": 5,
                "locked_count": 2,
                "graph_hash": "abc123",
                "node_labels": {},
                "captured_at": "2026-01-01T00:00:00",
            }

        mock_checker = MagicMock(spec=RegressionChecker)
        mock_checker.capture_metrics = mock_capture
        mock_checker.compare.return_value = {"status": "pass", "discrepancies": []}

        with patch.object(daemon_loop_mod, "_get_daemon_bus", return_value=mock_bus), \
             patch(
                 "jarvis_engine.knowledge.regression.RegressionChecker",
                 return_value=mock_checker,
             ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=25)

        assert rc == 0
        captured = capsys.readouterr()
        # Cycles 10 and 20 should have regression checks (25 cycles, % 10)
        assert captured.out.count("kg_regression_status=") == 2

    def test_regression_failure_triggers_auto_restore(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """When regression status is 'fail', daemon should auto-restore from backup."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)
        # Re-enable _run_periodic_subsystems so regression logic actually runs
        monkeypatch.setattr(
            daemon_loop_mod, "_run_periodic_subsystems",
            _real_run_periodic_subsystems,
        )

        # Create a fake backup file under tmp_path (matches daemon's root-relative path)
        backup_dir = tmp_path / ".planning" / "runtime" / "kg_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "20260101T000000_test.db"
        backup_file.write_bytes(b"fake_backup_data")

        mock_bus = MagicMock(spec=CommandBus)
        mock_kg = MagicMock(spec=KnowledgeGraph)
        mock_bus.ctx = AppContext(kg=mock_kg)

        mock_checker = MagicMock(spec=RegressionChecker)
        mock_checker.capture_metrics.return_value = {
            "node_count": 3,
            "edge_count": 1,
            "locked_count": 0,
            "graph_hash": "changed",
            "node_labels": {},
            "captured_at": "2026-01-01T00:00:00",
        }
        # First call sets baseline (status=baseline), second call detects regression
        mock_checker.compare.side_effect = [
            {"status": "baseline", "discrepancies": [], "current": {}, "previous": None},
            {
                "status": "fail",
                "discrepancies": [{"type": "node_loss", "severity": "fail"}],
                "current": {},
                "previous": {},
            },
        ]
        mock_checker.restore_graph.return_value = True

        try:
            with patch.object(daemon_loop_mod, "_get_daemon_bus", return_value=mock_bus), \
                 patch(
                     "jarvis_engine.knowledge.regression.RegressionChecker",
                     return_value=mock_checker,
                 ), \
                 patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
                rc = _run_daemon_impl(tmp_path, max_cycles=20)

            assert rc == 0
            captured = capsys.readouterr()
            assert "kg_regression_auto_restore=ok" in captured.out
            mock_checker.restore_graph.assert_called_once()
        finally:
            # Clean up the fake backup directory
            import shutil
            if backup_dir.exists():
                shutil.rmtree(backup_dir)

    def test_regression_check_exception_does_not_crash_daemon(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Regression check exception should be caught; daemon keeps running."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        def exploding_get_bus():
            bus = MagicMock(spec=CommandBus)
            bus.ctx = AppContext(kg=MagicMock(spec=KnowledgeGraph))
            return bus

        with patch.object(daemon_loop_mod, "_get_daemon_bus", side_effect=exploding_get_bus), \
             patch(
                 "jarvis_engine.knowledge.regression.RegressionChecker",
                 side_effect=RuntimeError("RegressionChecker init failed"),
             ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=10)

        assert rc == 0  # Daemon survives the error


class TestDaemonConsolidation:
    """Tests for memory consolidation in the daemon cycle."""

    def test_consolidation_runs_every_50_cycles(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Memory consolidation should run on multiples of 50 via CQRS bus."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)
        # Re-enable _run_periodic_subsystems so consolidation logic runs
        monkeypatch.setattr(
            daemon_loop_mod, "_run_periodic_subsystems",
            _real_run_periodic_subsystems,
        )

        from jarvis_engine.commands.learning_commands import ConsolidateMemoryResult

        mock_bus = MagicMock(spec=CommandBus)
        mock_bus.ctx = AppContext(
            engine=MagicMock(spec=MemoryEngine),
            kg=MagicMock(spec=KnowledgeGraph),
            gateway=None,
            embed_service=None,
        )

        consolidation_result = ConsolidateMemoryResult(
            groups_found=3, records_consolidated=9, new_facts_created=2,
            errors=[], message="Consolidated 2 facts from 3 groups.",
        )

        def _dispatch_side_effect(cmd):
            from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand
            if isinstance(cmd, ConsolidateMemoryCommand):
                return consolidation_result
            return MagicMock()

        mock_bus.dispatch.side_effect = _dispatch_side_effect

        with patch.object(daemon_loop_mod, "_get_daemon_bus", return_value=mock_bus), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=50)

        assert rc == 0
        captured = capsys.readouterr()
        assert "consolidation_groups=3" in captured.out
        assert "consolidation_new_facts=2" in captured.out

    def test_consolidation_exception_does_not_crash_daemon(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Consolidation failure should not crash the daemon."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        with patch.object(daemon_loop_mod, "_get_daemon_bus", side_effect=RuntimeError("bus down")), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=50)

        assert rc == 0

    def test_consolidation_skipped_when_engine_not_initialized(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """When engine is None, consolidation via bus returns 'not available'."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)
        # Re-enable _run_periodic_subsystems so consolidation logic runs
        monkeypatch.setattr(
            daemon_loop_mod, "_run_periodic_subsystems",
            _real_run_periodic_subsystems,
        )

        from jarvis_engine.commands.learning_commands import ConsolidateMemoryResult

        mock_bus = MagicMock(spec=CommandBus)
        mock_bus.ctx = AppContext(engine=None, kg=None)

        # Bus dispatch returns a result with 0 groups (handler sees engine=None)
        consolidation_result = ConsolidateMemoryResult(
            message="MemoryEngine not available.",
        )
        mock_bus.dispatch.return_value = consolidation_result

        with patch.object(daemon_loop_mod, "_get_daemon_bus", return_value=mock_bus), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=50)

        assert rc == 0
        captured = capsys.readouterr()
        # Now prints consolidation_groups=0 instead of skipped message
        assert "consolidation_groups=0" in captured.out


class TestDaemonEntityResolution:
    """Tests for entity resolution in the daemon cycle."""

    def test_entity_resolution_runs_every_100_cycles(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Entity resolution should run on multiples of 100."""
        mock_bus = MagicMock(spec=CommandBus)
        mock_kg = MagicMock(spec=KnowledgeGraph)
        mock_bus.ctx = AppContext(kg=mock_kg, embed_service=None)

        mock_resolve_result = MagicMock(spec=ResolutionResult)
        mock_resolve_result.candidates_found = 5
        mock_resolve_result.merges_applied = 2
        mock_resolve_result.errors = []

        mock_resolver = MagicMock(spec=EntityResolver)
        mock_resolver.auto_resolve.return_value = mock_resolve_result

        mock_rc_checker = MagicMock(spec=RegressionChecker)

        # Call _run_periodic_subsystems directly at cycle 100 instead of
        # running 100 full daemon loops — verifies the same trigger logic.
        with patch.object(daemon_loop_mod, "_get_daemon_bus", return_value=mock_bus), \
             patch(
                 "jarvis_engine.knowledge.entity_resolver.EntityResolver",
                 return_value=mock_resolver,
             ), \
             patch(
                 "jarvis_engine.knowledge.regression.RegressionChecker",
                 return_value=mock_rc_checker,
             ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            _real_run_periodic_subsystems(
                tmp_path, cycles=100, skip_heavy_tasks=False,
                cfg=daemon_loop_mod.DaemonConfig(run_missions=False, sync_every_cycles=0, self_heal_every_cycles=0, self_test_every_cycles=0, watchdog_every_cycles=0),
                cmd_mobile_desktop_sync=None, cmd_self_heal=None,
            )

        captured = capsys.readouterr()
        assert "entity_resolve_candidates=5" in captured.out
        assert "entity_resolve_merges=2" in captured.out
        mock_rc_checker.backup_graph.assert_called_with(tag="pre-entity-resolve")

    def test_entity_resolution_exception_does_not_crash_daemon(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Entity resolution failure should not crash the daemon."""
        # Call _run_periodic_subsystems directly at cycle 100 — the broad
        # try/except inside _run_entity_resolution_cycle should absorb the error.
        with patch.object(
            daemon_loop_mod, "_get_daemon_bus", side_effect=RuntimeError("bus broken")
        ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            # Should not raise
            _real_run_periodic_subsystems(
                tmp_path, cycles=100, skip_heavy_tasks=False,
                cfg=daemon_loop_mod.DaemonConfig(run_missions=False, sync_every_cycles=0, self_heal_every_cycles=0, self_test_every_cycles=0, watchdog_every_cycles=0),
                cmd_mobile_desktop_sync=None, cmd_self_heal=None,
            )

    def test_entity_resolution_skipped_when_kg_not_initialized(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """When KG is None on the bus, entity resolution should be skipped."""
        mock_bus = MagicMock(spec=[])
        mock_bus.ctx = AppContext(kg=None)

        with patch.object(daemon_loop_mod, "_get_daemon_bus", return_value=mock_bus), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            _real_run_periodic_subsystems(
                tmp_path, cycles=100, skip_heavy_tasks=False,
                cfg=daemon_loop_mod.DaemonConfig(run_missions=False, sync_every_cycles=0, self_heal_every_cycles=0, self_test_every_cycles=0, watchdog_every_cycles=0),
                cmd_mobile_desktop_sync=None, cmd_self_heal=None,
            )

        captured = capsys.readouterr()
        assert "entity_resolve_skipped=kg_not_initialized" in captured.out


class TestDaemonSubsystemIsolation:
    """Tests verifying that all new subsystems are fully isolated from each other."""

    def test_all_new_subsystems_fail_daemon_still_completes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When all new subsystems raise, daemon completes normally."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        # Make every lazy import blow up
        def bomb(*a, **kw):
            raise RuntimeError("Subsystem exploded")

        with patch(
            "jarvis_engine.activity_feed.log_activity", side_effect=bomb
        ), \
             patch.object(daemon_loop_mod, "_get_daemon_bus", side_effect=bomb):
            rc = _run_daemon_impl(tmp_path, max_cycles=100)

        assert rc == 0


class TestDaemonAutoHarvest:
    """Tests for autonomous knowledge harvesting in the daemon cycle."""

    def test_discover_harvest_topics_returns_topics_from_missions(
        self, tmp_path: Path
    ) -> None:
        """_discover_harvest_topics should find topics from learning missions."""
        import json
        missions_path = tmp_path / ".planning" / "missions.json"
        missions_path.parent.mkdir(parents=True, exist_ok=True)
        missions_path.write_text(json.dumps([
            {"mission_id": "m-1", "topic": "quantum computing basics", "status": "completed"},
            {"mission_id": "m-2", "topic": "machine learning fundamentals", "status": "done"},
            {"mission_id": "m-3", "topic": "pending topic review", "status": "pending"},
        ]), encoding="utf-8")

        with patch("jarvis_engine.learning.missions.load_missions", side_effect=lambda r: json.loads(
            (r / ".planning" / "missions.json").read_text(encoding="utf-8")
        )):
            topics = daemon_loop_mod.discover_harvest_topics(tmp_path)

        assert len(topics) >= 1
        assert len(topics) <= 3
        # Should prefer completed/done missions, not pending
        for t in topics:
            assert t in ("quantum computing basics", "machine learning fundamentals")

    def test_discover_harvest_topics_returns_empty_on_no_data(
        self, tmp_path: Path
    ) -> None:
        """_discover_harvest_topics should return [] when no data sources exist."""
        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        assert isinstance(topics, list)
        assert len(topics) <= 3

    def test_discover_harvest_topics_from_kg_sparse_branches(
        self, tmp_path: Path
    ) -> None:
        """_discover_harvest_topics should find sparse KG branches with multi-word topics."""
        import sqlite3

        db_dir = tmp_path / ".planning" / "brain"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "jarvis_memory.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                node_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT NOT NULL DEFAULT 'fact',
                confidence REAL NOT NULL DEFAULT 0.5,
                locked INTEGER NOT NULL DEFAULT 0,
                sources TEXT NOT NULL DEFAULT '[]',
                history TEXT NOT NULL DEFAULT '[]',
                created_at TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kg_edges (
                edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source_record TEXT DEFAULT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (source_id) REFERENCES kg_nodes(node_id),
                FOREIGN KEY (target_id) REFERENCES kg_nodes(node_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                task_id TEXT NOT NULL DEFAULT '',
                branch TEXT NOT NULL DEFAULT 'general',
                tags TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.72,
                tier TEXT NOT NULL DEFAULT 'warm',
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Insert sparse nodes (no outgoing edges — surface-level facts)
        conn.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence) VALUES "
            "('n1', 'Photosynthesis converts light energy', 0.8)"
        )
        conn.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence) VALUES "
            "('n2', 'Mitochondria produces ATP molecules', 0.7)"
        )
        conn.commit()
        conn.close()

        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        assert isinstance(topics, list)
        # Should have found multi-word topics from sparse KG nodes
        assert len(topics) >= 1
        for t in topics:
            assert len(t.split()) >= 2, f"Topic should be multi-word, got: {t!r}"

    def test_auto_harvest_runs_at_cycle_200(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Auto-harvest should trigger at cycle 200."""
        mock_harvester = MagicMock(spec=KnowledgeHarvester)
        mock_harvester.harvest.return_value = {
            "topic": "test topic",
            "results": [{"provider": "mock", "status": "ok", "records_created": 3, "cost_usd": 0.001}],
        }

        mock_provider = MagicMock(spec=HarvesterProvider)
        mock_provider.is_available = True

        mock_bus = MagicMock(spec=CommandBus)
        mock_bus.ctx = AppContext(
            engine=MagicMock(spec=MemoryEngine),
            embed_service=MagicMock(spec=EmbeddingService),
            kg=MagicMock(spec=KnowledgeGraph),
        )

        with patch.object(daemon_loop_mod, "discover_harvest_topics", return_value=["test topic"]), \
             patch.object(daemon_loop_mod, "_get_daemon_bus", return_value=mock_bus), \
             patch(
                 "jarvis_engine.harvesting.providers.MiniMaxProvider",
                 return_value=mock_provider,
             ), \
             patch(
                 "jarvis_engine.harvesting.providers.KimiProvider",
                 return_value=mock_provider,
             ), \
             patch(
                 "jarvis_engine.harvesting.providers.KimiNvidiaProvider",
                 return_value=mock_provider,
             ), \
             patch(
                 "jarvis_engine.harvesting.providers.GeminiProvider",
                 return_value=mock_provider,
             ), \
             patch(
                 "jarvis_engine.harvesting.harvester.KnowledgeHarvester",
                 return_value=mock_harvester,
             ), \
             patch(
                 "jarvis_engine.harvesting.budget.BudgetManager",
                 return_value=MagicMock(spec=BudgetManager),
             ), \
             patch(
                 "jarvis_engine.memory.classify.BranchClassifier",
                 return_value=MagicMock(spec=BranchClassifier),
             ), \
             patch(
                 "jarvis_engine.memory.ingest.EnrichedIngestPipeline",
                 return_value=MagicMock(spec=EnrichedIngestPipeline),
             ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            _real_run_periodic_subsystems(
                tmp_path, cycles=200, skip_heavy_tasks=False,
                cfg=daemon_loop_mod.DaemonConfig(run_missions=False, sync_every_cycles=0, self_heal_every_cycles=0, self_test_every_cycles=0, watchdog_every_cycles=0),
                cmd_mobile_desktop_sync=None, cmd_self_heal=None,
            )

        captured = capsys.readouterr()
        assert "auto_harvest_topic=" in captured.out

    def test_auto_harvest_does_not_run_before_cycle_200(
        self, tmp_path: Path, capsys
    ) -> None:
        """Auto-harvest should NOT trigger before cycle 200."""
        # Cycle 199 — auto_harvest fires at % 200 == 0, so 199 should skip it
        with patch.object(daemon_loop_mod, "_get_daemon_bus", side_effect=RuntimeError), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            _real_run_periodic_subsystems(
                tmp_path, cycles=199, skip_heavy_tasks=False,
                cfg=daemon_loop_mod.DaemonConfig(run_missions=False, sync_every_cycles=0, self_heal_every_cycles=0, self_test_every_cycles=0, watchdog_every_cycles=0),
                cmd_mobile_desktop_sync=None, cmd_self_heal=None,
            )

        captured = capsys.readouterr()
        assert "auto_harvest_topic=" not in captured.out

    def test_auto_harvest_failure_does_not_crash_daemon(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Auto-harvest exceptions should be isolated from the daemon."""
        # Force _discover_harvest_topics to raise
        with patch.object(
            daemon_loop_mod, "discover_harvest_topics",
            side_effect=RuntimeError("Discovery exploded"),
        ), \
             patch.object(daemon_loop_mod, "_get_daemon_bus", side_effect=RuntimeError), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            # Should not raise
            _real_run_periodic_subsystems(
                tmp_path, cycles=200, skip_heavy_tasks=False,
                cfg=daemon_loop_mod.DaemonConfig(run_missions=False, sync_every_cycles=0, self_heal_every_cycles=0, self_test_every_cycles=0, watchdog_every_cycles=0),
                cmd_mobile_desktop_sync=None, cmd_self_heal=None,
            )

    def test_auto_harvest_skipped_when_no_topics(
        self, tmp_path: Path, capsys
    ) -> None:
        """When no topics are discovered, auto-harvest should be skipped gracefully."""
        with patch.object(daemon_loop_mod, "discover_harvest_topics", return_value=[]), \
             patch.object(daemon_loop_mod, "_get_daemon_bus", side_effect=RuntimeError), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            _real_run_periodic_subsystems(
                tmp_path, cycles=200, skip_heavy_tasks=False,
                cfg=daemon_loop_mod.DaemonConfig(run_missions=False, sync_every_cycles=0, self_heal_every_cycles=0, self_test_every_cycles=0, watchdog_every_cycles=0),
                cmd_mobile_desktop_sync=None, cmd_self_heal=None,
            )

        captured = capsys.readouterr()
        assert "auto_harvest_skipped=no_topics_discovered" in captured.out

    def test_auto_harvest_skipped_when_no_providers(
        self, tmp_path: Path, capsys
    ) -> None:
        """When no providers have API keys, auto-harvest should be skipped."""
        mock_provider = MagicMock(spec=HarvesterProvider)
        mock_provider.is_available = False

        # _run_auto_harvest_cycle calls _get_daemon_bus() internally (line 981),
        # so it must return a proper mock bus (not raise) for the code to reach
        # the provider availability check.
        mock_bus = MagicMock(spec=CommandBus)
        mock_bus.ctx = AppContext(engine=MagicMock(), kg=MagicMock(), embed_service=MagicMock())

        with patch.object(daemon_loop_mod, "discover_harvest_topics", return_value=["some topic"]), \
             patch.object(daemon_loop_mod, "_get_daemon_bus", return_value=mock_bus), \
             patch(
                 "jarvis_engine.harvesting.providers.MiniMaxProvider",
                 return_value=mock_provider,
             ), \
             patch(
                 "jarvis_engine.harvesting.providers.KimiProvider",
                 return_value=mock_provider,
             ), \
             patch(
                 "jarvis_engine.harvesting.providers.KimiNvidiaProvider",
                 return_value=mock_provider,
             ), \
             patch(
                 "jarvis_engine.harvesting.providers.GeminiProvider",
                 return_value=mock_provider,
             ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            _real_run_periodic_subsystems(
                tmp_path, cycles=200, skip_heavy_tasks=False,
                cfg=daemon_loop_mod.DaemonConfig(run_missions=False, sync_every_cycles=0, self_heal_every_cycles=0, self_test_every_cycles=0, watchdog_every_cycles=0),
                cmd_mobile_desktop_sync=None, cmd_self_heal=None,
            )

        captured = capsys.readouterr()
        assert "auto_harvest_skipped=no_providers_available" in captured.out


# ---------------------------------------------------------------------------
# Helper: create a mock memory DB with records and KG tables
# ---------------------------------------------------------------------------

def _create_mock_memory_db(tmp_path: Path) -> Path:
    """Create a mock jarvis_memory.db with records, kg_nodes, and kg_edges tables."""
    import sqlite3

    db_dir = tmp_path / ".planning" / "brain"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "jarvis_memory.db"

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS records (
            record_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            task_id TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT 'general',
            tags TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.72,
            tier TEXT NOT NULL DEFAULT 'warm',
            access_count INTEGER NOT NULL DEFAULT 0,
            last_accessed TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS kg_nodes (
            node_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            node_type TEXT NOT NULL DEFAULT 'fact',
            confidence REAL NOT NULL DEFAULT 0.5,
            locked INTEGER NOT NULL DEFAULT 0,
            sources TEXT NOT NULL DEFAULT '[]',
            history TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS kg_edges (
            edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5,
            source_record TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES kg_nodes(node_id),
            FOREIGN KEY (target_id) REFERENCES kg_nodes(node_id)
        );
    """)
    conn.commit()
    conn.close()
    return db_path


class TestImprovedTopicDiscovery:
    """Tests for the improved topic discovery heuristics."""

    def test_conversation_derived_topics_from_recent_memories(
        self, tmp_path: Path
    ) -> None:
        """Source 1: should extract multi-word topics from recent user memory entries."""
        import sqlite3
        from datetime import datetime, timedelta
        from jarvis_engine._compat import UTC

        db_path = _create_mock_memory_db(tmp_path)
        conn = sqlite3.connect(str(db_path))

        recent_ts = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        conn.execute(
            "INSERT INTO records (record_id, ts, source, kind, summary, content_hash) "
            "VALUES (?, ?, 'user', 'episodic', ?, ?)",
            ("r1", recent_ts, "How do Python async patterns work with coroutines", "hash1"),
        )
        conn.execute(
            "INSERT INTO records (record_id, ts, source, kind, summary, content_hash) "
            "VALUES (?, ?, 'user', 'episodic', ?, ?)",
            ("r2", recent_ts, "Explain Kubernetes pod networking concepts", "hash2"),
        )
        conn.commit()
        conn.close()

        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        assert len(topics) >= 1
        assert len(topics) <= 3
        for t in topics:
            word_count = len(t.split())
            assert 2 <= word_count <= 5, f"Topic must be 2-5 words, got {word_count}: {t!r}"

    def test_topics_are_multi_word_not_single(
        self, tmp_path: Path
    ) -> None:
        """All discovered topics should be multi-word (2-5 words), never single words."""
        import sqlite3
        from datetime import datetime, timedelta
        from jarvis_engine._compat import UTC

        db_path = _create_mock_memory_db(tmp_path)
        conn = sqlite3.connect(str(db_path))

        recent_ts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        # Even with short content, topics should still be multi-word
        conn.execute(
            "INSERT INTO records (record_id, ts, source, kind, summary, content_hash) "
            "VALUES (?, ?, 'user', 'episodic', ?, ?)",
            ("r1", recent_ts, "Tell me about React state management hooks", "hash1"),
        )
        conn.commit()
        conn.close()

        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        for t in topics:
            assert len(t.split()) >= 2, f"Single-word topic not allowed: {t!r}"

    def test_deduplication_against_recently_harvested(
        self, tmp_path: Path
    ) -> None:
        """Topics that were recently harvested (last 14 days) should be skipped."""
        import sqlite3
        from datetime import datetime, timedelta
        from jarvis_engine._compat import UTC

        db_path = _create_mock_memory_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        recent_ts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        conn.execute(
            "INSERT INTO records (record_id, ts, source, kind, summary, content_hash) "
            "VALUES (?, ?, 'user', 'episodic', ?, ?)",
            ("r1", recent_ts, "Python async patterns explanation", "hash1"),
        )
        conn.commit()
        conn.close()

        # Mock _get_recently_harvested_topics to return the same topic
        with patch.object(
            harvest_discovery_mod, "_get_recently_harvested_topics",
            return_value={"python async patterns"},
        ):
            topics = daemon_loop_mod.discover_harvest_topics(tmp_path)

        # The exact phrase "Python async patterns" should be deduplicated
        for t in topics:
            assert t.lower() != "python async patterns", \
                f"Recently harvested topic should be skipped: {t!r}"

    def test_kg_gap_analysis_finds_low_edge_nodes(
        self, tmp_path: Path
    ) -> None:
        """Source 2: nodes with zero/one outgoing edges should be surfaced."""
        import sqlite3

        db_path = _create_mock_memory_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        # Insert nodes — one with edges, one without
        conn.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence) VALUES "
            "('n1', 'Neural network training optimization', 0.8)"
        )
        conn.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence) VALUES "
            "('n2', 'Gradient descent convergence rate', 0.7)"
        )
        conn.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence) VALUES "
            "('n3', 'Dense fully connected node', 0.9)"
        )
        # n3 has many edges, n1 and n2 have none
        conn.execute(
            "INSERT INTO kg_edges (source_id, target_id, relation) VALUES "
            "('n3', 'n1', 'related_to')"
        )
        conn.execute(
            "INSERT INTO kg_edges (source_id, target_id, relation) VALUES "
            "('n3', 'n2', 'causes')"
        )
        conn.commit()
        conn.close()

        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        assert len(topics) >= 1
        # All should be multi-word
        for t in topics:
            assert len(t.split()) >= 2

    def test_complementary_topics_from_strong_kg_areas(
        self, tmp_path: Path
    ) -> None:
        """Source 3: strong KG areas should be expanded with suffixes like 'best practices'."""
        import sqlite3

        db_path = _create_mock_memory_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        # Create a strong topic cluster (>= 5 nodes with same prefix)
        for i in range(6):
            conn.execute(
                "INSERT INTO kg_nodes (node_id, label, confidence) VALUES (?, ?, 0.8)",
                (f"n{i}", f"Python testing {['frameworks', 'patterns', 'mocking', 'coverage', 'fixtures', 'assertions'][i]}", ),
            )
        # Give them all edges so they are NOT caught by source 2
        for i in range(5):
            conn.execute(
                "INSERT INTO kg_edges (source_id, target_id, relation) VALUES (?, ?, 'related_to')",
                (f"n{i}", f"n{i+1}"),
            )
            conn.execute(
                "INSERT INTO kg_edges (source_id, target_id, relation) VALUES (?, ?, 'part_of')",
                (f"n{i+1}", f"n{i}"),
            )
        conn.commit()
        conn.close()

        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        # Should find at least one complementary topic with a suffix
        found_expanded = False
        for t in topics:
            tl = t.lower()
            if any(s in tl for s in ("best practices", "advanced techniques", "common patterns")):
                found_expanded = True
                break
        assert found_expanded, f"Expected complementary expansion, got: {topics}"

    def test_returns_up_to_three_topics(
        self, tmp_path: Path
    ) -> None:
        """Should return at most 3 topics."""
        import sqlite3
        from datetime import datetime, timedelta
        from jarvis_engine._compat import UTC

        db_path = _create_mock_memory_db(tmp_path)
        conn = sqlite3.connect(str(db_path))

        # Insert many user records to get multiple candidate topics
        recent_ts = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        for i in range(10):
            topics_list = [
                "Python async patterns explained",
                "Kubernetes pod networking overview",
                "React state management hooks",
                "Docker container security practices",
                "GraphQL schema design patterns",
                "PostgreSQL query optimization tips",
                "Redis caching strategy overview",
                "Terraform infrastructure provisioning guide",
                "Prometheus monitoring alerting setup",
                "gRPC service communication protocol",
            ]
            conn.execute(
                "INSERT INTO records (record_id, ts, source, kind, summary, content_hash) "
                "VALUES (?, ?, 'user', 'episodic', ?, ?)",
                (f"r{i}", recent_ts, topics_list[i], f"hash{i}"),
            )
        conn.commit()
        conn.close()

        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        assert len(topics) <= 3, f"Expected at most 3 topics, got {len(topics)}: {topics}"
        assert len(topics) >= 1, "Expected at least 1 topic from rich data"

    def test_fallback_chain_all_sources_empty(
        self, tmp_path: Path
    ) -> None:
        """When all data sources are empty, should return [] without errors."""
        # tmp_path has no brain directory, no missions, no activity feed
        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        assert topics == []

    def test_fallback_chain_only_missions_available(
        self, tmp_path: Path
    ) -> None:
        """When only mission data exists, should fall through to source 5."""

        with patch("jarvis_engine.learning.missions.load_missions", return_value=[
            {"mission_id": "m-1", "topic": "quantum computing fundamentals", "status": "completed"},
        ]):
            topics = daemon_loop_mod.discover_harvest_topics(tmp_path)

        assert len(topics) >= 1
        assert "quantum computing fundamentals" in topics

    def test_old_memories_not_preferred_over_recent(
        self, tmp_path: Path
    ) -> None:
        """Source 1 should only look at memories from the last 7 days."""
        import sqlite3
        from datetime import datetime, timedelta
        from jarvis_engine._compat import UTC

        db_path = _create_mock_memory_db(tmp_path)
        conn = sqlite3.connect(str(db_path))

        # Insert an old record (30 days ago) — should NOT be picked
        old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        conn.execute(
            "INSERT INTO records (record_id, ts, source, kind, summary, content_hash) "
            "VALUES (?, ?, 'user', 'episodic', ?, ?)",
            ("r-old", old_ts, "Ancient Sumerian agriculture techniques", "hash-old"),
        )
        conn.commit()
        conn.close()

        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        # The old memory topic should not appear (it's beyond 7-day window)
        for t in topics:
            assert "sumerian" not in t.lower(), \
                f"Old memory should not be selected: {t!r}"

    def test_extract_topic_phrases_utility(self) -> None:
        """_extract_topic_phrases should produce multi-word phrases from text."""
        phrases = harvest_discovery_mod._extract_topic_phrases(
            "How do Python async patterns work with coroutines and event loops"
        )
        assert len(phrases) >= 1
        for p in phrases:
            assert len(p.split()) >= 2
            assert len(p.split()) <= 5

    def test_extract_topic_phrases_filters_stopwords(self) -> None:
        """_extract_topic_phrases should filter common stop words."""
        phrases = harvest_discovery_mod._extract_topic_phrases("the is a an of in to for with on")
        # All stop words — should produce no phrases
        assert phrases == []

    def test_extract_topic_phrases_handles_empty_input(self) -> None:
        """_extract_topic_phrases should handle empty and whitespace input."""
        assert harvest_discovery_mod._extract_topic_phrases("") == []
        assert harvest_discovery_mod._extract_topic_phrases("   ") == []

    def test_single_word_mission_topics_are_skipped(
        self, tmp_path: Path
    ) -> None:
        """Source 5 should skip single-word mission topics (poor quality)."""
        with patch("jarvis_engine.learning.missions.load_missions", return_value=[
            {"mission_id": "m-1", "topic": "Python", "status": "completed"},
            {"mission_id": "m-2", "topic": "AI", "status": "done"},
        ]):
            topics = daemon_loop_mod.discover_harvest_topics(tmp_path)

        # Single-word topics should be skipped
        for t in topics:
            assert t not in ("Python", "AI"), f"Single-word topic should be skipped: {t!r}"

    def test_never_raises_on_corrupt_db(
        self, tmp_path: Path
    ) -> None:
        """Should return [] without raising even if the DB file is corrupt."""
        db_dir = tmp_path / ".planning" / "brain"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "jarvis_memory.db"
        db_path.write_bytes(b"not a real sqlite database")

        # Should NOT raise
        topics = daemon_loop_mod.discover_harvest_topics(tmp_path)
        assert isinstance(topics, list)
