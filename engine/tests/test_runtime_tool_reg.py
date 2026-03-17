"""Tests for runtime tool registration via CQRS command.

Tests AgentRegisterToolCommand handler:
  - Registers a tool in ToolRegistry at runtime
  - Returns rc=0, registered=True, tool_name echoed
  - Duplicate registration overwrites with log warning
  - Empty name returns rc=1, registered=False
  - JSON parse error on parameters returns rc=1
  - ToolRegistry.unregister() returns True when found, False when not
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry():
    from jarvis_engine.agent.tool_registry import ToolRegistry
    return ToolRegistry()


def _make_handler(registry=None):
    from jarvis_engine.handlers.agent_handlers import AgentRegisterToolHandler
    if registry is None:
        registry = _make_registry()
    return AgentRegisterToolHandler(Path("/tmp"), registry=registry)


# ---------------------------------------------------------------------------
# AgentRegisterToolCommand / AgentRegisterToolResult dataclass tests
# ---------------------------------------------------------------------------


class TestAgentRegisterToolCommandDataclass:
    def test_defaults(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand
        cmd = AgentRegisterToolCommand()
        assert cmd.name == ""
        assert cmd.description == ""
        assert cmd.parameters == "{}"
        assert cmd.requires_approval is False

    def test_frozen(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand
        cmd = AgentRegisterToolCommand(name="test")
        with pytest.raises((AttributeError, TypeError)):
            cmd.name = "other"  # type: ignore[misc]

    def test_custom_values(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand
        cmd = AgentRegisterToolCommand(
            name="mixamo",
            description="Mixamo for animations",
            parameters='{"url": "string"}',
            requires_approval=True,
        )
        assert cmd.name == "mixamo"
        assert cmd.requires_approval is True


class TestAgentRegisterToolResultDataclass:
    def test_defaults(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolResult
        res = AgentRegisterToolResult()
        assert res.return_code == 0
        assert res.tool_name == ""
        assert res.registered is False

    def test_success_result(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolResult
        res = AgentRegisterToolResult(return_code=0, tool_name="mixamo", registered=True)
        assert res.registered is True
        assert res.tool_name == "mixamo"


# ---------------------------------------------------------------------------
# AgentRegisterToolHandler tests
# ---------------------------------------------------------------------------


class TestAgentRegisterToolHandler:
    def test_registers_tool_success(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        registry = _make_registry()
        handler = _make_handler(registry)
        cmd = AgentRegisterToolCommand(
            name="mixamo",
            description="Mixamo for animations",
        )
        result = handler.handle(cmd)

        assert result.return_code == 0
        assert result.registered is True
        assert result.tool_name == "mixamo"
        # Tool should be in registry
        spec = registry.get("mixamo")
        assert spec is not None
        assert spec.name == "mixamo"
        assert spec.description == "Mixamo for animations"

    def test_placeholder_execute_returns_string(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        registry = _make_registry()
        handler = _make_handler(registry)
        cmd = AgentRegisterToolCommand(name="mytool", description="test tool")
        handler.handle(cmd)

        spec = registry.get("mytool")
        assert spec is not None
        # The placeholder execute is a sync callable returning a placeholder string
        # (runtime-registered tools may be async or sync; test callable returns string)
        import asyncio
        result = spec.execute()
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        assert "mytool" in str(result)
        assert "runtime-registered" in str(result).lower() or "invoked" in str(result).lower()

    def test_empty_name_returns_rc1(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        handler = _make_handler()
        cmd = AgentRegisterToolCommand(name="", description="test")
        result = handler.handle(cmd)

        assert result.return_code == 1
        assert result.registered is False

    def test_invalid_json_parameters_returns_rc1(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        handler = _make_handler()
        cmd = AgentRegisterToolCommand(name="tool", description="d", parameters="not-json{")
        result = handler.handle(cmd)

        assert result.return_code == 1
        assert result.registered is False

    def test_valid_json_parameters_parsed(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        registry = _make_registry()
        handler = _make_handler(registry)
        params = json.dumps({"url": {"type": "string"}})
        cmd = AgentRegisterToolCommand(name="webtool2", description="d", parameters=params)
        result = handler.handle(cmd)

        assert result.return_code == 0
        spec = registry.get("webtool2")
        assert spec is not None
        assert spec.parameters == {"url": {"type": "string"}}

    def test_duplicate_registration_overwrites(self, caplog):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand
        import logging

        registry = _make_registry()
        handler = _make_handler(registry)

        cmd1 = AgentRegisterToolCommand(name="duptool", description="first")
        cmd2 = AgentRegisterToolCommand(name="duptool", description="second")

        result1 = handler.handle(cmd1)
        with caplog.at_level(logging.WARNING):
            result2 = handler.handle(cmd2)

        assert result1.return_code == 0
        assert result2.return_code == 0
        assert result2.registered is True
        # The second registration overwrites
        spec = registry.get("duptool")
        assert spec is not None
        assert spec.description == "second"

    def test_requires_approval_propagated(self):
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        registry = _make_registry()
        handler = _make_handler(registry)
        cmd = AgentRegisterToolCommand(
            name="dangerous",
            description="risky tool",
            requires_approval=True,
        )
        handler.handle(cmd)

        spec = registry.get("dangerous")
        assert spec is not None
        assert spec.requires_approval is True

    def test_no_registry_configured(self):
        """Handler with registry=None should fail gracefully."""
        from jarvis_engine.handlers.agent_handlers import AgentRegisterToolHandler
        from jarvis_engine.commands.agent_commands import AgentRegisterToolCommand

        handler = AgentRegisterToolHandler(Path("/tmp"), registry=None)
        cmd = AgentRegisterToolCommand(name="test")
        result = handler.handle(cmd)
        assert result.return_code == 1
        assert result.registered is False


# ---------------------------------------------------------------------------
# ToolRegistry.unregister() tests
# ---------------------------------------------------------------------------


class TestToolRegistryUnregister:
    def test_unregister_existing_returns_true(self):
        from jarvis_engine.agent.tool_registry import ToolRegistry, ToolSpec

        registry = ToolRegistry()
        spec = ToolSpec(
            name="my_tool",
            description="test",
            parameters={},
            execute=lambda: None,
        )
        registry.register(spec)
        assert registry.get("my_tool") is not None

        result = registry.unregister("my_tool")
        assert result is True
        assert registry.get("my_tool") is None

    def test_unregister_nonexistent_returns_false(self):
        from jarvis_engine.agent.tool_registry import ToolRegistry

        registry = ToolRegistry()
        result = registry.unregister("does_not_exist")
        assert result is False

    def test_unregister_reduces_count(self):
        from jarvis_engine.agent.tool_registry import ToolRegistry, ToolSpec

        registry = ToolRegistry()
        for name in ("a", "b", "c"):
            registry.register(ToolSpec(name=name, description="", parameters={}, execute=lambda: None))

        assert len(registry) == 3
        registry.unregister("b")
        assert len(registry) == 2

    def test_unregister_then_reregister(self):
        from jarvis_engine.agent.tool_registry import ToolRegistry, ToolSpec

        registry = ToolRegistry()
        registry.register(ToolSpec(name="tool", description="v1", parameters={}, execute=lambda: None))
        registry.unregister("tool")
        registry.register(ToolSpec(name="tool", description="v2", parameters={}, execute=lambda: None))

        spec = registry.get("tool")
        assert spec is not None
        assert spec.description == "v2"
