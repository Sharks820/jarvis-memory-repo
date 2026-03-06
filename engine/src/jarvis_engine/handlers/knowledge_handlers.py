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
            return KnowledgeStatusResult(error="Knowledge graph not available.")

        try:
            from jarvis_engine.knowledge.regression import RegressionChecker
        except ImportError as exc:
            logger.warning("regression module not available: %s", exc)
            return KnowledgeStatusResult(error="Regression module not available.")

        metrics = RegressionChecker(self._kg).capture_metrics()
        return KnowledgeStatusResult(
            node_count=metrics.get("node_count", 0),
            edge_count=metrics.get("edge_count", 0),
            locked_count=metrics.get("locked_count", 0),
            pending_contradictions=self._kg.count_pending_contradictions(),
            graph_hash=metrics.get("graph_hash", ""),
        )


class ContradictionListHandler:
    def __init__(self, root: Path, kg: Any = None) -> None:
        self._root = root
        self._kg = kg

    def handle(self, cmd: ContradictionListCommand) -> ContradictionListResult:
        if self._kg is None:
            return ContradictionListResult(error="Knowledge graph not available.")

        try:
            from jarvis_engine.knowledge.contradictions import ContradictionManager
        except ImportError as exc:
            logger.warning("contradictions module not available: %s", exc)
            return ContradictionListResult(error="Contradictions module not available.")

        mgr = ContradictionManager(self._kg.db, self._kg.write_lock, self._kg.db_lock, kg=self._kg)
        contradictions = mgr.list_all(
            status=cmd.status if cmd.status else None,
            limit=min(cmd.limit, 500),
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

        try:
            from jarvis_engine.knowledge.contradictions import ContradictionManager
        except ImportError as exc:
            logger.warning("contradictions module not available: %s", exc)
            return ContradictionResolveResult(
                success=False,
                message="Contradictions module not available.",
            )

        mgr = ContradictionManager(self._kg.db, self._kg.write_lock, self._kg.db_lock, kg=self._kg)
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
                message="Knowledge graph not available.",
            )

        if cmd.action not in ("lock", "unlock"):
            return FactLockResult(
                success=False,
                node_id=cmd.node_id,
                message=f"Invalid action: {cmd.action!r}. Must be 'lock' or 'unlock'.",
            )

        try:
            from jarvis_engine.knowledge.locks import FactLockManager
        except ImportError as exc:
            logger.warning("locks module not available: %s", exc)
            return FactLockResult(
                success=False,
                node_id=cmd.node_id,
                message="Locks module not available.",
            )

        lock_mgr = FactLockManager(self._kg.db, self._kg.write_lock, self._kg.db_lock, kg=self._kg)
        if cmd.action == "lock":
            success = lock_mgr.owner_confirm_lock(cmd.node_id)
            return FactLockResult(
                success=success,
                node_id=cmd.node_id,
                locked=success,
                message="Fact locked." if success else "Fact already locked or not found.",
            )
        else:
            success = lock_mgr.unlock_fact(cmd.node_id)
            return FactLockResult(
                success=success,
                node_id=cmd.node_id,
                locked=not success,
                message="Fact unlocked." if success else "Fact already unlocked or not found.",
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

        try:
            from jarvis_engine.knowledge.regression import RegressionChecker
        except ImportError as exc:
            logger.warning("regression module not available: %s", exc)
            return KnowledgeRegressionResult(
                report={"status": "error", "message": "Regression module not available."},
            )

        checker = RegressionChecker(self._kg)
        current = checker.capture_metrics()

        if cmd.snapshot_path and cmd.snapshot_path.strip():
            snap_path = Path(cmd.snapshot_path).resolve()

            # Path traversal protection: must be within project root
            try:
                snap_path.relative_to(self._root.resolve())
            except ValueError:
                return KnowledgeRegressionResult(
                    report={
                        "status": "error",
                        "message": "Snapshot path must be within the project root.",
                        "current": current,
                    },
                )

            # Auto-detect .zip and switch to companion .json metadata
            if snap_path.suffix == ".zip":
                snap_path = snap_path.with_suffix(".json")

            # Load previous metrics from snapshot metadata
            try:
                meta = json.loads(snap_path.read_text(encoding="utf-8"))
                prev_metrics = meta.get("kg_metrics")
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load snapshot metadata from %s: %s", snap_path, exc)
                return KnowledgeRegressionResult(
                    report={
                        "status": "error",
                        "message": "Failed to load snapshot metadata.",
                        "current": current,
                    },
                )

            report = checker.compare(prev_metrics, current)
        else:
            # No snapshot path -- just return current metrics as baseline
            report = checker.compare(None, current)

        return KnowledgeRegressionResult(report=report)
