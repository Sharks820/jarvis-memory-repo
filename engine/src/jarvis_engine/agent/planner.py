"""TaskPlanner -- LLM-driven goal decomposition for the agent ReAct loop.

Breaks a user goal into an ordered list of AgentStep objects by calling
ModelGateway.complete() with a system prompt containing available tool schemas.

Usage::

    planner = TaskPlanner(gateway=gateway, registry=registry)
    steps, tokens_used = planner.plan("Read README and summarise it")
    # returns ([AgentStep(0, 'file', ...), ...], 35)

    # After a step fails:
    revised, tokens = planner.replan(remaining_steps, "FileNotFoundError", goal)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis_engine.agent.tool_registry import ToolRegistry
    from jarvis_engine.gateway.models import ModelGateway

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AgentStep dataclass
# ---------------------------------------------------------------------------

_PLAN_SYSTEM_PROMPT = (
    "You are a task planner for an autonomous agent. Break the goal into ordered steps. "
    "Each step uses exactly one tool. "
    "Output a JSON array of objects with keys: step_index, tool_name, description, params, depends_on. "
    "Available tools: {schemas}"
)

_REPLAN_SYSTEM_PROMPT = (
    "You are a task planner for an autonomous agent. A step failed with the error below. "
    "Revise the remaining steps to recover from the error and complete the original goal. "
    "Output a JSON array of objects with keys: step_index, tool_name, description, params, depends_on. "
    "Original goal: {goal}\n"
    "Error: {error}\n"
    "Remaining steps (to revise): {remaining}"
)


@dataclass
class AgentStep:
    """A single step in an agent plan."""

    step_index: int
    tool_name: str
    description: str
    params: dict[str, Any]
    depends_on: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TaskPlanner
# ---------------------------------------------------------------------------


class TaskPlanner:
    """Decomposes a goal into AgentStep objects using an LLM."""

    def __init__(
        self,
        gateway: "ModelGateway",
        registry: "ToolRegistry",
    ) -> None:
        self._gateway = gateway
        self._registry = registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, goal: str) -> tuple[list[AgentStep], int]:
        """Break *goal* into ordered AgentStep objects.

        Args:
            goal: The user's high-level task description.

        Returns:
            (steps, tokens_used) where steps is list[AgentStep] and
            tokens_used is the total token count for this LLM call.

        Raises:
            ValueError: If the LLM returns invalid JSON or a response that
                cannot be parsed into AgentStep objects.
        """
        schemas = self._registry.schemas_for_prompt()
        system_content = _PLAN_SYSTEM_PROMPT.format(schemas=json.dumps(schemas))
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": goal},
        ]

        response = self._gateway.complete(messages, route_reason="agent_planning")
        tokens_used = response.input_tokens + response.output_tokens

        steps = self._parse_steps(response.text)
        logger.info(
            "TaskPlanner.plan: parsed %d steps for goal %r (tokens=%d)",
            len(steps),
            goal[:80],
            tokens_used,
        )
        return steps, tokens_used

    def replan(
        self,
        remaining_steps: list[AgentStep],
        error: str,
        goal: str,
    ) -> tuple[list[AgentStep], int]:
        """Revise *remaining_steps* after an error.

        Args:
            remaining_steps: Steps not yet completed.
            error: The error message from the failed step.
            goal: The original user goal (for context).

        Returns:
            (revised_steps, tokens_used)

        Raises:
            ValueError: If the LLM response cannot be parsed.
        """
        remaining_json = json.dumps(
            [
                {
                    "step_index": s.step_index,
                    "tool_name": s.tool_name,
                    "description": s.description,
                    "params": s.params,
                    "depends_on": s.depends_on,
                }
                for s in remaining_steps
            ]
        )
        system_content = _REPLAN_SYSTEM_PROMPT.format(
            goal=goal,
            error=error,
            remaining=remaining_json,
        )
        messages = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": (
                    f"The original goal is: {goal}\n"
                    f"A step failed with error: {error}\n"
                    "Please provide a revised plan as a JSON array."
                ),
            },
        ]

        response = self._gateway.complete(messages, route_reason="agent_planning")
        tokens_used = response.input_tokens + response.output_tokens

        steps = self._parse_steps(response.text)
        logger.info(
            "TaskPlanner.replan: revised to %d steps (tokens=%d, error=%r)",
            len(steps),
            tokens_used,
            error[:80],
        )
        return steps, tokens_used

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_steps(self, text: str) -> list[AgentStep]:
        """Extract and parse a JSON array of steps from LLM response text.

        Handles markdown code fences (```json ... ``` or ``` ... ```).

        Raises:
            ValueError: If text is not a JSON array or items are missing
                required fields.
        """
        cleaned = self._strip_code_fences(text.strip())

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"TaskPlanner: LLM response is not valid JSON: {exc!s}\nResponse: {text[:200]}"
            ) from exc

        if not isinstance(data, list):
            raise ValueError(
                f"TaskPlanner: expected a JSON array but got {type(data).__name__!r}. "
                f"Response: {text[:200]}"
            )

        steps: list[AgentStep] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(
                    f"TaskPlanner: step {i} is not an object: {item!r}"
                )
            # Validate required fields
            for required in ("tool_name", "description", "params"):
                if required not in item:
                    raise ValueError(
                        f"TaskPlanner: step {i} missing required field {required!r}: {item!r}"
                    )
            step_index = item.get("step_index", i)
            steps.append(
                AgentStep(
                    step_index=step_index,
                    tool_name=item["tool_name"],
                    description=item["description"],
                    params=item["params"],
                    depends_on=list(item.get("depends_on", [])),
                )
            )

        return steps

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove markdown code fence wrappers from *text*."""
        # Match ```json ... ``` or ``` ... ``` (multiline)
        pattern = r"^```(?:json)?\s*\n?([\s\S]*?)\n?```\s*$"
        match = re.match(pattern, text.strip(), re.DOTALL)
        if match:
            return match.group(1).strip()
        return text
