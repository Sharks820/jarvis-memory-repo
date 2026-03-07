"""Comprehensive tests for knowledge graph locks, contradictions, and regression.

Tests cover:
- FactLockManager: auto-lock thresholds, owner confirm, unlock
- Locked fact blocks overwrite (contradiction quarantined)
- ContradictionManager: list pending, resolve accept_new/keep_old/merge
- RegressionChecker: capture metrics, compare snapshots, baseline
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_engine.knowledge.graph import KnowledgeGraph
from jarvis_engine.knowledge.locks import FactLockManager
from jarvis_engine.knowledge.contradictions import ContradictionManager
from jarvis_engine.knowledge.regression import RegressionChecker
from jarvis_engine.memory.engine import MemoryEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> MemoryEngine:
    """Create a MemoryEngine with a temporary database."""
    db_path = tmp_path / "test_locks.db"
    eng = MemoryEngine(db_path)
    yield eng
    eng.close()


@pytest.fixture
def kg(engine: MemoryEngine) -> KnowledgeGraph:
    """Create a KnowledgeGraph backed by a temporary MemoryEngine."""
    return KnowledgeGraph(engine)


@pytest.fixture
def lock_mgr(kg: KnowledgeGraph) -> FactLockManager:
    """Create a FactLockManager for the test KG."""
    return FactLockManager(kg._db, kg._write_lock)


@pytest.fixture
def contradiction_mgr(kg: KnowledgeGraph) -> ContradictionManager:
    """Create a ContradictionManager for the test KG."""
    return ContradictionManager(kg._db, kg._write_lock)


@pytest.fixture
def regression_checker(kg: KnowledgeGraph) -> RegressionChecker:
    """Create a RegressionChecker for the test KG."""
    return RegressionChecker(kg)


# ---------------------------------------------------------------------------
# FactLockManager Tests
# ---------------------------------------------------------------------------


class TestFactLockManager:

    def test_auto_lock_threshold_met(
        self, kg: KnowledgeGraph, lock_mgr: FactLockManager
    ) -> None:
        """Fact with confidence >= 0.9 and 3+ sources auto-locks."""
        # Add a fact and manually set it to meet thresholds
        kg.add_fact("health.med.metformin", "metformin", 0.95, source_record="src1")
        kg.add_fact("health.med.metformin", "metformin", 0.95, source_record="src2")
        kg.add_fact("health.med.metformin", "metformin", 0.95, source_record="src3")

        # Verify auto-lock was triggered (graph.add_fact calls check_and_auto_lock)
        node = kg.get_node("health.med.metformin")
        assert node is not None
        assert node["locked"] == 1
        assert node["locked_by"] == "auto"

    def test_auto_lock_threshold_not_met(
        self, kg: KnowledgeGraph, lock_mgr: FactLockManager
    ) -> None:
        """Fact with low confidence or few sources does NOT auto-lock."""
        # Low confidence
        kg.add_fact("pref.color", "blue", 0.5, source_record="src1")
        kg.add_fact("pref.color", "blue", 0.5, source_record="src2")
        kg.add_fact("pref.color", "blue", 0.5, source_record="src3")

        node = kg.get_node("pref.color")
        assert node is not None
        assert node["locked"] == 0

        # High confidence but too few sources
        kg.add_fact("pref.food", "pizza", 0.95, source_record="src1")

        node = kg.get_node("pref.food")
        assert node is not None
        assert node["locked"] == 0

    def test_owner_confirm_lock(
        self, kg: KnowledgeGraph, lock_mgr: FactLockManager
    ) -> None:
        """Owner can lock any fact regardless of thresholds."""
        kg.add_fact("family.pet", "dog", 0.3, source_record="src1")

        success = lock_mgr.owner_confirm_lock("family.pet")
        assert success is True

        node = kg.get_node("family.pet")
        assert node is not None
        assert node["locked"] == 1
        assert node["locked_by"] == "owner"

    def test_unlock_fact(
        self, kg: KnowledgeGraph, lock_mgr: FactLockManager
    ) -> None:
        """unlock_fact returns True and sets locked=0."""
        kg.add_fact("fact.a", "value_a", 0.5)
        lock_mgr.owner_confirm_lock("fact.a")

        node = kg.get_node("fact.a")
        assert node["locked"] == 1

        success = lock_mgr.unlock_fact("fact.a")
        assert success is True

        node = kg.get_node("fact.a")
        assert node["locked"] == 0
        assert node["locked_at"] is None
        assert node["locked_by"] is None


# ---------------------------------------------------------------------------
# Locked Fact Enforcement Tests
# ---------------------------------------------------------------------------


class TestLockedFactEnforcement:

    def test_locked_fact_blocks_overwrite(self, kg: KnowledgeGraph) -> None:
        """Attempt to update locked fact with different value is blocked."""
        kg.add_fact("family.wife", "Sarah", 0.9, source_record="src1")

        # Lock manually
        kg._db.execute(
            "UPDATE kg_nodes SET locked = 1, locked_at = datetime('now'), locked_by = 'owner' WHERE node_id = ?",
            ("family.wife",),
        )
        kg._db.commit()

        result = kg.add_fact("family.wife", "Jessica", 0.5, source_record="src2")
        assert result is False

        # Original value preserved
        node = kg.get_node("family.wife")
        assert node["label"] == "Sarah"

    def test_locked_fact_creates_contradiction(self, kg: KnowledgeGraph) -> None:
        """Blocked update creates a pending contradiction record."""
        kg.add_fact("family.wife", "Sarah", 0.9, source_record="src1")

        kg._db.execute(
            "UPDATE kg_nodes SET locked = 1, locked_at = datetime('now'), locked_by = 'owner' WHERE node_id = ?",
            ("family.wife",),
        )
        kg._db.commit()

        kg.add_fact("family.wife", "Jessica", 0.5, source_record="src2")

        assert kg.count_pending_contradictions() == 1

        cur = kg._db.execute(
            "SELECT * FROM kg_contradictions WHERE node_id = ?",
            ("family.wife",),
        )
        contradiction = dict(cur.fetchone())
        assert contradiction["existing_value"] == "Sarah"
        assert contradiction["incoming_value"] == "Jessica"
        assert contradiction["status"] == "pending"


# ---------------------------------------------------------------------------
# ContradictionManager Tests
# ---------------------------------------------------------------------------


class TestContradictionManager:

    def _create_contradiction(self, kg: KnowledgeGraph) -> int:
        """Helper: create a locked fact and trigger a contradiction."""
        kg.add_fact("family.wife", "Sarah", 0.9, source_record="src1")
        kg._db.execute(
            "UPDATE kg_nodes SET locked = 1, locked_at = datetime('now'), locked_by = 'owner' WHERE node_id = ?",
            ("family.wife",),
        )
        kg._db.commit()

        kg.add_fact("family.wife", "Jessica", 0.5, source_record="src2")

        # Get the contradiction ID
        cur = kg._db.execute(
            "SELECT contradiction_id FROM kg_contradictions WHERE node_id = 'family.wife'"
        )
        return cur.fetchone()[0]

    def test_contradiction_list_pending(
        self, kg: KnowledgeGraph, contradiction_mgr: ContradictionManager
    ) -> None:
        """ContradictionManager.list_pending returns only pending items."""
        self._create_contradiction(kg)

        pending = contradiction_mgr.list_pending()
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"
        assert pending[0]["node_id"] == "family.wife"

    def test_contradiction_resolve_accept_new(
        self, kg: KnowledgeGraph, contradiction_mgr: ContradictionManager
    ) -> None:
        """Resolving with 'accept_new' updates the node value and unlocks it."""
        cid = self._create_contradiction(kg)

        result = contradiction_mgr.resolve(cid, "accept_new")
        assert result["success"] is True
        assert result["resolution"] == "accept_new"

        # Node should have the new value and be unlocked
        node = kg.get_node("family.wife")
        assert node["label"] == "Jessica"
        assert node["locked"] == 0

        # History should contain the resolution
        history = json.loads(node["history"])
        assert len(history) >= 1
        assert history[-1]["action"] == "accept_new"

        # Contradiction should be resolved
        pending = contradiction_mgr.list_pending()
        assert len(pending) == 0

    def test_contradiction_resolve_keep_old(
        self, kg: KnowledgeGraph, contradiction_mgr: ContradictionManager
    ) -> None:
        """Resolving with 'keep_old' keeps original value unchanged."""
        cid = self._create_contradiction(kg)

        result = contradiction_mgr.resolve(cid, "keep_old")
        assert result["success"] is True
        assert result["resolution"] == "keep_old"

        # Node should still have the old value
        node = kg.get_node("family.wife")
        assert node["label"] == "Sarah"

        # Contradiction should be resolved
        all_resolved = contradiction_mgr.list_all(status="resolved")
        assert len(all_resolved) == 1

    def test_contradiction_resolve_merge(
        self, kg: KnowledgeGraph, contradiction_mgr: ContradictionManager
    ) -> None:
        """Resolving with 'merge' sets the merge_value on the node."""
        cid = self._create_contradiction(kg)

        result = contradiction_mgr.resolve(cid, "merge", merge_value="Sarah (also known as Jessica)")
        assert result["success"] is True
        assert result["resolution"] == "merge"

        # Node should have the merged value
        node = kg.get_node("family.wife")
        assert node["label"] == "Sarah (also known as Jessica)"

        # History should contain the resolution
        history = json.loads(node["history"])
        assert len(history) >= 1
        assert history[-1]["action"] == "merge"
        assert history[-1]["new_value"] == "Sarah (also known as Jessica)"


# ---------------------------------------------------------------------------
# RegressionChecker Tests
# ---------------------------------------------------------------------------


class TestRegressionChecker:

    def test_regression_capture_metrics(
        self, kg: KnowledgeGraph, regression_checker: RegressionChecker
    ) -> None:
        """RegressionChecker.capture_metrics returns correct counts and hash."""
        kg.add_fact("n1", "label_1", 0.8)
        kg.add_fact("n2", "label_2", 0.6)
        kg.add_edge("n1", "n2", "related_to", 0.5)

        # Lock one node
        kg._db.execute(
            "UPDATE kg_nodes SET locked = 1 WHERE node_id = 'n1'"
        )
        kg._db.commit()

        metrics = regression_checker.capture_metrics()
        assert metrics["node_count"] == 2
        assert metrics["edge_count"] == 1
        assert metrics["locked_count"] == 1
        assert "graph_hash" in metrics
        assert len(metrics["graph_hash"]) > 0
        assert "captured_at" in metrics

    def test_regression_compare_no_loss(
        self, kg: KnowledgeGraph, regression_checker: RegressionChecker
    ) -> None:
        """Comparing two identical snapshots returns status='pass'."""
        kg.add_fact("n1", "label_1", 0.8)
        kg.add_fact("n2", "label_2", 0.6)
        kg.add_edge("n1", "n2", "rel", 0.5)

        metrics1 = regression_checker.capture_metrics()
        metrics2 = regression_checker.capture_metrics()

        result = regression_checker.compare(metrics1, metrics2)
        assert result["status"] == "pass"
        assert len(result["discrepancies"]) == 0

    def test_regression_compare_detects_loss(
        self, kg: KnowledgeGraph, regression_checker: RegressionChecker
    ) -> None:
        """Comparing with reduced node count returns discrepancy."""
        kg.add_fact("n1", "label_1", 0.8)
        kg.add_fact("n2", "label_2", 0.6)

        previous = regression_checker.capture_metrics()

        # Simulate loss by creating a "current" with fewer nodes
        current = dict(previous)
        current["node_count"] = 1
        current["graph_hash"] = "different_hash"

        result = regression_checker.compare(previous, current)
        assert result["status"] == "fail"
        discrepancy_types = [d["type"] for d in result["discrepancies"]]
        assert "node_loss" in discrepancy_types

    def test_regression_baseline(
        self, kg: KnowledgeGraph, regression_checker: RegressionChecker
    ) -> None:
        """First run with no previous snapshot returns 'baseline'."""
        kg.add_fact("n1", "label_1", 0.8)
        current = regression_checker.capture_metrics()

        result = regression_checker.compare(None, current)
        assert result["status"] == "baseline"
        assert "Baseline established" in result["message"]

    def test_regression_locked_fact_loss_is_critical(
        self, kg: KnowledgeGraph, regression_checker: RegressionChecker
    ) -> None:
        """Locked fact loss has critical severity."""
        previous = {
            "node_count": 5,
            "edge_count": 3,
            "locked_count": 3,
            "graph_hash": "abc123",
        }
        current = {
            "node_count": 5,
            "edge_count": 3,
            "locked_count": 1,
            "graph_hash": "abc123",
        }

        result = regression_checker.compare(previous, current)
        assert result["status"] == "fail"
        critical = [d for d in result["discrepancies"] if d["severity"] == "critical"]
        assert len(critical) == 1
        assert critical[0]["type"] == "locked_fact_loss"


# ---------------------------------------------------------------------------
# Handler kg=None Degradation Tests
# ---------------------------------------------------------------------------


class TestHandlerDegradation:
    """Verify all knowledge handlers return safe defaults when kg=None."""

    def test_knowledge_status_handler_no_kg(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.knowledge_handlers import KnowledgeStatusHandler
        from jarvis_engine.commands.knowledge_commands import KnowledgeStatusCommand

        handler = KnowledgeStatusHandler(tmp_path, kg=None)
        result = handler.handle(KnowledgeStatusCommand())
        assert result.node_count == 0
        assert result.graph_hash == ""

    def test_contradiction_list_handler_no_kg(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.knowledge_handlers import ContradictionListHandler
        from jarvis_engine.commands.knowledge_commands import ContradictionListCommand

        handler = ContradictionListHandler(tmp_path, kg=None)
        result = handler.handle(ContradictionListCommand())
        assert result.contradictions == []

    def test_contradiction_resolve_handler_no_kg(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.knowledge_handlers import ContradictionResolveHandler
        from jarvis_engine.commands.knowledge_commands import ContradictionResolveCommand

        handler = ContradictionResolveHandler(tmp_path, kg=None)
        result = handler.handle(ContradictionResolveCommand(contradiction_id=1, resolution="keep_old"))
        assert result.success is False
        assert "not available" in result.message

    def test_fact_lock_handler_no_kg(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.knowledge_handlers import FactLockHandler
        from jarvis_engine.commands.knowledge_commands import FactLockCommand

        handler = FactLockHandler(tmp_path, kg=None)
        result = handler.handle(FactLockCommand(node_id="test", action="lock"))
        assert result.success is False
        assert "not available" in result.message

    def test_fact_lock_handler_invalid_action(
        self, kg: KnowledgeGraph, tmp_path: Path
    ) -> None:
        from jarvis_engine.handlers.knowledge_handlers import FactLockHandler
        from jarvis_engine.commands.knowledge_commands import FactLockCommand

        handler = FactLockHandler(tmp_path, kg=kg)
        result = handler.handle(FactLockCommand(node_id="test", action="invalid"))
        assert result.success is False
        assert "Invalid action" in result.message

    def test_knowledge_regression_handler_no_kg(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.knowledge_handlers import KnowledgeRegressionHandler
        from jarvis_engine.commands.knowledge_commands import KnowledgeRegressionCommand

        handler = KnowledgeRegressionHandler(tmp_path, kg=None)
        result = handler.handle(KnowledgeRegressionCommand())
        assert result.report["status"] == "error"
        assert "not available" in result.report["message"]


# ---------------------------------------------------------------------------
# query_relevant_facts Tests
# ---------------------------------------------------------------------------


class TestQueryRelevantFacts:
    """Tests for KnowledgeGraph.query_relevant_facts()."""

    def test_matching_keyword_returns_facts(self, kg: KnowledgeGraph) -> None:
        """Facts whose labels contain a keyword are returned."""
        kg.add_fact("health.med.metformin", "Takes metformin 500mg daily", 0.9)
        kg.add_fact("health.allergy.peanut", "Allergic to peanuts", 0.85)
        kg.add_fact("pref.color", "Favourite colour is blue", 0.7)

        results = kg.query_relevant_facts(["metformin"])
        assert len(results) == 1
        assert results[0]["node_id"] == "health.med.metformin"
        assert results[0]["label"] == "Takes metformin 500mg daily"

    def test_no_matching_keyword_returns_empty(self, kg: KnowledgeGraph) -> None:
        """Keywords that match no labels return an empty list."""
        kg.add_fact("pref.color", "Favourite colour is blue", 0.7)

        results = kg.query_relevant_facts(["nonexistent"])
        assert results == []

    def test_empty_keywords_returns_empty(self, kg: KnowledgeGraph) -> None:
        """Passing an empty keyword list returns an empty list immediately."""
        kg.add_fact("pref.color", "Favourite colour is blue", 0.7)

        results = kg.query_relevant_facts([])
        assert results == []

    def test_multiple_keywords_match_union(self, kg: KnowledgeGraph) -> None:
        """Multiple keywords find all matching facts (OR semantics)."""
        kg.add_fact("health.med.metformin", "Takes metformin 500mg daily", 0.9)
        kg.add_fact("health.allergy.peanut", "Allergic to peanuts", 0.85)
        kg.add_fact("pref.color", "Favourite colour is blue", 0.7)

        results = kg.query_relevant_facts(["metformin", "peanuts"])
        node_ids = {r["node_id"] for r in results}
        assert "health.med.metformin" in node_ids
        assert "health.allergy.peanut" in node_ids
        assert "pref.color" not in node_ids

    def test_min_confidence_filtering(self, kg: KnowledgeGraph) -> None:
        """Facts below min_confidence are excluded."""
        kg.add_fact("health.low", "Low confidence health fact", 0.3)
        kg.add_fact("health.high", "High confidence health fact", 0.9)

        results = kg.query_relevant_facts(["health"], min_confidence=0.5)
        assert len(results) == 1
        assert results[0]["node_id"] == "health.high"

    def test_min_confidence_default_is_0_4(self, kg: KnowledgeGraph) -> None:
        """Default min_confidence=0.4 filters out very low confidence facts."""
        kg.add_fact("fact.low", "fact with 0.3 confidence", 0.3)
        kg.add_fact("fact.ok", "fact with 0.5 confidence", 0.5)

        results = kg.query_relevant_facts(["fact"])
        # 0.3 < 0.4 default, so only the 0.5 fact should appear
        assert len(results) == 1
        assert results[0]["node_id"] == "fact.ok"

    def test_limit_caps_results(self, kg: KnowledgeGraph) -> None:
        """The limit parameter caps the number of results."""
        for i in range(5):
            kg.add_fact(f"fact.{i}", f"test fact number {i}", 0.8)

        results = kg.query_relevant_facts(["fact"], limit=3)
        assert len(results) == 3

    def test_results_sorted_by_confidence_descending(self, kg: KnowledgeGraph) -> None:
        """Results are ordered by confidence from highest to lowest."""
        kg.add_fact("a", "alpha fact", 0.6)
        kg.add_fact("b", "beta fact", 0.9)
        kg.add_fact("c", "gamma fact", 0.75)

        results = kg.query_relevant_facts(["fact"])
        confidences = [r["confidence"] for r in results]
        assert confidences == sorted(confidences, reverse=True)

    def test_result_dict_shape(self, kg: KnowledgeGraph) -> None:
        """Returned dicts have the expected keys."""
        kg.add_fact("n1", "test label", 0.8)

        results = kg.query_relevant_facts(["test"])
        assert len(results) == 1
        row = results[0]
        expected_keys = {"node_id", "label", "node_type", "confidence", "locked", "updated_at"}
        assert expected_keys == set(row.keys())

    def test_sql_injection_safe_percent(self, kg: KnowledgeGraph) -> None:
        """The % character in keywords is escaped and does not act as SQL wildcard."""
        kg.add_fact("pct", "100% success rate", 0.8)
        kg.add_fact("other", "total failure", 0.8)

        # Searching for literal "%" should match "100% success rate" but not "total failure"
        results = kg.query_relevant_facts(["%"])
        node_ids = {r["node_id"] for r in results}
        assert "pct" in node_ids
        assert "other" not in node_ids

    def test_sql_injection_safe_underscore(self, kg: KnowledgeGraph) -> None:
        """The _ character in keywords is escaped and does not act as single-char wildcard."""
        kg.add_fact("under", "uses snake_case naming", 0.8)
        kg.add_fact("nounderscore", "uses camelCase naming", 0.8)

        # Searching for "_" should match only the one with underscore
        results = kg.query_relevant_facts(["_"])
        node_ids = {r["node_id"] for r in results}
        assert "under" in node_ids
        assert "nounderscore" not in node_ids

    def test_keywords_capped_at_20(self, kg: KnowledgeGraph) -> None:
        """More than 20 keywords are silently truncated (no error)."""
        kg.add_fact("n1", "keyword0 label", 0.8)
        keywords = [f"keyword{i}" for i in range(30)]

        # Should not raise and should still find the matching one
        results = kg.query_relevant_facts(keywords)
        assert len(results) >= 1
        assert results[0]["node_id"] == "n1"

    def test_case_insensitive_like_matching(self, kg: KnowledgeGraph) -> None:
        """LIKE matching is case-insensitive for ASCII by default in SQLite."""
        kg.add_fact("n1", "Metformin daily dose", 0.8)

        results = kg.query_relevant_facts(["metformin"])
        assert len(results) == 1
        assert results[0]["node_id"] == "n1"
