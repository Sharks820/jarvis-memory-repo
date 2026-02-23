"""Handler classes for proactive intelligence, wake word, cost reduction, and self-testing."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from jarvis_engine.commands.proactive_commands import (
    CostReductionCommand,
    CostReductionResult,
    ProactiveCheckCommand,
    ProactiveCheckResult,
    SelfTestCommand,
    SelfTestResult,
    WakeWordStartCommand,
    WakeWordStartResult,
)

logger = logging.getLogger(__name__)


class ProactiveCheckHandler:
    """Load snapshot data and evaluate proactive trigger rules."""

    def __init__(self, root: Path, proactive_engine: Any = None) -> None:
        self._root = root
        self._engine = proactive_engine

    def handle(self, cmd: ProactiveCheckCommand) -> ProactiveCheckResult:
        if self._engine is None:
            return ProactiveCheckResult(message="Proactive engine not available.")

        # Load snapshot data
        snapshot_path = cmd.snapshot_path
        if not snapshot_path:
            snapshot_path = str(
                self._root / ".planning" / "ops_snapshot.live.json"
            )

        resolved_path = Path(snapshot_path).resolve()
        try:
            resolved_path.relative_to(self._root.resolve())
        except ValueError:
            return ProactiveCheckResult(
                message="Snapshot path outside project root."
            )

        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                snapshot_data = json.load(f)
        except FileNotFoundError:
            return ProactiveCheckResult(
                message=f"Snapshot file not found: {snapshot_path}"
            )
        except json.JSONDecodeError as exc:
            return ProactiveCheckResult(
                message=f"Invalid JSON in snapshot: {exc}"
            )

        # Evaluate triggers
        alerts = self._engine.evaluate(snapshot_data)
        alerts_dicts = [
            {
                "rule_id": a.rule_id,
                "message": a.message,
                "priority": a.priority,
                "timestamp": a.timestamp,
            }
            for a in alerts
        ]

        return ProactiveCheckResult(
            alerts_fired=len(alerts),
            alerts=json.dumps(alerts_dicts),
            message=f"Fired {len(alerts)} alert(s)." if alerts else "No alerts.",
        )


class WakeWordStartHandler:
    """Start wake word detection in a daemon thread."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: WakeWordStartCommand) -> WakeWordStartResult:
        try:
            from jarvis_engine.wakeword import WakeWordDetector
        except ImportError:
            return WakeWordStartResult(
                started=False,
                message="Wake word module not available.",
            )

        detector = WakeWordDetector(threshold=cmd.threshold)

        def _on_detected() -> None:
            logger.info("Wake word detected! Ready for voice command.")

        stop_event = threading.Event()
        thread = threading.Thread(
            target=detector.start,
            args=(_on_detected, stop_event),
            daemon=True,
        )
        thread.start()

        return WakeWordStartResult(
            started=True,
            message="Wake word detection started in background thread.",
        )


class CostReductionHandler:
    """Compute local-vs-cloud ratio, take snapshot, and compute trend."""

    def __init__(self, root: Path, cost_tracker: Any = None) -> None:
        self._root = root
        self._cost_tracker = cost_tracker

    def handle(self, cmd: CostReductionCommand) -> CostReductionResult:
        if self._cost_tracker is None:
            return CostReductionResult(message="Cost tracker not available.")

        from jarvis_engine.proactive.cost_tracking import (
            cost_reduction_snapshot,
            cost_reduction_trend,
            load_cost_history,
        )

        history_path = self._root / ".planning" / "brain" / "cost_history.jsonl"

        summary = self._cost_tracker.local_vs_cloud_summary(days=cmd.days)
        snapshot = cost_reduction_snapshot(self._cost_tracker, history_path)

        history = load_cost_history(history_path)
        trend_info = cost_reduction_trend(history)

        return CostReductionResult(
            local_pct=summary["local_pct"],
            cloud_cost_usd=summary["cloud_cost_usd"],
            trend=trend_info["trend"],
            message=(
                f"{summary['local_pct']}% local ({summary['local_count']}/{summary['total_count']} queries), "
                f"cloud cost ${summary['cloud_cost_usd']:.4f}, trend: {trend_info['trend']}"
            ),
        )


class SelfTestHandler:
    """Run adversarial memory quiz, save result, and check for regression."""

    def __init__(
        self, root: Path, engine: Any = None, embed_service: Any = None
    ) -> None:
        self._root = root
        self._engine = engine
        self._embed_service = embed_service

    def handle(self, cmd: SelfTestCommand) -> SelfTestResult:
        if self._engine is None or self._embed_service is None:
            return SelfTestResult(
                message="Memory engine or embedding service not available."
            )

        from jarvis_engine.proactive.self_test import AdversarialSelfTest

        history_path = self._root / ".planning" / "brain" / "self_test_history.jsonl"

        tester = AdversarialSelfTest(
            self._engine,
            self._embed_service,
            score_threshold=cmd.score_threshold,
        )

        result = tester.run_memory_quiz()
        tester.save_quiz_result(result, history_path)
        regression = tester.check_regression(history_path)

        return SelfTestResult(
            average_score=result["average_score"],
            tasks_run=result["tasks_run"],
            regression_detected=regression["regression_detected"],
            per_task_scores=result["per_task_scores"],
            message=(
                f"Avg score: {result['average_score']:.2f} across {result['tasks_run']} tasks. "
                f"Regression: {'YES' if regression['regression_detected'] else 'no'}"
            ),
        )
