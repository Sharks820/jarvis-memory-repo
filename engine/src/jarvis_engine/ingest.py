from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, asdict
from jarvis_engine._shared import now_iso as _now_iso, sha256_short
from typing import Literal

from jarvis_engine.memory_store import MemoryStore

logger = logging.getLogger(__name__)

SourceType = Literal["user", "claude", "opus", "gemini", "task_outcome"]
MemoryKind = Literal["episodic", "semantic", "procedural"]

_VALID_SOURCES = {"user", "claude", "opus", "gemini", "task_outcome"}
_VALID_KINDS = {"episodic", "semantic", "procedural"}


@dataclass
class IngestRecord:
    record_id: str
    ts: str
    source: SourceType
    kind: MemoryKind
    task_id: str
    content: str
    deduplicated: bool = False


class IngestionPipeline:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        # Content-hash deduplication: tracks recently ingested content hashes
        # to prevent duplicate records when identical content is submitted.
        self._seen_hashes: dict[str, str] = {}  # content_hash -> record_id
        self._seen_lock = threading.Lock()

    def _content_hash(self, source: str, kind: str, task_id: str, content: str) -> str:
        """Compute a deterministic content hash for deduplication.

        Uses source, kind, task_id, and content (but NOT timestamp) so that
        identical submissions produce the same hash regardless of timing.
        """
        material = f"{source}|{kind}|{task_id}|{content}".encode("utf-8")
        return sha256_short(material)

    def ingest(self, source: SourceType, kind: MemoryKind, task_id: str, content: str) -> IngestRecord:
        # Validate source and kind against allowed literals
        if source not in _VALID_SOURCES:
            raise ValueError(f"Invalid source: {source!r}. Must be one of {_VALID_SOURCES}")
        if kind not in _VALID_KINDS:
            raise ValueError(f"Invalid kind: {kind!r}. Must be one of {_VALID_KINDS}")

        # Content-hash deduplication: check if identical content was already ingested
        content_hash = self._content_hash(source, kind, task_id, content)
        with self._seen_lock:
            if content_hash in self._seen_hashes:
                existing_id = self._seen_hashes[content_hash]
                logger.debug("Deduplicated ingest: content_hash=%s existing_id=%s", content_hash, existing_id)
                return IngestRecord(
                    record_id=existing_id,
                    ts=_now_iso(),
                    source=source,
                    kind=kind,
                    task_id=task_id,
                    content=content,
                    deduplicated=True,
                )

        ts = _now_iso()
        # Use content hash as record_id for deterministic deduplication
        record_id = content_hash
        rec = IngestRecord(
            record_id=record_id,
            ts=ts,
            source=source,
            kind=kind,
            task_id=task_id,
            content=content,
        )
        log_record = asdict(rec)
        log_record["content"] = self._redacted_content(content)
        self.store.append(
            event_type=f"ingest:{source}:{kind}",
            message=json.dumps(log_record, ensure_ascii=True),
        )

        # Record the content hash for future deduplication
        with self._seen_lock:
            self._seen_hashes[content_hash] = record_id
            # Cap the dedup cache at 50k entries to prevent unbounded memory growth
            if len(self._seen_hashes) > 50_000:
                # Evict oldest half (dict preserves insertion order in Python 3.7+)
                keys = list(self._seen_hashes.keys())
                for k in keys[:25_000]:
                    del self._seen_hashes[k]

        return rec

    def _redacted_content(self, content: str) -> str:
        preview = content[:200]
        suffix = "" if len(content) <= 200 else "...(truncated)"
        return preview + suffix
