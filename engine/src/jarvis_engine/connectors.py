"""Backward-compatibility shim -- canonical module is jarvis_engine.ops.connectors."""

from jarvis_engine.ops.connectors import *  # noqa: F401,F403
from jarvis_engine.ops.connectors import (  # noqa: F401 -- private names
    _permissions_path,
    _any_env_set,
    _all_env_set,
    _any_file_exists,
)
