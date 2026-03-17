"""StepExecutor -- tool dispatch with checkpointing and approval gating.

Each call to execute_step():
  1. Looks up the tool in the ToolRegistry.
  2. Checks the ApprovalGate; blocks if human approval is required.
  3. Checkpoints the task to AgentStateStore (crash-safe).
  4. Emits step_start on the ProgressEventBus.
  5. Calls the tool (async or sync).
  6. Emits step_done on the ProgressEventBus.
  7. Returns a StepResult.

Usage::

    executor = StepExecutor(registry, gate, store, bus)
    result = await executor.execute_step(step, task)
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.agent.approval_gate import ApprovalGate
    from jarvis_engine.agent.planner import AgentStep
    from jarvis_engine.agent.progress_bus import ProgressEventBus
    from jarvis_engine.agent.state_store import AgentStateStore, AgentTask
    from jarvis_engine.agent.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StepResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Result of a single step execution."""

    success: bool
    output: str = ""
    error: str = ""
    tokens_used: int = 0


# ---------------------------------------------------------------------------
# StepExecutor
# ---------------------------------------------------------------------------


class StepExecutor:
    """Dispatches a single AgentStep to the appropriate tool."""

    def __init__(
        self,
        registry: "ToolRegistry",
        gate: "ApprovalGate",
        store: "AgentStateStore",
        bus: "ProgressEventBus",
    ) -> None:
        self._registry = registry
        self._gate = gate
        self._store = store
        self._bus = bus

    async def execute_step(
        self,
        step: "AgentStep",
        task: "AgentTask",
    ) -> StepResult:
        """Execute *step* within *task* context.

        Returns:
            StepResult with success/failure information.
        """
        from jarvis_engine.agent.approval_gate import ApprovalDecision

        # 1. Look up tool
        spec = self._registry.get(step.tool_name)
        if spec is None:
            logger.warning("StepExecutor: unknown tool %r for task %s", step.tool_name, task.task_id)
            return StepResult(success=False, error=f"Unknown tool: {step.tool_name!r}")

        # 2. Check approval gate
        decision = self._gate.check(spec, step.params)
        if decision == ApprovalDecision.REQUIRES_APPROVAL:
            task.status = "blocked"
            task.approval_needed = True
            self._store.checkpoint(task)
            await self._bus.emit(
                {
                    "type": "approval_needed",
                    "task_id": task.task_id,
                    "step_index": step.step_index,
                    "tool_name": step.tool_name,
                }
            )
            approved = await self._gate.wait_for_approval(task.task_id, step.description)
            if not approved:
                task.approval_needed = False
                return StepResult(
                    success=False,
                    error=f"Approval rejected for step {step.step_index} ({step.tool_name})",
                )
            task.approval_needed = False

        # 3. Checkpoint (running state)
        task.status = "running"
        self._store.checkpoint(task)

        # 4. Emit step_start
        await self._bus.emit(
            {
                "type": "step_start",
                "task_id": task.task_id,
                "step_index": step.step_index,
                "tool_name": step.tool_name,
                "description": step.description,
            }
        )

        # 5. Call tool (handle both sync and async)
        try:
            raw_result = spec.execute(**step.params)
            if inspect.isawaitable(raw_result):
                result_value = await raw_result
            else:
                result_value = raw_result
            output = str(result_value)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "StepExecutor: tool %r raised during task %s step %d",
                step.tool_name,
                task.task_id,
                step.step_index,
            )
            await self._bus.emit(
                {
                    "type": "step_done",
                    "task_id": task.task_id,
                    "step_index": step.step_index,
                    "success": False,
                    "error": str(exc),
                }
            )
            return StepResult(success=False, error=str(exc))

        # 6. Emit step_done
        await self._bus.emit(
            {
                "type": "step_done",
                "task_id": task.task_id,
                "step_index": step.step_index,
                "success": True,
                "output": output[:500],  # Truncate for event payload
            }
        )

        logger.debug(
            "StepExecutor: step %d (%s) succeeded for task %s",
            step.step_index,
            step.tool_name,
            task.task_id,
        )
        return StepResult(success=True, output=output)
