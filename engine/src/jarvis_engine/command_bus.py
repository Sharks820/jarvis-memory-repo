"""Command Bus: central dispatch for all Jarvis commands.

Every cmd_* function in main.py creates a Command dataclass and dispatches it
through the bus.  Handlers registered on the bus perform the actual work.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


class CommandBus:
    """Registry + dispatcher: maps command types to handler callables."""

    def __init__(self) -> None:
        self._handlers: dict[type, Callable[..., Any]] = {}

    def register(self, command_type: type, handler: Callable[..., Any]) -> None:
        """Register *handler* for *command_type*.  Overwrites silently."""
        self._handlers[command_type] = handler

    def dispatch(self, command: object) -> Any:
        """Look up the handler for *type(command)* and call it."""
        handler = self._handlers.get(type(command))
        if handler is None:
            raise ValueError(f"No handler for {type(command).__name__}")
        return handler(command)

    @property
    def registered_count(self) -> int:
        return len(self._handlers)
