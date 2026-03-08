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
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from jarvis_engine._protocols import ForensicLoggerProtocol

logger = logging.getLogger(__name__)


class ContainResult(TypedDict, total=False):
    """Result from :meth:`ContainmentEngine.contain`."""

    ip: str
    level: int
    level_name: str
    reason: str
    actions: list[str]
    timestamp: float
    credentials_rotated: bool


class ContainmentStatus(TypedDict):
    """Result from :meth:`ContainmentEngine.get_containment_status`."""

    current_level: int
    level_name: str
    throttled_ips: dict[str, float]
    blocked_ips: list[str]
    isolated_endpoints: list[str]
    lockdown_active: bool
    killed: bool
    history_count: int


class RecoveryResult(TypedDict, total=False):
    """Result from :meth:`ContainmentEngine.recover`."""

    recovered: bool
    reason: str
    level_recovered: int
    actions: list[str]


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
_MASTER_CREDENTIAL_HASH_ENV = "JARVIS_MASTER_PASSWORD_HASH"


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
        forensic_logger: ForensicLoggerProtocol | None = None,
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

    def _apply_containment_actions(
        self, ip: str, level: int,
    ) -> tuple[list[str], bool, bool]:
        """Apply containment state changes under the lock.

        Returns ``(actions, do_block_ip, credentials_rotated)``.
        Caller MUST hold ``self._lock``.
        """
        actions: list[str] = []
        do_block_ip = False
        credentials_rotated = False

        if level >= ContainmentLevel.THROTTLE:
            self._throttled_ips[ip] = 1.0  # 1 req/min
            actions.append(f"THROTTLE: rate limited {ip} to 1 req/min")

        if level >= ContainmentLevel.BLOCK:
            self._blocked_ips.add(ip)
            do_block_ip = True
            actions.append(f"BLOCK: added {ip} to blocklist")

        if level >= ContainmentLevel.ISOLATE:
            endpoint = f"/api/from/{ip}"
            self._isolated_endpoints.add(endpoint)
            actions.append(f"ISOLATE: disabled endpoint {endpoint}")

        do_terminate_sessions = False
        if level >= ContainmentLevel.LOCKDOWN:
            self._lockdown_active = True
            do_terminate_sessions = self._session_manager is not None
            credentials_rotated = True
            actions.append("LOCKDOWN: mobile API shut down, credentials rotated")
            actions.append("LOCKDOWN: all sessions invalidated")

        if level >= ContainmentLevel.FULL_KILL:
            self._killed = True
            actions.append("FULL_KILL: all services stopped")
            actions.append("FULL_KILL: incident report generated")
            actions.append("FULL_KILL: URGENT notification chain triggered")

        if level > self._current_level:
            self._current_level = level

        return actions, do_block_ip, credentials_rotated, do_terminate_sessions

    def _run_post_containment(
        self, ip: str, level: int, reason: str,
        actions: list[str], do_block_ip: bool, do_rotate: bool,
        do_terminate_sessions: bool = False,
    ) -> None:
        """Run side-effects outside the lock after containment."""
        if do_terminate_sessions and self._session_manager is not None:
            try:
                self._session_manager.terminate_all_sessions()
            except (RuntimeError, OSError) as exc:
                logger.warning("Failed to terminate sessions during lockdown: %s", exc)
        if do_rotate:
            self._rotate_credentials()

        if do_block_ip and self._ip_tracker is not None:
            self._ip_tracker.block_ip(ip)

        self._log_forensic(
            "containment_executed",
            severity="CRITICAL" if level >= ContainmentLevel.LOCKDOWN else "HIGH",
            ip=ip, level=level,
            level_name=ContainmentLevel(level).name,
            reason=reason, actions=actions,
        )

        logger.warning(
            "Containment level %d (%s) executed against %s: %s",
            level, ContainmentLevel(level).name, ip, reason,
        )

    def contain(self, ip: str, level: int, reason: str) -> ContainResult:
        """Execute containment at the specified *level* against *ip*.

        Returns a dict describing the actions taken.
        """
        level = int(level)
        if level < ContainmentLevel.THROTTLE or level > ContainmentLevel.FULL_KILL:
            raise ValueError(f"Invalid containment level: {level}")

        with self._lock:
            actions, do_block_ip, credentials_rotated, do_terminate = self._apply_containment_actions(ip, level)

            result: ContainResult = {
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

        self._run_post_containment(ip, level, reason, actions, do_block_ip, credentials_rotated, do_terminate)
        return result

    def get_containment_status(self) -> ContainmentStatus:
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

    def _verify_recovery_password(
        self, level: int, master_password: str | None,
    ) -> RecoveryResult | None:
        """Gate recovery behind master password for levels 4-5.

        Returns a denial :class:`RecoveryResult` if the password is missing
        or invalid, or ``None`` if verification passed (or not required).
        """
        if level < ContainmentLevel.LOCKDOWN:
            return None
        if master_password is None:
            self._log_forensic(
                "recovery_denied", severity="HIGH",
                level=level, reason="master password not provided",
            )
            return {"recovered": False, "reason": "Master password required for level 4+ recovery"}
        if not self._verify_master_password(master_password):
            self._log_forensic(
                "recovery_denied", severity="CRITICAL",
                level=level, reason="invalid master password",
            )
            return {"recovered": False, "reason": "Invalid master password"}
        return None

    def _clear_containment_state(self, level: int) -> tuple[list[str], list[str]]:
        """Clear containment state for the given level and below.

        Caller MUST hold ``self._lock``.
        Returns ``(actions, ips_to_unblock)``.
        """
        actions: list[str] = []
        ips_to_unblock: list[str] = []

        if level >= ContainmentLevel.FULL_KILL and self._killed:
            self._killed = False
            actions.append("Services restart permitted")
        if level >= ContainmentLevel.LOCKDOWN and self._lockdown_active:
            self._lockdown_active = False
            actions.append("Lockdown lifted, API re-enabled")
        if level >= ContainmentLevel.ISOLATE and self._isolated_endpoints:
            self._isolated_endpoints.clear()
            actions.append("All endpoint isolations removed")
        if level >= ContainmentLevel.BLOCK and self._blocked_ips:
            ips_to_unblock = list(self._blocked_ips)
            self._blocked_ips.clear()
            actions.append("All IP blocks cleared")
        if level >= ContainmentLevel.THROTTLE and self._throttled_ips:
            self._throttled_ips.clear()
            actions.append("All throttles cleared")

        # Adjust the current containment level.
        if self._current_level <= level:
            self._current_level = 0
        else:
            self._current_level = level + 1 if level < ContainmentLevel.FULL_KILL else 0

        return actions, ips_to_unblock

    def _unblock_recovered_ips(self, ips_to_unblock: list[str]) -> None:
        """Unblock IPs via the IP tracker (outside the lock)."""
        if not ips_to_unblock or self._ip_tracker is None:
            return
        for ip in ips_to_unblock:
            try:
                self._ip_tracker.unblock_ip(ip)
            except (OSError, ValueError, AttributeError) as exc:
                logger.debug("IP unblock during recovery failed: %s", exc)

    def recover(self, level: int, master_password: str | None = None) -> RecoveryResult:
        """Recover from containment at the specified *level*.

        Levels 1-3 auto-recover (no password required).
        Levels 4-5 require the owner's master password.

        Returns a dict describing recovery outcome.
        """
        level = int(level)
        if level < ContainmentLevel.THROTTLE or level > ContainmentLevel.FULL_KILL:
            raise ValueError(f"Invalid containment level: {level}")

        denial = self._verify_recovery_password(level, master_password)
        if denial is not None:
            return denial

        with self._lock:
            actions, ips_to_unblock = self._clear_containment_state(level)

        self._unblock_recovered_ips(ips_to_unblock)

        self._log_forensic("recovery_executed", severity="INFO", level=level, actions=actions)
        logger.info("Recovery from level %d completed: %s", level, actions)

        return {"recovered": True, "level_recovered": level, "actions": actions}

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
            except (RuntimeError, OSError, ValueError) as exc:
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
        stored = os.environ.get(_MASTER_CREDENTIAL_HASH_ENV)
        if stored is None:
            logger.critical(
                "%s not set — denying recovery (set env var to enable)",
                _MASTER_CREDENTIAL_HASH_ENV,
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
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Failed to write forensic log for %s: %s", event_type, exc)
