"""Tests for ProactiveEngine, AdversarialSelfTest, and kg_metrics modules."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


from jarvis_engine.proactive import ProactiveEngine
from jarvis_engine.proactive.triggers import TriggerRule
from jarvis_engine.proactive.notifications import Notifier
from jarvis_engine.proactive.self_test import AdversarialSelfTest
from jarvis_engine.proactive.kg_metrics import (
    append_kg_metrics,
    collect_kg_metrics,
    kg_growth_trend,
    load_kg_history,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    rule_id: str = "r1",
    messages: list[str] | None = None,
    cooldown: int = 60,
    raises: Exception | None = None,
) -> TriggerRule:
    """Create a TriggerRule with a controllable check_fn."""

    def check_fn(snapshot: dict) -> list[str]:
        if raises:
            raise raises
        return messages if messages is not None else []

    return TriggerRule(
        rule_id=rule_id,
        description=f"rule {rule_id}",
        check_fn=check_fn,
        cooldown_minutes=cooldown,
    )


def _make_notifier() -> Notifier:
    """Return a Notifier whose send() is mocked to always succeed."""
    n = Notifier()
    n.send = MagicMock(return_value=True)
    return n


def _make_kg_db() -> MagicMock:
    """Create an in-memory SQLite database with kg_nodes and kg_edges tables,
    wrapped in a mock KnowledgeGraph that exposes .db."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE kg_nodes ("
        "  node_id TEXT PRIMARY KEY,"
        "  label TEXT,"
        "  confidence REAL DEFAULT 0.9,"
        "  locked INTEGER DEFAULT 0,"
        "  temporal_type TEXT DEFAULT 'permanent'"
        ")"
    )
    conn.execute("CREATE TABLE kg_edges (  src TEXT, dst TEXT, relation TEXT)")
    kg = MagicMock()
    kg.db = conn
    return kg, conn


# ===========================================================================
# Module 1: ProactiveEngine (__init__.py)
# ===========================================================================


class TestProactiveEngineInit:
    """Initialization and basic state."""

    def test_init_stores_rules_and_notifier(self):
        notifier = _make_notifier()
        rules = [_make_rule("a"), _make_rule("b")]
        engine = ProactiveEngine(rules=rules, notifier=notifier)
        assert engine._rules is rules
        assert engine._notifier is notifier

    def test_init_empty_cooldowns(self):
        engine = ProactiveEngine(rules=[], notifier=_make_notifier())
        assert engine._last_fired == {}


class TestProactiveEngineEvaluate:
    """Trigger rule evaluation logic."""

    def test_no_rules_returns_empty(self):
        engine = ProactiveEngine(rules=[], notifier=_make_notifier())
        alerts = engine.evaluate({"key": "value"})
        assert alerts == []

    def test_rule_fires_and_returns_alert(self):
        notifier = _make_notifier()
        rule = _make_rule("r1", messages=["hello"])
        engine = ProactiveEngine(rules=[rule], notifier=notifier)
        alerts = engine.evaluate({})
        assert len(alerts) == 1
        assert alerts[0].rule_id == "r1"
        assert alerts[0].message == "hello"
        assert alerts[0].priority == "normal"
        assert alerts[0].timestamp != ""

    def test_rule_with_no_messages_does_not_fire(self):
        notifier = _make_notifier()
        rule = _make_rule("r1", messages=[])
        engine = ProactiveEngine(rules=[rule], notifier=notifier)
        alerts = engine.evaluate({})
        assert alerts == []
        notifier.send.assert_not_called()

    def test_notifier_send_batch_called(self):
        notifier = _make_notifier()
        notifier.send_batch = MagicMock(return_value=1)
        rule = _make_rule("r1", messages=["msg"])
        engine = ProactiveEngine(rules=[rule], notifier=notifier)
        engine.evaluate({})
        notifier.send_batch.assert_called_once()
        batch_alerts = notifier.send_batch.call_args[0][0]
        assert len(batch_alerts) == 1

    def test_multiple_messages_from_one_rule(self):
        notifier = _make_notifier()
        rule = _make_rule("r1", messages=["a", "b", "c"])
        engine = ProactiveEngine(rules=[rule], notifier=notifier)
        alerts = engine.evaluate({})
        assert len(alerts) == 3

    def test_duplicate_messages_are_deduped(self):
        notifier = _make_notifier()
        rule = _make_rule("r1", messages=["dup", "dup", "unique"])
        engine = ProactiveEngine(rules=[rule], notifier=notifier)
        alerts = engine.evaluate({})
        assert len(alerts) == 2
        messages = [a.message for a in alerts]
        assert messages == ["dup", "unique"]

    def test_rule_exception_is_caught_and_skipped(self):
        notifier = _make_notifier()
        bad_rule = _make_rule("bad", raises=RuntimeError("boom"))
        good_rule = _make_rule("good", messages=["ok"])
        engine = ProactiveEngine(rules=[bad_rule, good_rule], notifier=notifier)
        alerts = engine.evaluate({})
        assert len(alerts) == 1
        assert alerts[0].rule_id == "good"


