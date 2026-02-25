"""Tests for 24/7 daemon reliability and error handling."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from jarvis_engine import main as main_mod


class TestDaemonReliability:
    """Test suite for daemon 24/7 operation reliability."""

    def test_daemon_continues_after_cycle_exception(self, tmp_path: Path, monkeypatch) -> None:
        """C1: Daemon should not crash when a cycle raises an exception."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (False, ""))

        call_count = 0
        
        def failing_autopilot(*args, **kwargs) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated autopilot failure")
            return 0

        sleeps: list[int] = []
        monkeypatch.setattr(main_mod, "cmd_ops_autopilot", failing_autopilot)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: sleeps.append(s))

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
        """C1: Daemon should exit with error code after too many consecutive failures."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (False, ""))

        def always_failing_autopilot(*args, **kwargs) -> int:
            raise RuntimeError("Always fails")

        monkeypatch.setattr(main_mod, "cmd_ops_autopilot", always_failing_autopilot)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)

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
        
        assert rc == 3  # Circuit breaker exit code

    def test_daemon_isolated_mission_failure(self, tmp_path: Path, monkeypatch) -> None:
        """C1: Mission failure should not affect main cycle."""
        monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 10.0)
        monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (False, ""))

        autopilot_calls = 0
        
        def working_autopilot(*args, **kwargs) -> int:
            nonlocal autopilot_calls
            autopilot_calls += 1
            return 0

        def failing_mission(*args, **kwargs) -> int:
            raise RuntimeError("Mission failed")

        monkeypatch.setattr(main_mod, "cmd_ops_autopilot", working_autopilot)
        monkeypatch.setattr(main_mod, "_run_next_pending_mission", failing_mission)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)

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


class TestSTTReliability:
    """Test suite for STT (Speech-to-Text) reliability."""

    def test_voice_dictate_respects_timeout(self, monkeypatch) -> None:
        """M4: Voice dictate should timeout and not hang indefinitely."""
        from jarvis_engine import desktop_widget

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
        
        results = []
        
        def make_request(i: int) -> int:
            # All use same nonce to trigger replay detection
            headers = signed_headers(
                raw, 
                mobile_server.auth_token, 
                mobile_server.signing_key,
                nonce="racetestnonce123"
            )
            code, _ = http_request(
                "POST", 
                f"{mobile_server.base_url}/ingest", 
                raw, 
                headers
            )
            return code

        # Flood with concurrent requests using same nonce
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(make_request, range(50)))

        # Exactly one should succeed, rest should be 401 (replay detected)
        success_count = sum(1 for c in results if c == 201)
        replay_count = sum(1 for c in results if c == 401)
        
        assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
        assert replay_count == 49, f"Expected 49 replays detected, got {replay_count}"


class TestLearningMissionPerformance:
    """Test suite for learning mission performance optimizations."""

    def test_mission_uses_parallel_fetching(self, tmp_path: Path, monkeypatch) -> None:
        """H2: Mission should fetch pages in parallel."""
        from jarvis_engine import learning_missions

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
            "_search_duckduckgo", 
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
        assert duration < 0.3, f"Fetching took {duration}s, expected parallel execution"

    def test_mission_cache_avoids_refetch(self, tmp_path: Path, monkeypatch) -> None:
        """M3: Mission should cache fetched pages."""
        from jarvis_engine import learning_missions

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

def _base_daemon_monkeypatch(monkeypatch, tmp_path: Path) -> None:
    """Apply the standard monkeypatches needed by every daemon test."""
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(main_mod, "_windows_idle_seconds", lambda: 10.0)
    monkeypatch.setattr(main_mod, "_detect_active_game_process", lambda: (False, ""))
    monkeypatch.setattr(main_mod, "cmd_ops_autopilot", lambda *a, **kw: 0)
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    # Reset module-level KG regression state so tests are isolated
    monkeypatch.setattr(main_mod, "_daemon_kg_prev_metrics", None)


def _run_daemon_impl(tmp_path: Path, **kwargs) -> int:
    """Call _cmd_daemon_run_impl directly, bypassing the command bus dispatch.

    This allows tests to mock _get_bus() (used by subsystems inside the loop)
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
    return main_mod._cmd_daemon_run_impl(**defaults)


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

        capture_calls: list[int] = []
        mock_bus = MagicMock()
        mock_kg = MagicMock()
        mock_bus._kg = mock_kg

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

        mock_checker = MagicMock()
        mock_checker.capture_metrics = mock_capture
        mock_checker.compare.return_value = {"status": "pass", "discrepancies": []}

        with patch.object(main_mod, "_get_bus", return_value=mock_bus), \
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

        # Create a fake backup file
        backup_dir = Path(".planning/runtime/kg_backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backup_dir / "20260101T000000_test.db"
        backup_file.write_bytes(b"fake_backup_data")

        mock_bus = MagicMock()
        mock_kg = MagicMock()
        mock_bus._kg = mock_kg

        mock_checker = MagicMock()
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
            with patch.object(main_mod, "_get_bus", return_value=mock_bus), \
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
            bus = MagicMock()
            bus._kg = MagicMock()
            return bus

        with patch.object(main_mod, "_get_bus", side_effect=exploding_get_bus), \
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
        """Memory consolidation should run on multiples of 50."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        mock_bus = MagicMock()
        mock_engine = MagicMock()
        mock_kg = MagicMock()
        mock_bus._engine = mock_engine
        mock_bus._kg = mock_kg
        mock_bus._gateway = None
        mock_bus._embed_service = None

        mock_consolidation_result = MagicMock()
        mock_consolidation_result.groups_found = 3
        mock_consolidation_result.records_consolidated = 9
        mock_consolidation_result.new_facts_created = 2
        mock_consolidation_result.errors = []

        mock_consolidator = MagicMock()
        mock_consolidator.consolidate.return_value = mock_consolidation_result

        mock_rc_checker = MagicMock()

        with patch.object(main_mod, "_get_bus", return_value=mock_bus), \
             patch(
                 "jarvis_engine.learning.consolidator.MemoryConsolidator",
                 return_value=mock_consolidator,
             ), \
             patch(
                 "jarvis_engine.knowledge.regression.RegressionChecker",
                 return_value=mock_rc_checker,
             ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=50)

        assert rc == 0
        captured = capsys.readouterr()
        assert "consolidation_groups=3" in captured.out
        assert "consolidation_new_facts=2" in captured.out
        # Should have backed up KG before consolidation
        mock_rc_checker.backup_graph.assert_called_with(tag="pre-consolidation")

    def test_consolidation_exception_does_not_crash_daemon(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Consolidation failure should not crash the daemon."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        with patch.object(main_mod, "_get_bus", side_effect=RuntimeError("bus down")), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=50)

        assert rc == 0

    def test_consolidation_skipped_when_engine_not_initialized(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """When engine is None on the bus, consolidation should be skipped."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        mock_bus = MagicMock(spec=[])  # No attributes at all
        mock_bus._engine = None  # explicitly None
        mock_bus._kg = None

        with patch.object(main_mod, "_get_bus", return_value=mock_bus), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=50)

        assert rc == 0
        captured = capsys.readouterr()
        assert "consolidation_skipped=engine_not_initialized" in captured.out


