"""Tests for ProgressEventBus.

Uses asyncio.run() pattern (no pytest-asyncio) to match project convention.
"""
from __future__ import annotations

import asyncio

import pytest


def _make_bus(max_size: int = 256):
    from jarvis_engine.agent.progress_bus import ProgressEventBus

    return ProgressEventBus(max_size=max_size)


class TestProgressEventBus:
    def test_subscribe_returns_queue(self) -> None:
        bus = _make_bus()

        q = bus.subscribe()

        assert isinstance(q, asyncio.Queue)

    def test_emit_delivers_to_subscriber(self) -> None:
        bus = _make_bus()
        q = bus.subscribe()
        event = {"type": "step", "data": "hello"}

        asyncio.run(bus.emit(event))

        received = q.get_nowait()
        assert received == event

    def test_emit_delivers_to_multiple_subscribers(self) -> None:
        bus = _make_bus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        event = {"type": "step", "data": "broadcast"}

        asyncio.run(bus.emit(event))

        assert q1.get_nowait() == event
        assert q2.get_nowait() == event

    def test_unsubscribe_stops_delivery(self) -> None:
        bus = _make_bus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        event = {"type": "step"}

        asyncio.run(bus.emit(event))

        assert q.empty()

    def test_emit_drops_oldest_when_queue_full(self) -> None:
        bus = _make_bus(max_size=2)
        q = bus.subscribe()

        # Fill the queue
        asyncio.run(bus.emit({"type": "first"}))
        asyncio.run(bus.emit({"type": "second"}))
        # This should drop the oldest and add the new one
        asyncio.run(bus.emit({"type": "third"}))

        items = []
        while not q.empty():
            items.append(q.get_nowait())

        assert len(items) == 2
        assert items[-1]["type"] == "third"

    def test_multiple_emits_in_order(self) -> None:
        bus = _make_bus()
        q = bus.subscribe()

        async def run() -> None:
            await bus.emit({"seq": 1})
            await bus.emit({"seq": 2})
            await bus.emit({"seq": 3})

        asyncio.run(run())

        assert q.get_nowait()["seq"] == 1
        assert q.get_nowait()["seq"] == 2
        assert q.get_nowait()["seq"] == 3

    def test_no_subscribers_emit_is_noop(self) -> None:
        bus = _make_bus()
        # Should not raise
        asyncio.run(bus.emit({"type": "orphan"}))

    def test_empty_queue_after_unsubscribe(self) -> None:
        bus = _make_bus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)

        asyncio.run(bus.emit({"type": "only-q2"}))

        assert q1.empty()
        assert not q2.empty()


class TestGetProgressBus:
    def test_get_progress_bus_returns_same_instance(self) -> None:
        from jarvis_engine.agent.progress_bus import get_progress_bus

        b1 = get_progress_bus()
        b2 = get_progress_bus()

        assert b1 is b2

    def test_get_progress_bus_returns_progress_event_bus(self) -> None:
        from jarvis_engine.agent.progress_bus import ProgressEventBus, get_progress_bus

        bus = get_progress_bus()

        assert isinstance(bus, ProgressEventBus)
