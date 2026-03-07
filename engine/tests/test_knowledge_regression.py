"""Tests for jarvis_engine.knowledge.regression -- RegressionChecker.

Covers:
- capture_metrics: node/edge/locked counts, graph hash, empty graph
- compare: baseline (None previous), pass, warn, fail statuses
- Discrepancy types: node_loss, edge_loss, locked_fact_loss, graph_hash_change
- Edge cases: hash change with growth (expected), critical severity
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from jarvis_engine.knowledge.regression import RegressionChecker, _EMPTY_GRAPH_HASH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_kg(
    node_count: int = 0,
    edge_count: int = 0,
    locked_count: int = 0,
    nodes: list[tuple] | None = None,
    edges: list[tuple] | None = None,
) -> MagicMock:
    """Create a mock KnowledgeGraph with configurable metrics."""
    kg = MagicMock()
    kg.count_locked.return_value = locked_count

    # Build a real or mock NetworkX graph
    mock_graph = MagicMock()
    mock_graph.number_of_nodes.return_value = node_count
    mock_graph.number_of_edges.return_value = edge_count
    kg.to_networkx.return_value = mock_graph

    return kg


def _make_metrics(
    node_count: int = 10,
    edge_count: int = 15,
    locked_count: int = 3,
    graph_hash: str = "abc123",
    captured_at: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    """Create a metrics snapshot dict."""
    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "locked_count": locked_count,
        "graph_hash": graph_hash,
        "captured_at": captured_at,
    }


# ---------------------------------------------------------------------------
# capture_metrics
# ---------------------------------------------------------------------------


class TestCaptureMetrics:

    def test_empty_graph_returns_empty_hash(self):
        """An empty graph uses the special empty graph hash."""
        kg = _make_mock_kg(node_count=0, edge_count=0, locked_count=0)
        checker = RegressionChecker(kg)
        metrics = checker.capture_metrics()
        assert metrics["node_count"] == 0
        assert metrics["edge_count"] == 0
        assert metrics["locked_count"] == 0
        assert metrics["graph_hash"] == _EMPTY_GRAPH_HASH

    def test_nonempty_graph_uses_wl_hash(self):
        """A non-empty graph uses the Weisfeiler-Lehman hash."""
        kg = _make_mock_kg(node_count=5, edge_count=3, locked_count=1)
        checker = RegressionChecker(kg)
        with patch("networkx.weisfeiler_lehman_graph_hash", return_value="wl_hash_value"):
            metrics = checker.capture_metrics()
        assert metrics["graph_hash"] == "wl_hash_value"
        assert metrics["node_count"] == 5
        assert metrics["edge_count"] == 3
        assert metrics["locked_count"] == 1

    def test_wl_hash_failure_returns_empty_hash(self):
        """If WL hash computation fails, fall back to empty graph hash."""
        kg = _make_mock_kg(node_count=5, edge_count=3, locked_count=1)
        checker = RegressionChecker(kg)
        with patch("networkx.weisfeiler_lehman_graph_hash", side_effect=ValueError("hash error")):
            metrics = checker.capture_metrics()
        assert metrics["graph_hash"] == _EMPTY_GRAPH_HASH

    def test_captured_at_is_iso_string(self):
        """captured_at should be a valid ISO datetime string."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        metrics = checker.capture_metrics()
        assert "captured_at" in metrics
        assert isinstance(metrics["captured_at"], str)
        # Should contain date separator
        assert "T" in metrics["captured_at"]


# ---------------------------------------------------------------------------
# compare - baseline
# ---------------------------------------------------------------------------


class TestCompareBaseline:

    def test_none_previous_returns_baseline(self):
        """When previous is None, status is 'baseline'."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        current = _make_metrics()
        result = checker.compare(None, current)
        assert result["status"] == "baseline"
        assert result["discrepancies"] == []
        assert result["previous"] is None
        assert result["current"] is current

    def test_baseline_message(self):
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        result = checker.compare(None, _make_metrics())
        assert "Baseline" in result["message"]


# ---------------------------------------------------------------------------
# compare - pass (no regressions)
# ---------------------------------------------------------------------------


class TestComparePass:

    def test_identical_metrics_pass(self):
        """Same metrics in both snapshots -> pass."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, edge_count=15, locked_count=3, graph_hash="h1")
        curr = _make_metrics(node_count=10, edge_count=15, locked_count=3, graph_hash="h1")
        result = checker.compare(prev, curr)
        assert result["status"] == "pass"
        assert result["discrepancies"] == []

    def test_growth_is_pass(self):
        """Increased counts with same hash is pass."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, edge_count=15, locked_count=3, graph_hash="h1")
        curr = _make_metrics(node_count=12, edge_count=18, locked_count=4, graph_hash="h1")
        result = checker.compare(prev, curr)
        assert result["status"] == "pass"

    def test_hash_change_with_growth_is_pass(self):
        """Hash changed but counts increased -> no discrepancy on hash."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, edge_count=15, graph_hash="h1")
        curr = _make_metrics(node_count=12, edge_count=18, graph_hash="h2")
        result = checker.compare(prev, curr)
        # Hash change with growth should not be flagged
        hash_discrepancies = [d for d in result["discrepancies"] if d["type"] == "graph_hash_change"]
        assert len(hash_discrepancies) == 0


