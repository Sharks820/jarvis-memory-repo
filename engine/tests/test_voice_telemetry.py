"""Tests for voice_telemetry module — pipeline latency instrumentation and SLO enforcement."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from jarvis_engine.voice.telemetry import (
    ALL_STAGES,
    STAGE_COMMAND_DISPATCH,
    STAGE_INTENT_CLASSIFICATION,
    STAGE_RESPONSE_READY,
    STAGE_TRANSCRIPTION_END,
    STAGE_TRANSCRIPTION_START,
    STAGE_TTS_END,
    STAGE_TTS_START,
    STAGE_VAD_SPEECH_END,
    STAGE_VAD_SPEECH_ONSET,
    STAGE_WAKE_WORD_DETECTED,
    VoiceTelemetry,
    _percentile,
    _reset_telemetry,
    get_voice_telemetry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def telemetry():
    """Create a fresh VoiceTelemetry instance for each test."""
    return VoiceTelemetry(max_samples=100, health_interval=5)


# ---------------------------------------------------------------------------
# Stage marking and timestamp recording
# ---------------------------------------------------------------------------


class TestStageMarking:
    """Tests for mark_stage and begin_utterance."""

    def test_mark_stage_records_timestamp(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        # Access internal state for verification
        assert STAGE_VAD_SPEECH_ONSET in telemetry._current.timestamps
        assert isinstance(telemetry._current.timestamps[STAGE_VAD_SPEECH_ONSET], int)
        assert telemetry._current.timestamps[STAGE_VAD_SPEECH_ONSET] > 0

    def test_begin_utterance_resets_timestamps(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        assert telemetry._current.timestamps

        telemetry.begin_utterance()
        assert len(telemetry._current.timestamps) == 0

    def test_mark_all_stages(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        for stage in ALL_STAGES:
            telemetry.mark_stage(stage)
        assert len(telemetry._current.timestamps) == len(ALL_STAGES)

    def test_timestamps_are_monotonically_increasing(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        timestamps = []
        for stage in ALL_STAGES:
            telemetry.mark_stage(stage)
            timestamps.append(telemetry._current.timestamps[stage])
        # Nanosecond timestamps should be non-decreasing
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]

    def test_set_backend(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.set_backend("parakeet")
        assert telemetry._current.backend == "parakeet"

    def test_set_confidence(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.set_confidence(0.95)
        assert telemetry._current.confidence == 0.95

    def test_set_success(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.set_success(False)
        assert telemetry._current.success is False

    def test_mark_fallback(self, telemetry: VoiceTelemetry):
        assert telemetry._fallback_triggers == 0
        telemetry.mark_fallback()
        assert telemetry._fallback_triggers == 1
        telemetry.mark_fallback()
        assert telemetry._fallback_triggers == 2


# ---------------------------------------------------------------------------
# Derived metric computation
# ---------------------------------------------------------------------------


class TestDerivedMetrics:
    """Tests for finish_utterance derived metric computation."""

    def _run_full_pipeline(self, telemetry: VoiceTelemetry) -> dict[str, float]:
        """Simulate a full pipeline with known delays."""
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        telemetry.mark_stage(STAGE_VAD_SPEECH_END)
        telemetry.mark_stage(STAGE_WAKE_WORD_DETECTED)
        telemetry.mark_stage(STAGE_TRANSCRIPTION_START)
        telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
        telemetry.mark_stage(STAGE_INTENT_CLASSIFICATION)
        telemetry.mark_stage(STAGE_COMMAND_DISPATCH)
        telemetry.mark_stage(STAGE_RESPONSE_READY)
        telemetry.mark_stage(STAGE_TTS_START)
        telemetry.mark_stage(STAGE_TTS_END)
        telemetry.set_backend("parakeet")
        telemetry.set_confidence(0.92)
        return telemetry.finish_utterance()

    def test_full_pipeline_returns_all_metrics(self, telemetry: VoiceTelemetry):
        metrics = self._run_full_pipeline(telemetry)
        assert "capture_to_transcript_ms" in metrics
        assert "transcript_to_response_ms" in metrics
        assert "end_to_end_ms" in metrics
        assert "vad_duration_ms" in metrics

    def test_metrics_are_non_negative(self, telemetry: VoiceTelemetry):
        metrics = self._run_full_pipeline(telemetry)
        for key, value in metrics.items():
            assert value >= 0, f"{key} should be non-negative, got {value}"

    def test_capture_to_transcript_computed(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        # Small delay to ensure measurable difference
        telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
        metrics = telemetry.finish_utterance()
        assert "capture_to_transcript_ms" in metrics
        assert metrics["capture_to_transcript_ms"] >= 0

    def test_transcript_to_response_computed(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
        telemetry.mark_stage(STAGE_RESPONSE_READY)
        metrics = telemetry.finish_utterance()
        assert "transcript_to_response_ms" in metrics

    def test_end_to_end_computed(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        telemetry.mark_stage(STAGE_TTS_END)
        metrics = telemetry.finish_utterance()
        assert "end_to_end_ms" in metrics

    def test_vad_duration_computed(self, telemetry: VoiceTelemetry):
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        telemetry.mark_stage(STAGE_VAD_SPEECH_END)
        metrics = telemetry.finish_utterance()
        assert "vad_duration_ms" in metrics

    def test_missing_stages_omit_metrics(self, telemetry: VoiceTelemetry):
        """Metrics requiring missing stages should not be present."""
        telemetry.begin_utterance()
        # Only mark one stage -- no derived metrics can be computed
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        metrics = telemetry.finish_utterance()
        assert "capture_to_transcript_ms" not in metrics
        assert "transcript_to_response_ms" not in metrics
        assert "end_to_end_ms" not in metrics
        # vad_duration also requires speech_end
        assert "vad_duration_ms" not in metrics

    def test_partial_pipeline_only_computable_metrics(self, telemetry: VoiceTelemetry):
        """Only metrics with both required stages should appear."""
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
        # Missing: STAGE_RESPONSE_READY, STAGE_TTS_END, STAGE_VAD_SPEECH_END
        metrics = telemetry.finish_utterance()
        assert "capture_to_transcript_ms" in metrics
        assert "transcript_to_response_ms" not in metrics
        assert "end_to_end_ms" not in metrics
        assert "vad_duration_ms" not in metrics


# ---------------------------------------------------------------------------
# Percentile calculations
# ---------------------------------------------------------------------------


class TestPercentileCalculation:
    """Tests for the _percentile helper function."""

    def test_single_value(self):
        assert _percentile([42.0], 50) == 42.0
        assert _percentile([42.0], 95) == 42.0
        assert _percentile([42.0], 99) == 42.0

    def test_two_values(self):
        data = [10.0, 20.0]
        p50 = _percentile(data, 50)
        assert p50 == 15.0  # midpoint

    def test_known_percentiles(self):
        data = list(range(1, 101))  # 1 to 100
        p50 = _percentile(data, 50)
        p95 = _percentile(data, 95)
        p99 = _percentile(data, 99)
        assert abs(p50 - 50.5) < 0.1
        assert abs(p95 - 95.05) < 0.5
        assert abs(p99 - 99.01) < 0.5

    def test_empty_data(self):
        assert _percentile([], 50) == 0.0

    def test_p0_is_minimum(self):
        data = [5.0, 10.0, 15.0, 20.0]
        assert _percentile(data, 0) == 5.0

    def test_p100_is_maximum(self):
        data = [5.0, 10.0, 15.0, 20.0]
        assert _percentile(data, 100) == 20.0

    def test_deque_input(self):
        from collections import deque
        d = deque([1.0, 2.0, 3.0, 4.0, 5.0])
        p50 = _percentile(d, 50)
        assert p50 == 3.0

    def test_unsorted_input(self):
        data = [5.0, 1.0, 3.0, 2.0, 4.0]
        p50 = _percentile(data, 50)
        assert p50 == 3.0


# ---------------------------------------------------------------------------
# Latency stats
# ---------------------------------------------------------------------------


class TestLatencyStats:
    """Tests for get_latency_stats and get_end_to_end_stats."""

    def test_empty_stats(self, telemetry: VoiceTelemetry):
        stats = telemetry.get_latency_stats()
        assert stats["p50_ms"] == 0.0
        assert stats["p95_ms"] == 0.0
        assert stats["p99_ms"] == 0.0
        assert stats["sample_count"] == 0
        assert stats["slo_violations"] == []

    def test_stats_after_utterances(self, telemetry: VoiceTelemetry):
        # Simulate several utterances
        for _ in range(10):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.9)
            telemetry.finish_utterance()

        stats = telemetry.get_latency_stats()
        assert stats["sample_count"] == 10
        assert stats["p50_ms"] >= 0
        assert stats["p95_ms"] >= stats["p50_ms"]
        assert stats["p99_ms"] >= stats["p95_ms"]

    def test_end_to_end_stats(self, telemetry: VoiceTelemetry):
        for _ in range(5):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TTS_END)
            telemetry.set_backend("deepgram")
            telemetry.set_confidence(0.85)
            telemetry.finish_utterance()

        stats = telemetry.get_end_to_end_stats()
        assert stats["sample_count"] == 5


# ---------------------------------------------------------------------------
# SLO violation detection
# ---------------------------------------------------------------------------


class TestSLOViolations:
    """Tests for SLO violation detection and alerting."""

    def test_no_violations_under_target(self, telemetry: VoiceTelemetry):
        """Fast utterances should not trigger SLO violations."""
        for _ in range(5):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            # Immediate mark — effectively 0ms latency
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.mark_stage(STAGE_TTS_END)
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.95)
            telemetry.finish_utterance()

        stats = telemetry.get_latency_stats()
        # p50 and p95 should be far below targets for instant marks
        assert stats["slo_violations"] == []

    def test_violations_detected_with_high_latency(self, telemetry: VoiceTelemetry):
        """Simulate high latency by directly injecting samples."""
        with telemetry._lock:
            # Inject high-latency samples
            for _ in range(20):
                telemetry._capture_to_transcript_ms.append(5000.0)  # 5 seconds
                telemetry._end_to_end_ms.append(10000.0)  # 10 seconds

        # Trigger SLO check via a regular utterance
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
        telemetry.mark_stage(STAGE_TTS_END)
        telemetry.set_backend("parakeet")
        telemetry.finish_utterance()

        stats = telemetry.get_latency_stats()
        # Should have violations since injected latency exceeds targets
        assert len(stats["slo_violations"]) > 0

    @patch("jarvis_engine.voice.telemetry.VoiceTelemetry._emit_slo_alert")
    def test_sustained_breach_triggers_alert(self, mock_alert, telemetry: VoiceTelemetry):
        """10 consecutive SLO breaches should trigger an alert."""
        with telemetry._lock:
            # Pre-fill with high-latency samples
            for _ in range(50):
                telemetry._capture_to_transcript_ms.append(6000.0)
                telemetry._end_to_end_ms.append(12000.0)

        # Each finish_utterance call will check SLOs
        for _ in range(12):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.mark_stage(STAGE_TTS_END)
            telemetry.set_backend("parakeet")
            telemetry.finish_utterance()

        assert mock_alert.called


# ---------------------------------------------------------------------------
# Health event emission
# ---------------------------------------------------------------------------


class TestHealthEvents:
    """Tests for periodic health event emission."""

    @patch("jarvis_engine.voice.telemetry.VoiceTelemetry._emit_health_event")
    def test_health_emitted_at_interval(self, mock_emit, telemetry: VoiceTelemetry):
        """Health event should fire every health_interval utterances."""
        # telemetry has health_interval=5
        for _ in range(10):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.9)
            telemetry.finish_utterance()

        # Should have emitted at utterance 5 and 10
        assert mock_emit.call_count == 2

    @patch("jarvis_engine.voice.telemetry.VoiceTelemetry._emit_health_event")
    def test_health_not_emitted_before_interval(self, mock_emit, telemetry: VoiceTelemetry):
        """Health event should NOT fire before reaching the interval."""
        for _ in range(4):  # 4 < 5 (health_interval)
            telemetry.begin_utterance()
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.9)
            telemetry.finish_utterance()

        assert mock_emit.call_count == 0

    def test_health_event_content(self, telemetry: VoiceTelemetry):
        """Verify health event data contains expected fields."""
        for _ in range(3):
            telemetry.begin_utterance()
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.88)
            telemetry.finish_utterance()

        telemetry.begin_utterance()
        telemetry.set_backend("deepgram")
        telemetry.set_confidence(0.95)
        telemetry.finish_utterance()

        health = telemetry.get_health_summary()
        assert health["utterances_total"] == 4
        assert 0.0 <= health["success_rate"] <= 1.0
        assert 0.0 <= health["avg_confidence"] <= 1.0

    def test_backend_distribution(self, telemetry: VoiceTelemetry):
        """Verify backend distribution tracking."""
        for _ in range(3):
            telemetry.begin_utterance()
            telemetry.set_backend("parakeet")
            telemetry.finish_utterance()

        for _ in range(2):
            telemetry.begin_utterance()
            telemetry.set_backend("deepgram")
            telemetry.finish_utterance()

        dist = telemetry.get_backend_distribution()
        assert dist["parakeet"] == 3
        assert dist["deepgram"] == 2


# ---------------------------------------------------------------------------
# Singleton behavior
# ---------------------------------------------------------------------------


class TestSingleton:
    """Tests for the module-level singleton pattern."""

    def test_get_voice_telemetry_returns_singleton(self):
        _reset_telemetry()
        try:
            t1 = get_voice_telemetry()
            t2 = get_voice_telemetry()
            assert t1 is t2
        finally:
            _reset_telemetry()

    def test_reset_telemetry_clears_singleton(self):
        _reset_telemetry()
        try:
            t1 = get_voice_telemetry()
            _reset_telemetry()
            t2 = get_voice_telemetry()
            assert t1 is not t2
        finally:
            _reset_telemetry()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Tests for thread-safe concurrent access."""

    def test_concurrent_mark_and_finish(self, telemetry: VoiceTelemetry):
        """Multiple threads marking stages and finishing utterances concurrently.

        Each thread has its own per-thread _current record via threading.local(),
        so they don't corrupt each other's in-flight utterance data.
        """
        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                for _ in range(20):
                    telemetry.begin_utterance()
                    telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
                    telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
                    telemetry.mark_stage(STAGE_TTS_END)
                    telemetry.set_backend(f"backend-{thread_id}")
                    telemetry.set_confidence(0.85)
                    telemetry.finish_utterance()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread safety errors: {errors}"
        assert telemetry._utterances_total == 80  # 4 threads * 20 iterations

    def test_concurrent_reads(self, telemetry: VoiceTelemetry):
        """Concurrent stats queries should not raise."""
        # Seed some data
        for _ in range(10):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.mark_stage(STAGE_TTS_END)
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.9)
            telemetry.finish_utterance()

        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(50):
                    telemetry.get_latency_stats()
                    telemetry.get_end_to_end_stats()
                    telemetry.get_backend_distribution()
                    telemetry.get_health_summary()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_finish_without_begin(self, telemetry: VoiceTelemetry):
        """finish_utterance without begin_utterance should work (empty metrics)."""
        metrics = telemetry.finish_utterance()
        # No timestamps marked, so no derived metrics
        assert metrics == {}

    def test_finish_with_no_stages(self, telemetry: VoiceTelemetry):
        """finish_utterance after begin but no stage marks."""
        telemetry.begin_utterance()
        metrics = telemetry.finish_utterance()
        assert metrics == {}
        assert telemetry._utterances_total == 1

    def test_double_begin_resets(self, telemetry: VoiceTelemetry):
        """Calling begin_utterance twice should reset the current record."""
        telemetry.begin_utterance()
        telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
        telemetry.set_backend("parakeet")
        telemetry.begin_utterance()
        assert telemetry._current.timestamps == {}
        assert telemetry._current.backend == ""

    def test_failed_utterance_counted(self, telemetry: VoiceTelemetry):
        """Failed utterances should be tracked in success rate."""
        telemetry.begin_utterance()
        telemetry.set_success(False)
        telemetry.finish_utterance()

        health = telemetry.get_health_summary()
        assert health["utterances_total"] == 1
        assert health["success_rate"] == 0.0

    def test_mixed_success_rate(self, telemetry: VoiceTelemetry):
        """Success rate should reflect the mix of success and failure."""
        for _ in range(3):
            telemetry.begin_utterance()
            telemetry.set_success(True)
            telemetry.finish_utterance()

        telemetry.begin_utterance()
        telemetry.set_success(False)
        telemetry.finish_utterance()

        health = telemetry.get_health_summary()
        assert health["utterances_total"] == 4
        assert health["success_rate"] == 0.75

    def test_rolling_window_cap(self):
        """Latency deque should not grow beyond max_samples."""
        t = VoiceTelemetry(max_samples=10)
        for _ in range(20):
            t.begin_utterance()
            t.mark_stage(STAGE_VAD_SPEECH_ONSET)
            t.mark_stage(STAGE_TRANSCRIPTION_END)
            t.set_backend("parakeet")
            t.finish_utterance()

        assert len(t._capture_to_transcript_ms) <= 10

    def test_reset_clears_all_state(self, telemetry: VoiceTelemetry):
        """reset() should clear all internal state."""
        for _ in range(5):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.9)
            telemetry.finish_utterance()

        telemetry.reset()

        assert telemetry._utterances_total == 0
        assert telemetry._utterances_success == 0
        assert len(telemetry._capture_to_transcript_ms) == 0
        assert len(telemetry._end_to_end_ms) == 0
        assert telemetry._backend_counts == {}
        assert telemetry._slo_violations == []
        assert telemetry._consecutive_slo_breaches == 0

    def test_zero_confidence_average(self, telemetry: VoiceTelemetry):
        """Zero confidence utterances should not cause division errors."""
        telemetry.begin_utterance()
        telemetry.set_confidence(0.0)
        telemetry.finish_utterance()

        health = telemetry.get_health_summary()
        assert health["avg_confidence"] == 0.0


