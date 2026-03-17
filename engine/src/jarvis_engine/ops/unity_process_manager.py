"""Unity Editor process tree management.

Extends the ``ops/process_manager.py`` PID-file pattern for Unity Editor
processes.  Key difference from regular services: Unity spawns child
processes (``UnityShaderCompiler.exe``, import workers) that must be killed
with ``taskkill /f /t`` on Windows for a full tree kill.  Simply calling
``proc.terminate()`` leaves those children as orphans that hold the project
lock and prevent Unity from reopening the project.

Typical usage::

    from jarvis_engine.ops.unity_process_manager import ensure_unity_not_running

    ensure_unity_not_running(root)          # kill stale instance before launch
    # ... launch Unity ...
    write_pid_file(UNITY_SERVICE_NAME, root)
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from jarvis_engine.ops.process_manager import read_pid_file, remove_pid_file

logger = logging.getLogger(__name__)

UNITY_SERVICE_NAME = "unity_editor"


def kill_unity_tree(pid: int) -> bool:
    """Kill Unity Editor and all child processes.

    On Windows uses ``taskkill /f /t /pid`` for a full process-tree kill.
    On POSIX uses ``os.killpg`` to kill the entire process group.

    Returns ``True`` on success (or process already gone), ``False`` on failure.
    """
    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/f", "/t", "/pid", str(pid)],
            capture_output=True,
            text=True,
        )
        success = result.returncode == 0
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            success = True
        except ProcessLookupError:
            success = False

    if success:
        logger.info("Killed Unity process tree (pid=%d)", pid)
    else:
        logger.warning("Failed to kill Unity process tree (pid=%d)", pid)
    return success


def ensure_unity_not_running(root: Path) -> None:
    """Kill any stale Unity Editor instance before launching a new one.

    Reads the Unity PID file via ``process_manager.read_pid_file``.  If a
    live entry is found, kills the full process tree and removes the PID file.
    No-op when no lockfile exists (normal first-launch case).
    """
    info = read_pid_file(UNITY_SERVICE_NAME, root)
    if info is None:
        return
    kill_unity_tree(info["pid"])
    remove_pid_file(UNITY_SERVICE_NAME, root)
