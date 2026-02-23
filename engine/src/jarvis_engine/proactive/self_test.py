"""Adversarial self-testing framework for memory recall quality.

Periodically quizzes Jarvis on retained knowledge using golden memory-recall
tasks, detects score regressions, and alerts when quality drops.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AdversarialSelfTest:
    """Run memory-recall quizzes and track score history for regression detection."""

    def __init__(
        self,
        engine: Any,
        embed_service: Any,
        notifier: Any = None,
        score_threshold: float = 0.5,
    ) -> None:
        self._engine = engine
        self._embed_service = embed_service
        self._notifier = notifier
        self._score_threshold = score_threshold

    def run_memory_quiz(self, tasks: list | None = None) -> dict:
        """Run memory-recall golden tasks and return aggregated scores.

        If tasks is None, uses DEFAULT_MEMORY_TASKS from growth_tracker.
        Sends alert via notifier if average score drops below threshold.
        """
        from jarvis_engine.growth_tracker import (
            DEFAULT_MEMORY_TASKS,
            MemoryRecallTask,
            run_memory_eval,
        )

        if tasks is None:
            tasks = DEFAULT_MEMORY_TASKS

        results = run_memory_eval(tasks, self._engine, self._embed_service)

        per_task_scores = [
            {"task_id": r.task_id, "score": r.overall_score}
            for r in results
        ]

        avg_score = (
            sum(r.overall_score for r in results) / len(results)
            if results
            else 0.0
        )
        avg_score = round(avg_score, 4)

        below = avg_score < self._score_threshold

        if below and self._notifier is not None:
            try:
                self._notifier.send(
                    f"Memory quiz alert: average score {avg_score:.2f} "
                    f"below threshold {self._score_threshold:.2f}"
                )
            except Exception as exc:
                logger.warning("Failed to send self-test alert: %s", exc)

        return {
            "tasks_run": len(results),
            "average_score": avg_score,
            "below_threshold": below,
            "per_task_scores": per_task_scores,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def save_quiz_result(self, result: dict, history_path: Path) -> None:
        """Append a quiz result to the JSONL history file."""
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=True) + "\n")

    def check_regression(self, history_path: Path, window: int = 5) -> dict:
        """Check for score regression against recent history.

        Loads the last `window` quiz results. If the latest score is less than
        80% of the average of previous scores, flags a regression.

        Returns dict with: regression_detected, current_score, baseline_score, drop_pct.
        """
        if not history_path.exists():
            return {
                "regression_detected": False,
                "current_score": 0.0,
                "baseline_score": 0.0,
                "drop_pct": 0.0,
            }

        lines = history_path.read_text(encoding="utf-8").splitlines()
        entries: list[dict] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue

        recent = entries[-window:]
        if len(recent) < 2:
            current = recent[-1].get("average_score", 0.0) if recent else 0.0
            return {
                "regression_detected": False,
                "current_score": current,
                "baseline_score": current,
                "drop_pct": 0.0,
            }

        current_score = recent[-1].get("average_score", 0.0)
        previous = recent[:-1]
        baseline = sum(e.get("average_score", 0.0) for e in previous) / len(previous)

        regression = current_score < baseline * 0.8 if baseline > 0 else False
        drop = round(((baseline - current_score) / baseline * 100) if baseline > 0 else 0.0, 1)

        return {
            "regression_detected": regression,
            "current_score": round(current_score, 4),
            "baseline_score": round(baseline, 4),
            "drop_pct": drop,
        }
