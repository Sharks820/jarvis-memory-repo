"""Backward-compatibility shim -- moved to jarvis_engine.cli.security."""
from jarvis_engine.cli.security import *  # noqa: F401,F403
from jarvis_engine.cli.security import _get_bus  # noqa: F401  # re-export for monkeypatch
