"""PID file management for Jarvis service instances.

Prevents duplicate processes and provides visibility/control over running
services (daemon, mobile_api, widget).  PID files live under
``.planning/runtime/pids/`` as JSON with metadata.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis_engine._shared import atomic_write_json

logger = logging.getLogger(__name__)

SERVICES = ("daemon", "mobile_api", "widget")

# Maximum acceptable age difference (seconds) between stored start time and
# process creation time before we consider the PID reused by another process.
_MAX_CREATION_DRIFT_S = 5.0

# How long to wait for a graceful shutdown (seconds) before escalating
# to a hard TerminateProcess / SIGKILL.
_GRACEFUL_TIMEOUT_S = 5.0

# ---------------------------------------------------------------------------
# PID directory helpers
# ---------------------------------------------------------------------------

def _pids_dir(root: Path) -> Path:
    from jarvis_engine._constants import runtime_dir
    return runtime_dir(root) / "pids"


def _pid_path(service: str, root: Path) -> Path:
    return _pids_dir(root) / f"{service}.pid"


# ---------------------------------------------------------------------------
# Process alive check (no psutil dependency)
# ---------------------------------------------------------------------------

def _check_pid_alive(pid: int) -> bool:
    """Return True if *pid* refers to a running process."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _check_pid_alive_win32(pid)
    # POSIX: signal 0 tests existence without sending a real signal.
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive but we lack permission


def _check_pid_alive_win32(pid: int) -> bool:
    """Windows-specific alive check via kernel32.OpenProcess."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return exit_code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)


def _get_process_create_time(pid: int) -> float | None:
    """Return the process creation time as a UTC timestamp, or None if unavailable."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        return _get_process_create_time_win32(pid)
    # POSIX: read from /proc/<pid>/stat if available (Linux)
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            # Field 22 (0-indexed: 21) is starttime in clock ticks since boot
            # Simpler approach: use the file's creation time
            return stat_path.stat().st_ctime
    except OSError as exc:
        logger.debug("Failed to read process stat for PID %d: %s", pid, exc)
    return None


