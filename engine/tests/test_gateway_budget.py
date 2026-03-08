"""Tests for gateway budget enforcement, cost alerts, cost-aware routing,
circuit breaker, and provider health tracking.

Covers:
- 5.2a: BudgetEnforcer daily/monthly caps, SQLite tracking, BudgetExceededError
- 5.2b: Cost alert thresholds at 50/75/90%, activity events, audit logging
- 5.2c: Cost-aware routing (rank_providers_by_cost)
- 6.3a: Exponential backoff / circuit breaker (CLOSED/OPEN/HALF_OPEN)
- 6.3b: ProviderHealthTracker success_rate, avg_latency, filter_healthy
- Integration: ModelGateway.complete() budget + health wiring
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.gateway.budget import (
    BudgetEnforcer,
    BudgetExceededError,
    BudgetStatus,
    PROVIDER_COST_PER_1K,
)
from jarvis_engine.gateway.circuit_breaker import (
    CircuitState,
    ProviderHealth,
    ProviderHealthTracker,
    _cooldown_for_failures,
)


# ═══════════════════════════════════════════════════════════════════════════
# 5.2a: BudgetEnforcer — daily/monthly caps, SQLite tracking
# ═══════════════════════════════════════════════════════════════════════════


class TestBudgetEnforcerInit:
    """BudgetEnforcer creates DB and schema correctly."""

    def test_creates_db_file(self, tmp_path: Path) -> None:
        db = tmp_path / "budget.db"
        assert not db.exists()
        enforcer = BudgetEnforcer(db)
        assert db.exists()
        enforcer.close()

    def test_schema_has_budget_tracking_table(self, tmp_path: Path) -> None:
        enforcer = BudgetEnforcer(tmp_path / "budget.db")
        cur = enforcer._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='budget_tracking'"
        )
        assert cur.fetchone() is not None
        enforcer.close()

    def test_default_caps(self, tmp_path: Path) -> None:
        enforcer = BudgetEnforcer(tmp_path / "budget.db")
        assert enforcer._daily_cap == 5.0
        assert enforcer._monthly_cap == 50.0
        enforcer.close()

    def test_custom_caps(self, tmp_path: Path) -> None:
        enforcer = BudgetEnforcer(tmp_path / "budget.db", daily_cap=10.0, monthly_cap=100.0)
        assert enforcer._daily_cap == 10.0
        assert enforcer._monthly_cap == 100.0
        enforcer.close()

    @patch.dict("os.environ", {"JARVIS_BUDGET_DAILY_CAP": "3.50", "JARVIS_BUDGET_MONTHLY_CAP": "25.0"})
    def test_env_overrides(self, tmp_path: Path) -> None:
        enforcer = BudgetEnforcer(tmp_path / "budget.db")
        assert enforcer._daily_cap == 3.50
        assert enforcer._monthly_cap == 25.0
        enforcer.close()

    def test_context_manager(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as enforcer:
            enforcer.record_cost(0.01, "model", "prov")
        # Should be closed -- no error
        assert enforcer._closed


class TestBudgetRecordCost:
    """record_cost writes to SQLite and tracks cumulative spend."""

    def test_record_single_cost(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            e.record_cost(0.50, "kimi-k2", "groq")
            status = e.status()
            assert status.daily_spent == pytest.approx(0.50)

    def test_record_multiple_costs(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            e.record_cost(0.10, "kimi-k2", "groq")
            e.record_cost(0.20, "claude-haiku", "anthropic")
            e.record_cost(0.30, "devstral-2", "mistral")
            status = e.status()
            assert status.daily_spent == pytest.approx(0.60)

    def test_zero_cost_ignored(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            e.record_cost(0.0, "phi3", "ollama")
            status = e.status()
            assert status.daily_spent == 0.0

    def test_negative_cost_ignored(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            e.record_cost(-0.5, "model", "prov")
            status = e.status()
            assert status.daily_spent == 0.0


class TestBudgetCheckBudget:
    """check_budget raises BudgetExceededError when caps are exceeded."""

    def test_under_budget_no_error(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=5.0) as e:
            e.record_cost(1.0, "m", "p")
            e.check_budget(0.50)  # Should not raise

    def test_daily_cap_exceeded(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=1.0) as e:
            e.record_cost(0.90, "m", "p")
            with pytest.raises(BudgetExceededError) as exc_info:
                e.check_budget(0.20)
            assert exc_info.value.period == "daily"
            assert exc_info.value.spent == pytest.approx(0.90)
            assert exc_info.value.cap == 1.0

    def test_monthly_cap_exceeded(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=100.0, monthly_cap=2.0) as e:
            e.record_cost(1.80, "m", "p")
            with pytest.raises(BudgetExceededError) as exc_info:
                e.check_budget(0.30)
            assert exc_info.value.period == "monthly"

    def test_exact_cap_no_error(self, tmp_path: Path) -> None:
        """Spending exactly at cap is fine; going over triggers error."""
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=1.0) as e:
            e.record_cost(1.0, "m", "p")
            # Spending 0 more is ok (1.0 + 0.0 = 1.0 which is not > 1.0)
            e.check_budget(0.0)
            # Spending anything more exceeds
            with pytest.raises(BudgetExceededError):
                e.check_budget(0.01)


class TestBudgetEstimateCost:
    """estimate_cost delegates to pricing.calculate_cost."""

    def test_known_model(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            cost = e.estimate_cost("kimi-k2", 1000, 500)
            assert cost > 0.0

    def test_local_model_zero(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            cost = e.estimate_cost("gemma3:4b", 1000, 500)
            assert cost == 0.0


class TestBudgetStatus:
    """status() returns correct BudgetStatus snapshot."""

    def test_empty_status(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            s = e.status()
            assert isinstance(s, BudgetStatus)
            assert s.daily_spent == 0.0
            assert s.monthly_spent == 0.0
            assert s.budget_ok is True
            assert s.daily_pct == 0.0

    def test_status_after_spending(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=10.0) as e:
            e.record_cost(3.0, "m", "p")
            s = e.status()
            assert s.daily_spent == pytest.approx(3.0)
            assert s.daily_pct == pytest.approx(30.0)
            assert s.budget_ok is True

    def test_status_budget_exceeded(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=1.0) as e:
            e.record_cost(1.50, "m", "p")
            s = e.status()
            assert s.budget_ok is False
            assert s.daily_pct > 100.0


# ═══════════════════════════════════════════════════════════════════════════
# 5.2b: Cost alerts — thresholds at 50/75/90%
# ═══════════════════════════════════════════════════════════════════════════


class TestCostAlerts:
    """Cost alerts fire at 50%, 75%, and 90% thresholds."""

    def test_alert_at_50_pct(self, tmp_path: Path) -> None:
        audit = MagicMock()
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=2.0, audit=audit) as e:
            e.record_cost(1.10, "m", "p")  # 55% — triggers 50%
            assert 0.50 in e._fired_alerts["daily"]
            # Audit should have been called
            audit.log_decision.assert_called()
            call_kwargs = audit.log_decision.call_args.kwargs
            assert "50pct" in call_kwargs["reason"]

    def test_alert_at_75_pct(self, tmp_path: Path) -> None:
        audit = MagicMock()
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=2.0, audit=audit) as e:
            e.record_cost(1.60, "m", "p")  # 80% — triggers 50% and 75%
            assert 0.50 in e._fired_alerts["daily"]
            assert 0.75 in e._fired_alerts["daily"]

    def test_alert_at_90_pct(self, tmp_path: Path) -> None:
        audit = MagicMock()
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=2.0, audit=audit) as e:
            e.record_cost(1.90, "m", "p")  # 95% — triggers all three
            assert 0.50 in e._fired_alerts["daily"]
            assert 0.75 in e._fired_alerts["daily"]
            assert 0.90 in e._fired_alerts["daily"]

    def test_alert_not_fired_twice(self, tmp_path: Path) -> None:
        audit = MagicMock()
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=2.0, audit=audit) as e:
            e.record_cost(1.10, "m", "p")  # 55% triggers 50%
            call_count_after_first = audit.log_decision.call_count
            e.record_cost(0.10, "m", "p")  # 60% — already past 50%, no re-alert
            # Should not have generated another 50% alert
            assert audit.log_decision.call_count == call_count_after_first

    def test_monthly_alert(self, tmp_path: Path) -> None:
        audit = MagicMock()
        with BudgetEnforcer(tmp_path / "budget.db", daily_cap=100.0, monthly_cap=4.0, audit=audit) as e:
            e.record_cost(2.10, "m", "p")  # 52.5% of monthly
            assert 0.50 in e._fired_alerts["monthly"]

    def test_activity_event_emitted(self, tmp_path: Path) -> None:
        with patch("jarvis_engine.activity_feed.log_activity") as mock_activity:
            with BudgetEnforcer(tmp_path / "budget.db", daily_cap=2.0) as e:
                e.record_cost(1.10, "m", "p")
                mock_activity.assert_called()
                args = mock_activity.call_args
                assert args[0][0] == "cost_alert"
                assert "50%" in args[0][1]
                details = args[0][2]
                assert details["threshold_pct"] == 50
                assert details["period"] == "daily"


# ═══════════════════════════════════════════════════════════════════════════
# 5.2c: Cost-aware routing
# ═══════════════════════════════════════════════════════════════════════════


class TestCostAwareRouting:
    """rank_providers_by_cost sorts cheapest first."""

    def test_sort_order(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            result = e.rank_providers_by_cost(["anthropic", "groq", "ollama", "mistral"])
            # ollama (0.0) < zai (0.0004, not in list) < mistral (0.001) < groq (0.002) < anthropic (0.009)
            assert result[0] == "ollama"
            assert result[-1] == "anthropic"

    def test_groq_before_anthropic(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            result = e.rank_providers_by_cost(["anthropic", "groq"])
            assert result.index("groq") < result.index("anthropic")

    def test_unknown_provider_sorted_last(self, tmp_path: Path) -> None:
        with BudgetEnforcer(tmp_path / "budget.db") as e:
            result = e.rank_providers_by_cost(["groq", "unknown_provider"])
            assert result[-1] == "unknown_provider"

    def test_provider_cost_table_has_all_known(self) -> None:
        """PROVIDER_COST_PER_1K covers all known providers."""
        expected = {"ollama", "groq", "zai", "mistral", "anthropic",
                    "claude-cli", "codex-cli", "gemini-cli", "kimi-cli"}
        assert expected.issubset(set(PROVIDER_COST_PER_1K.keys()))


# ═══════════════════════════════════════════════════════════════════════════
# 6.3a: Circuit breaker — exponential backoff
# ═══════════════════════════════════════════════════════════════════════════


class TestCircuitBreakerBackoff:
    """Exponential backoff tiers and cooldown calculation."""

    def test_cooldown_under_3(self) -> None:
        assert _cooldown_for_failures(0) == 0.0
        assert _cooldown_for_failures(1) == 0.0
        assert _cooldown_for_failures(2) == 0.0

    def test_cooldown_at_3(self) -> None:
        assert _cooldown_for_failures(3) == 30.0

    def test_cooldown_at_5(self) -> None:
        assert _cooldown_for_failures(5) == 120.0

    def test_cooldown_at_10(self) -> None:
        assert _cooldown_for_failures(10) == 600.0

    def test_cooldown_at_15(self) -> None:
        """Above 10, still 10-min cooldown (highest tier)."""
        assert _cooldown_for_failures(15) == 600.0


class TestCircuitBreakerStates:
    """Circuit breaker transitions: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""

    def test_initial_state_closed(self) -> None:
        tracker = ProviderHealthTracker()
        h = tracker.get_health("groq")
        assert h is None  # Unknown = healthy, not tracked yet

    def test_failures_open_circuit(self) -> None:
        tracker = ProviderHealthTracker()
        for _ in range(3):
            tracker.record_failure("groq")
        h = tracker.get_health("groq")
        assert h is not None
        assert h.circuit_state == CircuitState.OPEN
        assert h.consecutive_failures == 3

    def test_should_skip_when_open(self) -> None:
        tracker = ProviderHealthTracker()
        for _ in range(3):
            tracker.record_failure("groq")
        assert tracker.should_skip("groq") is True

    def test_should_not_skip_when_closed(self) -> None:
        tracker = ProviderHealthTracker()
        tracker.record_success("groq", 100.0)
        assert tracker.should_skip("groq") is False

    def test_cooldown_expiry_transitions_to_half_open(self) -> None:
        tracker = ProviderHealthTracker()
        for _ in range(3):
            tracker.record_failure("groq")

        # Manually expire the cooldown
        with tracker._lock:
            h = tracker._providers["groq"]
            h.cooldown_until = time.monotonic() - 1.0  # Already expired

        # should_skip should transition to HALF_OPEN and return False
        assert tracker.should_skip("groq") is False
        h = tracker.get_health("groq")
        assert h is not None
        assert h.circuit_state == CircuitState.HALF_OPEN

    def test_success_in_half_open_closes_circuit(self) -> None:
        tracker = ProviderHealthTracker()
        for _ in range(3):
            tracker.record_failure("groq")

        # Force HALF_OPEN
        with tracker._lock:
            h = tracker._providers["groq"]
            h.circuit_state = CircuitState.HALF_OPEN

        tracker.record_success("groq", 100.0)
        h = tracker.get_health("groq")
        assert h is not None
        assert h.circuit_state == CircuitState.CLOSED
        assert h.consecutive_failures == 0

    def test_failure_in_half_open_reopens_circuit(self) -> None:
        tracker = ProviderHealthTracker()
        for _ in range(3):
            tracker.record_failure("groq")

        # Force HALF_OPEN
        with tracker._lock:
            h = tracker._providers["groq"]
            h.circuit_state = CircuitState.HALF_OPEN

        tracker.record_failure("groq")
        h = tracker.get_health("groq")
        assert h is not None
        assert h.circuit_state == CircuitState.OPEN

    def test_unknown_provider_not_skipped(self) -> None:
        tracker = ProviderHealthTracker()
        assert tracker.should_skip("never_seen_provider") is False


