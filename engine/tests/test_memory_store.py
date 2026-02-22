from __future__ import annotations

import json

from jarvis_engine.memory_store import MemoryStore


def test_tail_returns_recent_events(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    for i in range(10):
        store.append(event_type=f"evt-{i}", message=f"msg-{i}")

    tail = list(store.tail(3))
    assert len(tail) == 3
    assert [e.event_type for e in tail] == ["evt-7", "evt-8", "evt-9"]


def test_tail_skips_corrupt_lines(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append(event_type="ok-1", message="m1")

    events_path = tmp_path / ".planning" / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as f:
        f.write("{not json}\n")

    store.append(event_type="ok-2", message="m2")
    tail = list(store.tail(5))
    assert [e.event_type for e in tail] == ["ok-1", "ok-2"]

    # File still contains corrupt line to prove parser tolerance only in reader.
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert any(line == "{not json}" for line in lines)
    for line in lines:
        if line != "{not json}":
            json.loads(line)


def test_tail_zero_or_negative_limit_returns_empty(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append(event_type="evt", message="msg")
    assert list(store.tail(0)) == []
    assert list(store.tail(-5)) == []

