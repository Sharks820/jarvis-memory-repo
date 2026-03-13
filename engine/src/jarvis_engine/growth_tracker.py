"""Backward-compatibility shim — canonical location is jarvis_engine.learning.growth_tracker."""
from jarvis_engine.learning.growth_tracker import *  # noqa: F401,F403
from jarvis_engine.learning.growth_tracker import (  # noqa: F401 — re-export private names
    _TASK_INDEX,
    _generate,
    _history_lock,
)
