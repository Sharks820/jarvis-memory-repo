"""Backward-compatibility shim -- moved to jarvis_engine.cli.ops."""
from jarvis_engine.cli.ops import *  # noqa: F401,F403
from jarvis_engine.cli.ops import _get_bus  # noqa: F401  # re-export for monkeypatch
