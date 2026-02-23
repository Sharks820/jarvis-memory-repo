from __future__ import annotations

import collections
import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Iterable


@dataclass
class MemoryEvent:
    ts: str
    event_type: str
    message: str


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self._dir = root / ".planning"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self._dir / "events.jsonl"
        self._lock = threading.Lock()

    def append(self, event_type: str, message: str) -> MemoryEvent:
        event = MemoryEvent(
            ts=datetime.now(UTC).isoformat(),
            event_type=event_type,
            message=message,
        )
        line = json.dumps(asdict(event), ensure_ascii=True) + "\n"
        with self._lock:
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(line)
        return event

    def tail(self, limit: int = 5) -> Iterable[MemoryEvent]:
        if limit <= 0 or not self._events_path.exists():
            return []

        # Reverse-seek from end of file to avoid loading entire JSONL
        lines: list[str] = []
        try:
            with self._events_path.open("rb") as f:
                f.seek(0, 2)  # Seek to end
                file_size = f.tell()
                if file_size == 0:
                    return []
                # Read in chunks from end; 8KB per line is generous
                chunk_size = min(file_size, max(8192 * limit, 65536))
                f.seek(max(0, file_size - chunk_size))
                tail_bytes = f.read()
            for raw_line in tail_bytes.decode("utf-8", errors="replace").splitlines():
                stripped = raw_line.strip()
                if stripped:
                    lines.append(stripped)
            lines = lines[-limit:]
        except OSError:
            return []

        events: list[MemoryEvent] = []
        for line in lines:
            try:
                events.append(MemoryEvent(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return events
