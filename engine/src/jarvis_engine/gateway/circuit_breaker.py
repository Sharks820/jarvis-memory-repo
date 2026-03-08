"""Circuit breaker and provider health tracking for the LLM gateway.

Implements exponential backoff with three-state circuit breaker logic
(CLOSED / OPEN / HALF_OPEN) per provider, plus real-time health metrics
that feed into routing decisions.

Thread-safe: all state is protected by a threading lock.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit breaker states
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    """Three-state circuit breaker."""

    CLOSED = "closed"        # Normal — requests flow through
    OPEN = "open"            # Failing — skip this provider
    HALF_OPEN = "half_open"  # Testing — allow one probe request


# ---------------------------------------------------------------------------
# Backoff configuration
# ---------------------------------------------------------------------------

#: (consecutive_failures_threshold, cooldown_seconds)
_BACKOFF_TIERS: list[tuple[int, float]] = [
    (3, 30.0),     # After 3 failures: wait 30s
    (5, 120.0),    # After 5 failures: wait 2 min
    (10, 600.0),   # After 10 failures: wait 10 min
]


def _cooldown_for_failures(consecutive: int) -> float:
    """Return the appropriate cooldown in seconds for a failure count."""
    cooldown = 0.0
    for threshold, wait in _BACKOFF_TIERS:
        if consecutive >= threshold:
            cooldown = wait
    return cooldown


# ---------------------------------------------------------------------------
# ProviderHealth
# ---------------------------------------------------------------------------

@dataclass
class ProviderHealth:
    """Real-time health metrics for a single LLM provider."""

    provider: str
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    avg_latency_ms: float = 0.0
    last_success_ts: float = 0.0    # time.monotonic()
    last_failure_ts: float = 0.0    # time.monotonic()
    last_success_iso: str = ""      # ISO timestamp for display
    last_failure_iso: str = ""      # ISO timestamp for display
    circuit_state: CircuitState = CircuitState.CLOSED
    cooldown_until: float = 0.0     # time.monotonic() value

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0
        return self.total_successes / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        """Serialise for API responses."""
        return {
            "provider": self.provider,
            "total_requests": self.total_requests,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "consecutive_failures": self.consecutive_failures,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "last_success": self.last_success_iso,
            "last_failure": self.last_failure_iso,
            "circuit_state": self.circuit_state.value,
        }


# ---------------------------------------------------------------------------
# ProviderHealthTracker
# ---------------------------------------------------------------------------

_MIN_SUCCESS_RATE = 0.50  # Don't route to providers below this


class ProviderHealthTracker:
    """Tracks per-provider health and circuit breaker state.

    Thread-safe.  All methods acquire the internal lock before
    reading or writing provider health data.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: dict[str, ProviderHealth] = {}

    def _ensure(self, provider: str) -> ProviderHealth:
        """Get or create a ProviderHealth entry (must hold lock)."""
        if provider not in self._providers:
            self._providers[provider] = ProviderHealth(provider=provider)
        return self._providers[provider]

    def _now_iso(self) -> str:
        from jarvis_engine._shared import now_iso
        return now_iso()

    def record_success(self, provider: str, latency_ms: float) -> None:
        """Record a successful request for a provider."""
        with self._lock:
            h = self._ensure(provider)
            h.total_requests += 1
            h.total_successes += 1
            h.consecutive_failures = 0
            h.last_success_ts = time.monotonic()
            h.last_success_iso = self._now_iso()

            # Update rolling average latency
            if h.total_successes == 1:
                h.avg_latency_ms = latency_ms
            else:
                # Exponential moving average (alpha=0.2)
                h.avg_latency_ms = h.avg_latency_ms * 0.8 + latency_ms * 0.2

            # Auto-recover: success in HALF_OPEN -> CLOSED
            if h.circuit_state == CircuitState.HALF_OPEN:
                h.circuit_state = CircuitState.CLOSED
                h.cooldown_until = 0.0
                logger.info("Circuit breaker for %s: HALF_OPEN -> CLOSED (recovered)", provider)

    def record_failure(self, provider: str) -> None:
        """Record a failed request for a provider."""
        with self._lock:
            h = self._ensure(provider)
            h.total_requests += 1
            h.total_failures += 1
            h.consecutive_failures += 1
            h.last_failure_ts = time.monotonic()
            h.last_failure_iso = self._now_iso()

            cooldown = _cooldown_for_failures(h.consecutive_failures)
            if cooldown > 0:
                h.circuit_state = CircuitState.OPEN
                h.cooldown_until = time.monotonic() + cooldown
                logger.warning(
                    "Circuit breaker for %s: -> OPEN (consecutive_failures=%d, cooldown=%.0fs)",
                    provider, h.consecutive_failures, cooldown,
                )
            elif h.circuit_state == CircuitState.HALF_OPEN:
                # Failed during probe — back to OPEN with new cooldown
                h.circuit_state = CircuitState.OPEN
                new_cooldown = _cooldown_for_failures(h.consecutive_failures)
                h.cooldown_until = time.monotonic() + max(new_cooldown, 30.0)
                logger.warning(
                    "Circuit breaker for %s: HALF_OPEN -> OPEN (probe failed)", provider,
                )

    def should_skip(self, provider: str) -> bool:
        """Return True if the provider should be skipped (circuit is OPEN).

        Automatically transitions OPEN -> HALF_OPEN when cooldown expires
        to allow a single probe request.
        """
        with self._lock:
            h = self._providers.get(provider)
            if h is None:
                return False  # Unknown provider = never failed

            if h.circuit_state == CircuitState.CLOSED:
                return False

            if h.circuit_state == CircuitState.HALF_OPEN:
                return False  # Allow one probe

            # OPEN: check if cooldown has expired
            if time.monotonic() >= h.cooldown_until:
                h.circuit_state = CircuitState.HALF_OPEN
                logger.info(
                    "Circuit breaker for %s: OPEN -> HALF_OPEN (cooldown expired)", provider,
                )
                return False  # Allow one probe

            return True  # Still in cooldown

    def is_healthy(self, provider: str) -> bool:
        """Return True if a provider is healthy enough for routing.

        A provider is considered unhealthy if:
        - Circuit is OPEN (still in cooldown)
        - Success rate is below 50% (with at least 4 requests)
        """
        with self._lock:
            h = self._providers.get(provider)
            if h is None:
                return True  # Unknown = assume healthy

            if h.circuit_state == CircuitState.OPEN:
                # Check if cooldown expired
                if time.monotonic() < h.cooldown_until:
                    return False

            if h.total_requests >= 4 and h.success_rate < _MIN_SUCCESS_RATE:
                return False

            return True

    def get_health(self, provider: str) -> ProviderHealth | None:
        """Return health data for a single provider (or None)."""
        with self._lock:
            h = self._providers.get(provider)
            if h is None:
                return None
            # Return a copy to avoid race conditions
            return ProviderHealth(
                provider=h.provider,
                total_requests=h.total_requests,
                total_successes=h.total_successes,
                total_failures=h.total_failures,
                consecutive_failures=h.consecutive_failures,
                avg_latency_ms=h.avg_latency_ms,
                last_success_ts=h.last_success_ts,
                last_failure_ts=h.last_failure_ts,
                last_success_iso=h.last_success_iso,
                last_failure_iso=h.last_failure_iso,
                circuit_state=h.circuit_state,
                cooldown_until=h.cooldown_until,
            )

    def all_health(self) -> dict[str, dict]:
        """Return health dicts for all known providers."""
        with self._lock:
            return {name: h.to_dict() for name, h in self._providers.items()}

    def filter_healthy(self, providers: list[str]) -> list[str]:
        """Return only providers that are healthy enough for routing."""
        return [p for p in providers if self.is_healthy(p)]

    def rank_by_health(self, providers: list[str]) -> list[str]:
        """Sort providers by health score (best first).

        Score = success_rate * (1 / (1 + avg_latency_ms/1000))
        Unknown providers get score 0.5 (neutral).
        """
        def _score(provider: str) -> float:
            with self._lock:
                h = self._providers.get(provider)
            if h is None:
                return 0.5
            latency_factor = 1.0 / (1.0 + h.avg_latency_ms / 1000.0)
            return h.success_rate * latency_factor

        return sorted(providers, key=_score, reverse=True)