# ---------------------------------------------------------------------------
# compare - node_loss
# ---------------------------------------------------------------------------


class TestCompareNodeLoss:

    def test_node_loss_detected(self):
        """Decreased node count triggers node_loss discrepancy."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, graph_hash="h1")
        curr = _make_metrics(node_count=7, graph_hash="h1")
        result = checker.compare(prev, curr)
        assert result["status"] == "fail"
        node_loss = [d for d in result["discrepancies"] if d["type"] == "node_loss"]
        assert len(node_loss) == 1
        assert node_loss[0]["lost"] == 3
        assert node_loss[0]["severity"] == "fail"


# ---------------------------------------------------------------------------
# compare - edge_loss
# ---------------------------------------------------------------------------


class TestCompareEdgeLoss:

    def test_edge_loss_detected(self):
        """Decreased edge count triggers edge_loss discrepancy."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(edge_count=15, graph_hash="h1")
        curr = _make_metrics(edge_count=10, graph_hash="h1")
        result = checker.compare(prev, curr)
        edge_loss = [d for d in result["discrepancies"] if d["type"] == "edge_loss"]
        assert len(edge_loss) == 1
        assert edge_loss[0]["lost"] == 5
        assert edge_loss[0]["severity"] == "fail"


# ---------------------------------------------------------------------------
# compare - locked_fact_loss (critical)
# ---------------------------------------------------------------------------


class TestCompareLockedFactLoss:

    def test_locked_fact_loss_is_critical(self):
        """Decreased locked count triggers critical severity."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(locked_count=5, graph_hash="h1")
        curr = _make_metrics(locked_count=3, graph_hash="h1")
        result = checker.compare(prev, curr)
        assert result["status"] == "fail"
        locked_loss = [d for d in result["discrepancies"] if d["type"] == "locked_fact_loss"]
        assert len(locked_loss) == 1
        assert locked_loss[0]["severity"] == "critical"
        assert locked_loss[0]["lost"] == 2


# ---------------------------------------------------------------------------
# compare - graph_hash_change (warn)
# ---------------------------------------------------------------------------


class TestCompareGraphHashChange:

    def test_hash_change_without_growth_is_warn(self):
        """Hash change without count increase -> warn."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, edge_count=15, graph_hash="h1")
        curr = _make_metrics(node_count=10, edge_count=15, graph_hash="h2")
        result = checker.compare(prev, curr)
        assert result["status"] == "warn"
        hash_changes = [d for d in result["discrepancies"] if d["type"] == "graph_hash_change"]
        assert len(hash_changes) == 1
        assert hash_changes[0]["severity"] == "warn"

    def test_hash_change_with_count_decrease_is_warn_plus_fail(self):
        """Hash change combined with count decrease triggers both."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, edge_count=15, graph_hash="h1")
        curr = _make_metrics(node_count=8, edge_count=12, graph_hash="h2")
        result = checker.compare(prev, curr)
        assert result["status"] == "fail"
        types = [d["type"] for d in result["discrepancies"]]
        assert "node_loss" in types
        assert "edge_loss" in types
        assert "graph_hash_change" in types

    def test_empty_hash_no_change_detected(self):
        """If either hash is empty, no hash_change discrepancy is flagged."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(graph_hash="")
        curr = _make_metrics(graph_hash="h2")
        result = checker.compare(prev, curr)
        hash_changes = [d for d in result["discrepancies"] if d["type"] == "graph_hash_change"]
        assert len(hash_changes) == 0


# ---------------------------------------------------------------------------
# compare - overall status priority
# ---------------------------------------------------------------------------


class TestCompareStatusPriority:

    def test_critical_overrides_warn(self):
        """Critical (locked_fact_loss) results in 'fail' even with warn issues."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, edge_count=10, locked_count=5, graph_hash="h1")
        curr = _make_metrics(node_count=10, edge_count=10, locked_count=3, graph_hash="h2")
        result = checker.compare(prev, curr)
        assert result["status"] == "fail"

    def test_fail_severity_results_in_fail_status(self):
        """Any fail-severity discrepancy results in fail status."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, graph_hash="h1")
        curr = _make_metrics(node_count=5, graph_hash="h1")
        result = checker.compare(prev, curr)
        assert result["status"] == "fail"

    def test_only_warn_results_in_warn_status(self):
        """Only warn-severity discrepancies result in warn status."""
        kg = _make_mock_kg()
        checker = RegressionChecker(kg)
        prev = _make_metrics(node_count=10, edge_count=15, graph_hash="h1")
        curr = _make_metrics(node_count=10, edge_count=15, graph_hash="h2")
        result = checker.compare(prev, curr)
        assert result["status"] == "warn"