class TestProactiveEngineCooldown:
    """Cooldown enforcement and reset."""

    def test_cooldown_blocks_second_fire(self):
        notifier = _make_notifier()
        rule = _make_rule("r1", messages=["msg"], cooldown=60)
        engine = ProactiveEngine(rules=[rule], notifier=notifier)

        alerts1 = engine.evaluate({})
        assert len(alerts1) == 1

        alerts2 = engine.evaluate({})
        assert len(alerts2) == 0

    def test_cooldown_expired_allows_fire(self):
        notifier = _make_notifier()
        rule = _make_rule("r1", messages=["msg"], cooldown=5)
        engine = ProactiveEngine(rules=[rule], notifier=notifier)

        engine.evaluate({})
        # Backdate last_fired beyond cooldown
        engine._last_fired["r1"] = datetime.now(timezone.utc) - timedelta(minutes=10)
        alerts = engine.evaluate({})
        assert len(alerts) == 1

    def test_reset_cooldowns_clears_state(self):
        notifier = _make_notifier()
        rule = _make_rule("r1", messages=["msg"], cooldown=9999)
        engine = ProactiveEngine(rules=[rule], notifier=notifier)

        engine.evaluate({})
        assert "r1" in engine._last_fired

        engine.reset_cooldowns()
        assert engine._last_fired == {}

    def test_reset_cooldowns_allows_refire(self):
        notifier = _make_notifier()
        rule = _make_rule("r1", messages=["msg"], cooldown=9999)
        engine = ProactiveEngine(rules=[rule], notifier=notifier)

        engine.evaluate({})
        assert engine.evaluate({}) == []

        engine.reset_cooldowns()
        alerts = engine.evaluate({})
        assert len(alerts) == 1


# ===========================================================================
# Module 2: AdversarialSelfTest (self_test.py)
# ===========================================================================


class _FakeRecallResult:
    """Lightweight stand-in for MemoryRecallResult."""

    def __init__(self, task_id: str, overall_score: float):
        self.task_id = task_id
        self.overall_score = overall_score


