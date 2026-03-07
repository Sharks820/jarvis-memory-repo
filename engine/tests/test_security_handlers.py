"""Tests for security_handlers -- RuntimeControl, OwnerGuard, Connect*, Phone*, Persona."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from jarvis_engine.commands.security_commands import (
    ConnectBootstrapCommand,
    ConnectGrantCommand,
    ConnectStatusCommand,
    OwnerGuardCommand,
    PersonaConfigCommand,
    PhoneActionCommand,
    PhoneSpamGuardCommand,
    RuntimeControlCommand,
)
from jarvis_engine.handlers.security_handlers import (
    ConnectBootstrapHandler,
    ConnectGrantHandler,
    ConnectStatusHandler,
    OwnerGuardHandler,
    PersonaConfigHandler,
    PhoneActionHandler,
    PhoneSpamGuardHandler,
    RuntimeControlHandler,
)

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# RuntimeControlHandler
# ---------------------------------------------------------------------------


@patch(
    "jarvis_engine.runtime_control.read_control_state",
    return_value={"daemon_paused": False},
)
def test_runtime_control_read_only(mock_read: MagicMock) -> None:
    """No flags set => read current state."""
    handler = RuntimeControlHandler(ROOT)
    result = handler.handle(RuntimeControlCommand())
    assert result.state == {"daemon_paused": False}
    mock_read.assert_called_once_with(ROOT)


@patch(
    "jarvis_engine.runtime_control.reset_control_state", return_value={"reset": True}
)
def test_runtime_control_reset(mock_reset: MagicMock) -> None:
    handler = RuntimeControlHandler(ROOT)
    result = handler.handle(RuntimeControlCommand(reset=True))
    assert result.state == {"reset": True}
    mock_reset.assert_called_once_with(ROOT)


@patch(
    "jarvis_engine.runtime_control.write_control_state",
    return_value={"daemon_paused": True},
)
def test_runtime_control_pause(mock_write: MagicMock) -> None:
    handler = RuntimeControlHandler(ROOT)
    result = handler.handle(RuntimeControlCommand(pause=True, reason="maintenance"))
    assert result.state == {"daemon_paused": True}
    mock_write.assert_called_once_with(
        ROOT, daemon_paused=True, safe_mode=None, reason="maintenance"
    )


@patch(
    "jarvis_engine.runtime_control.write_control_state",
    return_value={"daemon_paused": False},
)
def test_runtime_control_resume(mock_write: MagicMock) -> None:
    handler = RuntimeControlHandler(ROOT)
    result = handler.handle(RuntimeControlCommand(resume=True))
    mock_write.assert_called_once_with(
        ROOT, daemon_paused=False, safe_mode=None, reason=""
    )


@patch(
    "jarvis_engine.runtime_control.write_control_state",
    return_value={"safe_mode": True},
)
def test_runtime_control_safe_on(mock_write: MagicMock) -> None:
    handler = RuntimeControlHandler(ROOT)
    result = handler.handle(RuntimeControlCommand(safe_on=True))
    mock_write.assert_called_once_with(
        ROOT, daemon_paused=None, safe_mode=True, reason=""
    )


@patch(
    "jarvis_engine.runtime_control.write_control_state",
    return_value={"safe_mode": False},
)
def test_runtime_control_safe_off(mock_write: MagicMock) -> None:
    handler = RuntimeControlHandler(ROOT)
    result = handler.handle(RuntimeControlCommand(safe_off=True))
    mock_write.assert_called_once_with(
        ROOT, daemon_paused=None, safe_mode=False, reason=""
    )


def test_runtime_control_conflicting_pause_resume() -> None:
    """Conflicting pause+resume returns error without calling any module."""
    handler = RuntimeControlHandler(ROOT)
    result = handler.handle(RuntimeControlCommand(pause=True, resume=True))
    assert "error" in result.state.get(
        "error", ""
    ).lower() or "Cannot" in result.state.get("error", "")


def test_runtime_control_conflicting_safe() -> None:
    """Conflicting safe_on+safe_off returns error."""
    handler = RuntimeControlHandler(ROOT)
    result = handler.handle(RuntimeControlCommand(safe_on=True, safe_off=True))
    assert "Cannot" in result.state.get("error", "")


# ---------------------------------------------------------------------------
# OwnerGuardHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.owner_guard.read_owner_guard", return_value={"enabled": True})
def test_owner_guard_read(mock_read: MagicMock) -> None:
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand())
    assert result.state == {"enabled": True}
    assert result.return_code == 0


@patch("jarvis_engine.owner_guard.set_master_password", return_value={"pw_set": True})
def test_owner_guard_set_master_password(mock_set: MagicMock) -> None:
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(set_master_password_value="hunter2"))
    assert result.return_code == 0
    mock_set.assert_called_once_with(ROOT, "hunter2")


@patch(
    "jarvis_engine.owner_guard.clear_master_password", return_value={"pw_set": False}
)
def test_owner_guard_clear_master_password(mock_clear: MagicMock) -> None:
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(clear_master_password_value=True))
    assert result.return_code == 0
    mock_clear.assert_called_once_with(ROOT)


@patch(
    "jarvis_engine.owner_guard.trust_mobile_device", return_value={"devices": ["phone"]}
)
def test_owner_guard_trust_device(mock_trust: MagicMock) -> None:
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(trust_device="phone"))
    assert result.return_code == 0
    mock_trust.assert_called_once_with(ROOT, "phone")


@patch("jarvis_engine.owner_guard.revoke_mobile_device", return_value={"devices": []})
def test_owner_guard_revoke_device(mock_revoke: MagicMock) -> None:
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(revoke_device="phone"))
    assert result.return_code == 0
    mock_revoke.assert_called_once_with(ROOT, "phone")


@patch("jarvis_engine.owner_guard.write_owner_guard", return_value={"enabled": True})
def test_owner_guard_enable(mock_write: MagicMock) -> None:
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(enable=True, owner_user="conner"))
    assert result.return_code == 0
    mock_write.assert_called_once_with(ROOT, enabled=True, owner_user_id="conner")


def test_owner_guard_enable_no_user() -> None:
    """Enable without owner_user returns rc=2."""
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(enable=True, owner_user=""))
    assert result.return_code == 2


@patch("jarvis_engine.owner_guard.write_owner_guard", return_value={"enabled": False})
def test_owner_guard_disable(mock_write: MagicMock) -> None:
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(disable=True))
    assert result.return_code == 0
    mock_write.assert_called_once_with(ROOT, enabled=False)


@patch("jarvis_engine.owner_guard.set_master_password", side_effect=ValueError("weak"))
def test_owner_guard_value_error(mock_set: MagicMock) -> None:
    """ValueError from any guard function returns rc=2."""
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(set_master_password_value="x"))
    assert result.return_code == 2


@patch("jarvis_engine.owner_guard.write_owner_guard", return_value={"owner": "bob"})
def test_owner_guard_set_owner_user_only(mock_write: MagicMock) -> None:
    """Setting owner_user alone (no enable/disable) updates the config."""
    handler = OwnerGuardHandler(ROOT)
    result = handler.handle(OwnerGuardCommand(owner_user="bob"))
    assert result.return_code == 0
    mock_write.assert_called_once_with(ROOT, owner_user_id="bob")


# ---------------------------------------------------------------------------
# ConnectStatusHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.connectors.build_connector_prompts", return_value=[])
@patch("jarvis_engine.connectors.evaluate_connector_statuses")
def test_connect_status(mock_eval: MagicMock, mock_prompts: MagicMock) -> None:
    s1 = SimpleNamespace(ready=True)
    s2 = SimpleNamespace(ready=False)
    mock_eval.return_value = [s1, s2]
    handler = ConnectStatusHandler(ROOT)
    result = handler.handle(ConnectStatusCommand())
    assert result.ready == 1
    assert result.pending == 1


# ---------------------------------------------------------------------------
# ConnectGrantHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.connectors.grant_connector_permission", return_value={"ok": True})
def test_connect_grant_success(mock_grant: MagicMock) -> None:
    handler = ConnectGrantHandler(ROOT)
    result = handler.handle(
        ConnectGrantCommand(connector_id="spotify", scopes=["read"])
    )
    assert result.return_code == 0
    assert result.granted == {"ok": True}


@patch(
    "jarvis_engine.connectors.grant_connector_permission",
    side_effect=ValueError("bad id"),
)
def test_connect_grant_value_error(mock_grant: MagicMock) -> None:
    handler = ConnectGrantHandler(ROOT)
    result = handler.handle(ConnectGrantCommand(connector_id="nope"))
    assert result.return_code == 2


# ---------------------------------------------------------------------------
# ConnectBootstrapHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.connectors.build_connector_prompts", return_value=[])
@patch("jarvis_engine.connectors.evaluate_connector_statuses", return_value=[])
def test_connect_bootstrap_all_ready(
    mock_eval: MagicMock, mock_prompts: MagicMock
) -> None:
    handler = ConnectBootstrapHandler(ROOT)
    result = handler.handle(ConnectBootstrapCommand())
    assert result.ready is True
    assert result.prompts == []


@patch("jarvis_engine.connectors.build_connector_prompts")
@patch("jarvis_engine.connectors.evaluate_connector_statuses", return_value=[])
def test_connect_bootstrap_with_prompts_no_auto(
    mock_eval: MagicMock, mock_prompts: MagicMock
) -> None:
    mock_prompts.return_value = [{"option_tap_url": "https://example.com/auth"}]
    handler = ConnectBootstrapHandler(ROOT)
    result = handler.handle(ConnectBootstrapCommand(auto_open=False))
    assert result.ready is False
    assert len(result.prompts) == 1


@patch("webbrowser.open")
@patch("jarvis_engine.connectors.build_connector_prompts")
@patch("jarvis_engine.connectors.evaluate_connector_statuses", return_value=[])
def test_connect_bootstrap_auto_open(
    mock_eval: MagicMock, mock_prompts: MagicMock, mock_browser: MagicMock
) -> None:
    mock_prompts.return_value = [
        {"option_tap_url": "https://example.com/auth"},
        {"option_tap_url": "ftp://bad"},  # non-http should be ignored
        {"option_tap_url": ""},  # empty should be ignored
    ]
    handler = ConnectBootstrapHandler(ROOT)
    result = handler.handle(ConnectBootstrapCommand(auto_open=True))
    assert result.ready is False
    # Only the https URL should be opened
    mock_browser.assert_called_once_with("https://example.com/auth")


# ---------------------------------------------------------------------------
# PhoneActionHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine.phone_guard.append_phone_actions")
@patch(
    "jarvis_engine.phone_guard.build_phone_action",
    return_value={"action": "block", "number": "555"},
)
def test_phone_action_queue(mock_build: MagicMock, mock_append: MagicMock) -> None:
    queue_path = ROOT / "phone_actions.jsonl"
    handler = PhoneActionHandler(ROOT)
    cmd = PhoneActionCommand(
        action="block", number="555", queue_path=queue_path, queue_action=True
    )
    result = handler.handle(cmd)
    assert result.return_code == 0
    assert result.record == {"action": "block", "number": "555"}
    mock_append.assert_called_once()


@patch("jarvis_engine.phone_guard.build_phone_action", return_value={"action": "sms"})
def test_phone_action_no_queue(mock_build: MagicMock) -> None:
    handler = PhoneActionHandler(ROOT)
    cmd = PhoneActionCommand(action="sms", queue_action=False)
    result = handler.handle(cmd)
    assert result.return_code == 0


@patch(
    "jarvis_engine.phone_guard.build_phone_action", side_effect=ValueError("bad action")
)
def test_phone_action_build_error(mock_build: MagicMock) -> None:
    handler = PhoneActionHandler(ROOT)
    cmd = PhoneActionCommand(action="invalid")
    result = handler.handle(cmd)
    assert result.return_code == 2


@patch("jarvis_engine.phone_guard.build_phone_action", return_value={"action": "block"})
@patch(
    "jarvis_engine._shared.check_path_within_root",
    side_effect=ValueError("outside root"),
)
def test_phone_action_path_escape(mock_check: MagicMock, mock_build: MagicMock) -> None:
    """Queue path outside root is rejected."""
    handler = PhoneActionHandler(ROOT)
    cmd = PhoneActionCommand(
        action="block", queue_path=Path("/etc/passwd"), queue_action=True
    )
    result = handler.handle(cmd)
    assert result.return_code == 2


# ---------------------------------------------------------------------------
# PhoneSpamGuardHandler
# ---------------------------------------------------------------------------


@patch("jarvis_engine._shared.check_path_within_root", side_effect=ValueError("escape"))
def test_spam_guard_path_escape(mock_check: MagicMock) -> None:
    handler = PhoneSpamGuardHandler(ROOT)
    cmd = PhoneSpamGuardCommand(
        call_log_path=Path("/tmp/log"),
        report_path=Path("/tmp/report"),
        queue_path=Path("/tmp/queue"),
    )
    result = handler.handle(cmd)
    assert result.return_code == 2


@patch("jarvis_engine._shared.check_path_within_root")
def test_spam_guard_missing_call_log(mock_check: MagicMock, tmp_path: Path) -> None:
    """Returns rc=2 when call_log_path does not exist."""
    handler = PhoneSpamGuardHandler(tmp_path)
    cmd = PhoneSpamGuardCommand(
        call_log_path=tmp_path / "nonexistent.json",
        report_path=tmp_path / "report.json",
        queue_path=tmp_path / "queue.jsonl",
    )
    result = handler.handle(cmd)
    assert result.return_code == 2


@patch("jarvis_engine.phone_guard.append_phone_actions")
@patch("jarvis_engine.phone_guard.write_spam_report")
@patch(
    "jarvis_engine.phone_guard.build_spam_block_actions",
    return_value=[{"action": "block"}],
)
@patch(
    "jarvis_engine.phone_guard.detect_spam_candidates", return_value=[{"number": "555"}]
)
@patch("jarvis_engine.phone_guard.load_call_log", return_value=[{"num": "555"}])
@patch("jarvis_engine._shared.check_path_within_root")
def test_spam_guard_success_queue(
    mock_check: MagicMock,
    mock_load: MagicMock,
    mock_detect: MagicMock,
    mock_build: MagicMock,
    mock_write: MagicMock,
    mock_append: MagicMock,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "call_log.json"
    log_file.write_text("[]")
    handler = PhoneSpamGuardHandler(tmp_path)
    cmd = PhoneSpamGuardCommand(
        call_log_path=log_file,
        report_path=tmp_path / "report.json",
        queue_path=tmp_path / "queue.jsonl",
        threshold=0.7,
        queue_actions=True,
    )
    result = handler.handle(cmd)
    assert result.return_code == 0
    assert result.candidates_count == 1
    assert result.queued_actions_count == 1
    mock_append.assert_called_once()


@patch("jarvis_engine.phone_guard.write_spam_report")
@patch(
    "jarvis_engine.phone_guard.build_spam_block_actions",
    return_value=[{"action": "block"}],
)
@patch("jarvis_engine.phone_guard.detect_spam_candidates", return_value=[{"n": "x"}])
@patch("jarvis_engine.phone_guard.load_call_log", return_value=[])
@patch("jarvis_engine._shared.check_path_within_root")
def test_spam_guard_no_queue(
    mock_check: MagicMock,
    mock_load: MagicMock,
    mock_detect: MagicMock,
    mock_build: MagicMock,
    mock_write: MagicMock,
    tmp_path: Path,
) -> None:
    """queue_actions=False means actions are not appended."""
    log_file = tmp_path / "call_log.json"
    log_file.write_text("[]")
    handler = PhoneSpamGuardHandler(tmp_path)
    cmd = PhoneSpamGuardCommand(
        call_log_path=log_file,
        report_path=tmp_path / "report.json",
        queue_path=tmp_path / "queue.jsonl",
        queue_actions=False,
    )
    result = handler.handle(cmd)
    assert result.return_code == 0
    assert result.queued_actions_count == 0


@patch(
    "jarvis_engine.phone_guard.load_call_log",
    side_effect=json.JSONDecodeError("err", "", 0),
)
@patch("jarvis_engine._shared.check_path_within_root")
def test_spam_guard_bad_json(
    mock_check: MagicMock, mock_load: MagicMock, tmp_path: Path
) -> None:
    log_file = tmp_path / "call_log.json"
    log_file.write_text("{bad")
    handler = PhoneSpamGuardHandler(tmp_path)
    cmd = PhoneSpamGuardCommand(
        call_log_path=log_file,
        report_path=tmp_path / "r.json",
        queue_path=tmp_path / "q.jsonl",
    )
    result = handler.handle(cmd)
    assert result.return_code == 2


# ---------------------------------------------------------------------------
# PersonaConfigHandler
# ---------------------------------------------------------------------------


@patch(
    "jarvis_engine.persona.load_persona_config",
    return_value={"enabled": True, "humor_level": 5},
)
def test_persona_config_read(mock_load: MagicMock) -> None:
    handler = PersonaConfigHandler(ROOT)
    result = handler.handle(PersonaConfigCommand())
    assert result.config == {"enabled": True, "humor_level": 5}
    mock_load.assert_called_once_with(ROOT)


@patch("jarvis_engine.persona.save_persona_config", return_value={"enabled": True})
def test_persona_config_enable(mock_save: MagicMock) -> None:
    handler = PersonaConfigHandler(ROOT)
    result = handler.handle(PersonaConfigCommand(enable=True))
    assert result.config == {"enabled": True}
    mock_save.assert_called_once_with(
        ROOT, enabled=True, humor_level=None, mode=None, style=None
    )


@patch("jarvis_engine.persona.save_persona_config", return_value={"enabled": False})
def test_persona_config_disable(mock_save: MagicMock) -> None:
    handler = PersonaConfigHandler(ROOT)
    result = handler.handle(PersonaConfigCommand(disable=True))
    # When both enable=False disable=True, enabled_opt should be False
    mock_save.assert_called_once_with(
        ROOT, enabled=False, humor_level=None, mode=None, style=None
    )


@patch("jarvis_engine.persona.save_persona_config", return_value={"humor_level": 8})
def test_persona_config_set_humor(mock_save: MagicMock) -> None:
    handler = PersonaConfigHandler(ROOT)
    result = handler.handle(PersonaConfigCommand(humor_level=8))
    mock_save.assert_called_once_with(
        ROOT, enabled=None, humor_level=8, mode=None, style=None
    )


@patch("jarvis_engine.persona.save_persona_config", return_value={"mode": "formal"})
def test_persona_config_set_mode_and_style(mock_save: MagicMock) -> None:
    handler = PersonaConfigHandler(ROOT)
    result = handler.handle(PersonaConfigCommand(mode="formal", style="concise"))
    mock_save.assert_called_once_with(
        ROOT, enabled=None, humor_level=None, mode="formal", style="concise"
    )
