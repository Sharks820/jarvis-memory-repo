from __future__ import annotations

import json
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path
from typing import Any

DEFAULT_CONTROL_STATE = {
    "daemon_paused": False,
    "safe_mode": False,
    "reason": "",
    "updated_utc": "",
}


from jarvis_engine._shared import atomic_write_json as _atomic_write_json


def control_state_path(root: Path) -> Path:
    return root / ".planning" / "runtime" / "control.json"


def read_control_state(root: Path) -> dict[str, Any]:
    path = control_state_path(root)
    if not path.exists():
        return dict(DEFAULT_CONTROL_STATE)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONTROL_STATE)
    if not isinstance(raw, dict):
        return dict(DEFAULT_CONTROL_STATE)
    return {
        "daemon_paused": bool(raw.get("daemon_paused", False)),
        "safe_mode": bool(raw.get("safe_mode", False)),
        "reason": str(raw.get("reason", "")).strip()[:200],
        "updated_utc": str(raw.get("updated_utc", "")),
    }


def write_control_state(
    root: Path,
    *,
    daemon_paused: bool | None = None,
    safe_mode: bool | None = None,
    reason: str = "",
) -> dict[str, Any]:
    state = read_control_state(root)
    if daemon_paused is not None:
        state["daemon_paused"] = daemon_paused
    if safe_mode is not None:
        state["safe_mode"] = safe_mode
    if reason.strip():
        state["reason"] = reason.strip()[:200]
    state["updated_utc"] = datetime.now(UTC).isoformat()

    path = control_state_path(root)
    _atomic_write_json(path, state)
    return state


def reset_control_state(root: Path) -> dict[str, Any]:
    state = dict(DEFAULT_CONTROL_STATE)
    state["updated_utc"] = datetime.now(UTC).isoformat()
    path = control_state_path(root)
    _atomic_write_json(path, state)
    return state