# ---------------------------------------------------------------------------
# Endpoint response
# ---------------------------------------------------------------------------


class TestEndpointResponse:
    """Tests for the get_endpoint_response method."""

    def test_endpoint_response_structure(self, telemetry: VoiceTelemetry):
        resp = telemetry.get_endpoint_response()
        assert "p50_ms" in resp
        assert "p95_ms" in resp
        assert "p99_ms" in resp
        assert "sample_count" in resp
        assert "slo_violations" in resp
        assert "backend_distribution" in resp
        assert "health" in resp

    def test_endpoint_response_with_data(self, telemetry: VoiceTelemetry):
        for _ in range(5):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.92)
            telemetry.finish_utterance()

        resp = telemetry.get_endpoint_response()
        assert resp["sample_count"] == 5
        assert resp["backend_distribution"]["parakeet"] == 5
        assert resp["health"]["utterances_total"] == 5
        assert resp["health"]["success_rate"] == 1.0

    def test_endpoint_response_empty(self, telemetry: VoiceTelemetry):
        resp = telemetry.get_endpoint_response()
        assert resp["sample_count"] == 0
        assert resp["p50_ms"] == 0.0
        assert resp["backend_distribution"] == {}
        assert resp["health"]["utterances_total"] == 0


# ---------------------------------------------------------------------------
# Stage transition emission
# ---------------------------------------------------------------------------


