"""Tests for unity_process_manager: kill_unity_tree and ensure_unity_not_running."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from jarvis_engine.ops.unity_process_manager import (
    UNITY_SERVICE_NAME,
    ensure_unity_not_running,
    kill_unity_tree,
)


# ---------------------------------------------------------------------------
# UNITY_SERVICE_NAME constant
# ---------------------------------------------------------------------------


def test_unity_service_name_constant() -> None:
    """UNITY_SERVICE_NAME is the expected string."""
    assert UNITY_SERVICE_NAME == "unity_editor"


# ---------------------------------------------------------------------------
# kill_unity_tree
# ---------------------------------------------------------------------------


def test_kill_unity_tree_windows_success(tmp_path: Path) -> None:
    """On Windows, kill_unity_tree calls taskkill /f /t /pid and returns True on rc=0."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with (
        patch("sys.platform", "win32"),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = kill_unity_tree(12345)
        assert result is True
        args = mock_run.call_args[0][0]
        assert "taskkill" in args
        assert "/f" in args
        assert "/t" in args
        assert "/pid" in args
        assert "12345" in args


def test_kill_unity_tree_windows_not_found(tmp_path: Path) -> None:
    """On Windows, kill_unity_tree returns False when taskkill reports failure."""
    mock_result = MagicMock()
    mock_result.returncode = 1

    with (
        patch("sys.platform", "win32"),
        patch("subprocess.run", return_value=mock_result),
    ):
        result = kill_unity_tree(99999)
        assert result is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only test")
def test_kill_unity_tree_posix_success() -> None:
    """On POSIX, kill_unity_tree calls os.killpg and returns True."""
    with (
        patch("sys.platform", "linux"),
        patch("os.getpgid", return_value=5678),
        patch("os.killpg") as mock_killpg,
    ):
        result = kill_unity_tree(5678)
        assert result is True
        mock_killpg.assert_called_once()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only test")
def test_kill_unity_tree_posix_not_found() -> None:
    """On POSIX, kill_unity_tree returns False when process is not found."""
    with (
        patch("sys.platform", "linux"),
        patch("os.getpgid", return_value=9999),
        patch("os.killpg", side_effect=ProcessLookupError),
    ):
        result = kill_unity_tree(9999)
        assert result is False


# ---------------------------------------------------------------------------
# ensure_unity_not_running
# ---------------------------------------------------------------------------


def test_ensure_unity_not_running_no_lockfile(tmp_path: Path) -> None:
    """No-op when no lockfile/PID file exists for Unity."""
    with patch(
        "jarvis_engine.ops.unity_process_manager.read_pid_file",
        return_value=None,
    ) as mock_read:
        ensure_unity_not_running(tmp_path)
        mock_read.assert_called_once_with(UNITY_SERVICE_NAME, tmp_path)


def test_ensure_unity_not_running_stale_lockfile(tmp_path: Path) -> None:
    """Kills tree and removes PID file when stale Unity PID file exists with live PID."""
    pid_info = {"pid": 42000, "service": "unity_editor"}

    with (
        patch(
            "jarvis_engine.ops.unity_process_manager.read_pid_file",
            return_value=pid_info,
        ),
        patch(
            "jarvis_engine.ops.unity_process_manager.kill_unity_tree",
            return_value=True,
        ) as mock_kill,
        patch(
            "jarvis_engine.ops.unity_process_manager.remove_pid_file",
        ) as mock_remove,
    ):
        ensure_unity_not_running(tmp_path)
        mock_kill.assert_called_once_with(42000)
        mock_remove.assert_called_once_with(UNITY_SERVICE_NAME, tmp_path)