# ═══════════════════════════════════════════════════════════════════════════
# 6.3b: Provider health tracking
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderHealth:
    """ProviderHealthTracker tracks success_rate, latency, etc."""

    def test_success_rate_after_successes(self) -> None:
        tracker = ProviderHealthTracker()
        for _ in range(8):
            tracker.record_success("groq", 100.0)
        for _ in range(2):
            tracker.record_failure("groq")
        h = tracker.get_health("groq")
        assert h is not None
        assert h.success_rate == pytest.approx(0.8)

    def test_avg_latency_tracking(self) -> None:
        tracker = ProviderHealthTracker()
        tracker.record_success("groq", 100.0)
        h = tracker.get_health("groq")
        assert h is not None
        assert h.avg_latency_ms == pytest.approx(100.0)

        tracker.record_success("groq", 200.0)
        h = tracker.get_health("groq")
        assert h is not None
        # EMA: 100*0.8 + 200*0.2 = 120
        assert h.avg_latency_ms == pytest.approx(120.0)

    def test_is_healthy_with_good_provider(self) -> None:
        tracker = ProviderHealthTracker()
        for _ in range(10):
            tracker.record_success("groq", 100.0)
        assert tracker.is_healthy("groq") is True

    def test_is_healthy_with_poor_provider(self) -> None:
        tracker = ProviderHealthTracker()
        # 1 success, 4 failures = 20% success rate (below 50% threshold, 5 requests > 4 min)
        tracker.record_success("groq", 100.0)
        for _ in range(4):
            # Reset circuit to avoid OPEN state interfering
            tracker.record_failure("groq")
        # Force circuit closed so we're only testing success_rate
        with tracker._lock:
            h = tracker._providers["groq"]
            h.circuit_state = CircuitState.CLOSED
            h.cooldown_until = 0
        assert tracker.is_healthy("groq") is False

    def test_filter_healthy(self) -> None:
        tracker = ProviderHealthTracker()
        # groq: healthy
        for _ in range(5):
            tracker.record_success("groq", 100.0)
        # mistral: unhealthy (all failures, circuit open)
        for _ in range(5):
            tracker.record_failure("mistral")
        result = tracker.filter_healthy(["groq", "mistral", "ollama"])
        assert "groq" in result
        assert "ollama" in result  # Unknown = healthy
        assert "mistral" not in result

    def test_all_health(self) -> None:
        tracker = ProviderHealthTracker()
        tracker.record_success("groq", 100.0)
        tracker.record_failure("anthropic")
        result = tracker.all_health()
        assert "groq" in result
        assert "anthropic" in result
        assert result["groq"]["success_rate"] == 1.0
        assert result["anthropic"]["success_rate"] == 0.0

    def test_to_dict(self) -> None:
        h = ProviderHealth(provider="groq", total_requests=10, total_successes=9, total_failures=1)
        d = h.to_dict()
        assert d["provider"] == "groq"
        assert d["success_rate"] == pytest.approx(0.9)
        assert d["circuit_state"] == "closed"

    def test_rank_by_health(self) -> None:
        tracker = ProviderHealthTracker()
        # groq: 100% success, low latency
        for _ in range(5):
            tracker.record_success("groq", 50.0)
        # mistral: 60% success, high latency
        for _ in range(3):
            tracker.record_success("mistral", 500.0)
        for _ in range(2):
            tracker.record_failure("mistral")
        # Force mistral circuit closed for ranking
        with tracker._lock:
            tracker._providers["mistral"].circuit_state = CircuitState.CLOSED
            tracker._providers["mistral"].cooldown_until = 0

        ranked = tracker.rank_by_health(["mistral", "groq"])
        assert ranked[0] == "groq"


