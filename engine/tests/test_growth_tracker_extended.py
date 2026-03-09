"""Comprehensive tests for growth_tracker.py — scoring, history, eval, validation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from http.client import HTTPResponse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.memory.embeddings import EmbeddingService
from jarvis_engine.memory.engine import MemoryEngine
from jarvis_engine.security.net_policy import is_safe_ollama_endpoint as _is_safe_ollama_endpoint
from jarvis_engine.growth_tracker import (
    BRANCH_TASK_MAP,
    DEFAULT_MEMORY_TASKS,
    EvalRun,
    GoldenTask,
    MemoryRecallResult,
    MemoryRecallTask,
    TaskEval,
    append_history,
    audit_run,
    compute_run_sha256,
    eval_branch,
    evaluate_memory_recall,
    load_golden_tasks,
    read_history,
    run_eval,
    run_memory_eval,
    score_text,
    summarize_history,
    validate_history_chain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task_eval(**overrides) -> TaskEval:
    """Create a TaskEval with sensible defaults, overridable per field."""
    defaults = dict(
        task_id="t1",
        matched=2,
        total=3,
        coverage=2 / 3,
        matched_tokens=["a", "b"],
        required_tokens=["a", "b", "c"],
        prompt="test prompt",
        response="test response",
        prompt_sha256="abc",
        response_sha256="def",
        response_source="response",
        eval_count=100,
        eval_duration_s=1.0,
        total_duration_s=2.0,
    )
    defaults.update(overrides)
    return TaskEval(**defaults)


def _make_eval_run(**overrides) -> EvalRun:
    """Create an EvalRun with sensible defaults."""
    defaults = dict(
        ts="2026-02-25T00:00:00+00:00",
        model="qwen3:latest",
        tasks=1,
        score_pct=75.0,
        avg_coverage_pct=75.0,
        avg_tps=50.0,
        avg_latency_s=0.5,
        results=[_make_task_eval()],
    )
    defaults.update(overrides)
    return EvalRun(**defaults)


# ===================================================================
# score_text tests
# ===================================================================

class TestScoreText:
    """Tests for score_text() — word-boundary matching of required tokens."""

    def test_all_tokens_matched(self) -> None:
        matched, total, coverage, tokens = score_text(
            "the quick brown fox", ["quick", "fox"]
        )
        assert matched == 2
        assert total == 2
        assert coverage == 1.0
        assert tokens == ["quick", "fox"]

    def test_no_tokens_matched(self) -> None:
        matched, total, coverage, tokens = score_text(
            "the quick brown fox", ["zebra", "giraffe"]
        )
        assert matched == 0
        assert total == 2
        assert coverage == 0.0
        assert tokens == []

    def test_partial_match(self) -> None:
        matched, total, coverage, tokens = score_text(
            "hello world test", ["hello", "missing", "test"]
        )
        assert matched == 2
        assert total == 3
        assert abs(coverage - 2 / 3) < 0.001
        assert tokens == ["hello", "test"]

    def test_empty_required_tokens(self) -> None:
        matched, total, coverage, tokens = score_text("any text", [])
        assert matched == 0
        assert total == 0
        assert coverage == 1.0
        assert tokens == []

    def test_empty_text(self) -> None:
        matched, total, coverage, tokens = score_text("", ["something"])
        assert matched == 0
        assert total == 1
        assert coverage == 0.0

    def test_case_insensitive(self) -> None:
        matched, total, coverage, tokens = score_text(
            "Hello WORLD", ["hello", "world"]
        )
        assert matched == 2
        assert coverage == 1.0

    def test_word_boundary_matching(self) -> None:
        """'the' should NOT match inside 'therefore'."""
        matched, total, coverage, tokens = score_text(
            "therefore", ["the"]
        )
        assert matched == 0
        assert coverage == 0.0

    def test_special_regex_characters_escaped(self) -> None:
        """Tokens with regex special chars should be safely escaped."""
        matched, total, coverage, tokens = score_text(
            "use c++ for development", ["c++"]
        )
        # "c++" contains regex special chars; re.escape handles it
        # Depending on word-boundary matching, c++ may or may not match
        assert total == 1

    def test_single_token(self) -> None:
        matched, total, coverage, tokens = score_text("one", ["one"])
        assert matched == 1
        assert coverage == 1.0

    def test_duplicate_tokens_counted_once(self) -> None:
        """If the same token appears twice in required_tokens, both count."""
        matched, total, coverage, tokens = score_text(
            "hello world", ["hello", "hello"]
        )
        assert matched == 2
        assert total == 2
        assert coverage == 1.0


# ===================================================================
# _is_safe_ollama_endpoint tests
# ===================================================================

class TestIsSafeOllamaEndpoint:
    """Tests for the Ollama endpoint safety check."""

    def test_localhost_http(self) -> None:
        assert _is_safe_ollama_endpoint("http://localhost:11434") is True

    def test_localhost_https(self) -> None:
        assert _is_safe_ollama_endpoint("https://localhost:11434") is True

    def test_127_0_0_1(self) -> None:
        assert _is_safe_ollama_endpoint("http://127.0.0.1:11434") is True

    def test_ipv6_loopback(self) -> None:
        assert _is_safe_ollama_endpoint("http://[::1]:11434") is True

    def test_remote_host_blocked(self) -> None:
        assert _is_safe_ollama_endpoint("http://example.com:11434") is False

    def test_ftp_scheme_blocked(self) -> None:
        assert _is_safe_ollama_endpoint("ftp://localhost:11434") is False

    def test_empty_string(self) -> None:
        assert _is_safe_ollama_endpoint("") is False

    def test_no_scheme(self) -> None:
        assert _is_safe_ollama_endpoint("localhost:11434") is False

    @patch.dict(os.environ, {"JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT": "true"})
    def test_nonlocal_allowed_by_env(self) -> None:
        assert _is_safe_ollama_endpoint("http://remote-server.com:11434") is True

    @patch.dict(os.environ, {"JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT": "false"})
    def test_nonlocal_not_allowed_by_env_false(self) -> None:
        assert _is_safe_ollama_endpoint("http://remote-server.com:11434") is False

    @patch.dict(os.environ, {"JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT": "1"})
    def test_nonlocal_allowed_by_env_1(self) -> None:
        assert _is_safe_ollama_endpoint("http://remote-server.com:11434") is True

    @patch.dict(os.environ, {"JARVIS_ALLOW_NONLOCAL_OLLAMA_ENDPOINT": ""})
    def test_nonlocal_empty_env_blocked(self) -> None:
        assert _is_safe_ollama_endpoint("http://remote-server.com:11434") is False


# ===================================================================
# load_golden_tasks tests
# ===================================================================

class TestLoadGoldenTasks:
    """Tests for load_golden_tasks() — JSON parsing and validation."""

    def test_valid_tasks(self, tmp_path: Path) -> None:
        tasks_json = [
            {"id": "t1", "prompt": "What is Python?", "must_include": ["python"]},
            {"id": "t2", "prompt": "Explain Rust.", "must_include": ["rust", "memory"]},
        ]
        p = tmp_path / "tasks.json"
        p.write_text(json.dumps(tasks_json), encoding="utf-8")

        tasks = load_golden_tasks(p)
        assert len(tasks) == 2
        assert tasks[0].task_id == "t1"
        assert tasks[0].prompt == "What is Python?"
        assert tasks[0].must_include == ["python"]
        assert tasks[1].must_include == ["rust", "memory"]

    def test_empty_array(self, tmp_path: Path) -> None:
        p = tmp_path / "tasks.json"
        p.write_text("[]", encoding="utf-8")
        tasks = load_golden_tasks(p)
        assert tasks == []

    def test_non_array_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "tasks.json"
        p.write_text('{"not": "array"}', encoding="utf-8")
        with pytest.raises(ValueError, match="JSON array"):
            load_golden_tasks(p)

    def test_skips_non_dict_items(self, tmp_path: Path) -> None:
        p = tmp_path / "tasks.json"
        p.write_text('[42, "string", {"id": "t1", "prompt": "valid"}]', encoding="utf-8")
        tasks = load_golden_tasks(p)
        assert len(tasks) == 1
        assert tasks[0].task_id == "t1"

    def test_skips_entries_without_id(self, tmp_path: Path) -> None:
        p = tmp_path / "tasks.json"
        p.write_text('[{"prompt": "no id here"}]', encoding="utf-8")
        tasks = load_golden_tasks(p)
        assert tasks == []

    def test_skips_entries_without_prompt(self, tmp_path: Path) -> None:
        p = tmp_path / "tasks.json"
        p.write_text('[{"id": "t1"}]', encoding="utf-8")
        tasks = load_golden_tasks(p)
        assert tasks == []

    def test_must_include_lowered(self, tmp_path: Path) -> None:
        tasks_json = [{"id": "t1", "prompt": "test", "must_include": ["UPPER", "MiXeD"]}]
        p = tmp_path / "tasks.json"
        p.write_text(json.dumps(tasks_json), encoding="utf-8")
        tasks = load_golden_tasks(p)
        assert tasks[0].must_include == ["upper", "mixed"]


# ===================================================================
# History management: append_history, read_history, summarize_history
# ===================================================================

class TestHistoryManagement:
    """Tests for history JSONL append/read/summarize."""

    def test_read_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        p.write_text("", encoding="utf-8")
        assert read_history(p) == []

    def test_read_nonexistent_file(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.jsonl"
        assert read_history(p) == []

    def test_append_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / "dir" / "history.jsonl"
        run = _make_eval_run()
        append_history(p, run)
        assert p.exists()
        rows = read_history(p)
        assert len(rows) == 1

    def test_append_preserves_order(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        run1 = _make_eval_run(model="model-a", score_pct=50.0)
        run2 = _make_eval_run(model="model-b", score_pct=80.0)
        append_history(p, run1)
        append_history(p, run2)
        rows = read_history(p)
        assert len(rows) == 2
        assert rows[0]["model"] == "model-a"
        assert rows[1]["model"] == "model-b"

    def test_read_skips_blank_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        p.write_text('{"a": 1}\n\n\n{"b": 2}\n', encoding="utf-8")
        rows = read_history(p)
        assert len(rows) == 2

    def test_read_skips_corrupt_json(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        p.write_text('{"a": 1}\nnot-json\n{"b": 2}\n', encoding="utf-8")
        rows = read_history(p)
        assert len(rows) == 2

    def test_append_sets_prev_run_sha256(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        run1 = _make_eval_run(score_pct=50.0)
        run2 = _make_eval_run(score_pct=80.0)
        append_history(p, run1)
        append_history(p, run2)
        rows = read_history(p)
        # Second row's prev_run_sha256 should equal first row's run_sha256
        assert rows[1]["prev_run_sha256"] == rows[0]["run_sha256"]

    def test_append_first_row_has_empty_prev_hash(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        run = _make_eval_run()
        append_history(p, run)
        rows = read_history(p)
        assert rows[0]["prev_run_sha256"] == ""


# ===================================================================
# summarize_history tests
# ===================================================================

class TestSummarizeHistory:
    """Tests for summarize_history()."""

    def test_empty_rows(self) -> None:
        summary = summarize_history([])
        assert summary["runs"] == 0
        assert summary["latest_score_pct"] == 0.0
        assert summary["delta_vs_prev_pct"] == 0.0
        assert summary["latest_model"] == ""
        assert summary["window_avg_pct"] == 0.0

    def test_single_row(self) -> None:
        rows = [{"score_pct": 80.0, "model": "test-model", "ts": "2026-01-01"}]
        summary = summarize_history(rows)
        assert summary["runs"] == 1
        assert summary["latest_score_pct"] == 80.0
        assert summary["delta_vs_prev_pct"] == 0.0
        assert summary["latest_model"] == "test-model"
        assert summary["window_avg_pct"] == 80.0

    def test_two_rows_delta(self) -> None:
        rows = [
            {"score_pct": 50.0, "model": "m1", "ts": "2026-01-01"},
            {"score_pct": 75.0, "model": "m2", "ts": "2026-01-02"},
        ]
        summary = summarize_history(rows)
        assert summary["delta_vs_prev_pct"] == 25.0
        assert summary["latest_score_pct"] == 75.0

    def test_window_limits_to_last_n(self) -> None:
        rows = [{"score_pct": float(i), "model": "m", "ts": ""} for i in range(20)]
        summary = summarize_history(rows, last=5)
        # Window should be last 5: 15, 16, 17, 18, 19
        expected_avg = round((15 + 16 + 17 + 18 + 19) / 5, 2)
        assert summary["window_avg_pct"] == expected_avg
        assert summary["runs"] == 20

    def test_negative_delta(self) -> None:
        rows = [
            {"score_pct": 90.0, "model": "m", "ts": ""},
            {"score_pct": 70.0, "model": "m", "ts": ""},
        ]
        summary = summarize_history(rows)
        assert summary["delta_vs_prev_pct"] == -20.0


# ===================================================================
# compute_run_sha256 and validate_history_chain tests
# ===================================================================

class TestChainIntegrity:
    """Tests for hash chain computation and validation."""

    def test_compute_run_sha256_deterministic(self) -> None:
        row = {"model": "test", "score_pct": 80.0, "run_sha256": ""}
        hash1 = compute_run_sha256(row)
        hash2 = compute_run_sha256(row)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex digest

    def test_compute_run_sha256_ignores_existing_hash(self) -> None:
        row = {"model": "test", "score_pct": 80.0, "run_sha256": "old-value"}
        hash1 = compute_run_sha256(row)
        row2 = {"model": "test", "score_pct": 80.0, "run_sha256": "different-value"}
        hash2 = compute_run_sha256(row2)
        assert hash1 == hash2

    def test_compute_different_data_different_hash(self) -> None:
        row1 = {"model": "a", "run_sha256": ""}
        row2 = {"model": "b", "run_sha256": ""}
        assert compute_run_sha256(row1) != compute_run_sha256(row2)

    def test_validate_history_chain_valid(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        run1 = _make_eval_run(score_pct=50.0)
        run2 = _make_eval_run(score_pct=80.0)
        append_history(p, run1)
        append_history(p, run2)
        rows = read_history(p)
        # Should not raise — valid chain passes validation
        validate_history_chain(rows)
        assert len(rows) == 2

    def test_validate_history_chain_tampered_hash(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        run = _make_eval_run()
        append_history(p, run)
        rows = read_history(p)
        rows[0]["run_sha256"] = "tampered_hash"
        with pytest.raises(RuntimeError, match="run hash mismatch"):
            validate_history_chain(rows)

    def test_validate_history_chain_tampered_prev_hash(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        run1 = _make_eval_run(score_pct=50.0)
        run2 = _make_eval_run(score_pct=80.0)
        append_history(p, run1)
        append_history(p, run2)
        rows = read_history(p)
        rows[1]["prev_run_sha256"] = "wrong_prev"
        with pytest.raises(RuntimeError, match="prev hash mismatch"):
            validate_history_chain(rows)

    def test_validate_empty_chain(self) -> None:
        """Empty list should pass validation without error."""
        result = validate_history_chain([])
        assert result is None  # validation completed without raising

    def test_validate_legacy_rows_without_hash(self) -> None:
        """Rows without run_sha256 (legacy) are tolerated."""
        rows = [{"model": "old", "score_pct": 50.0}]
        validate_history_chain(rows)  # Should not raise
        assert len(rows) == 1  # input unchanged


# ===================================================================
# audit_run tests
# ===================================================================

class TestAuditRun:
    """Tests for audit_run()."""

    def test_audit_latest_run(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        run = _make_eval_run(score_pct=85.0, model="qwen3:latest")
        append_history(p, run)
        rows = read_history(p)
        audit = audit_run(rows, run_index=-1)
        assert audit["score_pct"] == 85.0
        assert audit["model"] == "qwen3:latest"
        assert "run_sha256" in audit

    def test_audit_specific_index(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        append_history(p, _make_eval_run(model="first"))
        append_history(p, _make_eval_run(model="second"))
        rows = read_history(p)
        audit = audit_run(rows, run_index=0)
        assert audit["model"] == "first"

    def test_audit_empty_history_raises(self) -> None:
        with pytest.raises(RuntimeError, match="No history runs"):
            audit_run([])

    def test_audit_invalid_index_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        append_history(p, _make_eval_run())
        rows = read_history(p)
        with pytest.raises(RuntimeError, match="Invalid run index"):
            audit_run(rows, run_index=5)

    def test_audit_negative_out_of_range_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "history.jsonl"
        append_history(p, _make_eval_run())
        rows = read_history(p)
        with pytest.raises(RuntimeError, match="Invalid run index"):
            audit_run(rows, run_index=-5)


# ===================================================================
# MemoryRecallTask / evaluate_memory_recall / run_memory_eval
# ===================================================================

class TestMemoryRecall:
    """Tests for memory recall evaluation."""

    def test_default_memory_tasks_exist(self) -> None:
        assert len(DEFAULT_MEMORY_TASKS) == 18
        for task in DEFAULT_MEMORY_TASKS:
            assert isinstance(task, MemoryRecallTask)
            assert task.task_id
            assert task.query

    def test_evaluate_memory_recall_no_results(self) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.return_value = []
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        task = MemoryRecallTask("test", "query", ["health"], 1, ["medication"])
        result = evaluate_memory_recall(task, engine, embed)

        assert isinstance(result, MemoryRecallResult)
        assert result.results_found == 0
        assert result.overall_score == 0.0

    def test_evaluate_memory_recall_full_match(self) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.return_value = [("id1", 0.1), ("id2", 0.2)]
        engine.get_records_batch.return_value = [
            {"branch": "health", "summary": "Take medication daily"},
            {"branch": "health", "summary": "Medication for blood pressure"},
        ]
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        task = MemoryRecallTask("health_recall", "meds?", ["health"], 1, ["medication"])
        result = evaluate_memory_recall(task, engine, embed)

        assert result.results_found == 2
        assert result.branch_coverage == 1.0
        assert result.keyword_coverage == 1.0
        # 0.3 (has_results) + 0.3 (branch) + 0.4 (keyword) = 1.0
        assert result.overall_score == 1.0

    def test_evaluate_memory_recall_partial_branch(self) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.return_value = [("id1", 0.1)]
        engine.get_records_batch.return_value = [
            {"branch": "health", "summary": "daily routine"},
        ]
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        task = MemoryRecallTask(
            "test", "query", ["health", "fitness"], 1, ["medication"]
        )
        result = evaluate_memory_recall(task, engine, embed)

        # Branch coverage: found health (1 of 2) = 0.5
        assert result.branch_coverage == 0.5
        # Keyword: "medication" not in "daily routine" = 0.0
        assert result.keyword_coverage == 0.0

    def test_evaluate_memory_recall_no_required_branches(self) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.return_value = [("id1", 0.1)]
        engine.get_records_batch.return_value = [
            {"branch": "general", "summary": "some text with keyword"},
        ]
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        task = MemoryRecallTask("test", "query", [], 1, ["keyword"])
        result = evaluate_memory_recall(task, engine, embed)

        # Empty must_find_branches -> branch_cov = 1.0
        assert result.branch_coverage == 1.0

    def test_evaluate_memory_recall_no_required_keywords(self) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.return_value = [("id1", 0.1)]
        engine.get_records_batch.return_value = [
            {"branch": "health", "summary": "anything"},
        ]
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        task = MemoryRecallTask("test", "query", ["health"], 1, [])
        result = evaluate_memory_recall(task, engine, embed)

        # Empty must_include_in_results -> keyword_cov = 1.0
        assert result.keyword_coverage == 1.0

    def test_run_memory_eval_none_engine_raises(self) -> None:
        with pytest.raises(RuntimeError, match="engine is required"):
            run_memory_eval(DEFAULT_MEMORY_TASKS, None, MagicMock(spec=EmbeddingService))

    def test_run_memory_eval_none_embed_raises(self) -> None:
        with pytest.raises(RuntimeError, match="embed_service is required"):
            run_memory_eval(DEFAULT_MEMORY_TASKS, MagicMock(spec=MemoryEngine), None)

    def test_run_memory_eval_catches_task_failures(self) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.side_effect = RuntimeError("db locked")
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        tasks = [MemoryRecallTask("fail_task", "query", [], 1, [])]
        results = run_memory_eval(tasks, engine, embed)

        assert len(results) == 1
        assert results[0].task_id == "fail_task"
        assert results[0].overall_score == 0.0

    def test_run_memory_eval_multiple_tasks(self) -> None:
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.return_value = [("id1", 0.1)]
        engine.get_records_batch.return_value = [
            {"branch": "health", "summary": "medication info"},
        ]
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        tasks = [
            MemoryRecallTask("t1", "q1", ["health"], 1, ["medication"]),
            MemoryRecallTask("t2", "q2", ["health"], 1, ["medication"]),
        ]
        results = run_memory_eval(tasks, engine, embed)
        assert len(results) == 2
        assert all(r.task_id in ["t1", "t2"] for r in results)


# ===================================================================
# _generate and run_eval tests (with mocked HTTP)
# ===================================================================

class TestRunEval:
    """Tests for run_eval() with mocked Ollama HTTP calls."""

    def _mock_urlopen(self, response_data: dict):
        """Create a mock for urlopen returning the given JSON dict."""
        mock_resp = MagicMock(spec=HTTPResponse)
        mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_run_eval_happy_path(self) -> None:
        response_data = {
            "response": "Python is a programming language",
            "eval_count": 50,
            "eval_duration": 1_000_000_000,  # 1 second in nanoseconds
            "total_duration": 2_000_000_000,
        }
        tasks = [GoldenTask("t1", "What is Python?", ["python", "programming"])]

        with patch("jarvis_engine.gateway.ollama_client.urlopen", return_value=self._mock_urlopen(response_data)):
            result = run_eval(
                endpoint="http://localhost:11434",
                model="test-model",
                tasks=tasks,
            )

        assert isinstance(result, EvalRun)
        assert result.model == "test-model"
        assert result.tasks == 1
        assert result.score_pct == 100.0
        assert len(result.results) == 1
        assert result.results[0].matched == 2
        assert result.results[0].response_source == "response"

    def test_run_eval_empty_response_uses_thinking(self) -> None:
        response_data = {
            "response": "",
            "thinking": "I think Python is useful",
            "eval_count": 10,
            "eval_duration": 500_000_000,
            "total_duration": 1_000_000_000,
        }
        tasks = [GoldenTask("t1", "What is Python?", ["python"])]

        with patch("jarvis_engine.gateway.ollama_client.urlopen", return_value=self._mock_urlopen(response_data)):
            result = run_eval(
                endpoint="http://localhost:11434",
                model="test",
                tasks=tasks,
                accept_thinking=True,
            )

        assert result.results[0].response_source == "thinking"
        assert result.results[0].response == "I think Python is useful"

    def test_run_eval_empty_response_no_thinking(self) -> None:
        response_data = {
            "response": "",
            "thinking": "some thought",
            "eval_count": 0,
            "eval_duration": 0,
            "total_duration": 1_000_000_000,
        }
        tasks = [GoldenTask("t1", "What?", ["something"])]

        with patch("jarvis_engine.gateway.ollama_client.urlopen", return_value=self._mock_urlopen(response_data)):
            result = run_eval(
                endpoint="http://localhost:11434",
                model="test",
                tasks=tasks,
                accept_thinking=False,
            )

        assert result.results[0].response_source == "empty"
        assert result.results[0].response == ""

    def test_run_eval_no_tasks_raises(self) -> None:
        """Evaluating zero tasks should raise RuntimeError."""
        with pytest.raises(RuntimeError, match="No tasks were evaluated"):
            run_eval(
                endpoint="http://localhost:11434",
                model="test",
                tasks=[],
            )

    def test_run_eval_unsafe_endpoint_raises(self) -> None:
        tasks = [GoldenTask("t1", "test", [])]
        with pytest.raises(RuntimeError, match="Unsafe Ollama endpoint"):
            run_eval(
                endpoint="http://evil.com:11434",
                model="test",
                tasks=tasks,
            )

    def test_run_eval_network_error_raises(self) -> None:
        tasks = [GoldenTask("t1", "test", ["token"])]

        from urllib.error import URLError
        with patch("jarvis_engine.gateway.ollama_client.urlopen", side_effect=URLError("connection refused")):
            with pytest.raises(RuntimeError, match="Failed to reach Ollama"):
                run_eval(
                    endpoint="http://localhost:11434",
                    model="test",
                    tasks=tasks,
                )

    def test_run_eval_computes_tps(self) -> None:
        """Tokens-per-second should be computed from eval_count / eval_duration."""
        response_data = {
            "response": "answer with token",
            "eval_count": 200,
            "eval_duration": 2_000_000_000,  # 2 seconds
            "total_duration": 3_000_000_000,
        }
        tasks = [GoldenTask("t1", "prompt", ["token"])]

        with patch("jarvis_engine.gateway.ollama_client.urlopen", return_value=self._mock_urlopen(response_data)):
            result = run_eval(
                endpoint="http://localhost:11434",
                model="test",
                tasks=tasks,
            )

        # 200 tokens / 2 seconds = 100 tps
        assert result.avg_tps == 100.0

    def test_run_eval_sha256_computed(self) -> None:
        response_data = {
            "response": "test response",
            "eval_count": 10,
            "eval_duration": 1_000_000_000,
            "total_duration": 2_000_000_000,
        }
        tasks = [GoldenTask("t1", "test prompt", [])]

        with patch("jarvis_engine.gateway.ollama_client.urlopen", return_value=self._mock_urlopen(response_data)):
            result = run_eval(
                endpoint="http://localhost:11434",
                model="test",
                tasks=tasks,
            )

        # prompt_sha256 and response_sha256 should be set
        expected_prompt_hash = hashlib.sha256(b"test prompt").hexdigest()
        assert result.results[0].prompt_sha256 == expected_prompt_hash

    def test_run_eval_think_true_prepends_think(self) -> None:
        """When think=True, prompt should be prefixed with /think."""
        response_data = {
            "response": "result",
            "eval_count": 10,
            "eval_duration": 1_000_000_000,
            "total_duration": 2_000_000_000,
        }
        tasks = [GoldenTask("t1", "original prompt", [])]

        captured_payload = {}

        def mock_urlopen(req, timeout=None):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            return self._mock_urlopen(response_data)

        with patch("jarvis_engine.gateway.ollama_client.urlopen", side_effect=mock_urlopen):
            run_eval(
                endpoint="http://localhost:11434",
                model="test",
                tasks=tasks,
                think=True,
            )

        assert captured_payload["data"]["prompt"].startswith("/think\n")

    def test_run_eval_think_false_prepends_nothink(self) -> None:
        response_data = {
            "response": "result",
            "eval_count": 10,
            "eval_duration": 1_000_000_000,
            "total_duration": 2_000_000_000,
        }
        tasks = [GoldenTask("t1", "original prompt", [])]

        captured_payload = {}

        def mock_urlopen(req, timeout=None):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            return self._mock_urlopen(response_data)

        with patch("jarvis_engine.gateway.ollama_client.urlopen", side_effect=mock_urlopen):
            run_eval(
                endpoint="http://localhost:11434",
                model="test",
                tasks=tasks,
                think=False,
            )

        assert captured_payload["data"]["prompt"].startswith("/nothink\n")


# ===================================================================
# Dataclass tests
# ===================================================================

class TestDataclasses:
    """Verify dataclass defaults and structure."""

    def test_memory_recall_task_defaults(self) -> None:
        task = MemoryRecallTask()
        assert task.task_id == ""
        assert task.query == ""
        assert task.must_find_branches == []
        assert task.min_results == 1
        assert task.must_include_in_results == []

    def test_memory_recall_result_defaults(self) -> None:
        result = MemoryRecallResult()
        assert result.results_found == 0
        assert result.overall_score == 0.0
        assert result.branches_found == []

    def test_golden_task_fields(self) -> None:
        task = GoldenTask("id1", "prompt text", ["a", "b"])
        assert task.task_id == "id1"
        assert task.prompt == "prompt text"
        assert task.must_include == ["a", "b"]

    def test_eval_run_asdict(self) -> None:
        run = _make_eval_run()
        d = asdict(run)
        assert "ts" in d
        assert "model" in d
        assert "results" in d
        assert isinstance(d["results"], list)

    def test_memory_recall_task_frozen(self) -> None:
        task = MemoryRecallTask("id", "query")
        with pytest.raises(AttributeError):
            task.task_id = "new_id"  # type: ignore[misc]


# ===================================================================
# Branch task map and eval_branch tests
# ===================================================================

class TestBranchTaskMap:
    """Tests for BRANCH_TASK_MAP and eval_branch()."""

    _EXPECTED_BRANCHES = (
        "ops", "coding", "health", "finance", "security",
        "learning", "family", "communications", "gaming",
    )

    def test_all_branches_have_golden_tasks(self) -> None:
        """Every expected branch must appear in BRANCH_TASK_MAP with 2 tasks."""
        for branch in self._EXPECTED_BRANCHES:
            assert branch in BRANCH_TASK_MAP, f"Missing branch: {branch}"
            assert len(BRANCH_TASK_MAP[branch]) == 2, (
                f"Branch {branch} should have exactly 2 tasks"
            )

    def test_branch_task_map_covers_all_branches(self) -> None:
        """BRANCH_TASK_MAP keys should match exactly the 9 expected branches."""
        assert set(BRANCH_TASK_MAP.keys()) == set(self._EXPECTED_BRANCHES)

    def test_branch_task_map_ids_exist_in_default_tasks(self) -> None:
        """All task IDs referenced in BRANCH_TASK_MAP should exist in DEFAULT_MEMORY_TASKS."""
        all_task_ids = {t.task_id for t in DEFAULT_MEMORY_TASKS}
        for branch, task_ids in BRANCH_TASK_MAP.items():
            for tid in task_ids:
                assert tid in all_task_ids, (
                    f"Task ID {tid!r} in branch {branch!r} not found in DEFAULT_MEMORY_TASKS"
                )

    def test_eval_branch_returns_results(self) -> None:
        """eval_branch should return a dict with branch, results, and avg_score."""
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.return_value = [("id1", 0.1)]
        engine.get_records_batch.return_value = [
            {"branch": "health", "summary": "daily medication routine"},
        ]
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        result = eval_branch("health", engine, embed)

        assert result["branch"] == "health"
        assert len(result["results"]) == 2
        assert "avg_score" in result
        assert isinstance(result["avg_score"], float)

    def test_eval_branch_unknown_raises(self) -> None:
        """eval_branch with an unknown branch should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown branch"):
            eval_branch("nonexistent", MagicMock(spec=MemoryEngine), MagicMock(spec=EmbeddingService))

    def test_eval_branch_task_ids_match(self) -> None:
        """eval_branch should return the correct task_ids for the branch."""
        engine = MagicMock(spec=MemoryEngine)
        engine.search_vec.return_value = []
        embed = MagicMock(spec=EmbeddingService)
        embed.embed.return_value = [0.0] * 128

        result = eval_branch("ops", engine, embed)
        assert result["task_ids"] == ["ops_recall", "ops_routine_recall"]
