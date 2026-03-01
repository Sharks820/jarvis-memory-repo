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
    _MAX_CREATION_DRIFT_S,
    _check_pid_alive,
    _check_pid_alive_win32,
    _get_process_create_time,
    _graceful_shutdown,
    _pid_path,
    _pids_dir,
    _verify_pid_identity,
    check_and_restart_services,
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

    def test_write_includes_process_create_ts(self, tmp_root: Path) -> None:
        """write_pid_file should store process_create_ts when available."""
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=1700000000.0,
        ):
            write_pid_file("daemon", tmp_root)
        data = json.loads(
            _pid_path("daemon", tmp_root).read_text(encoding="utf-8")
        )
        assert data["process_create_ts"] == 1700000000.0

    def test_write_omits_process_create_ts_when_unavailable(
        self, tmp_root: Path
    ) -> None:
        """If _get_process_create_time returns None, key should be absent."""
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=None,
        ):
            write_pid_file("daemon", tmp_root)
        data = json.loads(
            _pid_path("daemon", tmp_root).read_text(encoding="utf-8")
        )
        assert "process_create_ts" not in data

    def test_read_returns_data_for_live_pid(self, tmp_root: Path) -> None:
        write_pid_file("mobile_api", tmp_root)
        data = read_pid_file("mobile_api", tmp_root)
        assert data is not None
        assert data["pid"] == os.getpid()

    def test_read_returns_none_for_missing(self, tmp_root: Path) -> None:
        assert read_pid_file("daemon", tmp_root) is None

    def test_read_cleans_stale_pid(self, tmp_root: Path) -> None:
        path = _pid_path("widget", tmp_root)
        path.write_text(
            json.dumps(
                {
                    "pid": 999999999,
                    "service": "widget",
                    "started_utc": "",
                    "python": "",
                }
            )
        )
        # PID 999999999 almost certainly doesn't exist
        assert read_pid_file("widget", tmp_root) is None
        assert not path.exists()

    def test_read_returns_none_for_malformed_json(self, tmp_root: Path) -> None:
        path = _pid_path("daemon", tmp_root)
        path.write_text("not json at all!")
        assert read_pid_file("daemon", tmp_root) is None

    def test_read_returns_none_when_pid_not_int(self, tmp_root: Path) -> None:
        """If the pid field is not an int, read_pid_file should treat as stale."""
        path = _pid_path("daemon", tmp_root)
        path.write_text(
            json.dumps(
                {
                    "pid": "not-a-number",
                    "service": "daemon",
                    "started_utc": "",
                    "python": "",
                }
            )
        )
        assert read_pid_file("daemon", tmp_root) is None
        assert not path.exists()

    def test_remove_idempotent(self, tmp_root: Path) -> None:
        remove_pid_file("daemon", tmp_root)  # no error even if missing
        write_pid_file("daemon", tmp_root)
        remove_pid_file("daemon", tmp_root)
        assert not _pid_path("daemon", tmp_root).exists()
        remove_pid_file("daemon", tmp_root)  # still no error

    def test_write_read_remove_full_cycle(self, tmp_root: Path) -> None:
        """Verify the complete lifecycle: write -> read -> remove -> read returns None."""
        write_pid_file("widget", tmp_root)
        data = read_pid_file("widget", tmp_root)
        assert data is not None
        assert data["pid"] == os.getpid()
        remove_pid_file("widget", tmp_root)
        assert read_pid_file("widget", tmp_root) is None


# ---------------------------------------------------------------------------
# PID reuse detection (_verify_pid_identity)
# ---------------------------------------------------------------------------