class TestSelfTestRunMemoryQuiz:
    """run_memory_quiz with mocked growth_tracker."""

    @patch("jarvis_engine.growth_tracker.run_memory_eval")
    @patch("jarvis_engine.growth_tracker.DEFAULT_MEMORY_TASKS", ["t1", "t2"])
    def test_uses_default_tasks_when_none(self, mock_eval):
        mock_eval.return_value = [
            _FakeRecallResult("t1", 0.8),
            _FakeRecallResult("t2", 0.9),
        ]
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.run_memory_quiz(tasks=None)
        # Should have called run_memory_eval with DEFAULT_MEMORY_TASKS
        mock_eval.assert_called_once()
        called_tasks = mock_eval.call_args[0][0]
        assert called_tasks == ["t1", "t2"]
        assert result["tasks_run"] == 2
        assert result["average_score"] == 0.85

    @patch("jarvis_engine.growth_tracker.run_memory_eval")
    def test_custom_tasks(self, mock_eval):
        mock_eval.return_value = [_FakeRecallResult("custom", 0.7)]
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.run_memory_quiz(tasks=["custom_task"])
        called_tasks = mock_eval.call_args[0][0]
        assert called_tasks == ["custom_task"]
        assert result["tasks_run"] == 1
        assert result["average_score"] == 0.7

    @patch("jarvis_engine.growth_tracker.run_memory_eval")
    def test_below_threshold_sends_alert(self, mock_eval):
        mock_eval.return_value = [_FakeRecallResult("t1", 0.2)]
        notifier = MagicMock()
        st = AdversarialSelfTest(
            engine=MagicMock(),
            embed_service=MagicMock(),
            notifier=notifier,
            score_threshold=0.5,
        )
        result = st.run_memory_quiz()
        assert result["below_threshold"] is True
        notifier.send.assert_called_once()
        call_args = notifier.send.call_args
        assert "Memory Quality Alert" in call_args[0][0]

    @patch("jarvis_engine.growth_tracker.run_memory_eval")
    def test_above_threshold_no_alert(self, mock_eval):
        mock_eval.return_value = [_FakeRecallResult("t1", 0.9)]
        notifier = MagicMock()
        st = AdversarialSelfTest(
            engine=MagicMock(),
            embed_service=MagicMock(),
            notifier=notifier,
            score_threshold=0.5,
        )
        result = st.run_memory_quiz()
        assert result["below_threshold"] is False
        notifier.send.assert_not_called()

    @patch("jarvis_engine.growth_tracker.run_memory_eval")
    def test_empty_results(self, mock_eval):
        mock_eval.return_value = []
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.run_memory_quiz()
        assert result["tasks_run"] == 0
        assert result["average_score"] == 0.0
        assert result["below_threshold"] is True

    @patch("jarvis_engine.growth_tracker.run_memory_eval")
    def test_notifier_exception_caught(self, mock_eval):
        mock_eval.return_value = [_FakeRecallResult("t1", 0.1)]
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError("fail")
        st = AdversarialSelfTest(
            engine=MagicMock(),
            embed_service=MagicMock(),
            notifier=notifier,
            score_threshold=0.5,
        )
        # Should not raise
        result = st.run_memory_quiz()
        assert result["below_threshold"] is True

    @patch("jarvis_engine.growth_tracker.run_memory_eval")
    def test_per_task_scores_in_result(self, mock_eval):
        mock_eval.return_value = [
            _FakeRecallResult("a", 0.6),
            _FakeRecallResult("b", 0.8),
        ]
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.run_memory_quiz()
        pts = result["per_task_scores"]
        assert len(pts) == 2
        assert pts[0] == {"task_id": "a", "score": 0.6}
        assert pts[1] == {"task_id": "b", "score": 0.8}


class TestSelfTestSaveQuizResult:
    """save_quiz_result to JSONL files."""

    def test_saves_to_new_file(self, tmp_path: Path):
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        data = {"average_score": 0.85, "tasks_run": 2}
        out = tmp_path / "subdir" / "history.jsonl"
        st.save_quiz_result(data, out)
        assert out.exists()
        loaded = json.loads(out.read_text(encoding="utf-8").strip())
        assert loaded["average_score"] == 0.85

    def test_appends_multiple_results(self, tmp_path: Path):
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        out = tmp_path / "history.jsonl"
        st.save_quiz_result({"score": 1}, out)
        st.save_quiz_result({"score": 2}, out)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


class TestSelfTestCheckRegression:
    """check_regression against history files."""

    def _write_history(self, path: Path, scores: list[float]):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for s in scores:
                f.write(json.dumps({"average_score": s}) + "\n")

    def test_no_history_file(self, tmp_path: Path):
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.check_regression(tmp_path / "nope.jsonl")
        assert result["regression_detected"] is False

    def test_single_entry_no_regression(self, tmp_path: Path):
        p = tmp_path / "h.jsonl"
        self._write_history(p, [0.8])
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.check_regression(p)
        assert result["regression_detected"] is False
        assert result["current_score"] == 0.8

    def test_regression_detected(self, tmp_path: Path):
        # Baseline ~0.9 avg, current 0.5 => 0.5 < 0.9*0.8=0.72 => regression
        p = tmp_path / "h.jsonl"
        self._write_history(p, [0.9, 0.9, 0.9, 0.5])
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.check_regression(p, window=5)
        assert result["regression_detected"] is True
        assert result["drop_pct"] > 0

    def test_no_regression_when_stable(self, tmp_path: Path):
        p = tmp_path / "h.jsonl"
        self._write_history(p, [0.85, 0.86, 0.84, 0.85])
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.check_regression(p, window=5)
        assert result["regression_detected"] is False

    def test_regression_with_malformed_lines(self, tmp_path: Path):
        p = tmp_path / "h.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            f.write("not-json\n")
            f.write(json.dumps({"average_score": 0.9}) + "\n")
            f.write(json.dumps({"average_score": 0.85}) + "\n")
        st = AdversarialSelfTest(engine=MagicMock(), embed_service=MagicMock())
        result = st.check_regression(p, window=5)
        assert result["regression_detected"] is False


