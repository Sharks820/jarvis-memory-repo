"""Voice pipeline telemetry — full latency instrumentation and SLO enforcement.

Instruments every stage of the voice pipeline with nanosecond-precision
timestamps and computes derived metrics (capture-to-transcript, end-to-end
latency, VAD duration, etc.).  SLO targets are enforced with rolling window
percentile tracking.

Thread safety: all mutations go through a single ``threading.Lock``.
Per-utterance state uses ``threading.local()`` so concurrent pipeline
threads do not corrupt each other's in-flight records.
Singleton: ``get_voice_telemetry()`` returns a module-level singleton.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


# TypedDicts for structured returns


class LatencyStatsDict(TypedDict):
    """Return type for ``get_latency_stats()``."""

    p50_ms: float
    p95_ms: float
    p99_ms: float
    sample_count: int
    slo_violations: list[dict[str, Any]]


class HealthEventDict(TypedDict):
    """Return type for voice pipeline health events."""

    utterances_total: int
    success_rate: float
    avg_confidence: float
    backend_distribution: dict[str, int]
    fallback_trigger_rate: float
    avg_latency_per_backend: dict[str, float]


class LatencyEndpointDict(TypedDict):
    """Return type for the ``GET /voice/latency`` endpoint."""

    p50_ms: float
    p95_ms: float
    p99_ms: float
    sample_count: int
    slo_violations: list[dict[str, Any]]
    backend_distribution: dict[str, int]
    health: dict[str, Any]


# Stage names (constants)

STAGE_VAD_SPEECH_ONSET = "vad_speech_onset_ts"
STAGE_VAD_SPEECH_END = "vad_speech_end_ts"
STAGE_WAKE_WORD_DETECTED = "wake_word_detected_ts"
STAGE_TRANSCRIPTION_START = "transcription_start_ts"
STAGE_TRANSCRIPTION_END = "transcription_end_ts"
STAGE_INTENT_CLASSIFICATION = "intent_classification_ts"
STAGE_COMMAND_DISPATCH = "command_dispatch_ts"
STAGE_RESPONSE_READY = "response_ready_ts"
STAGE_TTS_START = "tts_start_ts"
STAGE_TTS_END = "tts_end_ts"

ALL_STAGES = (
    STAGE_VAD_SPEECH_ONSET,
    STAGE_VAD_SPEECH_END,
    STAGE_WAKE_WORD_DETECTED,
    STAGE_TRANSCRIPTION_START,
    STAGE_TRANSCRIPTION_END,
    STAGE_INTENT_CLASSIFICATION,
    STAGE_COMMAND_DISPATCH,
    STAGE_RESPONSE_READY,
    STAGE_TTS_START,
    STAGE_TTS_END,
)


# SLO targets

_SLO_CAPTURE_TO_TRANSCRIPT_P50_MS = 1500.0
_SLO_CAPTURE_TO_TRANSCRIPT_P95_MS = 4000.0
_SLO_END_TO_END_P50_MS = 3000.0
_SLO_END_TO_END_P95_MS = 8000.0
_SLO_CONSECUTIVE_THRESHOLD = 10

_MAX_LATENCY_SAMPLES = 500
_HEALTH_EMIT_INTERVAL = 100  # every N utterances


# Utterance record


class _UtteranceRecord:
    """Holds timestamps and metadata for a single voice utterance."""

    __slots__ = ("timestamps", "backend", "confidence", "success")

    def __init__(self) -> None:
        self.timestamps: dict[str, int] = {}
        self.backend: str = ""
        self.confidence: float = 0.0
        self.success: bool = True


# VoiceTelemetry


class VoiceTelemetry:
    """Full pipeline latency telemetry with SLO enforcement.

    Tracks timestamps at each pipeline stage using ``time.perf_counter_ns()``
    for nanosecond precision, computes derived metrics, and enforces SLO
    targets with rolling percentile calculations.

    Per-utterance state (``_current``) is stored in ``threading.local()``
    so concurrent pipeline threads do not corrupt each other's records.

    Parameters
    ----------
    max_samples : int
        Maximum number of latency samples to retain in the rolling window.
    health_interval : int
        Emit health events every *health_interval* utterances.
    """

    def __init__(
        self,
        max_samples: int = _MAX_LATENCY_SAMPLES,
        health_interval: int = _HEALTH_EMIT_INTERVAL,
    ) -> None:
        self._lock = threading.Lock()
        self._max_samples = max(10, max_samples)
        self._health_interval = max(1, health_interval)

        # Per-thread utterance state (avoids cross-thread corruption)
        self._local = threading.local()

        # Rolling latency deques
        self._capture_to_transcript_ms: deque[float] = deque(maxlen=self._max_samples)
        self._transcript_to_response_ms: deque[float] = deque(maxlen=self._max_samples)
        self._end_to_end_ms: deque[float] = deque(maxlen=self._max_samples)
        self._vad_duration_ms: deque[float] = deque(maxlen=self._max_samples)

        # Backend tracking
        self._backend_counts: dict[str, int] = {}
        self._backend_latency_sums: dict[str, float] = {}
        self._backend_latency_counts: dict[str, int] = {}

        # Utterance tracking
        self._utterances_total: int = 0
        self._utterances_success: int = 0
        self._total_confidence: float = 0.0
        self._fallback_triggers: int = 0

        # SLO violation tracking
        self._slo_violations: list[dict[str, Any]] = []
        self._consecutive_slo_breaches: int = 0

    # Per-thread current utterance accessor

    @property
    def _current(self) -> _UtteranceRecord:
        """Return the per-thread current utterance record, creating if needed."""
        rec = getattr(self._local, "current", None)
        if rec is None:
            rec = _UtteranceRecord()
            self._local.current = rec
        return rec

    @_current.setter
    def _current(self, value: _UtteranceRecord) -> None:
        self._local.current = value

    # Stage marking

    def begin_utterance(self) -> None:
        """Start tracking a new utterance.  Resets current timestamps."""
        self._local.current = _UtteranceRecord()

    def mark_stage(self, stage: str) -> None:
        """Record a nanosecond timestamp for the given pipeline stage.

        Parameters
        ----------
        stage : str
            One of the ``STAGE_*`` constants defined in this module.
        """
        ts = time.perf_counter_ns()
        self._current.timestamps[stage] = ts

    def set_backend(self, backend: str) -> None:
        """Record the STT backend used for the current utterance.

        Parameters
        ----------
        backend : str
            Backend name (e.g. ``"parakeet"``, ``"deepgram"``).
        """
        self._current.backend = backend

    def set_confidence(self, confidence: float) -> None:
        """Record the STT confidence for the current utterance.

        Parameters
        ----------
        confidence : float
            Confidence score in [0.0, 1.0].
        """
        self._current.confidence = confidence

    def set_success(self, success: bool) -> None:
        """Mark the current utterance as successful or failed.

        Parameters
        ----------
        success : bool
            Whether the utterance was handled successfully.
        """
        self._current.success = success

    def mark_fallback(self) -> None:
        """Increment the fallback trigger counter.

        Called when the primary STT backend fails and a fallback is used.
        """
        with self._lock:
            self._fallback_triggers += 1

    # Utterance completion

    def finish_utterance(self) -> dict[str, float]:
        """Finalize the current utterance, compute derived metrics, and check SLOs.

        Returns a dict of computed latency metrics (in milliseconds) for this
        utterance.  Missing metrics are omitted.
        """
        # Snapshot the per-thread record before acquiring the shared lock
        record = self._current

        should_emit_health = False
        health_data: HealthEventDict | None = None
        slo_alert_violations: list[dict[str, Any]] | None = None

        with self._lock:
            ts = record.timestamps
            metrics: dict[str, float] = {}

            # Derived metrics (ns -> ms), clamped to 0
            onset = ts.get(STAGE_VAD_SPEECH_ONSET)
            trans_end = ts.get(STAGE_TRANSCRIPTION_END)
            resp_ready = ts.get(STAGE_RESPONSE_READY)
            tts_end = ts.get(STAGE_TTS_END)
            vad_end = ts.get(STAGE_VAD_SPEECH_END)

            if onset is not None and trans_end is not None:
                val = max(0.0, (trans_end - onset) / 1_000_000)
                metrics["capture_to_transcript_ms"] = val
                self._capture_to_transcript_ms.append(val)

            if trans_end is not None and resp_ready is not None:
                val = max(0.0, (resp_ready - trans_end) / 1_000_000)
                metrics["transcript_to_response_ms"] = val
                self._transcript_to_response_ms.append(val)

            if onset is not None and tts_end is not None:
                val = max(0.0, (tts_end - onset) / 1_000_000)
                metrics["end_to_end_ms"] = val
                self._end_to_end_ms.append(val)

            if onset is not None and vad_end is not None:
                val = max(0.0, (vad_end - onset) / 1_000_000)
                metrics["vad_duration_ms"] = val
                self._vad_duration_ms.append(val)

            # Backend tracking
            backend = record.backend
            if backend:
                self._backend_counts[backend] = self._backend_counts.get(backend, 0) + 1
                if "capture_to_transcript_ms" in metrics:
                    latency = metrics["capture_to_transcript_ms"]
                    self._backend_latency_sums[backend] = (
                        self._backend_latency_sums.get(backend, 0.0) + latency
                    )
                    self._backend_latency_counts[backend] = (
                        self._backend_latency_counts.get(backend, 0) + 1
                    )

            # Utterance counters
            self._utterances_total += 1
            if record.success:
                self._utterances_success += 1
            self._total_confidence += record.confidence

            # SLO checks (returns violations to alert outside lock)
            slo_alert_violations = self._check_slo_violations()

            # Health event emission
            should_emit_health = (
                self._utterances_total > 0
                and self._utterances_total % self._health_interval == 0
            )
            if should_emit_health:
                health_data = self._build_health_event_unlocked()

        # Emit events outside the lock to avoid deadlocks with ActivityFeed
        if slo_alert_violations is not None:
            self._emit_slo_alert(slo_alert_violations)
        if should_emit_health and health_data is not None:
            self._emit_health_event(health_data)

        return metrics

    # SLO enforcement

    def _check_slo_violations(self) -> list[dict[str, Any]] | None:
        """Check SLO targets and record violations.

        Must be called while ``self._lock`` is held.

        Returns
        -------
        list or None
            A snapshot of recent violations to alert on if the sustained
            breach threshold was crossed, otherwise ``None``.  The caller
            must emit the alert **outside** the lock.
        """
        violations: list[dict[str, Any]] = []

        # Capture-to-transcript SLO
        if len(self._capture_to_transcript_ms) >= 2:
            p50 = _percentile(self._capture_to_transcript_ms, 50)
            p95 = _percentile(self._capture_to_transcript_ms, 95)
            if p50 > _SLO_CAPTURE_TO_TRANSCRIPT_P50_MS:
                violations.append(
                    {
                        "metric": "capture_to_transcript_p50",
                        "target_ms": _SLO_CAPTURE_TO_TRANSCRIPT_P50_MS,
                        "actual_ms": round(p50, 1),
                    }
                )
            if p95 > _SLO_CAPTURE_TO_TRANSCRIPT_P95_MS:
                violations.append(
                    {
                        "metric": "capture_to_transcript_p95",
                        "target_ms": _SLO_CAPTURE_TO_TRANSCRIPT_P95_MS,
                        "actual_ms": round(p95, 1),
                    }
                )

        # End-to-end SLO
        if len(self._end_to_end_ms) >= 2:
            p50 = _percentile(self._end_to_end_ms, 50)
            p95 = _percentile(self._end_to_end_ms, 95)
            if p50 > _SLO_END_TO_END_P50_MS:
                violations.append(
                    {
                        "metric": "end_to_end_p50",
                        "target_ms": _SLO_END_TO_END_P50_MS,
                        "actual_ms": round(p50, 1),
                    }
                )
            if p95 > _SLO_END_TO_END_P95_MS:
                violations.append(
                    {
                        "metric": "end_to_end_p95",
                        "target_ms": _SLO_END_TO_END_P95_MS,
                        "actual_ms": round(p95, 1),
                    }
                )

        alert_violations: list[dict[str, Any]] | None = None

        if violations:
            self._slo_violations.extend(violations)
            # Cap stored violations
            if len(self._slo_violations) > 200:
                self._slo_violations = self._slo_violations[-200:]
            self._consecutive_slo_breaches += 1
        else:
            self._consecutive_slo_breaches = 0

        # Alert on sustained SLO breach — return violations for emission outside lock
        if self._consecutive_slo_breaches >= _SLO_CONSECUTIVE_THRESHOLD:
            alert_violations = list(self._slo_violations[-10:])
            self._consecutive_slo_breaches = 0  # reset after alert

        return alert_violations

    def _emit_slo_alert(self, violations: list[dict[str, Any]]) -> None:
        """Emit an activity feed alert for sustained SLO violations.

        Called **outside** the lock to avoid deadlocks with ActivityFeed.
        """
        try:
            from jarvis_engine.memory.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.VOICE_PIPELINE,
                "Voice pipeline SLO violation sustained for 10+ consecutive samples",
                {
                    "event": "slo_alert",
                    "consecutive_breaches": _SLO_CONSECUTIVE_THRESHOLD,
                    "violations": violations[:5],
                },
            )
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("SLO alert emission failed: %s", exc)

    # Health event emission

    def _build_health_event_unlocked(self) -> HealthEventDict:
        """Build a health event dict.  Must be called while lock is held."""
        total = self._utterances_total
        success_rate = self._utterances_success / total if total > 0 else 0.0
        avg_confidence = self._total_confidence / total if total > 0 else 0.0
        fallback_rate = self._fallback_triggers / total if total > 0 else 0.0

        avg_latency: dict[str, float] = {}
        for backend, count in self._backend_latency_counts.items():
            if count > 0:
                avg_latency[backend] = round(
                    self._backend_latency_sums.get(backend, 0.0) / count, 1
                )

        return HealthEventDict(
            utterances_total=total,
            success_rate=round(success_rate, 4),
            avg_confidence=round(avg_confidence, 4),
            backend_distribution=dict(self._backend_counts),
            fallback_trigger_rate=round(fallback_rate, 4),
            avg_latency_per_backend=avg_latency,
        )

    def _emit_health_event(self, health: HealthEventDict) -> None:
        """Emit a structured voice_pipeline_health event via activity feed."""
        try:
            from jarvis_engine.memory.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.VOICE_PIPELINE,
                f"Voice pipeline health: {health['utterances_total']} utterances, "
                f"success={health['success_rate']:.1%}",
                {
                    "event": "voice_pipeline_health",
                    **health,
                },
            )
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Health event emission failed: %s", exc)

    # Public query methods

    def get_latency_stats(self) -> LatencyStatsDict:
        """Return percentile latency stats for the capture-to-transcript metric.

        Returns
        -------
        LatencyStatsDict
            Contains p50, p95, p99 in milliseconds, sample count, and
            recent SLO violations.
        """
        with self._lock:
            samples = list(self._capture_to_transcript_ms)
            violations = list(self._slo_violations[-20:])

        if not samples:
            return LatencyStatsDict(
                p50_ms=0.0,
                p95_ms=0.0,
                p99_ms=0.0,
                sample_count=0,
                slo_violations=[],
            )

        return LatencyStatsDict(
            p50_ms=round(_percentile(samples, 50), 1),
            p95_ms=round(_percentile(samples, 95), 1),
            p99_ms=round(_percentile(samples, 99), 1),
            sample_count=len(samples),
            slo_violations=violations,
        )

    def get_end_to_end_stats(self) -> LatencyStatsDict:
        """Return percentile latency stats for the end-to-end metric.

        Returns
        -------
        LatencyStatsDict
            Contains p50, p95, p99 in milliseconds, sample count, and
            recent SLO violations.
        """
        with self._lock:
            samples = list(self._end_to_end_ms)
            violations = [
                v
                for v in self._slo_violations[-20:]
                if v.get("metric", "").startswith("end_to_end")
            ]

        if not samples:
            return LatencyStatsDict(
                p50_ms=0.0,
                p95_ms=0.0,
                p99_ms=0.0,
                sample_count=0,
                slo_violations=[],
            )

        return LatencyStatsDict(
            p50_ms=round(_percentile(samples, 50), 1),
            p95_ms=round(_percentile(samples, 95), 1),
            p99_ms=round(_percentile(samples, 99), 1),
            sample_count=len(samples),
            slo_violations=violations,
        )

    def get_backend_distribution(self) -> dict[str, int]:
        """Return the backend usage distribution.

        Returns
        -------
        dict[str, int]
            Mapping of backend name to usage count.
        """
        with self._lock:
            return dict(self._backend_counts)

    def get_health_summary(self) -> dict[str, Any]:
        """Return a health summary suitable for API responses.

        Returns
        -------
        dict[str, Any]
            Contains utterances_total, success_rate, avg_confidence, etc.
        """
        with self._lock:
            total = self._utterances_total
            success_rate = self._utterances_success / total if total > 0 else 0.0
            avg_confidence = self._total_confidence / total if total > 0 else 0.0
            return {
                "utterances_total": total,
                "success_rate": round(success_rate, 4),
                "avg_confidence": round(avg_confidence, 4),
            }

    def get_endpoint_response(self) -> LatencyEndpointDict:
        """Build the full response for the ``GET /voice/latency`` endpoint.

        Uses a single lock acquisition for an atomic snapshot of all data.

        Returns
        -------
        LatencyEndpointDict
            Combined latency stats, backend distribution, and health summary.
        """
        with self._lock:
            samples = list(self._capture_to_transcript_ms)
            violations = list(self._slo_violations[-20:])
            backend_dist = dict(self._backend_counts)
            total = self._utterances_total
            success_rate = self._utterances_success / total if total > 0 else 0.0
            avg_confidence = self._total_confidence / total if total > 0 else 0.0

        if not samples:
            p50 = p95 = p99 = 0.0
        else:
            p50 = round(_percentile(samples, 50), 1)
            p95 = round(_percentile(samples, 95), 1)
            p99 = round(_percentile(samples, 99), 1)

        return LatencyEndpointDict(
            p50_ms=p50,
            p95_ms=p95,
            p99_ms=p99,
            sample_count=len(samples),
            slo_violations=violations,
            backend_distribution=backend_dist,
            health={
                "utterances_total": total,
                "success_rate": round(success_rate, 4),
                "avg_confidence": round(avg_confidence, 4),
            },
        )

    # Stage transition events (for widget indicator)

    def emit_stage_transition(self, stage: str) -> None:
        """Emit an activity feed event for a voice pipeline stage transition.

        Used by the desktop widget to show listening/processing state.

        Parameters
        ----------
        stage : str
            The pipeline stage that was just entered.
        """
        try:
            from jarvis_engine.memory.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.VOICE_PIPELINE,
                f"Voice pipeline stage: {stage}",
                {
                    "event": "stage_transition",
                    "stage": stage,
                },
            )
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Stage transition event emission failed: %s", exc)

    # Reset (test-only)

    def reset(self) -> None:
        """Reset all telemetry state.  Intended for testing only."""
        with self._lock:
            self._local.current = _UtteranceRecord()
            self._capture_to_transcript_ms.clear()
            self._transcript_to_response_ms.clear()
            self._end_to_end_ms.clear()
            self._vad_duration_ms.clear()
            self._backend_counts.clear()
            self._backend_latency_sums.clear()
            self._backend_latency_counts.clear()
            self._utterances_total = 0
            self._utterances_success = 0
            self._total_confidence = 0.0
            self._fallback_triggers = 0
            self._slo_violations.clear()
            self._consecutive_slo_breaches = 0


# Percentile calculation helper


def _percentile(data: list[float] | deque[float], pct: float) -> float:
    """Compute the *pct*-th percentile of *data* using linear interpolation.

    Parameters
    ----------
    data : list or deque of float
        Non-empty sequence of latency values.
    pct : float
        Percentile to compute (0-100).

    Returns
    -------
    float
        The computed percentile value.
    """
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n == 1:
        return sorted_data[0]
    # Use the same quantile calculation as Python statistics
    idx = (pct / 100) * (n - 1)
    lower = int(idx)
    upper = min(lower + 1, n - 1)
    frac = idx - lower
    return sorted_data[lower] + frac * (sorted_data[upper] - sorted_data[lower])


# Module-level singleton (mutable container avoids ``global`` keyword)

_telemetry_state: dict[str, VoiceTelemetry | None] = {"instance": None}
_telemetry_lock = threading.Lock()


def get_voice_telemetry() -> VoiceTelemetry:
    """Return (or create) the module-level VoiceTelemetry singleton."""
    if _telemetry_state["instance"] is not None:
        return _telemetry_state["instance"]
    with _telemetry_lock:
        # Double-checked locking
        if _telemetry_state["instance"] is not None:
            return _telemetry_state["instance"]
        _telemetry_state["instance"] = VoiceTelemetry()
        return _telemetry_state["instance"]


def _reset_telemetry() -> None:
    """Discard the module-level singleton.  Test-only."""
    with _telemetry_lock:
        _telemetry_state["instance"] = None
