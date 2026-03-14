"""Tests for intelligence_dashboard module -- build_intelligence_dashboard,
_estimate_eta, _load_targets, _load_achievements, score slope, and interval helpers."""

from __future__ import annotations

import json
from pathlib import Path

from jarvis_engine.ops.intelligence_dashboard import (
    _estimate_eta,
    _load_achievements,
    _load_targets,
    _average_run_interval_days,
    _score_slope_per_run,
    build_intelligence_dashboard,
    DEFAULT_TARGETS,
)


# ---------------------------------------------------------------------------
# build_intelligence_dashboard
# ---------------------------------------------------------------------------


def test_build_intelligence_dashboard_returns_expected_structure(tmp_path: Path) -> None:
    """Dashboard output has all required top-level keys with correct types."""
    payload = build_intelligence_dashboard(tmp_path)

    assert "generated_utc" in payload
    assert isinstance(payload["generated_utc"], str)
    assert "methodology" in payload
    assert isinstance(payload["methodology"], dict)
    assert "jarvis" in payload
    assert isinstance(payload["jarvis"], dict)
    assert "ranking" in payload
    assert isinstance(payload["ranking"], list)
    assert "etas" in payload
    assert isinstance(payload["etas"], list)
    assert "memory_regression" in payload
    assert "knowledge_graph" in payload
    assert "gateway_audit" in payload
    assert "achievements" in payload
    assert isinstance(payload["achievements"], dict)
    assert "new" in payload["achievements"]
    assert "all" in payload["achievements"]


def test_build_intelligence_dashboard_without_history(tmp_path: Path) -> None:
    """Empty history yields score_pct=0.0 and zero history_runs."""
    payload = build_intelligence_dashboard(tmp_path)
    assert payload["jarvis"]["score_pct"] == 0.0
    assert payload["methodology"]["history_runs"] == 0
    assert isinstance(payload["ranking"], list)
    assert "memory_regression" in payload


def test_build_intelligence_dashboard_with_history(tmp_path: Path) -> None:
    """History rows populate score, model, ts, and delta fields."""
    history_path = tmp_path / ".planning" / "capability_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": "2026-02-20T00:00:00+00:00", "model": "m1", "score_pct": 62.0, "run_sha256": "", "prev_run_sha256": ""},
        {"ts": "2026-02-21T00:00:00+00:00", "model": "m1", "score_pct": 66.0, "run_sha256": "", "prev_run_sha256": ""},
        {"ts": "2026-02-22T00:00:00+00:00", "model": "m1", "score_pct": 70.0, "run_sha256": "", "prev_run_sha256": ""},
    ]
    history_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    payload = build_intelligence_dashboard(tmp_path)
    assert payload["jarvis"]["score_pct"] == 70.0
    assert payload["methodology"]["history_runs"] == 3
    assert len(payload["etas"]) >= 1


def test_build_intelligence_dashboard_ranking_sorted_descending(tmp_path: Path) -> None:
    """Ranking list is sorted by score_pct descending."""
    history_path = tmp_path / ".planning" / "capability_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    # Score 95 should place Jarvis above most default targets
    rows = [
        {"ts": "2026-02-20T00:00:00+00:00", "model": "m1", "score_pct": 95.0},
    ]
    history_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")

    payload = build_intelligence_dashboard(tmp_path)
    ranking = payload["ranking"]
    scores = [entry["score_pct"] for entry in ranking]
    assert scores == sorted(scores, reverse=True)


def test_build_intelligence_dashboard_methodology_fields(tmp_path: Path) -> None:
    """Methodology section has expected sub-keys."""
    payload = build_intelligence_dashboard(tmp_path)
    meth = payload["methodology"]
    assert "metric" in meth
    assert meth["metric"] == "golden_task_score_pct"
    assert "note" in meth
    assert "history_runs" in meth
    assert "slope_score_pct_per_run" in meth
    assert "avg_days_per_run" in meth


