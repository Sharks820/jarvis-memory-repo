"""Tests for CapabilityGate -- tiered authorization model."""

from __future__ import annotations

from jarvis_engine.capability import CapabilityGate, CapabilityDecision


def test_read_action_always_allowed() -> None:
    gate = CapabilityGate()
    result = gate.authorize("read", has_explicit_approval=False, task_requires_expansion=False)
    assert result.allowed is True


def test_bounded_write_allowed_without_approval() -> None:
    gate = CapabilityGate()
    result = gate.authorize("bounded_write", has_explicit_approval=False, task_requires_expansion=False)
    assert result.allowed is True


def test_privileged_denied_without_approval() -> None:
    gate = CapabilityGate()
    result = gate.authorize("privileged", has_explicit_approval=False, task_requires_expansion=False)
    assert result.allowed is False
    assert "denied" in result.reason.lower()


def test_privileged_allowed_with_explicit_approval() -> None:
    gate = CapabilityGate()
    result = gate.authorize("privileged", has_explicit_approval=True, task_requires_expansion=False)
    assert result.allowed is True
    assert "approved" in result.reason.lower()


def test_privileged_denied_with_expansion_but_no_approval() -> None:
    gate = CapabilityGate()
    result = gate.authorize("privileged", has_explicit_approval=False, task_requires_expansion=True)
    assert result.allowed is False
    assert "expansion" in result.reason.lower()


def test_unknown_action_class_denied() -> None:
    gate = CapabilityGate()
    result = gate.authorize("admin_override", has_explicit_approval=True, task_requires_expansion=False)
    assert result.allowed is False
    assert "Unknown" in result.reason


def test_capability_decision_dataclass() -> None:
    d = CapabilityDecision(allowed=True, reason="test")
    assert d.allowed is True
    assert d.reason == "test"
