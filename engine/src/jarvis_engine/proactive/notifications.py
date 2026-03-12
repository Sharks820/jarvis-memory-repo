"""Windows toast notification delivery with graceful degradation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.proactive.triggers import TriggerAlert

logger = logging.getLogger(__name__)


class Notifier:
    """Send Windows toast notifications via winotify, with graceful fallback."""

    def __init__(self, app_name: str = "Jarvis") -> None:
        self._app_name = app_name

    def send(self, title: str, message: str, priority: str = "normal") -> bool:
        """Send a single toast notification. Returns True on success."""
        try:
            from winotify import Notification  # type: ignore[import-not-found]

            toast = Notification(
                app_id=self._app_name,
                title=title,
                msg=message,
            )
            toast.show()
            return True
        except ImportError:
            logger.info("[%s] %s: %s", self._app_name, title, message)
            return False
        except (RuntimeError, OSError) as exc:
            logger.warning("Failed to send notification: %s", exc)
            return False

    def send_batch(self, alerts: list[TriggerAlert]) -> int:
        """Send all alerts, return count successfully sent."""
        count = 0
        for alert in alerts:
            title = f"Jarvis: {alert.rule_id.replace('_', ' ').title()}"
            if self.send(title, alert.message, alert.priority):
                count += 1
        return count