def test_build_intelligence_dashboard_etas_contain_target_info(tmp_path: Path) -> None:
    """Each ETA entry references a target with id, name, score, and eta dict."""
    payload = build_intelligence_dashboard(tmp_path)
    for eta_entry in payload["etas"]:
        assert "target_id" in eta_entry
        assert "target_name" in eta_entry
        assert "target_score_pct" in eta_entry
        assert "eta" in eta_entry
        eta = eta_entry["eta"]
        assert "status" in eta


def test_build_intelligence_dashboard_achievement_unlocks(tmp_path: Path) -> None:
    """Score of 75 should unlock milestones up to score_70."""
    history_path = tmp_path / ".planning" / "capability_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": "2026-02-20T00:00:00+00:00", "model": "m1", "score_pct": 75.0},
    ]
    history_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")

    payload = build_intelligence_dashboard(tmp_path)
    new_unlocks = payload["achievements"]["new"]
    new_ids = {u["id"] for u in new_unlocks}
    # Should have unlocked 50, 60, and 70 milestones
    assert "score_50" in new_ids
    assert "score_60" in new_ids
    assert "score_70" in new_ids
    # Should NOT have unlocked 80 or 90
    assert "score_80" not in new_ids
    assert "score_90" not in new_ids


def test_build_intelligence_dashboard_no_duplicate_unlocks(tmp_path: Path) -> None:
    """Previously unlocked achievements are not re-unlocked."""
    planning = tmp_path / ".planning"
    planning.mkdir(parents=True, exist_ok=True)

    # Pre-existing achievements
    achievements = {
        "unlocked": [
            {"id": "score_50", "label": "Reached 50%", "score": 50.0, "ts": "2026-01-01T00:00:00+00:00"},
        ]
    }
    (planning / "intelligence_achievements.json").write_text(
        json.dumps(achievements), encoding="utf-8"
    )

    # History with score 55 (above 50 but below 60)
    history_path = planning / "capability_history.jsonl"
    history_path.write_text(
        json.dumps({"ts": "2026-02-20T00:00:00+00:00", "model": "m1", "score_pct": 55.0}) + "\n",
        encoding="utf-8",
    )

    payload = build_intelligence_dashboard(tmp_path)
    new_unlocks = payload["achievements"]["new"]
    new_ids = [u["id"] for u in new_unlocks]
    # score_50 was already unlocked, so it should not appear again
    assert "score_50" not in new_ids


# ---------------------------------------------------------------------------
# _estimate_eta
# ---------------------------------------------------------------------------


def test_estimate_eta_score_already_met() -> None:
    """When latest_score >= target, status is 'met' with 0 runs/days."""
    result = _estimate_eta(
        latest_score=90.0,
        slope_per_run=1.0,
        avg_days_per_run=1.0,
        target_score=88.0,
    )
    assert result["status"] == "met"
    assert result["runs"] == 0
    assert result["days"] == 0.0


def test_estimate_eta_score_exactly_met() -> None:
    """When latest_score == target, status is 'met'."""
    result = _estimate_eta(
        latest_score=88.0,
        slope_per_run=2.0,
        avg_days_per_run=1.0,
        target_score=88.0,
    )
    assert result["status"] == "met"
    assert result["runs"] == 0


def test_estimate_eta_no_positive_trend() -> None:
    """Zero or negative slope yields 'insufficient_positive_trend'."""
    result = _estimate_eta(
        latest_score=50.0,
        slope_per_run=0.0,
        avg_days_per_run=1.0,
        target_score=88.0,
    )
    assert result["status"] == "insufficient_positive_trend"
    assert result["runs"] is None
    assert result["days"] is None


def test_estimate_eta_negative_slope() -> None:
    """Negative slope also yields 'insufficient_positive_trend'."""
    result = _estimate_eta(
        latest_score=60.0,
        slope_per_run=-0.5,
        avg_days_per_run=1.0,
        target_score=88.0,
    )
    assert result["status"] == "insufficient_positive_trend"
    assert result["runs"] is None


