"""Anomaly detection for mission and resource patterns.

SEC-04: Tracks mission creation/run frequency, memory growth, and
failed auth attempts.  Emits anomaly events when thresholds are exceeded.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Thresholds
_MAX_MISSIONS_PER_HOUR = 10
_MAX_MEMORY_GROWTH_MB = 100
_MAX_FAILED_AUTH_PER_HOUR = 50


@dataclass(frozen=True)
class AnomalyEvent:
    """A detected anomaly."""

    anomaly_type: str  # mission_flood | memory_growth | auth_flood | resource_spike
    severity: str  # LOW | MEDIUM | HIGH
    detail: str
    timestamp: float = field(default_factory=time.monotonic)
    metrics: dict[str, Any] = field(default_factory=dict)


class AnomalyDetector:
    """Detect anomalous mission, resource, and auth patterns.

    Thread-safe.  Call ``record_*`` methods to feed data, and
    ``check_anomalies()`` to get a list of current anomaly events.

    Parameters
    ----------
    max_missions_per_hour:
        Alert threshold for mission creates/runs per hour.
    max_memory_growth_mb:
        Alert threshold for memory growth in MB per hour.
    max_failed_auth_per_hour:
        Alert threshold for failed auth attempts per hour.
    """

    def __init__(
        self,
        *,
        max_missions_per_hour: int = _MAX_MISSIONS_PER_HOUR,
        max_memory_growth_mb: int = _MAX_MEMORY_GROWTH_MB,
        max_failed_auth_per_hour: int = _MAX_FAILED_AUTH_PER_HOUR,
    ) -> None:
        self._max_missions = max_missions_per_hour
        self._max_memory_mb = max_memory_growth_mb
        self._max_failed_auth = max_failed_auth_per_hour

        self._lock = threading.Lock()
        # Timestamps of mission events in the last hour
        self._mission_events: deque[float] = deque(maxlen=500)
        # Timestamps of failed auth events in the last hour
        self._auth_failures: deque[float] = deque(maxlen=500)
        # Memory snapshots: (timestamp, bytes)
        self._memory_snapshots: deque[tuple[float, int]] = deque(maxlen=120)
        # Recent anomaly events (for status reporting)
        self._recent_anomalies: deque[AnomalyEvent] = deque(maxlen=100)

    def record_mission_event(self) -> None:
        """Record a mission create or run event."""
        now = time.monotonic()
        with self._lock:
            self._mission_events.append(now)

    def record_auth_failure(self) -> None:
        """Record a failed authentication attempt."""
        now = time.monotonic()
        with self._lock:
            self._auth_failures.append(now)

    def record_memory_snapshot(self, memory_bytes: int) -> None:
        """Record current memory usage in bytes."""
        now = time.monotonic()
        with self._lock:
            self._memory_snapshots.append((now, memory_bytes))

    def check_anomalies(self) -> list[AnomalyEvent]:
        """Check all anomaly conditions and return a list of active anomalies.

        Also stores detected anomalies in the internal recent list.
        """
        now = time.monotonic()
        window = 3600.0  # 1 hour
        cutoff = now - window
        anomalies: list[AnomalyEvent] = []

        with self._lock:
            # Mission flood check
            recent_missions = sum(1 for t in self._mission_events if t > cutoff)
            if recent_missions > self._max_missions:
                evt = AnomalyEvent(
                    anomaly_type="mission_flood",
                    severity="HIGH",
                    detail=(
                        f"{recent_missions} missions in last hour "
                        f"(threshold: {self._max_missions})"
                    ),
                    metrics={"count": recent_missions, "threshold": self._max_missions},
                )
                anomalies.append(evt)

            # Auth failure flood check
            recent_auth = sum(1 for t in self._auth_failures if t > cutoff)
            if recent_auth > self._max_failed_auth:
                evt = AnomalyEvent(
                    anomaly_type="auth_flood",
                    severity="HIGH",
                    detail=(
                        f"{recent_auth} failed auth attempts in last hour "
                        f"(threshold: {self._max_failed_auth})"
                    ),
                    metrics={"count": recent_auth, "threshold": self._max_failed_auth},
                )
                anomalies.append(evt)

            # Memory growth check
            if len(self._memory_snapshots) >= 2:
                oldest_in_window = None
                latest = self._memory_snapshots[-1]
                for ts, mem in self._memory_snapshots:
                    if ts > cutoff:
                        oldest_in_window = (ts, mem)
                        break
                if oldest_in_window is not None:
                    growth_bytes = latest[1] - oldest_in_window[1]
                    growth_mb = growth_bytes / (1024 * 1024)
                    if growth_mb > self._max_memory_mb:
                        evt = AnomalyEvent(
                            anomaly_type="memory_growth",
                            severity="MEDIUM",
                            detail=(
                                f"Memory grew {growth_mb:.1f}MB in last hour "
                                f"(threshold: {self._max_memory_mb}MB)"
                            ),
                            metrics={
                                "growth_mb": round(growth_mb, 1),
                                "threshold_mb": self._max_memory_mb,
                            },
                        )
                        anomalies.append(evt)

            # Prune old entries outside the window
            while self._mission_events and self._mission_events[0] < cutoff:
                self._mission_events.popleft()
            while self._auth_failures and self._auth_failures[0] < cutoff:
                self._auth_failures.popleft()
            while (
                self._memory_snapshots
                and self._memory_snapshots[0][0] < cutoff
                and len(self._memory_snapshots) > 2
            ):
                self._memory_snapshots.popleft()

            # Store anomalies
            for a in anomalies:
                self._recent_anomalies.append(a)

        return anomalies

    def get_recent_anomalies(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent anomaly events as dicts (for status reporting)."""
        with self._lock:
            items = list(self._recent_anomalies)[-limit:]
        return [
            {
                "anomaly_type": a.anomaly_type,
                "severity": a.severity,
                "detail": a.detail,
                "metrics": dict(a.metrics),
            }
            for a in items
        ]

    def status(self) -> dict[str, Any]:
        """Return current anomaly detector status."""
        now = time.monotonic()
        cutoff = now - 3600.0
        with self._lock:
            mission_count = sum(1 for t in self._mission_events if t > cutoff)
            auth_count = sum(1 for t in self._auth_failures if t > cutoff)
            anomaly_count = len(self._recent_anomalies)
        return {
            "missions_last_hour": mission_count,
            "auth_failures_last_hour": auth_count,
            "recent_anomalies": anomaly_count,
        }
