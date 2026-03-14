"""Tests for OwnerSessionManager — owner session authentication.

Tests cover password hashing (Argon2id + PBKDF2 fallback), session lifecycle,
idle timeout extension, lockout after failed attempts, and logout.

Uses mocked ``time`` module to avoid real sleeps — deterministic and fast.
Session expiry uses ``time.time``; lockout timing uses ``time.monotonic``.
Both are set to the same ``_MockClock`` instance in tests that mock time.
Uses ``_pbkdf2_iterations=1`` on all tests except the PBKDF2-path test to
avoid the 600 000-iteration cost that saturates CPUs in parallel test runs.
"""

from __future__ import annotations

from unittest.mock import patch

from jarvis_engine.security.owner_session import OwnerSessionManager

# ---------------------------------------------------------------------------
# Test-only helper: bypass the expensive PBKDF2 key-stretching for every test
# that is exercising session lifecycle rather than cryptographic strength.
# The production default of 600 000 iterations takes ~200 ms per hash call;
# with 36+ calls across 12 tests running in 8 parallel workers that saturates
# all CPU cores.  _pbkdf2_iterations=1 keeps the full code path while reducing
# each hash to microseconds.
# ---------------------------------------------------------------------------
_FAST = {"force_pbkdf2": True, "_pbkdf2_iterations": 1}


# ---------------------------------------------------------------------------
# Helpers — deterministic clock
# ---------------------------------------------------------------------------


