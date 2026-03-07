"""Comprehensive tests for harvesting providers and orchestrator.

All SDK calls are mocked -- no real HTTP requests are made.
Tests cover: provider queries, graceful degradation, orchestrator flow,
pipeline ingestion, cost logging, and provider filtering.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.gateway.costs import CostTracker
from jarvis_engine.harvesting.harvester import (
    HarvestCommand,
    HarvestResult,
    KnowledgeHarvester,
)
from jarvis_engine.harvesting.providers import (
    GeminiProvider,
    HarvesterProvider,
    KimiNvidiaProvider,
    KimiProvider,
    MiniMaxProvider,
)
from jarvis_engine.memory.ingest import EnrichedIngestPipeline


# ---------------------------------------------------------------------------
# Helpers: mock OpenAI response
# ---------------------------------------------------------------------------


def _make_openai_response(text="Knowledge facts", input_tokens=100, output_tokens=200):
    """Build a mock OpenAI chat completion response."""
    usage = MagicMock()
    usage.prompt_tokens = input_tokens
    usage.completion_tokens = output_tokens

    message = MagicMock()
    message.content = text

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_gemini_response(text="Gemini knowledge", input_tokens=50, output_tokens=150):
    """Build a mock Gemini generate_content response."""
    usage_metadata = MagicMock()
    usage_metadata.prompt_token_count = input_tokens
    usage_metadata.candidates_token_count = output_tokens

    response = MagicMock()
    response.text = text
    response.usage_metadata = usage_metadata
    return response


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


class TestMiniMaxProvider:
    """Test MiniMaxProvider query and cost calculation."""

    @patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"})
    def test_minimax_provider_query(self):
        """Inject mock client and verify MiniMaxProvider returns correct HarvestResult."""
        provider = MiniMaxProvider()
        assert provider._available is True

        mock_response = _make_openai_response(
            text="MiniMax facts about topic",
            input_tokens=100,
            output_tokens=200,
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        # Inject mock client directly (bypasses lazy OpenAI import)
        provider._client = mock_client

        result = provider.query("test topic", "system prompt")

        assert isinstance(result, HarvestResult)
        assert result.provider == "minimax"
        assert result.text == "MiniMax facts about topic"
        assert result.model == "MiniMax-M2.5"
        assert result.input_tokens == 100
        assert result.output_tokens == 200
        # Cost: (100 * 0.30 + 200 * 1.20) / 1_000_000
        expected_cost = (100 * 0.30 + 200 * 1.20) / 1_000_000
        assert abs(result.cost_usd - expected_cost) < 1e-10

        # Verify SDK was called correctly
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "MiniMax-M2.5"


class TestKimiProvider:
    """Test KimiProvider query with Moonshot pricing."""

    @patch.dict(os.environ, {"KIMI_API_KEY": "test-kimi-key"})
    def test_kimi_provider_query(self):
        """Inject mock client and verify KimiProvider returns correct HarvestResult."""
        provider = KimiProvider()
        assert provider._available is True

        mock_response = _make_openai_response(
            text="Kimi knowledge output",
            input_tokens=150,
            output_tokens=300,
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        provider._client = mock_client

        result = provider.query("chemistry basics", "system prompt")

        assert result.provider == "kimi"
        assert result.text == "Kimi knowledge output"
        assert result.model == "kimi-k2.5"
        assert result.input_tokens == 150
        assert result.output_tokens == 300
        # Cost: (150 * 0.60 + 300 * 2.50) / 1_000_000
        expected_cost = (150 * 0.60 + 300 * 2.50) / 1_000_000
        assert abs(result.cost_usd - expected_cost) < 1e-10


class TestKimiNvidiaProvider:
    """Test KimiNvidiaProvider with thinking disabled and free pricing."""

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-nvidia-key"})
    def test_kimi_nvidia_provider_query(self):
        """Verify extra_body with thinking disabled and cost=0."""
        provider = KimiNvidiaProvider()
        assert provider._available is True

        mock_response = _make_openai_response(
            text="NVIDIA NIM response",
            input_tokens=80,
            output_tokens=160,
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        provider._client = mock_client

        result = provider.query("physics topic", "system prompt")

        assert result.provider == "kimi_nvidia"
        assert result.text == "NVIDIA NIM response"
        assert result.cost_usd == 0.0  # Free tier

        # Verify extra_body was passed
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


class TestGeminiProvider:
    """Test GeminiProvider with google-genai SDK."""

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"})
    def test_gemini_provider_query(self):
        """Inject mock client and verify Gemini returns text with usage metadata."""
        provider = GeminiProvider()
        assert provider._available is True

        mock_response = _make_gemini_response(
            text="Gemini knowledge about topic",
            input_tokens=60,
            output_tokens=120,
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        # Inject mock client directly (bypasses lazy google-genai import)
        provider._client = mock_client

        result = provider.query("biology topic", "system prompt")

        assert result.provider == "gemini"
        assert result.text == "Gemini knowledge about topic"
        assert result.model == "gemini-2.5-flash"
        assert result.input_tokens == 60
        assert result.output_tokens == 120
        assert result.cost_usd == 0.0  # Free tier

        # Verify generate_content was called
        mock_client.models.generate_content.assert_called_once()


class TestProviderAvailability:
    """Test graceful degradation when API keys are missing or present."""

    def test_provider_unavailable_when_no_api_key(self):
        """All providers set _available=False when env var is unset, query() raises RuntimeError."""
        # Clear any environment variables that might be set
        env_overrides = {
            "MINIMAX_API_KEY": "",
            "KIMI_API_KEY": "",
            "NVIDIA_API_KEY": "",
            "GEMINI_API_KEY": "",
        }

        with patch.dict(os.environ, env_overrides, clear=False):
            # Remove keys entirely if they exist
            for key in env_overrides:
                os.environ.pop(key, None)

            providers = [MiniMaxProvider(), KimiProvider(), KimiNvidiaProvider(), GeminiProvider()]

            for p in providers:
                assert p._available is False, f"{p.name} should be unavailable"
                assert p.is_available is False, f"{p.name} is_available should be False"
                with pytest.raises(RuntimeError, match="not configured"):
                    p.query("test", "prompt")

    @patch.dict(os.environ, {
        "MINIMAX_API_KEY": "key1",
        "KIMI_API_KEY": "key2",
        "NVIDIA_API_KEY": "key3",
        "GEMINI_API_KEY": "key4",
    })
    def test_provider_available_with_api_key(self):
        """All providers set _available=True when env var is set."""
        providers = [MiniMaxProvider(), KimiProvider(), KimiNvidiaProvider(), GeminiProvider()]

        for p in providers:
            assert p._available is True, f"{p.name} should be available"
            assert p.is_available is True, f"{p.name} is_available should be True"


# ---------------------------------------------------------------------------
# Harvester orchestration tests
# ---------------------------------------------------------------------------


def _make_mock_provider(name: str, available: bool = True, text: str = "facts"):
    """Create a mock provider with configurable availability."""
    provider = MagicMock(spec=HarvesterProvider)
    provider.name = name
    provider.is_available = available
    provider.query.return_value = HarvestResult(
        provider=name,
        text=text,
        model=f"{name}-model",
        input_tokens=100,
        output_tokens=200,
        cost_usd=0.001,
    )
    return provider


class TestKnowledgeHarvester:
    """Test harvester orchestration, pipeline ingestion, and cost logging."""

    def test_harvest_queries_all_available_providers(self):
        """Harvester queries all available providers and aggregates results."""
        p1 = _make_mock_provider("provider_a")
        p2 = _make_mock_provider("provider_b")

        harvester = KnowledgeHarvester(providers=[p1, p2])
        cmd = HarvestCommand(topic="quantum computing")
        result = harvester.harvest(cmd)

        assert result["topic"] == "quantum computing"
        assert len(result["results"]) == 2
        assert result["results"][0]["provider"] == "provider_a"
        assert result["results"][0]["status"] == "ok"
        assert result["results"][1]["provider"] == "provider_b"
        assert result["results"][1]["status"] == "ok"

        # Both providers were queried
        p1.query.assert_called_once()
        p2.query.assert_called_once()

    def test_harvest_skips_unavailable_providers(self):
        """Only available providers are queried when providers=None."""
        p_available = _make_mock_provider("available_one", available=True)
        p_unavailable = _make_mock_provider("unavailable_one", available=False)

        harvester = KnowledgeHarvester(providers=[p_available, p_unavailable])
        cmd = HarvestCommand(topic="test topic")
        result = harvester.harvest(cmd)

        # Only the available provider should have been queried
        assert len(result["results"]) == 1
        assert result["results"][0]["provider"] == "available_one"
        assert result["results"][0]["status"] == "ok"

        p_available.query.assert_called_once()
        p_unavailable.query.assert_not_called()

    def test_harvest_individual_provider_failure_non_blocking(self):
        """One provider failure does not block others."""
        p_ok = _make_mock_provider("good_provider")
        p_fail = _make_mock_provider("bad_provider")
        p_fail.query.side_effect = RuntimeError("API error")

        harvester = KnowledgeHarvester(providers=[p_fail, p_ok])
        cmd = HarvestCommand(topic="test topic")
        result = harvester.harvest(cmd)

        assert len(result["results"]) == 2

        # Failed provider
        fail_result = result["results"][0]
        assert fail_result["provider"] == "bad_provider"
        assert fail_result["status"] == "error"
        assert "API error" in fail_result["error"]

        # Successful provider
        ok_result = result["results"][1]
        assert ok_result["provider"] == "good_provider"
        assert ok_result["status"] == "ok"

    def test_harvest_ingests_through_pipeline(self):
        """Harvested content is ingested via pipeline with correct source and tags."""
        p1 = _make_mock_provider("test_provider", text="Some knowledge facts")
        mock_pipeline = MagicMock(spec=EnrichedIngestPipeline)
        mock_pipeline.ingest.return_value = ["record_1", "record_2"]

        harvester = KnowledgeHarvester(providers=[p1], pipeline=mock_pipeline)
        cmd = HarvestCommand(topic="machine learning")
        result = harvester.harvest(cmd)

        # Pipeline was called
        mock_pipeline.ingest.assert_called_once()
        call_kwargs = mock_pipeline.ingest.call_args

        assert call_kwargs.kwargs["source"] == "harvest:test_provider"
        assert call_kwargs.kwargs["kind"] == "semantic"
        assert call_kwargs.kwargs["task_id"] == "harvest:machine learning"
        assert "harvested" in call_kwargs.kwargs["tags"]
        assert "test_provider" in call_kwargs.kwargs["tags"]

        # Records created count
        assert result["results"][0]["records_created"] == 2

    def test_harvest_logs_cost(self):
        """Cost tracker is called with correct model, provider, tokens, and route_reason."""
        p1 = _make_mock_provider("cost_provider")
        mock_cost_tracker = MagicMock(spec=CostTracker)

        harvester = KnowledgeHarvester(providers=[p1], cost_tracker=mock_cost_tracker)
        cmd = HarvestCommand(topic="cost test topic")
        harvester.harvest(cmd)

        mock_cost_tracker.log.assert_called_once()
        call_kwargs = mock_cost_tracker.log.call_args

        assert call_kwargs.kwargs["model"] == "cost_provider-model"
        assert call_kwargs.kwargs["provider"] == "cost_provider"
        assert call_kwargs.kwargs["input_tokens"] == 100
        assert call_kwargs.kwargs["output_tokens"] == 200
        assert call_kwargs.kwargs["cost_usd"] == 0.001
        assert call_kwargs.kwargs["route_reason"] == "harvest:cost test topic"

    def test_harvest_specific_providers_only(self):
        """When providers list is specified, only those providers are queried."""
        p1 = _make_mock_provider("minimax")
        p2 = _make_mock_provider("kimi")
        p3 = _make_mock_provider("gemini")

        harvester = KnowledgeHarvester(providers=[p1, p2, p3])
        cmd = HarvestCommand(topic="filtered topic", providers=["minimax"])
        result = harvester.harvest(cmd)

        assert len(result["results"]) == 1
        assert result["results"][0]["provider"] == "minimax"

        p1.query.assert_called_once()
        p2.query.assert_not_called()
        p3.query.assert_not_called()

    def test_available_providers_returns_configured_only(self):
        """available_providers() returns only providers whose API keys are set."""
        p_avail = _make_mock_provider("configured", available=True)
        p_unavail = _make_mock_provider("unconfigured", available=False)

        harvester = KnowledgeHarvester(providers=[p_avail, p_unavail])
        available = harvester.available_providers()

        assert available == ["configured"]
        assert "unconfigured" not in available
