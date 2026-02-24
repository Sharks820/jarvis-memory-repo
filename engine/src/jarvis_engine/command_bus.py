"""Command Bus: central dispatch for all Jarvis commands.

Every cmd_* function in main.py creates a Command dataclass and dispatches it
through the bus.  Handlers registered on the bus perform the actual work.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


class CommandBus:
    """Registry + dispatcher: maps command types to handler callables.

    Thread-safe: register() and dispatch() are guarded by an RLock so the bus
    can be used safely from daemon threads, the mobile API server, etc.
    """

    def __init__(self) -> None:
        self._handlers: dict[type, Callable[..., Any]] = {}
        self._lock = threading.RLock()

    def register(self, command_type: type, handler: Callable[..., Any]) -> None:
        """Register *handler* for *command_type*.  Warns on overwrite."""
        with self._lock:
            if command_type in self._handlers:
                logger.warning("Overwriting handler for %s", command_type.__name__)
            self._handlers[command_type] = handler

    def dispatch(self, command: object) -> Any:
        """Look up the handler for *type(command)* and call it."""
        with self._lock:
            handler = self._handlers.get(type(command))
        if handler is None:
            raise ValueError(f"No handler for {type(command).__name__}")
        try:
            return handler(command)
        except Exception:
            logger.exception(
                "Handler for %s raised an exception", type(command).__name__
            )
            raise

    @property
    def registered_count(self) -> int:
        with self._lock:
            return len(self._handlers)
