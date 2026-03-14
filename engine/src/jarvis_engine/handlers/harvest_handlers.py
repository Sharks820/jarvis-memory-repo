"""Handler classes for knowledge harvesting commands."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jarvis_engine._constants import SUBSYSTEM_ERRORS_DB
from jarvis_engine.commands.harvest_commands import (
    HarvestBudgetCommand,
    HarvestBudgetResult,
    HarvestTopicCommand,
    HarvestTopicResult,
    IngestSessionCommand,
    IngestSessionResult,
)

if TYPE_CHECKING:
    from jarvis_engine.harvesting.budget import BudgetManager
    from jarvis_engine.harvesting.harvester import KnowledgeHarvester
    from jarvis_engine.memory.ingest import EnrichedIngestPipeline

logger = logging.getLogger(__name__)


class HarvestTopicHandler:
    """Delegates HarvestTopicCommand to KnowledgeHarvester."""

    def __init__(self, harvester: KnowledgeHarvester | None = None) -> None:
        self._harvester = harvester

    def handle(self, cmd: HarvestTopicCommand) -> HarvestTopicResult:
        if self._harvester is None:
            return HarvestTopicResult(
                topic=cmd.topic,
                return_code=2,
                results=[{"status": "error", "error": "Harvester not available"}],
            )

        from jarvis_engine.harvesting.harvester import HarvestCommand

        harvest_cmd = HarvestCommand(
            topic=cmd.topic,
            providers=cmd.providers,
            max_tokens=cmd.max_tokens,
        )
        result = self._harvester.harvest(harvest_cmd)
        return HarvestTopicResult(
            topic=result.get("topic", cmd.topic),
            results=cast("list[dict[Any, Any]]", result.get("results", [])),
            return_code=0,
        )


class IngestSessionHandler:
    """Discovers and ingests session JSONL files through the pipeline."""

    def __init__(self, pipeline: EnrichedIngestPipeline | None = None) -> None:
        self._pipeline = pipeline

    def handle(self, cmd: IngestSessionCommand) -> IngestSessionResult:
        if self._pipeline is None:
            return IngestSessionResult(
                source=cmd.source,
                return_code=2,
            )

        from jarvis_engine.harvesting.session_ingestors import (
            ClaudeCodeIngestor,
            CodexIngestor,
        )

        # Create appropriate ingestor
        ingestor: Any
        if cmd.source == "claude":
            ingestor = ClaudeCodeIngestor()
        elif cmd.source == "codex":
            ingestor = CodexIngestor()
        else:
            return IngestSessionResult(
                source=cmd.source,
                return_code=1,
            )

        # Discover sessions
        if cmd.session_path:
            session_resolved = Path(cmd.session_path).resolve()
            home = Path.home().resolve()
            allowed_roots = [
                home / ".claude",
                home / ".codex",
            ]
            if os.name == "nt":
                appdata = home / "AppData"
                if appdata.exists():
                    allowed_roots.append(appdata)
            if not any(
                session_resolved == root or session_resolved.is_relative_to(root)
                for root in allowed_roots
            ):
                return IngestSessionResult(
                    source=cmd.source,
                    return_code=2,
                )
            sessions = [session_resolved]
        elif cmd.source == "claude":
            sessions = ingestor.find_sessions(project_path=cmd.project_path)
        else:
            sessions = ingestor.find_sessions()

        sessions_processed = 0
        total_records = 0

        for session_path in sessions:
            texts = ingestor.ingest_session(session_path)
            if not texts:
                continue
            sessions_processed += 1

            for text in texts:
                try:
                    inserted = self._pipeline.ingest(
                        source=f"session:{cmd.source}",
                        kind="semantic",
                        task_id=f"session:{session_path.name}",
                        content=text,
                        tags=["session", cmd.source],
                    )
                    total_records += len(inserted)
                except SUBSYSTEM_ERRORS_DB as exc:
                    logger.warning(
                        "Failed to ingest session chunk from %s: %s",
                        session_path.name,
                        exc,
                    )

        return IngestSessionResult(
            source=cmd.source,
            sessions_processed=sessions_processed,
            records_created=total_records,
            return_code=0,
        )


class HarvestBudgetHandler:
    """View or set harvest budget limits."""

    def __init__(self, budget_manager: BudgetManager | None = None) -> None:
        self._budget = budget_manager

    def handle(self, cmd: HarvestBudgetCommand) -> HarvestBudgetResult:
        if self._budget is None:
            return HarvestBudgetResult(
                summary={"error": "Budget manager not available"},
                return_code=2,
            )

        if cmd.action == "set":
            if cmd.provider and cmd.period and cmd.limit_usd is not None:
                self._budget.set_budget(
                    provider=cmd.provider,
                    period=cmd.period,
                    limit_usd=cmd.limit_usd,
                    limit_requests=cmd.limit_requests or 0,
                )
                return HarvestBudgetResult(
                    summary={
                        "action": "set",
                        "provider": cmd.provider,
                        "period": cmd.period,
                        "limit_usd": cmd.limit_usd,
                        "limit_requests": cmd.limit_requests or 0,
                    },
                    return_code=0,
                )
            return HarvestBudgetResult(
                summary={
                    "error": "provider, period, and limit_usd required for set action"
                },
                return_code=1,
            )

        # Default: status
        summary = self._budget.get_spend_summary(
            provider=cmd.provider,
        )
        return HarvestBudgetResult(
            summary=cast("dict[Any, Any]", summary),
            return_code=0,
        )


# Backward-compat alias
HarvestHandler = HarvestTopicHandler
