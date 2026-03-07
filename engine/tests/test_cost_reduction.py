"""Tests for cost reduction tracking and adversarial self-testing (Plan 09-02).

Covers:
- CostTracker.local_vs_cloud_summary
- cost_reduction_snapshot, load_cost_history, cost_reduction_trend
- AdversarialSelfTest: run_memory_quiz, save_quiz_result, check_regression
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jarvis_engine.gateway.costs import CostTracker
from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.proactive.notifications import Notifier
from jarvis_engine.proactive.cost_tracking import (
    cost_reduction_snapshot,
    cost_reduction_trend,
    load_cost_history,
)
from jarvis_engine.proactive.self_test import AdversarialSelfTest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cost_db(tmp_path: Path) -> CostTracker:
    """Create a CostTracker backed by a temp SQLite DB."""
    db_path = tmp_path / "test_costs.db"
    tracker = CostTracker(db_path)
    yield tracker
    tracker.close()


@pytest.fixture
def history_file(tmp_path: Path) -> Path:
    """Return path for a temp JSONL history file."""
    return tmp_path / "cost_history.jsonl"


@pytest.fixture
def quiz_history(tmp_path: Path) -> Path:
    """Return path for a temp JSONL quiz history file."""
    return tmp_path / "quiz_history.jsonl"


# ---------------------------------------------------------------------------
# 1-5: CostTracker.local_vs_cloud_summary
# ---------------------------------------------------------------------------


def test_local_vs_cloud_summary_empty_db(cost_db: CostTracker) -> None:
    """No costs -> zeros."""
    result = cost_db.local_vs_cloud_summary()
    assert result["local_count"] == 0
    assert result["cloud_count"] == 0
    assert result["failed_count"] == 0
    assert result["total_count"] == 0
    assert result["local_pct"] == 0.0
    assert result["cloud_cost_usd"] == 0.0
    assert result["failed_cost_usd"] == 0.0
    assert result["period_days"] == 30


def test_local_vs_cloud_summary_local_only(cost_db: CostTracker) -> None:
    """All ollama queries -> 100% local."""
    for _ in range(5):
        cost_db.log("qwen3:8b", "ollama", 100, 50, cost_usd=0.0)

    result = cost_db.local_vs_cloud_summary()
    assert result["local_count"] == 5
    assert result["cloud_count"] == 0
    assert result["failed_count"] == 0
    assert result["local_pct"] == 100.0
    assert result["cloud_cost_usd"] == 0.0
    assert result["failed_cost_usd"] == 0.0


def test_local_vs_cloud_summary_cloud_only(cost_db: CostTracker) -> None:
    """All anthropic queries -> 0% local."""
    for _ in range(4):
        cost_db.log("claude-3.5-sonnet", "anthropic", 500, 200, cost_usd=0.01)

    result = cost_db.local_vs_cloud_summary()
    assert result["local_count"] == 0
    assert result["cloud_count"] == 4
    assert result["failed_count"] == 0
    assert result["local_pct"] == 0.0
    assert result["cloud_cost_usd"] == pytest.approx(0.04, abs=1e-6)
    assert result["failed_cost_usd"] == 0.0


def test_local_vs_cloud_summary_mixed(cost_db: CostTracker) -> None:
    """Mixed providers -> correct percentages."""
    for _ in range(3):
        cost_db.log("qwen3:8b", "ollama", 100, 50, cost_usd=0.0)
    for _ in range(7):
        cost_db.log("claude-3.5-sonnet", "anthropic", 500, 200, cost_usd=0.005)

    result = cost_db.local_vs_cloud_summary()
    assert result["local_count"] == 3
    assert result["cloud_count"] == 7
    assert result["failed_count"] == 0
    assert result["total_count"] == 10
    assert result["local_pct"] == 30.0
    assert result["cloud_cost_usd"] == pytest.approx(0.035, abs=1e-6)
    assert result["failed_cost_usd"] == 0.0


def test_local_vs_cloud_summary_respects_days(cost_db: CostTracker) -> None:
    """Older entries excluded when using shorter day window."""
    # Insert a recent row
    cost_db.log("qwen3:8b", "ollama", 100, 50, cost_usd=0.0)

    # Insert an old row directly with a date 60 days ago
    cost_db._db.execute(
        """
        INSERT INTO query_costs (ts, model, provider, input_tokens, output_tokens, cost_usd)
        VALUES (datetime('now', '-60 days'), 'claude-3.5-sonnet', 'anthropic', 500, 200, 0.01)
        """
    )
    cost_db._db.commit()

    # 7d window should only see the recent ollama row
    result = cost_db.local_vs_cloud_summary(days=7)
    assert result["local_count"] == 1
    assert result["cloud_count"] == 0
    assert result["failed_count"] == 0
    assert result["local_pct"] == 100.0

    # 90d window should see both
    result_90 = cost_db.local_vs_cloud_summary(days=90)
    assert result_90["total_count"] == 2


# ---------------------------------------------------------------------------
# 6-9: cost_reduction_snapshot, load_cost_history
# ---------------------------------------------------------------------------


def test_cost_reduction_snapshot_writes_jsonl(
    cost_db: CostTracker, history_file: Path
) -> None:
    """Verify snapshot writes JSONL file."""
    cost_db.log("qwen3:8b", "ollama", 100, 50, cost_usd=0.0)

    cost_reduction_snapshot(cost_db, history_file)

    assert history_file.exists()
    lines = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert "date" in data


def test_cost_reduction_snapshot_structure(
    cost_db: CostTracker, history_file: Path
) -> None:
    """Verify snapshot dict has expected keys."""
    cost_db.log("qwen3:8b", "ollama", 100, 50, cost_usd=0.0)

    snapshot = cost_reduction_snapshot(cost_db, history_file)

    expected_keys = {
        "date",
        "7d_local_pct",
        "30d_local_pct",
        "7d_cloud_cost_usd",
        "30d_cloud_cost_usd",
        "7d_failed_count",
        "30d_failed_count",
        "7d_failed_cost_usd",
        "30d_failed_cost_usd",
        "7d_total_queries",
        "30d_total_queries",
    }
    assert set(snapshot.keys()) == expected_keys


def test_load_cost_history_empty_file(history_file: Path) -> None:
    """Empty file -> empty list."""
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text("", encoding="utf-8")

    result = load_cost_history(history_file)
    assert result == []


def test_load_cost_history_limit(history_file: Path) -> None:
    """Only returns last N entries."""
    history_file.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(10):
        lines.append(json.dumps({"date": f"2026-01-{i + 1:02d}", "30d_local_pct": float(i)}))
    history_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = load_cost_history(history_file, limit=3)
    assert len(result) == 3
    assert result[0]["date"] == "2026-01-08"
    assert result[2]["date"] == "2026-01-10"


# ---------------------------------------------------------------------------
# 10-11: cost_reduction_trend
# ---------------------------------------------------------------------------


def test_cost_reduction_trend_improving() -> None:
    """local_pct increases -> 'improving'."""
    history = [
        {"date": "2026-01-01", "30d_local_pct": 20.0},
        {"date": "2026-01-15", "30d_local_pct": 30.0},
        {"date": "2026-02-01", "30d_local_pct": 45.0},
    ]
    result = cost_reduction_trend(history)
    assert result["trend"] == "improving"
    assert result["change_pct"] == 25.0
    assert result["first_local_pct"] == 20.0
    assert result["last_local_pct"] == 45.0


def test_cost_reduction_trend_declining() -> None:
    """local_pct decreases -> 'declining'."""
    history = [
        {"date": "2026-01-01", "30d_local_pct": 60.0},
        {"date": "2026-02-01", "30d_local_pct": 50.0},
    ]
    result = cost_reduction_trend(history)
    assert result["trend"] == "declining"
    assert result["change_pct"] == -10.0


def test_cost_reduction_trend_stable() -> None:
    """Small change -> 'stable'."""
    history = [
        {"date": "2026-01-01", "30d_local_pct": 50.0},
        {"date": "2026-02-01", "30d_local_pct": 51.0},
    ]
    result = cost_reduction_trend(history)
    assert result["trend"] == "stable"


def test_cost_reduction_trend_empty() -> None:
    """Empty history -> stable defaults."""
    result = cost_reduction_trend([])
    assert result["trend"] == "stable"
    assert result["first_date"] == ""
    assert result["last_date"] == ""


# ---------------------------------------------------------------------------
# 12-14: AdversarialSelfTest.run_memory_quiz
# ---------------------------------------------------------------------------


def _make_mock_engine_and_embed():
    """Create mocked engine and embed_service that return controlled results."""
    engine = MagicMock(spec=MemoryEngine)
    embed_service = MagicMock(spec=EmbeddingService)

    # embed returns a dummy vector
    embed_service.embed.return_value = [0.1] * 768

    # search_vec returns some result IDs
    engine.search_vec.return_value = [(1, 0.1), (2, 0.2)]

    # get_records_batch returns records with matching keywords and branches
    engine.get_records_batch.return_value = [
        {"branch": "health", "summary": "Owner takes medication daily"},
        {"branch": "gaming", "summary": "Owner plays video games regularly"},
    ]

    return engine, embed_service


def test_adversarial_self_test_run() -> None:
    """Mock engine, verify returns scores dict."""
    engine, embed_service = _make_mock_engine_and_embed()

    tester = AdversarialSelfTest(engine, embed_service, score_threshold=0.5)
    result = tester.run_memory_quiz()

    assert "tasks_run" in result
    assert "average_score" in result
    assert "below_threshold" in result
    assert "per_task_scores" in result
    assert "timestamp" in result
    assert result["tasks_run"] > 0
    assert isinstance(result["average_score"], float)


def test_adversarial_self_test_alerts_on_low_score() -> None:
    """Score below threshold -> notifier.send() called."""
    engine = MagicMock(spec=MemoryEngine)
    embed_service = MagicMock(spec=EmbeddingService)
    notifier = MagicMock(spec=Notifier)

    # Make engine return empty results so scores are 0
    embed_service.embed.return_value = [0.1] * 768
    engine.search_vec.return_value = []

    tester = AdversarialSelfTest(
        engine, embed_service, notifier=notifier, score_threshold=0.5
    )
    result = tester.run_memory_quiz()

    assert result["below_threshold"] is True
    notifier.send.assert_called_once()


def test_adversarial_self_test_no_alert_above_threshold() -> None:
    """Score above threshold -> no notifier.send() call."""
    engine, embed_service = _make_mock_engine_and_embed()
    notifier = MagicMock(spec=Notifier)

    tester = AdversarialSelfTest(
        engine, embed_service, notifier=notifier, score_threshold=0.0
    )
    result = tester.run_memory_quiz()

    assert result["below_threshold"] is False
    notifier.send.assert_not_called()


# ---------------------------------------------------------------------------
# 15-17: check_regression, save_quiz_result
# ---------------------------------------------------------------------------


def test_check_regression_detected(quiz_history: Path) -> None:
    """Dropping scores -> regression_detected=True."""
    quiz_history.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {"average_score": 0.8, "tasks_run": 5},
        {"average_score": 0.8, "tasks_run": 5},
        {"average_score": 0.8, "tasks_run": 5},
        {"average_score": 0.8, "tasks_run": 5},
        {"average_score": 0.3, "tasks_run": 5},  # Big drop
    ]
    lines = [json.dumps(e) for e in entries]
    quiz_history.write_text("\n".join(lines) + "\n", encoding="utf-8")

    engine, embed_service = _make_mock_engine_and_embed()
    tester = AdversarialSelfTest(engine, embed_service)
    result = tester.check_regression(quiz_history)

    assert result["regression_detected"] is True
    assert result["current_score"] == pytest.approx(0.3, abs=1e-4)
    assert result["baseline_score"] == pytest.approx(0.8, abs=1e-4)
    assert result["drop_pct"] > 0


def test_check_regression_not_detected(quiz_history: Path) -> None:
    """Stable scores -> regression_detected=False."""
    quiz_history.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {"average_score": 0.7, "tasks_run": 5},
        {"average_score": 0.75, "tasks_run": 5},
        {"average_score": 0.72, "tasks_run": 5},
        {"average_score": 0.73, "tasks_run": 5},
        {"average_score": 0.71, "tasks_run": 5},
    ]
    lines = [json.dumps(e) for e in entries]
    quiz_history.write_text("\n".join(lines) + "\n", encoding="utf-8")

    engine, embed_service = _make_mock_engine_and_embed()
    tester = AdversarialSelfTest(engine, embed_service)
    result = tester.check_regression(quiz_history)

    assert result["regression_detected"] is False


def test_save_quiz_result_appends_jsonl(quiz_history: Path) -> None:
    """Verify JSONL append works correctly."""
    engine, embed_service = _make_mock_engine_and_embed()
    tester = AdversarialSelfTest(engine, embed_service)

    result1 = {"average_score": 0.8, "tasks_run": 5, "timestamp": "2026-01-01T00:00:00"}
    result2 = {"average_score": 0.7, "tasks_run": 5, "timestamp": "2026-01-02T00:00:00"}

    tester.save_quiz_result(result1, quiz_history)
    tester.save_quiz_result(result2, quiz_history)

    lines = quiz_history.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["average_score"] == 0.8
    assert json.loads(lines[1])["average_score"] == 0.7


def test_check_regression_empty_history(quiz_history: Path) -> None:
    """No history file -> no regression."""
    engine, embed_service = _make_mock_engine_and_embed()
    tester = AdversarialSelfTest(engine, embed_service)
    result = tester.check_regression(quiz_history)

    assert result["regression_detected"] is False
    assert result["current_score"] == 0.0


def test_load_cost_history_nonexistent_file(tmp_path: Path) -> None:
    """Non-existent file -> empty list."""
    result = load_cost_history(tmp_path / "nonexistent.jsonl")
    assert result == []
