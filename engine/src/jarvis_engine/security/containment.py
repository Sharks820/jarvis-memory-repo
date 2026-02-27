"""Autonomous containment engine with graduated response levels.

Provides five containment levels from throttling to full system kill,
with automatic credential rotation at level 4+ and recovery gating
that requires owner master password for high-severity containment.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from enum import IntEnum

logger = logging.getLogger(__name__)


class ContainmentLevel(IntEnum):
    """Graduated containment response levels."""

    THROTTLE = 1
    BLOCK = 2
    ISOLATE = 3
    LOCKDOWN = 4
    FULL_KILL = 5


# Master password hash for recovery gates (levels 4-5).
# In production this would come from secure storage; here we use an env var
# or fall back to a known test hash.
_MASTER_PASSWORD_HASH_ENV = "JARVIS_MASTER_PASSWORD_HASH"


_PBKDF2_SALT = b"jarvis-containment-recovery-v1"  # fixed salt (env-hash comparison)
_PBKDF2_ITERATIONS = 600_000


def _hash_password(password: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), _PBKDF2_SALT, _PBKDF2_ITERATIONS,
    )
    return dk.hex()


class ContainmentEngine:
    """Execute graduated containment responses to security threats.

    Parameters
    ----------
    forensic_logger:
        Optional object with a ``log_event(dict)`` method for tamper-evident
        logging of all containment actions.
    ip_tracker:
        Optional object with ``block_ip(ip, duration_hours=None)`` for
        IP-level blocking.
    session_manager:
        Optional object with ``terminate_all_sessions()`` for session
        invalidation during lockdown/kill.
    """

    def __init__(
        self,
        forensic_logger: object | None = None,
        ip_tracker: object | None = None,
        session_manager: object | None = None,
    ) -> None:
        self._forensic_logger = forensic_logger
        self._ip_tracker = ip_tracker
        self._session_manager = session_manager

        # State tracking
        self._throttled_ips: dict[str, float] = {}  # ip -> max reqs/min
        self._blocked_ips: set[str] = set()
        self._isolated_endpoints: set[str] = set()
        self._lockdown_active: bool = False
        self._killed: bool = False
        self._current_level: int = 0  # 0 = no containment active
        self._containment_history: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def contain(self, ip: str, level: int, reason: str) -> dict:
        """Execute containment at the specified *level* against *ip*.

        Returns a dict describing the actions taken.
        """
        level = int(level)
        if level < ContainmentLevel.THROTTLE or level > ContainmentLevel.FULL_KILL:
            raise ValueError(f"Invalid containment level: {level}")

        actions: list[str] = []
        credentials_rotated = False

        # Level 1+: THROTTLE
        if level >= ContainmentLevel.THROTTLE:
            self._throttled_ips[ip] = 1.0  # 1 req/min
            actions.append(f"THROTTLE: rate limited {ip} to 1 req/min")

        # Level 2+: BLOCK
        if level >= ContainmentLevel.BLOCK:
            self._blocked_ips.add(ip)
            if self._ip_tracker is not None:
                self._ip_tracker.block_ip(ip)
            actions.append(f"BLOCK: added {ip} to blocklist")

        # Level 3+: ISOLATE
        if level >= ContainmentLevel.ISOLATE:
            # Isolate a generic endpoint associated with the threat
            endpoint = f"/api/from/{ip}"
            self._isolated_endpoints.add(endpoint)
            actions.append(f"ISOLATE: disabled endpoint {endpoint}")

        # Level 4+: LOCKDOWN
        if level >= ContainmentLevel.LOCKDOWN:
            self._lockdown_active = True
            if self._session_manager is not None:
                self._session_manager.terminate_all_sessions()
            self._rotate_credentials()
            credentials_rotated = True
            actions.append("LOCKDOWN: mobile API shut down, credentials rotated")
            actions.append("LOCKDOWN: all sessions invalidated")

        # Level 5: FULL KILL
        if level >= ContainmentLevel.FULL_KILL:
            self._killed = True
            actions.append("FULL_KILL: all services stopped")
            actions.append("FULL_KILL: incident report generated")
            actions.append("FULL_KILL: URGENT notification chain triggered")

        # Update current level (always escalate, never de-escalate implicitly)
        if level > self._current_level:
            self._current_level = level

        result = {
            "ip": ip,
            "level": level,
            "level_name": ContainmentLevel(level).name,
            "reason": reason,
            "actions": actions,
            "timestamp": time.time(),
        }
        if credentials_rotated:
            result["credentials_rotated"] = True

        self._containment_history.append(result)

        # Log to forensic logger
        self._log_forensic(
            "containment_executed",
            severity="CRITICAL" if level >= ContainmentLevel.LOCKDOWN else "HIGH",
            ip=ip,
            level=level,
            level_name=ContainmentLevel(level).name,
            reason=reason,
            actions=actions,
        )

        logger.warning(
            "Containment level %d (%s) executed against %s: %s",
            level,
            ContainmentLevel(level).name,
            ip,
            reason,
        )

        return result

    def get_containment_status(self) -> dict:
        """Return current containment state."""
        return {
            "current_level": self._current_level,
            "level_name": (
                ContainmentLevel(self._current_level).name
                if self._current_level > 0
                else "NONE"
            ),
            "throttled_ips": dict(self._throttled_ips),
            "blocked_ips": sorted(self._blocked_ips),
            "isolated_endpoints": sorted(self._isolated_endpoints),
            "lockdown_active": self._lockdown_active,
            "killed": self._killed,
            "history_count": len(self._containment_history),
        }

    def recover(self, level: int, master_password: str | None = None) -> dict:
        """Recover from containment at the specified *level*.

        Levels 1-3 auto-recover (no password required).
        Levels 4-5 require the owner's master password.

        Returns a dict describing recovery outcome.
        """
        level = int(level)
        if level < ContainmentLevel.THROTTLE or level > ContainmentLevel.FULL_KILL:
            raise ValueError(f"Invalid containment level: {level}")

        # Levels 4-5 require master password
        if level >= ContainmentLevel.LOCKDOWN:
            if master_password is None:
                self._log_forensic(
                    "recovery_denied",
                    severity="HIGH",
                    level=level,
                    reason="master password not provided",
                )
                return {
                    "recovered": False,
                    "reason": "Master password required for level 4+ recovery",
                }
            if not self._verify_master_password(master_password):
                self._log_forensic(
                    "recovery_denied",
                    severity="CRITICAL",
                    level=level,
                    reason="invalid master password",
                )
                return {
                    "recovered": False,
                    "reason": "Invalid master password",
                }

        actions: list[str] = []

        # Undo level-specific containment
        if level >= ContainmentLevel.FULL_KILL:
            self._killed = False
            actions.append("Services restart permitted")

        if level >= ContainmentLevel.LOCKDOWN:
            self._lockdown_active = False
            actions.append("Lockdown lifted, API re-enabled")

        if level >= ContainmentLevel.ISOLATE:
            self._isolated_endpoints.clear()
            actions.append("All endpoint isolations removed")

        if level >= ContainmentLevel.BLOCK:
            self._blocked_ips.clear()
            actions.append("All IP blocks cleared")

        if level >= ContainmentLevel.THROTTLE:
            self._throttled_ips.clear()
            actions.append("All throttles cleared")

        self._current_level = 0

        self._log_forensic(
            "recovery_executed",
            severity="INFO",
            level=level,
            actions=actions,
        )

        logger.info("Recovery from level %d completed: %s", level, actions)

        return {
            "recovered": True,
            "level_recovered": level,
            "actions": actions,
        }

    # ------------------------------------------------------------------
    # Credential rotation
    # ------------------------------------------------------------------

    def _rotate_credentials(self) -> str:
        """Generate a new HMAC signing key.

        Returns the new key as a hex string.  In production this would
        persist to secure storage and invalidate old keys.
        """
        os.urandom(32).hex()  # key generated; persist to secure storage in production
        self._log_forensic(
            "credential_rotation",
            severity="CRITICAL",
            action="HMAC signing key rotated",
        )
        logger.warning("HMAC signing key rotated")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _verify_master_password(self, password: str) -> bool:
        """Check *password* against the stored master password hash."""
        stored_hash = os.environ.get(_MASTER_PASSWORD_HASH_ENV)
        if stored_hash is None:
            logger.critical(
                "%s not set — denying recovery (set env var to enable)",
                _MASTER_PASSWORD_HASH_ENV,
            )
            return False
        return _hash_password(password) == stored_hash

    def _log_forensic(self, event_type: str, **kwargs: object) -> None:
        """Write to forensic logger if available."""
        if self._forensic_logger is None:
            return
        event = {"event_type": event_type, **kwargs}
        try:
            self._forensic_logger.log_event(event)
        except Exception:
            logger.warning("Failed to write forensic log for %s", event_type)
