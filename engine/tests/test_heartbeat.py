"""Tests for HeartbeatMonitor — dead man's switch for engine liveness."""

from __future__ import annotations

import threading

import pytest

from jarvis_engine.security.heartbeat import HeartbeatMonitor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def monitor() -> HeartbeatMonitor:
    """Monitor with a long interval so the background thread never fires
    during unit tests.  Tests that need missed-beat behaviour call
    ``_check_heartbeat()`` directly, which is both deterministic and instant."""
    m = HeartbeatMonitor(interval=60.0, max_missed=3)
    yield m
    m.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBasicHeartbeat:
    """beat() keeps the monitor healthy."""

    def test_healthy_after_beat(self, monitor: HeartbeatMonitor) -> None:
        monitor.start()
        monitor.beat()
        assert monitor.is_healthy() is True

    def test_healthy_immediately_after_start(self, monitor: HeartbeatMonitor) -> None:
        """Monitor is healthy right after start (no misses yet)."""
        monitor.start()
        assert monitor.is_healthy() is True


class TestMissedHeartbeat:
    """Missed heartbeats trigger the failure callback."""

    def test_missed_heartbeat_triggers_failure(self) -> None:
        failure_event = threading.Event()

        def on_fail() -> None:
            failure_event.set()

        # Use a long interval so the background thread never fires in this test;
        # we drive the watchdog logic by calling _check_heartbeat() directly.
        m = HeartbeatMonitor(interval=60.0, max_missed=2, on_failure=on_fail)
        try:
            m._check_heartbeat()  # miss 1
            m._check_heartbeat()  # miss 2 — triggers callback synchronously
            assert failure_event.is_set(), "on_failure callback was not called"
            assert m.is_healthy() is False
        finally:
            m.stop()

    def test_missed_count_increments(self) -> None:
        m = HeartbeatMonitor(interval=60.0, max_missed=10)
        try:
            m._check_heartbeat()
            m._check_heartbeat()
            status = m.status()
            assert status["missed_count"] == 2
        finally:
            m.stop()


class TestStatusReport:
    """status() returns a complete dictionary."""

    def test_status_keys(self, monitor: HeartbeatMonitor) -> None:
        monitor.start()
        monitor.beat()
        s = monitor.status()
        expected_keys = {"running", "healthy", "missed_count", "last_beat", "uptime"}
        assert set(s.keys()) == expected_keys

    def test_status_running_flag(self, monitor: HeartbeatMonitor) -> None:
        assert monitor.status()["running"] is False
        monitor.start()
        assert monitor.status()["running"] is True

    def test_status_uptime_increases(self, monitor: HeartbeatMonitor) -> None:
        monitor.start()
        s1 = monitor.status()
        s2 = monitor.status()
        # uptime is non-negative after start, and the second call is >= the first
        assert isinstance(s1["uptime"], float)
        assert s1["uptime"] >= 0.0
        assert s2["uptime"] >= s1["uptime"]

    def test_status_last_beat_none_before_first_beat(
        self, monitor: HeartbeatMonitor
    ) -> None:
        monitor.start()
        s = monitor.status()
        assert s["last_beat"] is None


class TestStopCleansUp:
    """stop() terminates the watchdog thread."""

    def test_thread_stops(self, monitor: HeartbeatMonitor) -> None:
        monitor.start()
        monitor.beat()
        assert monitor.status()["running"] is True
        # stop() sets _running=False and joins the watchdog thread before
        # returning, so no sleep is needed here.
        monitor.stop()
        assert monitor.status()["running"] is False

    def test_double_stop_is_safe(self, monitor: HeartbeatMonitor) -> None:
        monitor.start()
        monitor.stop()
        monitor.stop()  # Should not raise
        assert monitor.status()["running"] is False

    def test_double_start_is_safe(self, monitor: HeartbeatMonitor) -> None:
        monitor.start()
        monitor.start()  # Should not raise or create duplicate threads
        assert monitor.status()["running"] is True
        monitor.stop()


class TestBeatResetsCounter:
    """Sending a beat after misses resets the missed counter."""

    def test_beat_resets_missed_count(self) -> None:
        m = HeartbeatMonitor(interval=60.0, max_missed=10)
        try:
            # Simulate 3 missed check cycles without starting the real thread
            m._check_heartbeat()
            m._check_heartbeat()
            m._check_heartbeat()
            assert m.status()["missed_count"] == 3
            m.beat()
            assert m.status()["missed_count"] == 0
            assert m.is_healthy() is True
        finally:
            m.stop()

    def test_beat_restores_health_after_failure(self) -> None:
        failure_event = threading.Event()

        def on_fail() -> None:
            failure_event.set()

        m = HeartbeatMonitor(interval=60.0, max_missed=2, on_failure=on_fail)
        try:
            # Trigger failure synchronously
            m._check_heartbeat()  # miss 1
            m._check_heartbeat()  # miss 2 — callback fires
            assert failure_event.is_set(), "on_failure should have been called"
            assert m.is_healthy() is False
            # Beat should restore health
            m.beat()
            assert m.is_healthy() is True
            assert m.status()["missed_count"] == 0
        finally:
            m.stop()


class TestThreadSafety:
    """Concurrent access does not corrupt state."""

    def test_concurrent_beats(self) -> None:
        m = HeartbeatMonitor(interval=60.0, max_missed=5)
        try:
            m.start()
            errors: list[Exception] = []

            def beat_loop() -> None:
                try:
                    for _ in range(50):
                        m.beat()
                        m.is_healthy()
                        m.status()
                except (RuntimeError, ValueError, AttributeError) as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=beat_loop) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)
            assert errors == [], f"Concurrent access errors: {errors}"
        finally:
            m.stop()