class TestStageTransitionEvents:
    """Tests for stage transition activity feed events."""

    @patch("jarvis_engine.activity_feed.log_activity", autospec=True)
    def test_emit_stage_transition_calls_log_activity(self, mock_log, telemetry: VoiceTelemetry):
        telemetry.emit_stage_transition(STAGE_VAD_SPEECH_ONSET)
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "voice_pipeline"
        assert "stage_transition" in str(call_args[0][2])

    def test_emit_stage_transition_no_crash_on_import_error(self, telemetry: VoiceTelemetry):
        """Should not crash if activity feed is unavailable."""
        with patch.dict("sys.modules", {"jarvis_engine.activity_feed": None}):
            # Should not raise — graceful degradation
            telemetry.emit_stage_transition(STAGE_VAD_SPEECH_ONSET)


# ---------------------------------------------------------------------------
# Activity feed category
# ---------------------------------------------------------------------------


class TestActivityCategory:
    """Tests for VOICE_PIPELINE category in activity feed."""

    def test_voice_pipeline_category_exists(self):
        from jarvis_engine.activity_feed import ActivityCategory
        assert hasattr(ActivityCategory, "VOICE_PIPELINE")
        assert ActivityCategory.VOICE_PIPELINE == "voice_pipeline"


# ---------------------------------------------------------------------------
# Backend latency tracking
# ---------------------------------------------------------------------------


