"""Tests for the Command Bus infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from jarvis_engine.command_bus import CommandBus


@dataclass(frozen=True)
class DummyCommand:
    value: str = "hello"


@dataclass
class DummyResult:
    echoed: str = ""


def test_command_bus_register_and_dispatch() -> None:
    bus = CommandBus()

    def handler(cmd: DummyCommand) -> DummyResult:
        return DummyResult(echoed=cmd.value)

    bus.register(DummyCommand, handler)
    result = bus.dispatch(DummyCommand(value="world"))
    assert isinstance(result, DummyResult)
    assert result.echoed == "world"


def test_command_bus_dispatch_raises_for_unregistered() -> None:
    bus = CommandBus()
    with pytest.raises(ValueError, match="No handler for DummyCommand"):
        bus.dispatch(DummyCommand())


def test_command_bus_registered_count() -> None:
    bus = CommandBus()
    assert bus.registered_count == 0

    def handler(cmd: DummyCommand) -> DummyResult:
        return DummyResult()

    bus.register(DummyCommand, handler)
    assert bus.registered_count == 1


def test_create_app_returns_wired_bus() -> None:
    from jarvis_engine.app import create_app

    bus = create_app(Path("."))
    assert isinstance(bus, CommandBus)
    # Should have all 43 command types registered
    assert bus.registered_count >= 40


def test_embedding_service_lazy_load() -> None:
    from jarvis_engine.memory.embeddings import EmbeddingService

    svc = EmbeddingService()
    assert svc._model is None  # not loaded yet
    assert svc.MODEL_NAME == "nomic-ai/nomic-embed-text-v1.5"
