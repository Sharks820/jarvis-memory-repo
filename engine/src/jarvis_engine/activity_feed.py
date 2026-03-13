"""Backward-compatibility shim -- canonical module is jarvis_engine.memory.activity_feed."""

from jarvis_engine.memory.activity_feed import *  # noqa: F401,F403
from jarvis_engine.memory.activity_feed import (  # noqa: F401 -- private names
    _feed_holder,
    _feed_lock,
    _reset_feed,
)