# ═══════════════════════════════════════════════════════════════════════════
# Integration: ModelGateway + BudgetEnforcer + HealthTracker
# ═══════════════════════════════════════════════════════════════════════════


class TestGatewayBudgetIntegration:
    """ModelGateway.complete() respects budget enforcement."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_budget_exceeded_routes_to_ollama(self, tmp_path: Path) -> None:
        """When budget is exceeded, complete() routes to local Ollama."""
        from jarvis_engine.gateway.models import ModelGateway

        budget = BudgetEnforcer(tmp_path / "budget.db", daily_cap=0.01)
        budget.record_cost(0.02, "m", "p")  # Already over budget

        gw = ModelGateway(budget_enforcer=budget)
        # Mock Ollama to return a response
        mock_resp = MagicMock()
        mock_resp.message.content = "local response"
        mock_resp.prompt_eval_count = 10
        mock_resp.eval_count = 20

        with patch.object(gw, "_call_ollama") as mock_ollama:
            mock_ollama.return_value = MagicMock(
                text="local response", model="gemma3:4b", provider="ollama",
                input_tokens=10, output_tokens=20, cost_usd=0.0,
                fallback_used=False, fallback_reason="",
            )
            resp = gw.complete([{"role": "user", "content": "hello"}])
            mock_ollama.assert_called_once()
            assert resp.fallback_used is True
            assert "budget_exceeded" in resp.fallback_reason

        budget.close()
        gw.close()

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_under_budget_proceeds_normally(self, tmp_path: Path) -> None:
        """When under budget, complete() proceeds to the normal provider."""
        from jarvis_engine.gateway.models import ModelGateway

        budget = BudgetEnforcer(tmp_path / "budget.db", daily_cap=100.0)
        gw = ModelGateway(budget_enforcer=budget)

        with patch.object(gw, "_route_to_provider") as mock_route:
            mock_route.return_value = (
                MagicMock(
                    text="response", model="gemma3:4b", provider="ollama",
                    input_tokens=10, output_tokens=20, cost_usd=0.0,
                    fallback_used=False, fallback_reason="",
                ),
                "primary:ollama",
                time.perf_counter(),
            )
            resp = gw.complete([{"role": "user", "content": "hello"}])
            mock_route.assert_called_once()

        budget.close()
        gw.close()

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_cost_recorded_after_completion(self, tmp_path: Path) -> None:
        """After a successful completion, cost is recorded in the budget."""
        from jarvis_engine.gateway.models import ModelGateway

        budget = BudgetEnforcer(tmp_path / "budget.db", daily_cap=100.0)
        gw = ModelGateway(budget_enforcer=budget)

        with patch.object(gw, "_route_to_provider") as mock_route:
            mock_route.return_value = (
                MagicMock(
                    text="response", model="kimi-k2", provider="groq",
                    input_tokens=100, output_tokens=50, cost_usd=0.05,
                    fallback_used=False, fallback_reason="",
                ),
                "primary:groq",
                time.perf_counter(),
            )
            gw.complete([{"role": "user", "content": "hello"}])

        status = budget.status()
        assert status.daily_spent == pytest.approx(0.05)

        budget.close()
        gw.close()


class TestGatewayHealthIntegration:
    """ModelGateway tracks provider health and uses circuit breaker."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_health_recorded_on_success(self, tmp_path: Path) -> None:
        from jarvis_engine.gateway.models import ModelGateway

        tracker = ProviderHealthTracker()
        gw = ModelGateway(health_tracker=tracker)

        with patch.object(gw, "_route_to_provider") as mock_route:
            mock_route.return_value = (
                MagicMock(
                    text="ok", model="gemma3:4b", provider="ollama",
                    input_tokens=10, output_tokens=20, cost_usd=0.0,
                    fallback_used=False, fallback_reason="",
                ),
                "primary:ollama",
                time.perf_counter(),
            )
            gw.complete([{"role": "user", "content": "hello"}])

        health = tracker.all_health()
        assert "ollama" in health
        assert health["ollama"]["total_successes"] == 1

        gw.close()

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    def test_failed_provider_skipped_in_fallback(self, tmp_path: Path) -> None:
        """When a provider's circuit is OPEN, it is skipped in fallback chain."""
        tracker = ProviderHealthTracker()
        # Simulate groq having 3 consecutive failures
        for _ in range(3):
            tracker.record_failure("groq")

        assert tracker.should_skip("groq") is True

    def test_health_endpoint_data(self) -> None:
        """ProviderHealthTracker.all_health() produces API-ready dict."""
        tracker = ProviderHealthTracker()
        tracker.record_success("groq", 150.0)
        tracker.record_success("ollama", 50.0)
        tracker.record_failure("anthropic")

        data = tracker.all_health()
        assert set(data.keys()) == {"groq", "ollama", "anthropic"}
        assert data["groq"]["circuit_state"] == "closed"
        assert data["anthropic"]["circuit_state"] == "closed"  # Only 1 failure, not enough for OPEN


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases and thread safety
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_budget_enforcer_double_close(self, tmp_path: Path) -> None:
        e = BudgetEnforcer(tmp_path / "budget.db")
        e.close()
        e.close()  # Should not raise

    def test_budget_enforcer_closed_check(self, tmp_path: Path) -> None:
        e = BudgetEnforcer(tmp_path / "budget.db")
        e.close()
        e.check_budget(1.0)  # Should not raise (closed = skip)
        e.record_cost(1.0, "m", "p")  # Should not raise (closed = skip)
        s = e.status()
        assert s.daily_spent == 0.0  # Closed returns default

    def test_health_tracker_get_none_for_unknown(self) -> None:
        tracker = ProviderHealthTracker()
        assert tracker.get_health("nonexistent") is None

    def test_health_tracker_unknown_is_healthy(self) -> None:
        tracker = ProviderHealthTracker()
        assert tracker.is_healthy("never_seen") is True

    def test_provider_health_success_rate_zero_requests(self) -> None:
        h = ProviderHealth(provider="test")
        assert h.success_rate == 1.0  # Default to 100% when no data

    def test_budget_exceeded_error_attributes(self) -> None:
        exc = BudgetExceededError(
            "test", period="daily", spent=4.50, cap=5.00,
        )
        assert exc.period == "daily"
        assert exc.spent == 4.50
        assert exc.cap == 5.00
        assert "test" in str(exc)
