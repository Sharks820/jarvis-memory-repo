"""Tests for memory-recall golden tasks and knowledge growth metrics."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from jarvis_engine.growth_tracker import (
    DEFAULT_MEMORY_TASKS,
    MemoryRecallResult,
    MemoryRecallTask,
    evaluate_memory_recall,
    run_memory_eval,
)
from jarvis_engine.learning.metrics import capture_knowledge_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_mock(
    records: list[dict] | None = None,
    vec_results: list[tuple[str, float]] | None = None,
):
    """Create a mock MemoryEngine with search_vec and get_records_batch."""
    engine = MagicMock()
    engine.search_vec.return_value = vec_results or []
    engine.get_records_batch.return_value = records or []
    return engine


def _make_embed_service_mock():
    """Create a mock EmbeddingService."""
    svc = MagicMock()
    svc.embed.return_value = [0.1] * 384
    return svc


def _make_kg_mock(
    nodes: int = 0,
    edges: int = 0,
    locked: int = 0,
    db: sqlite3.Connection | None = None,
):
    """Create a mock KnowledgeGraph."""
    kg = MagicMock()
    kg.count_nodes.return_value = nodes
    kg.count_edges.return_value = edges
    kg.count_locked.return_value = locked
    kg.db = db
    return kg


# ---------------------------------------------------------------------------
# MemoryRecallTask dataclass tests
# ---------------------------------------------------------------------------


def test_memory_recall_task_defaults():
    t = MemoryRecallTask()
    assert t.task_id == ""
    assert t.query == ""
    assert t.must_find_branches == []
    assert t.min_results == 1
    assert t.must_include_in_results == []


def test_default_memory_tasks_has_eighteen():
    assert len(DEFAULT_MEMORY_TASKS) == 18
    ids = {t.task_id for t in DEFAULT_MEMORY_TASKS}
    assert "health_recall" in ids
    assert "gaming_recall" in ids
    assert "ops_recall" in ids
    assert "family_recall" in ids
    assert "coding_recall" in ids


# ---------------------------------------------------------------------------
# evaluate_memory_recall tests
# ---------------------------------------------------------------------------


def test_evaluate_memory_recall_has_results():
    task = MemoryRecallTask("test", "query", ["health"], 1, ["medication"])
    records = [{"branch": "health", "summary": "Takes medication daily"}]
    engine = _make_engine_mock(
        records=records,
        vec_results=[("rec-1", 0.1)],
    )
    embed = _make_embed_service_mock()

    result = evaluate_memory_recall(task, engine, embed)

    assert isinstance(result, MemoryRecallResult)
    assert result.overall_score > 0
    assert result.results_found == 1


def test_evaluate_memory_recall_no_results():
    task = MemoryRecallTask("test", "query", ["health"], 1, ["medication"])
    engine = _make_engine_mock(records=[], vec_results=[])
    embed = _make_embed_service_mock()

    result = evaluate_memory_recall(task, engine, embed)

    assert result.overall_score == 0.0
    assert result.results_found == 0


def test_evaluate_memory_recall_branch_match():
    task = MemoryRecallTask("test", "query", ["health", "ops"], 1, [])
    records = [
        {"branch": "health", "summary": "some health info"},
        {"branch": "ops", "summary": "some ops info"},
    ]
    engine = _make_engine_mock(
        records=records,
        vec_results=[("rec-1", 0.1), ("rec-2", 0.2)],
    )
    embed = _make_embed_service_mock()

    result = evaluate_memory_recall(task, engine, embed)

    assert result.branch_coverage == 1.0
    assert "health" in result.branches_found
    assert "ops" in result.branches_found


def test_evaluate_memory_recall_keyword_match():
    task = MemoryRecallTask("test", "query", [], 1, ["medication", "aspirin"])
    records = [
        {"branch": "health", "summary": "Owner takes medication and aspirin daily"},
    ]
    engine = _make_engine_mock(
        records=records,
        vec_results=[("rec-1", 0.05)],
    )
    embed = _make_embed_service_mock()

    result = evaluate_memory_recall(task, engine, embed)

    assert result.keyword_coverage == 1.0


def test_memory_recall_perfect_score():
    """All criteria met: has_results + full branch + full keyword -> score = 1.0."""
    task = MemoryRecallTask("perfect", "query", ["health"], 1, ["medication"])
    records = [{"branch": "health", "summary": "Takes medication daily"}]
    engine = _make_engine_mock(
        records=records,
        vec_results=[("rec-1", 0.05)],
    )
    embed = _make_embed_service_mock()

    result = evaluate_memory_recall(task, engine, embed)

    assert result.overall_score == 1.0


# ---------------------------------------------------------------------------
# run_memory_eval tests
# ---------------------------------------------------------------------------


def test_run_memory_eval_returns_list():
    tasks = [
        MemoryRecallTask("t1", "q1", [], 1, []),
        MemoryRecallTask("t2", "q2", [], 1, []),
    ]
    engine = _make_engine_mock(vec_results=[])
    embed = _make_embed_service_mock()

    results = run_memory_eval(tasks, engine, embed)

    assert isinstance(results, list)
    assert len(results) == 2
    assert all(isinstance(r, MemoryRecallResult) for r in results)


def test_run_memory_eval_no_engine_raises():
    with pytest.raises(RuntimeError, match="engine is required"):
        run_memory_eval([], None, _make_embed_service_mock())


def test_run_memory_eval_no_embed_raises():
    with pytest.raises(RuntimeError, match="embed_service is required"):
        run_memory_eval([], _make_engine_mock(), None)


# ---------------------------------------------------------------------------
# capture_knowledge_metrics tests
# ---------------------------------------------------------------------------


def test_capture_knowledge_metrics_structure():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE records (record_id TEXT, branch TEXT, summary TEXT)")
    db.execute("INSERT INTO records VALUES ('r1', 'health', 'test')")
    db.commit()

    engine = MagicMock()
    engine._db = db  # noqa: SLF001
    engine.db = db
    engine.db_lock = MagicMock()

    kg = _make_kg_mock(nodes=5, edges=3, locked=1, db=db)
    # kg_nodes doesn't have temporal_type column here -- test graceful fallback

    metrics = capture_knowledge_metrics(kg, engine)

    expected_keys = {
        "total_records",
        "total_facts",
        "total_edges",
        "locked_facts",
        "branches_populated",
        "branch_distribution",
        "temporal_distribution",
        "captured_at",
    }
    assert set(metrics.keys()) == expected_keys
    assert metrics["total_records"] == 1
    assert metrics["total_facts"] == 5
    assert metrics["total_edges"] == 3
    assert metrics["locked_facts"] == 1
    assert metrics["branches_populated"] == 1
    assert metrics["branch_distribution"] == {"health": 1}
    assert isinstance(metrics["captured_at"], str)
    db.close()


def test_capture_knowledge_metrics_empty_db():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE records (record_id TEXT, branch TEXT, summary TEXT)")
    db.commit()

    engine = MagicMock()
    engine._db = db  # noqa: SLF001
    engine.db = db
    engine.db_lock = MagicMock()

    kg = _make_kg_mock(nodes=0, edges=0, locked=0, db=db)

    metrics = capture_knowledge_metrics(kg, engine)

    assert metrics["total_records"] == 0
    assert metrics["total_facts"] == 0
    assert metrics["total_edges"] == 0
    assert metrics["locked_facts"] == 0
    assert metrics["branches_populated"] == 0
    db.close()


def test_capture_knowledge_metrics_temporal_missing():
    """When kg_nodes lacks temporal_type column, temporal_distribution is empty dict."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE records (record_id TEXT, branch TEXT, summary TEXT)")
    db.commit()

    engine = MagicMock()
    engine._db = db  # noqa: SLF001
    engine.db = db
    engine.db_lock = MagicMock()

    # Create kg_nodes without temporal_type column
    db.execute("CREATE TABLE kg_nodes (node_id TEXT, label TEXT)")
    db.commit()

    kg = _make_kg_mock(db=db)

    metrics = capture_knowledge_metrics(kg, engine)
    # Should gracefully handle missing temporal_type column
    assert metrics["temporal_distribution"] == {}
    db.close()


def test_capture_knowledge_metrics_none_engine():
    """When engine is None, total_records and branch_distribution are empty."""
    kg = _make_kg_mock(nodes=2, edges=1, locked=0)

    metrics = capture_knowledge_metrics(kg, None)

    assert metrics["total_records"] == 0
    assert metrics["branch_distribution"] == {}
    assert metrics["total_facts"] == 2
