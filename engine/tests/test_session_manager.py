"""Tests for SessionManager — session tracking with hijack detection."""
from __future__ import annotations

import hashlib
import time

import pytest

from jarvis_engine.security.session_manager import (
    Session,
    SessionManager,
    _compute_fingerprint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def manager() -> SessionManager:
    return SessionManager(max_sessions=3, idle_timeout_s=3600, absolute_timeout_s=7200)


@pytest.fixture()
def small_manager() -> SessionManager:
    """Manager with max 2 sessions for eviction tests."""
    return SessionManager(max_sessions=2, idle_timeout_s=3600, absolute_timeout_s=7200)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_deterministic(self) -> None:
        fp1 = _compute_fingerprint("10.0.0.1", "Mozilla/5.0")
        fp2 = _compute_fingerprint("10.0.0.1", "Mozilla/5.0")
        assert fp1 == fp2

    def test_different_ip_different_fingerprint(self) -> None:
        fp1 = _compute_fingerprint("10.0.0.1", "Mozilla/5.0")
        fp2 = _compute_fingerprint("10.0.0.2", "Mozilla/5.0")
        assert fp1 != fp2

    def test_different_ua_different_fingerprint(self) -> None:
        fp1 = _compute_fingerprint("10.0.0.1", "Mozilla/5.0")
        fp2 = _compute_fingerprint("10.0.0.1", "curl/7.0")
        assert fp1 != fp2

    def test_is_sha256(self) -> None:
        fp = _compute_fingerprint("10.0.0.1", "test")
        assert len(fp) == 64  # SHA-256 hex digest length
        int(fp, 16)  # Should be valid hex

    def test_matches_manual_hash(self) -> None:
        raw = "10.0.0.1:Mozilla/5.0"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert _compute_fingerprint("10.0.0.1", "Mozilla/5.0") == expected


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------


class TestSessionCreation:
    def test_create_returns_session_id(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "Mozilla/5.0")
        assert isinstance(sid, str)
        assert len(sid) == 32  # UUID hex without dashes

    def test_create_multiple_sessions(self, manager: SessionManager) -> None:
        s1 = manager.create_session("device1", "10.0.0.1")
        s2 = manager.create_session("device2", "10.0.0.2")
        assert s1 != s2

    def test_session_appears_in_active_list(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "UA")
        sessions = manager.get_active_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == sid
        assert sessions[0]["device_id"] == "device1"
        assert sessions[0]["ip"] == "10.0.0.1"

    def test_empty_user_agent_allowed(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1")
        valid, reason = manager.validate_session(sid, "10.0.0.1")
        assert valid is True


# ---------------------------------------------------------------------------
# Session validation — valid
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_session(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "Mozilla/5.0")
        valid, reason = manager.validate_session(sid, "10.0.0.1", "Mozilla/5.0")
        assert valid is True
        assert reason == "VALID"

    def test_updates_last_active(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "Mozilla/5.0")
        # Access the internal session to check last_active
        before = manager._sessions[sid].last_active
        time.sleep(0.05)
        manager.validate_session(sid, "10.0.0.1", "Mozilla/5.0")
        after = manager._sessions[sid].last_active
        assert after >= before


# ---------------------------------------------------------------------------
# Session validation — not found
# ---------------------------------------------------------------------------


class TestSessionNotFound:
    def test_nonexistent_session(self, manager: SessionManager) -> None:
        valid, reason = manager.validate_session("bogus-id", "10.0.0.1")
        assert valid is False
        assert reason == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Session validation — idle timeout
# ---------------------------------------------------------------------------


class TestIdleTimeout:
    def test_idle_timeout_detected(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "Mozilla/5.0")
        # Manually set last_active to the past
        manager._sessions[sid].last_active = time.time() - 7200
        valid, reason = manager.validate_session(sid, "10.0.0.1", "Mozilla/5.0")
        assert valid is False
        assert reason == "IDLE_TIMEOUT"
        # Session should be removed
        assert sid not in manager._sessions


# ---------------------------------------------------------------------------
# Session validation — absolute timeout
# ---------------------------------------------------------------------------


class TestAbsoluteTimeout:
    def test_absolute_timeout_detected(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "Mozilla/5.0")
        # Set created_at far in the past
        manager._sessions[sid].created_at = time.time() - 10000
        valid, reason = manager.validate_session(sid, "10.0.0.1", "Mozilla/5.0")
        assert valid is False
        assert reason == "ABSOLUTE_TIMEOUT"
        assert sid not in manager._sessions


# ---------------------------------------------------------------------------
# Session validation — hijack detection
# ---------------------------------------------------------------------------


class TestHijackDetection:
    def test_ip_change_triggers_hijack(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "Mozilla/5.0")
        valid, reason = manager.validate_session(sid, "192.168.1.1", "Mozilla/5.0")
        assert valid is False
        assert reason == "HIJACK_DETECTED"
        # Session should be terminated
        assert sid not in manager._sessions

    def test_user_agent_change_triggers_hijack(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "Mozilla/5.0")
        valid, reason = manager.validate_session(sid, "10.0.0.1", "curl/7.0")
        assert valid is False
        assert reason == "HIJACK_DETECTED"

    def test_both_changed_triggers_hijack(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1", "Mozilla/5.0")
        valid, reason = manager.validate_session(sid, "1.2.3.4", "EvilBot/1.0")
        assert valid is False
        assert reason == "HIJACK_DETECTED"


# ---------------------------------------------------------------------------
# Max sessions and eviction
# ---------------------------------------------------------------------------


class TestEviction:
    def test_oldest_evicted_at_capacity(self, small_manager: SessionManager) -> None:
        s1 = small_manager.create_session("device1", "10.0.0.1")
        time.sleep(0.01)
        s2 = small_manager.create_session("device2", "10.0.0.2")
        time.sleep(0.01)
        # This should evict s1 (oldest)
        s3 = small_manager.create_session("device3", "10.0.0.3")

        assert s1 not in small_manager._sessions
        assert s2 in small_manager._sessions
        assert s3 in small_manager._sessions

    def test_active_sessions_count_after_eviction(
        self, small_manager: SessionManager
    ) -> None:
        small_manager.create_session("d1", "10.0.0.1")
        time.sleep(0.01)
        small_manager.create_session("d2", "10.0.0.2")
        time.sleep(0.01)
        small_manager.create_session("d3", "10.0.0.3")
        assert len(small_manager.get_active_sessions()) == 2


# ---------------------------------------------------------------------------
# Termination
# ---------------------------------------------------------------------------


class TestTermination:
    def test_terminate_single_session(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1")
        manager.terminate_session(sid)
        assert len(manager.get_active_sessions()) == 0

    def test_terminate_nonexistent_session_no_error(
        self, manager: SessionManager
    ) -> None:
        # Should not raise
        manager.terminate_session("does-not-exist")

    def test_terminate_all_sessions(self, manager: SessionManager) -> None:
        manager.create_session("d1", "10.0.0.1")
        manager.create_session("d2", "10.0.0.2")
        manager.create_session("d3", "10.0.0.3")
        assert len(manager.get_active_sessions()) == 3

        manager.terminate_all_sessions()
        assert len(manager.get_active_sessions()) == 0

    def test_terminated_session_not_valid(self, manager: SessionManager) -> None:
        sid = manager.create_session("device1", "10.0.0.1")
        manager.terminate_session(sid)
        valid, reason = manager.validate_session(sid, "10.0.0.1")
        assert valid is False
        assert reason == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Purge expired
# ---------------------------------------------------------------------------


class TestPurgeExpired:
    def test_expired_sessions_purged_on_get_active(
        self, manager: SessionManager
    ) -> None:
        sid = manager.create_session("device1", "10.0.0.1")
        # Force expiry
        manager._sessions[sid].created_at = time.time() - 100000
        manager._sessions[sid].last_active = time.time() - 100000

        sessions = manager.get_active_sessions()
        assert len(sessions) == 0

    def test_expired_sessions_purged_on_create(
        self, small_manager: SessionManager
    ) -> None:
        s1 = small_manager.create_session("d1", "10.0.0.1")
        s2 = small_manager.create_session("d2", "10.0.0.2")
        # Expire both
        small_manager._sessions[s1].created_at = time.time() - 100000
        small_manager._sessions[s1].last_active = time.time() - 100000
        small_manager._sessions[s2].created_at = time.time() - 100000
        small_manager._sessions[s2].last_active = time.time() - 100000

        # Should be able to create without eviction since expired are purged
        s3 = small_manager.create_session("d3", "10.0.0.3")
        assert s3 in small_manager._sessions
        assert len(small_manager.get_active_sessions()) == 1


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


class TestSessionDataclass:
    def test_session_fields(self) -> None:
        s = Session(
            session_id="abc123",
            device_id="device1",
            ip="10.0.0.1",
            user_agent="Mozilla/5.0",
            created_at=1000.0,
            last_active=1000.0,
            fingerprint="deadbeef",
        )
        assert s.session_id == "abc123"
        assert s.device_id == "device1"
        assert s.ip == "10.0.0.1"
        assert s.user_agent == "Mozilla/5.0"
        assert s.created_at == 1000.0
        assert s.last_active == 1000.0
        assert s.fingerprint == "deadbeef"
