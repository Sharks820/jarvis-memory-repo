from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from typing import Literal

from jarvis_engine.memory_store import MemoryStore

SourceType = Literal["user", "claude", "opus", "gemini", "task_outcome"]
MemoryKind = Literal["episodic", "semantic", "procedural"]


@dataclass
class IngestRecord:
    record_id: str
    ts: str
    source: SourceType
    kind: MemoryKind
    task_id: str
    content: str


class IngestionPipeline:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def ingest(self, source: SourceType, kind: MemoryKind, task_id: str, content: str) -> IngestRecord:
        ts = datetime.now(UTC).isoformat()
        material = f"{source}|{kind}|{task_id}|{content}|{ts}".encode("utf-8")
        record_id = hashlib.sha256(material).hexdigest()[:32]
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
        return rec

    def _redacted_content(self, content: str) -> str:
        preview = content[:96]
        suffix = "" if len(content) <= 96 else "...(truncated)"
        return preview + suffix
