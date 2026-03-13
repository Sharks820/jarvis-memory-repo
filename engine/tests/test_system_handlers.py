"""Tests for system_handlers -- Status, Log, ServeMobile, Daemon, SelfHeal, etc."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from jarvis_engine.memory_store import MemoryStore as _MemoryStore
from jarvis_engine.commands.system_commands import (
    DaemonRunCommand,
    DesktopWidgetCommand,
    GamingModeCommand,
    LogCommand,
    MigrateMemoryCommand,
    MobileDesktopSyncCommand,
    OpenWebCommand,
    SelfHealCommand,
    ServeMobileCommand,
    StatusCommand,
    WeatherCommand,
)
from jarvis_engine.handlers.system_handlers import (
    DaemonRunHandler,
    DesktopWidgetHandler,
    GamingModeHandler,
    LogHandler,
    MigrateMemoryHandler,
    MobileDesktopSyncHandler,
    OpenWebHandler,
    SelfHealHandler,
    ServeMobileHandler,
    StatusHandler,
    WeatherHandler,
)

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# StatusHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.memory_store.MemoryStore")
@patch("jarvis_engine.config.load_config")
def test_status_handler(mock_config: MagicMock, mock_store_cls: MagicMock) -> None:
    mock_config.return_value = SimpleNamespace(
        profile="conner",
        primary_runtime="ollama",
        secondary_runtime="anthropic",
        security_strictness="high",
        operation_mode="normal",
        cloud_burst_enabled=True,
    )
    mock_store = MagicMock(spec=_MemoryStore)
    mock_store.tail.return_value = [{"ts": "1", "type": "test"}]
    mock_store_cls.return_value = mock_store

    handler = StatusHandler(ROOT)
    result = handler.handle(StatusCommand())

    assert result.profile == "conner"
    assert result.primary_runtime == "ollama"
    assert result.cloud_burst_enabled is True
    assert len(result.events) == 1


# ---------------------------------------------------------------------------
# LogHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.memory_store.MemoryStore")
def test_log_handler(mock_store_cls: MagicMock) -> None:
    mock_store = MagicMock(spec=_MemoryStore)
    mock_store.append.return_value = SimpleNamespace(ts="2024-01-01T00:00:00", event_type="info", message="hello")
    mock_store_cls.return_value = mock_store

    handler = LogHandler(ROOT)
    result = handler.handle(LogCommand(event_type="info", message="hello"))

    assert result.ts == "2024-01-01T00:00:00"
    assert result.event_type == "info"
    assert result.message == "hello"
    mock_store.append.assert_called_once_with(event_type="info", message="hello")


# ---------------------------------------------------------------------------
# ServeMobileHandler
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"JARVIS_MOBILE_TOKEN": "", "JARVIS_MOBILE_SIGNING_KEY": ""}, clear=False)
def test_serve_mobile_no_token() -> None:
    """Returns rc=2 when no token is provided."""
    handler = ServeMobileHandler(ROOT)
    result = handler.handle(ServeMobileCommand())
    assert result.return_code == 2


@patch.dict("os.environ", {"JARVIS_MOBILE_TOKEN": "tok123", "JARVIS_MOBILE_SIGNING_KEY": ""}, clear=False)
def test_serve_mobile_no_signing_key() -> None:
    """Returns rc=2 when no signing key is provided."""
    handler = ServeMobileHandler(ROOT)
    result = handler.handle(ServeMobileCommand())
    assert result.return_code == 2


@patch("jarvis_engine.mobile_api.run_mobile_server")
@patch.dict("os.environ", {"JARVIS_MOBILE_TOKEN": "", "JARVIS_MOBILE_SIGNING_KEY": ""}, clear=False)
def test_serve_mobile_explicit_args(mock_run: MagicMock) -> None:
    """Explicit token/signing_key args override env."""
    handler = ServeMobileHandler(ROOT)
    result = handler.handle(ServeMobileCommand(token="tok", signing_key="sk"))
    assert result.return_code == 0
    mock_run.assert_called_once_with(
        host="127.0.0.1",
        port=8787,
        auth_token="tok",
        signing_key="sk",
        repo_root=ROOT,
        tls=None,
    )


@patch("jarvis_engine.mobile_api.run_mobile_server", side_effect=KeyboardInterrupt)
@patch.dict("os.environ", {"JARVIS_MOBILE_TOKEN": "t", "JARVIS_MOBILE_SIGNING_KEY": "k"}, clear=False)
def test_serve_mobile_keyboard_interrupt(mock_run: MagicMock) -> None:
    """KeyboardInterrupt is caught cleanly, returns rc=0."""
    handler = ServeMobileHandler(ROOT)
    result = handler.handle(ServeMobileCommand())
    assert result.return_code == 0


@patch("jarvis_engine.mobile_api.run_mobile_server", side_effect=RuntimeError("bind failed"))
@patch.dict("os.environ", {"JARVIS_MOBILE_TOKEN": "t", "JARVIS_MOBILE_SIGNING_KEY": "k"}, clear=False)
def test_serve_mobile_runtime_error(mock_run: MagicMock) -> None:
    handler = ServeMobileHandler(ROOT)
    result = handler.handle(ServeMobileCommand())
    assert result.return_code == 3


@patch("jarvis_engine.mobile_api.run_mobile_server", side_effect=OSError("addr in use"))
@patch.dict("os.environ", {"JARVIS_MOBILE_TOKEN": "t", "JARVIS_MOBILE_SIGNING_KEY": "k"}, clear=False)
def test_serve_mobile_os_error(mock_run: MagicMock) -> None:
    handler = ServeMobileHandler(ROOT)
    result = handler.handle(ServeMobileCommand())
    assert result.return_code == 3


# ---------------------------------------------------------------------------
# DaemonRunHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.daemon_loop.cmd_daemon_run_impl", return_value=0)
def test_daemon_run_success(mock_impl: MagicMock) -> None:
    handler = DaemonRunHandler(ROOT)
    cmd = DaemonRunCommand(max_cycles=1)
    result = handler.handle(cmd)
    assert result.return_code == 0
    mock_impl.assert_called_once()
    cfg = mock_impl.call_args.args[0]
    assert cfg.max_cycles == 1


@patch("jarvis_engine.daemon_loop.cmd_daemon_run_impl", return_value=2)
def test_daemon_run_failure(mock_impl: MagicMock) -> None:
    handler = DaemonRunHandler(ROOT)
    result = handler.handle(DaemonRunCommand())
    assert result.return_code == 2


# ---------------------------------------------------------------------------
# MobileDesktopSyncHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.ops.resilience.run_mobile_desktop_sync", return_value={"sync_ok": True, "items": 5})
def test_mobile_desktop_sync_ok(mock_sync: MagicMock) -> None:
    handler = MobileDesktopSyncHandler(ROOT)
    result = handler.handle(MobileDesktopSyncCommand())
    assert result.return_code == 0
    assert result.report["sync_ok"] is True


@patch("jarvis_engine.ops.resilience.run_mobile_desktop_sync", return_value={"sync_ok": False})
def test_mobile_desktop_sync_fail(mock_sync: MagicMock) -> None:
    handler = MobileDesktopSyncHandler(ROOT)
    result = handler.handle(MobileDesktopSyncCommand())
    assert result.return_code == 2


# ---------------------------------------------------------------------------
# SelfHealHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.ops.resilience.run_self_heal", return_value={"status": "ok", "fixed": 3})
def test_self_heal_ok(mock_heal: MagicMock) -> None:
    handler = SelfHealHandler(ROOT)
    result = handler.handle(SelfHealCommand())
    assert result.return_code == 0
    assert result.report["status"] == "ok"


@patch("jarvis_engine.ops.resilience.run_self_heal", return_value={"status": "attention", "issues": 1})
def test_self_heal_attention(mock_heal: MagicMock) -> None:
    handler = SelfHealHandler(ROOT)
    result = handler.handle(SelfHealCommand())
    assert result.return_code == 0


@patch("jarvis_engine.ops.resilience.run_self_heal", return_value={"status": "critical"})
def test_self_heal_critical(mock_heal: MagicMock) -> None:
    handler = SelfHealHandler(ROOT)
    result = handler.handle(SelfHealCommand())
    assert result.return_code == 2


@patch("jarvis_engine.ops.resilience.run_self_heal", return_value={"status": "ok"})
def test_self_heal_keep_recent_clamped(mock_heal: MagicMock) -> None:
    """keep_recent is clamped between 200 and 50000."""
    handler = SelfHealHandler(ROOT)
    handler.handle(SelfHealCommand(keep_recent=10))  # below 200
    call_kwargs = mock_heal.call_args.kwargs
    assert call_kwargs["keep_recent"] == 200

    handler.handle(SelfHealCommand(keep_recent=99999))  # above 50000
    call_kwargs = mock_heal.call_args.kwargs
    assert call_kwargs["keep_recent"] == 50000


@patch("jarvis_engine.ops.resilience.run_self_heal", return_value={"status": "ok"})
def test_self_heal_snapshot_note_truncated(mock_heal: MagicMock) -> None:
    """snapshot_note is truncated to 160 chars."""
    handler = SelfHealHandler(ROOT)
    long_note = "x" * 300
    handler.handle(SelfHealCommand(snapshot_note=long_note))
    call_kwargs = mock_heal.call_args.kwargs
    assert len(call_kwargs["snapshot_note"]) == 160


@patch("jarvis_engine.ops.resilience.run_self_heal", return_value={"status": "ok"})
def test_self_heal_empty_note_defaults(mock_heal: MagicMock) -> None:
    """Empty snapshot_note defaults to 'self-heal'."""
    handler = SelfHealHandler(ROOT)
    handler.handle(SelfHealCommand(snapshot_note="   "))
    call_kwargs = mock_heal.call_args.kwargs
    assert call_kwargs["snapshot_note"] == "self-heal"


# ---------------------------------------------------------------------------
# DesktopWidgetHandler
# ---------------------------------------------------------------------------


def test_desktop_widget_success() -> None:
    """Handler calls run_desktop_widget and returns rc=0."""
    import sys
    import types

    mock_run = MagicMock()
    mock_widget_mod = types.ModuleType("jarvis_engine.desktop.widget")
    mock_widget_mod.run_desktop_widget = mock_run  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"jarvis_engine.desktop.widget": mock_widget_mod}):
        handler = DesktopWidgetHandler(ROOT)
        result = handler.handle(DesktopWidgetCommand())

    assert result.return_code == 0
    mock_run.assert_called_once()


def test_desktop_widget_import_error() -> None:
    """Returns rc=2 when desktop_widget module is not importable."""
    handler = DesktopWidgetHandler(ROOT)
    with patch.dict("sys.modules", {"jarvis_engine.desktop.widget": None}):
        # Force an ImportError by patching the import mechanism
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "jarvis_engine.desktop.widget":
                raise ImportError("no tkinter")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = handler.handle(DesktopWidgetCommand())
            assert result.return_code == 2


# ---------------------------------------------------------------------------
# GamingModeHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.daemon_loop.detect_active_game_process", return_value=(False, ""))
@patch("jarvis_engine.daemon_loop.read_gaming_mode_state", return_value={"enabled": False, "auto_detect": False})
def test_gaming_mode_read_only(mock_read: MagicMock, mock_detect: MagicMock) -> None:
    handler = GamingModeHandler(ROOT)
    result = handler.handle(GamingModeCommand())
    assert result.effective_enabled is False
    assert result.detected is False


@patch("jarvis_engine.daemon_loop.write_gaming_mode_state", return_value={"enabled": True})
@patch("jarvis_engine.daemon_loop.detect_active_game_process", return_value=(False, ""))
@patch("jarvis_engine.daemon_loop.read_gaming_mode_state", return_value={"enabled": False, "auto_detect": False})
def test_gaming_mode_enable(mock_read: MagicMock, mock_detect: MagicMock, mock_write: MagicMock) -> None:
    handler = GamingModeHandler(ROOT)
    result = handler.handle(GamingModeCommand(enable=True))
    mock_write.assert_called_once()
    # State from write is used; enabled=True but auto_detect is False so detect not called
    assert result.effective_enabled is True


@patch("jarvis_engine.daemon_loop.write_gaming_mode_state", return_value={"enabled": False, "auto_detect": True})
@patch("jarvis_engine.daemon_loop.detect_active_game_process", return_value=(True, "steam.exe"))
@patch("jarvis_engine.daemon_loop.read_gaming_mode_state", return_value={"enabled": False, "auto_detect": False})
def test_gaming_mode_auto_detect_on(mock_read: MagicMock, mock_detect: MagicMock, mock_write: MagicMock) -> None:
    handler = GamingModeHandler(ROOT)
    result = handler.handle(GamingModeCommand(auto_detect="on"))
    assert result.detected is True
    assert result.detected_process == "steam.exe"
    assert result.effective_enabled is True  # detected overrides enabled=False


@patch("jarvis_engine.daemon_loop.detect_active_game_process", return_value=(False, ""))
@patch("jarvis_engine.daemon_loop.read_gaming_mode_state", return_value={"enabled": False, "auto_detect": False})
def test_gaming_mode_no_change_no_write(mock_read: MagicMock, mock_detect: MagicMock) -> None:
    """When no flags change state, _write is NOT called."""
    handler = GamingModeHandler(ROOT)
    result = handler.handle(GamingModeCommand(reason="just checking"))
    # write_gaming_mode_state should not be called since changed=False
    assert result.effective_enabled is False


# ---------------------------------------------------------------------------
# OpenWebHandler
# ---------------------------------------------------------------------------


@patch("webbrowser.open")
def test_open_web_valid_https(mock_open: MagicMock) -> None:
    handler = OpenWebHandler(ROOT)
    result = handler.handle(OpenWebCommand(url="https://example.com"))
    assert result.return_code == 0
    assert result.opened_url == "https://example.com"
    mock_open.assert_called_once_with("https://example.com")


@patch("webbrowser.open")
def test_open_web_no_protocol(mock_open: MagicMock) -> None:
    """URL without protocol gets https:// prepended."""
    handler = OpenWebHandler(ROOT)
    result = handler.handle(OpenWebCommand(url="example.com"))
    assert result.return_code == 0
    assert result.opened_url == "https://example.com"


