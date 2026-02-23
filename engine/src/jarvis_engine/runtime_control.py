from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_CONTROL_STATE = {
    "daemon_paused": False,
    "safe_mode": False,
    "reason": "",
    "updated_utc": "",
}


def control_state_path(root: Path) -> Path:
    return root / ".planning" / "runtime" / "control.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_control_state(root: Path) -> dict[str, Any]:
    path = control_state_path(root)
    if not path.exists():
        return dict(DEFAULT_CONTROL_STATE)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
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
