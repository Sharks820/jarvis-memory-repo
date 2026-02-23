"""Memory handler classes -- dual-path: MemoryEngine when available, adapter shim fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from jarvis_engine.commands.memory_commands import (
    BrainCompactCommand,
    BrainCompactResult,
    BrainContextCommand,
    BrainContextResult,
    BrainRegressionCommand,
    BrainRegressionResult,
    BrainStatusCommand,
    BrainStatusResult,
    IngestCommand,
    IngestResult,
    MemoryMaintenanceCommand,
    MemoryMaintenanceResult,
    MemorySnapshotCommand,
    MemorySnapshotResult,
)


class BrainStatusHandler:
    def __init__(self, root: Path, engine: Any = None) -> None:
        self._root = root
        self._engine = engine

    def handle(self, cmd: BrainStatusCommand) -> BrainStatusResult:
        if self._engine is not None:
            # Use MemoryEngine: query record counts from SQLite
            count = self._engine.count_records()
            return BrainStatusResult(status={
                "updated_utc": "",
                "branch_count": 0,
                "fact_count": 0,
                "total_records": count,
                "regression": {"status": "pass"},
                "branches": [],
                "engine": "sqlite",
            })
        from jarvis_engine.brain_memory import brain_status

        status = brain_status(self._root)
        return BrainStatusResult(status=status)


class BrainContextHandler:
    def __init__(self, root: Path, engine: Any = None, embed_service: Any = None) -> None:
        self._root = root
        self._engine = engine
        self._embed_service = embed_service

    def handle(self, cmd: BrainContextCommand) -> BrainContextResult:
        if self._engine is not None and self._embed_service is not None:
            # Use hybrid search from MemoryEngine
            from jarvis_engine.memory.search import hybrid_search

            query_embedding = self._embed_service.embed_query(cmd.query)
            if not query_embedding:
                return BrainContextResult(packet={"query": cmd.query, "selected": [], "error": "embedding failed"})
            results = hybrid_search(
                self._engine,
                cmd.query,
                query_embedding,
                k=max(1, min(cmd.max_items, 40)),
            )
            selected = []
            total_chars = 0
            max_chars = max(500, min(cmd.max_chars, 12000))
            for record in results:
                summary = str(record.get("summary", ""))
                if total_chars + len(summary) > max_chars:
                    break  # Stop collecting once budget exceeded (not skip)
                selected.append({
                    "record_id": record.get("record_id", ""),
                    "branch": record.get("branch", "general"),
                    "summary": summary,
                    "source": record.get("source", ""),
                    "kind": record.get("kind", ""),
                    "ts": record.get("ts", ""),
                    "score": 0.0,
                })
                total_chars += len(summary)
            return BrainContextResult(packet={
                "query": cmd.query,
                "selected": selected,
                "selected_count": len(selected),
                "canonical_facts": [],
                "max_items": cmd.max_items,
                "max_chars": cmd.max_chars,
                "total_records_scanned": self._engine.count_records(),
                "engine": "sqlite",
            })
        from jarvis_engine.brain_memory import build_context_packet

        packet = build_context_packet(
            self._root,
            query=cmd.query,
            max_items=max(1, min(cmd.max_items, 40)),
            max_chars=max(500, min(cmd.max_chars, 12000)),
        )
        return BrainContextResult(packet=packet)


class BrainCompactHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: BrainCompactCommand) -> BrainCompactResult:
        from jarvis_engine.brain_memory import brain_compact

        result = brain_compact(self._root, keep_recent=max(200, min(cmd.keep_recent, 50000)))
        return BrainCompactResult(result=result)


class BrainRegressionHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: BrainRegressionCommand) -> BrainRegressionResult:
        from jarvis_engine.brain_memory import brain_regression_report

        report = brain_regression_report(self._root)
        return BrainRegressionResult(report=report)


class IngestHandler:
    def __init__(self, root: Path, pipeline: Any = None) -> None:
        self._root = root
        self._pipeline = pipeline

    def handle(self, cmd: IngestCommand) -> IngestResult:
        if self._pipeline is not None:
            # Use EnrichedIngestPipeline (SQLite path)
            ids = self._pipeline.ingest(
                source=cmd.source,
                kind=cmd.kind,
                task_id=cmd.task_id,
                content=cmd.content,
            )
            record_id = ids[0] if ids else "deduped"
            return IngestResult(
                record_id=record_id,
                source=cmd.source,
                kind=cmd.kind,
                task_id=cmd.task_id,
            )
        from jarvis_engine.ingest import IngestionPipeline, MemoryKind, SourceType
        from jarvis_engine.memory_store import MemoryStore

        store = MemoryStore(self._root)
        pipeline = IngestionPipeline(store)
        record = pipeline.ingest(
            source=cast(SourceType, cmd.source),
            kind=cast(MemoryKind, cmd.kind),
            task_id=cmd.task_id,
            content=cmd.content,
        )
        return IngestResult(
            record_id=record.record_id,
            source=record.source,
            kind=record.kind,
            task_id=record.task_id,
        )


class MemorySnapshotHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MemorySnapshotCommand) -> MemorySnapshotResult:
        from jarvis_engine.memory_snapshots import create_signed_snapshot, verify_signed_snapshot

        if cmd.create:
            result = create_signed_snapshot(self._root, note=cmd.note)
            return MemorySnapshotResult(
                created=True,
                snapshot_path=str(result.snapshot_path),
                metadata_path=str(result.metadata_path),
                signature_path=str(result.signature_path),
                sha256=result.sha256,
                file_count=result.file_count,
            )
        if cmd.verify_path and cmd.verify_path.strip():
            target = Path(cmd.verify_path).resolve()
            try:
                target.relative_to(self._root.resolve())
            except ValueError:
                return MemorySnapshotResult(
                    verified=True, ok=False, reason="Path outside project root",
                )
            verification = verify_signed_snapshot(self._root, target)
            return MemorySnapshotResult(
                verified=True,
                ok=verification.ok,
                reason=verification.reason,
                expected_sha256=verification.expected_sha256,
                actual_sha256=verification.actual_sha256,
            )
        return MemorySnapshotResult()


class MemoryMaintenanceHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: MemoryMaintenanceCommand) -> MemoryMaintenanceResult:
        from jarvis_engine.memory_snapshots import run_memory_maintenance

        report = run_memory_maintenance(
            self._root,
            keep_recent=max(200, min(cmd.keep_recent, 50000)),
            snapshot_note=cmd.snapshot_note.strip()[:160],
        )
        return MemoryMaintenanceResult(report=report)
