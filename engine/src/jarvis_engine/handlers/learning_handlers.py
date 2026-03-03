"""Handler classes for continuous learning commands."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jarvis_engine.commands.learning_commands import (
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

    def __init__(self, root: Path, learning_engine: Any = None) -> None:
        self._root = root
        self._learning_engine = learning_engine

    def handle(self, cmd: LearnInteractionCommand) -> LearnInteractionResult:
        if self._learning_engine is None:
            logger.warning("LearnInteractionCommand dropped: learning engine not available (task=%s)", cmd.task_id)
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
            logger.info("Learned %d record(s) from interaction (task=%s)", records, cmd.task_id)
        elif error:
            logger.warning("Learning produced 0 records (task=%s): %s", cmd.task_id, error)
        return LearnInteractionResult(
            records_created=records,
            message=error or "ok",
        )


class CrossBranchQueryHandler:
    """Delegates CrossBranchQueryCommand to cross_branch_query function."""

    def __init__(
        self,
        root: Path,
        engine: Any = None,
        kg: Any = None,
        embed_service: Any = None,
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

    def __init__(self, root: Path, kg: Any = None) -> None:
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
