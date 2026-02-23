"""Knowledge harvester orchestrator.

Coordinates multi-provider knowledge queries and ingests results through
the EnrichedIngestPipeline. Each provider is queried independently with
individual error handling so one failure does not block others.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.gateway.costs import CostTracker
    from jarvis_engine.memory.ingest import EnrichedIngestPipeline

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._providers = {p.name: p for p in providers}
        self._pipeline = pipeline
        self._cost_tracker = cost_tracker

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

            try:
                result = provider.query(
                    topic=cmd.topic,
                    system_prompt=self.SYSTEM_PROMPT,
                    max_tokens=cmd.max_tokens,
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
