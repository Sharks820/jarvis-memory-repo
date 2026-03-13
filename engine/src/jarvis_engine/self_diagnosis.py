"""Backward-compatibility shim — canonical location is jarvis_engine.ops.self_diagnosis."""
from jarvis_engine.ops.self_diagnosis import *  # noqa: F401,F403
from jarvis_engine.ops.self_diagnosis import (  # noqa: F401 — re-export private names
    _BYTES_PER_MB,
    _DB_SIZE_WARN_MB,
    _KG_AVG_CONFIDENCE_THRESHOLD,
    _MEMORY_PRESSURE_MB,
    _MISSION_FAILURE_RATE_THRESHOLD,
    _ORPHAN_NODE_RATIO_THRESHOLD,
    _SEVERITY_DEDUCTIONS,
    _SEVERITY_ORDER,
    _STUCK_MISSION_MINUTES,
    _WAL_SIZE_WARN_MB,
    _issue_id,
)
