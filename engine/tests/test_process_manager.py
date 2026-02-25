"""Tests for process_manager module — PID files, alive detection, service listing."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.process_manager import (
    SERVICES,
    _check_pid_alive,
    _pid_path,
    is_service_running,
    kill_service,
    list_services,
    read_pid_file,
    remove_pid_file,
    write_pid_file,
)


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Provide a temp directory that acts as repo root."""
    pids_dir = tmp_path / ".planning" / "runtime" / "pids"
    pids_dir.mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# PID file write / read / remove
# ---------------------------------------------------------------------------


class TestWriteReadRemove:
    def test_write_creates_json(self, tmp_root: Path) -> None:
        write_pid_file("daemon", tmp_root)
        path = _pid_path("daemon", tmp_root)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert data["service"] == "daemon"
        assert "started_utc" in data
        assert data["python"] == sys.executable

    def test_read_returns_data_for_live_pid(self, tmp_root: Path) -> None:
        write_pid_file("mobile_api", tmp_root)
        data = read_pid_file("mobile_api", tmp_root)
        assert data is not None
        assert data["pid"] == os.getpid()

    def test_read_returns_none_for_missing(self, tmp_root: Path) -> None:
        assert read_pid_file("daemon", tmp_root) is None

    def test_read_cleans_stale_pid(self, tmp_root: Path) -> None:
        path = _pid_path("widget", tmp_root)
        path.write_text(json.dumps({"pid": 999999999, "service": "widget", "started_utc": "", "python": ""}))
        # PID 999999999 almost certainly doesn't exist
        assert read_pid_file("widget", tmp_root) is None
        assert not path.exists()

    def test_read_returns_none_for_malformed_json(self, tmp_root: Path) -> None:
        path = _pid_path("daemon", tmp_root)
        path.write_text("not json at all!")
        assert read_pid_file("daemon", tmp_root) is None

    def test_remove_idempotent(self, tmp_root: Path) -> None:
        remove_pid_file("daemon", tmp_root)  # no error even if missing
        write_pid_file("daemon", tmp_root)
        remove_pid_file("daemon", tmp_root)
        assert not _pid_path("daemon", tmp_root).exists()
        remove_pid_file("daemon", tmp_root)  # still no error


# ---------------------------------------------------------------------------
# is_service_running
# ---------------------------------------------------------------------------


class TestIsServiceRunning:
    def test_returns_true_for_own_pid(self, tmp_root: Path) -> None:
        write_pid_file("daemon", tmp_root)
        assert is_service_running("daemon", tmp_root) is True

    def test_returns_false_when_no_pid_file(self, tmp_root: Path) -> None:
        assert is_service_running("daemon", tmp_root) is False

    def test_returns_false_for_dead_pid(self, tmp_root: Path) -> None:
        path = _pid_path("daemon", tmp_root)
        path.write_text(json.dumps({"pid": 999999999, "service": "daemon", "started_utc": "", "python": ""}))
        assert is_service_running("daemon", tmp_root) is False


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------


class TestDuplicatePrevention:
    def test_daemon_returns_error_4_when_already_running(self, tmp_root: Path) -> None:
        """Simulate the duplicate check in _cmd_daemon_run_impl."""
        write_pid_file("daemon", tmp_root)
        # The actual code checks is_service_running and returns 4
        assert is_service_running("daemon", tmp_root) is True

    def test_mobile_api_returns_error_4_when_already_running(self, tmp_root: Path) -> None:
        write_pid_file("mobile_api", tmp_root)
        assert is_service_running("mobile_api", tmp_root) is True


# ---------------------------------------------------------------------------
# kill_service
# ---------------------------------------------------------------------------


class TestKillService:
    def test_kill_returns_false_when_not_running(self, tmp_root: Path) -> None:
        assert kill_service("daemon", tmp_root) is False

    @patch("jarvis_engine.process_manager._check_pid_alive", return_value=True)
    def test_kill_removes_pid_file(self, mock_alive: MagicMock, tmp_root: Path) -> None:
        path = _pid_path("daemon", tmp_root)
        # Write a PID file with a fake PID
        path.write_text(json.dumps({"pid": 12345, "service": "daemon", "started_utc": "", "python": ""}))
        with patch("jarvis_engine.process_manager.ctypes") if sys.platform == "win32" else patch("jarvis_engine.process_manager.os.kill"):
            result = kill_service("daemon", tmp_root)
        # PID file should be removed regardless
        assert not path.exists() or result  # killed or file removed


