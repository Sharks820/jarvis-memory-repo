from __future__ import annotations

import json
import threading

from jarvis_engine.memory_store import MemoryEvent, MemoryStore


# ── existing tests ────────────────────────────────────────────────────────


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


# ── append tests ──────────────────────────────────────────────────────────


def test_append_creates_file_on_first_write(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    events_path = tmp_path / ".planning" / "events.jsonl"
    assert not events_path.exists()
    event = store.append(event_type="init", message="hello")
    assert events_path.exists()
    assert isinstance(event, MemoryEvent)
    assert event.event_type == "init"
    assert event.message == "hello"
    assert event.ts  # non-empty timestamp


def test_append_returns_memory_event_with_correct_fields(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    event = store.append(event_type="test-type", message="test-msg")
    assert event.event_type == "test-type"
    assert event.message == "test-msg"
    assert isinstance(event.ts, str)
    assert len(event.ts) > 0


def test_append_writes_valid_json_lines(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append(event_type="a", message="msg-a")
    store.append(event_type="b", message="msg-b")
    events_path = tmp_path / ".planning" / "events.jsonl"
    lines = [
        line
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert "ts" in parsed
        assert "event_type" in parsed
        assert "message" in parsed


def test_append_handles_unicode_and_special_chars(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append(
        event_type="unicode",
        message="Emoji: \u2615 CJK: \u4e16\u754c Newline: \\n Tab: \\t",
    )
    tail = list(store.tail(1))
    assert len(tail) == 1
    assert "Emoji" in tail[0].message


def test_append_concurrent_writes_are_serialized(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    errors: list[Exception] = []

    def writer(start: int) -> None:
        try:
            for i in range(20):
                store.append(event_type=f"thread-{start}", message=f"msg-{i}")
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    events_path = tmp_path / ".planning" / "events.jsonl"
    lines = [
        line
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 80  # 4 threads * 20 writes each
    for line in lines:
        json.loads(line)  # All should be valid JSON


def test_append_multiple_stores_on_same_root(tmp_path) -> None:
    store1 = MemoryStore(tmp_path)
    store2 = MemoryStore(tmp_path)
    store1.append(event_type="s1", message="from store1")
    store2.append(event_type="s2", message="from store2")
    # Both instances operate on the same file
    events_path = tmp_path / ".planning" / "events.jsonl"
    lines = [
        line
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 2


# ── tail edge case tests ─────────────────────────────────────────────────


def test_tail_empty_file(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    events_path = tmp_path / ".planning" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    result = list(store.tail(5))
    assert result == []


def test_tail_request_more_than_exist(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.append(event_type="only", message="one event")
    tail = list(store.tail(100))
    assert len(tail) == 1
    assert tail[0].event_type == "only"


def test_tail_no_file_returns_empty(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    result = list(store.tail(5))
    assert result == []


def test_tail_with_very_long_lines(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    long_msg = "x" * 50000
    store.append(event_type="long", message=long_msg)
    tail = list(store.tail(1))
    assert len(tail) == 1
    assert tail[0].message == long_msg


def test_tail_with_only_corrupt_lines(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    events_path = tmp_path / ".planning" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("not json 1\nnot json 2\n", encoding="utf-8")
    result = list(store.tail(5))
    assert result == []


def test_tail_limit_of_one(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    for i in range(5):
        store.append(event_type=f"e{i}", message=f"m{i}")
    tail = list(store.tail(1))
    assert len(tail) == 1
    assert tail[0].event_type == "e4"


def test_memory_store_creates_planning_dir(tmp_path) -> None:
    """Constructing MemoryStore should create the .planning directory."""
    planning_dir = tmp_path / ".planning"
    assert not planning_dir.exists()
    MemoryStore(tmp_path)
    assert planning_dir.exists()
