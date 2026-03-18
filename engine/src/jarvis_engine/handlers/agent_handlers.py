"""Real CQRS handler implementations for the Jarvis agent subsystem.

Replaces the Phase 20 stubs with full wiring:
  - AgentRunHandler  -- creates task, checkpoints, launches background loop
  - AgentStatusHandler -- reads live state from AgentStateStore
  - AgentApproveHandler -- resolves pending approvals via ApprovalGate

Pattern follows existing handlers (e.g. ops_handlers.py):
  - __init__ accepts root: Path and subsystem objects
  - .handle(cmd) -> Result is registered on the bus
  - Lazy imports of domain modules inside methods
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis_engine.commands.agent_commands import (
    AgentApproveCommand,
    AgentApproveResult,
    AgentRegisterToolCommand,
    AgentRegisterToolResult,
    AgentRunCommand,
    AgentRunResult,
    AgentStatusCommand,
    AgentStatusResult,
)

if TYPE_CHECKING:
    from jarvis_engine.agent.approval_gate import ApprovalGate
    from jarvis_engine.agent.progress_bus import ProgressEventBus
    from jarvis_engine.agent.state_store import AgentStateStore
    from jarvis_engine.agent.tool_registry import ToolRegistry
    from jarvis_engine.gateway.models import ModelGateway

logger = logging.getLogger(__name__)

# Shared thread pool so all agent loops are bounded
_AGENT_EXECUTOR: ThreadPoolExecutor | None = None


def _get_agent_executor() -> ThreadPoolExecutor:
    global _AGENT_EXECUTOR  # noqa: PLW0603
    if _AGENT_EXECUTOR is None:
        _AGENT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-loop")
    return _AGENT_EXECUTOR


class AgentRunHandler:
    """Handle AgentRunCommand -- creates task and launches background agent loop."""

    def __init__(
        self,
        root: Path,
        *,
        gateway: "ModelGateway | None" = None,
        registry: "ToolRegistry | None" = None,
        store: "AgentStateStore | None" = None,
        gate: "ApprovalGate | None" = None,
        bus: "ProgressEventBus | None" = None,
    ) -> None:
        self._root = root
        self._gateway = gateway
        self._registry = registry
        self._store = store
        self._gate = gate
        self._bus = bus

    def handle(self, cmd: AgentRunCommand) -> AgentRunResult:
        from jarvis_engine.agent.state_store import AgentTask

        task_id = cmd.task_id or secrets.token_hex(8)
        task = AgentTask(
            task_id=task_id,
            goal=cmd.goal,
            status="pending",
            token_budget=cmd.token_budget,
        )

        if self._store is not None:
            self._store.checkpoint(task)

        # Launch background loop (non-blocking)
        executor = _get_agent_executor()
        executor.submit(self._run_agent_loop, task)

        logger.info("AgentRunHandler: submitted task %s goal=%r", task_id, cmd.goal)
        return AgentRunResult(
            return_code=0,
            message="Task submitted",
            task_id=task_id,
            status="pending",
        )

    # ------------------------------------------------------------------
    # Background agent loop
    # ------------------------------------------------------------------

    def _run_agent_loop(self, task: Any) -> None:
        """Execute the full plan->execute->reflect cycle in a background thread."""
        if self._gateway is None or self._registry is None or self._store is None:
            logger.warning(
                "AgentRunHandler: gateway/registry/store not configured for task %s -- "
                "marking failed",
                task.task_id,
            )
            task.status = "failed"
            task.last_error = "Agent subsystem not fully configured"
            if self._store is not None:
                self._store.checkpoint(task)
            return

        try:
            from jarvis_engine.agent.executor import StepExecutor
            from jarvis_engine.agent.planner import TaskPlanner
            from jarvis_engine.agent.reflection import ReflectionLoop

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                planner = TaskPlanner(self._gateway, self._registry)
                executor_obj = StepExecutor(
                    self._registry,
                    self._gate,
                    self._store,
                    self._bus,
                )
                reflection = ReflectionLoop(executor_obj, planner, self._store, self._bus)

                steps, tokens = planner.plan(task.goal)
                task.plan_json = json.dumps([asdict(s) for s in steps])
                task.tokens_used += tokens
                task.status = "running"
                self._store.checkpoint(task)

                loop.run_until_complete(reflection.run_loop(task, steps))
            finally:
                loop.close()
                asyncio.set_event_loop(None)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "AgentRunHandler: background loop failed for task %s: %s",
                task.task_id,
                exc,
            )
            task.status = "failed"
            task.last_error = str(exc)
            try:
                self._store.checkpoint(task)
            except Exception:  # noqa: BLE001
                pass


class AgentStatusHandler:
    """Handle AgentStatusCommand -- reads live task state from AgentStateStore."""

    def __init__(
        self,
        root: Path,
        *,
        store: "AgentStateStore | None" = None,
    ) -> None:
        self._root = root
        self._store = store

    def handle(self, cmd: AgentStatusCommand) -> AgentStatusResult:
        if self._store is None:
            return AgentStatusResult(
                return_code=1,
                message="Agent state store not configured",
                task_id=cmd.task_id,
            )

        task = self._store.load(cmd.task_id)
        if task is None:
            return AgentStatusResult(
                return_code=1,
                message=f"Task not found: {cmd.task_id}",
                task_id=cmd.task_id,
            )

        return AgentStatusResult(
            return_code=0,
            message="ok",
            task_id=task.task_id,
            status=task.status,
            step_index=task.step_index,
            tokens_used=task.tokens_used,
            last_error=task.last_error,
        )


class AgentRegisterToolHandler:
    """Handle AgentRegisterToolCommand -- registers a new tool in ToolRegistry at runtime."""

    def __init__(
        self,
        root: Path,
        *,
        registry: "ToolRegistry | None" = None,
    ) -> None:
        self._root = root
        self._registry = registry

    def handle(self, cmd: AgentRegisterToolCommand) -> AgentRegisterToolResult:
        import json as _json

        if self._registry is None:
            return AgentRegisterToolResult(
                return_code=1,
                message="ToolRegistry not configured",
                tool_name=cmd.name,
                registered=False,
            )

        if not cmd.name:
            return AgentRegisterToolResult(
                return_code=1,
                message="Tool name must be non-empty",
                tool_name="",
                registered=False,
            )

        try:
            params_dict = _json.loads(cmd.parameters)
        except _json.JSONDecodeError as exc:
            return AgentRegisterToolResult(
                return_code=1,
                message=f"Invalid parameters JSON: {exc}",
                tool_name=cmd.name,
                registered=False,
            )

        from jarvis_engine.agent.tool_registry import ToolSpec

        _tool_name = cmd.name  # capture for closure

        def _placeholder_execute(**_kwargs: object) -> str:
            return f"Tool {_tool_name} invoked (runtime-registered, no execute implementation)"

        spec = ToolSpec(
            name=cmd.name,
            description=cmd.description,
            parameters=params_dict,
            execute=_placeholder_execute,
            requires_approval=cmd.requires_approval,
        )
        self._registry.register(spec)

        logger.info(
            "AgentRegisterToolHandler: registered tool %r at runtime",
            cmd.name,
        )
        return AgentRegisterToolResult(
            return_code=0,
            message=f"Tool '{cmd.name}' registered",
            tool_name=cmd.name,
            registered=True,
        )


class AgentApproveHandler:
    """Handle AgentApproveCommand -- resolves pending approvals via ApprovalGate."""

    def __init__(
        self,
        root: Path,
        *,
        gate: "ApprovalGate | None" = None,
    ) -> None:
        self._root = root
        self._gate = gate

    def handle(self, cmd: AgentApproveCommand) -> AgentApproveResult:
        if self._gate is None:
            return AgentApproveResult(
                return_code=1,
                message="ApprovalGate not configured",
                task_id=cmd.task_id,
                action_taken="none",
            )

        if cmd.approved:
            self._gate.approve(cmd.task_id)
            action = "approved"
        else:
            self._gate.reject(cmd.task_id)
            action = "rejected"

        logger.info(
            "AgentApproveHandler: task %s %s (reason=%r)",
            cmd.task_id,
            action,
            cmd.reason,
        )
        return AgentApproveResult(
            return_code=0,
            message=f"Task {action}",
            task_id=cmd.task_id,
            action_taken=action,
        )
