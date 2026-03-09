"""Tests for MEDIUM audit findings in gateway/routing: cumulative latency,
429 short retry, context guards, feedback routing, ModelRouter deprecation,
CLI token parsing.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import warnings
from unittest.mock import MagicMock, patch, PropertyMock

import httpx
import pytest

from jarvis_engine.gateway.models import (
    GatewayResponse,
    ModelGateway,
    _MODEL_CONTEXT_LIMITS,
    _CONTEXT_GUARD_THRESHOLD,
)
from jarvis_engine.gateway.cli_providers import _parse_token_usage, _cli_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLEAN_ENV = {"GROQ_API_KEY": "", "MISTRAL_API_KEY": "", "ZAI_API_KEY": ""}


def _make_gateway(**kwargs) -> ModelGateway:
    """Create a ModelGateway with mocked Ollama/CLI detection for isolation."""
    with patch("jarvis_engine.gateway.models.detect_cli_providers", return_value={}), \
         patch("jarvis_engine.gateway.models._HAS_OLLAMA", False):
        return ModelGateway(**kwargs)


# ---------------------------------------------------------------------------
# 1. Cumulative Fallback Latency Tracking
# ---------------------------------------------------------------------------

class TestCumulativeFallbackLatency:
    """Verify chain_latency_ms is computed across all retry/fallback attempts."""

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_chain_latency_logged_on_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        """When a fallback occurs, chain latency is logged."""
        gw = _make_gateway(groq_api_key="test-key")
        try:
            # Make Groq call fail so fallback triggers
            def fake_openai_compat(*args, **kwargs):
                time.sleep(0.01)
                raise RuntimeError("simulated cloud failure")

            gw._call_openai_compat = fake_openai_compat  # type: ignore[assignment]

            # Make ollama fallback return something
            gw._call_ollama = lambda msgs, model, mt, temp: GatewayResponse(
                text="ok", model=model, provider="ollama",
            )
            # Need to also make _fallback_chain work (it calls _call_openai_compat, then ollama)
            gw._fallback_to_ollama = lambda msgs, mt, reason, temp: GatewayResponse(
                text="ok", model="gemma3:4b", provider="ollama",
                fallback_used=True, fallback_reason=reason,
            )

            with caplog.at_level(logging.INFO):
                resp = gw.complete(
                    [{"role": "user", "content": "hello"}],
                    model="kimi-k2",
                    route_reason="test",
                )

            assert resp.fallback_used
            # Check that "Chain latency" appears in logs
            chain_logs = [r for r in caplog.records if "Chain latency" in r.message]
            assert len(chain_logs) >= 1, "Expected chain latency log entry"
            assert "ms" in chain_logs[0].message
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_no_chain_latency_log_without_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        """When no fallback occurs, no chain latency is logged."""
        gw = _make_gateway(groq_api_key="test-key")
        try:
            gw._call_openai_compat = lambda *a, **kw: GatewayResponse(
                text="hi", model="kimi-k2", provider="groq",
                input_tokens=10, output_tokens=5,
            )

            with caplog.at_level(logging.INFO):
                resp = gw.complete(
                    [{"role": "user", "content": "hello"}],
                    model="kimi-k2",
                )

            assert not resp.fallback_used
            chain_logs = [r for r in caplog.records if "Chain latency" in r.message]
            assert len(chain_logs) == 0
        finally:
            gw.close()


# ---------------------------------------------------------------------------
# 2. 429 Short-Wait Retry
# ---------------------------------------------------------------------------

class TestShortRetryOn429:
    """HTTP 429 with Retry-After <= 5s triggers one retry."""

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_429_short_retry_succeeds(self) -> None:
        """When 429 has Retry-After=1, sleep and retry succeeds."""
        gw = _make_gateway(groq_api_key="test-key")
        try:
            call_count = {"n": 0}

            def mock_post(url, json=None, headers=None):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    # First call: 429 with short Retry-After
                    resp = MagicMock()
                    resp.status_code = 429
                    resp.headers = {"Retry-After": "1"}
                    return resp
                # Second call: success
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "choices": [{"message": {"content": "success after retry"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
                return resp

            gw._http.post = mock_post

            with patch("jarvis_engine.gateway.models.time.sleep") as mock_sleep:
                result = gw._call_openai_compat(
                    [{"role": "user", "content": "test"}],
                    "kimi-k2", 1024, "groq",
                )

            assert result.text == "success after retry"
            assert call_count["n"] == 2
            mock_sleep.assert_called_once_with(1.0)
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_429_long_retry_raises(self) -> None:
        """When 429 has Retry-After=30, do NOT retry -- raise immediately."""
        gw = _make_gateway(groq_api_key="test-key")
        try:
            resp_mock = MagicMock()
            resp_mock.status_code = 429
            resp_mock.headers = {"Retry-After": "30"}
            gw._http.post = lambda *a, **kw: resp_mock

            with pytest.raises(RuntimeError, match="Rate limited"):
                gw._call_openai_compat(
                    [{"role": "user", "content": "test"}],
                    "kimi-k2", 1024, "groq",
                )
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_429_unknown_retry_after_raises(self) -> None:
        """When 429 has no/unparseable Retry-After, raise immediately."""
        gw = _make_gateway(groq_api_key="test-key")
        try:
            resp_mock = MagicMock()
            resp_mock.status_code = 429
            resp_mock.headers = {"Retry-After": "unknown"}
            gw._http.post = lambda *a, **kw: resp_mock

            with pytest.raises(RuntimeError, match="Rate limited"):
                gw._call_openai_compat(
                    [{"role": "user", "content": "test"}],
                    "kimi-k2", 1024, "groq",
                )
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_429_short_retry_still_429(self) -> None:
        """When retry after short wait also gets 429, raise RuntimeError."""
        gw = _make_gateway(groq_api_key="test-key")
        try:
            resp_mock = MagicMock()
            resp_mock.status_code = 429
            resp_mock.headers = {"Retry-After": "2"}
            gw._http.post = lambda *a, **kw: resp_mock

            with patch("jarvis_engine.gateway.models.time.sleep"):
                with pytest.raises(RuntimeError, match="retried once"):
                    gw._call_openai_compat(
                        [{"role": "user", "content": "test"}],
                        "kimi-k2", 1024, "groq",
                    )
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_429_exactly_5s_retries(self) -> None:
        """Retry-After=5 is within the threshold, should retry."""
        gw = _make_gateway(groq_api_key="test-key")
        try:
            call_count = {"n": 0}

            def mock_post(url, json=None, headers=None):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    resp = MagicMock()
                    resp.status_code = 429
                    resp.headers = {"Retry-After": "5"}
                    return resp
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
                return resp

            gw._http.post = mock_post

            with patch("jarvis_engine.gateway.models.time.sleep") as mock_sleep:
                result = gw._call_openai_compat(
                    [{"role": "user", "content": "test"}],
                    "kimi-k2", 1024, "groq",
                )

            assert result.text == "ok"
            mock_sleep.assert_called_once_with(5.0)
        finally:
            gw.close()


# ---------------------------------------------------------------------------
# 3. Context Window Guards Per Model
# ---------------------------------------------------------------------------

class TestContextWindowGuard:
    """Verify context guard truncates or switches model on oversized prompts."""

    def test_model_context_limits_populated(self) -> None:
        """_MODEL_CONTEXT_LIMITS has entries for key models."""
        assert "kimi-k2" in _MODEL_CONTEXT_LIMITS
        assert "gemma3:4b" in _MODEL_CONTEXT_LIMITS
        assert "claude-opus" in _MODEL_CONTEXT_LIMITS
        assert _MODEL_CONTEXT_LIMITS["kimi-k2"] == 131_072
        assert _MODEL_CONTEXT_LIMITS["gemma3:4b"] == 8_192

    def test_estimate_tokens_rough(self) -> None:
        """_estimate_tokens returns approximately len/4."""
        messages = [{"role": "user", "content": "a" * 400}]
        assert ModelGateway._estimate_tokens(messages) == 100

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_context_guard_no_action_when_under_limit(self) -> None:
        """Messages within 90% of context window are unchanged."""
        gw = _make_gateway()
        try:
            # Small message for kimi-k2 (131k limit)
            msgs = [{"role": "user", "content": "hello"}]
            result_msgs, result_model = gw._apply_context_guard(msgs, "kimi-k2")
            assert result_msgs is msgs  # same reference, unchanged
            assert result_model == "kimi-k2"
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_context_guard_truncates_system_prompt(self) -> None:
        """When exceeding context limit with no larger model, system prompt is truncated."""
        gw = _make_gateway()
        try:
            # Create messages that exceed 90% of gemma3:4b's 8192 context
            # 90% of 8192 = 7372 tokens ~ 29,488 chars
            big_system = "x" * 40_000  # ~10,000 tokens
            msgs = [
                {"role": "system", "content": big_system},
                {"role": "user", "content": "hello"},
            ]
            result_msgs, result_model = gw._apply_context_guard(msgs, "gemma3:4b")

            # Should have truncated the system message
            total_chars = sum(len(m.get("content", "")) for m in result_msgs)
            assert total_chars < 40_005  # less than original
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_context_guard_switches_to_larger_model(self) -> None:
        """When a larger model is available, switches to it instead of truncating."""
        gw = _make_gateway(groq_api_key="test-key")
        try:
            # Create messages that exceed gemma3:4b limit but fit in kimi-k2
            # gemma3:4b = 8192 tokens, 90% = 7372 tokens ~ 29,488 chars
            # kimi-k2 = 131072 tokens, 90% = 117,964 tokens ~ 471,858 chars
            big_content = "x" * 35_000  # ~8,750 tokens > 7,372 but << 117,964
            msgs = [
                {"role": "system", "content": big_content},
                {"role": "user", "content": "hello"},
            ]
            result_msgs, result_model = gw._apply_context_guard(msgs, "gemma3:4b")

            # Should have switched to a larger model
            assert result_model != "gemma3:4b"
            # Messages should be unchanged (no truncation needed)
            assert result_msgs is msgs
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_context_guard_unknown_model_no_action(self) -> None:
        """Unknown model names are passed through without modification."""
        gw = _make_gateway()
        try:
            msgs = [{"role": "user", "content": "x" * 1_000_000}]
            result_msgs, result_model = gw._apply_context_guard(msgs, "unknown-model-42")
            assert result_msgs is msgs
            assert result_model == "unknown-model-42"
        finally:
            gw.close()


# ---------------------------------------------------------------------------
# 4. Feedback Influences Routing
# ---------------------------------------------------------------------------

class TestFeedbackInfluencesRouting:
    """Feedback tracker logs warnings for low-satisfaction routes."""

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_low_satisfaction_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """When satisfaction < 0.4, a warning is logged."""
        mock_tracker = MagicMock()
        mock_tracker.get_route_quality.return_value = {
            "positive_count": 1,
            "negative_count": 9,
            "total": 10,
            "satisfaction_rate": 0.1,
        }

        gw = _make_gateway(feedback_tracker=mock_tracker)
        try:
            with caplog.at_level(logging.WARNING):
                gw._check_feedback_quality("routine")

            assert any("Low satisfaction" in r.message for r in caplog.records)
            mock_tracker.get_route_quality.assert_called_once_with("routine")
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_good_satisfaction_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """When satisfaction >= 0.4, no warning is logged."""
        mock_tracker = MagicMock()
        mock_tracker.get_route_quality.return_value = {
            "positive_count": 8,
            "negative_count": 2,
            "total": 10,
            "satisfaction_rate": 0.8,
        }

        gw = _make_gateway(feedback_tracker=mock_tracker)
        try:
            with caplog.at_level(logging.WARNING):
                gw._check_feedback_quality("routine")

            assert not any("Low satisfaction" in r.message for r in caplog.records)
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_no_feedback_tracker_no_error(self) -> None:
        """When no feedback tracker is set, check does nothing."""
        gw = _make_gateway()
        try:
            # Should not raise
            gw._check_feedback_quality("routine")
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_empty_route_skips_check(self) -> None:
        """When route_reason is empty, check is skipped."""
        mock_tracker = MagicMock()
        gw = _make_gateway(feedback_tracker=mock_tracker)
        try:
            gw._check_feedback_quality("")
            mock_tracker.get_route_quality.assert_not_called()
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_too_few_samples_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """When total < 5, no warning even if satisfaction is 0."""
        mock_tracker = MagicMock()
        mock_tracker.get_route_quality.return_value = {
            "positive_count": 0,
            "negative_count": 3,
            "total": 3,
            "satisfaction_rate": 0.0,
        }

        gw = _make_gateway(feedback_tracker=mock_tracker)
        try:
            with caplog.at_level(logging.WARNING):
                gw._check_feedback_quality("routine")

            assert not any("Low satisfaction" in r.message for r in caplog.records)
        finally:
            gw.close()

    @patch.dict("os.environ", _CLEAN_ENV)
    def test_feedback_check_called_in_complete(self, caplog: pytest.LogCaptureFixture) -> None:
        """complete() calls _check_feedback_quality before routing."""
        mock_tracker = MagicMock()
        mock_tracker.get_route_quality.return_value = {
            "positive_count": 0,
            "negative_count": 10,
            "total": 10,
            "satisfaction_rate": 0.0,
        }

        gw = _make_gateway(groq_api_key="test-key", feedback_tracker=mock_tracker)
        try:
            gw._call_openai_compat = lambda *a, **kw: GatewayResponse(
                text="hi", model="kimi-k2", provider="groq",
                input_tokens=10, output_tokens=5,
            )

            with caplog.at_level(logging.WARNING):
                gw.complete(
                    [{"role": "user", "content": "hello"}],
                    model="kimi-k2",
                    route_reason="routine",
                )

            assert any("Low satisfaction" in r.message for r in caplog.records)
        finally:
            gw.close()


# ---------------------------------------------------------------------------
# 5. ModelRouter Deprecation
# ---------------------------------------------------------------------------

class TestModelRouterDeprecation:
    """ModelRouter.__init__ emits DeprecationWarning."""

    def test_deprecation_warning(self) -> None:
        """Creating a ModelRouter should emit DeprecationWarning."""
        from jarvis_engine.router import ModelRouter

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            router = ModelRouter(cloud_burst_enabled=True)

        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "ModelRouter is deprecated" in str(w[0].message)
        assert "IntentClassifier" in str(w[0].message)

    def test_router_still_works_after_deprecation(self) -> None:
        """ModelRouter still functions after deprecation warning."""
        from jarvis_engine.router import ModelRouter, RouteDecision

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            router = ModelRouter(cloud_burst_enabled=True)

        result = router.route("high", "hard")
        assert isinstance(result, RouteDecision)
        assert result.provider == "cloud_verifier"


# ---------------------------------------------------------------------------
# 6. CLI Provider Token Parsing
# ---------------------------------------------------------------------------

class TestCLITokenParsing:
    """_parse_token_usage extracts token counts from CLI output."""

    def test_json_usage_dict(self) -> None:
        """Parse tokens from JSON with usage dict."""
        text = json.dumps({
            "result": "hello",
            "usage": {"input_tokens": 150, "output_tokens": 42},
        })
        assert _parse_token_usage(text) == (150, 42)

    def test_json_event_stream(self) -> None:
        """Parse tokens from Claude CLI event stream format."""
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"type": "result", "result": "hi", "usage": {"input_tokens": 200, "output_tokens": 80}},
        ]
        text = json.dumps(events)
        assert _parse_token_usage(text) == (200, 80)

    def test_plain_text_pattern_input_output(self) -> None:
        """Parse 'X input tokens, Y output tokens' pattern."""
        text = "Completed. 1500 input tokens, 300 output tokens used."
        assert _parse_token_usage(text) == (1500, 300)

    def test_tokens_colon_pattern(self) -> None:
        """Parse 'tokens: X input, Y output' pattern."""
        text = "Stats: tokens: 2000 input, 450 output"
        assert _parse_token_usage(text) == (2000, 450)

    def test_input_output_colon_pattern(self) -> None:
        """Parse 'input: X tokens, output: Y tokens' pattern."""
        text = "Usage: input: 500 tokens, output: 100 tokens"
        assert _parse_token_usage(text) == (500, 100)

    def test_prompt_completion_tokens_pattern(self) -> None:
        """Parse 'prompt_tokens: X ... completion_tokens: Y' pattern."""
        text = '{"prompt_tokens": 800, "completion_tokens": 200}'
        # This will be parsed as JSON first, but if we strip the json structure...
        text2 = "prompt_tokens: 800, completion_tokens: 200"
        assert _parse_token_usage(text2) == (800, 200)

    def test_empty_string_returns_zeros(self) -> None:
        """Empty input returns (0, 0)."""
        assert _parse_token_usage("") == (0, 0)

    def test_no_match_returns_zeros(self) -> None:
        """Non-matching text returns (0, 0)."""
        assert _parse_token_usage("just some random output text") == (0, 0)

    def test_json_no_usage_returns_zeros(self) -> None:
        """JSON without usage field returns (0, 0)."""
        text = json.dumps({"result": "hello", "cost_usd": 0.01})
        assert _parse_token_usage(text) == (0, 0)

    def test_comma_separated_numbers(self) -> None:
        """Parse comma-separated numbers like '1,500 input tokens'."""
        text = "1,500 input tokens, 300 output tokens"
        assert _parse_token_usage(text) == (1500, 300)

    def test_cli_result_includes_token_fields(self) -> None:
        """_cli_result includes input_tokens and output_tokens."""
        result = _cli_result("test", "test-model", text="hi", success=True,
                             input_tokens=100, output_tokens=50)
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50

    def test_cli_result_defaults_to_zero(self) -> None:
        """_cli_result defaults token counts to 0."""
        result = _cli_result("test", "test-model")
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