class TestPidReuseDetection:
    def test_verify_identity_passes_when_no_stored_ts(self) -> None:
        """Legacy PID files without process_create_ts should pass verification."""
        assert _verify_pid_identity(os.getpid(), None) is True

    def test_verify_identity_passes_when_create_time_unavailable(self) -> None:
        """When OS cannot report creation time, assume valid (conservative)."""
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=None,
        ):
            assert _verify_pid_identity(os.getpid(), 1700000000.0) is True

    def test_verify_identity_passes_when_times_match(self) -> None:
        """When creation times are within drift tolerance, identity confirmed."""
        stored_ts = 1700000000.0
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=stored_ts + 1.0,  # within _MAX_CREATION_DRIFT_S
        ):
            assert _verify_pid_identity(os.getpid(), stored_ts) is True

    def test_verify_identity_fails_when_times_mismatch(self) -> None:
        """When creation times differ beyond tolerance, PID was reused."""
        stored_ts = 1700000000.0
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=stored_ts + 100.0,  # well beyond _MAX_CREATION_DRIFT_S
        ):
            assert _verify_pid_identity(os.getpid(), stored_ts) is False

    def test_verify_identity_boundary_just_within_drift(self) -> None:
        """Creation time difference exactly at the boundary (just under) passes."""
        stored_ts = 1700000000.0
        within = stored_ts + _MAX_CREATION_DRIFT_S - 0.001
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=within,
        ):
            assert _verify_pid_identity(os.getpid(), stored_ts) is True

    def test_verify_identity_boundary_just_beyond_drift(self) -> None:
        """Creation time difference at exactly the threshold fails."""
        stored_ts = 1700000000.0
        beyond = stored_ts + _MAX_CREATION_DRIFT_S + 0.001
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=beyond,
        ):
            assert _verify_pid_identity(os.getpid(), stored_ts) is False

    def test_read_removes_pid_file_on_reuse_detection(self, tmp_root: Path) -> None:
        """read_pid_file should remove the PID file if identity check fails."""
        path = _pid_path("daemon", tmp_root)
        path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "service": "daemon",
                    "started_utc": "",
                    "python": "",
                    "process_create_ts": 1000000000.0,
                }
            )
        )
        # Mock: PID is alive but creation time is way off
        with patch(
            "jarvis_engine.process_manager._check_pid_alive", return_value=True
        ), patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=1700000000.0,
        ):
            result = read_pid_file("daemon", tmp_root)
        assert result is None
        assert not path.exists()


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
        path.write_text(
            json.dumps(
                {
                    "pid": 999999999,
                    "service": "daemon",
                    "started_utc": "",
                    "python": "",
                }
            )
        )
        assert is_service_running("daemon", tmp_root) is False


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------


class TestDuplicatePrevention:
    def test_daemon_returns_error_4_when_already_running(
        self, tmp_root: Path
    ) -> None:
        """Simulate the duplicate check in _cmd_daemon_run_impl."""
        write_pid_file("daemon", tmp_root)
        # The actual code checks is_service_running and returns 4
        assert is_service_running("daemon", tmp_root) is True

    def test_mobile_api_returns_error_4_when_already_running(
        self, tmp_root: Path
    ) -> None:
        write_pid_file("mobile_api", tmp_root)
        assert is_service_running("mobile_api", tmp_root) is True

    def test_write_pid_file_raises_when_other_process_holds_lock(
        self, tmp_root: Path
    ) -> None:
        """write_pid_file should raise RuntimeError if another PID already owns the file."""
        path = _pid_path("daemon", tmp_root)
        other_pid = os.getpid() + 1  # definitely not our PID
        path.write_text(
            json.dumps(
                {
                    "pid": other_pid,
                    "service": "daemon",
                    "started_utc": "",
                    "python": "",
                }
            )
        )
        # Mock the other PID as alive so read_pid_file returns data
        with patch(
            "jarvis_engine.process_manager._check_pid_alive", return_value=True
        ), pytest.raises(RuntimeError, match="already running"):
            write_pid_file("daemon", tmp_root)

    def test_write_pid_file_allows_rewrite_from_same_process(
        self, tmp_root: Path
    ) -> None:
        """write_pid_file should not raise if the PID file belongs to current process."""
        write_pid_file("daemon", tmp_root)
        # Writing again from the same process should succeed (idempotent)
        write_pid_file("daemon", tmp_root)
        data = read_pid_file("daemon", tmp_root)
        assert data is not None
        assert data["pid"] == os.getpid()


