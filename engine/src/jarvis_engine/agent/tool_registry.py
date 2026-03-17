"""ToolRegistry -- pluggable tool discovery for the agent ReAct loop.

Tools are registered with a JSON Schema descriptor so the LLM can understand
what arguments each tool accepts.  The registry exposes schemas_for_prompt()
to inject tool descriptions into the system prompt.

Destructive tools always require human approval regardless of the
requires_approval flag set by the caller.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ToolCallable(Protocol):
    """Async callable that a tool's execute field must satisfy."""

    async def __call__(self, **kwargs: Any) -> Any:  # pragma: no cover
        ...


def _default_validate(**kwargs: Any) -> bool:  # noqa: ARG001
    return True


def _default_estimate_cost(**kwargs: Any) -> float:  # noqa: ARG001
    return 0.0


@dataclass
class ToolSpec:
    """Specification for a single agent tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[..., Any]
    validate: Callable[..., bool] = field(default_factory=lambda: _default_validate)
    estimate_cost: Callable[..., float] = field(
        default_factory=lambda: _default_estimate_cost
    )
    requires_approval: bool = False
    is_destructive: bool = False

    def __post_init__(self) -> None:
        if self.is_destructive:
            self.requires_approval = True


class ToolRegistry:
    """In-process registry of available agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, spec: ToolSpec) -> None:
        """Register *spec*.  Logs a warning if name already exists."""
        if spec.name in self._tools:
            logger.warning(
                "ToolRegistry: overwriting existing tool %r with new spec", spec.name
            )
        self._tools[spec.name] = spec

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> ToolSpec | None:
        """Return ToolSpec for *name*, or None."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolSpec]:
        """Return all registered tool specs."""
        return list(self._tools.values())

    def schemas_for_prompt(self) -> list[dict[str, Any]]:
        """Return a list of tool schema dicts suitable for LLM prompt injection."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
                "requires_approval": spec.requires_approval,
            }
            for spec in self._tools.values()
        ]

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._tools)
