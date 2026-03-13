from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path
from typing import Any, TypedDict

from jarvis_engine._shared import atomic_write_json as _atomic_write_json
from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine._shared import runtime_dir as _runtime_dir

logger = logging.getLogger(__name__)

DEFAULT_CONTROL_STATE: "ControlState" = {
    "daemon_paused": False,
    "safe_mode": False,
    "muted": False,
    "mute_until_utc": "",
    "reason": "",
    "updated_utc": "",
}

DEFAULT_RESOURCE_BUDGETS = {
    "embedding_cache_mb": 256.0,
    "conversation_buffer_mb": 24.0,
    "mission_state_mb": 64.0,
    "process_memory_mb": 2048.0,
    "process_cpu_pct": 92.0,
}

_MB = 1024.0 * 1024.0
_DEFAULT_THROTTLE = {"mild_scale": 1.35, "severe_scale": 2.0, "max_sleep_s": 1800}

_dir_size_cache: dict[str, tuple[float, float]] = {}  # path -> (size_mb, timestamp)
_DIR_SIZE_CACHE_TTL = 600.0  # 10 minutes


class ControlState(TypedDict):
    daemon_paused: bool
    safe_mode: bool
    muted: bool
    mute_until_utc: str | None
    reason: str
    updated_utc: str


class ResourceSnapshot(TypedDict):
    captured_utc: str
    pressure_level: str
    over_budget_count: int
    should_throttle: bool
    metrics: dict
    throttle: dict


class DaemonSleepRecommendation(TypedDict):
    base_sleep_s: float
    sleep_s: float
    pressure_level: str
    skip_heavy_tasks: bool


def _default_control_state() -> ControlState:
    return {
        "daemon_paused": False,
        "safe_mode": False,
        "muted": False,
        "mute_until_utc": "",
        "reason": "",
        "updated_utc": "",
    }


def control_state_path(root: Path) -> Path:
    return _runtime_dir(root) / "control.json"


def resource_budgets_path(root: Path) -> Path:
    return _runtime_dir(root) / "resource_budgets.json"


def resource_pressure_path(root: Path) -> Path:
    return _runtime_dir(root) / "resource_pressure.json"


def read_control_state(root: Path) -> ControlState:
    from jarvis_engine._shared import load_json_file

    path = control_state_path(root)
    raw = load_json_file(path, None, expected_type=dict)
    if raw is None:
        return _default_control_state()
    state: ControlState = {
        "daemon_paused": bool(raw.get("daemon_paused", False)),
        "safe_mode": bool(raw.get("safe_mode", False)),
        "muted": bool(raw.get("muted", False)),
        "mute_until_utc": str(raw.get("mute_until_utc", "")),
        "reason": str(raw.get("reason", "")).strip()[:200],
        "updated_utc": str(raw.get("updated_utc", "")),
    }
    # Auto-expire mute if mute_until_utc has passed
    if state["muted"] and state["mute_until_utc"]:
        try:
            mute_until_raw = str(state["mute_until_utc"])
            mute_until = datetime.fromisoformat(mute_until_raw)
            # Ensure timezone-aware comparison: treat naive datetimes as UTC
            if mute_until.tzinfo is None:
                mute_until = mute_until.replace(tzinfo=UTC)
            if datetime.now(UTC) >= mute_until:
                state["muted"] = False
                state["mute_until_utc"] = ""
        except (ValueError, TypeError) as exc:
            logger.debug("Mute-expiry parse failed: %s", exc)
    return state


def read_resource_budgets(root: Path) -> dict[str, float]:
    path = resource_budgets_path(root)
    from jarvis_engine._shared import load_json_file

    raw: dict[str, Any] = load_json_file(path, {}, expected_type=dict)
    merged = dict(DEFAULT_RESOURCE_BUDGETS)
    for key, default_val in DEFAULT_RESOURCE_BUDGETS.items():
        value = raw.get(key)
        if isinstance(value, (int, float)) and float(value) > 0:
            merged[key] = float(value)
        else:
            merged[key] = float(default_val)
    return merged


def read_resource_pressure_state(root: Path) -> dict[str, Any]:
    from jarvis_engine._shared import load_json_file

    path = resource_pressure_path(root)
    return load_json_file(path, {}, expected_type=dict)


def _file_size_mb(path: Path) -> float:
    if not path.exists() or not path.is_file():
        return 0.0
    try:
        return max(0.0, float(path.stat().st_size) / _MB)
    except OSError:
        return 0.0


def _dir_size_mb(path: Path) -> float:
    if not path.exists() or not path.is_dir():
        return 0.0
    import time as _time

    cache_key = str(path)
    cached = _dir_size_cache.get(cache_key)
    if cached is not None:
        cached_size, cached_ts = cached
        if (_time.monotonic() - cached_ts) < _DIR_SIZE_CACHE_TTL:
            return cached_size

    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    logger.debug("Cannot stat file %s during size calculation", p)
                    continue
    except OSError:
        return 0.0
    result = max(0.0, float(total) / _MB)
    _dir_size_cache[cache_key] = (result, _time.monotonic())
    return result


