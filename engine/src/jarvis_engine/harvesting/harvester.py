"""Knowledge harvester orchestrator.

Coordinates multi-provider knowledge queries and ingests results through
the EnrichedIngestPipeline. Each provider is queried independently with
individual error handling so one failure does not block others.

Includes budget enforcement (per-provider daily/monthly limits) and
semantic deduplication (cosine > 0.92) to prevent near-duplicate content
from multiple providers polluting the knowledge base.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.gateway.costs import CostTracker
    from jarvis_engine.harvesting.budget import BudgetManager
    from jarvis_engine.memory.ingest import EnrichedIngestPipeline

logger = logging.getLogger(__name__)

# Semantic near-duplicate threshold (cosine similarity)
_DEDUP_COSINE_THRESHOLD = 0.92


@dataclass
class HarvestResult:
    """Result from a single provider query.

    Non-frozen so fields can be set incrementally during query().
    """

    provider: str
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class HarvestCommand:
    """Command to harvest knowledge about a topic.

    providers: list of provider names to query, or None for all available.
    """

    topic: str
    providers: list[str] | None = None
    max_tokens: int = 2048


class KnowledgeHarvester:
    """Orchestrates multi-provider knowledge harvesting.

    Queries each configured provider about a topic, logs costs, and ingests
    results through the enriched pipeline at lower confidence (0.50) to
    distinguish externally-harvested knowledge from owner-provided info.
    """

    SYSTEM_PROMPT = (
        "You are a knowledge extraction assistant. Given a topic, output "
        "factual, structured knowledge statements. Each statement should be "
        "a clear, verifiable fact. Avoid opinions, speculation, conversational "
        "filler, and disclaimers. Focus on concrete information: definitions, "
        "relationships, properties, dates, quantities, and processes."
    )

    def __init__(
        self,
        providers: list,
        pipeline: "EnrichedIngestPipeline | None" = None,
        cost_tracker: "CostTracker | None" = None,
        budget_manager: "BudgetManager | None" = None,
    ) -> None:
        self._providers = {p.name: p for p in providers}
        self._pipeline = pipeline
        self._cost_tracker = cost_tracker
        self._budget = budget_manager

    def available_providers(self) -> list[str]:
        """Return names of providers with valid API keys."""
        return [
            name
            for name, provider in self._providers.items()
            if provider.is_available
        ]

    def harvest(self, cmd: HarvestCommand) -> dict:
        """Harvest knowledge about a topic from multiple providers.

        Args:
            cmd: HarvestCommand with topic, optional provider filter, max_tokens.

        Returns:
            Dict with topic, results list (per-provider status, records, cost).
        """
        # Determine which providers to query
        if cmd.providers is not None:
            provider_names = [
                n for n in cmd.providers if n in self._providers
            ]
        else:
            provider_names = self.available_providers()

        results = []
        topic_tag = cmd.topic.lower().replace(" ", "_")[:50]

        # Track content hashes across providers for cross-provider dedup
        seen_hashes: set[str] = set()

        for name in provider_names:
            provider = self._providers[name]

            if not provider.is_available:
                results.append({
                    "provider": name,
                    "status": "unavailable",
                    "records_created": 0,
                    "cost_usd": 0.0,
                })
                continue

            # Budget check
            if self._budget is not None and not self._budget.can_spend(name):
                results.append({
                    "provider": name,
                    "status": "budget_exceeded",
                    "records_created": 0,
                    "cost_usd": 0.0,
                })
                continue

            try:
                result = provider.query(
                    topic=cmd.topic,
                    system_prompt=self.SYSTEM_PROMPT,
                    max_tokens=cmd.max_tokens,
                )

                # Record spend in budget manager
                if self._budget is not None:
                    self._budget.record_spend(
                        name, result.cost_usd, topic=cmd.topic,
                    )

                # Log cost
                if self._cost_tracker is not None:
                    self._cost_tracker.log(
                        model=result.model,
                        provider=result.provider,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        cost_usd=result.cost_usd,
                        route_reason=f"harvest:{cmd.topic}",
                    )

                # Ingest through pipeline at lower confidence
                records_created = 0
                if self._pipeline is not None and result.text:
                    # Semantic dedup: check for near-duplicate content
                    if self._is_near_duplicate(result.text, seen_hashes):
                        logger.info(
                            "Skipping semantic near-duplicate from %s",
                            name,
                        )
                        results.append({
                            "provider": name,
                            "status": "ok",
                            "records_created": 0,
                            "cost_usd": result.cost_usd,
                            "skipped_dedup": True,
                        })
                        continue

                    # Track this content hash for cross-provider dedup
                    content_hash = hashlib.sha256(
                        result.text.encode("utf-8"),
                    ).hexdigest()
                    seen_hashes.add(content_hash)

                    # Append confidence marker and ingest
                    content_with_confidence = (
                        f"{result.text}\n\n(confidence:0.50)"
                    )
                    inserted = self._pipeline.ingest(
                        source=f"harvest:{provider.name}",
                        kind="semantic",
                        task_id=f"harvest:{cmd.topic}",
                        content=content_with_confidence,
                        tags=["harvested", provider.name, topic_tag],
                    )
                    records_created = len(inserted)

                results.append({
                    "provider": name,
                    "status": "ok",
                    "records_created": records_created,
                    "cost_usd": result.cost_usd,
                })

            except Exception as exc:
                logger.warning(
                    "Harvest from %s failed: %s", name, exc,
                )
                results.append({
                    "provider": name,
                    "status": "error",
                    "error": str(exc),
                    "records_created": 0,
                    "cost_usd": 0.0,
                })

        return {
            "topic": cmd.topic,
            "results": results,
        }

    def _is_near_duplicate(self, text: str, seen_hashes: set[str]) -> bool:
        """Check if text is a near-duplicate of already-harvested content.

        Uses embedding cosine similarity (threshold > 0.92) when the pipeline
        has an embed service.  Falls back to SHA-256 exact match when no
        embedding service is available.

        Args:
            text: The harvested text to check.
            seen_hashes: Set of SHA-256 hashes of previously harvested texts.

        Returns:
            True if the text is a near-duplicate and should be skipped.
        """
        # SHA-256 exact dedup (always active)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_hash in seen_hashes:
            return True

        # Semantic dedup via embedding similarity
        if self._pipeline is not None:
            try:
                embed_service = getattr(self._pipeline, "_embed_service", None)
                engine = getattr(self._pipeline, "_engine", None)
                if embed_service is not None and engine is not None:
                    embedding = embed_service.embed(text, prefix="search_document")
                    # Search for similar existing content
                    similar = engine.search_by_vector(embedding, limit=3)
                    for record in similar:
                        score = record.get("score", 0.0)
                        if score > _DEDUP_COSINE_THRESHOLD:
                            return True
            except Exception as exc:
                logger.debug(
                    "Semantic dedup check failed, falling back to hash only: %s",
                    exc,
                )

        return False
