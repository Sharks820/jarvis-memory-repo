"""Backward-compatibility shim -- canonical module is jarvis_engine.ops.gaming_mode."""

from jarvis_engine.ops.gaming_mode import *  # noqa: F401,F403
from jarvis_engine.ops.gaming_mode import (  # noqa: F401 -- private names
    _game_detect_cache,
    _game_detect_lock,
    _GAME_DETECT_CACHE_TTL,
    _windows_idle_seconds,
)
