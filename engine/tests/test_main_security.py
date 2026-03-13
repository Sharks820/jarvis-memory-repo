"""Tests for security-related CLI commands.

Covers: voice-run owner guard (all variants), serve-mobile, phone-spam-guard,
phone-action, connect-status, connect-grant, connect-bootstrap,
voice-run execute/auth requirements.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from jarvis_engine import main as main_mod
from jarvis_engine.voice import pipeline as voice_pipeline_mod
from jarvis_engine import daemon_loop as daemon_loop_mod
from jarvis_engine import _bus as bus_mod


# ===========================================================================
# Voice-run auth requirements
# ===========================================================================


def test_cmd_voice_run_execute_requires_voice_auth() -> None:
    rc = main_mod.cmd_voice_run(
        text="Jarvis, sync my calendar and inbox",
        execute=True,
        approve_privileged=False,
        speak=False,
        snapshot_path=Path("ops_snapshot.live.json"),
        actions_path=Path("actions.generated.json"),
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 2


def test_cmd_voice_run_state_mutation_requires_voice_auth() -> None:
    rc = main_mod.cmd_voice_run(
        text="Jarvis, pause daemon",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=Path("ops_snapshot.live.json"),
        actions_path=Path("actions.generated.json"),
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 2


# ===========================================================================
# Owner guard tests
# ===========================================================================


def test_cmd_voice_run_owner_guard_blocks_non_owner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="Jarvis, runtime status",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="other_user",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 2


def test_cmd_voice_run_owner_guard_allows_read_only_without_voice_auth(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="Jarvis, runtime status",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 0


def test_cmd_voice_run_owner_guard_requires_voice_auth_for_mutation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="Jarvis, pause daemon",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
    )
    assert rc == 2


def test_cmd_voice_run_skip_voice_auth_guard_allows_owner_mutation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="Jarvis, pause daemon",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
        skip_voice_auth_guard=True,
    )
    assert rc == 0


def test_cmd_voice_run_skip_voice_auth_guard_still_blocks_non_owner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="Jarvis, runtime status",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="other_user",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="",
        skip_voice_auth_guard=True,
    )
    assert rc == 2


def test_cmd_voice_run_owner_guard_allows_with_master_password(tmp_path: Path, monkeypatch) -> None:
    """Master password should bypass owner guard for any command."""
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    rc_owner = main_mod.cmd_owner_guard(
        enable=True,
        disable=False,
        owner_user="conner",
        trust_device="",
        revoke_device="",
        set_master_password_value="TestPass123!",
        clear_master_password_value=False,
    )
    assert rc_owner == 0

    rc = main_mod.cmd_voice_run(
        text="pause daemon",
        execute=False,
        approve_privileged=False,
        speak=False,
        snapshot_path=tmp_path / "ops_snapshot.live.json",
        actions_path=tmp_path / "actions.generated.json",
        voice_user="conner",
        voice_auth_wav="",
        voice_threshold=0.82,
        master_password="TestPass123!",
    )
    assert rc != 2, "Master password should bypass owner guard"


# ===========================================================================
# Phone spam guard
# ===========================================================================


def test_cmd_phone_spam_guard_can_run_without_queue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(daemon_loop_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(voice_pipeline_mod, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(bus_mod, "repo_root", lambda: tmp_path)
    call_log_path = tmp_path / "calls.json"
    report_path = tmp_path / "report.json"
    queue_path = tmp_path / "queue.jsonl"
    call_log_path.write_text(
        json.dumps(
            [
                {"number": "+14155551234", "type": "missed", "duration_sec": 0, "contact_name": "", "ts_utc": "2026-02-22T00:00:00+00:00"},
                {"number": "+14155551234", "type": "missed", "duration_sec": 0, "contact_name": "", "ts_utc": "2026-02-22T00:01:00+00:00"},
                {"number": "+14155551234", "type": "missed", "duration_sec": 0, "contact_name": "", "ts_utc": "2026-02-22T00:02:00+00:00"},
                {"number": "+14155551234", "type": "missed", "duration_sec": 0, "contact_name": "", "ts_utc": "2026-02-22T00:03:00+00:00"},
            ]
        ),
        encoding="utf-8",
    )
    rc = main_mod.cmd_phone_spam_guard(
        call_log_path=call_log_path,
        report_path=report_path,
        queue_path=queue_path,
        threshold=0.65,
        queue_actions=False,
    )
    assert rc == 0
    assert report_path.exists()
    assert queue_path.exists() is False


# ===========================================================================
# Serve-mobile edge cases
# ===========================================================================


def test_cmd_serve_mobile_requires_token_and_signing_key(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
    rc = main_mod.cmd_serve_mobile(host="127.0.0.1", port=8787, token=None, signing_key=None)
    assert rc == 2


def test_cmd_serve_mobile_uses_env_values(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_mobile_server(host: str, port: int, auth_token: str, signing_key: str, repo_root, **kwargs) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["auth_token"] = auth_token
        captured["signing_key"] = signing_key
        captured["repo_root"] = repo_root

    monkeypatch.setenv("JARVIS_MOBILE_TOKEN", "env-auth")
    monkeypatch.setenv("JARVIS_MOBILE_SIGNING_KEY", "env-sign")
    monkeypatch.setattr(main_mod, "run_mobile_server", fake_run_mobile_server)
    # Bypass PID-based duplicate detection when mobile API is already running
    import jarvis_engine.ops.process_manager as pm_mod
    monkeypatch.setattr(pm_mod, "is_service_running", lambda *a, **kw: False)
    monkeypatch.setattr(pm_mod, "write_pid_file", lambda *a, **kw: None)
    monkeypatch.setattr(pm_mod, "remove_pid_file", lambda *a, **kw: None)

    rc = main_mod.cmd_serve_mobile(host="127.0.0.1", port=9001, token=None, signing_key=None)
    assert rc == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9001
    assert captured["auth_token"] == "env-auth"
    assert captured["signing_key"] == "env-sign"


class TestServeMobileEdgeCases:
    """Tests for cmd_serve_mobile edge cases."""

    def test_config_file_not_found(self, capsys, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1", port=8787, token=None, signing_key=None,
            config_file="/nonexistent/config.json",
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "config file not found" in out

    def test_config_file_invalid_json(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        bad_cfg = tmp_path / "bad.json"
        bad_cfg.write_text("not json", encoding="utf-8")
        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1", port=8787, token=None, signing_key=None,
            config_file=str(bad_cfg),
        )
        assert rc == 2

    def test_missing_token_only(self, capsys, monkeypatch):
        monkeypatch.delenv("JARVIS_MOBILE_TOKEN", raising=False)
        monkeypatch.setenv("JARVIS_MOBILE_SIGNING_KEY", "key123")
        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1", port=8787, token=None, signing_key=None,
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "missing mobile token" in out

    def test_missing_signing_key_only(self, capsys, monkeypatch):
        monkeypatch.setenv("JARVIS_MOBILE_TOKEN", "tok123")
        monkeypatch.delenv("JARVIS_MOBILE_SIGNING_KEY", raising=False)
        rc = main_mod.cmd_serve_mobile(
            host="127.0.0.1", port=8787, token=None, signing_key=None,
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "missing signing key" in out


# ===========================================================================
# Connector commands
# ===========================================================================


class TestConnectStatus:
    """Tests for cmd_connect_status."""

    def test_connect_status_all_ready(self, capsys, mock_bus):
        from jarvis_engine.commands.security_commands import ConnectStatusResult
        cs = MagicMock()
        cs.connector_id = "email"
        cs.ready = True
        cs.permission_granted = True
        cs.configured = True
        cs.message = "ready"
        result = ConnectStatusResult(statuses=[cs], prompts=[], ready=1, pending=0)
        bus = mock_bus(result)
        rc = main_mod.cmd_connect_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "ready=1" in out
        assert "id=email" in out

    def test_connect_status_with_prompts(self, capsys, mock_bus):
        from jarvis_engine.commands.security_commands import ConnectStatusResult
        result = ConnectStatusResult(
            statuses=[], prompts=[{"connector_id": "cal", "option_voice": "setup cal", "option_tap_url": "http://cal"}],
            ready=0, pending=1,
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_connect_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "connector_prompts_begin" in out


class TestConnectGrant:
    """Tests for cmd_connect_grant."""

    def test_grant_success(self, capsys, mock_bus):
        from jarvis_engine.commands.security_commands import ConnectGrantResult
        result = ConnectGrantResult(
            granted={"scopes": ["read", "write"], "granted_utc": "2026-01-01"},
            return_code=0,
        )
        bus = mock_bus(result)
        rc = main_mod.cmd_connect_grant(connector_id="email", scopes=["read", "write"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "granted=true" in out
        assert "read,write" in out

    def test_grant_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.security_commands import ConnectGrantResult
        result = ConnectGrantResult(return_code=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_connect_grant(connector_id="bad", scopes=[])
        assert rc == 2


class TestConnectBootstrap:
    """Tests for cmd_connect_bootstrap."""

    def test_bootstrap_ready(self, capsys, mock_bus):
        from jarvis_engine.commands.security_commands import ConnectBootstrapResult
        result = ConnectBootstrapResult(prompts=[], ready=True)
        bus = mock_bus(result)
        rc = main_mod.cmd_connect_bootstrap(auto_open=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "connectors_ready=true" in out

    def test_bootstrap_not_ready(self, capsys, mock_bus):
        from jarvis_engine.commands.security_commands import ConnectBootstrapResult
        prompts = [{"connector_id": "email", "option_voice": "Setup email", "option_tap_url": "http://setup"}]
        result = ConnectBootstrapResult(prompts=prompts, ready=False)
        bus = mock_bus(result)
        rc = main_mod.cmd_connect_bootstrap(auto_open=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "connectors_ready=false" in out
        assert "connector_prompt" in out


# ===========================================================================
# Phone action commands
# ===========================================================================


class TestPhoneAction:
    """Tests for cmd_phone_action."""

    def test_phone_action_success(self, capsys, mock_bus):
        from jarvis_engine.commands.security_commands import PhoneActionResult
        record = MagicMock()
        record.action = "send_sms"
        record.number = "+1234567890"
        record.message = "Hello"
        result = PhoneActionResult(record=record, return_code=0)
        bus = mock_bus(result)
        rc = main_mod.cmd_phone_action(
            action="send_sms", number="+1234567890", message="Hello",
            queue_path=Path("/tmp/queue.jsonl"), queue_action=True,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "action=send_sms" in out

    def test_phone_action_failure(self, capsys, mock_bus):
        from jarvis_engine.commands.security_commands import PhoneActionResult
        result = PhoneActionResult(return_code=2)
        bus = mock_bus(result)
        rc = main_mod.cmd_phone_action(
            action="block_number", number="", message="",
            queue_path=Path("/tmp/queue.jsonl"), queue_action=False,
        )
        assert rc == 2
