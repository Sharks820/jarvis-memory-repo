"""Owner session authentication — authenticate once, operate freely.

Provides password-based owner authentication with session tokens.
Uses Argon2id for password hashing (preferred) with PBKDF2-HMAC-SHA256
fallback (600K iterations) when ``argon2-cffi`` is not installed.

Sessions are stored in memory only (never persisted) and expire after
an idle timeout.  Consecutive authentication failures trigger exponential
lockout to mitigate brute-force attacks.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class SessionStatus(TypedDict):
    """Result from :meth:`OwnerSessionManager.session_status`."""

    active: bool
    locked_out: bool
    session_count: int

# ---------------------------------------------------------------------------
# Argon2id — optional, fall back to PBKDF2 if not installed
# ---------------------------------------------------------------------------

_HAS_ARGON2 = False
_Argon2Hasher: type[Any] | None = None
_Argon2Mismatch: type[Exception] = Exception
try:
    from argon2 import PasswordHasher as _ImportedArgon2Hasher  # type: ignore[import-not-found]
    from argon2.exceptions import VerifyMismatchError as _ImportedArgon2Mismatch  # type: ignore[import-not-found]

    _Argon2Hasher = _ImportedArgon2Hasher
    _Argon2Mismatch = _ImportedArgon2Mismatch
    _HAS_ARGON2 = True
except ImportError:  # pragma: no cover
    logger.debug("argon2-cffi not installed; falling back to PBKDF2 for password hashing")

# ---------------------------------------------------------------------------
# PBKDF2 constants
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 600_000
_PBKDF2_SALT_LEN = 32  # 256-bit random salt per password


# ---------------------------------------------------------------------------
# OwnerSessionManager
# ---------------------------------------------------------------------------


class OwnerSessionManager:
    """Owner authentication with session tokens.

    Parameters
    ----------
    session_timeout:
        Idle timeout in seconds.  Sessions expire after this period of
        inactivity.  ``validate_session`` extends the deadline on success.
    max_failures:
        Consecutive wrong-password attempts before lockout.
    lockout_duration:
        Base lockout duration in seconds.  Exponential backoff applies:
        ``lockout_duration * 2 ** (lockout_count - 1)``.
    force_pbkdf2:
        When ``True``, always use PBKDF2 even if argon2 is available.
        Useful for testing without the optional dependency.
    """

    MAX_SESSIONS: int = 10

    def __init__(
        self,
        session_timeout: int = 1800,
        max_failures: int = 5,
        lockout_duration: int = 300,
        force_pbkdf2: bool = False,
    ) -> None:
        self._session_timeout = session_timeout
        self._max_failures = max(max_failures, 1)
        self._lockout_duration = lockout_duration
        self._force_pbkdf2 = force_pbkdf2
        self._lock = threading.Lock()

        # Password state
        self._password_hash: str | None = None
        self._password_salt: bytes | None = None  # PBKDF2 only
        self._hash_algo: str = ""  # "argon2" or "pbkdf2"

        # Session state — token -> expiry timestamp
        self._sessions: dict[str, float] = {}

        # Lockout state
        self._failure_count: int = 0
        self._lockout_until: float = 0.0
        self._lockout_count: int = 0  # for exponential backoff

        # Decide which hasher to use
        self._use_argon2 = _HAS_ARGON2 and not force_pbkdf2

    # ------------------------------------------------------------------
    # Internal housekeeping
    # ------------------------------------------------------------------

    def _purge_expired(self) -> None:
        """Remove expired sessions from ``_sessions``.  Caller must hold lock."""
        now = time.monotonic()
        expired = [t for t, exp in self._sessions.items() if now > exp]
        for t in expired:
            del self._sessions[t]

    # ------------------------------------------------------------------
    # Password management
    # ------------------------------------------------------------------

    def set_password(self, password: str) -> None:
        """Hash and store *password*.

        Replaces any previously stored password.  All existing sessions
        are invalidated — the owner must re-authenticate with the new
        password.
        """
        with self._lock:
            if self._use_argon2:
                if _Argon2Hasher is None:
                    raise RuntimeError("argon2 hasher unavailable")
                ph = _Argon2Hasher(
                    memory_cost=65536,
                    time_cost=3,
                    parallelism=4,
                    type=2,  # argon2id
                )
                self._password_hash = ph.hash(password)
                self._password_salt = None
                self._hash_algo = "argon2"
            else:
                salt = secrets.token_bytes(_PBKDF2_SALT_LEN)
                dk = hashlib.pbkdf2_hmac(
                    "sha256",
                    password.encode("utf-8"),
                    salt,
                    _PBKDF2_ITERATIONS,
                )
                self._password_hash = dk.hex()
                self._password_salt = salt
                self._hash_algo = "pbkdf2"
            # Invalidate all existing sessions — force re-auth with new password
            count = len(self._sessions)
            self._sessions.clear()
            if count:
                logger.info("Password changed — %d session(s) invalidated", count)
            logger.info("Owner password set (algo=%s)", self._hash_algo)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self, password: str) -> str | None:
        """Verify *password* and return a 64-char hex session token.

        Returns ``None`` if the password is wrong, no password has been
        set, or the account is locked out.
        """
        with self._lock:
            if self._password_hash is None:
                logger.warning("authenticate() called before set_password()")
                return None

            # Housekeeping — evict expired sessions before any checks
            self._purge_expired()

            # Check lockout
            if self._is_locked_out_internal():
                logger.warning("Authentication rejected — account is locked out")
                return None

            # Verify password
            if not self._verify_password_internal(password):
                self._failure_count += 1
                if self._failure_count >= self._max_failures:
                    self._lockout_count += 1
                    duration = self._lockout_duration * (2 ** (self._lockout_count - 1))
                    self._lockout_until = time.monotonic() + duration
                    logger.warning(
                        "Account locked out for %ds after %d failures (lockout #%d)",
                        duration,
                        self._failure_count,
                        self._lockout_count,
                    )
                return None

            # Check session cap after purge
            if len(self._sessions) >= self.MAX_SESSIONS:
                logger.warning(
                    "Session cap reached (%d) — rejecting new session",
                    self.MAX_SESSIONS,
                )
                return None

            # Success — reset failure and lockout counters, create session
            self._failure_count = 0
            self._lockout_count = 0
            token = secrets.token_hex(32)
            self._sessions[token] = time.monotonic() + self._session_timeout
            logger.info("Owner authenticated, session ...%s created", token[-4:])
            return token

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def validate_session(self, token: str) -> bool:
        """Return ``True`` if *token* is a valid active session.

        On success the idle timeout is extended (sliding window).
        """
        with self._lock:
            expiry = self._sessions.get(token)
            if expiry is None:
                return False
            if time.monotonic() > expiry:
                # Expired — remove it
                self._sessions.pop(token, None)
                return False
            # Extend idle timeout
            self._sessions[token] = time.monotonic() + self._session_timeout
            return True

    def logout(self, token: str) -> None:
        """Invalidate a specific session."""
        with self._lock:
            removed = self._sessions.pop(token, None)
        if removed is not None:
            logger.info("Session ...%s logged out", token[-4:])

    def logout_all(self) -> None:
        """Invalidate all active sessions."""
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
        logger.info("All %d sessions invalidated", count)

    # ------------------------------------------------------------------
    # Lockout
    # ------------------------------------------------------------------

    def is_locked_out(self) -> bool:
        """Return ``True`` if the account is currently locked out."""
        with self._lock:
            return self._is_locked_out_internal()

    def _is_locked_out_internal(self) -> bool:
        """Check lockout without acquiring the lock (caller must hold it)."""
        if self._lockout_until <= 0:
            return False
        if time.monotonic() >= self._lockout_until:
            # Lockout expired — reset failure count but keep lockout_count
            # for exponential backoff on next lockout
            self._failure_count = 0
            self._lockout_until = 0.0
            return False
        return True

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def session_status(self) -> SessionStatus:
        """Return a status summary.

        Returns
        -------
        dict
            ``active`` — whether any non-expired session exists.
            ``locked_out`` — whether the account is locked out.
            ``session_count`` — number of non-expired sessions.
        """
        with self._lock:
            self._purge_expired()
            return {
                "active": len(self._sessions) > 0,
                "locked_out": self._is_locked_out_internal(),
                "session_count": len(self._sessions),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def create_external_session(self) -> str | None:
        """Create a session token for an externally-verified owner.

        Use this when the owner has been authenticated through an
        alternative mechanism (e.g. master password verification) and
        needs a session token without going through the internal
        password check.

        Returns ``None`` if the session cap has been reached.
        """
        with self._lock:
            self._purge_expired()
            if len(self._sessions) >= self.MAX_SESSIONS:
                logger.warning(
                    "Session cap reached (%d) — rejecting new external session",
                    self.MAX_SESSIONS,
                )
                return None
            self._failure_count = 0
            token = secrets.token_hex(32)
            self._sessions[token] = time.monotonic() + self._session_timeout
            logger.info("External session ...%s created", token[-4:])
            return token

    def _verify_password_internal(self, password: str) -> bool:
        """Verify *password* against stored hash.  Caller must hold lock."""
        if self._hash_algo == "argon2":
            stored_hash = self._password_hash
            if not _HAS_ARGON2 or _Argon2Hasher is None or stored_hash is None:
                logger.error("Password stored with argon2 but argon2-cffi not installed")
                return False
            ph = _Argon2Hasher(
                memory_cost=65536,
                time_cost=3,
                parallelism=4,
                type=2,
            )
            try:
                return ph.verify(stored_hash, password)
            except _Argon2Mismatch:
                return False
        elif self._hash_algo == "pbkdf2":
            stored_hash = self._password_hash
            salt = self._password_salt
            if stored_hash is None or salt is None:
                logger.error("Password stored with PBKDF2 but salt/hash is missing")
                return False
            dk = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                _PBKDF2_ITERATIONS,
            )
            return secrets.compare_digest(dk.hex(), stored_hash)
        return False