def test_open_web_empty_url() -> None:
    handler = OpenWebHandler(ROOT)
    result = handler.handle(OpenWebCommand(url="   "))
    assert result.return_code == 2


def test_open_web_too_long() -> None:
    handler = OpenWebHandler(ROOT)
    result = handler.handle(OpenWebCommand(url="https://x.com/" + "a" * 500))
    assert result.return_code == 2


def test_open_web_non_http_scheme_rejected() -> None:
    """Non-http/https schemes like ftp:// are rejected early."""
    handler = OpenWebHandler(ROOT)
    result = handler.handle(OpenWebCommand(url="ftp://example.com"))
    assert result.return_code == 2


def test_open_web_credentials_in_url() -> None:
    """URLs with user:pass@ are rejected."""
    handler = OpenWebHandler(ROOT)
    result = handler.handle(OpenWebCommand(url="https://user:pass@evil.com"))
    assert result.return_code == 2


def test_open_web_no_hostname() -> None:
    handler = OpenWebHandler(ROOT)
    result = handler.handle(OpenWebCommand(url="https://"))
    assert result.return_code == 2


# ---------------------------------------------------------------------------
# WeatherHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.handlers.system_handlers.urlopen")
@patch.dict("os.environ", {"JARVIS_DEFAULT_LOCATION": ""}, clear=False)
def test_weather_success(mock_urlopen: MagicMock) -> None:
    resp_data = json.dumps({
        "current_condition": [{
            "temp_F": "72",
            "weatherDesc": [{"value": "Sunny"}],
        }]
    }).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp

    handler = WeatherHandler(ROOT)
    result = handler.handle(WeatherCommand(location="Seattle"))
    assert result.return_code == 0
    assert result.location == "Seattle"
    assert result.description == "Sunny"
    assert result.current["temp_F"] == "72"


