"""Backward-compatibility shim -- canonical module is jarvis_engine.security.owner_guard."""

from jarvis_engine.security.owner_guard import *  # noqa: F401,F403
from jarvis_engine.security.owner_guard import (  # noqa: F401 -- private names
    _hash_master_password,
)
