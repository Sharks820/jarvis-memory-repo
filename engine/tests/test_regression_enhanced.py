"""Tests for enhanced RegressionChecker: backup, restore, node_diff, and compare with diff."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock


from jarvis_engine.knowledge.regression import RegressionChecker, _MAX_BACKUPS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_kg(tmp_path: Path) -> MagicMock:
    """Build a mock KnowledgeGraph with a real SQLite DB for backup tests."""
    db_path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS kg_nodes (node_id TEXT PRIMARY KEY, label TEXT)")
    conn.commit()

    engine_mock = MagicMock()
    engine_mock._db_path = db_path
    engine_mock.db_path = db_path
    engine_mock._db = conn
    engine_mock._write_lock = threading.Lock()
    engine_mock._db_lock = threading.Lock()

    kg = MagicMock()
    kg._engine = engine_mock
    kg._db = conn
    kg._write_lock = engine_mock._write_lock
    kg._db_lock = engine_mock._db_lock
    # Public accessors (used by RegressionChecker instead of private attrs)
    kg.db_path = db_path
    kg.db = conn
    kg.write_lock = engine_mock._write_lock
    kg.db_lock = engine_mock._db_lock
    kg.count_locked.return_value = 0
    kg.invalidate_cache = MagicMock()
    kg.ensure_schema = MagicMock()

    import networkx as nx
    empty_g = nx.DiGraph()
    kg.to_networkx.return_value = empty_g
    kg._ensure_schema = MagicMock()

    return kg


def _snapshot_with_labels(node_labels: dict[str, str], **overrides) -> dict:
    """Build a metrics-like snapshot dict with node_labels and counts."""
    result = {
        "node_count": len(node_labels),
        "edge_count": 0,
        "locked_count": 0,
        "graph_hash": "abc123",
        "node_labels": node_labels,
        "captured_at": "2026-02-25T00:00:00+00:00",
    }
    result.update(overrides)
    return result


# ===================================================================
# Backup tests
# ===================================================================

class TestBackupGraph:
    """Tests for RegressionChecker.backup_graph()."""

    def test_backup_creates_file(self, tmp_path: Path, monkeypatch) -> None:
        """backup_graph should create a .db file in the kg_backups directory."""
        monkeypatch.chdir(tmp_path)
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        result = checker.backup_graph(tag="test")

        assert result.exists()
        assert result.suffix == ".db"
        assert "test" in result.name
        assert "kg_backups" in str(result.parent)

    def test_backup_auto_prunes_old(self, tmp_path: Path, monkeypatch) -> None:
        """When more than _MAX_BACKUPS exist, oldest should be pruned."""
        monkeypatch.chdir(tmp_path)
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        # Create _MAX_BACKUPS + 3 backups
        created: list[Path] = []
        for i in range(_MAX_BACKUPS + 3):
            p = checker.backup_graph(tag=f"b{i:02d}")
            created.append(p)

        backup_dir = tmp_path / "kg_backups"
        remaining = list(backup_dir.glob("*.db"))
        assert len(remaining) == _MAX_BACKUPS

    def test_backup_with_empty_tag(self, tmp_path: Path, monkeypatch) -> None:
        """backup_graph with no tag should still produce a valid file."""
        monkeypatch.chdir(tmp_path)
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        result = checker.backup_graph()

        assert result.exists()
        # No underscore before .db when tag is empty
        assert result.name.endswith(".db")


# ===================================================================
# Node diff tests
# ===================================================================

class TestNodeDiff:
    """Tests for RegressionChecker.node_diff()."""

    def test_node_diff_detects_additions(self, tmp_path: Path) -> None:
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        before = _snapshot_with_labels({"n1": "alpha"})
        after = _snapshot_with_labels({"n1": "alpha", "n2": "beta"})

        diff = checker.node_diff(before, after)
        assert diff["added"] == ["n2:beta"]
        assert diff["removed"] == []
        assert diff["modified"] == []

    def test_node_diff_detects_removals(self, tmp_path: Path) -> None:
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        before = _snapshot_with_labels({"n1": "alpha", "n2": "beta"})
        after = _snapshot_with_labels({"n1": "alpha"})

        diff = checker.node_diff(before, after)
        assert diff["added"] == []
        assert diff["removed"] == ["n2:beta"]
        assert diff["modified"] == []

    def test_node_diff_detects_modifications(self, tmp_path: Path) -> None:
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        before = _snapshot_with_labels({"n1": "alpha"})
        after = _snapshot_with_labels({"n1": "alpha_v2"})

        diff = checker.node_diff(before, after)
        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["modified"] == ["n1:alpha->alpha_v2"]

    def test_node_diff_empty_snapshots(self, tmp_path: Path) -> None:
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        diff = checker.node_diff({}, {})
        assert diff == {"added": [], "removed": [], "modified": []}

    def test_node_diff_mixed_changes(self, tmp_path: Path) -> None:
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        before = _snapshot_with_labels({"n1": "a", "n2": "b", "n3": "c"})
        after = _snapshot_with_labels({"n1": "a", "n2": "b_updated", "n4": "d"})

        diff = checker.node_diff(before, after)
        assert "n4:d" in diff["added"]
        assert "n3:c" in diff["removed"]
        assert "n2:b->b_updated" in diff["modified"]


# ===================================================================
# Compare with diff tests
# ===================================================================

class TestCompareIncludesDiff:
    """Tests for compare() including node_diff on regression."""

    def test_compare_includes_diff_on_regression(self, tmp_path: Path) -> None:
        """When node loss is detected, compare result should include node_diff."""
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        previous = _snapshot_with_labels(
            {"n1": "alpha", "n2": "beta"},
            node_count=2,
            edge_count=0,
            locked_count=0,
            graph_hash="hash1",
        )
        current = _snapshot_with_labels(
            {"n1": "alpha"},
            node_count=1,
            edge_count=0,
            locked_count=0,
            graph_hash="hash2",
        )

        result = checker.compare(previous, current)
        assert result["status"] == "fail"
        assert "node_diff" in result
        assert result["node_diff"]["removed"] == ["n2:beta"]

    def test_compare_no_diff_on_pass(self, tmp_path: Path) -> None:
        """When status is pass, node_diff should NOT be present."""
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        snapshot = _snapshot_with_labels(
            {"n1": "alpha"},
            node_count=1,
            edge_count=1,
            locked_count=0,
            graph_hash="same_hash",
        )

        result = checker.compare(snapshot, snapshot)
        assert result["status"] == "pass"
        assert "node_diff" not in result

    def test_compare_diff_on_warn(self, tmp_path: Path) -> None:
        """Hash change without count increase triggers warn + node_diff."""
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        previous = _snapshot_with_labels(
            {"n1": "alpha"},
            node_count=1,
            edge_count=1,
            locked_count=0,
            graph_hash="hash_a",
        )
        current = _snapshot_with_labels(
            {"n1": "alpha_modified"},
            node_count=1,
            edge_count=1,
            locked_count=0,
            graph_hash="hash_b",
        )

        result = checker.compare(previous, current)
        assert result["status"] == "warn"
        assert "node_diff" in result
        assert "n1:alpha->alpha_modified" in result["node_diff"]["modified"]

    def test_compare_baseline_no_diff(self, tmp_path: Path) -> None:
        """Baseline comparison (previous=None) should not include node_diff."""
        kg = _make_mock_kg(tmp_path)
        checker = RegressionChecker(kg)

        current = _snapshot_with_labels({"n1": "alpha"}, node_count=1)
        result = checker.compare(None, current)
        assert result["status"] == "baseline"
        assert "node_diff" not in result
