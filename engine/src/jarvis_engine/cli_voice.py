"""Backward-compatibility shim -- moved to jarvis_engine.cli.voice."""
from jarvis_engine.cli.voice import *  # noqa: F401,F403
from jarvis_engine.cli.voice import _get_bus  # noqa: F401  # re-export for monkeypatch