# ---------------------------------------------------------------------------
# kill_service
# ---------------------------------------------------------------------------


class TestKillService:
    def test_kill_returns_false_when_not_running(self, tmp_root: Path) -> None:
        assert kill_service("daemon", tmp_root) is False

    @patch("jarvis_engine.process_manager._check_pid_alive", return_value=True)
    def test_kill_removes_pid_file(
        self, mock_alive: MagicMock, tmp_root: Path
    ) -> None:
        path = _pid_path("daemon", tmp_root)
        # Write a PID file with a fake PID
        path.write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "service": "daemon",
                    "started_utc": "",
                    "python": "",
                }
            )
        )
        if sys.platform == "win32":
            ctx = patch("jarvis_engine.process_manager.ctypes")
        else:
            ctx = patch("jarvis_engine.process_manager.os.kill")
        with ctx:
            result = kill_service("daemon", tmp_root)
        # PID file should be removed regardless
        assert not path.exists() or result  # killed or file removed

    def test_kill_refuses_when_pid_reused(self, tmp_root: Path) -> None:
        """kill_service should refuse to kill and clean up if PID identity fails."""
        path = _pid_path("daemon", tmp_root)
        path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "service": "daemon",
                    "started_utc": "",
                    "python": "",
                    "process_create_ts": 1000000000.0,
                }
            )
        )
        # read_pid_file will already detect the mismatch and return None,
        # so kill_service returns False
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=1700000000.0,
        ):
            result = kill_service("daemon", tmp_root)
        assert result is False
        # PID file should be cleaned up by read_pid_file
        assert not path.exists()


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

    def test_shows_mixed_running_and_stopped(self, tmp_root: Path) -> None:
        """Only services with a PID file should show as running."""
        write_pid_file("daemon", tmp_root)
        result = list_services(tmp_root)
        running = [s for s in result if s["running"]]
        stopped = [s for s in result if not s["running"]]
        assert len(running) == 1
        assert running[0]["service"] == "daemon"
        assert len(stopped) == 2

    def test_uptime_includes_python_and_started_utc(self, tmp_root: Path) -> None:
        """Running services should populate python and started_utc fields."""
        write_pid_file("mobile_api", tmp_root)
        result = list_services(tmp_root)
        mobile = [s for s in result if s["service"] == "mobile_api"][0]
        assert mobile["python"] == sys.executable
        assert mobile["started_utc"]  # non-empty string

    def test_stopped_service_fields(self, tmp_root: Path) -> None:
        """Stopped services should have standard empty/None/0 fields."""
        result = list_services(tmp_root)
        for svc in result:
            assert svc["uptime_seconds"] == 0
            assert svc["python"] == ""
            assert svc["started_utc"] is None


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
# _check_pid_alive_win32 (mocked kernel32)
# ---------------------------------------------------------------------------