def _get_process_create_time_win32(pid: int) -> float | None:
    """Return process creation time on Windows via kernel32.GetProcessTimes."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        # FILETIME structures (each is two DWORDs: low, high)
        creation = ctypes.c_ulonglong()
        exit_t = ctypes.c_ulonglong()
        kernel_t = ctypes.c_ulonglong()
        user_t = ctypes.c_ulonglong()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_t),
            ctypes.byref(kernel_t),
            ctypes.byref(user_t),
        ):
            return None
        # FILETIME is 100-nanosecond intervals since 1601-01-01
        # Convert to Unix epoch (seconds since 1970-01-01)
        EPOCH_DIFF = 116444736000000000  # 100-ns intervals between 1601 and 1970
        unix_us = (creation.value - EPOCH_DIFF) / 10_000_000.0
        return unix_us
    finally:
        kernel32.CloseHandle(handle)


def _verify_pid_identity(pid: int, stored_create_ts: float | None) -> bool:
    """Verify that *pid* still belongs to the process we originally started.

    Compares the stored process creation timestamp (``process_create_ts`` from
    the PID file) against the actual OS-reported process creation time.
    Returns True if identity is confirmed or cannot be checked (conservative).
    """
    if stored_create_ts is None:
        return True  # No stored creation time — assume valid (legacy PID files)
    create_time = _get_process_create_time(pid)
    if create_time is None:
        return True  # Cannot verify — assume valid to avoid false negatives
    return abs(create_time - stored_create_ts) < _MAX_CREATION_DRIFT_S


# ---------------------------------------------------------------------------
# PID file CRUD
# ---------------------------------------------------------------------------

def _lock_pid_file(service: str, root: Path):
    """Acquire an exclusive lock file for PID operations on *service*.

    Returns a context manager. Uses a separate .lock file to avoid
    conflicting with the atomic-write strategy of the PID file itself.
    """
    import contextlib

    lock_path = _pids_dir(root) / f"{service}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _lock():
        fd = None
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
            if sys.platform == "win32":
                import msvcrt
                # Write a sentinel byte so the file is non-empty before locking;
                # msvcrt.locking on an empty file may not enforce mutual exclusion.
                os.write(fd, b'\x00')
                os.lseek(fd, 0, os.SEEK_SET)
                # Non-blocking would use LK_NBLCK; blocking uses LK_LOCK
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fd is not None:
                if sys.platform == "win32":
                    try:
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    except OSError as exc:
                        logger.debug("Failed to unlock file: %s", exc)
                os.close(fd)

    return _lock()


def write_pid_file(service: str, root: Path) -> None:
    """Write a PID file for *service* with current process metadata.

    Uses file locking to prevent TOCTOU races where another process could
    start between the is_service_running() check and the PID file write.
    """
    with _lock_pid_file(service, root):
        # Re-check under lock: if another process already claimed the PID file, bail out
        existing = read_pid_file(service, root)
        if existing is not None and existing["pid"] != os.getpid():
            raise RuntimeError(
                f"Service {service!r} is already running (pid={existing['pid']})"
            )
        # Store the actual process creation time (for PID reuse detection) alongside
        # the PID file creation time (for uptime display).
        create_ts = _get_process_create_time(os.getpid())
        payload = {
            "pid": os.getpid(),
            "service": service,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "python": sys.executable,
        }
        if create_ts is not None:
            payload["process_create_ts"] = create_ts
        atomic_write_json(_pid_path(service, root), payload)
        logger.info("Wrote PID file for %s (pid=%d)", service, os.getpid())


def read_pid_file(service: str, root: Path) -> dict[str, Any] | None:
    """Read and validate a PID file.  Returns ``None`` if missing or stale.

    Validates both that the PID is alive AND that its creation time matches
    the stored ``started_utc`` to guard against PID reuse by the OS.
    """
    path = _pid_path(service, root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    pid = data.get("pid")
    if not isinstance(pid, int) or not _check_pid_alive(pid):
        # Stale PID file — clean up silently.
        _remove_pid_file_path(path)
        return None
    # Guard against PID reuse: verify the process creation time matches
    stored_create_ts = data.get("process_create_ts")
    if not _verify_pid_identity(pid, stored_create_ts):
        logger.warning(
            "PID %d for %s exists but creation time does not match stored "
            "process_create_ts=%s — likely PID reuse. Cleaning up stale PID file.",
            pid, service, stored_create_ts,
        )
        _remove_pid_file_path(path)
        return None
    return data


def remove_pid_file(service: str, root: Path) -> None:
    """Remove PID file for *service* (idempotent)."""
    _remove_pid_file_path(_pid_path(service, root))


def _remove_pid_file_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Failed to remove PID file %s: %s", path, exc)


# ---------------------------------------------------------------------------
# High-level queries
# ---------------------------------------------------------------------------

def is_service_running(service: str, root: Path) -> bool:
    """Return True if *service* has a live PID file."""
    return read_pid_file(service, root) is not None


def _graceful_shutdown(pid: int) -> bool:
    """Attempt to gracefully stop *pid* and wait up to ``_GRACEFUL_TIMEOUT_S``.

    On Windows, sends ``CTRL_C_EVENT`` via ``os.kill`` to trigger a
    ``KeyboardInterrupt`` in the target Python process.  On POSIX, sends
    ``SIGTERM``.

    Returns True if the process exited within the timeout, False if it is
    still alive and a hard kill is needed.

    NOTE: ``signal.CTRL_C_EVENT`` on Windows is delivered to all processes
    sharing the same console.  If the target runs in a separate console (the
    normal case with ``Start-Process -WindowStyle Hidden``), the signal may
    not reach it.  The caller should fall back to ``TerminateProcess`` when
    this returns False.
    """
    try:
        if sys.platform == "win32":
            # CTRL_C_EVENT affects entire console group on Windows,
            # potentially killing the calling process. Skip graceful
            # shutdown and fall through to hard kill.
            return False
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, PermissionError):
        # Process already gone or inaccessible — treat as success.
        return True

    # Poll until the process exits or we run out of patience.
    deadline = time.monotonic() + _GRACEFUL_TIMEOUT_S
    while time.monotonic() < deadline:
        if not _check_pid_alive(pid):
            return True
        time.sleep(0.25)
    return not _check_pid_alive(pid)


def _hard_kill(pid: int) -> None:
    """Forcefully terminate *pid* (TerminateProcess on Windows, SIGKILL on POSIX)."""
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
    else:
        os.kill(pid, signal.SIGKILL)


def kill_service(service: str, root: Path, *, force: bool = False) -> bool:
    """Terminate *service* process and remove its PID file.  Returns True if killed.

    By default attempts a graceful shutdown first (CTRL_C_EVENT / SIGTERM),
    waiting up to ``_GRACEFUL_TIMEOUT_S`` seconds before escalating to a hard
    kill.  Pass ``force=True`` to skip the graceful attempt and immediately
    use TerminateProcess / SIGKILL.

    Verifies process identity (creation time) before sending the kill signal
    to avoid killing an unrelated process that reused the same PID.
    """
    info = read_pid_file(service, root)
    if info is None:
        return False
    pid = info["pid"]
    stored_create_ts = info.get("process_create_ts")
    # Double-check identity right before kill (read_pid_file already checks,
    # but another process could have replaced the original between read and kill)
    if not _verify_pid_identity(pid, stored_create_ts):
        logger.warning(
            "PID %d for %s no longer matches stored start time — "
            "refusing to kill (possible PID reuse). Removing stale PID file.",
            pid, service,
        )
        remove_pid_file(service, root)
        return False
    try:
        if force:
            _hard_kill(pid)
        else:
            exited = _graceful_shutdown(pid)
            if not exited:
                logger.info(
                    "Graceful shutdown of %s (pid=%d) timed out, escalating to hard kill.",
                    service, pid,
                )
                _hard_kill(pid)
    except (OSError, PermissionError) as exc:
        logger.warning("Failed to kill %s (pid=%d): %s", service, pid, exc)
        return False
    remove_pid_file(service, root)
    logger.info("Killed %s (pid=%d)", service, pid)
    return True


def list_services(root: Path) -> list[dict[str, Any]]:
    """Return status dicts for all known services."""
    results = []
    for svc in SERVICES:
        info = read_pid_file(svc, root)
        if info is not None:
            started = info.get("started_utc", "")
            uptime_s = 0
            if started:
                try:
                    start_dt = datetime.fromisoformat(started)
                    uptime_s = int((datetime.now(timezone.utc) - start_dt).total_seconds())
                except (ValueError, TypeError):
                    logger.debug("Invalid started_utc format for service %s: %s", svc, started)
            results.append({
                "service": svc,
                "running": True,
                "pid": info["pid"],
                "started_utc": started,
                "uptime_seconds": uptime_s,
                "python": info.get("python", ""),
            })
        else:
            results.append({
                "service": svc,
                "running": False,
                "pid": None,
                "started_utc": None,
                "uptime_seconds": 0,
                "python": "",
            })
    return results


# ---------------------------------------------------------------------------
# Service watchdog
# ---------------------------------------------------------------------------

def check_and_restart_services(
    root: Path,
    restart_callback: Any | None = None,
) -> list[str]:
    """Check for crashed services and optionally restart them.

    Iterates all registered services looking for *stale* PID files — a PID
    file exists on disk but the process behind it is dead.  For each such
    service the stale PID file is cleaned up and, if *restart_callback* is
    provided, ``restart_callback(service_name)`` is called so the caller
    can decide how to bring the service back.

    Returns the list of service names that were found dead (regardless of
    whether the callback was called or succeeded).
    """
    dead_services: list[str] = []
    for svc in SERVICES:
        path = _pid_path(svc, root)
        if not path.exists():
            continue
        # Try to read the raw JSON without the auto-cleanup side effect
        # of read_pid_file so we can distinguish "file present but stale"
        # from "file absent".
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt PID file — clean it up.
            _remove_pid_file_path(path)
            dead_services.append(svc)
            logger.warning("Watchdog: corrupt PID file for %s, cleaned up.", svc)
            if restart_callback is not None:
                try:
                    restart_callback(svc)
                except (OSError, RuntimeError, ValueError):  # noqa: BLE001
                    logger.warning("Watchdog: restart callback failed for %s", svc, exc_info=True)
            continue

        pid = data.get("pid")
        if not isinstance(pid, int):
            _remove_pid_file_path(path)
            dead_services.append(svc)
            logger.warning("Watchdog: invalid PID in file for %s, cleaned up.", svc)
            if restart_callback is not None:
                try:
                    restart_callback(svc)
                except (OSError, RuntimeError, ValueError):  # noqa: BLE001
                    logger.warning("Watchdog: restart callback failed for %s", svc, exc_info=True)
            continue

        if _check_pid_alive(pid):
            # Also verify identity (guards against PID reuse)
            stored_create_ts = data.get("process_create_ts")
            if _verify_pid_identity(pid, stored_create_ts):
                continue  # Service is healthy.
            # PID was reused by another process — treat as dead.
            logger.warning(
                "Watchdog: PID %d for %s is alive but identity mismatch (PID reuse). "
                "Cleaning stale PID file.", pid, svc,
            )

        # Process is dead or PID was reused — clean up and notify.
        _remove_pid_file_path(path)
        dead_services.append(svc)
        logger.warning("Watchdog: %s (pid=%s) found dead, cleaned stale PID file.", svc, pid)
        if restart_callback is not None:
            try:
                restart_callback(svc)
            except (OSError, RuntimeError, ValueError):  # noqa: BLE001
                logger.warning("Watchdog: restart callback failed for %s", svc, exc_info=True)

    return dead_services
