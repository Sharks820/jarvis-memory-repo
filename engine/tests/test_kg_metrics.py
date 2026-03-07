"""Tests for KG integrity and growth metrics (proactive/kg_metrics.py)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from jarvis_engine.proactive.kg_metrics import (
    append_kg_metrics,
    collect_kg_metrics,
    kg_growth_trend,
    load_kg_history,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(
    *,
    nodes: list[tuple] | None = None,
    edges: list[tuple] | None = None,
    with_temporal: bool = False,
) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with kg_nodes/kg_edges tables and optional data."""
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE kg_nodes ("
        "  node_id TEXT PRIMARY KEY,"
        "  label TEXT NOT NULL,"
        "  node_type TEXT NOT NULL DEFAULT 'fact',"
        "  confidence REAL NOT NULL DEFAULT 0.5,"
        "  locked INTEGER NOT NULL DEFAULT 0,"
        "  locked_at TEXT DEFAULT NULL,"
        "  locked_by TEXT DEFAULT NULL,"
        "  sources TEXT NOT NULL DEFAULT '[]',"
        "  history TEXT NOT NULL DEFAULT '[]',"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    db.execute(
        "CREATE TABLE kg_edges ("
        "  edge_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  source_id TEXT NOT NULL,"
        "  target_id TEXT NOT NULL,"
        "  relation TEXT NOT NULL,"
        "  confidence REAL NOT NULL DEFAULT 0.5,"
        "  source_record TEXT DEFAULT NULL,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    if with_temporal:
        db.execute("ALTER TABLE kg_nodes ADD COLUMN temporal_type TEXT DEFAULT 'unknown'")

    if nodes:
        for n in nodes:
            if with_temporal and len(n) == 4:
                db.execute(
                    "INSERT INTO kg_nodes (node_id, label, confidence, locked, temporal_type) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (n[0], n[1], n[2], n[3], n[4] if len(n) > 4 else "unknown"),
                )
            else:
                db.execute(
                    "INSERT INTO kg_nodes (node_id, label, confidence, locked) "
                    "VALUES (?, ?, ?, ?)",
                    n[:4],
                )

    if edges:
        for e in edges:
            db.execute(
                "INSERT INTO kg_edges (source_id, target_id, relation, confidence) "
                "VALUES (?, ?, ?, ?)",
                e,
            )

    db.commit()
    return db


def _make_kg_mock(db: sqlite3.Connection) -> MagicMock:
    """Create a mock KG object with a .db property returning the given connection."""
    kg = MagicMock()
    type(kg).db = PropertyMock(return_value=db)
    return kg


# ---------------------------------------------------------------------------
# collect_kg_metrics
# ---------------------------------------------------------------------------

class TestCollectKgMetrics:
    """Tests for collect_kg_metrics()."""

    def test_basic_counts(self):
        """Node and edge counts are reported correctly."""
        nodes = [
            ("health.bp", "Blood pressure 120/80", 0.9, 1),
            ("coding.project", "Jarvis assistant", 0.7, 0),
            ("health.med", "Aspirin daily", 0.6, 0),
        ]
        edges = [
            ("health.bp", "health.med", "related_to", 0.8),
            ("coding.project", "health.bp", "cross_branch_related", 0.5),
        ]
        db = _make_db(nodes=nodes, edges=edges)
        kg = _make_kg_mock(db)

        m = collect_kg_metrics(kg)

        assert m["node_count"] == 3
        assert m["edge_count"] == 2
        assert m["cross_branch_edges"] == 1
        assert m["locked_facts"] == 1
        db.close()

    def test_branch_counts(self):
        """Branch grouping uses first segment of node_id before first dot."""
        nodes = [
            ("health.bp", "BP", 0.5, 0),
            ("health.med", "Med", 0.5, 0),
            ("coding.jarvis", "Jarvis", 0.5, 0),
        ]
        db = _make_db(nodes=nodes)
        kg = _make_kg_mock(db)

        m = collect_kg_metrics(kg)

        assert m["branch_counts"] == {"health": 2, "coding": 1}
        db.close()

    def test_confidence_distribution(self):
        """Confidence buckets are computed correctly (high >0.8, medium 0.5-0.8, low <0.5)."""
        nodes = [
            ("a.1", "A", 0.95, 0),   # high
            ("b.1", "B", 0.85, 0),   # high
            ("c.1", "C", 0.65, 0),   # medium
            ("d.1", "D", 0.3, 0),    # low
        ]
        db = _make_db(nodes=nodes)
        kg = _make_kg_mock(db)

        m = collect_kg_metrics(kg)

        assert m["confidence_distribution"]["high"] == 2
        assert m["confidence_distribution"]["medium"] == 1
        assert m["confidence_distribution"]["low"] == 1
        assert m["avg_confidence"] == pytest.approx((0.95 + 0.85 + 0.65 + 0.3) / 4, abs=0.002)
        db.close()

    def test_empty_db(self):
        """Empty tables return zero counts and empty collections."""
        db = _make_db()
        kg = _make_kg_mock(db)

        m = collect_kg_metrics(kg)

        assert m["node_count"] == 0
        assert m["edge_count"] == 0
        assert m["branch_counts"] == {}
        assert m["cross_branch_edges"] == 0
        assert m["avg_confidence"] == 0.0
        assert m["locked_facts"] == 0
        assert "ts" in m
        db.close()

    def test_missing_tables_graceful(self):
        """When kg tables do not exist, returns defaults without raising."""
        db = sqlite3.connect(":memory:")
        kg = _make_kg_mock(db)

        m = collect_kg_metrics(kg)

        assert m["node_count"] == 0
        assert m["edge_count"] == 0
        db.close()

    def test_temporal_breakdown_with_column(self):
        """When temporal_type column exists, breakdown is populated."""
        db = _make_db(with_temporal=True)
        # Insert nodes with various temporal types
        db.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence, locked, temporal_type) "
            "VALUES ('a.1', 'A', 0.5, 0, 'permanent')"
        )
        db.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence, locked, temporal_type) "
            "VALUES ('b.1', 'B', 0.5, 0, 'permanent')"
        )
        db.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence, locked, temporal_type) "
            "VALUES ('c.1', 'C', 0.5, 0, 'time_sensitive')"
        )
        db.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence, locked, temporal_type) "
            "VALUES ('d.1', 'D', 0.5, 0, 'expired')"
        )
        db.commit()
        kg = _make_kg_mock(db)

        m = collect_kg_metrics(kg)

        assert m["temporal_breakdown"]["permanent"] == 2
        assert m["temporal_breakdown"]["time_sensitive"] == 1
        assert m["temporal_breakdown"]["expired"] == 1
        assert m["expired_facts"] == 1
        db.close()

    def test_temporal_breakdown_without_column(self):
        """When temporal_type column is missing, temporal_breakdown keeps defaults."""
        db = _make_db(nodes=[("a.1", "A", 0.5, 0)])
        kg = _make_kg_mock(db)

        m = collect_kg_metrics(kg)

        # Should be default zeros -- column doesn't exist so try/except catches it
        assert m["temporal_breakdown"] == {
            "permanent": 0, "time_sensitive": 0, "expired": 0, "unknown": 0
        }
        db.close()

    def test_node_id_without_dot(self):
        """Nodes whose node_id has no dot get an empty-string branch (excluded)."""
        db = _make_db(nodes=[("nodot", "No dot node", 0.5, 0)])
        kg = _make_kg_mock(db)

        m = collect_kg_metrics(kg)

        assert m["node_count"] == 1
        # "nodot" has no dot, so SUBSTR returns "nodot" (INSTR finds dot in "nodot." at pos 6)
        # Actually: INSTR("nodot.", ".") = 6, SUBSTR("nodot", 1, 5) = "nodot"
        assert "nodot" in m["branch_counts"]
        db.close()


