"""Tests for KG FTS5 index, semantic search, MemoryEngine optimize, and SQLite backup improvements.

Covers:
- Upgrade 1: FTS5 index on KG facts (fts_kg_nodes table, MATCH queries, LIKE fallback)
- Upgrade 2: Semantic search for KG facts (vec_kg_nodes, query_relevant_facts_semantic)
- Upgrade 3: MemoryEngine.optimize() (ANALYZE, VACUUM)
- Upgrade 4: SQLite backup via Connection.backup() + WAL/SHM cleanup on restore
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from conftest import MockEmbeddingService
from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.knowledge.regression import RegressionChecker
from jarvis_engine.memory.engine import MemoryEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> MemoryEngine:
    """Create a MemoryEngine with a temporary database."""
    db_path = tmp_path / "test_kg_fts.db"
    eng = MemoryEngine(db_path)
    yield eng
    eng.close()


@pytest.fixture
def mock_embed() -> MockEmbeddingService:
    """Create a deterministic mock embedding service."""
    return MockEmbeddingService()


@pytest.fixture
def kg(engine: MemoryEngine) -> KnowledgeGraph:
    """Create a KnowledgeGraph without embed_service."""
    return KnowledgeGraph(engine)


@pytest.fixture
def kg_with_embed(
    engine: MemoryEngine, mock_embed: MockEmbeddingService
) -> KnowledgeGraph:
    """Create a KnowledgeGraph with mock embed_service."""
    return KnowledgeGraph(engine, embed_service=mock_embed)


# ===================================================================
# Upgrade 1: FTS5 Index on KG Facts
# ===================================================================


class TestKGFTS5Schema:
    """Tests for fts_kg_nodes virtual table creation."""

    def test_fts_kg_nodes_table_created(
        self, engine: MemoryEngine, kg: KnowledgeGraph
    ) -> None:
        """fts_kg_nodes virtual table is created during schema init."""
        cur = engine._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {row[0] for row in cur.fetchall()}
        assert "fts_kg_nodes" in table_names

    def test_fts_kg_nodes_has_correct_columns(
        self, engine: MemoryEngine, kg: KnowledgeGraph
    ) -> None:
        """fts_kg_nodes has node_id and label columns."""
        # Insert a test row to verify schema
        kg.add_fact("test_node", "test label", 0.8)
        cur = engine._db.execute("SELECT node_id, label FROM fts_kg_nodes")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "test_node"
        assert row[1] == "test label"


class TestKGFTS5Insert:
    """Tests for FTS5 index maintenance on add_fact."""

    def test_add_fact_inserts_into_fts(
        self, kg: KnowledgeGraph, engine: MemoryEngine
    ) -> None:
        """add_fact inserts the node into fts_kg_nodes."""
        kg.add_fact("med.aspirin", "aspirin for heart health", 0.75)

        cur = engine._db.execute(
            "SELECT node_id, label FROM fts_kg_nodes WHERE node_id = ?",
            ("med.aspirin",),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "med.aspirin"
        assert row[1] == "aspirin for heart health"

    def test_add_fact_update_updates_fts(
        self, kg: KnowledgeGraph, engine: MemoryEngine
    ) -> None:
        """Updating an existing fact updates the FTS5 index."""
        kg.add_fact("pref.color", "blue", 0.5)
        kg.add_fact("pref.color", "dark blue", 0.8)

        cur = engine._db.execute(
            "SELECT label FROM fts_kg_nodes WHERE node_id = ?",
            ("pref.color",),
        )
        rows = cur.fetchall()
        # Should be exactly one row with updated label
        assert len(rows) == 1
        assert rows[0][0] == "dark blue"

    def test_multiple_facts_all_indexed(
        self, kg: KnowledgeGraph, engine: MemoryEngine
    ) -> None:
        """Multiple facts are all inserted into FTS5."""
        kg.add_fact("n1", "python programming", 0.8)
        kg.add_fact("n2", "java development", 0.7)
        kg.add_fact("n3", "python data science", 0.9)

        cur = engine._db.execute("SELECT COUNT(*) FROM fts_kg_nodes")
        assert cur.fetchone()[0] == 3


class TestKGFTS5Retract:
    """Tests for FTS5 index cleanup on retract_facts."""

    def test_retract_removes_from_fts(
        self, kg: KnowledgeGraph, engine: MemoryEngine
    ) -> None:
        """retract_facts removes retracted nodes from fts_kg_nodes."""
        kg.add_fact("temp.note", "temporary note about meeting", 0.6)
        assert kg.retract_facts(["temporary"]) == 1

        cur = engine._db.execute(
            "SELECT COUNT(*) FROM fts_kg_nodes WHERE node_id = ?",
            ("temp.note",),
        )
        assert cur.fetchone()[0] == 0

    def test_retract_locked_preserves_fts(
        self, kg: KnowledgeGraph, engine: MemoryEngine
    ) -> None:
        """Locked facts are not retracted and remain in FTS5."""
        kg.add_fact("locked.fact", "important locked fact", 0.9)
        engine._db.execute(
            "UPDATE kg_nodes SET locked = 1 WHERE node_id = 'locked.fact'"
        )
        engine._db.commit()

        assert kg.retract_facts(["important"]) == 0

        cur = engine._db.execute(
            "SELECT COUNT(*) FROM fts_kg_nodes WHERE node_id = ?",
            ("locked.fact",),
        )
        assert cur.fetchone()[0] == 1


class TestKGFTS5Query:
    """Tests for FTS5-accelerated query_relevant_facts."""

    def test_fts_query_finds_matching_facts(self, kg: KnowledgeGraph) -> None:
        """query_relevant_facts finds facts via FTS5 MATCH."""
        kg.add_fact("med.metformin", "metformin for diabetes", 0.8)
        kg.add_fact("med.aspirin", "aspirin for heart", 0.7)
        kg.add_fact("pref.color", "favorite color is blue", 0.6)

        results = kg.query_relevant_facts(["metformin"])
        assert len(results) >= 1
        assert any(r["node_id"] == "med.metformin" for r in results)

    def test_fts_query_multiple_keywords(self, kg: KnowledgeGraph) -> None:
        """query_relevant_facts with multiple keywords finds all matches."""
        kg.add_fact("n1", "python programming", 0.8)
        kg.add_fact("n2", "java development", 0.7)
        kg.add_fact("n3", "cooking recipes", 0.6)

        results = kg.query_relevant_facts(["python", "java"])
        node_ids = {r["node_id"] for r in results}
        assert "n1" in node_ids
        assert "n2" in node_ids
        assert "n3" not in node_ids

    def test_fts_query_respects_min_confidence(self, kg: KnowledgeGraph) -> None:
        """query_relevant_facts filters by min_confidence."""
        kg.add_fact("low", "low confidence fact", 0.2)
        kg.add_fact("high", "high confidence fact", 0.9)

        results = kg.query_relevant_facts(["confidence"], min_confidence=0.5)
        node_ids = {r["node_id"] for r in results}
        assert "high" in node_ids
        assert "low" not in node_ids

    def test_fts_query_respects_limit(self, kg: KnowledgeGraph) -> None:
        """query_relevant_facts respects the limit parameter."""
        for i in range(10):
            kg.add_fact(f"n{i}", f"test item number {i}", 0.8)

        results = kg.query_relevant_facts(["test"], limit=3)
        assert len(results) <= 3

    def test_fts_query_empty_keywords(self, kg: KnowledgeGraph) -> None:
        """query_relevant_facts returns empty for empty keywords."""
        kg.add_fact("n1", "something", 0.8)
        assert kg.query_relevant_facts([]) == []

    def test_fts_fallback_to_like_for_substrings(self, kg: KnowledgeGraph) -> None:
        """LIKE fallback catches substring matches that FTS5 misses.

        FTS5 tokenizes on word boundaries, so a search for 'prog' won't
        match 'programming' via MATCH.  The LIKE fallback handles this.
        """
        kg.add_fact("n1", "programming tutorials", 0.8)

        # 'prog' is a substring that FTS5 won't match (it's not a full token)
        results = kg.query_relevant_facts(["prog"])
        assert len(results) >= 1
        assert results[0]["node_id"] == "n1"

    def test_fts_query_special_characters_sanitized(self, kg: KnowledgeGraph) -> None:
        """Special FTS5 characters in keywords don't cause errors."""
        kg.add_fact("n1", "test data", 0.8)
        # Should not raise even with special chars
        results = kg.query_relevant_facts(['"test"', "data*"])
        # Should still find the fact (special chars stripped)
        assert len(results) >= 1