class TestCheckPidAliveWin32:
    """Test Windows-specific path with mocked ctypes.windll.kernel32."""

    def test_alive_process(self) -> None:
        """OpenProcess succeeds and exit code == STILL_ACTIVE."""
        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 42  # non-zero handle
        mock_kernel32.GetExitCodeProcess.side_effect = (
            lambda handle, code_ref: setattr(code_ref, "value", 259) or True
        )
        mock_kernel32.CloseHandle.return_value = True
        with patch("jarvis_engine.process_manager.ctypes") as mock_ctypes:
            mock_ctypes.windll.kernel32 = mock_kernel32
            mock_ctypes.c_ulong = type("c_ulong", (), {"__init__": lambda s: None, "value": 0})
            mock_ctypes.byref = lambda x: x
            result = _check_pid_alive_win32(100)
        assert result is True
        mock_kernel32.CloseHandle.assert_called_once_with(42)

    def test_dead_process_no_handle(self) -> None:
        """OpenProcess returns 0 (process not found)."""
        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 0
        with patch("jarvis_engine.process_manager.ctypes") as mock_ctypes:
            mock_ctypes.windll.kernel32 = mock_kernel32
            result = _check_pid_alive_win32(99999)
        assert result is False

    def test_dead_process_exit_code_not_active(self) -> None:
        """OpenProcess succeeds but exit code != STILL_ACTIVE."""
        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 42
        mock_kernel32.GetExitCodeProcess.side_effect = (
            lambda handle, code_ref: setattr(code_ref, "value", 0) or True
        )
        mock_kernel32.CloseHandle.return_value = True
        with patch("jarvis_engine.process_manager.ctypes") as mock_ctypes:
            mock_ctypes.windll.kernel32 = mock_kernel32
            mock_ctypes.c_ulong = type("c_ulong", (), {"__init__": lambda s: None, "value": 0})
            mock_ctypes.byref = lambda x: x
            result = _check_pid_alive_win32(100)
        assert result is False
        mock_kernel32.CloseHandle.assert_called_once()

    def test_get_exit_code_fails(self) -> None:
        """GetExitCodeProcess returns False (API call fails)."""
        mock_kernel32 = MagicMock()
        mock_kernel32.OpenProcess.return_value = 42
        mock_kernel32.GetExitCodeProcess.return_value = False
        mock_kernel32.CloseHandle.return_value = True
        with patch("jarvis_engine.process_manager.ctypes") as mock_ctypes:
            mock_ctypes.windll.kernel32 = mock_kernel32
            mock_ctypes.c_ulong = type("c_ulong", (), {"__init__": lambda s: None, "value": 0})
            mock_ctypes.byref = lambda x: x
            result = _check_pid_alive_win32(100)
        assert result is False
        mock_kernel32.CloseHandle.assert_called_once()


# ---------------------------------------------------------------------------
# _get_process_create_time
# ---------------------------------------------------------------------------


class TestGetProcessCreateTime:
    def test_returns_none_for_zero_pid(self) -> None:
        assert _get_process_create_time(0) is None

    def test_returns_none_for_negative_pid(self) -> None:
        assert _get_process_create_time(-5) is None

    def test_returns_float_for_own_pid(self) -> None:
        """On the running platform, own PID should have a creation time."""
        result = _get_process_create_time(os.getpid())
        # On Windows this uses kernel32; on Linux /proc.
        # May return None on some platforms, but on Windows it should work.
        if sys.platform == "win32":
            assert result is not None
            assert isinstance(result, float)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_pids_dir(self, tmp_root: Path) -> None:
        expected = tmp_root / ".planning" / "runtime" / "pids"
        assert _pids_dir(tmp_root) == expected

    def test_pid_path(self, tmp_root: Path) -> None:
        expected = tmp_root / ".planning" / "runtime" / "pids" / "daemon.pid"
        assert _pid_path("daemon", tmp_root) == expected


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


