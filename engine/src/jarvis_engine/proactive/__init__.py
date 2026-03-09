"""Proactive intelligence: trigger rules, notifications, and the ProactiveEngine."""

from jarvis_engine.proactive.engine import ProactiveEngine
from jarvis_engine.proactive.notifications import Notifier
from jarvis_engine.proactive.triggers import (
    DEFAULT_TRIGGER_RULES,
    TriggerAlert,
    TriggerRule,
)

__all__ = [
    "DEFAULT_TRIGGER_RULES",
    "Notifier",
    "ProactiveEngine",
    "TriggerAlert",
    "TriggerRule",
]
