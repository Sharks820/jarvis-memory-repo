from __future__ import annotations

import logging
import math
from datetime import datetime
from jarvis_engine._compat import UTC
from jarvis_engine._shared import now_iso as _now_iso
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from jarvis_engine._shared import atomic_write_json as _atomic_write_json
from jarvis_engine._shared import safe_float as _safe_float
from jarvis_engine.brain_memory import brain_regression_report
from jarvis_engine.growth_tracker import read_history, summarize_history


DEFAULT_TARGETS: list[dict[str, Any]] = [
    {
        "id": "gemini_proxy",
        "name": "Gemini Reasoning (Proxy)",
        "target_score_pct": 88.0,
    },
    {
        "id": "claude_opus_4_6_proxy",
        "name": "Claude Opus 4.6 (Proxy)",
        "target_score_pct": 92.0,
    },
    {
        "id": "codex_5_3_proxy",
        "name": "Codex 5.3 (Proxy)",
        "target_score_pct": 94.0,
    },
]

MILESTONES: list[dict[str, Any]] = [
    {"id": "score_50", "label": "Reached 50% Intelligence Index", "score": 50.0},
    {"id": "score_60", "label": "Reached 60% Intelligence Index", "score": 60.0},
    {"id": "score_70", "label": "Reached 70% Intelligence Index", "score": 70.0},
    {"id": "score_80", "label": "Reached 80% Intelligence Index", "score": 80.0},
    {"id": "score_90", "label": "Reached 90% Intelligence Index", "score": 90.0},
]


_to_float = _safe_float


def _safe_parse_ts(value: str) -> datetime | None:
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _targets_path(root: Path) -> Path:
    return root / ".planning" / "intelligence_targets.json"


def _achievements_path(root: Path) -> Path:
    return root / ".planning" / "intelligence_achievements.json"


def _history_path(root: Path) -> Path:
    return root / ".planning" / "capability_history.jsonl"


def _load_targets(root: Path) -> list[dict[str, Any]]:
    from jarvis_engine._shared import load_json_file

    path = _targets_path(root)
    raw = load_json_file(path, None, expected_type=list)
    if raw is None:
        return list(DEFAULT_TARGETS)

    values: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            score = item.get("target_score_pct")
            score_value = _to_float(score, float("nan"))
            if math.isnan(score_value):
                continue
            values.append(
                {
                    "id": str(item.get("id", "")).strip()
                    or f"target_{len(values) + 1}",
                    "name": str(item.get("name", "")).strip()
                    or f"Target {len(values) + 1}",
                    "target_score_pct": max(0.0, min(100.0, score_value)),
                }
            )
    return values or list(DEFAULT_TARGETS)


def _load_achievements(root: Path) -> dict[str, Any]:
    from jarvis_engine._shared import load_json_file

    path = _achievements_path(root)
    default: dict[str, Any] = {"unlocked": []}
    raw = load_json_file(path, None, expected_type=dict)
    if raw is None:
        return default
    unlocked = raw.get("unlocked", [])
    if not isinstance(unlocked, list):
        unlocked = []
    cleaned: list[dict[str, Any]] = []
    for item in unlocked:
        if not isinstance(item, dict):
            continue
        cleaned.append(
            {
                "id": str(item.get("id", "")).strip(),
                "label": str(item.get("label", "")).strip(),
                "score": _to_float(item.get("score", 0.0), 0.0),
                "ts": str(item.get("ts", "")).strip(),
            }
        )
    return {"unlocked": cleaned}


def _save_achievements(root: Path, payload: dict[str, Any]) -> None:
    _atomic_write_json(_achievements_path(root), payload)


def _average_run_interval_days(rows: list[dict[str, Any]], window: int = 10) -> float:
    if len(rows) < 2:
        return 1.0
    parsed: list[datetime] = []
    for row in rows[-window:]:
        ts = _safe_parse_ts(str(row.get("ts", "")))
        if ts is not None:
            parsed.append(ts)
    if len(parsed) < 2:
        return 1.0
    deltas = []
    for idx in range(1, len(parsed)):
        delta = (parsed[idx] - parsed[idx - 1]).total_seconds()
        if delta > 0:
            deltas.append(delta / 86400.0)
    if not deltas:
        return 1.0
    return max(0.05, sum(deltas) / len(deltas))


