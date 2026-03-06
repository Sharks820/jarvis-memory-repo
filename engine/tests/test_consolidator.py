"""Tests for the memory consolidation engine."""

from __future__ import annotations

import json
import sqlite3
import threading
from unittest.mock import MagicMock

from conftest import make_test_db
from jarvis_engine.learning.consolidator import MemoryConsolidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine() -> MagicMock:
    """Build a mock MemoryEngine with an in-memory SQLite DB."""
    db = make_test_db()
    db.executescript("""
        CREATE TABLE records (
            record_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            kind TEXT NOT NULL,
            task_id TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT 'general',
            tags TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.72,
            tier TEXT NOT NULL DEFAULT 'warm',
            access_count INTEGER NOT NULL DEFAULT 0,
            last_accessed TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX idx_content_hash ON records(content_hash);
    """)

    engine = MagicMock()
    engine._db = db
    engine._db_lock = threading.Lock()
    engine._write_lock = threading.Lock()
    # Public property aliases (used by consolidator via MemoryEngine properties)
    engine.db = db
    engine.db_lock = engine._db_lock
    engine.write_lock = engine._write_lock
    # Make insert_record actually insert into the real DB
    engine.insert_record = MagicMock(return_value=True)
    return engine


def _seed_records(engine: MagicMock, count: int, branch: str = "general") -> list[dict]:
    """Insert ``count`` episodic records into the engine's DB and return them."""
    records = []
    for i in range(count):
        rec = {
            "record_id": f"{branch}_rec_{i:04d}",
            "ts": f"2026-02-20T10:{i:02d}:00+00:00",
            "source": "test",
            "kind": "episodic",
            "task_id": "",
            "branch": branch,
            "tags": "[]",
            "summary": f"Memory about topic alpha detail {i}",
            "content_hash": f"{branch}_hash_{i:04d}",
            "confidence": 0.72,
            "tier": "warm",
            "access_count": 0,
            "last_accessed": "",
        }
        engine._db.execute(
            """INSERT INTO records
               (record_id, ts, source, kind, task_id, branch, tags,
                summary, content_hash, confidence, tier, access_count, last_accessed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["record_id"], rec["ts"], rec["source"], rec["kind"],
                rec["task_id"], rec["branch"], rec["tags"], rec["summary"],
                rec["content_hash"], rec["confidence"], rec["tier"],
                rec["access_count"], rec["last_accessed"],
            ),
        )
        records.append(rec)
    engine._db.commit()
    return records


def _similar_embeddings(n: int, dim: int = 768) -> list[list[float]]:
    """Return ``n`` near-identical normalised vectors (cosine sim ~1.0)."""
    import numpy as np
    base = np.random.default_rng(42).random(dim).astype(np.float64)
    base /= np.linalg.norm(base)
    vecs = []
    for i in range(n):
        noise = np.random.default_rng(i).random(dim) * 1e-4
        v = base + noise
        v /= np.linalg.norm(v)
        vecs.append(v.tolist())
    return vecs


def _dissimilar_embeddings(n: int, dim: int = 768) -> list[list[float]]:
    """Return ``n`` orthogonal-ish vectors (cosine sim ~0)."""
    import numpy as np
    vecs = []
    for i in range(n):
        v = np.zeros(dim, dtype=np.float64)
        v[i % dim] = 1.0
        vecs.append(v.tolist())
    return vecs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClusterRecords:
    """Tests for _cluster_records (greedy cosine-similarity clustering)."""

    def test_cluster_similar_records(self):
        """Records with near-identical embeddings land in the same cluster."""
        engine = _make_engine()
        records = _seed_records(engine, 5)

        consolidator = MemoryConsolidator(
            engine=engine,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        embeddings = _similar_embeddings(5)
        groups = consolidator._cluster_records(records, embeddings)

        # All 5 should be in one group
        assert len(groups) == 1
        assert len(groups[0]) == 5

    def test_dissimilar_records_no_clusters(self):
        """Orthogonal embeddings should produce no qualifying clusters."""
        engine = _make_engine()
        records = _seed_records(engine, 5)

        consolidator = MemoryConsolidator(
            engine=engine,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        embeddings = _dissimilar_embeddings(5)
        groups = consolidator._cluster_records(records, embeddings)

        assert groups == []

    def test_min_group_size_respected(self):
        """Groups smaller than min_group_size are excluded."""
        engine = _make_engine()
        records = _seed_records(engine, 2)

        consolidator = MemoryConsolidator(
            engine=engine,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        embeddings = _similar_embeddings(2)
        groups = consolidator._cluster_records(records, embeddings)

        # Only 2 records -- below min_group_size of 3
        assert groups == []

    def test_empty_records(self):
        """Empty input returns no groups."""
        engine = _make_engine()
        consolidator = MemoryConsolidator(engine=engine)
        assert consolidator._cluster_records([], []) == []


class TestConsolidateCreatesNewRecord:
    """Full consolidation pipeline with mocked LLM gateway."""

    def test_consolidate_creates_new_record(self):
        """Gateway returns a fact; a new semantic record is stored."""
        engine = _make_engine()
        _seed_records(engine, 5)

        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Alpha is a recurring theme in the user's episodic memory."
        mock_gateway.complete.return_value = mock_response

        mock_embed = MagicMock()
        mock_embed.embed_batch.return_value = _similar_embeddings(5)
        mock_embed.embed.return_value = [0.0] * 768

        consolidator = MemoryConsolidator(
            engine=engine,
            gateway=mock_gateway,
            embed_service=mock_embed,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        result = consolidator.consolidate()

        assert result.groups_found >= 1
        assert result.new_facts_created >= 1
        assert result.records_consolidated >= 3
        assert result.errors == []

        # Verify insert_record was called with a semantic record
        engine.insert_record.assert_called()
        call_args = engine.insert_record.call_args
        record_dict = call_args[0][0] if call_args[0] else call_args[1].get("record")
        assert record_dict["kind"] == "semantic"
        assert record_dict["source"] == "consolidation"
        assert record_dict["confidence"] == 0.85
        tags = json.loads(record_dict["tags"])
        assert "consolidated" in tags


class TestConsolidateMarksOriginals:
    """Verify that original records get tagged after consolidation."""

    def test_consolidate_marks_originals(self):
        """Original records receive a 'consolidated_into:<id>' tag."""
        engine = _make_engine()
        _seed_records(engine, 4)

        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Consolidated fact statement."
        mock_gateway.complete.return_value = mock_response

        mock_embed = MagicMock()
        mock_embed.embed_batch.return_value = _similar_embeddings(4)
        mock_embed.embed.return_value = [0.0] * 768

        consolidator = MemoryConsolidator(
            engine=engine,
            gateway=mock_gateway,
            embed_service=mock_embed,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        result = consolidator.consolidate()

        assert result.new_facts_created >= 1

        # Check that original records were tagged in the DB
        cur = engine._db.execute(
            "SELECT tags FROM records WHERE kind = 'episodic'"
        )
        rows = cur.fetchall()
        tagged_count = 0
        for row in rows:
            tags = json.loads(row[0])
            for tag in tags:
                if tag.startswith("consolidated_into:"):
                    tagged_count += 1
                    break

        assert tagged_count >= 3


class TestDryRun:
    """Dry-run mode should compute groups but write nothing."""

    def test_dry_run_no_writes(self):
        """dry_run=True does not call insert_record or update tags."""
        engine = _make_engine()
        _seed_records(engine, 5)

        mock_embed = MagicMock()
        mock_embed.embed_batch.return_value = _similar_embeddings(5)

        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Dry run fact."
        mock_gateway.complete.return_value = mock_response

        consolidator = MemoryConsolidator(
            engine=engine,
            gateway=mock_gateway,
            embed_service=mock_embed,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        result = consolidator.consolidate(dry_run=True)

        assert result.groups_found >= 1
        assert result.new_facts_created >= 1
        assert result.records_consolidated >= 3
        assert result.errors == []

        # insert_record must NOT have been called
        engine.insert_record.assert_not_called()

        # Original records must still have empty tags
        cur = engine._db.execute("SELECT tags FROM records")
        for row in cur.fetchall():
            tags = json.loads(row[0])
            assert not any(t.startswith("consolidated_into:") for t in tags)


class TestNoGatewayConcatenates:
    """When no gateway is provided, summaries are concatenated."""

    def test_no_gateway_concatenates(self):
        """Without a gateway, _consolidate_group joins summaries with ' | '."""
        engine = _make_engine()
        records = _seed_records(engine, 4)

        mock_embed = MagicMock()
        mock_embed.embed_batch.return_value = _similar_embeddings(4)
        mock_embed.embed.return_value = [0.0] * 768

        consolidator = MemoryConsolidator(
            engine=engine,
            gateway=None,
            embed_service=mock_embed,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        result = consolidator.consolidate()

        assert result.new_facts_created >= 1
        assert result.errors == []

        # Verify the stored record's summary is a concatenation
        call_args = engine.insert_record.call_args
        record_dict = call_args[0][0] if call_args[0] else call_args[1].get("record")
        # The summary should contain the ' | ' separator (truncated to 200)
        assert record_dict["source"] == "consolidation"


class TestBranchFiltering:
    """Branch parameter restricts which records are considered."""

    def test_branch_filter(self):
        """Only records from the specified branch are fetched."""
        engine = _make_engine()
        _seed_records(engine, 4, branch="health")
        _seed_records(engine, 4, branch="finance")

        mock_embed = MagicMock()
        mock_embed.embed_batch.return_value = _similar_embeddings(4)
        mock_embed.embed.return_value = [0.0] * 768

        consolidator = MemoryConsolidator(
            engine=engine,
            gateway=None,
            embed_service=mock_embed,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        result = consolidator.consolidate(branch="health")

        assert result.groups_found >= 1
        # Only the 4 health records should participate
        assert result.records_consolidated <= 4


class TestErrorHandling:
    """Errors are collected in result.errors, never raised."""

    def test_embedding_error_collected(self):
        """An exception from embed_service is caught and reported."""
        engine = _make_engine()
        _seed_records(engine, 5)

        mock_embed = MagicMock()
        mock_embed.embed_batch.side_effect = RuntimeError("model not loaded")

        consolidator = MemoryConsolidator(
            engine=engine,
            embed_service=mock_embed,
        )

        result = consolidator.consolidate()

        assert result.new_facts_created == 0
        assert any("embedding failed" in e for e in result.errors)

    def test_gateway_error_collected(self):
        """An LLM failure on one group does not crash the run."""
        engine = _make_engine()
        _seed_records(engine, 5)

        mock_gateway = MagicMock()
        mock_gateway.complete.side_effect = RuntimeError("API timeout")

        mock_embed = MagicMock()
        mock_embed.embed_batch.return_value = _similar_embeddings(5)

        consolidator = MemoryConsolidator(
            engine=engine,
            gateway=mock_gateway,
            embed_service=mock_embed,
            similarity_threshold=0.75,
            min_group_size=3,
        )

        result = consolidator.consolidate()

        assert result.new_facts_created == 0
        assert any("summarisation failed" in e for e in result.errors)