# ===========================================================================
# Module 3: kg_metrics
# ===========================================================================


class TestCollectKgMetrics:
    """collect_kg_metrics against an in-memory SQLite DB."""

    def test_empty_kg(self):
        kg, conn = _make_kg_db()
        metrics = collect_kg_metrics(kg)
        assert metrics["node_count"] == 0
        assert metrics["edge_count"] == 0
        assert metrics["branch_counts"] == {}
        assert metrics["avg_confidence"] == 0.0
        conn.close()

    def test_populated_kg(self):
        kg, conn = _make_kg_db()
        conn.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence, locked, temporal_type) "
            "VALUES ('health.bp', 'Blood Pressure', 0.9, 1, 'permanent')"
        )
        conn.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence, locked, temporal_type) "
            "VALUES ('health.hr', 'Heart Rate', 0.7, 0, 'time_sensitive')"
        )
        conn.execute(
            "INSERT INTO kg_nodes (node_id, label, confidence, locked, temporal_type) "
            "VALUES ('finance.rent', 'Rent', 0.3, 0, 'expired')"
        )
        conn.execute(
            "INSERT INTO kg_edges (src, dst, relation) "
            "VALUES ('health.bp', 'health.hr', 'related')"
        )
        conn.execute(
            "INSERT INTO kg_edges (src, dst, relation) "
            "VALUES ('health.bp', 'finance.rent', 'cross_branch_related')"
        )
        conn.commit()

        metrics = collect_kg_metrics(kg)
        assert metrics["node_count"] == 3
        assert metrics["edge_count"] == 2
        assert metrics["branch_counts"]["health"] == 2
        assert metrics["branch_counts"]["finance"] == 1
        assert metrics["cross_branch_edges"] == 1
        # avg confidence = (0.9 + 0.7 + 0.3) / 3 = 0.633...
        assert 0.63 <= metrics["avg_confidence"] <= 0.64
        assert metrics["locked_facts"] == 1
        assert metrics["temporal_breakdown"]["permanent"] == 1
        assert metrics["temporal_breakdown"]["time_sensitive"] == 1
        assert metrics["temporal_breakdown"]["expired"] == 1
        assert metrics["expired_facts"] == 1
        conn.close()

    def test_confidence_distribution(self):
        kg, conn = _make_kg_db()
        # high (>0.8): 0.85, 0.95
        # medium (0.5-0.8): 0.6
        # low (<0.5): 0.3
        for nid, conf in [("a.1", 0.85), ("a.2", 0.95), ("a.3", 0.6), ("a.4", 0.3)]:
            conn.execute(
                "INSERT INTO kg_nodes (node_id, label, confidence) VALUES (?, ?, ?)",
                (nid, nid, conf),
            )
        conn.commit()
        metrics = collect_kg_metrics(kg)
        dist = metrics["confidence_distribution"]
        assert dist["high"] == 2
        assert dist["medium"] == 1
        assert dist["low"] == 1
        conn.close()

    def test_db_error_returns_defaults(self):
        kg = MagicMock()
        kg.db.execute.side_effect = sqlite3.OperationalError("no such table")
        metrics = collect_kg_metrics(kg)
        assert metrics["node_count"] == 0
        assert metrics["edge_count"] == 0

    def test_no_temporal_type_column(self):
        """If temporal_type column is missing, metrics still return without error."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE kg_nodes ("
            "  node_id TEXT PRIMARY KEY, label TEXT, confidence REAL DEFAULT 0.9, locked INTEGER DEFAULT 0"
            ")"
        )
        conn.execute("CREATE TABLE kg_edges (src TEXT, dst TEXT, relation TEXT)")
        conn.execute("INSERT INTO kg_nodes (node_id, label) VALUES ('a.1', 'test')")
        conn.commit()
        kg = MagicMock()
        kg.db = conn
        metrics = collect_kg_metrics(kg)
        assert metrics["node_count"] == 1
        # temporal_breakdown stays at defaults since the inner try/except catches the error
        conn.close()


class TestAppendKgMetrics:
    def test_append_creates_file(self, tmp_path: Path):
        p = tmp_path / "kg.jsonl"
        append_kg_metrics({"node_count": 10}, p)
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8").strip())
        assert data["node_count"] == 10

    def test_append_multiple(self, tmp_path: Path):
        p = tmp_path / "kg.jsonl"
        append_kg_metrics({"v": 1}, p)
        append_kg_metrics({"v": 2}, p)
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_creates_parent_dirs(self, tmp_path: Path):
        p = tmp_path / "deep" / "nested" / "kg.jsonl"
        append_kg_metrics({"ok": True}, p)
        assert p.exists()


class TestLoadKgHistory:
    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        assert load_kg_history(p) == []

    def test_nonexistent_file(self, tmp_path: Path):
        assert load_kg_history(tmp_path / "nope.jsonl") == []

    def test_load_respects_limit(self, tmp_path: Path):
        p = tmp_path / "h.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(json.dumps({"i": i}) + "\n")
        result = load_kg_history(p, limit=3)
        assert len(result) == 3
        # Should be the LAST 3 entries
        assert result[0]["i"] == 7
        assert result[2]["i"] == 9

    def test_load_skips_malformed_json(self, tmp_path: Path):
        p = tmp_path / "h.jsonl"
        p.write_text('{"ok": true}\nnot-json\n{"ok": false}\n', encoding="utf-8")
        result = load_kg_history(p)
        assert len(result) == 2


class TestKgGrowthTrend:
    def test_insufficient_data_single_entry(self):
        result = kg_growth_trend([{"node_count": 10}])
        assert result["trend"] == "insufficient_data"

    def test_insufficient_data_empty(self):
        result = kg_growth_trend([])
        assert result["trend"] == "insufficient_data"

    def test_growing_trend(self):
        history = [
            {
                "node_count": 10,
                "edge_count": 5,
                "avg_confidence": 0.8,
                "cross_branch_edges": 1,
                "ts": "t0",
            },
            {
                "node_count": 20,
                "edge_count": 15,
                "avg_confidence": 0.85,
                "cross_branch_edges": 3,
                "ts": "t1",
            },
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "growing"
        assert result["node_growth"] == 10
        assert result["edge_growth"] == 10
        assert result["cross_branch_growth"] == 2
        assert result["confidence_change"] == 0.05
        assert result["snapshots_analyzed"] == 2

    def test_stable_trend(self):
        entry = {
            "node_count": 10,
            "edge_count": 5,
            "avg_confidence": 0.8,
            "cross_branch_edges": 1,
            "ts": "t0",
        }
        result = kg_growth_trend([entry, entry])
        assert result["trend"] == "stable"
        assert result["node_growth"] == 0
        assert result["edge_growth"] == 0

    def test_declining_trend(self):
        history = [
            {
                "node_count": 20,
                "edge_count": 15,
                "avg_confidence": 0.9,
                "cross_branch_edges": 5,
                "ts": "t0",
            },
            {
                "node_count": 15,
                "edge_count": 10,
                "avg_confidence": 0.85,
                "cross_branch_edges": 3,
                "ts": "t1",
            },
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "declining"
        assert result["node_growth"] == -5
        assert result["edge_growth"] == -5

    def test_mixed_growth_declining(self):
        """Nodes grow but edges shrink => declining."""
        history = [
            {
                "node_count": 10,
                "edge_count": 20,
                "avg_confidence": 0.8,
                "cross_branch_edges": 0,
                "ts": "t0",
            },
            {
                "node_count": 15,
                "edge_count": 10,
                "avg_confidence": 0.8,
                "cross_branch_edges": 0,
                "ts": "t1",
            },
        ]
        result = kg_growth_trend(history)
        assert result["trend"] == "declining"

    def test_snapshot_timestamps_in_result(self):
        history = [
            {"node_count": 1, "edge_count": 1, "ts": "2026-01-01"},
            {"node_count": 2, "edge_count": 2, "ts": "2026-02-01"},
        ]
        result = kg_growth_trend(history)
        assert result["first_snapshot"] == "2026-01-01"
        assert result["last_snapshot"] == "2026-02-01"

    def test_missing_keys_default_to_zero(self):
        history = [{}, {}]
        result = kg_growth_trend(history)
        assert result["trend"] == "stable"
        assert result["node_growth"] == 0
        assert result["edge_growth"] == 0
        assert result["confidence_change"] == 0.0
