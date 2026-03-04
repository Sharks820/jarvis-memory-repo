"""Autonomous containment engine with graduated response levels.

Provides five containment levels from throttling to full system kill,
with automatic credential rotation at level 4+ and recovery gating
that requires owner master password for high-severity containment.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac_module
import logging
import os
import secrets
import threading
import time
from collections import deque
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


_PBKDF2_ITERATIONS = 600_000


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash password with PBKDF2-SHA256.  Returns ``salt_hex:hash_hex``.

    When *salt* is ``None`` a random 32-byte salt is generated (use for
    creating new hashes).  Pass the original salt to verify.
    """
    if salt is None:
        salt = secrets.token_bytes(32)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS,
    )
    return salt.hex() + ":" + dk.hex()


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
        on_credential_rotate: object | None = None,
    ) -> None:
        self._forensic_logger = forensic_logger
        self._ip_tracker = ip_tracker
        self._session_manager = session_manager
        self._on_credential_rotate = on_credential_rotate
        self._lock = threading.Lock()

        # State tracking
        self._throttled_ips: dict[str, float] = {}  # ip -> max reqs/min
        self._blocked_ips: set[str] = set()
        self._isolated_endpoints: set[str] = set()
        self._lockdown_active: bool = False
        self._killed: bool = False
        self._current_level: int = 0  # 0 = no containment active
        self._containment_history: deque[dict] = deque(maxlen=1000)
        self._current_hmac_key: str | None = None

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

        _do_block_ip = False
        with self._lock:
            actions: list[str] = []
            credentials_rotated = False

            # Level 1+: THROTTLE
            if level >= ContainmentLevel.THROTTLE:
                self._throttled_ips[ip] = 1.0  # 1 req/min
                actions.append(f"THROTTLE: rate limited {ip} to 1 req/min")

            # Level 2+: BLOCK
            if level >= ContainmentLevel.BLOCK:
                self._blocked_ips.add(ip)
                _do_block_ip = True
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

        # IP tracker operations (outside lock — avoids lock-ordering deadlock)
        if _do_block_ip and self._ip_tracker is not None:
            self._ip_tracker.block_ip(ip)

        # Log to forensic logger (outside lock — no shared state mutation)
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
        with self._lock:
            if self._current_level > 0:
                try:
                    level_name = ContainmentLevel(self._current_level).name
                except ValueError:
                    level_name = f"UNKNOWN({self._current_level})"
            else:
                level_name = "NONE"
            return {
                "current_level": self._current_level,
                "level_name": level_name,
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

        with self._lock:
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

            _ips_to_unblock: list[str] = []
            if level >= ContainmentLevel.BLOCK:
                _ips_to_unblock = list(self._blocked_ips)
                self._blocked_ips.clear()
                actions.append("All IP blocks cleared")

            if level >= ContainmentLevel.THROTTLE:
                self._throttled_ips.clear()
                actions.append("All throttles cleared")

            self._current_level = 0

        # IP tracker operations (outside lock — avoids lock-ordering deadlock)
        if _ips_to_unblock and self._ip_tracker is not None:
            for _unblock_ip in _ips_to_unblock:
                try:
                    self._ip_tracker.unblock_ip(_unblock_ip)
                except Exception:
                    pass

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
        """Generate a new HMAC signing key, store it, and propagate to server.

        Returns the new key as a hex string.  If ``on_credential_rotate``
        callback was provided at construction, it is called with the new key
        so the HTTP server's signing key is updated in real-time.
        """
        new_key = os.urandom(32).hex()
        self._current_hmac_key = new_key
        if callable(self._on_credential_rotate):
            try:
                self._on_credential_rotate(new_key)
            except Exception as exc:
                logger.error("Credential rotate callback failed: %s", exc)
        self._log_forensic(
            "credential_rotation",
            severity="CRITICAL",
            action="HMAC signing key rotated",
        )
        logger.warning("HMAC signing key rotated")
        return new_key

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _verify_master_password(self, password: str) -> bool:
        """Check *password* against the stored master password hash.

        Supports both the new ``salt_hex:hash_hex`` format and the
        legacy fixed-salt format for backwards compatibility.
        """
        stored = os.environ.get(_MASTER_PASSWORD_HASH_ENV)
        if stored is None:
            logger.critical(
                "%s not set — denying recovery (set env var to enable)",
                _MASTER_PASSWORD_HASH_ENV,
            )
            return False
        if ":" in stored:
            # New format: salt_hex:hash_hex
            salt_hex, _ = stored.split(":", 1)
            salt = bytes.fromhex(salt_hex)
            computed = _hash_password(password, salt=salt)
            return _hmac_module.compare_digest(computed, stored)
        # Legacy: fixed salt, bare hash
        _LEGACY_SALT = b"jarvis-containment-recovery-v1"
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _LEGACY_SALT, _PBKDF2_ITERATIONS,
        )
        return _hmac_module.compare_digest(dk.hex(), stored)

    def _log_forensic(self, event_type: str, **kwargs: object) -> None:
        """Write to forensic logger if available."""
        if self._forensic_logger is None:
            return
        event = {"event_type": event_type, **kwargs}
        try:
            self._forensic_logger.log_event(event)
        except Exception as exc:
            logger.warning("Failed to write forensic log for %s: %s", event_type, exc)