def _score_slope_per_run(rows: list[dict[str, Any]], window: int = 12) -> float:
    sample = rows[-window:]
    if len(sample) < 2:
        return 0.0
    start = _to_float(sample[0].get("score_pct", 0.0), 0.0)
    end = _to_float(sample[-1].get("score_pct", 0.0), 0.0)
    return (end - start) / float(len(sample) - 1)


def _estimate_eta(
    latest_score: float,
    slope_per_run: float,
    avg_days_per_run: float,
    target_score: float,
) -> dict[str, Any]:
    if latest_score >= target_score:
        return {"runs": 0, "days": 0.0, "status": "met"}
    if slope_per_run <= 0.0:
        return {"runs": None, "days": None, "status": "insufficient_positive_trend"}

    runs_needed = max(1, math.ceil((target_score - latest_score) / slope_per_run))
    days_needed = round(runs_needed * avg_days_per_run, 1)
    return {"runs": runs_needed, "days": days_needed, "status": "projected"}


def _safe_learning_metrics(
    pref_tracker: Any = None,
    feedback_tracker: Any = None,
    usage_tracker: Any = None,
) -> dict[str, Any]:
    """Collect learning metrics from all trackers (returns empty on failure)."""
    result: dict[str, Any] = {}

    # Per-route quality scores (LEARN-08)
    if feedback_tracker is not None:
        try:
            result["route_quality"] = feedback_tracker.get_all_route_quality()
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            logger.debug("Failed to collect route quality metrics: %s", exc)
            result["route_quality"] = {}

    # Preference summary (LEARN-08)
    if pref_tracker is not None:
        try:
            result["preferences"] = pref_tracker.get_preferences()
            result["all_preferences"] = pref_tracker.get_all_preferences()
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            logger.debug("Failed to collect preference metrics: %s", exc)
            result["preferences"] = {}
            result["all_preferences"] = []

    # Peak usage hours (LEARN-08)
    if usage_tracker is not None:
        try:
            result["peak_hours"] = usage_tracker.get_peak_hours(top_n=5)
            result["hourly_distribution"] = usage_tracker.get_hourly_distribution()
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            logger.debug("Failed to collect usage metrics: %s", exc)
            result["peak_hours"] = []
            result["hourly_distribution"] = {}

    return result


def _safe_knowledge_snapshot(kg: Any = None, engine: Any = None) -> dict[str, Any]:
    """Capture live knowledge metrics (returns empty on failure)."""
    if kg is None and engine is None:
        return {}
    try:
        from jarvis_engine.learning.metrics import capture_knowledge_metrics

        return capture_knowledge_metrics(kg, engine)
    except (ImportError, AttributeError, OSError, ValueError) as exc:
        logger.debug("Knowledge snapshot capture failed: %s", exc)
        return {}