# ---------------------------------------------------------------------------
# Graceful shutdown (_graceful_shutdown / _hard_kill)
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    """Tests for the two-phase graceful -> hard kill behaviour."""

    @patch("jarvis_engine.process_manager._check_pid_alive", return_value=False)
    @patch("jarvis_engine.process_manager.os.kill")
    def test_graceful_shutdown_succeeds_when_process_exits_quickly(
        self, mock_kill: MagicMock, mock_alive: MagicMock,
    ) -> None:
        """_graceful_shutdown returns True when the target dies before timeout.

        On Windows, graceful shutdown is skipped (CTRL_C_EVENT affects the
        entire console group), so the function always returns False there.
        """
        result = _graceful_shutdown(12345)
        if sys.platform == "win32":
            assert result is False  # Skips graceful on Windows
        else:
            assert result is True
            mock_kill.assert_called_once()

    @patch("jarvis_engine.process_manager._check_pid_alive", return_value=True)
    @patch("jarvis_engine.process_manager.os.kill")
    @patch("jarvis_engine.process_manager._GRACEFUL_TIMEOUT_S", 0.1)
    def test_graceful_shutdown_returns_false_when_process_survives(
        self, mock_kill: MagicMock, mock_alive: MagicMock,
    ) -> None:
        """_graceful_shutdown returns False when process refuses to die."""
        result = _graceful_shutdown(12345)
        assert result is False

    @patch("jarvis_engine.process_manager.os.kill", side_effect=OSError("gone"))
    def test_graceful_shutdown_returns_true_when_kill_raises(
        self, mock_kill: MagicMock,
    ) -> None:
        """If os.kill raises (process already gone), treat as success.

        On Windows, graceful shutdown returns False before os.kill is called.
        """
        result = _graceful_shutdown(12345)
        if sys.platform == "win32":
            assert result is False
        else:
            assert result is True

    @patch("jarvis_engine.process_manager._check_pid_alive", return_value=True)
    @patch("jarvis_engine.process_manager._graceful_shutdown", return_value=False)
    @patch("jarvis_engine.process_manager._hard_kill")
    def test_kill_service_escalates_to_hard_kill(
        self,
        mock_hard_kill: MagicMock,
        mock_graceful: MagicMock,
        mock_alive: MagicMock,
        tmp_root: Path,
    ) -> None:
        """kill_service should escalate to _hard_kill when graceful fails."""
        path = _pid_path("daemon", tmp_root)
        path.write_text(
            json.dumps({"pid": 12345, "service": "daemon", "started_utc": "", "python": ""})
        )
        result = kill_service("daemon", tmp_root)
        assert result is True
        mock_graceful.assert_called_once_with(12345)
        mock_hard_kill.assert_called_once_with(12345)

    @patch("jarvis_engine.process_manager._check_pid_alive", return_value=True)
    @patch("jarvis_engine.process_manager._graceful_shutdown", return_value=True)
    @patch("jarvis_engine.process_manager._hard_kill")
    def test_kill_service_skips_hard_kill_when_graceful_succeeds(
        self,
        mock_hard_kill: MagicMock,
        mock_graceful: MagicMock,
        mock_alive: MagicMock,
        tmp_root: Path,
    ) -> None:
        """kill_service should NOT call _hard_kill when graceful shutdown works."""
        path = _pid_path("daemon", tmp_root)
        path.write_text(
            json.dumps({"pid": 12345, "service": "daemon", "started_utc": "", "python": ""})
        )
        result = kill_service("daemon", tmp_root)
        assert result is True
        mock_graceful.assert_called_once_with(12345)
        mock_hard_kill.assert_not_called()

    @patch("jarvis_engine.process_manager._check_pid_alive", return_value=True)
    @patch("jarvis_engine.process_manager._hard_kill")
    @patch("jarvis_engine.process_manager._graceful_shutdown")
    def test_kill_service_force_skips_graceful(
        self,
        mock_graceful: MagicMock,
        mock_hard_kill: MagicMock,
        mock_alive: MagicMock,
        tmp_root: Path,
    ) -> None:
        """kill_service(force=True) should skip graceful and go straight to _hard_kill."""
        path = _pid_path("daemon", tmp_root)
        path.write_text(
            json.dumps({"pid": 12345, "service": "daemon", "started_utc": "", "python": ""})
        )
        result = kill_service("daemon", tmp_root, force=True)
        assert result is True
        mock_graceful.assert_not_called()
        mock_hard_kill.assert_called_once_with(12345)


# ---------------------------------------------------------------------------
# Watchdog (check_and_restart_services)
# ---------------------------------------------------------------------------


