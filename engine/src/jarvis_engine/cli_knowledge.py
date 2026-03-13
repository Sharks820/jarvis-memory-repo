"""Backward-compatibility shim -- moved to jarvis_engine.cli.knowledge."""
from jarvis_engine.cli.knowledge import *  # noqa: F401,F403
from jarvis_engine.cli.knowledge import _get_bus  # noqa: F401  # re-export for monkeypatch
