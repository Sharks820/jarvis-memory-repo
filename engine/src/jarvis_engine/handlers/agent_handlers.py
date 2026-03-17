"""Stub CQRS handler classes for the Jarvis agent subsystem.

All three handlers return return_code=0 with a "not yet implemented" message.
They define the stable public handler interface that Phase 22 (Core Agent Loop)
fills in.  The handler signatures MUST NOT change -- only the implementation
bodies evolve.

Pattern follows existing handlers (e.g. ops_handlers.py):
  - __init__ accepts root: Path (and optional subsystem objects)
  - .handle(cmd) -> Result is registered on the bus
  - Lazy imports of domain modules inside handle() to reduce startup overhead
"""

from __future__ import annotations

import logging
from pathlib import Path

from jarvis_engine.commands.agent_commands import (
    AgentApproveCommand,
    AgentApproveResult,
    AgentRunCommand,
    AgentRunResult,
    AgentStatusCommand,
    AgentStatusResult,
)

logger = logging.getLogger(__name__)

_NOT_IMPLEMENTED_MSG = "Agent subsystem not yet implemented"


class AgentRunHandler:
    """Handle AgentRunCommand -- returns stub result."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: AgentRunCommand) -> AgentRunResult:
        logger.debug("AgentRunHandler: goal=%r (stub)", cmd.goal)
        return AgentRunResult(
            return_code=0,
            message=_NOT_IMPLEMENTED_MSG,
            task_id=cmd.task_id,
            status="pending",
        )


class AgentStatusHandler:
    """Handle AgentStatusCommand -- returns stub result."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: AgentStatusCommand) -> AgentStatusResult:
        logger.debug("AgentStatusHandler: task_id=%r (stub)", cmd.task_id)
        return AgentStatusResult(
            return_code=0,
            message=_NOT_IMPLEMENTED_MSG,
            task_id=cmd.task_id,
            status="pending",
        )


class AgentApproveHandler:
    """Handle AgentApproveCommand -- returns stub result."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: AgentApproveCommand) -> AgentApproveResult:
        logger.debug(
            "AgentApproveHandler: task_id=%r approved=%r (stub)",
            cmd.task_id,
            cmd.approved,
        )
        return AgentApproveResult(
            return_code=0,
            message=_NOT_IMPLEMENTED_MSG,
            task_id=cmd.task_id,
            action_taken="none",
        )