def test_estimate_eta_positive_projected() -> None:
    """Positive slope computes projected runs and days."""
    result = _estimate_eta(
        latest_score=70.0,
        slope_per_run=2.0,
        avg_days_per_run=1.5,
        target_score=88.0,
    )
    assert result["status"] == "projected"
    # Need to close gap of 18 points at 2 per run -> ceil(18/2) = 9 runs
    assert result["runs"] == 9
    # 9 runs * 1.5 days/run = 13.5 days
    assert result["days"] == 13.5


def test_estimate_eta_projected_fractional_runs() -> None:
    """Fractional run count is ceiled up."""
    result = _estimate_eta(
        latest_score=85.0,
        slope_per_run=2.0,
        avg_days_per_run=3.0,
        target_score=88.0,
    )
    assert result["status"] == "projected"
    # Gap of 3 at slope 2 -> ceil(3/2) = 2 runs
    assert result["runs"] == 2
    assert result["days"] == 6.0


def test_estimate_eta_small_gap_minimum_one_run() -> None:
    """Even a tiny gap needs at least 1 run."""
    result = _estimate_eta(
        latest_score=87.9,
        slope_per_run=5.0,
        avg_days_per_run=1.0,
        target_score=88.0,
    )
    assert result["status"] == "projected"
    assert result["runs"] >= 1


# ---------------------------------------------------------------------------
# _load_targets
# ---------------------------------------------------------------------------


def test_load_targets_defaults_when_no_file(tmp_path: Path) -> None:
    """Returns DEFAULT_TARGETS when no targets file exists."""
    targets = _load_targets(tmp_path)
    assert len(targets) == len(DEFAULT_TARGETS)
    for t in targets:
        assert "id" in t
        assert "name" in t
        assert "target_score_pct" in t


def test_load_targets_custom_file(tmp_path: Path) -> None:
    """Loads custom targets from JSON file."""
    planning = tmp_path / ".planning"
    planning.mkdir(parents=True, exist_ok=True)
    custom_targets = [
        {"id": "custom_1", "name": "Custom Target", "target_score_pct": 75.0},
    ]
    (planning / "intelligence_targets.json").write_text(
        json.dumps(custom_targets), encoding="utf-8"
    )
    targets = _load_targets(tmp_path)
    assert len(targets) == 1
    assert targets[0]["id"] == "custom_1"
    assert targets[0]["target_score_pct"] == 75.0


def test_load_targets_invalid_json_returns_defaults(tmp_path: Path) -> None:
    """Corrupt JSON falls back to DEFAULT_TARGETS."""
    planning = tmp_path / ".planning"
    planning.mkdir(parents=True, exist_ok=True)
    (planning / "intelligence_targets.json").write_text("not json!", encoding="utf-8")
    targets = _load_targets(tmp_path)
    assert len(targets) == len(DEFAULT_TARGETS)


def test_load_targets_filters_invalid_entries(tmp_path: Path) -> None:
    """Entries without valid target_score_pct are dropped."""
    planning = tmp_path / ".planning"
    planning.mkdir(parents=True, exist_ok=True)
    mixed = [
        {"id": "good", "name": "Good", "target_score_pct": 80.0},
        {"id": "bad", "name": "Bad", "target_score_pct": "not_a_number"},
        {"id": "also_bad", "name": "Also Bad"},  # missing score
    ]
    (planning / "intelligence_targets.json").write_text(
        json.dumps(mixed), encoding="utf-8"
    )
    targets = _load_targets(tmp_path)
    assert len(targets) == 1
    assert targets[0]["id"] == "good"


