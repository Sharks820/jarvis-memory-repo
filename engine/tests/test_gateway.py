"""Tests for the gateway package: ModelGateway, CostTracker, pricing, audit."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from anthropic import Anthropic, APIConnectionError
from ollama import ChatResponse, Client as OllamaClient
from ollama._types import Message as OllamaMessage

from jarvis_engine.gateway.audit import GatewayAudit
from jarvis_engine.gateway.costs import CostTracker
from jarvis_engine.gateway.models import (
    CLOUD_MODEL_MAP,
    OPENAI_COMPAT_PROVIDERS,
    GatewayResponse,
    ModelGateway,
)
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
        cost_tracker.flush()

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
    from anthropic.types import Message, TextBlock, Usage
    content_block = MagicMock(spec=TextBlock)
    content_block.text = text

    usage = MagicMock(spec=Usage)
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    response = MagicMock(spec=Message)
    response.content = [content_block]
    response.usage = usage
    return response


def _mock_ollama_response(text: str = "local answer") -> MagicMock:
    """Create a mock Ollama chat() response."""
    message = MagicMock(spec=OllamaMessage)
    message.content = text

    response = MagicMock(spec=ChatResponse)
    response.message = message
    return response


class TestModelGateway:
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_anthropic_call(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """ModelGateway dispatches to Anthropic for claude-* models."""
        mock_client = MagicMock(spec=Anthropic)
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
        mock_ollama = MagicMock(spec=OllamaClient)
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
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_fallback_on_api_error(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock) -> None:
        """ModelGateway falls back to Ollama when Anthropic raises APIConnectionError."""
        mock_client = MagicMock(spec=Anthropic)
        mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mock_client.messages.create.side_effect = APIConnectionError(request=mock_request)
        mock_anthropic_cls.return_value = mock_client

        mock_ollama = MagicMock(spec=OllamaClient)
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

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_local_only_mode(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock) -> None:
        """With no API key and no cloud keys, claude-* models fall through to Ollama."""
        mock_ollama = MagicMock(spec=OllamaClient)
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
        mock_client = MagicMock(spec=Anthropic)
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
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_all_providers_fail(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock) -> None:
        """When both Anthropic and Ollama fail, return graceful error response."""
        mock_client = MagicMock(spec=Anthropic)
        mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mock_client.messages.create.side_effect = APIConnectionError(request=mock_request)
        mock_anthropic_cls.return_value = mock_client

        mock_ollama = MagicMock(spec=OllamaClient)
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
        mock_client = MagicMock(spec=Anthropic)
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
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_gateway_logs_audit_on_fallback(
        self,
        mock_anthropic_cls: MagicMock,
        mock_ollama_cls: MagicMock,
        mock_cli: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ModelGateway logs both the failed attempt and the fallback success."""
        mock_client = MagicMock(spec=Anthropic)
        mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mock_client.messages.create.side_effect = APIConnectionError(request=mock_request)
        mock_anthropic_cls.return_value = mock_client

        mock_ollama = MagicMock(spec=OllamaClient)
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
        mock_client = MagicMock(spec=Anthropic)
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
        mock_client = MagicMock(spec=Anthropic)
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


# ---------------------------------------------------------------------------
# Helper: mock httpx response for OpenAI-compatible APIs
# ---------------------------------------------------------------------------