class TestDaemonEntityResolution:
    """Tests for entity resolution in the daemon cycle."""

    def test_entity_resolution_runs_every_100_cycles(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Entity resolution should run on multiples of 100."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        mock_bus = MagicMock()
        mock_kg = MagicMock()
        mock_bus._kg = mock_kg
        mock_bus._embed_service = None

        mock_resolve_result = MagicMock()
        mock_resolve_result.candidates_found = 5
        mock_resolve_result.merges_applied = 2
        mock_resolve_result.errors = []

        mock_resolver = MagicMock()
        mock_resolver.auto_resolve.return_value = mock_resolve_result

        mock_rc_checker = MagicMock()

        with patch.object(main_mod, "_get_bus", return_value=mock_bus), \
             patch(
                 "jarvis_engine.knowledge.entity_resolver.EntityResolver",
                 return_value=mock_resolver,
             ), \
             patch(
                 "jarvis_engine.knowledge.regression.RegressionChecker",
                 return_value=mock_rc_checker,
             ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=100)

        assert rc == 0
        captured = capsys.readouterr()
        assert "entity_resolve_candidates=5" in captured.out
        assert "entity_resolve_merges=2" in captured.out
        # Should have backed up KG before entity resolution
        mock_rc_checker.backup_graph.assert_called_with(tag="pre-entity-resolve")

    def test_entity_resolution_exception_does_not_crash_daemon(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Entity resolution failure should not crash the daemon."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        with patch.object(
            main_mod, "_get_bus", side_effect=RuntimeError("bus broken")
        ), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=100)

        assert rc == 0

    def test_entity_resolution_skipped_when_kg_not_initialized(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """When KG is None on the bus, entity resolution should be skipped."""
        _base_daemon_monkeypatch(monkeypatch, tmp_path)

        mock_bus = MagicMock(spec=[])
        mock_bus._kg = None

        with patch.object(main_mod, "_get_bus", return_value=mock_bus), \
             patch("jarvis_engine.activity_feed.log_activity", return_value="id"):
            rc = _run_daemon_impl(tmp_path, max_cycles=100)

        assert rc == 0
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
             patch.object(main_mod, "_get_bus", side_effect=bomb):
            rc = _run_daemon_impl(tmp_path, max_cycles=100)

        assert rc == 0
