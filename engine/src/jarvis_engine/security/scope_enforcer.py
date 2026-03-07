"""AI operational boundary enforcement — scope and action gating.

Ensures the AI assistant never exceeds its defined operational scope.
Every action attempt is checked against an allow-list; violations are
logged and counted for audit.  Certain high-impact actions require an
active owner session (escalation).
"""
from __future__ import annotations

import collections
import logging
import threading

from jarvis_engine._shared import now_iso as _now_iso

logger = logging.getLogger(__name__)


class ScopeEnforcer:
    """Gate-keep every AI action against a strict scope/action allow-list.

    Parameters
    ----------
    owner_session_active:
        Whether the device owner's authenticated session is currently
        active.  Required for escalation-gated actions.
    """

    # ------------------------------------------------------------------
    # Class-level policy tables
    # ------------------------------------------------------------------

    ALLOWED_SCOPES: dict[str, set[str]] = {
        "memory": {"read", "write", "search", "delete_own"},
        "knowledge": {"read", "add_fact", "query", "update_fact"},
        "network": {"http_get", "http_post"},
        "filesystem": {"read_data_dir", "write_data_dir"},
        "system": {"get_time", "get_battery", "get_network_status"},
        "notification": {"send_routine", "send_important", "send_urgent"},
        "security": {"read_status", "read_threats", "read_audit"},
    }

    ESCALATION_REQUIRED: set[str] = {
        "notification.send_urgent",
        "security.modify_rules",
        "security.containment_override",
        "system.modify_settings",
        "filesystem.write_outside_sandbox",
    }

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(self, owner_session_active: bool = False) -> None:
        self._owner_session_active = owner_session_active
        self._lock = threading.Lock()
        self._violations: collections.deque[dict] = collections.deque(maxlen=1000)
        self._total_violation_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, scope: str, action: str) -> tuple[bool, str]:
        """Check whether *scope*.*action* is permitted.

        Returns
        -------
        (allowed, message) where *allowed* is ``True`` when the action
        may proceed and *message* is ``"ok"`` on success or a denial
        reason on failure.
        """
        qualified = f"{scope}.{action}"

        # 1. Unknown scope
        if scope not in self.ALLOWED_SCOPES:
            reason = f"Unknown scope: {scope}"
            self._record_violation(scope, action, reason)
            return False, reason

        # 2. Escalation check (before normal allow-list so that
        #    escalation-only actions that are NOT in the base allow-set
        #    still get the correct denial message).
        if qualified in self.ESCALATION_REQUIRED:
            if not self._owner_session_active:
                reason = f"Requires owner authentication: {qualified}"
                self._record_violation(scope, action, reason)
                return False, reason
            # Owner session active — escalation granted.
            return True, "ok"

        # 3. Action not in the allowed set
        allowed_actions = self.ALLOWED_SCOPES[scope]
        if action not in allowed_actions:
            reason = f"Action not permitted: {scope}.{action}"
            self._record_violation(scope, action, reason)
            return False, reason

        # 4. Allowed
        return True, "ok"

    def set_owner_session(self, active: bool) -> None:
        """Update the owner-session flag at runtime."""
        self._owner_session_active = active

    def violation_count(self) -> int:
        """Return the total number of violations recorded this session."""
        with self._lock:
            return self._total_violation_count

    def recent_violations(self, limit: int = 20) -> list[dict]:
        """Return the most recent violations (up to *limit*).

        Entries are in chronological order (oldest first within the
        returned window).
        """
        with self._lock:
            buf = list(self._violations)
        return buf[-limit:] if limit < len(buf) else buf

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_violation(self, scope: str, action: str, reason: str) -> None:
        entry = {
            "timestamp": _now_iso(),
            "scope": scope,
            "action": action,
            "reason": reason,
        }
        logger.warning("Scope violation: %s.%s — %s", scope, action, reason)
        with self._lock:
            self._violations.append(entry)
            self._total_violation_count += 1