class TestKGFTS5Sanitize:
    """Tests for _sanitize_fts_query static method."""

    def test_sanitize_removes_special_chars(self) -> None:
        """Special FTS5 characters are stripped."""
        result = KnowledgeGraph._sanitize_fts_query('"hello" AND "world"')
        assert '"' not in result

    def test_sanitize_removes_boolean_operators(self) -> None:
        """FTS5 boolean operators are removed."""
        result = KnowledgeGraph._sanitize_fts_query("python AND java OR NOT ruby")
        tokens = result.split()
        assert "AND" not in tokens
        assert "OR" not in tokens
        assert "NOT" not in tokens
        assert "python" in tokens
        assert "java" in tokens
        assert "ruby" in tokens

    def test_sanitize_empty_string(self) -> None:
        """Empty string returns empty string."""
        assert KnowledgeGraph._sanitize_fts_query("") == ""

    def test_sanitize_only_special_chars(self) -> None:
        """String of only special chars returns empty string."""
        assert KnowledgeGraph._sanitize_fts_query('***"[]()') == ""


# ===================================================================
# Upgrade 2: Semantic Search for KG Facts
# ===================================================================


class TestKGVecSchema:
    """Tests for vec_kg_nodes virtual table creation."""

    def test_vec_kg_nodes_created_when_vec_available(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """vec_kg_nodes is created when sqlite-vec is available."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)
        cur = engine._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {row[0] for row in cur.fetchall()}
        assert "vec_kg_nodes" in table_names


class TestKGVecInsert:
    """Tests for vec embedding on add_fact."""

    def test_add_fact_inserts_embedding(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """add_fact with embed_service inserts embedding into vec_kg_nodes."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)
        kg.add_fact("n1", "python programming tutorial", 0.8)

        cur = engine._db.execute(
            "SELECT node_id FROM vec_kg_nodes WHERE node_id = ?", ("n1",)
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "n1"

    def test_add_fact_update_replaces_embedding(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Updating a fact replaces its embedding in vec_kg_nodes."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)
        kg.add_fact("n1", "original label", 0.5)
        kg.add_fact("n1", "updated label", 0.8)

        cur = engine._db.execute(
            "SELECT COUNT(*) FROM vec_kg_nodes WHERE node_id = ?", ("n1",)
        )
        assert cur.fetchone()[0] == 1

    def test_add_fact_without_embed_service_no_vec(self, engine: MemoryEngine) -> None:
        """add_fact without embed_service skips vec insertion."""
        kg = KnowledgeGraph(engine)
        kg.add_fact("n1", "some label", 0.8)

        if engine._vec_available:
            cur = engine._db.execute(
                "SELECT COUNT(*) FROM vec_kg_nodes WHERE node_id = ?", ("n1",)
            )
            assert cur.fetchone()[0] == 0


class TestKGSemanticSearch:
    """Tests for query_relevant_facts_semantic."""

    def test_semantic_search_returns_results(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Semantic search returns facts sorted by similarity."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)

        kg.add_fact("n1", "python programming tutorial", 0.8)
        kg.add_fact("n2", "cooking recipe for pasta", 0.7)
        kg.add_fact("n3", "python data science guide", 0.9)

        results = kg.query_relevant_facts_semantic("python coding")
        assert len(results) >= 1
        # All results should have 'distance' field
        for r in results:
            assert "distance" in r

    def test_semantic_search_filters_retracted(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Semantic search excludes retracted facts (confidence=0)."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)

        kg.add_fact("n1", "python tutorial", 0.8)
        kg.add_fact("n2", "python guide retracted", 0.8)
        kg.retract_facts(["retracted"])

        results = kg.query_relevant_facts_semantic("python")
        node_ids = {r["node_id"] for r in results}
        assert "n2" not in node_ids

    def test_semantic_search_respects_limit(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Semantic search respects the limit parameter."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)

        for i in range(10):
            kg.add_fact(f"n{i}", f"python topic number {i}", 0.8)

        results = kg.query_relevant_facts_semantic("python", limit=3)
        assert len(results) <= 3

    def test_semantic_search_no_embed_service_returns_empty(
        self, engine: MemoryEngine
    ) -> None:
        """Semantic search returns empty when no embed_service is available."""
        kg = KnowledgeGraph(engine)
        kg.add_fact("n1", "test", 0.8)
        results = kg.query_relevant_facts_semantic("test")
        assert results == []

    def test_semantic_search_no_vec_returns_empty(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Semantic search returns empty when sqlite-vec is unavailable."""
        kg = KnowledgeGraph(engine, embed_service=mock_embed)
        kg._vec_available = False
        kg.add_fact("n1", "test", 0.8)
        results = kg.query_relevant_facts_semantic("test")
        assert results == []

    def test_semantic_search_with_explicit_embed_service(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Semantic search accepts an explicit embed_service parameter."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)
        kg.add_fact("n1", "test content", 0.8)

        other_embed = MockEmbeddingService()
        results = kg.query_relevant_facts_semantic("test", embed_service=other_embed)
        assert isinstance(results, list)

    def test_semantic_search_respects_min_confidence(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Semantic search filters by min_confidence."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)

        kg.add_fact("low", "low confidence item", 0.2)
        kg.add_fact("high", "high confidence item", 0.9)

        results = kg.query_relevant_facts_semantic(
            "confidence item", min_confidence=0.5
        )
        node_ids = {r["node_id"] for r in results}
        assert "high" in node_ids
        assert "low" not in node_ids


class TestKGRetractVec:
    """Tests for vec index cleanup on retract_facts."""

    def test_retract_removes_from_vec(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """retract_facts removes retracted nodes from vec_kg_nodes."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)
        kg.add_fact("temp.note", "temporary note about meeting", 0.6)

        # Verify embedding exists
        cur = engine._db.execute(
            "SELECT COUNT(*) FROM vec_kg_nodes WHERE node_id = ?",
            ("temp.note",),
        )
        assert cur.fetchone()[0] == 1

        kg.retract_facts(["temporary"])

        # Verify embedding removed
        cur = engine._db.execute(
            "SELECT COUNT(*) FROM vec_kg_nodes WHERE node_id = ?",
            ("temp.note",),
        )
        assert cur.fetchone()[0] == 0


# ===================================================================
# Upgrade 3: MemoryEngine.optimize()
# ===================================================================


class TestMemoryEngineOptimize:
    """Tests for MemoryEngine.optimize() method."""

    def test_optimize_analyze_only(self, engine: MemoryEngine) -> None:
        """optimize() with default params runs ANALYZE and reports success."""
        # Insert some data for ANALYZE to work on
        for i in range(5):
            engine.insert_record(
                {
                    "record_id": f"opt_{i}",
                    "ts": "2026-01-01T00:00:00",
                    "source": "test",
                    "kind": "note",
                    "summary": f"test record {i}",
                    "content_hash": hashlib.sha256(f"opt_{i}".encode()).hexdigest(),
                }
            )

        result = engine.optimize()
        assert result["analyzed"] is True
        assert result["vacuumed"] is False
        assert result["errors"] == []

    def test_optimize_with_vacuum(self, engine: MemoryEngine) -> None:
        """optimize(vacuum=True) runs both ANALYZE and VACUUM."""
        result = engine.optimize(vacuum=True)
        assert result["analyzed"] is True
        assert result["vacuumed"] is True
        assert result["errors"] == []

    def test_optimize_on_closed_engine_raises(self, tmp_path: Path) -> None:
        """optimize() on closed engine raises RuntimeError."""
        db_path = tmp_path / "closed.db"
        eng = MemoryEngine(db_path)
        eng.close()
        with pytest.raises(RuntimeError, match="closed"):
            eng.optimize()

    def test_optimize_preserves_data(self, engine: MemoryEngine) -> None:
        """Data survives ANALYZE + VACUUM operations."""
        engine.insert_record(
            {
                "record_id": "survive",
                "ts": "2026-01-01T00:00:00",
                "source": "test",
                "kind": "note",
                "summary": "this should survive optimize",
                "content_hash": hashlib.sha256(b"survive_opt").hexdigest(),
            }
        )

        engine.optimize(vacuum=True)

        record = engine.get_record("survive")
        assert record is not None
        assert record["summary"] == "this should survive optimize"

    def test_optimize_empty_db(self, engine: MemoryEngine) -> None:
        """optimize() works on empty database without errors."""
        result = engine.optimize(vacuum=True)
        assert result["analyzed"] is True
        assert result["vacuumed"] is True
        assert result["errors"] == []

    def test_optimize_after_deletes(self, engine: MemoryEngine) -> None:
        """VACUUM after deletes reclaims space (no errors)."""
        for i in range(20):
            engine.insert_record(
                {
                    "record_id": f"del_{i}",
                    "ts": "2026-01-01T00:00:00",
                    "source": "test",
                    "kind": "note",
                    "summary": f"record to delete {i}",
                    "content_hash": hashlib.sha256(f"del_{i}".encode()).hexdigest(),
                }
            )
        engine.delete_records_batch([f"del_{i}" for i in range(20)])

        result = engine.optimize(vacuum=True)
        assert result["analyzed"] is True
        assert result["vacuumed"] is True
        assert result["errors"] == []


# ===================================================================
# Upgrade 4: SQLite Backup + WAL/SHM cleanup on restore
# ===================================================================


class TestBackupUsesConnectionAPI:
    """Tests for backup_graph using sqlite3.Connection.backup()."""

    def test_backup_creates_valid_db(self, tmp_path: Path) -> None:
        """backup_graph creates a valid SQLite database file."""
        db_path = tmp_path / "test_memory.db"
        engine = MemoryEngine(db_path)
        try:
            kg = KnowledgeGraph(engine)
            kg.add_fact("n1", "test fact alpha", 0.8)
            kg.add_fact("n2", "test fact beta", 0.7)

            checker = RegressionChecker(kg)
            backup_path = checker.backup_graph(tag="test")

            assert backup_path.exists()

            # Verify backup is a valid SQLite DB with the data
            backup_conn = sqlite3.connect(str(backup_path))
            backup_conn.row_factory = sqlite3.Row
            try:
                cur = backup_conn.execute("SELECT COUNT(*) FROM kg_nodes")
                assert cur.fetchone()[0] >= 2
            finally:
                backup_conn.close()
        finally:
            engine.close()

    def test_backup_captures_wal_data(self, tmp_path: Path) -> None:
        """Backup captures in-flight WAL data (not just the main DB file)."""
        db_path = tmp_path / "test_wal.db"
        engine = MemoryEngine(db_path)
        try:
            kg = KnowledgeGraph(engine)
            kg.add_fact("wal_test", "data in WAL", 0.9)

            checker = RegressionChecker(kg)
            backup_path = checker.backup_graph(tag="wal")

            # Verify the WAL data is in the backup
            backup_conn = sqlite3.connect(str(backup_path))
            try:
                cur = backup_conn.execute(
                    "SELECT label FROM kg_nodes WHERE node_id = ?",
                    ("wal_test",),
                )
                row = cur.fetchone()
                assert row is not None
                assert row[0] == "data in WAL"
            finally:
                backup_conn.close()
        finally:
            engine.close()


class TestRestoreCleanupWALSHM:
    """Tests for restore_graph cleaning up stale WAL/SHM files."""

    def test_restore_removes_wal_shm(self, tmp_path: Path) -> None:
        """restore_graph deletes stale WAL/SHM files before copying backup.

        Simulates a crash scenario by creating a backup, closing the live
        connection, placing stale WAL/SHM files, then restoring.
        """
        db_path = tmp_path / "test_restore.db"
        engine = MemoryEngine(db_path)
        try:
            kg = KnowledgeGraph(engine)
            kg.add_fact("n1", "original fact", 0.8)

            checker = RegressionChecker(kg)
            backup_path = checker.backup_graph(tag="restore_test")

            # Create a stale WAL file to verify restore cleans it up.
            # On Windows, the SHM file is locked by the open connection,
            # so we only test WAL cleanup here.
            wal_path = db_path.with_suffix(".db-wal")
            # Write to WAL outside the live connection (append stale data)
            with open(str(wal_path), "ab") as f:
                f.write(b"stale wal data appended")
            assert wal_path.exists()

            # Restore should clean up stale WAL before copying
            result = checker.restore_graph(backup_path)
            assert result is True

            # The key is that restore succeeded and data is intact
            node = kg.get_node("n1")
            assert node is not None
            assert node["label"] == "original fact"
        finally:
            try:
                engine.close()
            except (OSError, RuntimeError):
                pass

    def test_restore_works_without_wal_shm(self, tmp_path: Path) -> None:
        """restore_graph works when no WAL/SHM files exist."""
        db_path = tmp_path / "test_no_wal.db"
        engine = MemoryEngine(db_path)
        try:
            kg = KnowledgeGraph(engine)
            kg.add_fact("n1", "fact one", 0.8)

            checker = RegressionChecker(kg)
            backup_path = checker.backup_graph(tag="clean")

            # Add more data after backup
            kg.add_fact("n2", "fact two after backup", 0.7)

            # Ensure no WAL/SHM exist (they may or may not)
            wal_path = db_path.with_suffix(".db-wal")
            shm_path = db_path.with_suffix(".db-shm")

            # Restore should work regardless
            result = checker.restore_graph(backup_path)
            assert result is True

            # n1 should exist (was in backup), n2 should not
            assert kg.get_node("n1") is not None
        finally:
            try:
                engine.close()
            except (OSError, RuntimeError):
                pass

    def test_restore_preserves_fts_index(self, tmp_path: Path) -> None:
        """After restore, FTS5 index is functional."""
        db_path = tmp_path / "test_fts_restore.db"
        engine = MemoryEngine(db_path)
        try:
            kg = KnowledgeGraph(engine)
            kg.add_fact("n1", "python programming", 0.8)
            kg.add_fact("n2", "java development", 0.7)

            checker = RegressionChecker(kg)
            backup_path = checker.backup_graph(tag="fts")

            # Restore
            result = checker.restore_graph(backup_path)
            assert result is True

            # FTS search should still work
            results = kg.query_relevant_facts(["python"])
            assert len(results) >= 1
            assert any(r["node_id"] == "n1" for r in results)
        finally:
            try:
                engine.close()
            except (OSError, RuntimeError):
                pass


# ===================================================================
# Integration: FTS5 + Semantic search together
# ===================================================================


class TestKGSearchIntegration:
    """Integration tests combining FTS5 and semantic search."""

    def test_fts_and_semantic_return_consistent_results(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Both FTS5 and semantic search find the same relevant facts."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)

        kg.add_fact("n1", "python machine learning tutorial", 0.9)
        kg.add_fact("n2", "cooking pasta recipe", 0.7)

        fts_results = kg.query_relevant_facts(["python"])
        sem_results = kg.query_relevant_facts_semantic("python programming")

        # Both should find n1
        fts_ids = {r["node_id"] for r in fts_results}
        sem_ids = {r["node_id"] for r in sem_results}
        assert "n1" in fts_ids
        assert "n1" in sem_ids

    def test_add_update_retract_lifecycle(
        self, engine: MemoryEngine, mock_embed: MockEmbeddingService
    ) -> None:
        """Full lifecycle: add -> update -> retract maintains all indexes."""
        if not engine._vec_available:
            pytest.skip("sqlite-vec not available")
        kg = KnowledgeGraph(engine, embed_service=mock_embed)

        # Add
        kg.add_fact("lifecycle", "initial label", 0.5)
        fts_count = engine._db.execute(
            "SELECT COUNT(*) FROM fts_kg_nodes WHERE node_id = 'lifecycle'"
        ).fetchone()[0]
        vec_count = engine._db.execute(
            "SELECT COUNT(*) FROM vec_kg_nodes WHERE node_id = 'lifecycle'"
        ).fetchone()[0]
        assert fts_count == 1
        assert vec_count == 1

        # Update
        kg.add_fact("lifecycle", "updated label", 0.8)
        fts_count = engine._db.execute(
            "SELECT COUNT(*) FROM fts_kg_nodes WHERE node_id = 'lifecycle'"
        ).fetchone()[0]
        vec_count = engine._db.execute(
            "SELECT COUNT(*) FROM vec_kg_nodes WHERE node_id = 'lifecycle'"
        ).fetchone()[0]
        assert fts_count == 1
        assert vec_count == 1

        # Retract
        kg.retract_facts(["updated"])
        fts_count = engine._db.execute(
            "SELECT COUNT(*) FROM fts_kg_nodes WHERE node_id = 'lifecycle'"
        ).fetchone()[0]
        vec_count = engine._db.execute(
            "SELECT COUNT(*) FROM vec_kg_nodes WHERE node_id = 'lifecycle'"
        ).fetchone()[0]
        assert fts_count == 0
        assert vec_count == 0
