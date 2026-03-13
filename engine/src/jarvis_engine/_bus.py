"""CommandBus factory with repo_root-aware caching.

Extracted from main.py so other modules (mobile_api, widget, tests) can
obtain a ready-to-use bus without importing the heavyweight CLI module.

Public API
----------
get_bus() -> CommandBus
    Return (or create) a CommandBus wired to the current repo_root().
"""

from __future__ import annotations

import threading
from pathlib import Path

from jarvis_engine.command_bus import CommandBus
from jarvis_engine.config import repo_root

# Caching state

_cached_bus: CommandBus | None = None
_cached_bus_root: Path | None = None
_cached_bus_lock = threading.Lock()


# Public factory


def get_bus() -> CommandBus:
    """Return a CommandBus wired to the current repo_root().

    Uses a cached bus when repo_root() hasn't changed (e.g. mobile API
    in-process calls).  Falls back to creating a fresh bus when
    repo_root() changes (e.g. tests monkeypatching repo_root).
    """
    global _cached_bus, _cached_bus_root
    from jarvis_engine.app import create_app

    root = repo_root()
    with _cached_bus_lock:
        if _cached_bus is not None and _cached_bus_root == root:
            return _cached_bus
        bus = create_app(root)
        _cached_bus = bus
        _cached_bus_root = root
        return bus
