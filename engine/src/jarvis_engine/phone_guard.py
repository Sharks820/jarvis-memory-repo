"""Backward-compatibility shim — real implementation in jarvis_engine.phone.guard."""
from jarvis_engine.phone.guard import *  # noqa: F401,F403
from jarvis_engine.phone.guard import (  # noqa: F401 — underscore names not covered by star
    _parse_ts,
    _ACTIONS_LOCK,
)
