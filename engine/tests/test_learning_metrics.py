"""Comprehensive tests for jarvis_engine.learning.metrics module.

Covers knowledge metrics capture with various KG and engine states,
error handling, and temporal distribution.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock

from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.memory.engine import MemoryEngine

from jarvis_engine.learning.metrics import capture_knowledge_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(total_records=0, branch_rows=None):
    """Create a mock MemoryEngine with configurable DB responses."""
    engine = MagicMock(spec=MemoryEngine)
    db = MagicMock()
    engine._db = db
    engine.db = db  # Public property alias
    engine.db_lock = MagicMock()  # Public property alias

    count_cursor = MagicMock()
    count_cursor.fetchone.return_value = (total_records,)

    branch_cursor = MagicMock()
    branch_cursor.fetchall.return_value = branch_rows or []

    def execute_side_effect(sql, *args):
        if "COUNT(*) FROM records" in sql:
            return count_cursor
        if "GROUP BY branch" in sql:
            return branch_cursor
        return MagicMock()

    db.execute.side_effect = execute_side_effect
    return engine


def _make_kg(nodes=0, edges=0, locked=0, temporal_rows=None, temporal_error=False):
    """Create a mock KnowledgeGraph with configurable counts."""
    kg = MagicMock(spec=KnowledgeGraph)
    kg.count_nodes.return_value = nodes
    kg.count_edges.return_value = edges
    kg.count_locked.return_value = locked

    db = MagicMock()
    kg.db = db

    if temporal_error:
        db.execute.side_effect = sqlite3.OperationalError("no such column: temporal_type")
    else:
        temporal_cursor = MagicMock()
        temporal_cursor.fetchall.return_value = temporal_rows or []
        db.execute.return_value = temporal_cursor

    return kg


# ---------------------------------------------------------------------------
# Basic capture tests
# ---------------------------------------------------------------------------

class TestCaptureKnowledgeMetrics:
    def test_basic_capture(self):
        kg = _make_kg(nodes=100, edges=250, locked=10)
        engine = _make_engine(total_records=500, branch_rows=[
            ("family", 120),
            ("health", 80),
            ("finance", 50),
        ])
        result = capture_knowledge_metrics(kg, engine)

        assert result["total_records"] == 500
        assert result["total_facts"] == 100
        assert result["total_edges"] == 250
        assert result["locked_facts"] == 10
        assert result["branches_populated"] == 3
        assert result["branch_distribution"]["family"] == 120
        assert result["branch_distribution"]["health"] == 80
        assert result["branch_distribution"]["finance"] == 50
        assert "captured_at" in result

    def test_captured_at_is_iso_format(self):
        kg = _make_kg()
        engine = _make_engine()
        result = capture_knowledge_metrics(kg, engine)
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(result["captured_at"])
        assert parsed is not None

    def test_temporal_distribution(self):
        kg = _make_kg(temporal_rows=[
            ("permanent", 50),
            ("recurring", 30),
            ("temporary", 10),
        ])
        engine = _make_engine()
        result = capture_knowledge_metrics(kg, engine)
        assert result["temporal_distribution"]["permanent"] == 50
        assert result["temporal_distribution"]["recurring"] == 30
        assert result["temporal_distribution"]["temporary"] == 10


# ---------------------------------------------------------------------------
# Null/None handling
# ---------------------------------------------------------------------------

class TestNullHandling:
    def test_none_kg(self):
        engine = _make_engine(total_records=100)
        result = capture_knowledge_metrics(None, engine)
        assert result["total_facts"] == 0
        assert result["total_edges"] == 0
        assert result["locked_facts"] == 0
        assert result["temporal_distribution"] == {}
        assert result["total_records"] == 100

    def test_none_engine(self):
        kg = _make_kg(nodes=50, edges=100, locked=5)
        result = capture_knowledge_metrics(kg, None)
        assert result["total_records"] == 0
        assert result["branches_populated"] == 0
        assert result["branch_distribution"] == {}
        assert result["total_facts"] == 50

    def test_both_none(self):
        result = capture_knowledge_metrics(None, None)
        assert result["total_records"] == 0
        assert result["total_facts"] == 0
        assert result["total_edges"] == 0
        assert result["locked_facts"] == 0
        assert result["branches_populated"] == 0
        assert result["branch_distribution"] == {}
        assert result["temporal_distribution"] == {}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_engine_db_exception(self):
        engine = MagicMock(spec=MemoryEngine)
        engine.db.execute.side_effect = sqlite3.OperationalError("DB locked")
        kg = _make_kg(nodes=10)

        result = capture_knowledge_metrics(kg, engine)
        # Should gracefully handle and return 0 for records
        assert result["total_records"] == 0
        assert result["total_facts"] == 10

    def test_kg_count_nodes_exception(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.count_nodes.side_effect = sqlite3.OperationalError("Graph corrupted")
        kg.count_edges.return_value = 50
        kg.count_locked.return_value = 5
        kg.db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
        engine = _make_engine()

        result = capture_knowledge_metrics(kg, engine)
        assert result["total_facts"] == 0
        assert result["total_edges"] == 50

    def test_kg_count_edges_exception(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.count_nodes.return_value = 100
        kg.count_edges.side_effect = sqlite3.OperationalError("Edge table missing")
        kg.count_locked.return_value = 5
        kg.db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
        engine = _make_engine()

        result = capture_knowledge_metrics(kg, engine)
        assert result["total_facts"] == 100
        assert result["total_edges"] == 0

    def test_kg_count_locked_exception(self):
        kg = MagicMock(spec=KnowledgeGraph)
        kg.count_nodes.return_value = 100
        kg.count_edges.return_value = 250
        kg.count_locked.side_effect = sqlite3.OperationalError("Lock table missing")
        kg.db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
        engine = _make_engine()

        result = capture_knowledge_metrics(kg, engine)
        assert result["locked_facts"] == 0

    def test_temporal_column_missing(self):
        kg = _make_kg(nodes=10, edges=20, locked=1, temporal_error=True)
        engine = _make_engine()

        result = capture_knowledge_metrics(kg, engine)
        assert result["temporal_distribution"] == {}
        # Other fields should still work
        assert result["total_facts"] == 10


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_database(self):
        kg = _make_kg(nodes=0, edges=0, locked=0)
        engine = _make_engine(total_records=0)
        result = capture_knowledge_metrics(kg, engine)
        assert result["total_records"] == 0
        assert result["total_facts"] == 0
        assert result["branches_populated"] == 0

    def test_single_branch(self):
        kg = _make_kg()
        engine = _make_engine(total_records=10, branch_rows=[("personal", 10)])
        result = capture_knowledge_metrics(kg, engine)
        assert result["branches_populated"] == 1
        assert result["branch_distribution"] == {"personal": 10}

    def test_many_branches(self):
        branches = [(f"branch_{i}", i * 10) for i in range(20)]
        kg = _make_kg()
        engine = _make_engine(total_records=sum(b[1] for b in branches), branch_rows=branches)
        result = capture_knowledge_metrics(kg, engine)
        assert result["branches_populated"] == 20

    def test_large_counts(self):
        kg = _make_kg(nodes=1_000_000, edges=5_000_000, locked=100_000)
        engine = _make_engine(total_records=2_000_000)
        result = capture_knowledge_metrics(kg, engine)
        assert result["total_records"] == 2_000_000
        assert result["total_facts"] == 1_000_000
        assert result["total_edges"] == 5_000_000

    def test_result_keys_complete(self):
        kg = _make_kg()
        engine = _make_engine()
        result = capture_knowledge_metrics(kg, engine)
        expected_keys = {
            "total_records", "total_facts", "total_edges", "locked_facts",
            "branches_populated", "branch_distribution", "temporal_distribution",
            "captured_at",
        }
        assert set(result.keys()) == expected_keys
