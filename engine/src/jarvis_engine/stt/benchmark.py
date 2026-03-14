"""STT command benchmark framework (STT-09).

Provides a standardized set of basic Jarvis commands and a benchmark runner
that measures transcription accuracy and latency across STT backends.

Usage::

    from jarvis_engine.stt.benchmark import run_benchmark, BASIC_COMMANDS
    result = run_benchmark(transcribe_fn)
    print(f"Accuracy: {result.accuracy_pct:.1f}%")
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# 25 common Jarvis voice commands used as the benchmark corpus.
BASIC_COMMANDS: list[str] = [
    "Jarvis, what's on my schedule today?",
    "Jarvis, run the ops brief.",
    "Jarvis, check brain status.",
    "Jarvis, add a task.",
    "Jarvis, self heal.",
    "Jarvis, daily brief.",
    "Jarvis, pause daemon.",
    "Jarvis, resume daemon.",
    "Jarvis, safe mode.",
    "Jarvis, stop listening.",
    "Jarvis, start listening.",
    "Jarvis, what time is it?",
    "Jarvis, set a reminder for tomorrow.",
    "Jarvis, search the web for Python tutorials.",
    "Jarvis, check the weather.",
    "Jarvis, play some music.",
    "Jarvis, take a note.",
    "Jarvis, read my messages.",
    "Jarvis, turn off safe mode.",
    "Jarvis, knowledge graph status.",
    "Jarvis, how many tasks do I have?",
    "Jarvis, cancel the last task.",
    "Jarvis, run diagnostics.",
    "Jarvis, show me the dashboard.",
    "Jarvis, good morning.",
]

# Default accuracy threshold: 80% of commands must be recognized correctly.
DEFAULT_ACCURACY_THRESHOLD: float = 80.0


@dataclass
class CommandResult:
    """Result of benchmarking a single command."""

    expected: str
    actual: str
    passed: bool
    latency_ms: float
    confidence: float


@dataclass
class BenchmarkResult:
    """Aggregate result of a full benchmark run."""

    accuracy_pct: float = 0.0
    total_commands: int = 0
    passed_commands: int = 0
    failed_commands: int = 0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    meets_threshold: bool = False
    threshold_pct: float = DEFAULT_ACCURACY_THRESHOLD
    per_command: list[CommandResult] = field(default_factory=list)


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for fuzzy command comparison.

    Strips punctuation, lowercases, and removes the wake word prefix
    so that ``"Jarvis, run the ops brief."`` matches ``"run the ops brief"``.
    """
    import re

    normalized = text.lower().strip()
    # Remove wake word prefix
    normalized = re.sub(r"^(?:jarvis|hey jarvis)[,;:!.\s]*", "", normalized)
    # Remove trailing/leading punctuation and extra whitespace
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _command_matches(expected: str, actual: str) -> bool:
    """Return True if *actual* transcription is close enough to *expected*.

    Uses normalized comparison with a word-overlap ratio: at least 70% of
    expected words must appear in the actual transcription.
    """
    if not actual.strip():
        return False
    norm_expected = _normalize_for_comparison(expected)
    norm_actual = _normalize_for_comparison(actual)

    if norm_expected == norm_actual:
        return True

    expected_words = set(norm_expected.split())
    actual_words = set(norm_actual.split())
    if not expected_words:
        return False

    overlap = len(expected_words & actual_words)
    ratio = overlap / len(expected_words)
    return ratio >= 0.70


def run_benchmark(
    transcribe_fn: Callable[[str], tuple[str, float]],
    *,
    commands: list[str] | None = None,
    threshold_pct: float = DEFAULT_ACCURACY_THRESHOLD,
) -> BenchmarkResult:
    """Run the STT command benchmark.

    Parameters
    ----------
    transcribe_fn:
        A callable that accepts a command string (the expected text) and
        returns ``(transcribed_text, confidence)``.  In a real benchmark
        this would play synthesized audio through the STT pipeline; for
        unit testing it can be a simple stub.
    commands:
        Optional override for the command list.  Defaults to
        :data:`BASIC_COMMANDS`.
    threshold_pct:
        Accuracy percentage required to pass (default 80%).

    Returns
    -------
    BenchmarkResult
        Aggregate accuracy, latency stats, and per-command breakdown.
    """
    if commands is None:
        commands = BASIC_COMMANDS

    per_command: list[CommandResult] = []
    latencies: list[float] = []
    passed = 0

    for cmd in commands:
        t0 = time.monotonic()
        try:
            actual_text, confidence = transcribe_fn(cmd)
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            logger.warning("Benchmark transcription failed for %r: %s", cmd, exc)
            actual_text = ""
            confidence = 0.0
        elapsed_ms = (time.monotonic() - t0) * 1000

        is_pass = _command_matches(cmd, actual_text)
        if is_pass:
            passed += 1

        cr = CommandResult(
            expected=cmd,
            actual=actual_text,
            passed=is_pass,
            latency_ms=round(elapsed_ms, 1),
            confidence=confidence,
        )
        per_command.append(cr)
        latencies.append(elapsed_ms)

    total = len(commands)
    accuracy = (passed / total * 100) if total > 0 else 0.0

    return BenchmarkResult(
        accuracy_pct=round(accuracy, 1),
        total_commands=total,
        passed_commands=passed,
        failed_commands=total - passed,
        avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        min_latency_ms=round(min(latencies), 1) if latencies else 0.0,
        max_latency_ms=round(max(latencies), 1) if latencies else 0.0,
        meets_threshold=accuracy >= threshold_pct,
        threshold_pct=threshold_pct,
        per_command=per_command,
    )
