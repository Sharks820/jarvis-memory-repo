"""Tests for ScopeEnforcer — AI operational boundary enforcement."""

from __future__ import annotations

import threading


from jarvis_engine.security.scope_enforcer import ScopeEnforcer


# ------------------------------------------------------------------
# 1. Allowed action passes
# ------------------------------------------------------------------


class TestAllowedAction:
    def test_allowed_action(self) -> None:
        enforcer = ScopeEnforcer()
        allowed, msg = enforcer.check("memory", "read")
        assert allowed is True
        assert msg == "ok"

    def test_allowed_action_write(self) -> None:
        enforcer = ScopeEnforcer()
        allowed, msg = enforcer.check("knowledge", "add_fact")
        assert allowed is True
        assert msg == "ok"


# ------------------------------------------------------------------
# 2. Unknown scope blocked
# ------------------------------------------------------------------


class TestBlockedUnknownScope:
    def test_blocked_unknown_scope(self) -> None:
        enforcer = ScopeEnforcer()
        allowed, msg = enforcer.check("nuclear_launch", "fire")
        assert allowed is False
        assert "Unknown scope" in msg
        assert "nuclear_launch" in msg


# ------------------------------------------------------------------
# 3. Unknown action blocked
# ------------------------------------------------------------------


class TestBlockedUnknownAction:
    def test_blocked_unknown_action(self) -> None:
        enforcer = ScopeEnforcer()
        allowed, msg = enforcer.check("memory", "destroy_all")
        assert allowed is False
        assert "Action not permitted" in msg
        assert "destroy_all" in msg


# ------------------------------------------------------------------
# 4. Escalation required — no owner session
# ------------------------------------------------------------------


class TestEscalationNoSession:
    def test_escalation_required_no_session(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=False)
        allowed, msg = enforcer.check("notification", "send_urgent")
        assert allowed is False
        assert "Requires owner authentication" in msg

    def test_escalation_security_modify_rules(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=False)
        allowed, msg = enforcer.check("security", "modify_rules")
        assert allowed is False
        assert "Requires owner authentication" in msg

    def test_escalation_system_modify_settings(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=False)
        allowed, msg = enforcer.check("system", "modify_settings")
        assert allowed is False
        assert "Requires owner authentication" in msg

    def test_escalation_filesystem_write_outside_sandbox(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=False)
        allowed, msg = enforcer.check("filesystem", "write_outside_sandbox")
        assert allowed is False
        assert "Requires owner authentication" in msg

    def test_escalation_containment_override(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=False)
        allowed, msg = enforcer.check("security", "containment_override")
        assert allowed is False
        assert "Requires owner authentication" in msg


# ------------------------------------------------------------------
# 5. Escalation allowed with owner session
# ------------------------------------------------------------------


class TestEscalationWithSession:
    def test_escalation_allowed_with_session(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=True)
        allowed, msg = enforcer.check("notification", "send_urgent")
        assert allowed is True
        assert msg == "ok"

    def test_set_owner_session_enables_escalation(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=False)
        # Initially blocked
        allowed, _ = enforcer.check("notification", "send_urgent")
        assert allowed is False

        # Enable session
        enforcer.set_owner_session(True)
        allowed, msg = enforcer.check("notification", "send_urgent")
        assert allowed is True
        assert msg == "ok"

    def test_set_owner_session_disables_escalation(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=True)
        # Initially allowed
        allowed, _ = enforcer.check("notification", "send_urgent")
        assert allowed is True

        # Disable session
        enforcer.set_owner_session(False)
        allowed, msg = enforcer.check("notification", "send_urgent")
        assert allowed is False
        assert "Requires owner authentication" in msg


# ------------------------------------------------------------------
# 6. Violation logging
# ------------------------------------------------------------------


class TestViolationLogging:
    def test_violation_count_increases(self) -> None:
        enforcer = ScopeEnforcer()
        assert enforcer.violation_count() == 0

        enforcer.check("nuclear_launch", "fire")
        assert enforcer.violation_count() == 1

        enforcer.check("memory", "destroy_all")
        assert enforcer.violation_count() == 2

    def test_allowed_action_no_violation(self) -> None:
        enforcer = ScopeEnforcer()
        enforcer.check("memory", "read")
        assert enforcer.violation_count() == 0

    def test_recent_violations_content(self) -> None:
        enforcer = ScopeEnforcer()
        enforcer.check("nuclear_launch", "fire")
        enforcer.check("memory", "destroy_all")

        violations = enforcer.recent_violations()
        assert len(violations) == 2
        assert violations[0]["scope"] == "nuclear_launch"
        assert violations[0]["action"] == "fire"
        assert "reason" in violations[0]
        assert "timestamp" in violations[0]
        assert violations[1]["scope"] == "memory"
        assert violations[1]["action"] == "destroy_all"

    def test_recent_violations_limit(self) -> None:
        enforcer = ScopeEnforcer()
        for i in range(5):
            enforcer.check(f"scope_{i}", "bad_action")
        violations = enforcer.recent_violations(limit=3)
        assert len(violations) == 3
        # Should be the most recent 3
        assert violations[0]["scope"] == "scope_2"
        assert violations[2]["scope"] == "scope_4"

    def test_escalation_violation_is_recorded(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=False)
        enforcer.check("notification", "send_urgent")
        assert enforcer.violation_count() == 1
        violations = enforcer.recent_violations()
        assert "Requires owner authentication" in violations[0]["reason"]


# ------------------------------------------------------------------
# 7. All allowed scopes work
# ------------------------------------------------------------------


class TestAllAllowedScopes:
    def test_all_allowed_scopes_work(self) -> None:
        enforcer = ScopeEnforcer(owner_session_active=True)
        for scope, actions in ScopeEnforcer.ALLOWED_SCOPES.items():
            for action in actions:
                allowed, msg = enforcer.check(scope, action)
                assert allowed is True, (
                    f"{scope}.{action} should be allowed but got: {msg}"
                )
                assert msg == "ok"


# ------------------------------------------------------------------
# 8. Thread safety
# ------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_violations(self) -> None:
        enforcer = ScopeEnforcer()
        errors: list[str] = []

        def violate(n: int) -> None:
            try:
                for _ in range(50):
                    enforcer.check(f"bad_scope_{n}", "bad_action")
            except (RuntimeError, ValueError, AttributeError) as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=violate, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        assert enforcer.violation_count() == 500
