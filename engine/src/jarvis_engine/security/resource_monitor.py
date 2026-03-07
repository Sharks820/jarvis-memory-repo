"""Resource usage monitoring with hard caps and z-score anomaly detection.

Tracks cumulative and per-event metrics, enforces configurable hard caps, and
flags statistical anomalies using a rolling z-score window.  All public methods
are thread-safe.
"""

from __future__ import annotations

import collections
import logging
import math
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_CAPS: dict[str, float] = {
    "tokens_per_day": 500_000,
    "api_calls_per_hour": 200,
    "memory_mb": 1024,
}

_MIN_WINDOW_FOR_ZSCORE = 10


class ResourceMonitor:
    """Monitor resource metrics against caps and detect anomalous values.

    Parameters
    ----------
    caps:
        Mapping of metric name to hard-cap value.  Defaults to
        ``tokens_per_day=500000``, ``api_calls_per_hour=200``,
        ``memory_mb=1024``.
    z_threshold:
        Absolute z-score above which a value is considered anomalous.
    window_size:
        Maximum number of recent values kept per metric for z-score
        calculation.
    """

    def __init__(
        self,
        caps: Optional[dict[str, float]] = None,
        z_threshold: float = 3.0,
        window_size: int = 100,
    ) -> None:
        self._caps: dict[str, float] = dict(caps) if caps is not None else dict(_DEFAULT_CAPS)
        self._z_threshold = z_threshold
        self._window_size = window_size

        self._lock = threading.Lock()
        # Cumulative totals per metric (for cap checking).
        self._totals: dict[str, float] = {}
        # Rolling windows per metric (for z-score).
        self._windows: dict[str, collections.deque[float]] = {}
        # Cached anomaly flag per metric (set on each record()).
        self._anomalous: dict[str, bool] = {}
        # Set of metrics that have exceeded their cap.
        self._cap_exceeded: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, metric: str, value: float) -> None:
        """Record a metric observation.

        Accumulates *value* into the running total for *metric*, checks the
        hard cap, appends to the rolling window, and updates the anomaly flag.
        """
        with self._lock:
            # Accumulate total.
            self._totals[metric] = self._totals.get(metric, 0.0) + value

            # Hard cap check.
            cap = self._caps.get(metric)
            if cap is not None and self._totals[metric] > cap:
                if metric not in self._cap_exceeded:
                    logger.warning(
                        "ResourceMonitor: cap exceeded for %s (%.2f > %.2f)",
                        metric,
                        self._totals[metric],
                        cap,
                    )
                self._cap_exceeded.add(metric)

            # Rolling window for z-score.
            window = self._windows.get(metric)
            if window is None:
                window = collections.deque(maxlen=self._window_size)
                self._windows[metric] = window

            # Z-score anomaly check — computed BEFORE appending the current
            # value so the test value is not part of the reference distribution.
            self._anomalous[metric] = self._compute_anomaly(window, value)

            window.append(value)

    def check_cap(self, metric: str) -> tuple[bool, float]:
        """Check whether *metric* is within its hard cap.

        Returns
        -------
        (within_cap, current_value)
            ``within_cap`` is ``True`` when the cumulative value has not
            exceeded the cap (or no cap is defined for the metric).
        """
        with self._lock:
            current = self._totals.get(metric, 0.0)
            cap = self._caps.get(metric)
            if cap is None:
                return True, current
            return current <= cap, current

    def is_anomalous(self, metric: str) -> bool:
        """Return ``True`` if the most recent value for *metric* was anomalous."""
        with self._lock:
            return self._anomalous.get(metric, False)

    def status(self) -> dict:
        """Return a snapshot of all tracked metrics.

        Returns a dict with keys ``metrics``, ``anomalies``, ``cap_exceeded``.
        """
        with self._lock:
            metrics: dict[str, dict] = {}
            anomalies: list[str] = []

            # Collect all known metric names.
            all_metrics = set(self._totals.keys()) | set(self._caps.keys())

            for name in sorted(all_metrics):
                current = self._totals.get(name, 0.0)
                cap = self._caps.get(name)
                window = self._windows.get(name)
                anomalous = self._anomalous.get(name, False)

                utilization_pct = 0.0
                if cap is not None and cap > 0:
                    utilization_pct = (current / cap) * 100.0

                metrics[name] = {
                    "current": current,
                    "cap": cap,
                    "utilization_pct": utilization_pct,
                    "anomalous": anomalous,
                    "values_count": len(window) if window else 0,
                }

                if anomalous:
                    anomalies.append(name)

            return {
                "metrics": metrics,
                "anomalies": anomalies,
                "cap_exceeded": sorted(self._cap_exceeded),
            }

    def reset_daily(self) -> None:
        """Reset cumulative counters and cap-exceeded flags.

        Called at the start of a new day to zero out daily accumulators like
        ``tokens_per_day``.  Rolling windows are preserved so that anomaly
        baselines carry over.
        """
        with self._lock:
            self._totals.clear()
            self._cap_exceeded.clear()
            logger.info("ResourceMonitor: daily counters reset")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_anomaly(self, window: collections.deque[float], value: float) -> bool:
        """Return ``True`` if *value* is a z-score anomaly within *window*.

        Requires at least ``_MIN_WINDOW_FOR_ZSCORE - 1`` previous values in the
        window (the test *value* itself is not yet in the window, so N-1
        reference points are needed to form the 10-observation threshold).
        The *value* being tested must NOT already be in *window* -- the caller
        is responsible for computing the anomaly before appending.
        """
        if len(window) < _MIN_WINDOW_FOR_ZSCORE - 1:
            return False

        n = len(window)
        mean = sum(window) / n
        variance = sum((x - mean) ** 2 for x in window) / n
        stddev = math.sqrt(variance)

        if stddev == 0.0:
            # All values in the window are identical.  Any different value is
            # anomalous by definition (infinite z-score).
            return value != mean

        z_score = abs((value - mean) / stddev)
        return z_score > self._z_threshold