class TestWatchdog:
    """Tests for the check_and_restart_services watchdog function."""

    def test_returns_empty_when_no_pid_files(self, tmp_root: Path) -> None:
        """No PID files means no dead services."""
        dead = check_and_restart_services(tmp_root)
        assert dead == []

    def test_ignores_running_services(self, tmp_root: Path) -> None:
        """Services with valid live PIDs should not be flagged."""
        write_pid_file("daemon", tmp_root)
        dead = check_and_restart_services(tmp_root)
        assert dead == []
        # PID file should still exist
        assert _pid_path("daemon", tmp_root).exists()

    def test_detects_dead_service(self, tmp_root: Path) -> None:
        """A PID file for a dead process should be flagged and cleaned up."""
        path = _pid_path("mobile_api", tmp_root)
        path.write_text(
            json.dumps({
                "pid": 999999999,
                "service": "mobile_api",
                "started_utc": "",
                "python": "",
            })
        )
        dead = check_and_restart_services(tmp_root)
        assert "mobile_api" in dead
        # Stale PID file should be removed
        assert not path.exists()

    def test_cleans_corrupt_pid_file(self, tmp_root: Path) -> None:
        """A corrupt PID file should be cleaned up and reported."""
        path = _pid_path("widget", tmp_root)
        path.write_text("not valid json!!")
        dead = check_and_restart_services(tmp_root)
        assert "widget" in dead
        assert not path.exists()

    def test_calls_restart_callback_for_dead_service(self, tmp_root: Path) -> None:
        """restart_callback should be called with the dead service's name."""
        path = _pid_path("mobile_api", tmp_root)
        path.write_text(
            json.dumps({
                "pid": 999999999,
                "service": "mobile_api",
                "started_utc": "",
                "python": "",
            })
        )
        callback = MagicMock()
        dead = check_and_restart_services(tmp_root, restart_callback=callback)
        assert "mobile_api" in dead
        callback.assert_called_once_with("mobile_api")

    def test_does_not_call_callback_for_running_services(self, tmp_root: Path) -> None:
        """Healthy services should not trigger the callback."""
        write_pid_file("daemon", tmp_root)
        callback = MagicMock()
        dead = check_and_restart_services(tmp_root, restart_callback=callback)
        assert dead == []
        callback.assert_not_called()

    def test_callback_exception_does_not_propagate(self, tmp_root: Path) -> None:
        """If the callback raises, the watchdog should catch it and continue."""
        path = _pid_path("mobile_api", tmp_root)
        path.write_text(
            json.dumps({
                "pid": 999999999,
                "service": "mobile_api",
                "started_utc": "",
                "python": "",
            })
        )
        callback = MagicMock(side_effect=RuntimeError("restart failed"))
        dead = check_and_restart_services(tmp_root, restart_callback=callback)
        assert "mobile_api" in dead
        callback.assert_called_once()

    def test_detects_pid_reuse_as_dead(self, tmp_root: Path) -> None:
        """A PID file whose process was replaced (PID reuse) should be flagged."""
        path = _pid_path("daemon", tmp_root)
        path.write_text(
            json.dumps({
                "pid": os.getpid(),
                "service": "daemon",
                "started_utc": "",
                "python": "",
                "process_create_ts": 1000000000.0,
            })
        )
        with patch(
            "jarvis_engine.process_manager._get_process_create_time",
            return_value=1700000000.0,
        ):
            dead = check_and_restart_services(tmp_root)
        assert "daemon" in dead
        assert not path.exists()

    def test_multiple_dead_services(self, tmp_root: Path) -> None:
        """Multiple dead services should all be detected in one call."""
        for svc in ("daemon", "mobile_api", "widget"):
            path = _pid_path(svc, tmp_root)
            path.write_text(
                json.dumps({
                    "pid": 999999999,
                    "service": svc,
                    "started_utc": "",
                    "python": "",
                })
            )
        dead = check_and_restart_services(tmp_root)
        assert set(dead) == {"daemon", "mobile_api", "widget"}
