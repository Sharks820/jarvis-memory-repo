"""Structural (duck-typed) protocols for commonly injected services.

Using ``typing.Protocol`` lets modules declare the interface they need
without importing the concrete class, eliminating circular imports and
enabling lightweight test doubles.
"""

from __future__ import annotations

__all__ = ["EmbedServiceProtocol", "ForensicLoggerProtocol"]

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedServiceProtocol(Protocol):
    """Any object that can produce a float-vector embedding for text.

    The canonical implementation is
    ``jarvis_engine.memory.embeddings.EmbeddingService``, but tests may
    substitute a simple stub that returns a fixed-length vector.
    """

    def embed(self, text: str, prefix: str = "search_document") -> list[float]:
        """Return a dense embedding vector for *text*."""
        ...

    def embed_batch(
        self,
        texts: list[str],
        prefix: str = "search_document",
    ) -> list[list[float]]:
        """Return dense embedding vectors for *texts*."""
        ...


@runtime_checkable
class ForensicLoggerProtocol(Protocol):
    """Any object that can append structured events to a forensic log.

    The canonical implementation is
    ``jarvis_engine.security.forensic_logger.ForensicLogger``.
    """

    def log_event(self, event: dict) -> None:
        """Persist *event* (a JSON-serialisable dict) to the log."""
        ...
