"""Gaming mode state management and game process detection.

Core functions accept an optional explicit path parameter; when omitted
they resolve the default path via ``repo_root()``.  Convenience helpers
``gaming_mode_state_path()`` and ``gaming_processes_path()`` expose the
path resolution for callers that need it.
"""

from __future__ import annotations

__all__ = [
    "GamingModeState",
    "DEFAULT_GAMING_PROCESSES",
    "gaming_mode_state_path",
    "gaming_processes_path",
    "read_gaming_mode_state",
    "write_gaming_mode_state",
    "load_gaming_processes",
    "detect_active_game_process",
]

import csv
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, TypedDict

from jarvis_engine._shared import now_iso
from jarvis_engine._shared import runtime_dir
from jarvis_engine.config import repo_root


class GamingModeState(TypedDict):
    """Typed shape for gaming mode state."""

    enabled: bool
    auto_detect: bool
    updated_utc: str
    reason: str

logger = logging.getLogger(__name__)


# Windows idle detection


def _windows_idle_seconds() -> float | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        last_input = LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)) == 0:  # type: ignore[attr-defined]
            return None
        tick_now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF  # type: ignore[attr-defined]
        idle_ms = (tick_now - last_input.dwTime) & 0xFFFFFFFF
        return max(0.0, idle_ms / 1000.0)
    except (OSError, ImportError, ValueError, AttributeError) as exc:
        logger.debug("Windows idle time detection failed: %s", exc)
        return None


# Gaming mode state read/write

DEFAULT_GAMING_PROCESSES = (
    "FortniteClient-Win64-Shipping.exe",
    "VALORANT-Win64-Shipping.exe",
    "r5apex.exe",
    "cs2.exe",
    "Overwatch.exe",
    "RocketLeague.exe",
    "GTA5.exe",
    "eldenring.exe",
)


def read_gaming_mode_state(state_path: Path | None = None) -> GamingModeState:
    """Read gaming mode state from *state_path*.

    When *state_path* is ``None``, resolves the default path via
    :func:`gaming_mode_state_path`.

    Returns a dict with keys ``enabled``, ``auto_detect``, ``updated_utc``,
    ``reason`` (all typed).  Falls back to a safe default if the file is
    missing or corrupt.
    """
    from jarvis_engine._shared import load_json_file

    if state_path is None:
        state_path = gaming_mode_state_path()
    default: GamingModeState = {"enabled": False, "auto_detect": False, "updated_utc": "", "reason": ""}
    raw = load_json_file(state_path, None, expected_type=dict)
    if raw is None:
        return default
    return {
        "enabled": bool(raw.get("enabled", False)),
        "auto_detect": bool(raw.get("auto_detect", False)),
        "updated_utc": str(raw.get("updated_utc", "")),
        "reason": str(raw.get("reason", "")),
    }


def write_gaming_mode_state(
    state: GamingModeState | dict[str, Any],
    state_path: Path | None = None,
) -> GamingModeState:
    """Atomically write *state* to *state_path* and return the normalised payload.

    When *state_path* is ``None``, resolves the default path via
    :func:`gaming_mode_state_path`.
    """
    from jarvis_engine._shared import atomic_write_json

    if state_path is None:
        state_path = gaming_mode_state_path()
    payload: GamingModeState = {
        "enabled": bool(state.get("enabled", False)),
        "auto_detect": bool(state.get("auto_detect", False)),
        "updated_utc": str(state.get("updated_utc", "")) or now_iso(),
        "reason": str(state.get("reason", "")).strip()[:200],
    }
    atomic_write_json(state_path, dict(payload))
    return payload


def load_gaming_processes(processes_path: Path | None = None) -> list[str]:
    """Load the list of game executable names from *processes_path*.

    When *processes_path* is ``None``, resolves the default path via
    :func:`gaming_processes_path`.

    An environment variable ``JARVIS_GAMING_PROCESSES`` overrides the file.
    Falls back to ``DEFAULT_GAMING_PROCESSES`` if the file is missing or
    empty.
    """
    env_override = os.getenv("JARVIS_GAMING_PROCESSES", "").strip()
    if env_override:
        return [item.strip() for item in env_override.split(",") if item.strip()]

    if processes_path is None:
        processes_path = gaming_processes_path()

    from jarvis_engine._shared import load_json_file

    raw = load_json_file(processes_path, None)
    if raw is None:
        return list(DEFAULT_GAMING_PROCESSES)

    if isinstance(raw, dict):
        values = raw.get("processes", [])
    elif isinstance(raw, list):
        values = raw
    else:
        values = []

    if not isinstance(values, list):
        return list(DEFAULT_GAMING_PROCESSES)
    processes = [str(item).strip() for item in values if str(item).strip()]
    return processes or list(DEFAULT_GAMING_PROCESSES)


# Game process detection with cache

_game_detect_cache: tuple[float, bool, str] = (0.0, False, "")
_game_detect_lock = threading.Lock()
_GAME_DETECT_CACHE_TTL = 30.0  # seconds


def detect_active_game_process(processes: list[str] | None = None) -> tuple[bool, str]:
    """Detect if a known game process is currently running.

    Parameters
    ----------
    processes:
        Explicit list of process names to scan for.  When *None* (default),
        uses ``DEFAULT_GAMING_PROCESSES``.

    Returns ``(found, process_name)`` where *found* is True when a match
    exists.  Results are cached for ``_GAME_DETECT_CACHE_TTL`` seconds.
    Thread-safe: concurrent callers are serialised via ``_game_detect_lock``.
    """
    global _game_detect_cache

    with _game_detect_lock:
        # Return cached result if still fresh
        cached_time, cached_found, cached_name = _game_detect_cache
        if (time.monotonic() - cached_time) < _GAME_DETECT_CACHE_TTL:
            return cached_found, cached_name

    if os.name != "nt":
        with _game_detect_lock:
            _game_detect_cache = (time.monotonic(), False, "")
        return False, ""
    if processes is None:
        processes = list(DEFAULT_GAMING_PROCESSES)
    patterns = [name.lower() for name in processes]
    if not patterns:
        with _game_detect_lock:
            _game_detect_cache = (time.monotonic(), False, "")
        return False, ""
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        with _game_detect_lock:
            _game_detect_cache = (time.monotonic(), False, "")
        return False, ""
    if result.returncode != 0:
        with _game_detect_lock:
            _game_detect_cache = (time.monotonic(), False, "")
        return False, ""

    running: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("info:"):
            continue
        try:
            row = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            logger.debug("Skipping unparseable tasklist CSV line: %s", line)
            continue
        if not row:
            continue
        running.append(row[0].strip().lower())

    for proc_name in running:
        for pattern in patterns:
            if proc_name == pattern or pattern in proc_name:
                with _game_detect_lock:
                    _game_detect_cache = (time.monotonic(), True, proc_name)
                return True, proc_name
    with _game_detect_lock:
        _game_detect_cache = (time.monotonic(), False, "")
    return False, ""


# No-arg convenience wrappers (resolve paths via repo_root)


def gaming_mode_state_path() -> Path:
    """Return the path to the gaming mode JSON state file."""
    return runtime_dir(repo_root()) / "gaming_mode.json"


def gaming_processes_path() -> Path:
    """Return the path to the gaming processes JSON config file."""
    return repo_root() / ".planning" / "gaming_processes.json"
