"""Tests for the gateway package: ModelGateway, CostTracker, pricing, audit."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from anthropic import APIConnectionError

from jarvis_engine.gateway.audit import GatewayAudit
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

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
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

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
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


# ---------------------------------------------------------------------------
# GatewayAudit tests
# ---------------------------------------------------------------------------

class TestGatewayAudit:
    def test_log_decision_writes_jsonl(self, tmp_path: Path) -> None:
        """log_decision appends a valid JSONL line to the audit file."""
        audit = GatewayAudit(tmp_path / "audit.jsonl")
        audit.log_decision(
            provider="anthropic",
            model="claude-sonnet-4-5-20250929",
            reason="primary:anthropic",
            latency_ms=123.4,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            success=True,
        )

        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["provider"] == "anthropic"
        assert record["model"] == "claude-sonnet-4-5-20250929"
        assert record["reason"] == "primary:anthropic"
        assert record["latency_ms"] == 123.4
        assert record["input_tokens"] == 100
        assert record["output_tokens"] == 50
        assert record["cost_usd"] == 0.001
        assert record["success"] is True
        assert record["fallback_from"] == ""
        assert record["privacy_routed"] is False
        assert "ts" in record

    def test_log_decision_multiple_records(self, tmp_path: Path) -> None:
        """Multiple log_decision calls append separate lines."""
        audit = GatewayAudit(tmp_path / "audit.jsonl")
        for i in range(3):
            audit.log_decision(
                provider="ollama",
                model="gemma3:4b",
                reason="primary:ollama",
                latency_ms=float(i * 100),
                input_tokens=10 * i,
                output_tokens=5 * i,
                cost_usd=0.0,
                success=True,
            )

        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_log_decision_with_fallback_fields(self, tmp_path: Path) -> None:
        """log_decision correctly records fallback and privacy routing."""
        audit = GatewayAudit(tmp_path / "audit.jsonl")
        audit.log_decision(
            provider="ollama",
            model="gemma3:4b",
            reason="fallback:APIConnectionError",
            latency_ms=500.0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            success=True,
            fallback_from="APIConnectionError",
            privacy_routed=True,
        )

        record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip())
        assert record["fallback_from"] == "APIConnectionError"
        assert record["privacy_routed"] is True

    def test_recent_returns_last_n_records(self, tmp_path: Path) -> None:
        """recent(n) returns only the last n records."""
        audit = GatewayAudit(tmp_path / "audit.jsonl")
        for i in range(10):
            audit.log_decision(
                provider="anthropic",
                model=f"model-{i}",
                reason="test",
                latency_ms=1.0,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                success=True,
            )

        last3 = audit.recent(3)
        assert len(last3) == 3
        assert last3[0]["model"] == "model-7"
        assert last3[1]["model"] == "model-8"
        assert last3[2]["model"] == "model-9"

    def test_recent_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        """recent() returns empty list when audit file does not exist."""
        audit = GatewayAudit(tmp_path / "nonexistent.jsonl")
        assert audit.recent() == []

    def test_recent_handles_corrupt_lines(self, tmp_path: Path) -> None:
        """recent() skips corrupt JSON lines gracefully."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text('{"provider":"ok"}\nnot-json\n{"provider":"also-ok"}\n')
        audit = GatewayAudit(audit_path)
        records = audit.recent()
        assert len(records) == 2
        assert records[0]["provider"] == "ok"
        assert records[1]["provider"] == "also-ok"

    def test_summary_computes_correct_stats(self, tmp_path: Path) -> None:
        """summary() aggregates provider counts, cost, latency, and failures."""
        audit = GatewayAudit(tmp_path / "audit.jsonl")

        # 2 successful anthropic calls
        for _ in range(2):
            audit.log_decision(
                provider="anthropic",
                model="claude-sonnet",
                reason="primary",
                latency_ms=200.0,
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                success=True,
            )

        # 1 failed ollama call
        audit.log_decision(
            provider="ollama",
            model="gemma3:4b",
            reason="fallback",
            latency_ms=500.0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            success=False,
        )

        # 1 privacy-routed call
        audit.log_decision(
            provider="ollama",
            model="gemma3:4b",
            reason="privacy",
            latency_ms=300.0,
            input_tokens=50,
            output_tokens=25,
            cost_usd=0.0,
            success=True,
            privacy_routed=True,
        )

        summary = audit.summary(hours=24)
        assert summary["total_decisions"] == 4
        assert summary["provider_breakdown"] == {"anthropic": 2, "ollama": 2}
        assert summary["total_cost_usd"] == pytest.approx(0.02)
        assert summary["avg_latency_ms"] == pytest.approx(300.0)  # (200+200+500+300)/4
        assert summary["failure_count"] == 1
        assert summary["failure_rate_pct"] == pytest.approx(25.0)
        assert summary["privacy_routed_count"] == 1

    def test_summary_empty_file(self, tmp_path: Path) -> None:
        """summary() returns zero values when no records exist."""
        audit = GatewayAudit(tmp_path / "nonexistent.jsonl")
        summary = audit.summary(hours=24)
        assert summary["total_decisions"] == 0
        assert summary["total_cost_usd"] == 0.0
        assert summary["avg_latency_ms"] == 0.0
        assert summary["failure_count"] == 0
        assert summary["failure_rate_pct"] == 0.0

    def test_log_creates_parent_directories(self, tmp_path: Path) -> None:
        """log_decision creates parent directories if they don't exist."""
        audit = GatewayAudit(tmp_path / "nested" / "dir" / "audit.jsonl")
        audit.log_decision(
            provider="test",
            model="test",
            reason="test",
            latency_ms=0.0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            success=True,
        )
        assert (tmp_path / "nested" / "dir" / "audit.jsonl").exists()


