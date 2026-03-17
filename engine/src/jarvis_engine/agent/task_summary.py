"""TaskSummary -- structured completion report for finished agent tasks.

Generates a human-readable + machine-readable summary from an AgentTask after
it reaches status "done" or "failed".  Called by ReflectionLoop and available
standalone for testing.

Usage::

    from jarvis_engine.agent.task_summary import generate_task_summary
    summary = generate_task_summary(task)
    print(summary.summary_text)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.agent.state_store import AgentTask

logger = logging.getLogger(__name__)


@dataclass
class TaskSummary:
    """Structured completion report for an agent task.

    Attributes:
        task_id:         The agent task identifier.
        goal:            Original natural-language goal.
        status:          Final FSM status (done/failed/etc.).
        steps_completed: Number of plan steps completed (from task.step_index).
        tokens_used:     Total LLM tokens consumed by this task.
        error_count:     Number of step failures recorded.
        files_touched:   Deduplicated list of file paths written/read by "file" tool steps.
        summary_text:    Human-readable one-paragraph summary.
    """

    task_id: str
    goal: str
    status: str
    steps_completed: int
    tokens_used: int
    error_count: int
    files_touched: list[str] = field(default_factory=list)
    summary_text: str = ""


def generate_task_summary(task: "AgentTask") -> TaskSummary:
    """Build a TaskSummary from a completed (or failed) AgentTask.

    Parses plan_json to extract files touched by "file" tool steps.
    Returns sensible defaults if plan_json is empty or malformed.
    """
    files_touched = _extract_files_touched(task.plan_json)

    summary_text = _build_summary_text(
        task_id=task.task_id,
        goal=task.goal,
        status=task.status,
        steps_completed=task.step_index,
        tokens_used=task.tokens_used,
        error_count=task.error_count,
        files_touched=files_touched,
    )

    return TaskSummary(
        task_id=task.task_id,
        goal=task.goal,
        status=task.status,
        steps_completed=task.step_index,
        tokens_used=task.tokens_used,
        error_count=task.error_count,
        files_touched=files_touched,
        summary_text=summary_text,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_files_touched(plan_json: str) -> list[str]:
    """Parse plan_json and return deduplicated file paths from 'file' tool steps."""
    if not plan_json or plan_json.strip() in ("", "[]", "{}"):
        return []

    try:
        steps = json.loads(plan_json)
    except (json.JSONDecodeError, ValueError):
        logger.debug("task_summary: could not parse plan_json -- no files extracted")
        return []

    if not isinstance(steps, list):
        return []

    seen: set[str] = set()
    files: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("tool_name") != "file":
            continue
        params = step.get("params", {})
        if not isinstance(params, dict):
            continue
        path = params.get("path", "")
        if path and isinstance(path, str) and path not in seen:
            seen.add(path)
            files.append(path)

    return files


def _build_summary_text(
    *,
    task_id: str,
    goal: str,
    status: str,
    steps_completed: int,
    tokens_used: int,
    error_count: int,
    files_touched: list[str],
) -> str:
    """Build a one-paragraph human-readable summary string."""
    status_phrase = "completed successfully" if status == "done" else f"finished with status '{status}'"

    parts = [
        f"Task {task_id[:8]} {status_phrase}.",
        f"Goal: {goal!r}.",
        f"Steps completed: {steps_completed}.",
        f"Tokens used: {tokens_used}.",
    ]
    if error_count:
        parts.append(f"Errors encountered: {error_count}.")
    if files_touched:
        file_list = ", ".join(files_touched[:5])
        extra = f" (+{len(files_touched) - 5} more)" if len(files_touched) > 5 else ""
        parts.append(f"Files touched: {file_list}{extra}.")

    return " ".join(parts)