def test_load_targets_clamps_score(tmp_path: Path) -> None:
    """Scores are clamped to [0, 100]."""
    planning = tmp_path / ".planning"
    planning.mkdir(parents=True, exist_ok=True)
    data = [
        {"id": "over", "name": "Over 100", "target_score_pct": 150.0},
        {"id": "under", "name": "Under 0", "target_score_pct": -10.0},
    ]
    (planning / "intelligence_targets.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    targets = _load_targets(tmp_path)
    assert targets[0]["target_score_pct"] == 100.0
    assert targets[1]["target_score_pct"] == 0.0


# ---------------------------------------------------------------------------
# _load_achievements
# ---------------------------------------------------------------------------


def test_load_achievements_no_file(tmp_path: Path) -> None:
    """Missing file returns empty unlocked list."""
    result = _load_achievements(tmp_path)
    assert result == {"unlocked": []}


def test_load_achievements_valid_file(tmp_path: Path) -> None:
    """Valid achievements file is loaded correctly."""
    planning = tmp_path / ".planning"
    planning.mkdir(parents=True, exist_ok=True)
    data = {
        "unlocked": [
            {"id": "score_50", "label": "Reached 50%", "score": 50.0, "ts": "2026-01-01T00:00:00"},
        ]
    }
    (planning / "intelligence_achievements.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    result = _load_achievements(tmp_path)
    assert len(result["unlocked"]) == 1
    assert result["unlocked"][0]["id"] == "score_50"


def test_load_achievements_corrupt_json(tmp_path: Path) -> None:
    """Corrupt JSON returns empty unlocked list."""
    planning = tmp_path / ".planning"
    planning.mkdir(parents=True, exist_ok=True)
    (planning / "intelligence_achievements.json").write_text("{bad", encoding="utf-8")
    result = _load_achievements(tmp_path)
    assert result == {"unlocked": []}


# ---------------------------------------------------------------------------
# _score_slope_per_run
# ---------------------------------------------------------------------------


def test_score_slope_per_run_positive_trend() -> None:
    """Positive trend computes correct slope."""
    rows = [
        {"score_pct": 50.0},
        {"score_pct": 55.0},
        {"score_pct": 60.0},
    ]
    slope = _score_slope_per_run(rows)
    # (60 - 50) / (3 - 1) = 5.0
    assert slope == 5.0


def test_score_slope_per_run_single_row() -> None:
    """Single row has zero slope."""
    rows = [{"score_pct": 70.0}]
    slope = _score_slope_per_run(rows)
    assert slope == 0.0


def test_score_slope_per_run_empty() -> None:
    """Empty rows have zero slope."""
    slope = _score_slope_per_run([])
    assert slope == 0.0


def test_score_slope_per_run_negative_trend() -> None:
    """Negative trend gives negative slope."""
    rows = [
        {"score_pct": 80.0},
        {"score_pct": 75.0},
        {"score_pct": 70.0},
    ]
    slope = _score_slope_per_run(rows)
    assert slope == -5.0


# ---------------------------------------------------------------------------
# _average_run_interval_days
# ---------------------------------------------------------------------------


def test_average_run_interval_days_two_rows() -> None:
    """Two rows one day apart gives interval of 1.0."""
    rows = [
        {"ts": "2026-02-20T00:00:00+00:00"},
        {"ts": "2026-02-21T00:00:00+00:00"},
    ]
    avg = _average_run_interval_days(rows)
    assert abs(avg - 1.0) < 0.01


def test_average_run_interval_days_fewer_than_two_rows() -> None:
    """Fewer than 2 rows returns 1.0 default."""
    assert _average_run_interval_days([]) == 1.0
    assert _average_run_interval_days([{"ts": "2026-02-20T00:00:00+00:00"}]) == 1.0


def test_average_run_interval_days_irregular_spacing() -> None:
    """Irregular spacing computes correct average."""
    rows = [
        {"ts": "2026-02-20T00:00:00+00:00"},
        {"ts": "2026-02-21T00:00:00+00:00"},  # 1 day gap
        {"ts": "2026-02-24T00:00:00+00:00"},  # 3 day gap
    ]
    avg = _average_run_interval_days(rows)
    # Average of [1, 3] = 2.0
    assert abs(avg - 2.0) < 0.01


def test_average_run_interval_days_missing_timestamps() -> None:
    """Rows with unparseable timestamps are skipped gracefully."""
    rows = [
        {"ts": "2026-02-20T00:00:00+00:00"},
        {"ts": ""},
        {"ts": "2026-02-22T00:00:00+00:00"},
    ]
    avg = _average_run_interval_days(rows)
    # Only 2 parseable timestamps -> 2-day gap -> avg = 2.0
    assert abs(avg - 2.0) < 0.01
