"""Handler classes for proactive intelligence and wake word commands."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from jarvis_engine.commands.proactive_commands import (
    ProactiveCheckCommand,
    ProactiveCheckResult,
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

        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
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
