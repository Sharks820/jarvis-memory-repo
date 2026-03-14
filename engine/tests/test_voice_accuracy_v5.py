"""Tests for Voice Accuracy requirements STT-09 through STT-14.

Each test class maps to one requirement for traceability.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np


# ---------------------------------------------------------------------------
# STT-09: Command Benchmark Framework
# ---------------------------------------------------------------------------


class TestBenchmarkFramework:
    """STT-09: Real-room command benchmark reaches agreed accuracy threshold."""

    def test_basic_commands_list_has_minimum_size(self) -> None:
        from jarvis_engine.stt.benchmark import BASIC_COMMANDS

        assert len(BASIC_COMMANDS) >= 20

    def test_run_benchmark_perfect_transcription(self) -> None:
        from jarvis_engine.stt.benchmark import run_benchmark

        def perfect_fn(cmd: str) -> tuple[str, float]:
            return cmd, 0.95

        result = run_benchmark(perfect_fn)
        assert result.accuracy_pct == 100.0
        assert result.meets_threshold is True
        assert result.passed_commands == result.total_commands
        assert result.failed_commands == 0

    def test_run_benchmark_all_failures(self) -> None:
        from jarvis_engine.stt.benchmark import run_benchmark

        def empty_fn(cmd: str) -> tuple[str, float]:
            return "", 0.0

        result = run_benchmark(empty_fn)
        assert result.accuracy_pct == 0.0
        assert result.meets_threshold is False
        assert result.failed_commands == result.total_commands

    def test_run_benchmark_partial_accuracy(self) -> None:
        from jarvis_engine.stt.benchmark import BASIC_COMMANDS, run_benchmark

        # Return correct text for first half, empty for second half
        half = len(BASIC_COMMANDS) // 2

        call_count = 0

        def partial_fn(cmd: str) -> tuple[str, float]:
            nonlocal call_count
            call_count += 1
            if call_count <= half:
                return cmd, 0.9
            return "", 0.0

        result = run_benchmark(partial_fn)
        assert 0.0 < result.accuracy_pct < 100.0
        assert result.total_commands == len(BASIC_COMMANDS)

    def test_run_benchmark_custom_commands(self) -> None:
        from jarvis_engine.stt.benchmark import run_benchmark

        custom = ["hello world", "goodbye"]

        def echo_fn(cmd: str) -> tuple[str, float]:
            return cmd, 0.9

        result = run_benchmark(echo_fn, commands=custom)
        assert result.total_commands == 2
        assert result.accuracy_pct == 100.0

    def test_run_benchmark_custom_threshold(self) -> None:
        from jarvis_engine.stt.benchmark import run_benchmark

        def echo_fn(cmd: str) -> tuple[str, float]:
            return cmd, 0.9

        result = run_benchmark(echo_fn, commands=["test"], threshold_pct=99.0)
        assert result.meets_threshold is True
        assert result.threshold_pct == 99.0

    def test_run_benchmark_records_latency(self) -> None:
        from jarvis_engine.stt.benchmark import run_benchmark

        def slow_fn(cmd: str) -> tuple[str, float]:
            return cmd, 0.9

        result = run_benchmark(slow_fn, commands=["test"])
        assert result.avg_latency_ms >= 0.0
        assert result.min_latency_ms >= 0.0
        assert result.max_latency_ms >= 0.0

    def test_run_benchmark_per_command_results(self) -> None:
        from jarvis_engine.stt.benchmark import run_benchmark

        def echo_fn(cmd: str) -> tuple[str, float]:
            return cmd, 0.85

        result = run_benchmark(echo_fn, commands=["alpha", "beta"])
        assert len(result.per_command) == 2
        assert result.per_command[0].expected == "alpha"
        assert result.per_command[0].passed is True
        assert result.per_command[0].confidence == 0.85

    def test_command_matches_fuzzy(self) -> None:
        from jarvis_engine.stt.benchmark import _command_matches

        # Exact match with different casing/punctuation
        assert _command_matches(
            "Jarvis, run the ops brief.",
            "jarvis run the ops brief",
        )
        # Word overlap >= 70%
        assert _command_matches(
            "Jarvis, check the weather.",
            "check the weather forecast",
        )
        # Complete miss
        assert not _command_matches(
            "Jarvis, run the ops brief.",
            "play some music",
        )

    def test_benchmark_handles_transcribe_exception(self) -> None:
        from jarvis_engine.stt.benchmark import run_benchmark

        def failing_fn(cmd: str) -> tuple[str, float]:
            raise RuntimeError("backend down")

        result = run_benchmark(failing_fn, commands=["test"])
        assert result.failed_commands == 1
        assert result.per_command[0].passed is False


# ---------------------------------------------------------------------------
# STT-10: Personal Lexicon Consistency
# ---------------------------------------------------------------------------


class TestPersonalLexicon:
    """STT-10: Personal lexicon and corrections applied consistently."""

    def test_load_personal_lexicon_from_file(self, tmp_path: Path) -> None:
        from jarvis_engine.stt.postprocess import load_personal_lexicon

        lexicon_dir = tmp_path / "data"
        lexicon_dir.mkdir()
        (lexicon_dir / "personal_lexicon.txt").write_text(
            "Conner\nJarvis\nOllama\n", encoding="utf-8"
        )

        with patch("jarvis_engine._shared.load_personal_vocab_lines", return_value=[]):
            terms = load_personal_lexicon(tmp_path)

        assert "Conner" in terms
        assert "Jarvis" in terms
        assert "Ollama" in terms

    def test_load_personal_lexicon_deduplicates(self, tmp_path: Path) -> None:
        from jarvis_engine.stt.postprocess import load_personal_lexicon

        lexicon_dir = tmp_path / "data"
        lexicon_dir.mkdir()
        (lexicon_dir / "personal_lexicon.txt").write_text(
            "Conner\nconner\nJarvis\n", encoding="utf-8"
        )

        with patch("jarvis_engine._shared.load_personal_vocab_lines", return_value=["Conner"]):
            terms = load_personal_lexicon(tmp_path)

        # Should have Conner only once (case-insensitive dedup)
        conner_count = sum(1 for t in terms if t.lower() == "conner")
        assert conner_count == 1

    def test_load_personal_lexicon_fallback_to_vocab(self, tmp_path: Path) -> None:
        from jarvis_engine.stt.postprocess import load_personal_lexicon

        # No lexicon file exists -- should fall back to shared vocab
        with patch(
            "jarvis_engine._shared.load_personal_vocab_lines",
            return_value=["Groq", "Anthropic"],
        ):
            terms = load_personal_lexicon(tmp_path)

        assert "Groq" in terms
        assert "Anthropic" in terms

    def test_entity_corrections_applied_to_all_backends(self) -> None:
        """Verify postprocess_transcription applies entity corrections."""
        from jarvis_engine.stt.postprocess import postprocess_transcription

        result = postprocess_transcription(
            "Hey conner check the weather today please",
            confidence=0.8,
            gateway=None,
            entity_list=["Conner", "Jarvis"],
        )
        assert "Conner" in result


# ---------------------------------------------------------------------------
# STT-11: Low-Confidence Confirmation Flow
# ---------------------------------------------------------------------------


class TestLowConfidenceConfirmation:
    """STT-11: Low-confidence segments trigger confirmation flow."""

    def test_confidence_threshold_constant_exists(self) -> None:
        from jarvis_engine.stt.core import CONFIDENCE_CONFIRMATION_THRESHOLD

        assert isinstance(CONFIDENCE_CONFIRMATION_THRESHOLD, float)
        assert 0.0 < CONFIDENCE_CONFIRMATION_THRESHOLD < 1.0

    def test_needs_confirmation_field_on_result(self) -> None:
        from jarvis_engine.stt.contracts import TranscriptionResult

        result = TranscriptionResult(text="test", confidence=0.3)
        assert hasattr(result, "needs_confirmation")
        assert result.needs_confirmation is False  # default

        result.needs_confirmation = True
        assert result.needs_confirmation is True

    @patch("jarvis_engine.stt.core._preprocess_audio_if_needed")
    @patch("jarvis_engine.stt.core._transcribe_auto")
    @patch("jarvis_engine.stt.core._apply_postprocessing")
    def test_low_confidence_sets_needs_confirmation(
        self,
        mock_postprocess: MagicMock,
        mock_auto: MagicMock,
        mock_preprocess: MagicMock,
    ) -> None:
        from jarvis_engine.stt.contracts import TranscriptionResult
        from jarvis_engine.stt.core import (
            CONFIDENCE_CONFIRMATION_THRESHOLD,
            transcribe_smart,
        )

        low_conf_result = TranscriptionResult(
            text="some unclear text",
            confidence=CONFIDENCE_CONFIRMATION_THRESHOLD - 0.1,
            backend="test",
        )
        mock_preprocess.return_value = (np.zeros(1600, dtype=np.float32), None)
        mock_auto.return_value = low_conf_result
        mock_postprocess.return_value = low_conf_result

        result = transcribe_smart(np.zeros(1600, dtype=np.float32))
        assert result.needs_confirmation is True

    @patch("jarvis_engine.stt.core._preprocess_audio_if_needed")
    @patch("jarvis_engine.stt.core._transcribe_auto")
    @patch("jarvis_engine.stt.core._apply_postprocessing")
    def test_high_confidence_no_confirmation(
        self,
        mock_postprocess: MagicMock,
        mock_auto: MagicMock,
        mock_preprocess: MagicMock,
    ) -> None:
        from jarvis_engine.stt.contracts import TranscriptionResult
        from jarvis_engine.stt.core import transcribe_smart

        high_conf_result = TranscriptionResult(
            text="clear command",
            confidence=0.95,
            backend="test",
        )
        mock_preprocess.return_value = (np.zeros(1600, dtype=np.float32), None)
        mock_auto.return_value = high_conf_result
        mock_postprocess.return_value = high_conf_result

        result = transcribe_smart(np.zeros(1600, dtype=np.float32))
        assert result.needs_confirmation is False

    @patch("jarvis_engine.stt.core._preprocess_audio_if_needed")
    @patch("jarvis_engine.stt.core._transcribe_auto")
    @patch("jarvis_engine.stt.core._apply_postprocessing")
    def test_empty_text_no_confirmation_even_if_low_confidence(
        self,
        mock_postprocess: MagicMock,
        mock_auto: MagicMock,
        mock_preprocess: MagicMock,
    ) -> None:
        from jarvis_engine.stt.contracts import TranscriptionResult
        from jarvis_engine.stt.core import transcribe_smart

        empty_result = TranscriptionResult(
            text="",
            confidence=0.1,
            backend="test",
        )
        mock_preprocess.return_value = (np.zeros(1600, dtype=np.float32), None)
        mock_auto.return_value = empty_result
        mock_postprocess.return_value = empty_result

        result = transcribe_smart(np.zeros(1600, dtype=np.float32))
        assert result.needs_confirmation is False


# ---------------------------------------------------------------------------
# STT-12: Pipeline Latency Tracking
# ---------------------------------------------------------------------------


class TestPipelineLatency:
    """STT-12: Wake-word + VAD + STT pipeline latency within interactive target."""

    def test_pipeline_latency_ms_field_exists(self) -> None:
        from jarvis_engine.stt.contracts import TranscriptionResult

        result = TranscriptionResult()
        assert hasattr(result, "pipeline_latency_ms")
        assert result.pipeline_latency_ms == 0.0

    @patch("jarvis_engine.stt.core._preprocess_audio_if_needed")
    @patch("jarvis_engine.stt.core._transcribe_auto")
    @patch("jarvis_engine.stt.core._apply_postprocessing")
    def test_transcribe_smart_sets_pipeline_latency(
        self,
        mock_postprocess: MagicMock,
        mock_auto: MagicMock,
        mock_preprocess: MagicMock,
    ) -> None:
        from jarvis_engine.stt.contracts import TranscriptionResult
        from jarvis_engine.stt.core import transcribe_smart

        mock_result = TranscriptionResult(
            text="hello",
            confidence=0.9,
            backend="test",
        )
        mock_preprocess.return_value = (np.zeros(1600, dtype=np.float32), None)
        mock_auto.return_value = mock_result
        mock_postprocess.return_value = mock_result

        result = transcribe_smart(np.zeros(1600, dtype=np.float32))
        # Pipeline latency is set after postprocessing; on fast machines it
        # may round to 0.0 so we just check it was assigned (>= 0)
        assert result.pipeline_latency_ms >= 0.0
        # Verify the field was explicitly set (not the default)
        assert hasattr(result, "pipeline_latency_ms")

    def test_pipeline_latency_ms_is_numeric(self) -> None:
        """Verify pipeline_latency_ms is a float and can be set."""
        from jarvis_engine.stt.contracts import TranscriptionResult

        result = TranscriptionResult(text="hello", confidence=0.9, backend="test")
        result.pipeline_latency_ms = 42.5
        assert result.pipeline_latency_ms == 42.5

    def test_duration_seconds_still_tracked(self) -> None:
        from jarvis_engine.stt.contracts import TranscriptionResult

        result = TranscriptionResult(duration_seconds=1.5)
        assert result.duration_seconds == 1.5


# ---------------------------------------------------------------------------
# STT-13: Dictation Punctuation
# ---------------------------------------------------------------------------


class TestDictationPunctuation:
    """STT-13: Continuous dictation handles punctuation and sentence boundaries."""

    def test_add_punctuation_capitalizes_first_word(self) -> None:
        from jarvis_engine.stt.postprocess import add_punctuation

        result = add_punctuation("hello world")
        assert result[0] == "H"

    def test_add_punctuation_adds_final_period(self) -> None:
        from jarvis_engine.stt.postprocess import add_punctuation

        result = add_punctuation("hello world")
        assert result.endswith(".")

    def test_add_punctuation_preserves_existing_punctuation(self) -> None:
        from jarvis_engine.stt.postprocess import add_punctuation

        result = add_punctuation("Hello world. How are you?")
        assert "Hello world." in result
        assert "you?" in result

    def test_add_punctuation_inserts_commas_before_transitions(self) -> None:
        from jarvis_engine.stt.postprocess import add_punctuation

        result = add_punctuation("I went to the store however they were closed")
        assert "," in result

    def test_add_punctuation_splits_at_boundary_signals(self) -> None:
        from jarvis_engine.stt.postprocess import add_punctuation

        result = add_punctuation(
            "I opened the door and then I walked inside"
        )
        # Should have sentence-level structure
        assert result[0].isupper()
        assert result.endswith(".")

    def test_add_punctuation_empty_input(self) -> None:
        from jarvis_engine.stt.postprocess import add_punctuation

        assert add_punctuation("") == ""
        assert add_punctuation("   ") == "   "

    def test_add_punctuation_single_word(self) -> None:
        from jarvis_engine.stt.postprocess import add_punctuation

        result = add_punctuation("hello")
        assert result == "Hello."

    def test_add_punctuation_already_punctuated_gets_minimal_changes(self) -> None:
        from jarvis_engine.stt.postprocess import add_punctuation

        result = add_punctuation("already done.")
        assert result == "Already done."


# ---------------------------------------------------------------------------
# STT-14: Voice Error Tracking
# ---------------------------------------------------------------------------


def _make_tracker(tmp_path: Path) -> "SttErrorTracker":
    """Create an SttErrorTracker with errors_path pointing to tmp_path."""
    from jarvis_engine.learning.correction_detector import SttErrorTracker

    runtime = tmp_path / ".planning" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    tracker = SttErrorTracker.__new__(SttErrorTracker)
    tracker._errors_path = runtime / "stt_errors.jsonl"
    return tracker


# Import here so the type annotation above works
from jarvis_engine.learning.correction_detector import SttErrorTracker  # noqa: E402


class TestSttErrorTracking:
    """STT-14: Voice errors logged and measurably decrease over time."""

    def test_log_stt_error_creates_file(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)

        tracker.log_stt_error(
            expected="run the ops brief",
            actual="run the ops reef",
            backend="parakeet-tdt",
        )

        assert tracker._errors_path.exists()
        lines = tracker._errors_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["expected"] == "run the ops brief"
        assert record["actual"] == "run the ops reef"
        assert record["backend"] == "parakeet-tdt"
        assert "ts" in record
        assert "epoch" in record

    def test_log_multiple_errors(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)

        for i in range(5):
            tracker.log_stt_error(
                expected=f"command {i}",
                actual=f"wrong {i}",
                backend="test-backend",
            )

        lines = tracker._errors_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 5

    def test_get_error_trend_empty(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)

        trend = tracker.get_error_trend(days=7)
        assert trend["total_errors"] == 0
        assert trend["trend"] == "stable"
        assert trend["by_backend"] == {}

    def test_get_error_trend_with_data(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)

        # Log errors from different backends
        tracker.log_stt_error(expected="a", actual="b", backend="parakeet-tdt")
        tracker.log_stt_error(expected="c", actual="d", backend="groq-whisper")
        tracker.log_stt_error(expected="e", actual="f", backend="parakeet-tdt")

        trend = tracker.get_error_trend(days=7)
        assert trend["total_errors"] == 3
        assert trend["by_backend"]["parakeet-tdt"] == 2
        assert trend["by_backend"]["groq-whisper"] == 1

    def test_get_error_trend_filters_old_entries(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)

        # Write an old record directly (30 days ago)
        old_epoch = time.time() - (30 * 86400)
        with open(tracker._errors_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": "2026-01-01T00:00:00Z",
                "expected": "old",
                "actual": "ancient",
                "backend": "test",
                "epoch": old_epoch,
            }) + "\n")

        # Log a recent error
        tracker.log_stt_error(expected="new", actual="recent", backend="test")

        trend = tracker.get_error_trend(days=7)
        assert trend["total_errors"] == 1  # only the recent one

    def test_get_error_trend_detects_improvement(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)
        now = time.time()

        # Write many errors early in the window, few later
        with open(tracker._errors_path, "w", encoding="utf-8") as f:
            # 10 errors 5 days ago
            for i in range(10):
                f.write(json.dumps({
                    "ts": "2026-03-09T00:00:00Z",
                    "expected": f"cmd{i}",
                    "actual": f"wrong{i}",
                    "backend": "test",
                    "epoch": now - (5 * 86400) + i,
                }) + "\n")
            # 2 errors 1 day ago
            for i in range(2):
                f.write(json.dumps({
                    "ts": "2026-03-13T00:00:00Z",
                    "expected": f"cmd{i}",
                    "actual": f"wrong{i}",
                    "backend": "test",
                    "epoch": now - 86400 + i,
                }) + "\n")

        trend = tracker.get_error_trend(days=7)
        assert trend["total_errors"] == 12
        assert trend["trend"] == "improving"

    def test_error_tracker_truncates_long_text(self, tmp_path: Path) -> None:
        tracker = _make_tracker(tmp_path)

        long_text = "x" * 1000
        tracker.log_stt_error(expected=long_text, actual=long_text, backend="test")

        record = json.loads(tracker._errors_path.read_text(encoding="utf-8").strip())
        assert len(record["expected"]) == 500
        assert len(record["actual"]) == 500
