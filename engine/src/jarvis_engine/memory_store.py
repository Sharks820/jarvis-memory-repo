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

        recent: collections.deque[str] = collections.deque(maxlen=limit)
        with self._events_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    recent.append(line)

        events: list[MemoryEvent] = []
        for line in recent:
            try:
                events.append(MemoryEvent(**json.loads(line)))
            except json.JSONDecodeError:
                continue
        return events
