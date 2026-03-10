"""Handler classes for continuous learning commands."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from jarvis_engine.gateway.models import ModelGateway
    from jarvis_engine.knowledge.graph import KnowledgeGraph
    from jarvis_engine.learning.engine import ConversationLearningEngine
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

from jarvis_engine.commands.learning_commands import (
    ConsolidateMemoryCommand,
    ConsolidateMemoryResult,
    CrossBranchQueryCommand,
    CrossBranchQueryResult,
    FlagExpiredFactsCommand,
    FlagExpiredFactsResult,
    LearnInteractionCommand,
    LearnInteractionResult,
)

logger = logging.getLogger(__name__)


class LearnInteractionHandler:
    """Delegates LearnInteractionCommand to ConversationLearningEngine."""

    def __init__(
        self, root: Path, learning_engine: Optional[ConversationLearningEngine] = None
    ) -> None:
        self._root = root
        self._learning_engine = learning_engine

    def handle(self, cmd: LearnInteractionCommand) -> LearnInteractionResult:
        if self._learning_engine is None:
            logger.warning(
                "LearnInteractionCommand dropped: learning engine not available (task=%s)",
                cmd.task_id,
            )
            return LearnInteractionResult(
                message="Learning engine not available.",
            )

        result = self._learning_engine.learn_from_interaction(
            user_message=cmd.user_message,
            assistant_response=cmd.assistant_response,
            task_id=cmd.task_id,
            route=cmd.route,
            topic=cmd.topic,
        )
        records = result.get("records_created", 0)
        error = result.get("error", "")
        if records > 0:
            logger.info(
                "Learned %d record(s) from interaction (task=%s)", records, cmd.task_id
            )
        elif error:
            logger.warning(
                "Learning produced 0 records (task=%s): %s", cmd.task_id, error
            )
        return LearnInteractionResult(
            records_created=records,
            message=error or "ok",
        )


class CrossBranchQueryHandler:
    """Delegates CrossBranchQueryCommand to cross_branch_query function."""

    def __init__(
        self,
        root: Path,
        engine: Optional[MemoryEngine] = None,
        kg: Optional[KnowledgeGraph] = None,
        embed_service: Optional[EmbeddingService] = None,
    ) -> None:
        self._root = root
        self._engine = engine
        self._kg = kg
        self._embed_service = embed_service

    def handle(self, cmd: CrossBranchQueryCommand) -> CrossBranchQueryResult:
        if self._engine is None or self._kg is None or self._embed_service is None:
            return CrossBranchQueryResult(
                message="Cross-branch query requires engine, kg, and embed_service.",
            )

        try:
            from jarvis_engine.learning.cross_branch import cross_branch_query
        except ImportError as exc:
            logger.warning("cross_branch module not available: %s", exc)
            return CrossBranchQueryResult(message="Cross-branch module not available.")

        result = cross_branch_query(
            query=cmd.query,
            engine=self._engine,
            kg=self._kg,
            embed_service=self._embed_service,
            k=cmd.k,
        )
        return CrossBranchQueryResult(
            direct_results=result.get("direct_results", []),
            cross_branch_connections=result.get("cross_branch_connections", []),
            branches_involved=result.get("branches_involved", []),
            message="ok",
        )


class FlagExpiredFactsHandler:
    """Delegates FlagExpiredFactsCommand to flag_expired_facts function."""

    def __init__(self, root: Path, kg: Optional[KnowledgeGraph] = None) -> None:
        self._root = root
        self._kg = kg

    def handle(self, cmd: FlagExpiredFactsCommand) -> FlagExpiredFactsResult:
        if self._kg is None:
            return FlagExpiredFactsResult(
                message="Knowledge graph not available.",
            )

        try:
            from jarvis_engine.learning.temporal import flag_expired_facts
        except ImportError as exc:
            logger.warning("temporal module not available: %s", exc)
            return FlagExpiredFactsResult(message="Temporal module not available.")

        count = flag_expired_facts(self._kg)
        return FlagExpiredFactsResult(
            expired_count=count,
            message=f"Flagged {count} expired fact(s).",
        )


class ConsolidateMemoryHandler:
    """Delegates ConsolidateMemoryCommand to MemoryConsolidator."""

    def __init__(
        self,
        root: Path,
        engine: Optional[MemoryEngine] = None,
        gateway: Optional[ModelGateway] = None,
        embed_service: Optional[EmbeddingService] = None,
        kg: Optional[KnowledgeGraph] = None,
    ) -> None:
        self._root = root
        self._engine = engine
        self._gateway = gateway
        self._embed_service = embed_service
        self._kg = kg

    def handle(self, cmd: ConsolidateMemoryCommand) -> ConsolidateMemoryResult:
        if self._engine is None:
            return ConsolidateMemoryResult(message="MemoryEngine not available.")

        try:
            from jarvis_engine.learning.consolidator import MemoryConsolidator
        except ImportError as exc:
            logger.warning("Consolidator module not available: %s", exc)
            return ConsolidateMemoryResult(message="Consolidator module not available.")

        # Backup KG state before consolidation
        if self._kg is not None:
            try:
                from jarvis_engine.knowledge.regression import RegressionChecker

                rc_checker = RegressionChecker(self._kg)
                rc_checker.backup_graph(tag="pre-consolidation")
            except (sqlite3.Error, OSError) as exc:
                logger.warning("KG backup before consolidation failed: %s", exc)

        consolidator = MemoryConsolidator(
            self._engine,
            gateway=self._gateway,
            embed_service=self._embed_service,
        )
        result = consolidator.consolidate(
            branch=cmd.branch or None,
            max_groups=cmd.max_groups,
            dry_run=cmd.dry_run,
        )

        # Log to activity feed
        try:
            from jarvis_engine.activity_feed import log_activity, ActivityCategory

            log_activity(
                ActivityCategory.CONSOLIDATION,
                f"Memory consolidation: {result.new_facts_created} facts from {result.groups_found} groups",
                {
                    "groups_found": result.groups_found,
                    "records_consolidated": result.records_consolidated,
                    "new_facts_created": result.new_facts_created,
                },
            )
        except (ImportError, OSError) as exc:
            logger.debug("Activity feed logging for consolidation failed: %s", exc)

        return ConsolidateMemoryResult(
            groups_found=result.groups_found,
            records_consolidated=result.records_consolidated,
            new_facts_created=result.new_facts_created,
            errors=result.errors,
            message=(
                f"Consolidated {result.new_facts_created} facts from {result.groups_found} groups."
                if not result.errors
                else f"Consolidated {result.new_facts_created} facts with {len(result.errors)} error(s)."
            ),
        )
