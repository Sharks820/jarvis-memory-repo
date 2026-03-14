"""Least-privilege execution policy and mission-level guardrails.

SEC-02: Defines ``ExecutionPolicy`` for automated tool execution with
default deny-mutations policy.

SEC-03: Defines ``MissionPolicy`` for mission-level guardrails with
allowed tools, data scopes, and side-effect limits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# SEC-02: Default read-only commands that are always allowed
_DEFAULT_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "search_web",
    "memory_search",
    "memory_read",
    "knowledge_graph_query",
    "list_missions",
    "get_mission",
    "status",
    "get_context",
    "hybrid_search",
    "fetch_page_text",
})

# Commands that always require explicit approval
_DEFAULT_BLOCKED_COMMANDS: frozenset[str] = frozenset({
    "delete_memory",
    "drop_table",
    "exec_shell",
    "file_write",
    "file_delete",
    "send_email",
    "credential_rotate",
    "block_ip",
    "unblock_ip",
    "containment_recover",
})


@dataclass(frozen=True)
class ExecutionPolicy:
    """Least-privilege policy for automated tool execution paths.

    Parameters
    ----------
    allowed_commands:
        Set of command names that are permitted without approval.
    blocked_commands:
        Set of command names that are always denied.
    max_side_effects:
        Maximum number of state-mutating operations per session.
    require_approval:
        If True, any command not in *allowed_commands* requires
        explicit owner approval before execution.
    """

    allowed_commands: frozenset[str] = _DEFAULT_ALLOWED_COMMANDS
    blocked_commands: frozenset[str] = _DEFAULT_BLOCKED_COMMANDS
    max_side_effects: int = 10
    require_approval: bool = True


def check_execution_policy(
    command: str,
    policy: ExecutionPolicy,
    *,
    side_effect_count: int = 0,
) -> bool:
    """Check whether *command* is permitted under *policy*.

    Returns ``True`` if the command is allowed, ``False`` otherwise.

    Parameters
    ----------
    command:
        The command name to check.
    policy:
        The execution policy to enforce.
    side_effect_count:
        Current count of side effects already performed in this session.
    """
    if not command or not command.strip():
        logger.debug("Empty command rejected by execution policy")
        return False

    cmd = command.strip().lower()

    # Blocked commands are always denied
    if cmd in policy.blocked_commands:
        logger.info("Command %r blocked by execution policy", cmd)
        return False

    # Side-effect limit exceeded
    if side_effect_count >= policy.max_side_effects:
        if cmd not in policy.allowed_commands:
            logger.info(
                "Command %r denied: side-effect limit %d reached",
                cmd, policy.max_side_effects,
            )
            return False

    # Explicitly allowed commands pass
    if cmd in policy.allowed_commands:
        return True

    # If require_approval is set and command is not explicitly allowed, deny
    if policy.require_approval:
        logger.info("Command %r requires approval (not in allowed set)", cmd)
        return False

    return True


# SEC-03: Mission-level policy guardrails

# Default tools allowed during mission execution
_DEFAULT_MISSION_TOOLS: frozenset[str] = frozenset({
    "search_web",
    "memory_read",
    "memory_search",
    "fetch_page_text",
    "hybrid_search",
    "knowledge_graph_query",
})

# Default data scopes for missions
_DEFAULT_DATA_SCOPES: frozenset[str] = frozenset({
    "web_public",
    "memory_read",
    "knowledge_graph_read",
})


@dataclass(frozen=True)
class MissionPolicy:
    """Policy guardrails for mission execution.

    Parameters
    ----------
    allowed_tools:
        Tools the mission is permitted to invoke.
    data_scopes:
        Data access scopes the mission may use.
    max_side_effects:
        Maximum state-mutating operations per mission run.
    max_duration_s:
        Maximum wall-clock seconds the mission may run.
    allow_file_writes:
        Whether the mission may write files (default False).
    allow_network_mutations:
        Whether the mission may make non-GET network requests.
    """

    allowed_tools: frozenset[str] = _DEFAULT_MISSION_TOOLS
    data_scopes: frozenset[str] = _DEFAULT_DATA_SCOPES
    max_side_effects: int = 10
    max_duration_s: int = 300
    allow_file_writes: bool = False
    allow_network_mutations: bool = False


class MissionPolicyViolation(RuntimeError):
    """Raised when a mission attempts an action that violates its policy."""

    def __init__(self, mission_id: str, violation: str) -> None:
        self.mission_id = mission_id
        self.violation = violation
        super().__init__(
            f"Mission policy violation [{mission_id}]: {violation}"
        )


def check_mission_tool(
    tool_name: str,
    policy: MissionPolicy,
) -> bool:
    """Check whether *tool_name* is allowed by the mission policy.

    Returns ``True`` if allowed, ``False`` otherwise.
    """
    if not tool_name:
        return False
    return tool_name.strip().lower() in policy.allowed_tools


def check_mission_data_scope(
    scope: str,
    policy: MissionPolicy,
) -> bool:
    """Check whether *scope* is within the mission's allowed data scopes."""
    if not scope:
        return False
    return scope.strip().lower() in policy.data_scopes


def enforce_mission_policy(
    mission_id: str,
    tool_name: str,
    policy: MissionPolicy,
    *,
    side_effect_count: int = 0,
    elapsed_s: float = 0.0,
) -> None:
    """Enforce the mission policy, raising ``MissionPolicyViolation`` on violation.

    Parameters
    ----------
    mission_id:
        The mission being checked.
    tool_name:
        Tool the mission is trying to invoke.
    policy:
        The mission's policy guardrails.
    side_effect_count:
        Number of side effects already performed.
    elapsed_s:
        Wall-clock seconds elapsed since mission start.
    """
    if not check_mission_tool(tool_name, policy):
        raise MissionPolicyViolation(
            mission_id,
            f"tool '{tool_name}' not in allowed_tools",
        )

    if side_effect_count >= policy.max_side_effects:
        raise MissionPolicyViolation(
            mission_id,
            f"side-effect limit {policy.max_side_effects} exceeded "
            f"(current: {side_effect_count})",
        )

    if elapsed_s > policy.max_duration_s:
        raise MissionPolicyViolation(
            mission_id,
            f"duration limit {policy.max_duration_s}s exceeded "
            f"(elapsed: {elapsed_s:.1f}s)",
        )
