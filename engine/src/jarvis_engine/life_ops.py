"""Backward-compatibility shim -- canonical module is jarvis_engine.ops.life_ops."""

from jarvis_engine.ops.life_ops import *  # noqa: F401,F403
from jarvis_engine.ops.life_ops import (  # noqa: F401 -- private names
    _safe_bool,
    _is_due_item,
    _is_urgent_item,
    _assemble_data_summary,
)
