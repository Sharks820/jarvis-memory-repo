"""Shared utility functions used across multiple jarvis_engine modules.

Consolidates duplicated helpers to a single source of truth:
- atomic_write_json: safe JSON file writes with atomic replace
- safe_float / safe_int: type coercion with defaults
- check_path_within_root: path traversal guard
- win_hidden_subprocess_kwargs: Windows subprocess window suppression
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def atomic_write_json(
    path: Path,
    payload: dict[str, Any] | list[Any],
    *,
    retries: int = 3,
    secure: bool = True,
) -> None:
    """Write JSON to *path* atomically via tmp-write-then-replace.

    Args:
        path: Destination file path.
        payload: JSON-serializable data.
        retries: Number of retry attempts on PermissionError (Windows lock contention).
        secure: If True, attempt ``chmod 0o600`` on the destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=True, indent=2)
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        tmp = path.with_suffix(f"{path.suffix}.tmp.{attempt}")
        try:
            tmp.write_text(raw, encoding="utf-8")
            os.replace(str(tmp), str(path))
            if secure:
                try:
                    os.chmod(str(path), 0o600)
                except OSError:
                    pass
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.06 * (attempt + 1))
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    if last_error is not None:
        raise last_error


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Convert *value* to int, returning *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def check_path_within_root(path: Path, root: Path, label: str) -> None:
    """Resolve *path* and verify it stays within *root*.

    Raises ValueError if the resolved path escapes the root directory.
    """
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"{label} outside project root: {path}")


def win_hidden_subprocess_kwargs() -> dict[str, Any]:
    """Return subprocess kwargs to hide console windows on Windows.

    Returns an empty dict on non-Windows platforms.
    """
    if os.name != "nt":
        return {}
    import subprocess

    kwargs: dict[str, Any] = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if creationflags:
        kwargs["creationflags"] = creationflags
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    return kwargs
