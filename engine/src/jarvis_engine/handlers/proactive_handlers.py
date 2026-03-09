"""Handler classes for proactive intelligence, wake word, cost reduction, and self-testing."""

from __future__ import annotations

import json
import logging
import threading
import time as _time_mod
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from jarvis_engine.gateway.costs import CostTracker
    from jarvis_engine.gateway.models import ModelGateway
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine
    from jarvis_engine.proactive import ProactiveEngine

from jarvis_engine._constants import ACTIONS_FILENAME, OPS_SNAPSHOT_FILENAME

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

    def __init__(
        self, root: Path, proactive_engine: Optional[ProactiveEngine] = None
    ) -> None:
        self._root = root
        self._engine = proactive_engine

    def handle(self, cmd: ProactiveCheckCommand) -> ProactiveCheckResult:
        if self._engine is None:
            return ProactiveCheckResult(message="Proactive engine not available.")

        # Load snapshot data
        snapshot_path = cmd.snapshot_path
        if not snapshot_path:
            snapshot_path = str(self._root / ".planning" / OPS_SNAPSHOT_FILENAME)

        resolved_path = Path(snapshot_path).resolve()
        try:
            resolved_path.relative_to(self._root.resolve())
        except ValueError:
            return ProactiveCheckResult(message="Snapshot path outside project root.")

        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                snapshot_data = json.load(f)
        except FileNotFoundError:
            return ProactiveCheckResult(
                message=f"Snapshot file not found: {snapshot_path}"
            )
        except json.JSONDecodeError as exc:
            return ProactiveCheckResult(message=f"Invalid JSON in snapshot: {exc}")

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

        # Diagnostic: check which data sources are empty
        diagnostics_parts: list[str] = []
        _data_sources = {
            "medications": "medication_reminder",
            "bills": "bill_due_alert",
            "calendar_events": "calendar_prep",
            "tasks": "urgent_task_alert",
        }
        for source_key, rule_id in _data_sources.items():
            items = snapshot_data.get(source_key, [])
            if not items:
                diagnostics_parts.append(f"{rule_id}: no {source_key} data available")

        connectors = snapshot_data.get("connector_statuses", [])
        not_ready = [
            c["name"]
            for c in connectors
            if isinstance(c, dict) and not c.get("ready", False)
        ]
        if not_ready:
            diagnostics_parts.append(f"Connectors not ready: {', '.join(not_ready)}")

        diagnostics_str = "; ".join(diagnostics_parts) if diagnostics_parts else ""

        if alerts:
            message = f"Fired {len(alerts)} alert(s)."
        elif diagnostics_parts:
            message = f"No alerts. {len(diagnostics_parts)} diagnostic(s)."
        else:
            message = "No alerts. All data sources populated."

        return ProactiveCheckResult(
            alerts_fired=len(alerts),
            alerts=alerts_dicts,
            message=message,
            diagnostics=diagnostics_str,
        )


