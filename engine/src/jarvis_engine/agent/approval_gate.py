"""ApprovalGate -- safety layer that blocks destructive/costly tool calls.

Checks tool specs before execution and either auto-approves safe tools or
requires human approval for destructive/costly ones.  Approval state is
tracked via asyncio.Event objects so the agent loop can await a decision
without blocking the event loop.

Usage::

    gate = ApprovalGate(progress_bus=bus)

    decision = gate.check(spec, params)
    if decision == ApprovalDecision.REQUIRES_APPROVAL:
        approved = await gate.wait_for_approval(task_id, "run shell command")
        if not approved:
            raise RuntimeError("User rejected the action")
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis_engine.agent.progress_bus import ProgressEventBus
    from jarvis_engine.agent.tool_registry import ToolSpec

logger = logging.getLogger(__name__)


class ApprovalDecision(Enum):
    """Decision returned by ApprovalGate.check()."""

    AUTO = "auto"
    REQUIRES_APPROVAL = "requires_approval"


class ApprovalGate:
    """Safety gate that checks tool specs and manages pending approval futures."""

    def __init__(self, progress_bus: "ProgressEventBus") -> None:
        self._bus = progress_bus
        # Maps task_id -> (event, approved_flag)
        self._pending: dict[str, tuple[asyncio.Event, list[bool]]] = {}

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def check(self, spec: "ToolSpec", params: dict[str, Any]) -> ApprovalDecision:
        """Determine whether a tool call requires human approval.

        Returns REQUIRES_APPROVAL if:
        - spec.requires_approval is True, OR
        - spec.is_destructive is True, OR
        - spec.estimate_cost(**params) > 0

        Otherwise returns AUTO.
        """
        if spec.requires_approval or spec.is_destructive:
            logger.debug(
                "ApprovalGate.check: REQUIRES_APPROVAL for %r "
                "(requires_approval=%s, is_destructive=%s)",
                getattr(spec, "name", "?"),
                spec.requires_approval,
                spec.is_destructive,
            )
            return ApprovalDecision.REQUIRES_APPROVAL

        try:
            cost = spec.estimate_cost(**params)
        except Exception:  # noqa: BLE001
            cost = 0.0

        if cost > 0:
            logger.debug(
                "ApprovalGate.check: REQUIRES_APPROVAL for %r (cost=%.4f)",
                getattr(spec, "name", "?"),
                cost,
            )
            return ApprovalDecision.REQUIRES_APPROVAL

        return ApprovalDecision.AUTO

    # ------------------------------------------------------------------
    # Approval lifecycle
    # ------------------------------------------------------------------

    async def wait_for_approval(self, task_id: str, step_desc: str) -> bool:
        """Block until approve() or reject() is called for *task_id*.

        Emits an approval_needed event on the ProgressEventBus so downstream
        consumers (UI, tests) can react.

        Args:
            task_id: Unique identifier for the task awaiting approval.
            step_desc: Human-readable description of the step being gated.

        Returns:
            True if approved, False if rejected.
        """
        event = asyncio.Event()
        approved_flag: list[bool] = [False]  # mutable container for closure
        self._pending[task_id] = (event, approved_flag)

        await self._bus.emit(
            {
                "type": "approval_needed",
                "task_id": task_id,
                "step": step_desc,
            }
        )
        logger.info("ApprovalGate: awaiting approval for task %r (%s)", task_id, step_desc)

        await event.wait()

        self._pending.pop(task_id, None)
        result = approved_flag[0]
        logger.info(
            "ApprovalGate: task %r %s",
            task_id,
            "approved" if result else "rejected",
        )
        return result

    def approve(self, task_id: str) -> None:
        """Signal approval for *task_id*.

        Safe to call with an unknown task_id (no-op).
        """
        entry = self._pending.get(task_id)
        if entry is None:
            logger.debug("ApprovalGate.approve: unknown task_id %r (noop)", task_id)
            return
        event, approved_flag = entry
        approved_flag[0] = True
        event.set()
        logger.debug("ApprovalGate: approved task %r", task_id)

    def reject(self, task_id: str) -> None:
        """Signal rejection for *task_id*.

        Safe to call with an unknown task_id (no-op).
        """
        entry = self._pending.get(task_id)
        if entry is None:
            logger.debug("ApprovalGate.reject: unknown task_id %r (noop)", task_id)
            return
        event, approved_flag = entry
        approved_flag[0] = False
        event.set()
        logger.debug("ApprovalGate: rejected task %r", task_id)
