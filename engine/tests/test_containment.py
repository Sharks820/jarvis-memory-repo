"""Tests for jarvis_engine.security.containment."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.security.containment import ContainmentEngine, ContainmentLevel


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _make_engine(**kwargs):
    """Create a ContainmentEngine with optional mock collaborators."""
    return ContainmentEngine(**kwargs)


# ---------------------------------------------------------------
# Containment levels
# ---------------------------------------------------------------


class TestContainmentLevels:
    def test_throttle_rate_limits_ip(self) -> None:
        eng = _make_engine()
        result = eng.contain("10.0.0.1", ContainmentLevel.THROTTLE, "scan detected")
        assert result["level"] == 1
        assert result["level_name"] == "THROTTLE"
        assert "10.0.0.1" in result["ip"]
        status = eng.get_containment_status()
        assert "10.0.0.1" in status["throttled_ips"]
        assert status["throttled_ips"]["10.0.0.1"] == 1.0

    def test_block_adds_ip_to_blocklist(self) -> None:
        eng = _make_engine()
        result = eng.contain("10.0.0.2", ContainmentLevel.BLOCK, "brute force")
        assert result["level"] == 2
        status = eng.get_containment_status()
        assert "10.0.0.2" in status["blocked_ips"]
        # Block also throttles
        assert "10.0.0.2" in status["throttled_ips"]

    def test_isolate_disables_endpoint(self) -> None:
        eng = _make_engine()
        result = eng.contain("10.0.0.3", ContainmentLevel.ISOLATE, "targeted attack")
        assert result["level"] == 3
        status = eng.get_containment_status()
        assert len(status["isolated_endpoints"]) > 0
        assert status["current_level"] == 3

    def test_lockdown_shuts_api_and_rotates(self) -> None:
        eng = _make_engine()
        result = eng.contain("10.0.0.4", ContainmentLevel.LOCKDOWN, "breach detected")
        assert result["level"] == 4
        assert "new_signing_key" in result
        assert len(result["new_signing_key"]) == 64  # 32 bytes hex
        status = eng.get_containment_status()
        assert status["lockdown_active"] is True

    def test_full_kill_stops_everything(self) -> None:
        eng = _make_engine()
        result = eng.contain("10.0.0.5", ContainmentLevel.FULL_KILL, "active exploit")
        assert result["level"] == 5
        assert result["level_name"] == "FULL_KILL"
        status = eng.get_containment_status()
        assert status["killed"] is True
        assert status["lockdown_active"] is True
        assert "10.0.0.5" in status["blocked_ips"]

    def test_invalid_level_raises(self) -> None:
        eng = _make_engine()
        with pytest.raises(ValueError, match="Invalid containment level"):
            eng.contain("10.0.0.1", 0, "bad level")
        with pytest.raises(ValueError, match="Invalid containment level"):
            eng.contain("10.0.0.1", 6, "bad level")

    def test_contain_returns_actions_list(self) -> None:
        eng = _make_engine()
        result = eng.contain("10.0.0.1", ContainmentLevel.BLOCK, "test")
        assert isinstance(result["actions"], list)
        assert len(result["actions"]) >= 2  # throttle + block
        assert any("THROTTLE" in a for a in result["actions"])
        assert any("BLOCK" in a for a in result["actions"])


# ---------------------------------------------------------------
# Credential rotation
# ---------------------------------------------------------------


class TestCredentialRotation:
    def test_rotation_returns_new_key(self) -> None:
        eng = _make_engine()
        result = eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "test rotation")
        key = result["new_signing_key"]
        assert isinstance(key, str)
        assert len(key) == 64

    def test_rotation_produces_different_keys(self) -> None:
        eng = _make_engine()
        r1 = eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "first")
        r2 = eng.contain("10.0.0.2", ContainmentLevel.LOCKDOWN, "second")
        assert r1["new_signing_key"] != r2["new_signing_key"]


# ---------------------------------------------------------------
# Recovery gates
# ---------------------------------------------------------------


class TestRecovery:
    def test_recover_level_1_no_password(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.THROTTLE, "test")
        result = eng.recover(ContainmentLevel.THROTTLE)
        assert result["recovered"] is True
        status = eng.get_containment_status()
        assert len(status["throttled_ips"]) == 0
        assert status["current_level"] == 0

    def test_recover_level_2_no_password(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.BLOCK, "test")
        result = eng.recover(ContainmentLevel.BLOCK)
        assert result["recovered"] is True
        status = eng.get_containment_status()
        assert len(status["blocked_ips"]) == 0

    def test_recover_level_3_no_password(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.ISOLATE, "test")
        result = eng.recover(ContainmentLevel.ISOLATE)
        assert result["recovered"] is True
        status = eng.get_containment_status()
        assert len(status["isolated_endpoints"]) == 0

    def test_recover_level_4_needs_password(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "test")
        result = eng.recover(ContainmentLevel.LOCKDOWN)
        assert result["recovered"] is False
        assert "Master password required" in result["reason"]

    def test_recover_level_5_needs_password(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.FULL_KILL, "test")
        result = eng.recover(ContainmentLevel.FULL_KILL)
        assert result["recovered"] is False

    @patch.dict("os.environ", {}, clear=False)
    def test_recover_level_4_with_password_succeeds(self) -> None:
        # No env hash configured -> any non-empty password accepted
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "test")
        result = eng.recover(ContainmentLevel.LOCKDOWN, master_password="owner123")
        assert result["recovered"] is True
        status = eng.get_containment_status()
        assert status["lockdown_active"] is False

    @patch.dict("os.environ", {}, clear=False)
    def test_recover_level_5_with_password_succeeds(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.FULL_KILL, "test")
        result = eng.recover(ContainmentLevel.FULL_KILL, master_password="owner123")
        assert result["recovered"] is True
        status = eng.get_containment_status()
        assert status["killed"] is False
        assert status["lockdown_active"] is False
        assert status["current_level"] == 0

    def test_recover_invalid_level_raises(self) -> None:
        eng = _make_engine()
        with pytest.raises(ValueError, match="Invalid containment level"):
            eng.recover(0)

    @patch.dict(
        "os.environ",
        {"JARVIS_MASTER_PASSWORD_HASH": "wrong_hash"},
        clear=False,
    )
    def test_recover_level_4_wrong_password_denied(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "test")
        result = eng.recover(ContainmentLevel.LOCKDOWN, master_password="bad_pass")
        assert result["recovered"] is False
        assert "Invalid master password" in result["reason"]


# ---------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------


class TestStatus:
    def test_initial_status_clean(self) -> None:
        eng = _make_engine()
        status = eng.get_containment_status()
        assert status["current_level"] == 0
        assert status["level_name"] == "NONE"
        assert len(status["throttled_ips"]) == 0
        assert len(status["blocked_ips"]) == 0
        assert len(status["isolated_endpoints"]) == 0
        assert status["lockdown_active"] is False
        assert status["killed"] is False
        assert status["history_count"] == 0

    def test_status_after_multiple_containments(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.THROTTLE, "scan")
        eng.contain("10.0.0.2", ContainmentLevel.BLOCK, "brute force")
        status = eng.get_containment_status()
        assert status["current_level"] == 2
        assert len(status["throttled_ips"]) == 2
        assert len(status["blocked_ips"]) == 1
        assert status["history_count"] == 2

    def test_level_only_escalates(self) -> None:
        eng = _make_engine()
        eng.contain("10.0.0.1", ContainmentLevel.ISOLATE, "serious")
        eng.contain("10.0.0.2", ContainmentLevel.THROTTLE, "minor")
        status = eng.get_containment_status()
        assert status["current_level"] == 3  # stays at ISOLATE


# ---------------------------------------------------------------
# Forensic logger integration
# ---------------------------------------------------------------


class TestForensicLoggerIntegration:
    def test_contain_logs_event(self) -> None:
        mock_logger = MagicMock()
        eng = _make_engine(forensic_logger=mock_logger)
        eng.contain("10.0.0.1", ContainmentLevel.THROTTLE, "test")
        mock_logger.log_event.assert_called_once()
        event = mock_logger.log_event.call_args[0][0]
        assert event["event_type"] == "containment_executed"
        assert event["ip"] == "10.0.0.1"
        assert event["level"] == 1

    def test_recovery_logs_event(self) -> None:
        mock_logger = MagicMock()
        eng = _make_engine(forensic_logger=mock_logger)
        eng.contain("10.0.0.1", ContainmentLevel.THROTTLE, "test")
        mock_logger.reset_mock()
        eng.recover(ContainmentLevel.THROTTLE)
        mock_logger.log_event.assert_called_once()
        event = mock_logger.log_event.call_args[0][0]
        assert event["event_type"] == "recovery_executed"

    def test_lockdown_logs_credential_rotation(self) -> None:
        mock_logger = MagicMock()
        eng = _make_engine(forensic_logger=mock_logger)
        eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "test")
        # Should have containment + rotation log events
        assert mock_logger.log_event.call_count == 2
        event_types = [
            call[0][0]["event_type"]
            for call in mock_logger.log_event.call_args_list
        ]
        assert "credential_rotation" in event_types
        assert "containment_executed" in event_types

    def test_denied_recovery_logs_event(self) -> None:
        mock_logger = MagicMock()
        eng = _make_engine(forensic_logger=mock_logger)
        eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "test")
        mock_logger.reset_mock()
        eng.recover(ContainmentLevel.LOCKDOWN)  # no password
        mock_logger.log_event.assert_called_once()
        event = mock_logger.log_event.call_args[0][0]
        assert event["event_type"] == "recovery_denied"


# ---------------------------------------------------------------
# IP tracker integration
# ---------------------------------------------------------------


class TestIPTrackerIntegration:
    def test_block_calls_ip_tracker(self) -> None:
        mock_tracker = MagicMock()
        eng = _make_engine(ip_tracker=mock_tracker)
        eng.contain("10.0.0.1", ContainmentLevel.BLOCK, "test")
        mock_tracker.block_ip.assert_called_once_with("10.0.0.1")

    def test_throttle_does_not_call_ip_tracker(self) -> None:
        mock_tracker = MagicMock()
        eng = _make_engine(ip_tracker=mock_tracker)
        eng.contain("10.0.0.1", ContainmentLevel.THROTTLE, "test")
        mock_tracker.block_ip.assert_not_called()

    def test_isolate_calls_ip_tracker(self) -> None:
        mock_tracker = MagicMock()
        eng = _make_engine(ip_tracker=mock_tracker)
        eng.contain("10.0.0.1", ContainmentLevel.ISOLATE, "test")
        mock_tracker.block_ip.assert_called_once_with("10.0.0.1")

    def test_lockdown_calls_ip_tracker(self) -> None:
        mock_tracker = MagicMock()
        eng = _make_engine(ip_tracker=mock_tracker)
        eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "test")
        mock_tracker.block_ip.assert_called_once()


# ---------------------------------------------------------------
# Session manager integration
# ---------------------------------------------------------------


class TestSessionManagerIntegration:
    def test_lockdown_terminates_all_sessions(self) -> None:
        mock_sm = MagicMock()
        eng = _make_engine(session_manager=mock_sm)
        eng.contain("10.0.0.1", ContainmentLevel.LOCKDOWN, "breach")
        mock_sm.terminate_all_sessions.assert_called_once()

    def test_throttle_does_not_terminate_sessions(self) -> None:
        mock_sm = MagicMock()
        eng = _make_engine(session_manager=mock_sm)
        eng.contain("10.0.0.1", ContainmentLevel.THROTTLE, "minor")
        mock_sm.terminate_all_sessions.assert_not_called()

    def test_full_kill_terminates_all_sessions(self) -> None:
        mock_sm = MagicMock()
        eng = _make_engine(session_manager=mock_sm)
        eng.contain("10.0.0.1", ContainmentLevel.FULL_KILL, "exploit")
        mock_sm.terminate_all_sessions.assert_called_once()