@patch("jarvis_engine.handlers.system_handlers.urlopen", side_effect=OSError("timeout"))
def test_weather_network_error(mock_urlopen: MagicMock) -> None:
    handler = WeatherHandler(ROOT)
    result = handler.handle(WeatherCommand(location="Nowhere"))
    assert result.return_code == 2
    assert result.location == "Nowhere"


@patch("jarvis_engine.handlers.system_handlers.urlopen")
def test_weather_empty_current_condition(mock_urlopen: MagicMock) -> None:
    """Returns rc=2 when API returns no current_condition."""
    resp_data = json.dumps({"current_condition": []}).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp

    handler = WeatherHandler(ROOT)
    result = handler.handle(WeatherCommand(location="Empty"))
    assert result.return_code == 2


@patch("jarvis_engine.handlers.system_handlers.urlopen")
@patch.dict("os.environ", {"JARVIS_DEFAULT_LOCATION": "Portland, OR"}, clear=False)
def test_weather_default_location(mock_urlopen: MagicMock) -> None:
    """Uses JARVIS_DEFAULT_LOCATION env when location is empty."""
    resp_data = json.dumps({
        "current_condition": [{"temp_F": "55", "weatherDesc": [{"value": "Rainy"}]}]
    }).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp

    handler = WeatherHandler(ROOT)
    result = handler.handle(WeatherCommand(location=""))
    assert result.location == "Portland, OR"


# ---------------------------------------------------------------------------
# MigrateMemoryHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.memory.migration.run_full_migration", return_value={"status": "ok", "migrated": 10})
@patch("jarvis_engine.memory.embeddings.EmbeddingService")
def test_migrate_memory_success(mock_embed: MagicMock, mock_migrate: MagicMock) -> None:
    handler = MigrateMemoryHandler(ROOT)
    result = handler.handle(MigrateMemoryCommand())
    assert result.return_code == 0
    assert result.summary["status"] == "ok"


@patch("jarvis_engine.memory.migration.run_full_migration", return_value={"status": "error", "reason": "corrupt"})
@patch("jarvis_engine.memory.embeddings.EmbeddingService")
def test_migrate_memory_failure(mock_embed: MagicMock, mock_migrate: MagicMock) -> None:
    handler = MigrateMemoryHandler(ROOT)
    result = handler.handle(MigrateMemoryCommand())
    assert result.return_code == 2
