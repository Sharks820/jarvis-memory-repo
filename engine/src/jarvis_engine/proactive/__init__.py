"""Proactive intelligence: trigger rules, notifications, and the ProactiveEngine."""

from __future__ import annotations

import logging
import threading
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
        self._lock = threading.Lock()

    def evaluate(self, snapshot_data: dict) -> list[TriggerAlert]:
        """Check each rule, respect cooldowns, fire notifications, return alerts."""
        now = datetime.now(timezone.utc)
        alerts: list[TriggerAlert] = []
        seen_messages: set[str] = set()

        for rule in self._rules:
            # Check cooldown and reserve firing slot atomically
            with self._lock:
                last = self._last_fired.get(rule.rule_id)
                if last is not None:
                    elapsed = (now - last).total_seconds() / 60.0
                    if elapsed < rule.cooldown_minutes:
                        continue
                # Reserve the slot so concurrent evaluations see this rule as fired
                self._last_fired[rule.rule_id] = now

            # Run the check function
            try:
                messages = rule.check_fn(snapshot_data)
            except Exception as exc:
                logger.warning("Trigger rule %s failed: %s", rule.rule_id, exc)
                # Undo reservation since the rule didn't actually fire
                with self._lock:
                    if self._last_fired.get(rule.rule_id) == now:
                        self._last_fired[rule.rule_id] = last
                continue

            if not messages:
                # Undo reservation since no alerts were produced
                with self._lock:
                    if self._last_fired.get(rule.rule_id) == now:
                        self._last_fired[rule.rule_id] = last
                continue

            # Create alerts and send notifications (with dedup)
            new_alerts: list[TriggerAlert] = []
            for msg in messages:
                dedup_key = f"{rule.rule_id}:{msg}"
                if dedup_key in seen_messages:
                    continue
                seen_messages.add(dedup_key)
                alert = TriggerAlert(
                    rule_id=rule.rule_id,
                    message=msg,
                    priority="normal",
                    timestamp=now.isoformat(),
                )
                new_alerts.append(alert)
                alerts.append(alert)

            if new_alerts:
                self._notifier.send_batch(new_alerts)
            else:
                # No alerts after dedup — undo reservation
                with self._lock:
                    if self._last_fired.get(rule.rule_id) == now:
                        self._last_fired[rule.rule_id] = last

        # Log to activity feed
        if alerts:
            try:
                from jarvis_engine.activity_feed import log_activity
                log_activity("proactive_trigger", f"Fired {len(alerts)} alerts", {
                    "alerts": [a.rule_id for a in alerts],
                })
            except ImportError:
                pass
            except Exception as exc:
                logger.debug("Proactive activity feed logging failed: %s", exc)

        return alerts

    def reset_cooldowns(self) -> None:
        """Clear all cooldown state."""
        with self._lock:
            self._last_fired.clear()
