"""Tests for cli_system.py — System CLI command handlers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_bus(dispatch_return):
    """Create a mock bus whose dispatch() returns *dispatch_return*."""
    bus = MagicMock()
    bus.dispatch.return_value = dispatch_return
    return bus


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


class TestCmdStatus:

    def test_status_returns_zero(self, capsys):
        result = SimpleNamespace(
            profile="default",
            primary_runtime="ollama",
            secondary_runtime="groq",
            security_strictness="standard",
            operation_mode="normal",
            cloud_burst_enabled=False,
            events=[],
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_status
            rc = cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Jarvis Engine Status" in out
        assert "profile=default" in out

    def test_status_prints_events(self, capsys):
        event = SimpleNamespace(ts="2026-01-01T00:00:00Z", event_type="info", message="hello")
        result = SimpleNamespace(
            profile="default",
            primary_runtime="ollama",
            secondary_runtime="groq",
            security_strictness="standard",
            operation_mode="normal",
            cloud_burst_enabled=False,
            events=[event],
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_status
            rc = cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "hello" in out
        assert "- none" not in out


# ---------------------------------------------------------------------------
# cmd_log
# ---------------------------------------------------------------------------


class TestCmdLog:

    def test_log_returns_zero(self, capsys):
        result = SimpleNamespace(ts="2026-01-01T00:00:00Z", event_type="info", message="test msg")
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_log
            rc = cmd_log("info", "test msg")
        assert rc == 0
        out = capsys.readouterr().out
        assert "logged:" in out
        assert "test msg" in out


# ---------------------------------------------------------------------------
# cmd_ingest
# ---------------------------------------------------------------------------


class TestCmdIngest:

    def test_ingest_returns_zero(self, capsys):
        result = SimpleNamespace(record_id="r1", source="cli", kind="episodic", task_id="t1")
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_ingest
            rc = cmd_ingest("cli", "episodic", "t1", "some content")
        assert rc == 0
        out = capsys.readouterr().out
        assert "ingested:" in out
        assert "id=r1" in out


# ---------------------------------------------------------------------------
# cmd_gaming_mode
# ---------------------------------------------------------------------------


class TestCmdGamingMode:

    def test_gaming_mode_enable(self, capsys):
        result = SimpleNamespace(
            state={"enabled": True, "auto_detect": False, "updated_utc": "2026-01-01T00:00:00Z", "reason": "manual"},
            detected=False,
            detected_process=None,
            effective_enabled=True,
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_gaming_mode
            rc = cmd_gaming_mode(True, "manual", "")
        assert rc == 0
        out = capsys.readouterr().out
        assert "enabled=True" in out
        assert "effective_enabled=True" in out

    def test_gaming_mode_detected_process(self, capsys):
        result = SimpleNamespace(
            state={"enabled": False, "auto_detect": True, "updated_utc": "now"},
            detected=True,
            detected_process="steam.exe",
            effective_enabled=True,
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_gaming_mode
            rc = cmd_gaming_mode(None, "", "enable")
        assert rc == 0
        out = capsys.readouterr().out
        assert "detected_process=steam.exe" in out


# ---------------------------------------------------------------------------
# cmd_runtime_control
# ---------------------------------------------------------------------------


class TestCmdRuntimeControl:

    def test_runtime_control_pause(self, capsys):
        result = SimpleNamespace(state={"daemon_paused": True, "safe_mode": False, "updated_utc": "now", "reason": "manual"})
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_runtime_control
            rc = cmd_runtime_control(pause=True, resume=False, safe_on=False, safe_off=False, reset=False, reason="manual")
        assert rc == 0
        out = capsys.readouterr().out
        assert "daemon_paused=True" in out
        assert "reason=manual" in out


# ---------------------------------------------------------------------------
# cmd_persona_config
# ---------------------------------------------------------------------------


class TestCmdPersonaConfig:

    def test_persona_config_enable(self, capsys):
        cfg = SimpleNamespace(enabled=True, mode="default", style="concise", humor_level=3, updated_utc="now")
        result = SimpleNamespace(config=cfg)
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_persona_config
            rc = cmd_persona_config(enable=True, disable=False, humor_level=3, mode="default", style="concise")
        assert rc == 0
        out = capsys.readouterr().out
        assert "persona_config" in out
        assert "humor_level=3" in out

    def test_persona_config_error(self, capsys):
        result = SimpleNamespace(config={"error": "conflicting flags"})
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_persona_config
            rc = cmd_persona_config(enable=True, disable=True, humor_level=None, mode="", style="")
        assert rc == 1
        out = capsys.readouterr().out
        assert "error=conflicting flags" in out


# ---------------------------------------------------------------------------
# cmd_memory_snapshot
# ---------------------------------------------------------------------------


class TestCmdMemorySnapshot:

    def test_snapshot_create(self, capsys):
        result = SimpleNamespace(
            created=True, verified=False,
            snapshot_path="/tmp/snap.zip", metadata_path="/tmp/meta.json",
            signature_path="/tmp/sig.json", sha256="abc123", file_count=5,
            ok=False, reason="", expected_sha256="", actual_sha256="",
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_memory_snapshot
            rc = cmd_memory_snapshot(True, None, "test")
        assert rc == 0
        out = capsys.readouterr().out
        assert "memory_snapshot_created=true" in out
        assert "sha256=abc123" in out

    def test_snapshot_verify_ok(self, capsys):
        result = SimpleNamespace(
            created=False, verified=True,
            ok=True, reason="match", expected_sha256="aaa", actual_sha256="aaa",
            snapshot_path="", metadata_path="", signature_path="", sha256="", file_count=0,
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_memory_snapshot
            rc = cmd_memory_snapshot(False, "/tmp/snap.zip", "")
        assert rc == 0

    def test_snapshot_neither(self, capsys):
        result = SimpleNamespace(
            created=False, verified=False,
            ok=False, reason="", expected_sha256="", actual_sha256="",
            snapshot_path="", metadata_path="", signature_path="", sha256="", file_count=0,
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_memory_snapshot
            rc = cmd_memory_snapshot(False, None, "")
        assert rc == 2


# ---------------------------------------------------------------------------
# cmd_memory_maintenance
# ---------------------------------------------------------------------------


class TestCmdMemoryMaintenance:

    def test_memory_maintenance(self, capsys):
        report = {
            "status": "ok",
            "report_path": "/tmp/report.json",
            "compact": {"compacted": True, "total_records": 100, "kept_records": 80},
            "regression": {"status": "pass", "duplicate_ratio": 0.02, "unresolved_conflicts": 0},
            "snapshot": {"path": "/tmp/snap.zip"},
        }
        result = SimpleNamespace(report=report)
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_memory_maintenance
            rc = cmd_memory_maintenance(1800, "test note")
        assert rc == 0
        out = capsys.readouterr().out
        assert "status=ok" in out
        assert "compacted=True" in out


# ---------------------------------------------------------------------------
# cmd_migrate_memory
# ---------------------------------------------------------------------------


class TestCmdMigrateMemory:

    def test_migrate_success(self, capsys):
        result = SimpleNamespace(
            return_code=0,
            summary={"totals": {"inserted": 50, "skipped": 2, "errors": 0}, "db_path": "/tmp/mem.db"},
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_migrate_memory
            rc = cmd_migrate_memory()
        assert rc == 0
        out = capsys.readouterr().out
        assert "total_inserted=50" in out

    def test_migrate_failure(self, capsys):
        result = SimpleNamespace(return_code=1, summary={})
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_migrate_memory
            rc = cmd_migrate_memory()
        assert rc == 1


# ---------------------------------------------------------------------------
# cmd_weather
# ---------------------------------------------------------------------------


class TestCmdWeather:

    def test_weather_success(self, capsys):
        result = SimpleNamespace(
            return_code=0,
            location="Denver",
            current={"temp_F": "72", "temp_C": "22", "FeelsLikeF": "70", "humidity": "30"},
            description="Clear sky",
        )
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_weather
            rc = cmd_weather("Denver")
        assert rc == 0
        out = capsys.readouterr().out
        assert "temperature_f=72" in out
        assert "conditions=Clear sky" in out

    def test_weather_failure(self, capsys):
        result = SimpleNamespace(return_code=1, location="", current={}, description="")
        with patch("jarvis_engine.cli_system._get_bus", return_value=_mock_bus(result)):
            from jarvis_engine.cli_system import cmd_weather
            rc = cmd_weather("InvalidPlace")
        assert rc == 1


# ---------------------------------------------------------------------------
# cmd_serve_mobile
# ---------------------------------------------------------------------------


class TestCmdServeMobile:

    def test_missing_token(self, capsys, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        from jarvis_engine.cli_system import cmd_serve_mobile
        rc = cmd_serve_mobile("127.0.0.1", 8787, None, None)
        assert rc == 2
        assert "missing mobile token" in capsys.readouterr().out

    def test_missing_signing_key(self, capsys, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        from jarvis_engine.cli_system import cmd_serve_mobile
        rc = cmd_serve_mobile("127.0.0.1", 8787, "tok123", None)
        assert rc == 2
        assert "missing signing key" in capsys.readouterr().out

    def test_config_file_not_found(self, capsys, tmp_path, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        from jarvis_engine.cli_system import cmd_serve_mobile
        rc = cmd_serve_mobile("127.0.0.1", 8787, None, None, config_file=str(tmp_path / "nonexistent.json"))
        assert rc == 2
        assert "config file not found" in capsys.readouterr().out