# ---------------------------------------------------------------------------
# append_kg_metrics / load_kg_history
# ---------------------------------------------------------------------------

class TestKgHistory:
    """Tests for JSONL persistence functions."""

    def test_append_and_load(self, tmp_path: Path):
        """Appended metrics are readable via load_kg_history."""
        history_path = tmp_path / "subdir" / "kg_history.jsonl"

        snap1 = {"ts": "2026-01-01T00:00:00", "node_count": 10}
        snap2 = {"ts": "2026-01-02T00:00:00", "node_count": 15}

        append_kg_metrics(snap1, history_path)
        append_kg_metrics(snap2, history_path)

        loaded = load_kg_history(history_path)
        assert len(loaded) == 2
        assert loaded[0]["node_count"] == 10
        assert loaded[1]["node_count"] == 15

    def test_load_with_limit(self, tmp_path: Path):
        """load_kg_history respects the limit parameter (returns last N)."""
        history_path = tmp_path / "kg_history.jsonl"

        for i in range(10):
            append_kg_metrics({"ts": f"2026-01-{i+1:02d}", "node_count": i}, history_path)

        loaded = load_kg_history(history_path, limit=3)
        assert len(loaded) == 3
        assert loaded[0]["node_count"] == 7
        assert loaded[1]["node_count"] == 8
        assert loaded[2]["node_count"] == 9

    def test_load_nonexistent_file(self, tmp_path: Path):
        """load_kg_history returns empty list for missing file."""
        result = load_kg_history(tmp_path / "does_not_exist.jsonl")
        assert result == []

    def test_load_with_corrupt_lines(self, tmp_path: Path):
        """Corrupt JSONL lines are skipped gracefully."""
        history_path = tmp_path / "kg_history.jsonl"
        history_path.write_text(
            '{"ts": "2026-01-01", "node_count": 5}\n'
            "NOT VALID JSON\n"
            '{"ts": "2026-01-02", "node_count": 10}\n',
            encoding="utf-8",
        )

        loaded = load_kg_history(history_path)
        assert len(loaded) == 2
        assert loaded[0]["node_count"] == 5
        assert loaded[1]["node_count"] == 10

    def test_append_creates_parent_dirs(self, tmp_path: Path):
        """append_kg_metrics creates intermediate directories."""
        deep_path = tmp_path / "a" / "b" / "c" / "kg_history.jsonl"
        append_kg_metrics({"ts": "now", "node_count": 1}, deep_path)
        assert deep_path.exists()
        loaded = load_kg_history(deep_path)
        assert len(loaded) == 1


