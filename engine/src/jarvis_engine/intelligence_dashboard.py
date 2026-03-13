"""Backward-compatibility shim — canonical location is jarvis_engine.ops.intelligence_dashboard."""
from jarvis_engine.ops.intelligence_dashboard import *  # noqa: F401,F403
from jarvis_engine.ops.intelligence_dashboard import (  # noqa: F401 — re-export private names
    _achievements_path,
    _average_run_interval_days,
    _build_ranking_and_etas,
    _check_milestone_unlocks,
    _estimate_eta,
    _history_path,
    _load_achievements,
    _load_targets,
    _safe_diagnostics,
    _safe_gateway_summary,
    _safe_hygiene_metrics,
    _safe_kg_metrics,
    _safe_knowledge_snapshot,
    _safe_learning_metrics,
    _safe_mission_metrics,
    _save_achievements,
    _score_slope_per_run,
    _targets_path,
)