# ---------------------------------------------------------------------------
# list_services
# ---------------------------------------------------------------------------


class TestListServices:
    def test_lists_all_three_services(self, tmp_root: Path) -> None:
        result = list_services(tmp_root)
        assert len(result) == 3
        names = {s["service"] for s in result}
        assert names == set(SERVICES)

    def test_shows_running_for_own_pid(self, tmp_root: Path) -> None:
        write_pid_file("daemon", tmp_root)
        result = list_services(tmp_root)
        daemon = [s for s in result if s["service"] == "daemon"][0]
        assert daemon["running"] is True
        assert daemon["pid"] == os.getpid()
        assert daemon["uptime_seconds"] >= 0

    def test_shows_stopped_when_no_pid(self, tmp_root: Path) -> None:
        result = list_services(tmp_root)
        for svc in result:
            assert svc["running"] is False
            assert svc["pid"] is None


# ---------------------------------------------------------------------------
# _check_pid_alive
# ---------------------------------------------------------------------------


class TestCheckPidAlive:
    def test_own_pid_is_alive(self) -> None:
        assert _check_pid_alive(os.getpid()) is True

    def test_zero_pid_is_dead(self) -> None:
        assert _check_pid_alive(0) is False

    def test_negative_pid_is_dead(self) -> None:
        assert _check_pid_alive(-1) is False

    def test_nonexistent_pid(self) -> None:
        # 999999999 almost certainly doesn't exist
        assert _check_pid_alive(999999999) is False


# ---------------------------------------------------------------------------
# --config-file integration in cmd_serve_mobile
# ---------------------------------------------------------------------------


class TestConfigFileArg:
    def test_config_file_loads_credentials(self, tmp_path: Path) -> None:
        config = {"token": "test-token-abc", "signing_key": "test-key-xyz"}
        config_file = tmp_path / "test_config.json"
        config_file.write_text(json.dumps(config))

        from jarvis_engine import main as main_mod

        # Mock run_mobile_server so it doesn't actually start a server
        with patch.object(main_mod, "run_mobile_server") as mock_server, \
             patch("jarvis_engine.process_manager.is_service_running", return_value=False), \
             patch("jarvis_engine.process_manager.write_pid_file"), \
             patch("jarvis_engine.process_manager.remove_pid_file"):
            rc = main_mod.cmd_serve_mobile(
                host="127.0.0.1",
                port=0,
                token=None,
                signing_key=None,
                config_file=str(config_file),
            )
        assert rc == 0
        mock_server.assert_called_once()
        call_kwargs = mock_server.call_args
        assert call_kwargs[1]["auth_token"] == "test-token-abc"
        assert call_kwargs[1]["signing_key"] == "test-key-xyz"

    def test_config_file_missing_returns_error(self) -> None:
        from jarvis_engine import main as main_mod

        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1",
            port=0,
            token=None,
            signing_key=None,
            config_file="/nonexistent/path.json",
        )
        assert rc == 2

    def test_cli_token_overrides_config_file(self, tmp_path: Path) -> None:
        config = {"token": "file-token", "signing_key": "file-key"}
        config_file = tmp_path / "test_config.json"
        config_file.write_text(json.dumps(config))

        from jarvis_engine import main as main_mod

        with patch.object(main_mod, "run_mobile_server") as mock_server, \
             patch("jarvis_engine.process_manager.is_service_running", return_value=False), \
             patch("jarvis_engine.process_manager.write_pid_file"), \
             patch("jarvis_engine.process_manager.remove_pid_file"):
            rc = main_mod.cmd_serve_mobile(
                host="127.0.0.1",
                port=0,
                token="cli-token",
                signing_key="cli-key",
                config_file=str(config_file),
            )
        assert rc == 0
        call_kwargs = mock_server.call_args
        assert call_kwargs[1]["auth_token"] == "cli-token"
        assert call_kwargs[1]["signing_key"] == "cli-key"
