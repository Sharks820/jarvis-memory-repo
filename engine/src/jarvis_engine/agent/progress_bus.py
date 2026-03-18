"""ProgressEventBus -- bounded asyncio.Queue fan-out event bus.

Provides a pub/sub mechanism for streaming agent progress events to downstream
consumers (SSE endpoints, UI widgets, tests).  Each subscriber gets its own
bounded queue; emit() fans out to all of them.  Full queues drop the oldest
event to prevent back-pressure stalls.

Module-level singleton accessible via get_progress_bus().
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE = 256


class ProgressEventBus:
    """Fan-out event bus backed by asyncio.Queue per subscriber."""

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._max_size = max_size
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._sub_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Create and return a new subscriber queue.

        The caller receives all events emitted after this call.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_size)
        with self._sub_lock:
            self._subscribers.append(q)
            total = len(self._subscribers)
        logger.debug("ProgressEventBus: subscriber added (total=%d)", total)
        return q

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove *queue* from the subscriber list.

        Silently ignores queues not currently subscribed.
        """
        try:
            with self._sub_lock:
                self._subscribers.remove(queue)
                remaining = len(self._subscribers)
            logger.debug(
                "ProgressEventBus: subscriber removed (remaining=%d)", remaining
            )
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    async def emit(self, event: dict[str, Any]) -> None:
        """Fan out *event* to all subscriber queues.

        If a subscriber's queue is full, the oldest item is dropped before
        adding the new event (no blocking).
        """
        with self._sub_lock:
            snapshot = list(self._subscribers)
        for q in snapshot:
            if q.full():
                try:
                    q.get_nowait()
                    logger.debug("ProgressEventBus: dropped oldest event from full queue")
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("ProgressEventBus: queue still full after drop, skipping subscriber")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bus: ProgressEventBus | None = None


def get_progress_bus() -> ProgressEventBus:
    """Return the module-level singleton ProgressEventBus, creating it if needed."""
    global _bus  # noqa: PLW0603
    if _bus is None:
        _bus = ProgressEventBus()
        logger.debug("ProgressEventBus: singleton created")
    return _bus