class TestBackendLatencyTracking:
    """Tests for per-backend latency tracking."""

    def test_backend_latency_averages(self, telemetry: VoiceTelemetry):
        """Average latency per backend should be tracked."""
        # Inject known latency values for testing
        for _ in range(3):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.set_backend("parakeet")
            telemetry.set_confidence(0.9)
            telemetry.finish_utterance()

        # The average latency should be recorded
        assert "parakeet" in telemetry._backend_latency_counts
        assert telemetry._backend_latency_counts["parakeet"] == 3

    def test_multiple_backends_tracked_independently(self, telemetry: VoiceTelemetry):
        for _ in range(2):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.set_backend("parakeet")
            telemetry.finish_utterance()

        for _ in range(3):
            telemetry.begin_utterance()
            telemetry.mark_stage(STAGE_VAD_SPEECH_ONSET)
            telemetry.mark_stage(STAGE_TRANSCRIPTION_END)
            telemetry.set_backend("deepgram")
            telemetry.finish_utterance()

        assert telemetry._backend_latency_counts.get("parakeet", 0) == 2
        assert telemetry._backend_latency_counts.get("deepgram", 0) == 3


# ---------------------------------------------------------------------------
# Fallback rate tracking
# ---------------------------------------------------------------------------


class TestFallbackTracking:
    """Tests for fallback trigger rate."""

    def test_fallback_rate_in_health(self, telemetry: VoiceTelemetry):
        telemetry.mark_fallback()
        telemetry.mark_fallback()

        # Complete 4 utterances total
        for _ in range(4):
            telemetry.begin_utterance()
            telemetry.set_backend("parakeet")
            telemetry.finish_utterance()

        # Fallback rate = 2 fallbacks / 4 utterances = 0.5
        health = telemetry._build_health_event_unlocked()
        assert health["fallback_trigger_rate"] == 0.5


# ---------------------------------------------------------------------------
# Constants verification
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify stage name constants are consistent."""

    def test_all_stages_tuple_has_10_entries(self):
        assert len(ALL_STAGES) == 10

    def test_stage_names_end_with_ts(self):
        for stage in ALL_STAGES:
            assert stage.endswith("_ts"), f"{stage} should end with _ts"

    def test_stage_constants_match_all_stages(self):
        expected = {
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
        }
        assert set(ALL_STAGES) == expected
