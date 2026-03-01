"""Lazy-loaded embedding service for semantic memory.

Uses nomic-ai/nomic-embed-text-v1.5 (768-dim, 8192 token context).
The model is NOT loaded at import time -- only on first embed() call.

This module is created in Plan 01 but wired into the memory engine in Plan 02.
"""

from __future__ import annotations

import threading
from typing import Any


class EmbeddingService:
    """Lazy-loaded sentence-transformer singleton (thread-safe)."""

    MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
    # Use default revision (latest release tag) rather than "main" to avoid
    # pulling unreviewed code from the repository HEAD.
    MODEL_REVISION = None

    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.Lock()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            # Double-checked locking
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                kwargs: dict[str, Any] = {
                    "trust_remote_code": True,
                }
                if self.MODEL_REVISION is not None:
                    kwargs["revision"] = self.MODEL_REVISION
                self._model = SentenceTransformer(
                    self.MODEL_NAME,
                    **kwargs,
                )
        return self._model

    def embed(self, text: str, prefix: str = "search_document") -> list[float]:
        """Embed a single text with the given prefix."""
        model = self._ensure_model()
        prefixed = f"{prefix}: {text}" if prefix else text
        vec = model.encode([prefixed], normalize_embeddings=True)
        return vec[0].tolist()

    def embed_query(self, query: str) -> list[float]:
        """Embed a query (uses search_query prefix)."""
        return self.embed(query, prefix="search_query")

    def embed_batch(self, texts: list[str], prefix: str = "search_document") -> list[list[float]]:
        """Embed a batch of texts."""
        model = self._ensure_model()
        prefixed = [f"{prefix}: {t}" if prefix else t for t in texts]
        vecs = model.encode(prefixed, normalize_embeddings=True)
        return [v.tolist() for v in vecs]