def _mock_httpx_response(
    status_code: int = 200,
    json_body: dict | None = None,
    text: str = "",
) -> MagicMock:
    """Create a mock httpx.Response for cloud API calls."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
        resp.text = json.dumps(json_body)
    else:
        resp.text = text
        resp.json.side_effect = json.JSONDecodeError("bad json", text, 0)
    return resp


def _openai_chat_response(
    content: str = "cloud answer",
    prompt_tokens: int = 15,
    completion_tokens: int = 8,
) -> dict:
    """Return a standard OpenAI-compatible chat/completions response body."""
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# _call_openai_compat tests
# ---------------------------------------------------------------------------

class TestCallOpenaiCompat:
    """Tests for ModelGateway._call_openai_compat() with mocked httpx."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-test-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_successful_groq_call(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Groq provider calls the correct endpoint with GROQ_API_KEY."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, _openai_chat_response("groq answer", 20, 10)
        )

        resp = gw._call_openai_compat(
            [{"role": "user", "content": "hi"}], "kimi-k2", 1024, "groq"
        )

        assert resp.text == "groq answer"
        assert resp.provider == "groq"
        assert resp.input_tokens == 20
        assert resp.output_tokens == 10
        assert resp.cost_usd > 0  # kimi-k2 is priced

        # Verify URL and headers
        call_args = gw._http.post.call_args
        assert "groq.com" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer groq-test-key"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "mistral-test-key", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_successful_mistral_call(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Mistral provider calls the correct endpoint with MISTRAL_API_KEY."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, _openai_chat_response("mistral answer", 30, 15)
        )

        resp = gw._call_openai_compat(
            [{"role": "user", "content": "hi"}], "devstral-2", 1024, "mistral"
        )

        assert resp.text == "mistral answer"
        assert resp.provider == "mistral"
        assert resp.input_tokens == 30
        assert resp.output_tokens == 15

        call_args = gw._http.post.call_args
        assert "mistral.ai" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer mistral-test-key"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": "zai-test-key"})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_successful_zai_call(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Z.ai provider calls the correct endpoint with ZAI_API_KEY."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, _openai_chat_response("zai answer", 25, 12)
        )

        resp = gw._call_openai_compat(
            [{"role": "user", "content": "hi"}], "glm-4.7", 1024, "zai"
        )

        assert resp.text == "zai answer"
        assert resp.provider == "zai"
        assert resp.input_tokens == 25
        assert resp.output_tokens == 12

        call_args = gw._http.post.call_args
        assert "bigmodel.cn" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer zai-test-key"

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-test-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_http_error_raises_runtime_error(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Non-200 HTTP status from cloud API raises RuntimeError."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            429, text="Rate limit exceeded"
        )

        with pytest.raises(RuntimeError, match="HTTP 429"):
            gw._call_openai_compat(
                [{"role": "user", "content": "hi"}], "kimi-k2", 1024, "groq"
            )

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-test-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_http_500_error(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """HTTP 500 from cloud API raises RuntimeError with status code."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            500, text="Internal Server Error"
        )

        with pytest.raises(RuntimeError, match="HTTP 500"):
            gw._call_openai_compat(
                [{"role": "user", "content": "hi"}], "kimi-k2", 1024, "groq"
            )

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-test-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_model_alias_resolved_to_api_model(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Cloud model aliases (e.g. kimi-k2) are resolved to API model IDs in the payload."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, _openai_chat_response("ok")
        )

        gw._call_openai_compat(
            [{"role": "user", "content": "hi"}], "kimi-k2", 512, "groq"
        )

        call_args = gw._http.post.call_args
        payload = call_args[1]["json"]
        assert payload["model"] == "moonshotai/kimi-k2-instruct"
        assert payload["max_tokens"] == 512

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-test-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_unknown_model_passes_through(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Model name not in CLOUD_MODEL_MAP passes through as-is."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, _openai_chat_response("ok")
        )

        gw._call_openai_compat(
            [{"role": "user", "content": "hi"}], "custom-model-xyz", 1024, "groq"
        )

        call_args = gw._http.post.call_args
        payload = call_args[1]["json"]
        assert payload["model"] == "custom-model-xyz"

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-test-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_empty_choices_returns_empty_text(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """When API returns empty choices array, text should be empty string."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 0}}
        )

        resp = gw._call_openai_compat(
            [{"role": "user", "content": "hi"}], "kimi-k2", 1024, "groq"
        )

        assert resp.text == ""
        assert resp.input_tokens == 5
        assert resp.output_tokens == 0

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-test-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_missing_usage_defaults_to_zero(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """When API response lacks usage field, tokens default to 0."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, {"choices": [{"message": {"content": "hi"}}]}
        )

        resp = gw._call_openai_compat(
            [{"role": "user", "content": "hi"}], "kimi-k2", 1024, "groq"
        )

        assert resp.text == "hi"
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0


# ---------------------------------------------------------------------------
# _resolve_provider tests
# ---------------------------------------------------------------------------

class TestResolveProvider:
    """Tests for ModelGateway._resolve_provider() routing logic."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_claude_model_routes_to_anthropic(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """claude-* models route to anthropic when API key is configured."""
        mock_anthropic_cls.return_value = MagicMock(spec=Anthropic)
        gw = ModelGateway(anthropic_api_key="test-key")

        assert gw._resolve_provider("claude-sonnet-4-5-20250929") == "anthropic"
        assert gw._resolve_provider("claude-opus") == "anthropic"
        assert gw._resolve_provider("claude-haiku") == "anthropic"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_claude_without_key_falls_to_ollama(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """claude-* models fall through to ollama when no API key."""
        gw = ModelGateway(anthropic_api_key=None)

        assert gw._resolve_provider("claude-sonnet-4-5-20250929") == "ollama"

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_groq_model_routes_to_cloud(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Groq cloud models route to cloud:groq when GROQ_API_KEY is set."""
        gw = ModelGateway()

        assert gw._resolve_provider("kimi-k2") == "cloud:groq"
        assert gw._resolve_provider("llama-3.3-70b") == "cloud:groq"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "mistral-key", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_mistral_model_routes_to_cloud(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Mistral models route to cloud:mistral when MISTRAL_API_KEY is set."""
        gw = ModelGateway()

        assert gw._resolve_provider("devstral-2") == "cloud:mistral"
        assert gw._resolve_provider("devstral-small-2") == "cloud:mistral"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": "zai-key"})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_zai_model_routes_to_cloud(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Z.ai models route to cloud:zai when ZAI_API_KEY is set."""
        gw = ModelGateway()

        assert gw._resolve_provider("glm-4.7") == "cloud:zai"
        assert gw._resolve_provider("glm-4.7-flash") == "cloud:zai"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_cloud_model_without_key_falls_to_ollama(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Cloud model names fall to ollama when their API key is missing."""
        gw = ModelGateway()

        assert gw._resolve_provider("kimi-k2") == "ollama"
        assert gw._resolve_provider("devstral-2") == "ollama"
        assert gw._resolve_provider("glm-4.7") == "ollama"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_unknown_model_routes_to_ollama(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Unknown/local model names always route to ollama."""
        gw = ModelGateway()

        assert gw._resolve_provider("qwen3:14b") == "ollama"
        assert gw._resolve_provider("gemma3:4b") == "ollama"
        assert gw._resolve_provider("custom-model") == "ollama"


# ---------------------------------------------------------------------------
# _best_cloud_model tests
# ---------------------------------------------------------------------------

class TestBestCloudModel:
    """Tests for ModelGateway._best_cloud_model() priority selection."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "key", "MISTRAL_API_KEY": "key", "ZAI_API_KEY": "key"})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_groq_is_highest_priority(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """When all cloud keys present, Groq (kimi-k2) wins as fastest."""
        gw = ModelGateway()
        assert gw._best_cloud_model() == "kimi-k2"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "key", "ZAI_API_KEY": "key"})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_mistral_when_no_groq(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """When Groq missing, Mistral (devstral-2) is next priority."""
        gw = ModelGateway()
        assert gw._best_cloud_model() == "devstral-2"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": "key"})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_zai_when_no_groq_or_mistral(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """When Groq and Mistral missing, Z.ai (glm-4.7-flash) is selected."""
        gw = ModelGateway()
        assert gw._best_cloud_model() == "glm-4.7-flash"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_no_cloud_keys_returns_none(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """When no cloud keys are configured, returns None."""
        gw = ModelGateway()
        assert gw._best_cloud_model() is None


# ---------------------------------------------------------------------------
# _fallback_chain tests
# ---------------------------------------------------------------------------

class TestFallbackChain:
    """Tests for ModelGateway._fallback_chain() provider cascade."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_anthropic_fails_groq_succeeds(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """When Anthropic fails, fallback chain tries Groq and succeeds."""
        gw = ModelGateway(anthropic_api_key="test-key")
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, _openai_chat_response("groq fallback", 10, 5)
        )

        resp = gw._fallback_chain(
            [{"role": "user", "content": "hi"}], 1024, "APIConnectionError",
            skip_provider="anthropic",
        )

        assert resp.provider == "groq"
        assert resp.text == "groq fallback"
        assert resp.fallback_used is True
        assert "APIConnectionError" in resp.fallback_reason

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_all_cloud_fails_ollama_fallback(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock) -> None:
        """When no cloud providers available, chain falls back to Ollama."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.chat.return_value = _mock_ollama_response("ollama saves the day")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway(anthropic_api_key="test-key")

        resp = gw._fallback_chain(
            [{"role": "user", "content": "hi"}], 1024, "APIConnectionError",
            skip_provider="anthropic",
        )

        assert resp.provider == "ollama"
        assert resp.fallback_used is True
        assert resp.text == "ollama saves the day"

    @patch.dict("os.environ", {"GROQ_API_KEY": "key", "MISTRAL_API_KEY": "key", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_skip_failed_provider(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Fallback chain skips the provider that already failed."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, _openai_chat_response("mistral fallback", 10, 5)
        )

        resp = gw._fallback_chain(
            [{"role": "user", "content": "hi"}], 1024, "groq error",
            skip_provider="groq",
        )

        # Should have called Mistral, not Groq
        assert resp.provider == "mistral"
        assert resp.fallback_used is True

    @patch.dict("os.environ", {"GROQ_API_KEY": "key", "MISTRAL_API_KEY": "key", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_groq_fails_mistral_fails_ollama_succeeds(
        self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock
    ) -> None:
        """When Groq and Mistral both fail, chain falls through to Ollama."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.chat.return_value = _mock_ollama_response("local final")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(500, text="Server Error")

        resp = gw._fallback_chain(
            [{"role": "user", "content": "hi"}], 1024, "anthropic error",
            skip_provider="anthropic",
        )

        assert resp.provider == "ollama"
        assert resp.fallback_used is True

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", False)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_all_providers_fail_returns_none_provider(
        self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock
    ) -> None:
        """When all cloud and Ollama unavailable, provider is 'none'."""
        gw = ModelGateway()

        resp = gw._fallback_chain(
            [{"role": "user", "content": "hi"}], 1024, "everything broke",
            skip_provider="anthropic",
        )

        assert resp.provider == "none"
        assert resp.fallback_used is True
        assert "Ollama also failed" in resp.fallback_reason


# ---------------------------------------------------------------------------
# Health check tests
# ---------------------------------------------------------------------------

class TestHealthChecks:
    """Tests for check_ollama(), check_anthropic(), check_cloud()."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_check_ollama_reachable(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """check_ollama returns True when Ollama server responds."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.list.return_value = {"models": []}
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway()
        assert gw.check_ollama() is True

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_check_ollama_unreachable(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """check_ollama returns False when Ollama server is down."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.list.side_effect = ConnectionError("Connection refused")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway()
        assert gw.check_ollama() is False

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", False)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_check_ollama_no_package(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """check_ollama returns False when ollama package is not installed."""
        gw = ModelGateway()
        # _HAS_OLLAMA is False, so _ollama will be None
        gw._ollama = None
        assert gw.check_ollama() is False

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_check_anthropic_with_key(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """check_anthropic returns True when API key is configured."""
        mock_anthropic_cls.return_value = MagicMock(spec=Anthropic)
        gw = ModelGateway(anthropic_api_key="test-key")
        assert gw.check_anthropic() is True

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_check_anthropic_without_key(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """check_anthropic returns False when no API key."""
        gw = ModelGateway(anthropic_api_key=None)
        assert gw.check_anthropic() is False

    @patch.dict("os.environ", {"GROQ_API_KEY": "gk", "MISTRAL_API_KEY": "", "ZAI_API_KEY": "zk"})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_check_cloud_returns_configured_providers(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """check_cloud returns dict of providers that have keys."""
        gw = ModelGateway()
        cloud = gw.check_cloud()
        assert cloud == {"groq": True, "zai": True}
        assert "mistral" not in cloud

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_check_cloud_no_keys(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """check_cloud returns empty dict when no cloud keys configured."""
        gw = ModelGateway()
        assert gw.check_cloud() == {}


# ---------------------------------------------------------------------------
# available_providers tests
# ---------------------------------------------------------------------------

class TestAvailableProviders:
    """Tests for ModelGateway.available_providers()."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "gk", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_all_providers_available(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Lists anthropic, cloud providers, and ollama when all configured."""
        mock_anthropic_cls.return_value = MagicMock(spec=Anthropic)
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.list.return_value = {"models": []}
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway(anthropic_api_key="test-key")
        providers = gw.available_providers()

        assert "anthropic" in providers
        assert "groq" in providers
        assert "ollama" in providers

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_ollama_only(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock) -> None:
        """With no API keys, only ollama is available (if running)."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.list.return_value = {"models": []}
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway(anthropic_api_key=None)
        providers = gw.available_providers()

        assert providers == ["ollama"]
        assert "anthropic" not in providers

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_no_providers_when_ollama_down(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock) -> None:
        """With no API keys and Ollama down, empty provider list."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.list.side_effect = ConnectionError("down")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway(anthropic_api_key=None)
        providers = gw.available_providers()

        assert providers == []

    @patch.dict("os.environ", {"GROQ_API_KEY": "gk", "MISTRAL_API_KEY": "mk", "ZAI_API_KEY": "zk"})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_all_cloud_keys_present(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """All three cloud providers appear when all keys set."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.list.side_effect = ConnectionError("down")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway()
        providers = gw.available_providers()

        assert "groq" in providers
        assert "mistral" in providers
        assert "zai" in providers
        assert "ollama" not in providers  # Ollama is down


# ---------------------------------------------------------------------------
# Context manager and close() tests
# ---------------------------------------------------------------------------

class TestGatewayLifecycle:
    """Tests for close() and context manager protocol."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_close_releases_http_client(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """close() calls _http.close() to release connection pool."""
        gw = ModelGateway()
        mock_http = MagicMock(spec=httpx.Client)
        gw._http = mock_http

        gw.close()

        mock_http.close.assert_called_once()

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_context_manager_returns_gateway(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Context manager __enter__ returns the gateway instance."""
        gw = ModelGateway()
        mock_http = MagicMock(spec=httpx.Client)
        gw._http = mock_http

        with gw as g:
            assert g is gw

        mock_http.close.assert_called_once()

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_context_manager_closes_on_exception(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """Context manager still calls close() even if body raises."""
        gw = ModelGateway()
        mock_http = MagicMock(spec=httpx.Client)
        gw._http = mock_http

        with pytest.raises(ValueError):
            with gw:
                raise ValueError("test error")

        mock_http.close.assert_called_once()


# ---------------------------------------------------------------------------
# Cloud complete() integration tests (full path through complete())
# ---------------------------------------------------------------------------

class TestCloudCompleteIntegration:
    """End-to-end tests for complete() dispatching to cloud providers."""

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-key", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_complete_dispatches_to_groq(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """complete() with a Groq model name dispatches through cloud path."""
        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        gw._http.post.return_value = _mock_httpx_response(
            200, _openai_chat_response("groq response", 20, 10)
        )

        resp = gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="kimi-k2",
        )

        assert resp.provider == "groq"
        assert resp.text == "groq response"
        assert resp.fallback_used is False

    @patch.dict("os.environ", {"GROQ_API_KEY": "groq-key", "MISTRAL_API_KEY": "mistral-key", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={})
    @patch("jarvis_engine.gateway.models._HAS_OLLAMA", True)
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_complete_cloud_failure_triggers_fallback(
        self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock, mock_cli: MagicMock
    ) -> None:
        """complete() with cloud provider failure falls back through chain."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.chat.return_value = _mock_ollama_response("ollama rescue")
        mock_ollama_cls.return_value = mock_ollama

        gw = ModelGateway()
        gw._http = MagicMock(spec=httpx.Client)
        # All HTTP calls fail
        gw._http.post.return_value = _mock_httpx_response(500, text="Server Error")

        resp = gw.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="kimi-k2",
        )

        assert resp.fallback_used is True
        # Should have fallen through groq failure -> tried mistral (also failed) -> ollama
        assert resp.provider == "ollama"

    @patch.dict("os.environ", {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""})
    @patch("jarvis_engine.gateway.models.OllamaClient")
    @patch("jarvis_engine.gateway.models.Anthropic")
    def test_init_with_explicit_cloud_keys(self, mock_anthropic_cls: MagicMock, mock_ollama_cls: MagicMock) -> None:
        """ModelGateway accepts explicit cloud API keys via constructor."""
        gw = ModelGateway(groq_api_key="explicit-groq", mistral_api_key="explicit-mistral")

        assert "groq" in gw._cloud_keys
        assert "mistral" in gw._cloud_keys
        assert "zai" not in gw._cloud_keys
        assert gw._cloud_keys["groq"] == "explicit-groq"
        assert gw._cloud_keys["mistral"] == "explicit-mistral"


# ---------------------------------------------------------------------------
# Data integrity tests
# ---------------------------------------------------------------------------

class TestCloudModelRegistry:
    """Tests for the CLOUD_MODEL_MAP and OPENAI_COMPAT_PROVIDERS data."""

    def test_all_cloud_models_reference_valid_providers(self) -> None:
        """Every model in CLOUD_MODEL_MAP references a provider in OPENAI_COMPAT_PROVIDERS."""
        for model_alias, (provider_key, _) in CLOUD_MODEL_MAP.items():
            assert provider_key in OPENAI_COMPAT_PROVIDERS, (
                f"Model '{model_alias}' references unknown provider '{provider_key}'"
            )

    def test_all_providers_have_required_fields(self) -> None:
        """Every provider config has env_key, base_url, and provider_name."""
        for key, cfg in OPENAI_COMPAT_PROVIDERS.items():
            assert "env_key" in cfg, f"Provider '{key}' missing env_key"
            assert "base_url" in cfg, f"Provider '{key}' missing base_url"
            assert "provider_name" in cfg, f"Provider '{key}' missing provider_name"

    def test_provider_urls_are_https(self) -> None:
        """All cloud provider base URLs use HTTPS."""
        for key, cfg in OPENAI_COMPAT_PROVIDERS.items():
            assert cfg["base_url"].startswith("https://"), (
                f"Provider '{key}' base_url is not HTTPS"
            )

    def test_all_cloud_models_have_pricing(self) -> None:
        """Every cloud model alias in CLOUD_MODEL_MAP has a pricing entry."""
        for model_alias in CLOUD_MODEL_MAP:
            cost = calculate_cost(model_alias, 1_000_000, 1_000_000)
            assert cost > 0, f"Model '{model_alias}' has no pricing entry"
