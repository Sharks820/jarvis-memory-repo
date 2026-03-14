from __future__ import annotations

from jarvis_engine.learning.growth_tracker import (
    EvalRun,
    TaskEval,
    append_history,
    read_history,
    score_text,
    summarize_history,
)


def test_score_text_matches_required_tokens() -> None:
    matched, total, coverage, matched_tokens = score_text(
        "Phase 1 next action is to secure primary desktop runtime.",
        ["phase", "next", "action", "desktop"],
    )
    assert matched == 4
    assert total == 4
    assert coverage == 1.0
    assert matched_tokens == ["phase", "next", "action", "desktop"]


def test_history_append_read_and_summary(tmp_path) -> None:
    history_path = tmp_path / "capability_history.jsonl"
    run1 = EvalRun(
        ts="2026-02-22T00:00:00+00:00",
        model="qwen3:latest",
        tasks=3,
        score_pct=66.67,
        avg_coverage_pct=66.67,
        avg_tps=50.0,
        avg_latency_s=0.8,
        results=[
            TaskEval(
                task_id="a",
                matched=2,
                total=3,
                coverage=2 / 3,
                matched_tokens=["phase", "next"],
                required_tokens=["phase", "next", "action"],
                prompt="p1",
                response="r1",
                prompt_sha256="x1",
                response_sha256="y1",
                response_source="response",
                eval_count=20,
                eval_duration_s=0.2,
                total_duration_s=0.4,
            )
        ],
    )
    run2 = EvalRun(
        ts="2026-02-23T00:00:00+00:00",
        model="qwen3:latest",
        tasks=3,
        score_pct=83.33,
        avg_coverage_pct=83.33,
        avg_tps=48.0,
        avg_latency_s=0.9,
        results=[
            TaskEval(
                task_id="b",
                matched=3,
                total=3,
                coverage=1.0,
                matched_tokens=["phase", "next", "action"],
                required_tokens=["phase", "next", "action"],
                prompt="p2",
                response="r2",
                prompt_sha256="x2",
                response_sha256="y2",
                response_source="response",
                eval_count=22,
                eval_duration_s=0.21,
                total_duration_s=0.41,
            )
        ],
    )
    append_history(history_path, run1)
    append_history(history_path, run2)

    rows = read_history(history_path)
    summary = summarize_history(rows, last=10)
    assert summary["runs"] == 2
    assert summary["latest_model"] == "qwen3:latest"
    assert summary["latest_score_pct"] == 83.33
    assert summary["delta_vs_prev_pct"] == 16.66
    assert summary["window_avg_pct"] == 75.0