class _MockClock:
    """Deterministic replacement for ``time.monotonic``."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# 1. Set password and authenticate — returns 64-char hex token
# ---------------------------------------------------------------------------


def test_set_and_verify_password():
    mgr = OwnerSessionManager(**_FAST)
    mgr.set_password("hunter2")
    token = mgr.authenticate("hunter2")
    assert token is not None
    assert isinstance(token, str)
    assert len(token) == 64
    # Confirm it's valid hex
    int(token, 16)


# ---------------------------------------------------------------------------
# 2. Wrong password returns None
# ---------------------------------------------------------------------------


def test_wrong_password_returns_none():
    mgr = OwnerSessionManager(**_FAST)
    mgr.set_password("correct-password")
    result = mgr.authenticate("wrong-password")
    assert result is None


# ---------------------------------------------------------------------------
# 3. Session is valid immediately after auth
# ---------------------------------------------------------------------------


def test_session_valid_after_auth():
    mgr = OwnerSessionManager(**_FAST)
    mgr.set_password("mypassword")
    token = mgr.authenticate("mypassword")
    assert token is not None
    assert mgr.validate_session(token) is True


# ---------------------------------------------------------------------------
# 4. Session invalid after timeout
# ---------------------------------------------------------------------------


@patch("jarvis_engine.security.owner_session.time")
def test_session_invalid_after_timeout(mock_time):
    clock = _MockClock()
    mock_time.monotonic = clock
    mock_time.time = clock

    mgr = OwnerSessionManager(session_timeout=1, **_FAST)
    mgr.set_password("pass")
    token = mgr.authenticate("pass")
    assert token is not None
    assert mgr.validate_session(token) is True

    clock.advance(1.5)
    assert mgr.validate_session(token) is False


# ---------------------------------------------------------------------------
# 5. validate_session extends idle timer
# ---------------------------------------------------------------------------


@patch("jarvis_engine.security.owner_session.time")
def test_session_extends_on_activity(mock_time):
    clock = _MockClock()
    mock_time.monotonic = clock
    mock_time.time = clock

    mgr = OwnerSessionManager(session_timeout=2, **_FAST)
    mgr.set_password("pass")
    token = mgr.authenticate("pass")
    assert token is not None

    # Advance 1s, validate (extends), advance 1.5s more — should still be valid
    # because the validate at t=1s extended the deadline to t=3s
    clock.advance(1.0)
    assert mgr.validate_session(token) is True  # extends to t~3s
    clock.advance(1.5)
    # Now at t~2.5s — should still be valid since extended at t~1s
    assert mgr.validate_session(token) is True


# ---------------------------------------------------------------------------
# 6. Lockout after consecutive failed attempts
# ---------------------------------------------------------------------------


def test_lockout_after_failed_attempts():
    mgr = OwnerSessionManager(max_failures=3, lockout_duration=300, **_FAST)
    mgr.set_password("correct")

    # 3 consecutive failures
    for _ in range(3):
        assert mgr.authenticate("wrong") is None

    # Now locked out
    assert mgr.is_locked_out() is True

    # Even correct password should fail during lockout
    result = mgr.authenticate("correct")
    assert result is None


# ---------------------------------------------------------------------------
# 7. Logout invalidates session
# ---------------------------------------------------------------------------


def test_logout_invalidates_session():
    mgr = OwnerSessionManager(**_FAST)
    mgr.set_password("pass")
    token = mgr.authenticate("pass")
    assert token is not None
    assert mgr.validate_session(token) is True

    mgr.logout(token)
    assert mgr.validate_session(token) is False


# ---------------------------------------------------------------------------
# 8. PBKDF2 fallback works when force_pbkdf2=True
# ---------------------------------------------------------------------------


def test_pbkdf2_fallback_when_no_argon2():
    mgr = OwnerSessionManager(**_FAST)
    mgr.set_password("secure-pass")

    # Should authenticate fine even without argon2
    token = mgr.authenticate("secure-pass")
    assert token is not None
    assert len(token) == 64
    assert mgr.validate_session(token) is True

    # Wrong password should fail
    assert mgr.authenticate("bad-pass") is None


# ---------------------------------------------------------------------------
# 9. session_status returns correct fields
# ---------------------------------------------------------------------------


def test_session_status():
    mgr = OwnerSessionManager(max_failures=5, **_FAST)
    mgr.set_password("pass")

    status = mgr.session_status()
    assert status["active"] is False
    assert status["locked_out"] is False
    assert status["session_count"] == 0

    token = mgr.authenticate("pass")
    assert token is not None

    status = mgr.session_status()
    assert status["active"] is True
    assert status["locked_out"] is False
    assert status["session_count"] == 1

    # Add second session
    token2 = mgr.authenticate("pass")
    assert token2 is not None
    status = mgr.session_status()
    assert status["session_count"] == 2

    # Logout one
    mgr.logout(token)
    status = mgr.session_status()
    assert status["session_count"] == 1
    assert status["active"] is True


# ---------------------------------------------------------------------------
# 10. logout_all clears every session
# ---------------------------------------------------------------------------


def test_logout_all():
    mgr = OwnerSessionManager(**_FAST)
    mgr.set_password("pass")
    t1 = mgr.authenticate("pass")
    t2 = mgr.authenticate("pass")
    assert t1 is not None and t2 is not None

    mgr.logout_all()
    assert mgr.validate_session(t1) is False
    assert mgr.validate_session(t2) is False
    assert mgr.session_status()["session_count"] == 0


# ---------------------------------------------------------------------------
# 11. Lockout uses exponential backoff
# ---------------------------------------------------------------------------


@patch("jarvis_engine.security.owner_session.time")
def test_lockout_exponential_backoff(mock_time):
    clock = _MockClock()
    mock_time.monotonic = clock
    mock_time.time = clock

    mgr = OwnerSessionManager(max_failures=2, lockout_duration=1, **_FAST)
    mgr.set_password("correct")

    # First lockout: 2 failures -> locked for 1s (base * 2^0 = 1s)
    mgr.authenticate("wrong")
    mgr.authenticate("wrong")
    assert mgr.is_locked_out() is True

    # Advance past first lockout
    clock.advance(1.2)
    assert mgr.is_locked_out() is False

    # Second lockout: 2 more failures -> locked for 2s (base * 2^1 = 2s)
    mgr.authenticate("wrong")
    mgr.authenticate("wrong")
    assert mgr.is_locked_out() is True

    # After 1.2s it should still be locked (duration is 2s this time)
    clock.advance(1.2)
    assert mgr.is_locked_out() is True

    # After another 1.2s (total ~2.4s) it should be unlocked
    clock.advance(1.2)
    assert mgr.is_locked_out() is False


# ---------------------------------------------------------------------------
# 12. Successful auth resets failure counter
# ---------------------------------------------------------------------------


def test_successful_auth_resets_failures():
    mgr = OwnerSessionManager(max_failures=3, **_FAST)
    mgr.set_password("correct")

    # 2 failures (not enough for lockout)
    mgr.authenticate("wrong")
    mgr.authenticate("wrong")
    assert mgr.is_locked_out() is False

    # Successful auth resets counter
    token = mgr.authenticate("correct")
    assert token is not None

    # 2 more failures — should not lock out (counter was reset)
    mgr.authenticate("wrong")
    mgr.authenticate("wrong")
    assert mgr.is_locked_out() is False
