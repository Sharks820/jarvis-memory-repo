"""Tests for ModelRouter -- LLM routing decisions."""

from __future__ import annotations


from jarvis_engine.router import ModelRouter, RouteDecision


def test_high_risk_cloud_burst_enabled() -> None:
    router = ModelRouter(cloud_burst_enabled=True)
    decision = router.route(risk="high", complexity="easy")
    assert decision.provider == "cloud_verifier"
    assert "High-risk" in decision.reason


def test_critical_risk_cloud_burst_enabled() -> None:
    router = ModelRouter(cloud_burst_enabled=True)
    decision = router.route(risk="critical", complexity="easy")
    assert decision.provider == "cloud_verifier"


def test_high_risk_cloud_burst_disabled() -> None:
    router = ModelRouter(cloud_burst_enabled=False)
    decision = router.route(risk="high", complexity="easy")
    assert decision.provider == "local_primary"


def test_complex_task_cloud_burst_enabled() -> None:
    router = ModelRouter(cloud_burst_enabled=True)
    decision = router.route(risk="low", complexity="hard")
    assert decision.provider == "cloud_burst"
    assert "Complex" in decision.reason


def test_very_hard_task_cloud_burst_enabled() -> None:
    router = ModelRouter(cloud_burst_enabled=True)
    decision = router.route(risk="low", complexity="very_hard")
    assert decision.provider == "cloud_burst"


def test_complex_task_cloud_burst_disabled() -> None:
    router = ModelRouter(cloud_burst_enabled=False)
    decision = router.route(risk="low", complexity="hard")
    assert decision.provider == "local_primary"


def test_default_local_routing() -> None:
    router = ModelRouter(cloud_burst_enabled=True)
    decision = router.route(risk="low", complexity="easy")
    assert decision.provider == "local_primary"
    assert "Default" in decision.reason


def test_high_risk_takes_priority_over_complexity() -> None:
    """High risk routes to cloud_verifier even if complexity is also hard."""
    router = ModelRouter(cloud_burst_enabled=True)
    decision = router.route(risk="high", complexity="very_hard")
    assert decision.provider == "cloud_verifier"


def test_route_decision_dataclass() -> None:
    d = RouteDecision(provider="test", reason="test reason")
    assert d.provider == "test"
    assert d.reason == "test reason"
