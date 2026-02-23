"""Enriched ingestion pipeline with chunking, embedding, and semantic classification.

Pipeline steps: sanitize -> deduplicate (SHA-256) -> chunk (>2000 chars) ->
embed -> classify branch -> write SQLite via MemoryEngine.

This is SEPARATE from engine/src/jarvis_engine/ingest.py (the old pipeline).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.memory.classify import BranchClassifier
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

# Patterns for credential redaction (matches password, token, secret, api_key, etc.)
_CREDENTIAL_PATTERNS = [
    re.compile(r"(password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(token|api[_-]?key|secret|signing[_-]?key)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(bearer)\s+\S+", re.IGNORECASE),
]


class EnrichedIngestPipeline:
    """Enriched memory ingestion with chunking, embedding, and branch classification."""

    def __init__(
        self,
        engine: "MemoryEngine",
        embed_service: "EmbeddingService",
        classifier: "BranchClassifier",
    ) -> None:
        self._engine = engine
        self._embed_service = embed_service
        self._classifier = classifier

    def ingest(
        self,
        source: str,
        kind: str,
        task_id: str,
        content: str,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Ingest content through the enriched pipeline.

        Pipeline: sanitize -> dedup -> chunk -> embed -> classify -> store.

        Args:
            source: Origin of content (user, claude, task_outcome, etc.).
            kind: Memory kind (episodic, semantic, procedural).
            task_id: Task identifier.
            content: Raw content text.
            tags: Optional tags list.

        Returns:
            List of inserted record IDs (empty if all duplicates).
        """
        # Step 1: Sanitize
        sanitized = self._sanitize(content)
        if not sanitized:
            return []

        # Step 2: Pre-flight dedup for short content
        full_content_hash = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        chunks = self._chunk_content(sanitized)
        if len(chunks) == 1:
            # Single chunk -- check full content hash for quick dedup
            existing = self._engine.get_record_by_hash(full_content_hash)
            if existing is not None:
                return []

        # Step 3 & 4: Process each chunk
        ts = datetime.now(UTC).isoformat()
        tag_str = str(sorted({t.lower() for t in (tags or []) if t.strip()})[:10])
        inserted_ids: list[str] = []

        for chunk in chunks:
            # 4a: Per-chunk content hash (CRITICAL: hash chunk text, NOT full document)
            chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()

            # 4b: Generate record_id -- 32 hex chars (Codex finding: >16 to avoid collisions)
            id_material = f"{source}|{kind}|{task_id}|{chunk}".encode("utf-8")
            record_id = hashlib.sha256(id_material).hexdigest()[:32]

            # 4c: Generate embedding
            embedding = self._embed_service.embed(chunk, prefix="search_document")

            # 4d: Classify branch
            branch = self._classifier.classify(embedding)

            # 4e: Build record dict
            record = {
                "record_id": record_id,
                "ts": ts,
                "source": source,
                "kind": kind,
                "task_id": task_id,
                "branch": branch,
                "tags": tag_str,
                "summary": chunk[:200],
                "content_hash": chunk_hash,
                "confidence": 0.72,
                "tier": "warm",
                "access_count": 0,
                "last_accessed": "",
            }

            # 4f: Insert via engine (UNIQUE constraint on content_hash handles dedup)
            was_inserted = self._engine.insert_record(record, embedding=embedding)
            if was_inserted:
                inserted_ids.append(record_id)

        return inserted_ids

    def _sanitize(self, content: str) -> str:
        """Sanitize content: strip, truncate, redact credentials."""
        text = content.strip()
        if not text:
            return ""
        # Truncate to 10000 chars
        text = text[:10000]
        # Redact credential patterns
        for pattern in _CREDENTIAL_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    def _chunk_content(self, content: str, max_chunk: int = 1500) -> list[str]:
        """Split content into chunks at sentence boundaries.

        If content is short enough (within 20% of max_chunk), return as single chunk.
        Otherwise split on sentence boundaries: '. ', '.\\n', '\\n\\n'.
        """
        if len(content) <= int(max_chunk * 1.2):
            return [content]

        # Split on sentence boundaries
        # First try paragraph breaks, then sentence ends
        sentences: list[str] = []
        # Split on double newlines first
        paragraphs = re.split(r"\n\n+", content)
        for para in paragraphs:
            # Split paragraphs on sentence-ending patterns
            parts = re.split(r"(?<=\. )", para)
            for part in parts:
                stripped = part.strip()
                if stripped:
                    sentences.append(stripped)

        if not sentences:
            # Fallback: just return the content as-is
            return [content]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0

        for sentence in sentences:
            sentence_len = len(sentence)
            # If adding this sentence would exceed max_chunk, start a new chunk
            if current_len + sentence_len + 1 > max_chunk and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(sentence)
            current_len += sentence_len + 1  # +1 for space

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks if chunks else [content]
