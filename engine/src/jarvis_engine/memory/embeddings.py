"""Lazy-loaded embedding service for semantic memory.

Uses nomic-ai/nomic-embed-text-v1.5 (768-dim, 8192 token context).
The model is NOT loaded at import time -- only on first embed() call.

This module is created in Plan 01 but wired into the memory engine in Plan 02.
"""

from __future__ import annotations

import collections
import threading
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class CacheInfo(NamedTuple):
    """Statistics for the embedding LRU cache."""

    hits: int
    misses: int
    size: int
    maxsize: int


class EmbeddingService:
    """Lazy-loaded sentence-transformer singleton (thread-safe).

    Includes a thread-safe LRU cache on ``embed()`` / ``embed_query()``
    to eliminate redundant model.encode() calls when the same text is
    embedded more than once per request (e.g. hybrid_search + vec search).
    """

    MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
    # Use default revision (latest release tag) rather than "main" to avoid
    # pulling unreviewed code from the repository HEAD.
    MODEL_REVISION = None

    _CACHE_MAXSIZE = 1024

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()
        # LRU cache: keyed by (text, prefix), stores list[float]
        self._cache: collections.OrderedDict[tuple[str, str], list[float]] = (
            collections.OrderedDict()
        )
        self._cache_lock = threading.Lock()
        self._cache_hits = 0
        self._cache_misses = 0

    def _ensure_model(self) -> SentenceTransformer:
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
        """Embed a single text with the given prefix.

        Results are cached (LRU, 256 entries) so repeated calls with the
        same *text* and *prefix* skip the model entirely.
        """
        key = (text, prefix)
        with self._cache_lock:
            if key in self._cache:
                self._cache_hits += 1
                self._cache.move_to_end(key)
                return self._cache[key]

        # Cache miss -- run the model (outside the cache lock so other
        # threads can still read cached values concurrently).
        model = self._ensure_model()
        prefixed = f"{prefix}: {text}" if prefix else text
        vec = model.encode([prefixed], normalize_embeddings=True)
        result = vec[0].tolist()

        with self._cache_lock:
            self._cache_misses += 1
            self._cache[key] = result
            self._cache.move_to_end(key)
            # Evict oldest entry if over capacity.
            while len(self._cache) > self._CACHE_MAXSIZE:
                self._cache.popitem(last=False)

        return result

    def embed_query(self, query: str) -> list[float]:
        """Embed a query (uses search_query prefix)."""
        return self.embed(query, prefix="search_query")

    def embed_batch(self, texts: list[str], prefix: str = "search_document") -> list[list[float]]:
        """Embed a batch of texts."""
        model = self._ensure_model()
        prefixed = [f"{prefix}: {t}" if prefix else t for t in texts]
        vecs = model.encode(prefixed, normalize_embeddings=True)
        return [v.tolist() for v in vecs]

    # -- Cache management --------------------------------------------------

    def clear_cache(self) -> None:
        """Drop all cached embeddings and reset hit/miss counters."""
        with self._cache_lock:
            self._cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0

    def cache_info(self) -> CacheInfo:
        """Return current cache statistics (thread-safe snapshot)."""
        with self._cache_lock:
            return CacheInfo(
                hits=self._cache_hits,
                misses=self._cache_misses,
                size=len(self._cache),
                maxsize=self._CACHE_MAXSIZE,
            )
