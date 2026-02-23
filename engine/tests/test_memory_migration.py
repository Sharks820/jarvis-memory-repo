"""Tests for JSONL-to-SQLite migration with count verification.

All tests use MockEmbeddingService and temp directories.
Does NOT modify any existing test files.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jarvis_engine.memory.classify import BranchClassifier
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.memory.migration import (
    migrate_brain_records,
    migrate_events,
    migrate_facts,
    run_full_migration,
)


# ---------------------------------------------------------------------------
# Mock Embedding Service
# ---------------------------------------------------------------------------


class MockEmbeddingService:
    """Deterministic embedding service for testing."""

    def __init__(self, dim: int = 768) -> None:
        self._dim = dim
        self.embed_calls: list[str] = []

    def embed(self, text: str, prefix: str = "search_document") -> list[float]:
        self.embed_calls.append(text)
        seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16) / 1e8
        return [math.sin(seed + i * 0.1) for i in range(self._dim)]

    def embed_query(self, query: str) -> list[float]:
        return self.embed(query, prefix="search_query")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def engine(tmp_path: Path) -> MemoryEngine:
    db_path = tmp_path / "migration_test.db"
    eng = MemoryEngine(db_path)
    yield eng
    eng.close()


@pytest.fixture
def embed_service() -> MockEmbeddingService:
    return MockEmbeddingService()


@pytest.fixture
def classifier(embed_service: MockEmbeddingService) -> BranchClassifier:
    return BranchClassifier(embed_service)


def _make_brain_record(
    idx: int,
    summary: str | None = None,
    source: str = "user",
    kind: str = "episodic",
) -> dict:
    """Create a brain record dict matching the JSONL format."""
    summary = summary or f"Test brain record number {idx} with some content"
    content_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    return {
        "record_id": hashlib.sha256(f"rec-{idx}".encode()).hexdigest()[:16],
        "ts": datetime.now(UTC).isoformat(),
        "source": source,
        "kind": kind,
        "task_id": f"task-{idx}",
        "branch": "general",
        "tags": ["test"],
        "summary": summary,
        "content_hash": content_hash,
        "confidence": 0.72,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Write records as JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=True) + "\n")


# ---------------------------------------------------------------------------
# Migration Tests
# ---------------------------------------------------------------------------


class TestMigrateBrainRecords:

    def test_migrate_brain_records_count_verification(
        self,
        tmp_dir: Path,
        engine: MemoryEngine,
        embed_service: MockEmbeddingService,
        classifier: BranchClassifier,
    ) -> None:
        """Create temp JSONL with 10 records, migrate, verify inserted count matches."""
        records = [_make_brain_record(i) for i in range(10)]
        jsonl_path = tmp_dir / "records.jsonl"
        _write_jsonl(jsonl_path, records)

        result = migrate_brain_records(jsonl_path, engine, embed_service, classifier)
        assert result["status"] == "ok"
        assert result["source_count"] == 10
        assert result["inserted"] == 10
        assert result["skipped"] == 0
        assert result["errors"] == 0
        # Verify count: inserted + skipped + errors == source_count
        assert result["inserted"] + result["skipped"] + result["errors"] == result["source_count"]

    def test_migrate_brain_records_handles_malformed_json(
        self,
        tmp_dir: Path,
        engine: MemoryEngine,
        embed_service: MockEmbeddingService,
        classifier: BranchClassifier,
    ) -> None:
        """Include a malformed line in JSONL, verify it is skipped and counted in errors."""
        records = [_make_brain_record(i) for i in range(3)]
        jsonl_path = tmp_dir / "records.jsonl"
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(records[0]) + "\n")
            f.write("THIS IS NOT VALID JSON\n")  # Malformed line
            f.write(json.dumps(records[1]) + "\n")
            f.write("{not: valid}\n")  # Another malformed line
            f.write(json.dumps(records[2]) + "\n")

        result = migrate_brain_records(jsonl_path, engine, embed_service, classifier)
        assert result["status"] == "partial"  # Has both insertions and errors
        assert result["source_count"] == 5  # 5 non-empty lines
        assert result["inserted"] == 3
        assert result["errors"] == 2
        assert len(result["error_details"]) == 2

    def test_migrate_brain_records_preserves_all_fields(
        self,
        tmp_dir: Path,
        engine: MemoryEngine,
        embed_service: MockEmbeddingService,
        classifier: BranchClassifier,
    ) -> None:
        """Migrate a known record, verify all fields present in SQLite."""
        record = _make_brain_record(0, summary="Doctor appointment for annual checkup tomorrow")
        jsonl_path = tmp_dir / "records.jsonl"
        _write_jsonl(jsonl_path, [record])

        result = migrate_brain_records(jsonl_path, engine, embed_service, classifier)
        assert result["inserted"] == 1

        # Verify record is in SQLite with expected fields
        all_ids = engine.get_all_record_ids()
        assert len(all_ids) == 1
        stored = engine.get_record(all_ids[0])
        assert stored is not None
        assert stored["source"] == "user"
        assert stored["kind"] == "episodic"
        assert "Doctor appointment" in stored["summary"]
        assert stored["content_hash"] != ""
        assert stored["confidence"] > 0

    def test_migrate_brain_records_generates_embeddings(
        self,
        tmp_dir: Path,
        engine: MemoryEngine,
        embed_service: MockEmbeddingService,
        classifier: BranchClassifier,
    ) -> None:
        """Verify embed_service.embed was called for each record."""
        records = [_make_brain_record(i) for i in range(5)]
        jsonl_path = tmp_dir / "records.jsonl"
        _write_jsonl(jsonl_path, records)

        embed_service.embed_calls.clear()
        result = migrate_brain_records(jsonl_path, engine, embed_service, classifier)

        assert result["inserted"] == 5
        # embed() should be called at least once per record for the summary embedding
        # (classifier centroid calls add more, but at minimum 5 for records)
        summary_calls = [c for c in embed_service.embed_calls if "Test brain record" in c]
        assert len(summary_calls) == 5

    def test_migrate_brain_records_classifies_branches(
        self,
        tmp_dir: Path,
        engine: MemoryEngine,
        embed_service: MockEmbeddingService,
        classifier: BranchClassifier,
    ) -> None:
        """Verify migrated records have branches assigned by classifier."""
        records = [_make_brain_record(i) for i in range(3)]
        jsonl_path = tmp_dir / "records.jsonl"
        _write_jsonl(jsonl_path, records)

        result = migrate_brain_records(jsonl_path, engine, embed_service, classifier)
        assert result["inserted"] == 3

        # All records should have a non-empty branch
        for rid in engine.get_all_record_ids():
            stored = engine.get_record(rid)
            assert stored is not None
            assert stored["branch"] != ""
            # Branch should be one of the known branches or "general"
            from jarvis_engine.memory.classify import BRANCH_DESCRIPTIONS
            assert stored["branch"] in list(BRANCH_DESCRIPTIONS.keys()) + ["general"]


class TestMigrateFacts:

    def test_migrate_facts(
        self, tmp_dir: Path, engine: MemoryEngine
    ) -> None:
        """Create temp facts.json with 3 facts, migrate, verify all 3 in facts table."""
        facts_data = {
            "facts": {
                "runtime.safe_mode": {
                    "value": "enabled",
                    "confidence": 0.84,
                    "updated_utc": datetime.now(UTC).isoformat(),
                    "sources": ["rec1"],
                    "history": [],
                },
                "phone.spam_guard": {
                    "value": "enabled",
                    "confidence": 0.77,
                    "updated_utc": datetime.now(UTC).isoformat(),
                    "sources": ["rec2"],
                    "history": [],
                },
                "ops.daily_autopilot": {
                    "value": "preferred",
                    "confidence": 0.7,
                    "updated_utc": datetime.now(UTC).isoformat(),
                    "sources": ["rec3"],
                    "history": [],
                },
            },
            "conflicts": [],
        }
        facts_path = tmp_dir / "facts.json"
        facts_path.write_text(json.dumps(facts_data), encoding="utf-8")

        result = migrate_facts(facts_path, engine)
        assert result["status"] == "ok"
        assert result["source_count"] == 3
        assert result["inserted"] == 3
        assert result["errors"] == 0

        # Verify facts are in the database
        cur = engine._db.execute("SELECT COUNT(*) FROM facts")
        count = cur.fetchone()[0]
        assert count == 3

        # Verify a specific fact
        cur = engine._db.execute("SELECT value, confidence FROM facts WHERE key = ?", ("runtime.safe_mode",))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "enabled"
        assert abs(row[1] - 0.84) < 0.01


class TestFullMigration:

    def test_full_migration_returns_summary(
        self, tmp_dir: Path, embed_service: MockEmbeddingService
    ) -> None:
        """Run full migration on temp data, verify return dict has all expected keys."""
        # Set up directory structure
        brain_dir = tmp_dir / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)

        # Create brain records
        records = [_make_brain_record(i) for i in range(5)]
        _write_jsonl(brain_dir / "records.jsonl", records)

        # Create facts
        facts_data = {
            "facts": {
                "test.fact1": {
                    "value": "true",
                    "confidence": 0.9,
                    "updated_utc": datetime.now(UTC).isoformat(),
                    "sources": [],
                    "history": [],
                }
            },
            "conflicts": [],
        }
        (brain_dir / "facts.json").write_text(json.dumps(facts_data), encoding="utf-8")

        # Create events
        events = [
            {"event_type": "test", "message": f"Event {i}", "ts": datetime.now(UTC).isoformat()}
            for i in range(3)
        ]
        events_path = tmp_dir / ".planning" / "events.jsonl"
        _write_jsonl(events_path, events)

        db_path = brain_dir / "test_migration.db"
        result = run_full_migration(tmp_dir, db_path, embed_service)

        assert result["status"] == "ok"
        assert "brain" in result
        assert "facts" in result
        assert "events" in result
        assert "totals" in result
        assert "db_path" in result

        # Verify totals
        totals = result["totals"]
        assert totals["inserted"] == 5 + 1 + 3  # brain + facts + events
        assert totals["errors"] == 0
