"""CQRS command and result dataclasses for the Jarvis agent subsystem.

These commands define the public API surface for Phase 22 (Core Agent Loop).
For Phase 20, the handlers return stub results with return_code=0 and a
"not yet implemented" message.  The command *signatures* are stable and will
not change in later phases -- only the handler implementations change.

Commands::

    AgentRunCommand            -- Submit a new agent task goal for execution
    AgentStatusCommand         -- Query the status of an in-progress task
    AgentApproveCommand        -- Approve or reject a task waiting for human approval
    AgentRegisterToolCommand   -- Register a new tool in ToolRegistry at runtime
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis_engine.commands.base import ResultBase


# ---------------------------------------------------------------------------
# AgentRunCommand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentRunCommand:
    """Submit a new agent task.

    Attributes:
        goal: Natural-language goal description for the agent (e.g. "build a
              Unity scene with a rotating cube").
        task_id: Optional caller-supplied stable ID.  If empty the handler
                 generates a UUID.
        token_budget: Maximum LLM tokens the agent may consume for this task.
    """

    goal: str = ""
    task_id: str = ""
    token_budget: int = 50000


@dataclass
class AgentRunResult(ResultBase):
    """Result of AgentRunCommand.

    Attributes:
        task_id: The ID assigned to the new task (caller-supplied or generated).
        status:  Initial task status, typically "pending" or "running".
    """

    task_id: str = ""
    status: str = ""


# ---------------------------------------------------------------------------
# AgentStatusCommand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentStatusCommand:
    """Query the current status of an agent task.

    Attributes:
        task_id: The task ID returned by AgentRunCommand.
    """

    task_id: str = ""


@dataclass
class AgentStatusResult(ResultBase):
    """Result of AgentStatusCommand.

    Attributes:
        task_id:     The queried task ID.
        status:      Current FSM state (pending/running/waiting_approval/completed/failed).
        step_index:  Index of the next step to execute (0-based).
        tokens_used: Cumulative LLM tokens consumed so far.
        last_error:  Most recent error string, empty if none.
    """

    task_id: str = ""
    status: str = ""
    step_index: int = 0
    tokens_used: int = 0
    last_error: str = ""


# ---------------------------------------------------------------------------
# AgentApproveCommand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentApproveCommand:
    """Approve or reject a task waiting for human approval.

    Attributes:
        task_id:  The task ID to approve or reject.
        approved: True to approve and continue; False to reject and cancel.
        reason:   Optional human-readable reason (stored for audit).
    """

    task_id: str = ""
    approved: bool = True
    reason: str = ""


@dataclass
class AgentApproveResult(ResultBase):
    """Result of AgentApproveCommand.

    Attributes:
        task_id:      The affected task ID.
        action_taken: Human-readable description of the action taken.
    """

    task_id: str = ""
    action_taken: str = ""


# ---------------------------------------------------------------------------
# AgentRegisterToolCommand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentRegisterToolCommand:
    """Register a new tool in ToolRegistry at runtime.

    Attributes:
        name:             Unique tool identifier (e.g. "mixamo").
        description:      Human-readable description of what the tool does.
        parameters:       JSON string of parameter schema (e.g. '{"url": "string"}').
        requires_approval: If True the tool invocation always requires human approval.
    """

    name: str = ""
    description: str = ""
    parameters: str = "{}"  # JSON string of parameter schema
    requires_approval: bool = False


@dataclass
class AgentRegisterToolResult(ResultBase):
    """Result of AgentRegisterToolCommand.

    Attributes:
        tool_name:  The name of the tool that was (or was not) registered.
        registered: True when the tool was successfully added to the registry.
    """

    tool_name: str = ""
    registered: bool = False
