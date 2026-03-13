"""Command Bus: central dispatch for all Jarvis commands.

Every cmd_* function in main.py creates a Command dataclass and dispatches it
through the bus.  Handlers registered on the bus perform the actual work.
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
import threading
from typing import TYPE_CHECKING, Any, Callable, TypeVar

if TYPE_CHECKING:
    from jarvis_engine.gateway.classifier import IntentClassifier
    from jarvis_engine.gateway.models import ModelGateway
    from jarvis_engine.knowledge.graph import KnowledgeGraph
    from jarvis_engine.learning.preferences import PreferenceTracker
    from jarvis_engine.learning.engine import ConversationLearningEngine
    from jarvis_engine.learning.feedback import ResponseFeedbackTracker
    from jarvis_engine.learning.usage_patterns import UsagePatternTracker
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

T = TypeVar("T")

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class AppContext:
    """Typed container for subsystem references exposed on the CommandBus.

    Replaces the previous pattern of monkey-patching private attributes
    (``bus._engine``, ``bus._kg``, etc.) with ``# type: ignore[attr-defined]``.
    All fields are Optional so the context remains usable even when some
    subsystems fail to initialize.
    """

    engine: MemoryEngine | None = None
    embed_service: EmbeddingService | None = None
    intent_classifier: IntentClassifier | None = None
    kg: KnowledgeGraph | None = None
    gateway: ModelGateway | None = None
    pref_tracker: PreferenceTracker | None = None
    feedback_tracker: ResponseFeedbackTracker | None = None
    usage_tracker: UsagePatternTracker | None = None
    learning_engine: ConversationLearningEngine | None = None


class CommandBus:
    """Registry + dispatcher: maps command types to handler callables.

    Thread-safe: register() and dispatch() are guarded by an RLock so the bus
    can be used safely from daemon threads, the mobile API server, etc.
    """

    def __init__(self) -> None:
        self._handlers: dict[type, Callable[..., Any]] = {}
        self._lock = threading.RLock()
        self.ctx: AppContext = AppContext()

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
        except (RuntimeError, ValueError, TypeError, OSError, sqlite3.Error) as exc:
            logger.exception(
                "Handler for %s raised an exception: %s", type(command).__name__, exc
            )
            raise

    @property
    def registered_count(self) -> int:
        with self._lock:
            return len(self._handlers)