def build_intelligence_dashboard(
    root: Path,
    *,
    last_runs: int = 20,
    pref_tracker: Any = None,
    feedback_tracker: Any = None,
    usage_tracker: Any = None,
    kg: Any = None,
    engine: Any = None,
) -> dict[str, Any]:
    history_rows = read_history(_history_path(root))
    summary = summarize_history(history_rows, last=last_runs)
    latest_score = _to_float(summary.get("latest_score_pct", 0.0), 0.0)
    slope = _score_slope_per_run(history_rows, window=max(4, min(20, last_runs)))
    avg_days = _average_run_interval_days(
        history_rows, window=max(4, min(20, last_runs))
    )

    targets = _load_targets(root)
    ranking = [
        {
            "id": "jarvis_unlimited",
            "name": "Jarvis Unlimited",
            "score_pct": latest_score,
            "kind": "live",
        }
    ]
    etas: list[dict[str, Any]] = []
    for target in targets:
        target_score = _to_float(target.get("target_score_pct", 0.0), 0.0)
        ranking.append(
            {
                "id": str(target.get("id", "")),
                "name": str(target.get("name", "")),
                "score_pct": target_score,
                "kind": "proxy_target",
            }
        )
        eta = _estimate_eta(latest_score, slope, avg_days, target_score)
        etas.append(
            {
                "target_id": str(target.get("id", "")),
                "target_name": str(target.get("name", "")),
                "target_score_pct": target_score,
                "eta": eta,
            }
        )

    ranking.sort(
        key=lambda item: _to_float(item.get("score_pct", 0.0), 0.0), reverse=True
    )

    achievements = _load_achievements(root)
    unlocked = achievements.get("unlocked", [])
    if not isinstance(unlocked, list):
        unlocked = []
    unlocked_ids = {
        str(item.get("id", "")) for item in unlocked if isinstance(item, dict)
    }
    new_unlocks: list[dict[str, Any]] = []
    now = _now_iso()
    for milestone in MILESTONES:
        milestone_score = _to_float(milestone.get("score", 0.0), 0.0)
        milestone_id = str(milestone.get("id", ""))
        if latest_score >= milestone_score and milestone_id not in unlocked_ids:
            new_unlocks.append(
                {
                    "id": milestone_id,
                    "label": str(milestone.get("label", "")),
                    "score": milestone_score,
                    "ts": now,
                }
            )
    if new_unlocks:
        unlocked.extend(new_unlocks)
        _save_achievements(root, {"unlocked": unlocked})

    return {
        "generated_utc": now,
        "methodology": {
            "metric": "golden_task_score_pct",
            "note": "Proxy targets are configurable local benchmarks, not vendor-internal model scores.",
            "history_runs": len(history_rows),
            "slope_score_pct_per_run": round(slope, 3),
            "avg_days_per_run": round(avg_days, 3),
        },
        "jarvis": {
            "score_pct": latest_score,
            "delta_vs_prev_pct": _to_float(summary.get("delta_vs_prev_pct", 0.0), 0.0),
            "window_avg_pct": _to_float(summary.get("window_avg_pct", 0.0), 0.0),
            "latest_model": str(summary.get("latest_model", "")),
            "latest_ts": str(summary.get("latest_ts", "")),
        },
        "ranking": ranking,
        "etas": etas,
        "memory_regression": brain_regression_report(root),
        "knowledge_graph": _safe_kg_metrics(root),
        "gateway_audit": _safe_gateway_summary(root),
        "learning": _safe_learning_metrics(
            pref_tracker, feedback_tracker, usage_tracker
        ),
        "knowledge_snapshot": _safe_knowledge_snapshot(kg, engine),
        "achievements": {
            "new": new_unlocks,
            "all": unlocked,
        },
    }


def _safe_kg_metrics(root: Path) -> dict[str, Any]:
    """Collect KG metrics safely (returns empty dict on failure)."""
    try:
        from jarvis_engine.proactive.kg_metrics import load_kg_history, kg_growth_trend
        from jarvis_engine._constants import runtime_dir, KG_METRICS_LOG

        history_path = runtime_dir(root) / KG_METRICS_LOG
        history = load_kg_history(history_path, limit=50)
        if history:
            latest = history[-1]
            trend = kg_growth_trend(history)
            return {
                "node_count": latest.get("node_count", 0),
                "edge_count": latest.get("edge_count", 0),
                "cross_branch_edges": latest.get("cross_branch_edges", 0),
                "avg_confidence": latest.get("avg_confidence", 0.0),
                "locked_facts": latest.get("locked_facts", 0),
                "branch_counts": latest.get("branch_counts", {}),
                "trend": trend,
            }
    except (ImportError, OSError, ValueError):
        logger.debug(
            "Failed to collect KG metrics (knowledge graph module may not be available)"
        )
    return {}


def _safe_gateway_summary(root: Path) -> dict[str, Any]:
    """Summarize recent gateway decisions safely."""
    try:
        from jarvis_engine._constants import runtime_dir, GATEWAY_AUDIT_LOG
        from jarvis_engine.gateway.audit import GatewayAudit

        audit_path = runtime_dir(root) / GATEWAY_AUDIT_LOG
        audit = GatewayAudit(audit_path)
        return audit.summary(hours=24)
    except (ImportError, OSError, ValueError):
        logger.debug(
            "Failed to collect gateway audit summary (audit log may not exist)"
        )
    return {}
