"""Comprehensive tests for the KnowledgeHarvester orchestrator.

Tests cover:
- Initialization and configuration
- Provider selection and availability filtering
- Harvest flow (single provider, multi-provider)
- Budget enforcement (budget_exceeded status, record_spend calls)
- Content deduplication (hash-based and semantic)
- Cost tracking integration
- Pipeline ingestion integration
- Error handling (provider failures, network errors)
- HarvestCommand / HarvestResult dataclasses
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from jarvis_engine.harvesting.harvester import (
    HarvestCommand,
    HarvestResult,
    KnowledgeHarvester,
    _DEDUP_COSINE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProvider:
    """Lightweight fake provider for testing the harvester orchestrator."""

    def __init__(
        self,
        name: str,
        available: bool = True,
        result_text: str = "some knowledge",
        cost_usd: float = 0.01,
        raise_on_query: Exception | None = None,
    ) -> None:
        self.name = name
        self._available = available
        self._result_text = result_text
        self._cost_usd = cost_usd
        self._raise_on_query = raise_on_query
        self.query_calls: list[dict] = []

    @property
    def is_available(self) -> bool:
        return self._available

    def query(
        self, topic: str, system_prompt: str, max_tokens: int = 2048
    ) -> HarvestResult:
        self.query_calls.append(
            {"topic": topic, "system_prompt": system_prompt, "max_tokens": max_tokens}
        )
        if self._raise_on_query is not None:
            raise self._raise_on_query
        return HarvestResult(
            provider=self.name,
            text=self._result_text,
            model=f"{self.name}-model",
            input_tokens=100,
            output_tokens=200,
            cost_usd=self._cost_usd,
        )


def _make_providers(*specs) -> list[FakeProvider]:
    """Create FakeProvider instances from (name, available) tuples or just names."""
    providers = []
    for spec in specs:
        if isinstance(spec, str):
            providers.append(FakeProvider(name=spec))
        else:
            providers.append(FakeProvider(name=spec[0], available=spec[1]))
    return providers


# ---------------------------------------------------------------------------
# HarvestResult / HarvestCommand Tests
# ---------------------------------------------------------------------------


class TestHarvestDataclasses:
    def test_harvest_result_defaults(self) -> None:
        """HarvestResult has sensible defaults for numeric fields."""
        r = HarvestResult(provider="test", text="data", model="m")
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.cost_usd == 0.0

    def test_harvest_result_fields_settable(self) -> None:
        """HarvestResult is non-frozen so fields can be set after creation."""
        r = HarvestResult(provider="test", text="data", model="m")
        r.cost_usd = 1.23
        assert r.cost_usd == 1.23

    def test_harvest_command_frozen(self) -> None:
        """HarvestCommand is frozen; fields cannot be reassigned."""
        cmd = HarvestCommand(topic="AI", providers=["a"])
        with pytest.raises(AttributeError):
            cmd.topic = "changed"  # type: ignore[misc]

    def test_harvest_command_defaults(self) -> None:
        """HarvestCommand defaults: providers=None, max_tokens=2048."""
        cmd = HarvestCommand(topic="python")
        assert cmd.providers is None
        assert cmd.max_tokens == 2048


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------


class TestHarvesterInit:
    def test_init_stores_providers_by_name(self) -> None:
        """Providers are accessible by name after init."""
        providers = _make_providers("alpha", "beta")
        h = KnowledgeHarvester(providers=providers)
        assert "alpha" in h._providers
        assert "beta" in h._providers

    def test_init_optional_deps_none(self) -> None:
        """Harvester works without pipeline, cost_tracker, or budget_manager."""
        h = KnowledgeHarvester(providers=[])
        assert h._pipeline is None
        assert h._cost_tracker is None
        assert h._budget is None

    def test_system_prompt_exists(self) -> None:
        """SYSTEM_PROMPT is a non-empty string."""
        assert isinstance(KnowledgeHarvester.SYSTEM_PROMPT, str)
        assert len(KnowledgeHarvester.SYSTEM_PROMPT) > 50


# ---------------------------------------------------------------------------
# available_providers Tests
# ---------------------------------------------------------------------------


class TestAvailableProviders:
    def test_returns_only_available(self) -> None:
        """available_providers filters out unavailable providers."""
        providers = [
            FakeProvider("a", available=True),
            FakeProvider("b", available=False),
            FakeProvider("c", available=True),
        ]
        h = KnowledgeHarvester(providers=providers)
        assert h.available_providers() == ["a", "c"]

    def test_returns_empty_when_all_unavailable(self) -> None:
        providers = [FakeProvider("x", available=False)]
        h = KnowledgeHarvester(providers=providers)
        assert h.available_providers() == []

    def test_returns_all_when_all_available(self) -> None:
        providers = _make_providers("p1", "p2", "p3")
        h = KnowledgeHarvester(providers=providers)
        assert len(h.available_providers()) == 3


# ---------------------------------------------------------------------------
# Harvest Flow Tests
# ---------------------------------------------------------------------------


class TestHarvestFlow:
    def test_harvest_single_provider_ok(self) -> None:
        """Harvest from a single specified provider succeeds."""
        providers = _make_providers("alpha")
        h = KnowledgeHarvester(providers=providers)
        cmd = HarvestCommand(topic="machine learning", providers=["alpha"])
        result = h.harvest(cmd)

        assert result["topic"] == "machine learning"
        assert len(result["results"]) == 1
        assert result["results"][0]["status"] == "ok"
        assert result["results"][0]["provider"] == "alpha"

    def test_harvest_all_providers_when_none_specified(self) -> None:
        """When cmd.providers is None, all available providers are queried."""
        providers = [
            FakeProvider("a", available=True),
            FakeProvider("b", available=True),
            FakeProvider("c", available=False),
        ]
        h = KnowledgeHarvester(providers=providers)
        cmd = HarvestCommand(topic="topic")  # providers=None -> all available
        result = h.harvest(cmd)

        queried = [r["provider"] for r in result["results"]]
        assert "a" in queried
        assert "b" in queried
        # "c" is unavailable, so not included in available_providers list
        assert "c" not in queried

    def test_harvest_skips_unavailable_provider(self) -> None:
        """Specified but unavailable providers return 'unavailable' status."""
        providers = [FakeProvider("x", available=False)]
        h = KnowledgeHarvester(providers=providers)
        cmd = HarvestCommand(topic="test", providers=["x"])
        result = h.harvest(cmd)

        assert len(result["results"]) == 1
        assert result["results"][0]["status"] == "unavailable"
        assert result["results"][0]["records_created"] == 0

    def test_harvest_ignores_unknown_provider(self) -> None:
        """Unknown provider names in cmd.providers are silently dropped."""
        providers = _make_providers("alpha")
        h = KnowledgeHarvester(providers=providers)
        cmd = HarvestCommand(topic="test", providers=["alpha", "does_not_exist"])
        result = h.harvest(cmd)

        assert len(result["results"]) == 1  # only alpha
        assert result["results"][0]["provider"] == "alpha"

    def test_harvest_records_created_without_pipeline(self) -> None:
        """Without a pipeline, records_created is always 0."""
        providers = _make_providers("alpha")
        h = KnowledgeHarvester(providers=providers, pipeline=None)
        cmd = HarvestCommand(topic="topic", providers=["alpha"])
        result = h.harvest(cmd)

        assert result["results"][0]["records_created"] == 0

    def test_harvest_topic_tag_truncation(self) -> None:
        """Topic tags are lowercased, spaces replaced, truncated to 50 chars."""
        long_topic = (
            "a very very very very long topic name that exceeds fifty characters"
        )
        providers = _make_providers("alpha")
        pipeline = MagicMock()
        pipeline.ingest.return_value = ["rec1"]
        h = KnowledgeHarvester(providers=providers, pipeline=pipeline)
        cmd = HarvestCommand(topic=long_topic, providers=["alpha"])
        h.harvest(cmd)

        # The tags list passed to pipeline.ingest includes the truncated topic_tag
        call_args = pipeline.ingest.call_args
        tags = (
            call_args[1].get("tags") or call_args[0][-1]
            if call_args[0]
            else call_args[1]["tags"]
        )
        topic_tag = [t for t in tags if t not in ["harvested", "alpha"]]
        assert len(topic_tag[0]) <= 50


# ---------------------------------------------------------------------------
# Budget Enforcement Tests
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    def test_budget_exceeded_skips_provider(self) -> None:
        """When budget.can_spend returns False, provider is skipped."""
        providers = _make_providers("alpha")
        budget = MagicMock()
        budget.can_spend.return_value = False
        h = KnowledgeHarvester(providers=providers, budget_manager=budget)
        cmd = HarvestCommand(topic="topic", providers=["alpha"])
        result = h.harvest(cmd)

        assert result["results"][0]["status"] == "budget_exceeded"
        assert result["results"][0]["cost_usd"] == 0.0
        # Provider should NOT have been queried
        assert len(providers[0].query_calls) == 0

    def test_budget_record_spend_called_on_success(self) -> None:
        """On successful query, budget.record_spend is called with cost."""
        providers = [FakeProvider("alpha", cost_usd=0.05)]
        budget = MagicMock()
        budget.can_spend.return_value = True
        h = KnowledgeHarvester(providers=providers, budget_manager=budget)
        cmd = HarvestCommand(topic="test topic", providers=["alpha"])
        h.harvest(cmd)

        budget.record_spend.assert_called_once_with("alpha", 0.05, topic="test topic")

    def test_no_budget_manager_skips_checks(self) -> None:
        """When no budget_manager, providers are queried without budget checks."""
        providers = _make_providers("alpha")
        h = KnowledgeHarvester(providers=providers, budget_manager=None)
        cmd = HarvestCommand(topic="test", providers=["alpha"])
        result = h.harvest(cmd)

        assert result["results"][0]["status"] == "ok"

    def test_budget_checked_per_provider(self) -> None:
        """Budget is checked individually per provider."""
        p1 = FakeProvider("allowed")
        p2 = FakeProvider("blocked")
        budget = MagicMock()
        budget.can_spend.side_effect = lambda name: name == "allowed"

        h = KnowledgeHarvester(providers=[p1, p2], budget_manager=budget)
        cmd = HarvestCommand(topic="topic", providers=["allowed", "blocked"])
        result = h.harvest(cmd)

        statuses = {r["provider"]: r["status"] for r in result["results"]}
        assert statuses["allowed"] == "ok"
        assert statuses["blocked"] == "budget_exceeded"


# ---------------------------------------------------------------------------
# Cost Tracking Tests
# ---------------------------------------------------------------------------


class TestCostTracking:
    def test_cost_tracker_log_called(self) -> None:
        """When cost_tracker is provided, log() is called with correct args."""
        providers = [FakeProvider("p", cost_usd=0.03)]
        cost_tracker = MagicMock()
        h = KnowledgeHarvester(providers=providers, cost_tracker=cost_tracker)
        cmd = HarvestCommand(topic="ai safety", providers=["p"])
        h.harvest(cmd)

        cost_tracker.log.assert_called_once()
        kwargs = cost_tracker.log.call_args[1]
        assert kwargs["provider"] == "p"
        assert kwargs["cost_usd"] == 0.03
        assert "harvest:ai safety" in kwargs["route_reason"]

    def test_no_cost_tracker_no_error(self) -> None:
        """When cost_tracker is None, harvest still works."""
        providers = _make_providers("p")
        h = KnowledgeHarvester(providers=providers, cost_tracker=None)
        cmd = HarvestCommand(topic="test", providers=["p"])
        result = h.harvest(cmd)
        assert result["results"][0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Pipeline Integration Tests
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def test_pipeline_ingest_called_with_content(self) -> None:
        """Pipeline.ingest is called with enriched content and correct args."""
        providers = [FakeProvider("alpha", result_text="fact about AI")]
        pipeline = MagicMock()
        pipeline.ingest.return_value = ["rec1"]
        h = KnowledgeHarvester(providers=providers, pipeline=pipeline)
        cmd = HarvestCommand(topic="artificial intelligence", providers=["alpha"])
        result = h.harvest(cmd)

        pipeline.ingest.assert_called_once()
        kwargs = pipeline.ingest.call_args[1]
        assert kwargs["source"] == "harvest:alpha"
        assert kwargs["kind"] == "semantic"
        assert "(confidence:0.50)" in kwargs["content"]
        assert "fact about AI" in kwargs["content"]
        assert result["results"][0]["records_created"] == 1

    def test_pipeline_not_called_when_result_text_empty(self) -> None:
        """If provider returns empty text, pipeline.ingest is NOT called."""
        providers = [FakeProvider("alpha", result_text="")]
        pipeline = MagicMock()
        h = KnowledgeHarvester(providers=providers, pipeline=pipeline)
        cmd = HarvestCommand(topic="test", providers=["alpha"])
        result = h.harvest(cmd)

        pipeline.ingest.assert_not_called()
        assert result["results"][0]["records_created"] == 0

    def test_pipeline_tags_include_provider_and_topic(self) -> None:
        """Tags include 'harvested', provider name, and topic tag."""
        providers = [FakeProvider("gemini", result_text="some data")]
        pipeline = MagicMock()
        pipeline.ingest.return_value = []
        h = KnowledgeHarvester(providers=providers, pipeline=pipeline)
        cmd = HarvestCommand(topic="quantum computing", providers=["gemini"])
        h.harvest(cmd)

        tags = pipeline.ingest.call_args[1]["tags"]
        assert "harvested" in tags
        assert "gemini" in tags
        assert "quantum_computing" in tags


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------


class TestHarvestErrorHandling:
    def test_provider_exception_caught(self) -> None:
        """Provider query exception is caught and reported as 'error' status."""
        providers = [FakeProvider("bad", raise_on_query=RuntimeError("API down"))]
        h = KnowledgeHarvester(providers=providers)
        cmd = HarvestCommand(topic="test", providers=["bad"])
        result = h.harvest(cmd)

        assert result["results"][0]["status"] == "error"
        assert "API down" in result["results"][0]["error"]
        assert result["results"][0]["records_created"] == 0
        assert result["results"][0]["cost_usd"] == 0.0

    def test_one_provider_error_does_not_block_others(self) -> None:
        """One provider failing does not prevent others from succeeding."""
        p_bad = FakeProvider("bad", raise_on_query=ValueError("timeout"))
        p_good = FakeProvider("good", result_text="good data")
        h = KnowledgeHarvester(providers=[p_bad, p_good])
        cmd = HarvestCommand(topic="test", providers=["bad", "good"])
        result = h.harvest(cmd)

        statuses = {r["provider"]: r["status"] for r in result["results"]}
        assert statuses["bad"] == "error"
        assert statuses["good"] == "ok"

    def test_network_error_as_exception(self) -> None:
        """Network-type errors (ConnectionError) are handled gracefully."""
        providers = [FakeProvider("p", raise_on_query=ConnectionError("no network"))]
        h = KnowledgeHarvester(providers=providers)
        cmd = HarvestCommand(topic="test", providers=["p"])
        result = h.harvest(cmd)

        assert result["results"][0]["status"] == "error"


# ---------------------------------------------------------------------------
# Deduplication Tests
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_exact_hash_dedup_across_providers(self) -> None:
        """Two providers returning identical text: second is deduplicated."""
        same_text = "identical knowledge about physics"
        p1 = FakeProvider("alpha", result_text=same_text)
        p2 = FakeProvider("beta", result_text=same_text)
        pipeline = MagicMock()
        pipeline.ingest.return_value = ["rec1"]
        h = KnowledgeHarvester(providers=[p1, p2], pipeline=pipeline)
        cmd = HarvestCommand(topic="test", providers=["alpha", "beta"])
        result = h.harvest(cmd)

        # First provider gets ingested; second is deduped
        results_dict = {r["provider"]: r for r in result["results"]}
        assert results_dict["alpha"]["records_created"] == 1
        assert results_dict["beta"].get("skipped_dedup") is True
        assert results_dict["beta"]["records_created"] == 0

    def test_different_texts_both_ingested(self) -> None:
        """Two providers returning different text: both get ingested."""
        p1 = FakeProvider("alpha", result_text="alpha knowledge")
        p2 = FakeProvider("beta", result_text="beta knowledge")
        pipeline = MagicMock()
        pipeline.ingest.return_value = ["rec1"]
        h = KnowledgeHarvester(providers=[p1, p2], pipeline=pipeline)
        cmd = HarvestCommand(topic="test", providers=["alpha", "beta"])
        result = h.harvest(cmd)

        results_dict = {r["provider"]: r for r in result["results"]}
        assert results_dict["alpha"]["records_created"] == 1
        assert results_dict["beta"]["records_created"] == 1

    def test_is_near_duplicate_exact_match(self) -> None:
        """_is_near_duplicate returns True for exact SHA-256 hash match."""
        h = KnowledgeHarvester(providers=[])
        text = "some text"
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        seen = {text_hash}
        assert h._is_near_duplicate(text, seen) is True

    def test_is_near_duplicate_no_match(self) -> None:
        """_is_near_duplicate returns False for unseen text without pipeline."""
        h = KnowledgeHarvester(providers=[], pipeline=None)
        assert h._is_near_duplicate("new text", set()) is False

    def test_semantic_dedup_via_embed_service(self) -> None:
        """When pipeline has embed_service and engine, semantic dedup triggers."""
        # Mock the pipeline with embed_service and engine
        pipeline = MagicMock()
        embed_service = MagicMock()
        engine_mock = MagicMock()

        pipeline._embed_service = embed_service
        pipeline._engine = engine_mock

        # Simulate a very similar existing record (distance ~0 means cosine ~1.0)
        engine_mock.search_vec.return_value = [("existing_rec", 0.1)]  # L2 distance 0.1
        embed_service.embed.return_value = [0.0] * 768

        h = KnowledgeHarvester(providers=[], pipeline=pipeline)
        # With L2=0.1: similarity = 1.0 - (0.01/2) = 0.995 > 0.92 => duplicate
        assert h._is_near_duplicate("new text", set()) is True

    def test_semantic_dedup_below_threshold(self) -> None:
        """When embedding distance is large, text is not a duplicate."""
        pipeline = MagicMock()
        embed_service = MagicMock()
        engine_mock = MagicMock()

        pipeline._embed_service = embed_service
        pipeline._engine = engine_mock

        # Large L2 distance => low similarity => not duplicate
        engine_mock.search_vec.return_value = [("far_rec", 1.5)]
        embed_service.embed.return_value = [0.0] * 768

        h = KnowledgeHarvester(providers=[], pipeline=pipeline)
        # similarity = 1.0 - (2.25/2) = -0.125 < 0.92 => not duplicate
        assert h._is_near_duplicate("different text", set()) is False

    def test_semantic_dedup_exception_falls_back(self) -> None:
        """If embedding/search throws, fall back to hash-only (no duplicate)."""
        pipeline = MagicMock()
        pipeline._embed_service = MagicMock()
        pipeline._embed_service.embed.side_effect = RuntimeError("embed failed")
        pipeline._engine = MagicMock()

        h = KnowledgeHarvester(providers=[], pipeline=pipeline)
        # Should not raise; should return False (hash not in seen_hashes)
        assert h._is_near_duplicate("text", set()) is False

    def test_semantic_dedup_no_embed_service(self) -> None:
        """If pipeline lacks _embed_service, only hash dedup is used."""
        pipeline = MagicMock(spec=[])  # no attributes beyond MagicMock basics
        del pipeline._embed_service  # ensure getattr returns None
        h = KnowledgeHarvester(providers=[], pipeline=pipeline)
        assert h._is_near_duplicate("text", set()) is False

    def test_dedup_threshold_constant(self) -> None:
        """Verify the threshold constant is 0.92."""
        assert _DEDUP_COSINE_THRESHOLD == 0.92


# ---------------------------------------------------------------------------
# Multi-Provider Ordering Tests
# ---------------------------------------------------------------------------


class TestMultiProviderOrdering:
    def test_providers_queried_in_order(self) -> None:
        """Providers are queried in the order specified in cmd.providers."""
        p1 = FakeProvider("first")
        p2 = FakeProvider("second")
        p3 = FakeProvider("third")
        h = KnowledgeHarvester(providers=[p1, p2, p3])
        cmd = HarvestCommand(topic="test", providers=["third", "first", "second"])
        result = h.harvest(cmd)

        provider_order = [r["provider"] for r in result["results"]]
        assert provider_order == ["third", "first", "second"]

    def test_empty_providers_list(self) -> None:
        """Empty providers list in command returns empty results."""
        h = KnowledgeHarvester(providers=_make_providers("alpha"))
        cmd = HarvestCommand(topic="test", providers=[])
        result = h.harvest(cmd)
        assert result["results"] == []

    def test_harvest_returns_topic_in_result(self) -> None:
        """Result dict always includes the original topic."""
        h = KnowledgeHarvester(providers=[])
        cmd = HarvestCommand(topic="quantum physics", providers=[])
        result = h.harvest(cmd)
        assert result["topic"] == "quantum physics"