class WakeWordStartHandler:
    """Start wake word detection in a daemon thread."""

    def __init__(self, root: Path, gateway: ModelGateway | None = None) -> None:
        self._root = root
        self._gateway = gateway
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._mic_lock = threading.Lock()
        self._conversation_until: float = 0.0

    def handle(self, cmd: WakeWordStartCommand) -> WakeWordStartResult:
        # Prevent duplicate threads
        if self._thread is not None and self._thread.is_alive():
            return WakeWordStartResult(
                started=True,
                message="Wake word detection already running.",
            )

        try:
            from jarvis_engine.wakeword import WakeWordDetector
        except ImportError:
            return WakeWordStartResult(
                started=False,
                message="Wake word module not available.",
            )

        detector = WakeWordDetector(threshold=cmd.threshold)

        def _on_detected() -> None:
            """Wake word detected — record command, transcribe, and dispatch."""
            logger.info("Wake word detected! Listening for command...")
            try:
                from jarvis_engine.stt import record_from_microphone, transcribe_smart

                # Pause wake word mic stream to avoid dual-stream conflicts,
                # then record on a fresh stream with buffer drain.
                detector.pause()
                _time_mod.sleep(0.15)  # Let OS audio driver release mic fully
                try:
                    audio = record_from_microphone(
                        max_duration_seconds=8.0,
                        drain_seconds=0.3,
                    )
                finally:
                    detector.resume()
                # Load personal vocab for NER entity correction
                from jarvis_engine.stt_postprocess import _load_personal_vocab

                _entities = _load_personal_vocab()
                result = transcribe_smart(
                    audio,
                    language="en",
                    gateway=self._gateway,
                    entity_list=_entities if _entities else None,
                )
                text = result.text.strip()
                if not text:
                    logger.info("No speech detected after wake word.")
                    return
                # Strip "jarvis" prefix if present
                from jarvis_engine.voice_extractors import strip_wake_word

                text = strip_wake_word(text)
                if not text:
                    logger.info("Wake word only, no command.")
                    return
                logger.info(
                    "Voice command: '%s' (backend=%s, %.2fs)",
                    text,
                    result.backend,
                    result.duration_seconds,
                )
                # Dispatch through voice-run pipeline
                try:
                    from jarvis_engine.voice_intents import cmd_voice_run_impl
                    from jarvis_engine.config import repo_root

                    _root = repo_root()
                    cmd_voice_run_impl(
                        text=text,
                        execute=True,
                        approve_privileged=False,
                        speak=True,
                        snapshot_path=_root / ".planning" / OPS_SNAPSHOT_FILENAME,
                        actions_path=_root / ".planning" / ACTIONS_FILENAME,
                        voice_user="conner",
                        voice_auth_wav="",
                        voice_threshold=0.82,
                        master_password="",
                    )
                except (RuntimeError, OSError, ValueError) as exc:
                    logger.error("Voice command dispatch failed: %s", exc)
                # Enter conversation mode for 20 seconds
                self._conversation_until = _time_mod.time() + 20.0
            except (RuntimeError, OSError) as exc:
                logger.error("Wake word callback error: %s", exc)

        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=detector.start,
            args=(_on_detected, self._stop_event, self._mic_lock),
            daemon=True,
        )
        self._thread.start()

        return WakeWordStartResult(
            started=True,
            message="Wake word detection started. Say 'Jarvis' to activate.",
        )

    def stop(self) -> None:
        """Stop the wake word detection thread if running."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._stop_event = None


class CostReductionHandler:
    """Compute local-vs-cloud ratio, take snapshot, and compute trend."""

    def __init__(self, root: Path, cost_tracker: Optional[CostTracker] = None) -> None:
        self._root = root
        self._cost_tracker = cost_tracker

    def handle(self, cmd: CostReductionCommand) -> CostReductionResult:
        if self._cost_tracker is None:
            return CostReductionResult(message="Cost tracker not available.")

        try:
            from jarvis_engine.proactive.cost_tracking import (
                cost_reduction_snapshot,
                cost_reduction_trend,
                load_cost_history,
            )
        except ImportError as exc:
            logger.warning("cost_tracking module not available: %s", exc)
            return CostReductionResult(message="Cost tracking module not available.")

        history_path = self._root / ".planning" / "brain" / "cost_history.jsonl"

        summary = self._cost_tracker.local_vs_cloud_summary(days=cmd.days)
        cost_reduction_snapshot(
            self._cost_tracker, history_path
        )  # side-effect: writes snapshot

        history = load_cost_history(history_path)
        trend_info = cost_reduction_trend(history)
        failed_count = int(summary.get("failed_count", 0) or 0)
        failed_cost_usd = float(summary.get("failed_cost_usd", 0.0) or 0.0)
        failed_fragment = ""
        if failed_count > 0:
            failed_fragment = f", failed {failed_count} (${failed_cost_usd:.4f})"

        return CostReductionResult(
            local_pct=float(summary.get("local_pct", 0.0)),
            cloud_cost_usd=float(summary.get("cloud_cost_usd", 0.0)),
            failed_count=failed_count,
            failed_cost_usd=failed_cost_usd,
            trend=str(trend_info.get("trend", "stable")),
            message=(
                f"{summary.get('local_pct', 0.0)}% local "
                f"({summary.get('local_count', 0)}/{summary.get('total_count', 0)} queries), "
                f"cloud cost ${float(summary.get('cloud_cost_usd', 0.0)):.4f}"
                f"{failed_fragment}, trend: {trend_info.get('trend', 'stable')}"
            ),
        )


class SelfTestHandler:
    """Run adversarial memory quiz, save result, and check for regression."""

    def __init__(
        self,
        root: Path,
        engine: Optional[MemoryEngine] = None,
        embed_service: Optional[EmbeddingService] = None,
    ) -> None:
        self._root = root
        self._engine = engine
        self._embed_service = embed_service

    def handle(self, cmd: SelfTestCommand) -> SelfTestResult:
        if self._engine is None or self._embed_service is None:
            return SelfTestResult(
                message="Memory engine or embedding service not available."
            )

        try:
            from jarvis_engine.proactive.self_test import AdversarialSelfTest
        except ImportError as exc:
            logger.warning("self_test module not available: %s", exc)
            return SelfTestResult(message="Self-test module not available.")

        from jarvis_engine._constants import SELF_TEST_HISTORY
        from jarvis_engine._shared import runtime_dir

        history_path = runtime_dir(self._root) / SELF_TEST_HISTORY

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
