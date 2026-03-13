"""Session management with hijack detection.

Tracks active sessions per device, enforces idle and absolute timeouts,
and detects session hijacking via fingerprint (IP + user-agent) changes.
"""

from __future__ import annotations

import hmac as _hmac_module
import logging
import secrets
import threading
import time
from dataclasses import dataclass

from jarvis_engine._shared import sha256_hex

logger = logging.getLogger(__name__)


# Session dataclass


@dataclass
class Session:
    """Represents a single authenticated session."""

    session_id: str
    device_id: str
    ip: str
    user_agent: str
    created_at: float
    last_active: float
    fingerprint: str


# Helpers


def _compute_fingerprint(ip: str, user_agent: str) -> str:
    """SHA-256 hash of ``ip:user_agent``."""
    raw = f"{ip}:{user_agent}"
    return sha256_hex(raw)


# SessionManager


class SessionManager:
    """In-memory session store with hijack detection.

    Parameters
    ----------
    max_sessions:
        Maximum number of concurrent active sessions.  When exceeded the
        oldest session is evicted.
    idle_timeout_s:
        Seconds of inactivity after which a session is considered expired.
        Default 24 hours.
    absolute_timeout_s:
        Maximum session lifetime regardless of activity.
        Default 72 hours.
    """

    def __init__(
        self,
        max_sessions: int = 3,
        idle_timeout_s: int = 86400,
        absolute_timeout_s: int = 259200,
    ) -> None:
        self._max_sessions = max(max_sessions, 1)
        self._idle_timeout_s = idle_timeout_s
        self._absolute_timeout_s = absolute_timeout_s
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    # Public API

    def create_session(self, device_id: str, ip: str, user_agent: str = "") -> str:
        """Create a new session and return its ID.

        If the maximum number of concurrent sessions has been reached the
        oldest session (by ``created_at``) is evicted first.
        """
        with self._lock:
            # Purge expired sessions first
            self._purge_expired()

            # Evict oldest sessions if at capacity
            while len(self._sessions) >= self._max_sessions:
                oldest_id = min(
                    self._sessions, key=lambda k: self._sessions[k].created_at
                )
                logger.info("Evicting oldest session %s to make room", oldest_id[:8])
                del self._sessions[oldest_id]

            now = time.time()
            session_id = secrets.token_hex(32)
            fingerprint = _compute_fingerprint(ip, user_agent)
            session = Session(
                session_id=session_id,
                device_id=device_id,
                ip=ip,
                user_agent=user_agent,
                created_at=now,
                last_active=now,
                fingerprint=fingerprint,
            )
            self._sessions[session_id] = session
            logger.info(
                "Created session %s for device %s from %s",
                session_id[:8],
                device_id,
                ip,
            )
            return session_id

    def validate_session(
        self, session_id: str, ip: str, user_agent: str = ""
    ) -> tuple[bool, str]:
        """Validate an existing session.

        Returns
        -------
        (valid, reason)
            ``valid`` is ``True`` if the session is active and passes all
            checks.  *reason* describes the validation result.

        Possible failure reasons:
        - ``SESSION_NOT_FOUND``
        - ``IDLE_TIMEOUT``
        - ``ABSOLUTE_TIMEOUT``
        - ``HIJACK_DETECTED`` — fingerprint mismatch triggers immediate
          session termination.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return (False, "SESSION_NOT_FOUND")

            now = time.time()

            # Check absolute timeout
            if now - session.created_at > self._absolute_timeout_s:
                self._sessions.pop(session_id, None)
                return (False, "ABSOLUTE_TIMEOUT")

            # Check idle timeout
            if now - session.last_active > self._idle_timeout_s:
                self._sessions.pop(session_id, None)
                return (False, "IDLE_TIMEOUT")

            # Check fingerprint (constant-time comparison)
            current_fingerprint = _compute_fingerprint(ip, user_agent)
            if not _hmac_module.compare_digest(
                current_fingerprint, session.fingerprint
            ):
                logger.warning(
                    "HIJACK DETECTED on session %s: fingerprint changed (device=%s)",
                    session_id[:8],
                    session.device_id,
                )
                self._sessions.pop(session_id, None)
                return (False, "HIJACK_DETECTED")

            # All checks passed — update last_active
            session.last_active = now
            return (True, "VALID")

    def terminate_session(self, session_id: str) -> None:
        """Remove a single session."""
        with self._lock:
            removed = self._sessions.pop(session_id, None)
        if removed is not None:
            logger.info("Terminated session %s", session_id[:8])

    def terminate_all_sessions(self) -> None:
        """Nuclear option — remove all active sessions (breach response)."""
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
        logger.warning("Terminated all %d sessions (breach response)", count)

    def get_active_sessions(self) -> list[dict]:
        """Return a list of all active (non-expired) sessions."""
        with self._lock:
            self._purge_expired()
            result: list[dict] = []
            for s in self._sessions.values():
                result.append(
                    {
                        "session_id": s.session_id[:8] + "...",
                        "device_id": s.device_id,
                        "ip": s.ip,
                        "user_agent": s.user_agent,
                        "created_at": s.created_at,
                        "last_active": s.last_active,
                    }
                )
            return result

    # Internal helpers

    def _purge_expired(self) -> None:
        """Remove sessions that have exceeded idle or absolute timeouts."""
        now = time.time()
        expired_ids: list[str] = []
        for sid, session in self._sessions.items():
            if now - session.created_at > self._absolute_timeout_s:
                expired_ids.append(sid)
            elif now - session.last_active > self._idle_timeout_s:
                expired_ids.append(sid)
        for sid in expired_ids:
            del self._sessions[sid]
            logger.debug("Purged expired session %s", sid[:8])