# ---------------------------------------------------------------------------
# kg_growth_trend
# ---------------------------------------------------------------------------

class TestKgGrowthTrend:
    """Tests for trend analysis across history snapshots."""

    def test_insufficient_data(self):
        """Single snapshot returns insufficient_data trend."""
        result = kg_growth_trend([{"ts": "t1", "node_count": 10, "edge_count": 5}])
        assert result["trend"] == "insufficient_data"

    def test_empty_history(self):
        """Empty list returns insufficient_data trend."""
        result = kg_growth_trend([])
        assert result["trend"] == "insufficient_data"

    def test_growing_trend(self):
        """Positive node and edge growth returns 'growing'."""
        history = [
            {"ts": "t1", "node_count": 10, "edge_count": 5, "avg_confidence": 0.6, "cross_branch_edges": 1},
            {"ts": "t2", "node_count": 15, "edge_count": 8, "avg_confidence": 0.7, "cross_branch_edges": 3},
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "growing"
        assert result["node_growth"] == 5
        assert result["edge_growth"] == 3
        assert result["cross_branch_growth"] == 2
        assert result["confidence_change"] == 0.1
        assert result["snapshots_analyzed"] == 2

    def test_stable_trend(self):
        """No change in nodes or edges returns 'stable'."""
        history = [
            {"ts": "t1", "node_count": 10, "edge_count": 5, "avg_confidence": 0.7, "cross_branch_edges": 2},
            {"ts": "t2", "node_count": 10, "edge_count": 5, "avg_confidence": 0.7, "cross_branch_edges": 2},
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "stable"
        assert result["node_growth"] == 0
        assert result["edge_growth"] == 0

    def test_declining_trend(self):
        """Negative growth returns 'declining'."""
        history = [
            {"ts": "t1", "node_count": 20, "edge_count": 10, "avg_confidence": 0.8, "cross_branch_edges": 5},
            {"ts": "t2", "node_count": 15, "edge_count": 7, "avg_confidence": 0.6, "cross_branch_edges": 3},
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "declining"
        assert result["node_growth"] == -5
        assert result["edge_growth"] == -3
        assert result["confidence_change"] == -0.2

    def test_mixed_growth_classified_as_declining(self):
        """Nodes grew but edges shrank -- not both positive, so 'declining'."""
        history = [
            {"ts": "t1", "node_count": 10, "edge_count": 10},
            {"ts": "t2", "node_count": 15, "edge_count": 8},
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "declining"

    def test_multi_snapshot_uses_first_and_last(self):
        """Trend is computed from first and last snapshot, not adjacent pairs."""
        history = [
            {"ts": "t1", "node_count": 10, "edge_count": 5, "avg_confidence": 0.5, "cross_branch_edges": 0},
            {"ts": "t2", "node_count": 8, "edge_count": 3, "avg_confidence": 0.4, "cross_branch_edges": 0},
            {"ts": "t3", "node_count": 20, "edge_count": 12, "avg_confidence": 0.8, "cross_branch_edges": 4},
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "growing"
        assert result["node_growth"] == 10
        assert result["edge_growth"] == 7
        assert result["snapshots_analyzed"] == 3
        assert result["first_snapshot"] == "t1"
        assert result["last_snapshot"] == "t3"

    def test_missing_keys_default_to_zero(self):
        """Snapshots missing keys default to 0 for safe arithmetic."""
        history = [
            {"ts": "t1"},
            {"ts": "t2", "node_count": 5, "edge_count": 3},
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "growing"
        assert result["node_growth"] == 5
        assert result["edge_growth"] == 3
