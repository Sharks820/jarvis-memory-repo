"""Proactive intelligence: trigger rules, notifications, and the ProactiveEngine."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from jarvis_engine.proactive.triggers import (
    DEFAULT_TRIGGER_RULES,
    TriggerAlert,
    TriggerRule,
)
from jarvis_engine.proactive.notifications import Notifier

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TRIGGER_RULES",
    "Notifier",
    "ProactiveEngine",
    "TriggerAlert",
    "TriggerRule",
]


class ProactiveEngine:
    """Evaluate trigger rules against snapshot data and fire notifications."""

    def __init__(self, rules: list[TriggerRule], notifier: Notifier) -> None:
        self._rules = rules
        self._notifier = notifier
        self._last_fired: dict[str, datetime] = {}

    def evaluate(self, snapshot_data: dict) -> list[TriggerAlert]:
        """Check each rule, respect cooldowns, fire notifications, return alerts."""
        now = datetime.now(timezone.utc)
        alerts: list[TriggerAlert] = []

        for rule in self._rules:
            # Check cooldown
            last = self._last_fired.get(rule.rule_id)
            if last is not None:
                elapsed = (now - last).total_seconds() / 60.0
                if elapsed < rule.cooldown_minutes:
                    continue

            # Run the check function
            try:
                messages = rule.check_fn(snapshot_data)
            except Exception as exc:
                logger.warning("Trigger rule %s failed: %s", rule.rule_id, exc)
                continue

            if not messages:
                continue

            # Create alerts and send notifications
            for msg in messages:
                alert = TriggerAlert(
                    rule_id=rule.rule_id,
                    message=msg,
                    priority="normal",
                    timestamp=now.isoformat(),
                )
                alerts.append(alert)

            self._notifier.send_batch(alerts[-len(messages):])
            self._last_fired[rule.rule_id] = now

        return alerts

    def reset_cooldowns(self) -> None:
        """Clear all cooldown state."""
        self._last_fired.clear()
