"""ReflectionLoop -- evaluate step results, replan on failure, escalate.

The ReflectionLoop orchestrates the full ReAct + Plan-and-Execute hybrid:

  For each step:
    1. Check token budget -- stop if exceeded.
    2. Execute via StepExecutor.
    3. Evaluate result.
    4. If success: advance step_index, checkpoint, continue.
    5. If failure:
       - Hash the error. If same hash 3× consecutive → escalate (failed).
       - Otherwise: replan remaining steps, retry from current step.
  6. All steps done → task.status = "done".

Usage::

    loop = ReflectionLoop(executor=executor, planner=planner, store=store, bus=bus)
    task = await loop.run_loop(task, steps)
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.agent.executor import StepExecutor, StepResult
    from jarvis_engine.agent.planner import AgentStep, TaskPlanner
    from jarvis_engine.agent.progress_bus import ProgressEventBus
    from jarvis_engine.agent.state_store import AgentStateStore, AgentTask

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_SAME_ERRORS = 3


class ReflectionLoop:
    """Evaluate step results and orchestrate replanning / escalation."""

    def __init__(
        self,
        executor: "StepExecutor",
        planner: "TaskPlanner",
        store: "AgentStateStore",
        bus: "ProgressEventBus",
    ) -> None:
        self._executor = executor
        self._planner = planner
        self._store = store
        self._bus = bus

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, step_result: "StepResult") -> bool:
        """Return True if *step_result* indicates success."""
        return step_result.success

    async def run_loop(
        self,
        task: "AgentTask",
        steps: list["AgentStep"],
    ) -> "AgentTask":
        """Execute *steps* for *task*, handling failures via replanning.

        Starts from task.step_index (supports resume after checkpoint).
        Modifies *task* in place and returns it.
        """
        # Work on a mutable copy of the steps list so we can replace it on replan
        remaining: list["AgentStep"] = list(steps)

        # Track consecutive same-error state
        last_error_hash: str = ""
        consecutive_same_error: int = 0

        # The current position within remaining[] (not task.step_index)
        # task.step_index is the global index; we skip steps already completed.
        pos = 0

        # Skip steps already completed based on task.step_index
        while pos < len(remaining) and remaining[pos].step_index < task.step_index:
            pos += 1

        while pos < len(remaining):
            step = remaining[pos]

            # 1. Token budget check
            if task.tokens_used >= task.token_budget:
                logger.warning(
                    "ReflectionLoop: task %s halted -- token limit reached (%d/%d)",
                    task.task_id,
                    task.tokens_used,
                    task.token_budget,
                )
                task.status = "failed"
                task.last_error = (
                    f"Token budget exceeded: used={task.tokens_used}, budget={task.token_budget}"
                )
                self._store.checkpoint(task)
                await self._bus.emit(
                    {
                        "type": "budget_exceeded",
                        "task_id": task.task_id,
                        "tokens_used": task.tokens_used,
                        "token_budget": task.token_budget,
                    }
                )
                return task

            # 2. Execute step
            result = await self._executor.execute_step(step, task)

            # 3. Evaluate
            if self.evaluate(result):
                # Success path
                task.step_index = step.step_index + 1
                consecutive_same_error = 0
                last_error_hash = ""
                self._store.checkpoint(task)
                pos += 1
                continue

            # Failure path
            error_hash = self._error_hash(result.error)
            if error_hash == last_error_hash:
                consecutive_same_error += 1
            else:
                consecutive_same_error = 1
                last_error_hash = error_hash

            task.error_count += 1
            task.last_error = result.error
            logger.warning(
                "ReflectionLoop: step %d failed for task %s "
                "(error=%r, consecutive=%d)",
                step.step_index,
                task.task_id,
                result.error[:120],
                consecutive_same_error,
            )

            if consecutive_same_error >= _MAX_CONSECUTIVE_SAME_ERRORS:
                # Escalate
                task.status = "failed"
                self._store.checkpoint(task)
                await self._bus.emit(
                    {
                        "type": "escalation",
                        "task_id": task.task_id,
                        "step_index": step.step_index,
                        "error": result.error,
                        "consecutive_failures": consecutive_same_error,
                        "message": (
                            f"Agent stopped after {consecutive_same_error} consecutive "
                            f"identical failures: {result.error}"
                        ),
                    }
                )
                logger.error(
                    "ReflectionLoop: escalating task %s after %d same errors: %r",
                    task.task_id,
                    consecutive_same_error,
                    result.error[:120],
                )
                return task

            # Replan remaining steps
            steps_from_here = remaining[pos:]
            try:
                revised_steps, replan_tokens = self._planner.replan(
                    steps_from_here, result.error, task.goal
                )
                task.tokens_used += replan_tokens
            except Exception as exc:  # noqa: BLE001
                logger.error("ReflectionLoop: replan failed for task %s: %s", task.task_id, exc)
                # Can't replan -- escalate
                task.status = "failed"
                task.last_error = f"Replan failed: {exc}"
                self._store.checkpoint(task)
                await self._bus.emit(
                    {
                        "type": "escalation",
                        "task_id": task.task_id,
                        "step_index": step.step_index,
                        "error": str(exc),
                        "message": f"Replan failed: {exc}",
                    }
                )
                return task

            # Replace remaining list from current position onwards
            remaining = remaining[:pos] + revised_steps
            # Update plan_json to reflect the new plan
            task.plan_json = json.dumps(
                [
                    {
                        "step_index": s.step_index,
                        "tool_name": s.tool_name,
                        "description": s.description,
                        "params": s.params,
                        "depends_on": s.depends_on,
                    }
                    for s in remaining
                ]
            )
            self._store.checkpoint(task)
            # Do NOT advance pos -- retry the current position with revised steps

        # All steps completed
        task.status = "done"
        self._store.checkpoint(task)
        await self._bus.emit(
            {
                "type": "task_done",
                "task_id": task.task_id,
                "steps_completed": task.step_index,
            }
        )
        logger.info(
            "ReflectionLoop: task %s completed successfully (%d steps)",
            task.task_id,
            task.step_index,
        )
        return task

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _error_hash(error: str) -> str:
        """Return an MD5 hex digest of *error* for deduplication.

        Not used for security -- only for detecting repeated identical errors.
        """
        return hashlib.md5(error.encode("utf-8", errors="replace")).hexdigest()  # noqa: S324
