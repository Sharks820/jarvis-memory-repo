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

from jarvis_engine.command_bus import CommandBus
from jarvis_engine.config import repo_root

# Caching state

_bus_cache: dict[str, object] = {"bus": None, "root": None}
_cached_bus_lock = threading.Lock()


# Public factory


def get_bus() -> CommandBus:
    """Return a CommandBus wired to the current repo_root().

    Uses a cached bus when repo_root() hasn't changed (e.g. mobile API
    in-process calls).  Falls back to creating a fresh bus when
    repo_root() changes (e.g. tests monkeypatching repo_root).
    """
    from jarvis_engine.app import create_app

    root = repo_root()
    with _cached_bus_lock:
        if _bus_cache["bus"] is not None and _bus_cache["root"] == root:
            return _bus_cache["bus"]  # type: ignore[return-value]
        bus = create_app(root)
        _bus_cache["bus"] = bus
        _bus_cache["root"] = root
        return bus
