"""Dead man's switch — HeartbeatMonitor.

Runs a daemon watchdog thread that expects periodic ``beat()`` calls from the
engine's main loop.  If ``max_missed`` consecutive heartbeat intervals elapse
without a beat, the monitor marks itself unhealthy and invokes the optional
``on_failure`` callback (e.g. safe shutdown).

All public state access is protected by a threading lock.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional, TypedDict

logger = logging.getLogger(__name__)


class HeartbeatStatus(TypedDict):
    """Result from :meth:`HeartbeatMonitor.status`."""

    running: bool
    healthy: bool
    missed_count: int
    last_beat: float | None
    uptime: float


class HeartbeatMonitor:
    """Watchdog that detects engine liveness failures.

    Parameters
    ----------
    interval:
        Expected heartbeat interval in seconds.
    max_missed:
        Number of consecutive missed intervals before triggering failure.
    on_failure:
        Optional callback invoked (from the watchdog thread) when failure is
        detected.  Must be thread-safe.
    """

    def __init__(
        self,
        interval: float = 30.0,
        max_missed: int = 3,
        on_failure: Optional[Callable[[], None]] = None,
    ) -> None:
        self._interval = interval
        self._max_missed = max_missed
        self._on_failure = on_failure

        # Guarded by _lock ---------------------------------------------------
        self._lock = threading.Lock()
        self._last_beat: Optional[float] = None
        self._missed_count: int = 0
        self._healthy: bool = True
        self._running: bool = False
        self._started_at: Optional[float] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # Public API

    def start(self) -> None:
        """Start the watchdog daemon thread.

        Calling ``start()`` on an already-running monitor is a safe no-op.
        """
        with self._lock:
            if self._running:
                return
            self._running = True
            self._healthy = True
            self._missed_count = 0
            self._started_at = time.monotonic()
            self._stop_event.clear()

        t = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="heartbeat-watchdog"
        )
        self._thread = t
        t.start()
        logger.info(
            "HeartbeatMonitor started (interval=%.1fs, max_missed=%d)",
            self._interval,
            self._max_missed,
        )

    def stop(self) -> None:
        """Stop the watchdog thread.

        Calling ``stop()`` on an already-stopped monitor is a safe no-op.
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._interval * 2)
        self._thread = None
        logger.info("HeartbeatMonitor stopped")

    def beat(self) -> None:
        """Record a heartbeat, resetting the missed counter and health flag."""
        with self._lock:
            self._last_beat = time.monotonic()
            self._missed_count = 0
            self._healthy = True

    def is_healthy(self) -> bool:
        """Return ``True`` if heartbeats are arriving on schedule."""
        with self._lock:
            return self._healthy

    def status(self) -> HeartbeatStatus:
        """Return a snapshot of monitor state.

        Keys: ``running``, ``healthy``, ``missed_count``, ``last_beat``,
        ``uptime``.
        """
        with self._lock:
            now = time.monotonic()
            return {
                "running": self._running,
                "healthy": self._healthy,
                "missed_count": self._missed_count,
                "last_beat": self._last_beat,
                "uptime": (now - self._started_at)
                if self._started_at is not None
                else 0.0,
            }

    # Internal watchdog loop

    def _watchdog_loop(self) -> None:
        """Background loop that checks for missed heartbeats."""
        logger.debug("Watchdog thread started")
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break
            self._check_heartbeat()
        logger.debug("Watchdog thread exiting")

    def _check_heartbeat(self) -> None:
        """Check if heartbeat is overdue and fire callback if threshold reached."""
        fire_callback = False
        with self._lock:
            now = time.monotonic()
            # Only count a miss if the last beat is actually overdue.
            # A beat is overdue when no beat has been received yet or the
            # elapsed time since the last beat exceeds the expected interval.
            if self._last_beat is None or (now - self._last_beat) >= self._interval:
                self._missed_count += 1
                if self._missed_count >= self._max_missed:
                    if self._healthy:
                        logger.warning(
                            "HeartbeatMonitor: %d consecutive misses — marking UNHEALTHY",
                            self._missed_count,
                        )
                    self._healthy = False
                    fire_callback = True

        # Invoke callback outside the lock to avoid potential deadlocks.
        if fire_callback and self._on_failure is not None:
            try:
                self._on_failure()
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.exception("on_failure callback raised an exception: %s", exc)