# ---------------------------------------------------------------------------
# ModelGateway audit integration tests
# ---------------------------------------------------------------------------

class TestModelGatewayAudit:
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_logs_audit_on_success(
        self,
        mock_anthropic_cls: MagicMock,
        mock_ollama_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ModelGateway writes an audit record on successful Anthropic call."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("hi", 100, 50)
        mock_anthropic_cls.return_value = mock_client

        audit_path = tmp_path / "audit.jsonl"
        gw = ModelGateway(anthropic_api_key="test-key", audit_path=audit_path)
        gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-5-20250929",
        )

        records = GatewayAudit(audit_path).recent()
        assert len(records) == 1
        assert records[0]["provider"] == "anthropic"
        assert records[0]["success"] is True
        assert records[0]["input_tokens"] == 100
        assert records[0]["output_tokens"] == 50
        assert records[0]["latency_ms"] > 0

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_logs_audit_on_fallback(
        self,
        mock_anthropic_cls: MagicMock,
        mock_ollama_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ModelGateway logs both the failed attempt and the fallback success."""
        mock_client = MagicMock()
        mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mock_client.messages.create.side_effect = APIConnectionError(request=mock_request)
        mock_anthropic_cls.return_value = mock_client

        mock_ollama = MagicMock()
        ollama_resp = _mock_ollama_response("fallback")
        ollama_resp.prompt_eval_count = 20
        ollama_resp.eval_count = 10
        mock_ollama.chat.return_value = ollama_resp
        mock_ollama_cls.return_value = mock_ollama

        audit_path = tmp_path / "audit.jsonl"
        gw = ModelGateway(anthropic_api_key="test-key", audit_path=audit_path)
        gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-5-20250929",
        )

        records = GatewayAudit(audit_path).recent()
        assert len(records) == 2

        # First record: the failed anthropic attempt
        assert records[0]["provider"] == "anthropic"
        assert records[0]["success"] is False

        # Second record: the successful fallback
        assert records[1]["provider"] == "ollama"
        assert records[1]["success"] is True
        assert "APIConnectionError" in records[1]["fallback_from"]

    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_no_audit_when_not_configured(
        self,
        mock_anthropic_cls: MagicMock,
        mock_ollama_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ModelGateway works fine without audit (no overhead, no file created)."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("hi", 10, 5)
        mock_anthropic_cls.return_value = mock_client

        gw = ModelGateway(anthropic_api_key="test-key")  # no audit_path
        resp = gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-5-20250929",
        )

        assert resp.text == "hi"
        assert resp.provider == "anthropic"
        # No audit file should exist anywhere in tmp_path
        assert not list(tmp_path.glob("*.jsonl"))

    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_audit_records_privacy_routing(
        self,
        mock_anthropic_cls: MagicMock,
        mock_ollama_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """privacy_routed flag is passed through to audit records."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("hi", 10, 5)
        mock_anthropic_cls.return_value = mock_client

        audit_path = tmp_path / "audit.jsonl"
        gw = ModelGateway(anthropic_api_key="test-key", audit_path=audit_path)
        gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-5-20250929",
            privacy_routed=True,
        )

        records = GatewayAudit(audit_path).recent()
        assert len(records) == 1
        assert records[0]["privacy_routed"] is True
