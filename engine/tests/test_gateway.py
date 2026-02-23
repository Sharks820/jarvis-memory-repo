"""Tests for the gateway package: ModelGateway, CostTracker, pricing."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from anthropic import APIConnectionError

from jarvis_engine.gateway.costs import CostTracker
from jarvis_engine.gateway.models import GatewayResponse, ModelGateway
from jarvis_engine.gateway.pricing import PRICING, calculate_cost


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cost_tracker(tmp_path: Path) -> CostTracker:
    """Create a CostTracker with a temporary SQLite database."""
    tracker = CostTracker(tmp_path / "costs.db")
    yield tracker
    tracker.close()


# ---------------------------------------------------------------------------
# CostTracker tests
# ---------------------------------------------------------------------------

class TestCostTracker:
    def test_cost_tracker_creates_table(self, tmp_path: Path) -> None:
        """CostTracker creates the query_costs table on init."""
        tracker = CostTracker(tmp_path / "test.db")
        try:
            conn = sqlite3.connect(str(tmp_path / "test.db"))
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='query_costs'"
            )
            assert cur.fetchone() is not None
            conn.close()
        finally:
            tracker.close()

    def test_cost_tracker_log_and_summary(self, cost_tracker: CostTracker) -> None:
        """Log 3 entries, verify summary returns correct counts and totals."""
        cost_tracker.log("claude-sonnet-4-5-20250929", "anthropic", 1000, 500, cost_usd=0.01)
        cost_tracker.log("claude-sonnet-4-5-20250929", "anthropic", 2000, 1000, cost_usd=0.02)
        cost_tracker.log("qwen3:14b", "ollama", 500, 300, cost_usd=0.0)

        summary = cost_tracker.summary(days=30)
        assert summary["period_days"] == 30
        assert len(summary["models"]) == 2
        assert summary["total_cost_usd"] == pytest.approx(0.03)

        # Find the sonnet entry
        sonnet = next(m for m in summary["models"] if m["model"] == "claude-sonnet-4-5-20250929")
        assert sonnet["count"] == 2
        assert sonnet["input_tokens"] == 3000
        assert sonnet["output_tokens"] == 1500
        assert sonnet["cost_usd"] == pytest.approx(0.03)

    def test_cost_tracker_auto_calculates_cost(self, cost_tracker: CostTracker) -> None:
        """When cost_usd=None, cost is auto-calculated from pricing table."""
        cost_tracker.log(
            "claude-sonnet-4-5-20250929", "anthropic",
            input_tokens=1_000_000, output_tokens=500_000,
            cost_usd=None,
        )
        summary = cost_tracker.summary(days=30)
        expected = calculate_cost("claude-sonnet-4-5-20250929", 1_000_000, 500_000)
        assert summary["total_cost_usd"] == pytest.approx(expected)

    def test_cost_tracker_summary_respects_days_filter(self, cost_tracker: CostTracker) -> None:
        """Entries older than the days filter are excluded from summary."""
        cost_tracker.log("claude-sonnet-4-5-20250929", "anthropic", 1000, 500, cost_usd=0.01)

        # Manually backdate the entry to 60 days ago
        cost_tracker._db.execute(
            "UPDATE query_costs SET ts = datetime('now', '-60 days')"
        )
        cost_tracker._db.commit()

        summary = cost_tracker.summary(days=30)
        assert summary["total_cost_usd"] == pytest.approx(0.0)
        assert len(summary["models"]) == 0


# ---------------------------------------------------------------------------
# GatewayResponse tests
# ---------------------------------------------------------------------------

class TestGatewayResponse:
    def test_gateway_response_defaults(self) -> None:
        """GatewayResponse has correct default values."""
        resp = GatewayResponse(text="hello", model="test", provider="test")
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.cost_usd == 0.0
        assert resp.fallback_used is False
        assert resp.fallback_reason == ""


# ---------------------------------------------------------------------------
# ModelGateway tests (mocked SDKs)
# ---------------------------------------------------------------------------

def _mock_anthropic_response(text: str = "hello", input_tokens: int = 10, output_tokens: int = 5) -> MagicMock:
    """Create a mock Anthropic messages.create() response."""
    content_block = MagicMock()
    content_block.text = text

    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    return response


def _mock_ollama_response(text: str = "local answer") -> MagicMock:
    """Create a mock Ollama chat() response."""
    message = MagicMock()
    message.content = text

    response = MagicMock()
    response.message = message
    return response


class TestModelGateway:
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_anthropic_call(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """ModelGateway dispatches to Anthropic for claude-* models."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("hello", 10, 5)
        mock_anthropic_cls.return_value = mock_client

        gw = ModelGateway(anthropic_api_key="test-key")
        resp = gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-5-20250929",
        )

        assert resp.text == "hello"
        assert resp.provider == "anthropic"
        assert resp.input_tokens == 10
        assert resp.output_tokens == 5
        assert resp.cost_usd > 0
        assert resp.fallback_used is False
        mock_client.messages.create.assert_called_once()

    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_ollama_call(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """ModelGateway dispatches to Ollama for non-claude models or when no API key."""
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = _mock_ollama_response("local answer")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway(anthropic_api_key=None)
        resp = gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen3:14b",
        )

        assert resp.text == "local answer"
        assert resp.provider == "ollama"
        assert resp.cost_usd == 0.0
        assert resp.fallback_used is False
        mock_ollama.chat.assert_called_once()

    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_fallback_on_api_error(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """ModelGateway falls back to Ollama when Anthropic raises APIConnectionError."""
        mock_client = MagicMock()
        mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mock_client.messages.create.side_effect = APIConnectionError(request=mock_request)
        mock_anthropic_cls.return_value = mock_client

        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = _mock_ollama_response("fallback answer")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway(anthropic_api_key="test-key")
        resp = gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-5-20250929",
        )

        assert resp.fallback_used is True
        assert resp.provider == "ollama"
        assert resp.text == "fallback answer"
        assert "APIConnectionError" in resp.fallback_reason

    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_local_only_mode(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """With no API key, claude-* models fall through to Ollama."""
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = _mock_ollama_response("local only")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway(anthropic_api_key=None)
        resp = gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-5-20250929",
        )

        assert resp.provider == "ollama"
        assert resp.text == "local only"
        # Anthropic class should NOT have been instantiated
        mock_anthropic_cls.assert_not_called()

    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_logs_cost(
        self,
        mock_anthropic_cls: MagicMock,
        mock_ollama_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ModelGateway logs completion cost to CostTracker."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("hi", 100, 50)
        mock_anthropic_cls.return_value = mock_client

        tracker = CostTracker(tmp_path / "costs.db")
        try:
            gw = ModelGateway(anthropic_api_key="test-key", cost_tracker=tracker)
            gw.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-sonnet-4-5-20250929",
            )

            summary = tracker.summary(days=30)
            assert len(summary["models"]) == 1
            assert summary["models"][0]["count"] == 1
        finally:
            tracker.close()

    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_all_providers_fail(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """When both Anthropic and Ollama fail, return graceful error response."""
        mock_client = MagicMock()
        mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mock_client.messages.create.side_effect = APIConnectionError(request=mock_request)
        mock_anthropic_cls.return_value = mock_client

        mock_ollama = MagicMock()
        mock_ollama.chat.side_effect = ConnectionError("Ollama not running")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway(anthropic_api_key="test-key")
        resp = gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-5-20250929",
        )

        assert resp.text == ""
        assert resp.provider == "none"
        assert resp.fallback_used is True
        assert "APIConnectionError" in resp.fallback_reason
        assert "Ollama also failed" in resp.fallback_reason


# ---------------------------------------------------------------------------
# Pricing tests
# ---------------------------------------------------------------------------

class TestPricing:
    def test_calculate_cost_known_model(self) -> None:
        """Known model prefix returns correct cost."""
        # claude-sonnet: input $3/Mtok, output $15/Mtok
        # 1M input + 1M output = 3.0 + 15.0 = 18.0
        cost = calculate_cost("claude-sonnet-4-5-20250929", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.0)

    def test_calculate_cost_unknown_model(self) -> None:
        """Unknown model (e.g. local Ollama) returns 0.0."""
        cost = calculate_cost("qwen3:14b", 1000, 1000)
        assert cost == 0.0

    def test_pricing_table_has_required_entries(self) -> None:
        """PRICING dict has at least 3 entries (opus, sonnet, haiku)."""
        assert len(PRICING) >= 3
        assert "claude-opus" in PRICING
        assert "claude-sonnet" in PRICING
        assert "claude-haiku" in PRICING
