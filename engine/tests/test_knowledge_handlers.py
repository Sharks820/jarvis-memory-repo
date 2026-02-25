"""Comprehensive tests for knowledge handler classes in knowledge_handlers.py.

Covers KnowledgeStatusHandler, ContradictionListHandler,
ContradictionResolveHandler, FactLockHandler, and
KnowledgeRegressionHandler -- including all edge cases, error paths,
and fallback behaviour when dependencies are unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
from jarvis_engine.handlers.knowledge_handlers import (
    ContradictionListHandler,
    ContradictionResolveHandler,
    FactLockHandler,
    KnowledgeRegressionHandler,
    KnowledgeStatusHandler,
)


# ---------------------------------------------------------------------------
# KnowledgeStatusHandler
# ---------------------------------------------------------------------------


class TestKnowledgeStatusHandler:
    """Tests for KnowledgeStatusHandler."""

    def test_no_kg_returns_empty(self, tmp_path: Path) -> None:
        handler = KnowledgeStatusHandler(root=tmp_path, kg=None)
        result = handler.handle(KnowledgeStatusCommand())
        assert result.node_count == 0
        assert result.edge_count == 0
        assert result.locked_count == 0
        assert result.pending_contradictions == 0
        assert result.graph_hash == ""

    def test_import_error_returns_empty(self, tmp_path: Path) -> None:
        handler = KnowledgeStatusHandler(root=tmp_path, kg=MagicMock())
        with patch.dict("sys.modules", {"jarvis_engine.knowledge.regression": None}):
            result = handler.handle(KnowledgeStatusCommand())
        assert result.node_count == 0

    def test_successful_status(self, tmp_path: Path) -> None:
        """Full happy path: metrics captured from regression checker."""
        kg = MagicMock()
        kg.count_pending_contradictions.return_value = 3

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {
            "node_count": 100,
            "edge_count": 250,
            "locked_count": 10,
            "graph_hash": "abc123",
        }
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeStatusHandler(root=tmp_path, kg=kg)
            result = handler.handle(KnowledgeStatusCommand())

        assert result.node_count == 100
        assert result.edge_count == 250
        assert result.locked_count == 10
        assert result.pending_contradictions == 3
        assert result.graph_hash == "abc123"

    def test_missing_metric_keys_default_to_zero(self, tmp_path: Path) -> None:
        """When capture_metrics returns partial dict, missing keys default to 0."""
        kg = MagicMock()
        kg.count_pending_contradictions.return_value = 0

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeStatusHandler(root=tmp_path, kg=kg)
            result = handler.handle(KnowledgeStatusCommand())

        assert result.node_count == 0
        assert result.edge_count == 0
        assert result.locked_count == 0
        assert result.graph_hash == ""


# ---------------------------------------------------------------------------
# ContradictionListHandler
# ---------------------------------------------------------------------------


class TestContradictionListHandler:
    """Tests for ContradictionListHandler."""

    def test_no_kg_returns_empty(self, tmp_path: Path) -> None:
        handler = ContradictionListHandler(root=tmp_path, kg=None)
        result = handler.handle(ContradictionListCommand())
        assert result.contradictions == []

    def test_import_error_returns_empty(self, tmp_path: Path) -> None:
        handler = ContradictionListHandler(root=tmp_path, kg=MagicMock())
        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": None}
        ):
            result = handler.handle(ContradictionListCommand())
        assert result.contradictions == []

    def test_successful_list(self, tmp_path: Path) -> None:
        kg = MagicMock()
        contradictions = [
            {"id": 1, "status": "pending", "fact_a": "X", "fact_b": "Y"},
            {"id": 2, "status": "pending", "fact_a": "A", "fact_b": "B"},
        ]

        mock_contra_mod = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.list_all.return_value = contradictions
        mock_contra_mod.ContradictionManager.return_value = mock_mgr

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": mock_contra_mod}
        ):
            handler = ContradictionListHandler(root=tmp_path, kg=kg)
            result = handler.handle(ContradictionListCommand(status="pending", limit=50))

        assert len(result.contradictions) == 2
        mock_contra_mod.ContradictionManager.assert_called_once_with(
            kg.db, kg.write_lock, kg.db_lock
        )
        mock_mgr.list_all.assert_called_once_with(status="pending", limit=50)

    def test_empty_status_passes_none(self, tmp_path: Path) -> None:
        """When status is empty string, None is passed to list_all."""
        kg = MagicMock()

        mock_contra_mod = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.list_all.return_value = []
        mock_contra_mod.ContradictionManager.return_value = mock_mgr

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": mock_contra_mod}
        ):
            handler = ContradictionListHandler(root=tmp_path, kg=kg)
            handler.handle(ContradictionListCommand(status="", limit=20))

        mock_mgr.list_all.assert_called_once_with(status=None, limit=20)

    def test_limit_capped_at_500(self, tmp_path: Path) -> None:
        """Limit is capped at 500 regardless of what user passes."""
        kg = MagicMock()

        mock_contra_mod = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.list_all.return_value = []
        mock_contra_mod.ContradictionManager.return_value = mock_mgr

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": mock_contra_mod}
        ):
            handler = ContradictionListHandler(root=tmp_path, kg=kg)
            handler.handle(ContradictionListCommand(limit=9999))

        mock_mgr.list_all.assert_called_once_with(status=None, limit=500)

    def test_limit_below_500_unchanged(self, tmp_path: Path) -> None:
        kg = MagicMock()

        mock_contra_mod = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.list_all.return_value = []
        mock_contra_mod.ContradictionManager.return_value = mock_mgr

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": mock_contra_mod}
        ):
            handler = ContradictionListHandler(root=tmp_path, kg=kg)
            handler.handle(ContradictionListCommand(limit=50))

        mock_mgr.list_all.assert_called_once_with(status=None, limit=50)


# ---------------------------------------------------------------------------
# ContradictionResolveHandler
# ---------------------------------------------------------------------------


class TestContradictionResolveHandler:
    """Tests for ContradictionResolveHandler."""

    def test_no_kg_returns_failure(self, tmp_path: Path) -> None:
        handler = ContradictionResolveHandler(root=tmp_path, kg=None)
        result = handler.handle(ContradictionResolveCommand())
        assert result.success is False
        assert "not available" in result.message.lower()

    def test_import_error_returns_failure(self, tmp_path: Path) -> None:
        handler = ContradictionResolveHandler(root=tmp_path, kg=MagicMock())
        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": None}
        ):
            result = handler.handle(ContradictionResolveCommand())
        assert result.success is False
        assert "not available" in result.message.lower()

    def test_successful_resolve(self, tmp_path: Path) -> None:
        kg = MagicMock()

        mock_contra_mod = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.resolve.return_value = {
            "success": True,
            "node_id": "n42",
            "resolution": "accept_new",
            "message": "Contradiction resolved.",
        }
        mock_contra_mod.ContradictionManager.return_value = mock_mgr

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": mock_contra_mod}
        ):
            handler = ContradictionResolveHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                ContradictionResolveCommand(
                    contradiction_id=5,
                    resolution="accept_new",
                    merge_value="",
                )
            )

        assert result.success is True
        assert result.node_id == "n42"
        assert result.resolution == "accept_new"
        mock_mgr.resolve.assert_called_once_with(
            contradiction_id=5, resolution="accept_new", merge_value=""
        )

    def test_resolve_with_merge(self, tmp_path: Path) -> None:
        kg = MagicMock()

        mock_contra_mod = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.resolve.return_value = {
            "success": True,
            "node_id": "n7",
            "resolution": "merge",
            "message": "Merged.",
        }
        mock_contra_mod.ContradictionManager.return_value = mock_mgr

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": mock_contra_mod}
        ):
            handler = ContradictionResolveHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                ContradictionResolveCommand(
                    contradiction_id=7,
                    resolution="merge",
                    merge_value="combined value",
                )
            )

        assert result.success is True
        assert result.resolution == "merge"

    def test_resolve_failure(self, tmp_path: Path) -> None:
        kg = MagicMock()

        mock_contra_mod = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.resolve.return_value = {
            "success": False,
            "message": "Contradiction not found.",
        }
        mock_contra_mod.ContradictionManager.return_value = mock_mgr

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": mock_contra_mod}
        ):
            handler = ContradictionResolveHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                ContradictionResolveCommand(contradiction_id=999, resolution="keep_old")
            )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_missing_keys_default_to_empty(self, tmp_path: Path) -> None:
        """When resolve returns partial dict, missing keys default properly."""
        kg = MagicMock()

        mock_contra_mod = MagicMock()
        mock_mgr = MagicMock()
        mock_mgr.resolve.return_value = {"success": True}
        mock_contra_mod.ContradictionManager.return_value = mock_mgr

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.contradictions": mock_contra_mod}
        ):
            handler = ContradictionResolveHandler(root=tmp_path, kg=kg)
            result = handler.handle(ContradictionResolveCommand(contradiction_id=1))

        assert result.success is True
        assert result.node_id == ""
        assert result.resolution == ""
        assert result.message == ""


# ---------------------------------------------------------------------------
# FactLockHandler
# ---------------------------------------------------------------------------


class TestFactLockHandler:
    """Tests for FactLockHandler."""

    def test_no_kg_returns_failure(self, tmp_path: Path) -> None:
        handler = FactLockHandler(root=tmp_path, kg=None)
        result = handler.handle(FactLockCommand(node_id="n1", action="lock"))
        assert result.success is False
        assert result.node_id == "n1"
        assert "not available" in result.message.lower()

    def test_invalid_action(self, tmp_path: Path) -> None:
        handler = FactLockHandler(root=tmp_path, kg=MagicMock())
        result = handler.handle(FactLockCommand(node_id="n1", action="delete"))
        assert result.success is False
        assert "invalid action" in result.message.lower()
        assert "'delete'" in result.message

    def test_invalid_action_empty(self, tmp_path: Path) -> None:
        handler = FactLockHandler(root=tmp_path, kg=MagicMock())
        result = handler.handle(FactLockCommand(node_id="n1", action=""))
        assert result.success is False
        assert "invalid action" in result.message.lower()

    def test_import_error_returns_failure(self, tmp_path: Path) -> None:
        handler = FactLockHandler(root=tmp_path, kg=MagicMock())
        with patch.dict("sys.modules", {"jarvis_engine.knowledge.locks": None}):
            result = handler.handle(FactLockCommand(node_id="n1", action="lock"))
        assert result.success is False
        assert "not available" in result.message.lower()

    def test_lock_success(self, tmp_path: Path) -> None:
        kg = MagicMock()

        mock_locks_mod = MagicMock()
        mock_lock_mgr = MagicMock()
        mock_lock_mgr.owner_confirm_lock.return_value = True
        mock_locks_mod.FactLockManager.return_value = mock_lock_mgr

        with patch.dict("sys.modules", {"jarvis_engine.knowledge.locks": mock_locks_mod}):
            handler = FactLockHandler(root=tmp_path, kg=kg)
            result = handler.handle(FactLockCommand(node_id="n42", action="lock"))

        assert result.success is True
        assert result.locked is True
        assert result.node_id == "n42"
        assert "locked" in result.message.lower()
        mock_lock_mgr.owner_confirm_lock.assert_called_once_with("n42")

    def test_lock_already_locked(self, tmp_path: Path) -> None:
        kg = MagicMock()

        mock_locks_mod = MagicMock()
        mock_lock_mgr = MagicMock()
        mock_lock_mgr.owner_confirm_lock.return_value = False
        mock_locks_mod.FactLockManager.return_value = mock_lock_mgr

        with patch.dict("sys.modules", {"jarvis_engine.knowledge.locks": mock_locks_mod}):
            handler = FactLockHandler(root=tmp_path, kg=kg)
            result = handler.handle(FactLockCommand(node_id="n42", action="lock"))

        assert result.success is False
        assert "already locked or not found" in result.message.lower()

    def test_unlock_success(self, tmp_path: Path) -> None:
        kg = MagicMock()

        mock_locks_mod = MagicMock()
        mock_lock_mgr = MagicMock()
        mock_lock_mgr.unlock_fact.return_value = True
        mock_locks_mod.FactLockManager.return_value = mock_lock_mgr

        with patch.dict("sys.modules", {"jarvis_engine.knowledge.locks": mock_locks_mod}):
            handler = FactLockHandler(root=tmp_path, kg=kg)
            result = handler.handle(FactLockCommand(node_id="n42", action="unlock"))

        assert result.success is True
        assert result.locked is False
        assert "unlocked" in result.message.lower()
        mock_lock_mgr.unlock_fact.assert_called_once_with("n42")

    def test_unlock_already_unlocked(self, tmp_path: Path) -> None:
        kg = MagicMock()

        mock_locks_mod = MagicMock()
        mock_lock_mgr = MagicMock()
        mock_lock_mgr.unlock_fact.return_value = False
        mock_locks_mod.FactLockManager.return_value = mock_lock_mgr

        with patch.dict("sys.modules", {"jarvis_engine.knowledge.locks": mock_locks_mod}):
            handler = FactLockHandler(root=tmp_path, kg=kg)
            result = handler.handle(FactLockCommand(node_id="n42", action="unlock"))

        assert result.success is False
        assert result.locked is True  # not unlocked means still locked
        assert "already unlocked or not found" in result.message.lower()

    def test_lock_manager_receives_kg_fields(self, tmp_path: Path) -> None:
        """FactLockManager is constructed with kg.db, kg.write_lock, kg.db_lock."""
        kg = MagicMock()
        kg.db = MagicMock(name="db")
        kg.write_lock = MagicMock(name="wl")
        kg.db_lock = MagicMock(name="dl")

        mock_locks_mod = MagicMock()
        mock_lock_mgr = MagicMock()
        mock_lock_mgr.owner_confirm_lock.return_value = True
        mock_locks_mod.FactLockManager.return_value = mock_lock_mgr

        with patch.dict("sys.modules", {"jarvis_engine.knowledge.locks": mock_locks_mod}):
            handler = FactLockHandler(root=tmp_path, kg=kg)
            handler.handle(FactLockCommand(node_id="n1", action="lock"))

        mock_locks_mod.FactLockManager.assert_called_once_with(kg.db, kg.write_lock, kg.db_lock)


# ---------------------------------------------------------------------------
# KnowledgeRegressionHandler
# ---------------------------------------------------------------------------


class TestKnowledgeRegressionHandler:
    """Tests for KnowledgeRegressionHandler."""

    def test_no_kg_returns_error(self, tmp_path: Path) -> None:
        handler = KnowledgeRegressionHandler(root=tmp_path, kg=None)
        result = handler.handle(KnowledgeRegressionCommand())
        assert result.report["status"] == "error"
        assert "not available" in result.report["message"].lower()

    def test_import_error_returns_error(self, tmp_path: Path) -> None:
        handler = KnowledgeRegressionHandler(root=tmp_path, kg=MagicMock())
        with patch.dict("sys.modules", {"jarvis_engine.knowledge.regression": None}):
            result = handler.handle(KnowledgeRegressionCommand())
        assert result.report["status"] == "error"
        assert "not available" in result.report["message"].lower()

    def test_no_snapshot_path_returns_baseline(self, tmp_path: Path) -> None:
        """Without snapshot path, compares None to current metrics."""
        kg = MagicMock()
        current_metrics = {"node_count": 50, "edge_count": 100}

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = current_metrics
        mock_checker.compare.return_value = {"status": "baseline", "current": current_metrics}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(KnowledgeRegressionCommand(snapshot_path=""))

        mock_checker.compare.assert_called_once_with(None, current_metrics)
        assert result.report["status"] == "baseline"

    def test_snapshot_outside_root(self, tmp_path: Path) -> None:
        """Path traversal: snapshot path outside root is rejected."""
        kg = MagicMock()

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {"node_count": 10}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                KnowledgeRegressionCommand(snapshot_path="/etc/evil.json")
            )

        assert result.report["status"] == "error"
        assert "within the project root" in result.report["message"]

    def test_zip_suffix_switched_to_json(self, tmp_path: Path) -> None:
        """When snapshot path ends in .zip, switches to .json companion."""
        kg = MagicMock()
        meta_path = tmp_path / "snapshot.json"
        meta_path.write_text(
            json.dumps({"kg_metrics": {"node_count": 40}}), encoding="utf-8"
        )

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {"node_count": 50}
        mock_checker.compare.return_value = {"status": "pass"}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                KnowledgeRegressionCommand(snapshot_path=str(tmp_path / "snapshot.zip"))
            )

        mock_checker.compare.assert_called_once_with({"node_count": 40}, {"node_count": 50})
        assert result.report["status"] == "pass"

    def test_snapshot_json_load_failure(self, tmp_path: Path) -> None:
        """Corrupted snapshot metadata returns error."""
        kg = MagicMock()
        bad_meta = tmp_path / "bad.json"
        bad_meta.write_text("{not valid}", encoding="utf-8")

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {"node_count": 10}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                KnowledgeRegressionCommand(snapshot_path=str(bad_meta))
            )

        assert result.report["status"] == "error"
        assert "failed to load" in result.report["message"].lower()

    def test_snapshot_file_missing(self, tmp_path: Path) -> None:
        """Nonexistent snapshot file returns error."""
        kg = MagicMock()

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {"node_count": 10}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                KnowledgeRegressionCommand(
                    snapshot_path=str(tmp_path / "missing.json")
                )
            )

        assert result.report["status"] == "error"
        assert "failed to load" in result.report["message"].lower()

    def test_successful_comparison_with_snapshot(self, tmp_path: Path) -> None:
        """Full path: load metadata, compare previous to current metrics."""
        kg = MagicMock()
        prev_metrics = {"node_count": 30, "edge_count": 60}
        meta_path = tmp_path / "snap.json"
        meta_path.write_text(
            json.dumps({"kg_metrics": prev_metrics}), encoding="utf-8"
        )

        current = {"node_count": 35, "edge_count": 70}

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = current
        mock_checker.compare.return_value = {
            "status": "pass",
            "node_delta": 5,
            "edge_delta": 10,
        }
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                KnowledgeRegressionCommand(snapshot_path=str(meta_path))
            )

        assert result.report["status"] == "pass"
        assert result.report["node_delta"] == 5
        mock_checker.compare.assert_called_once_with(prev_metrics, current)

    def test_empty_snapshot_path_string(self, tmp_path: Path) -> None:
        """Empty string snapshot_path treated as no snapshot."""
        kg = MagicMock()

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {}
        mock_checker.compare.return_value = {"status": "baseline"}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(KnowledgeRegressionCommand(snapshot_path=""))

        mock_checker.compare.assert_called_once_with(None, {})

    def test_whitespace_only_snapshot_path(self, tmp_path: Path) -> None:
        """Whitespace-only snapshot_path treated as no snapshot."""
        kg = MagicMock()

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {}
        mock_checker.compare.return_value = {"status": "baseline"}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(KnowledgeRegressionCommand(snapshot_path="   "))

        mock_checker.compare.assert_called_once_with(None, {})

    def test_snapshot_without_kg_metrics_key(self, tmp_path: Path) -> None:
        """When snapshot metadata has no 'kg_metrics', prev_metrics is None."""
        kg = MagicMock()
        meta_path = tmp_path / "snap.json"
        meta_path.write_text(json.dumps({"other": "data"}), encoding="utf-8")

        mock_regression_mod = MagicMock()
        mock_checker = MagicMock()
        mock_checker.capture_metrics.return_value = {"node_count": 10}
        mock_checker.compare.return_value = {"status": "pass"}
        mock_regression_mod.RegressionChecker.return_value = mock_checker

        with patch.dict(
            "sys.modules", {"jarvis_engine.knowledge.regression": mock_regression_mod}
        ):
            handler = KnowledgeRegressionHandler(root=tmp_path, kg=kg)
            result = handler.handle(
                KnowledgeRegressionCommand(snapshot_path=str(meta_path))
            )

        # prev_metrics will be None (meta.get("kg_metrics") on dict without key)
        mock_checker.compare.assert_called_once_with(None, {"node_count": 10})