def _process_usage() -> tuple[float, float]:
    memory_mb = 0.0
    cpu_pct = 0.0
    try:
        import psutil  # type: ignore[import-untyped]

        proc = psutil.Process(os.getpid())
        memory_mb = float(proc.memory_info().rss) / _MB
        cpu_pct = float(proc.cpu_percent(interval=0.0))
    except (ImportError, OSError, AttributeError):
        try:
            import resource

            getrusage_fn = getattr(resource, "getrusage", None)
            rusage_self = getattr(resource, "RUSAGE_SELF", None)
            if callable(getrusage_fn) and rusage_self is not None:
                maxrss = float(getrusage_fn(rusage_self).ru_maxrss)
                if sys.platform == "darwin":
                    memory_mb = maxrss / _MB
                else:
                    memory_mb = maxrss / 1024.0
        except (ImportError, OSError, AttributeError):
            memory_mb = 0.0
            cpu_pct = 0.0
    return round(max(0.0, memory_mb), 3), round(max(0.0, cpu_pct), 3)


def _metric_is_over_budget(metrics: dict[str, Any], key: str) -> bool:
    metric = metrics.get(key)
    if not isinstance(metric, dict):
        return False
    return bool(metric.get("over_budget", False))


def capture_runtime_resource_snapshot(root: Path) -> ResourceSnapshot:
    budgets = read_resource_budgets(root)
    memory_mb, cpu_pct = _process_usage()

    embedding_cache_mb = _dir_size_mb(root / ".planning" / "cache")
    conversation_buffer_mb = _file_size_mb(
        root / ".planning" / "brain" / "conversation_history.json"
    )
    mission_state_mb = _file_size_mb(root / ".planning" / "missions.json") + _dir_size_mb(
        root / ".planning" / "missions"
    )

    current = {
        "embedding_cache_mb": embedding_cache_mb,
        "conversation_buffer_mb": conversation_buffer_mb,
        "mission_state_mb": mission_state_mb,
        "process_memory_mb": memory_mb,
        "process_cpu_pct": cpu_pct,
    }
    metrics = {}
    over_budget_count = 0
    for key, value in current.items():
        budget = float(budgets.get(key, 0.0))
        over_budget = budget > 0 and value > budget
        if over_budget:
            over_budget_count += 1
        metrics[key] = {
            "current": round(float(value), 3),
            "budget": round(float(budget), 3),
            "over_budget": over_budget,
        }

    process_memory_over = _metric_is_over_budget(metrics, "process_memory_mb")
    process_cpu_over = _metric_is_over_budget(metrics, "process_cpu_pct")
    if process_memory_over or process_cpu_over or over_budget_count >= 3:
        pressure_level = "severe"
    elif over_budget_count >= 1:
        pressure_level = "mild"
    else:
        pressure_level = "none"

    return {
        "captured_utc": _now_iso(),
        "pressure_level": pressure_level,
        "over_budget_count": over_budget_count,
        "should_throttle": pressure_level in {"mild", "severe"},
        "metrics": metrics,
        "throttle": dict(_DEFAULT_THROTTLE),
    }


def write_resource_pressure_state(
    root: Path,
    snapshot: ResourceSnapshot | dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = dict(snapshot) if isinstance(snapshot, dict) else {}
    _atomic_write_json(resource_pressure_path(root), dict(payload))
    return payload


def recommend_daemon_sleep(
    base_sleep_s: int,
    snapshot: ResourceSnapshot | dict[str, Any],
) -> DaemonSleepRecommendation:
    level = str(snapshot.get("pressure_level", "none")).lower()
    throttle = snapshot.get("throttle", {})
    mild_scale = float(throttle.get("mild_scale", _DEFAULT_THROTTLE["mild_scale"]))
    severe_scale = float(throttle.get("severe_scale", _DEFAULT_THROTTLE["severe_scale"]))
    max_sleep = int(throttle.get("max_sleep_s", _DEFAULT_THROTTLE["max_sleep_s"]))
    max_sleep = max(60, max_sleep)
    adjusted = int(base_sleep_s)
    if level == "mild":
        adjusted = int(round(base_sleep_s * mild_scale))
    elif level == "severe":
        adjusted = int(round(base_sleep_s * severe_scale))
    adjusted = max(30, min(adjusted, max_sleep))
    return {
        "base_sleep_s": int(base_sleep_s),
        "sleep_s": adjusted,
        "pressure_level": level,
        "skip_heavy_tasks": level == "severe",
    }


def write_control_state(
    root: Path,
    *,
    daemon_paused: bool | None = None,
    safe_mode: bool | None = None,
    muted: bool | None = None,
    mute_until_utc: str | None = None,
    reason: str = "",
) -> ControlState:
    state = read_control_state(root)
    if daemon_paused is not None:
        state["daemon_paused"] = daemon_paused
    if safe_mode is not None:
        state["safe_mode"] = safe_mode
    if muted is not None:
        state["muted"] = muted
        if not muted:
            state["mute_until_utc"] = ""
    if mute_until_utc is not None:
        state["mute_until_utc"] = mute_until_utc.strip()
    if reason.strip():
        state["reason"] = reason.strip()[:200]
    state["updated_utc"] = _now_iso()

    path = control_state_path(root)
    _atomic_write_json(path, dict(state))
    return state


def reset_control_state(root: Path) -> ControlState:
    state = _default_control_state()
    state["updated_utc"] = _now_iso()
    path = control_state_path(root)
    _atomic_write_json(path, dict(state))
    return state
