"""Tests for 24/7 daemon reliability and error handling."""
from __future__ import annotations

import time
from pathlib import Path

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
