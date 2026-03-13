"""Proactive and self-test CLI command handlers.

Extracted from main.py to improve file health and separation of concerns.
Contains: proactive-check, cost-reduction, self-test.
"""

from __future__ import annotations

from jarvis_engine._cli_helpers import cli_dispatch as _dispatch

from jarvis_engine.commands.proactive_commands import (
    CostReductionCommand,
    ProactiveCheckCommand,
    SelfTestCommand,
)


def cmd_proactive_check(snapshot_path: str) -> int:
    result, _ = _dispatch(ProactiveCheckCommand(snapshot_path=snapshot_path))
    print(f"alerts_fired={result.alerts_fired}")
    if result.alerts_fired:
        alerts = result.alerts if isinstance(result.alerts, list) else []
        for a in alerts:
            if not isinstance(a, dict):
                continue
            print(f"  [{a.get('rule_id', '?')}] {a.get('message', '')}")
    print(f"message={result.message}")
    if result.diagnostics:
        print(f"diagnostics={result.diagnostics}")
    return 0


def cmd_cost_reduction(days: int) -> int:
    result, _ = _dispatch(CostReductionCommand(days=days))
    print(f"local_pct={result.local_pct}")
    print(f"cloud_cost_usd={result.cloud_cost_usd}")
    print(f"failed_count={result.failed_count}")
    print(f"failed_cost_usd={result.failed_cost_usd}")
    print(f"trend={result.trend}")
    print(f"message={result.message}")
    return 0


def cmd_self_test(threshold: float) -> int:
    result, _ = _dispatch(SelfTestCommand(score_threshold=threshold))
    print(f"average_score={result.average_score:.4f}")
    print(f"tasks_run={result.tasks_run}")
    print(f"regression_detected={result.regression_detected}")
    for task_score in result.per_task_scores:
        print(f"  task={task_score.get('task_id', '?')} score={task_score.get('score', 0.0):.4f}")
    print(f"message={result.message}")
    return 0
