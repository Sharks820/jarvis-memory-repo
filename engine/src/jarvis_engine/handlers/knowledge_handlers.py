"""Handler classes for knowledge graph commands."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jarvis_engine.commands.knowledge_commands import (
    ContradictionListCommand,
    ContradictionListResult,
    ContradictionResolveCommand,
    ContradictionResolveResult,
    FactLockCommand,
    FactLockResult,
    KnowledgeRegressionCommand,
    KnowledgeRegressionResult,
    KnowledgeStatusCommand,
    KnowledgeStatusResult,
)

logger = logging.getLogger(__name__)


class KnowledgeStatusHandler:
    def __init__(self, root: Path, kg: Any = None) -> None:
        self._root = root
        self._kg = kg

    def handle(self, cmd: KnowledgeStatusCommand) -> KnowledgeStatusResult:
        if self._kg is None:
            return KnowledgeStatusResult()

        from jarvis_engine.knowledge.regression import RegressionChecker

        metrics = RegressionChecker(self._kg).capture_metrics()
        return KnowledgeStatusResult(
            node_count=self._kg.count_nodes(),
            edge_count=self._kg.count_edges(),
            locked_count=self._kg.count_locked(),
            pending_contradictions=self._kg.count_pending_contradictions(),
            graph_hash=metrics.get("graph_hash", ""),
        )


class ContradictionListHandler:
    def __init__(self, root: Path, kg: Any = None) -> None:
        self._root = root
        self._kg = kg

    def handle(self, cmd: ContradictionListCommand) -> ContradictionListResult:
        if self._kg is None:
            return ContradictionListResult()

        from jarvis_engine.knowledge.contradictions import ContradictionManager

        mgr = ContradictionManager(self._kg._db, self._kg._write_lock)
        contradictions = mgr.list_all(
            status=cmd.status if cmd.status else None,
            limit=cmd.limit,
        )
        return ContradictionListResult(contradictions=contradictions)


class ContradictionResolveHandler:
    def __init__(self, root: Path, kg: Any = None) -> None:
        self._root = root
        self._kg = kg

    def handle(self, cmd: ContradictionResolveCommand) -> ContradictionResolveResult:
        if self._kg is None:
            return ContradictionResolveResult(
                success=False,
                message="Knowledge graph not available.",
            )

        from jarvis_engine.knowledge.contradictions import ContradictionManager

        mgr = ContradictionManager(self._kg._db, self._kg._write_lock)
        result = mgr.resolve(
            contradiction_id=cmd.contradiction_id,
            resolution=cmd.resolution,
            merge_value=cmd.merge_value,
        )
        return ContradictionResolveResult(
            success=result.get("success", False),
            node_id=result.get("node_id", ""),
            resolution=result.get("resolution", ""),
            message=result.get("message", ""),
        )


class FactLockHandler:
    def __init__(self, root: Path, kg: Any = None) -> None:
        self._root = root
        self._kg = kg

    def handle(self, cmd: FactLockCommand) -> FactLockResult:
        if self._kg is None:
            return FactLockResult(
                success=False,
                node_id=cmd.node_id,
            )

        from jarvis_engine.knowledge.locks import FactLockManager

        mgr = FactLockManager(self._kg._db, self._kg._write_lock)
        if cmd.action == "lock":
            success = mgr.owner_confirm_lock(cmd.node_id)
            return FactLockResult(
                success=success,
                node_id=cmd.node_id,
                locked=success,
            )
        elif cmd.action == "unlock":
            success = mgr.unlock_fact(cmd.node_id)
            return FactLockResult(
                success=success,
                node_id=cmd.node_id,
                locked=not success,
            )
        return FactLockResult(
            success=False,
            node_id=cmd.node_id,
            locked=False,
        )


class KnowledgeRegressionHandler:
    def __init__(self, root: Path, kg: Any = None) -> None:
        self._root = root
        self._kg = kg

    def handle(self, cmd: KnowledgeRegressionCommand) -> KnowledgeRegressionResult:
        if self._kg is None:
            return KnowledgeRegressionResult(
                report={"status": "error", "message": "Knowledge graph not available."},
            )

        from jarvis_engine.knowledge.regression import RegressionChecker

        checker = RegressionChecker(self._kg)
        current = checker.capture_metrics()

        if cmd.snapshot_path and cmd.snapshot_path.strip():
            # Load previous metrics from snapshot metadata
            try:
                meta = json.loads(
                    Path(cmd.snapshot_path).read_text(encoding="utf-8")
                )
                prev_metrics = meta.get("kg_metrics")
            except (json.JSONDecodeError, OSError) as exc:
                return KnowledgeRegressionResult(
                    report={
                        "status": "error",
                        "message": f"Failed to load snapshot: {exc}",
                        "current": current,
                    },
                )

            report = checker.compare(prev_metrics, current)
        else:
            # No snapshot path -- just return current metrics as baseline
            report = checker.compare(None, current)

        return KnowledgeRegressionResult(report=report)
