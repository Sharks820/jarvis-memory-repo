"""Tests for TripoTool -- tripo3d SDK wrapper with approval gate.

All tests mock the tripo3d SDK (no real API calls).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(tmp_path: Path) -> Any:
    """Import and instantiate TripoTool with a temp output dir."""
    from jarvis_engine.agent.tools.tripo_tool import TripoTool

    return TripoTool(output_dir=tmp_path, api_key="test-key-123")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Task spec
# ---------------------------------------------------------------------------


class TestTripoToolSpec:
    def test_get_tool_spec_name(self, tmp_path: Path) -> None:
        """get_tool_spec() returns ToolSpec with name='tripo'."""
        tool = _make_tool(tmp_path)
        spec = tool.get_tool_spec()
        assert spec.name == "tripo"

    def test_get_tool_spec_requires_approval(self, tmp_path: Path) -> None:
        """get_tool_spec() sets requires_approval=True (every call costs credits)."""
        tool = _make_tool(tmp_path)
        spec = tool.get_tool_spec()
        assert spec.requires_approval is True

    def test_estimate_cost_nonzero(self, tmp_path: Path) -> None:
        """estimate_cost() returns >0 so the ApprovalGate is triggered."""
        tool = _make_tool(tmp_path)
        spec = tool.get_tool_spec()
        cost = spec.estimate_cost(prompt="a wooden crate")
        assert cost > 0

    def test_parameters_schema(self, tmp_path: Path) -> None:
        """parameters schema includes 'prompt' as required and optional 'format'/'image_path'."""
        tool = _make_tool(tmp_path)
        spec = tool.get_tool_spec()
        params = spec.parameters
        assert params["type"] == "object"
        assert "prompt" in params["properties"]
        assert "format" in params["properties"]
        assert "image_path" in params["properties"]
        assert "prompt" in params["required"]


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestTripoToolValidate:
    def test_validate_valid_prompt(self, tmp_path: Path) -> None:
        """validate() returns True for a non-empty prompt."""
        tool = _make_tool(tmp_path)
        spec = tool.get_tool_spec()
        assert spec.validate(prompt="a wooden crate") is True

    def test_validate_empty_prompt(self, tmp_path: Path) -> None:
        """validate() returns False for empty string prompt."""
        tool = _make_tool(tmp_path)
        spec = tool.get_tool_spec()
        assert spec.validate(prompt="") is False

    def test_validate_missing_prompt(self, tmp_path: Path) -> None:
        """validate() returns False when prompt kwarg is absent."""
        tool = _make_tool(tmp_path)
        spec = tool.get_tool_spec()
        assert spec.validate() is False


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def _make_mock_client(task_id: str = "task-abc", downloaded_path: str = "model.fbx") -> MagicMock:
    """Build a mock TripoClient context manager."""
    task = MagicMock()
    task.task_id = task_id

    result = MagicMock()
    result.task_id = task_id

    client = MagicMock()
    client.text_to_model = AsyncMock(return_value=task)
    client.image_to_model = AsyncMock(return_value=task)
    client.wait_for_task = AsyncMock(return_value=result)
    client.download_task_models = AsyncMock(return_value=[downloaded_path])

    # Support async context manager protocol
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    return cm


class TestTripoToolExecute:
    def test_execute_text_to_model(self, tmp_path: Path) -> None:
        """execute(prompt=...) calls TripoClient.text_to_model and returns dict."""
        tool = _make_tool(tmp_path)
        mock_cm = _make_mock_client(task_id="task-001")

        with patch("jarvis_engine.agent.tools.tripo_tool.TripoClient", return_value=mock_cm):
            result = _run(tool.execute(prompt="a wooden crate"))

        assert result["task_id"] == "task-001"
        assert result["format"] == "fbx"
        assert "model_path" in result

    def test_execute_glb_format(self, tmp_path: Path) -> None:
        """execute with format='glb' downloads GLB and returns format='glb' in result."""
        tool = _make_tool(tmp_path)
        mock_cm = _make_mock_client(task_id="task-002")

        with patch("jarvis_engine.agent.tools.tripo_tool.TripoClient", return_value=mock_cm):
            result = _run(tool.execute(prompt="a sword", format="glb"))

        assert result["format"] == "glb"
        assert result["task_id"] == "task-002"

    def test_execute_returns_required_keys(self, tmp_path: Path) -> None:
        """execute() result dict has model_path, format, task_id keys."""
        tool = _make_tool(tmp_path)
        mock_cm = _make_mock_client(task_id="task-003")

        with patch("jarvis_engine.agent.tools.tripo_tool.TripoClient", return_value=mock_cm):
            result = _run(tool.execute(prompt="a medieval castle"))

        assert set(result.keys()) >= {"model_path", "format", "task_id"}

    def test_execute_missing_api_key_raises(self, tmp_path: Path) -> None:
        """execute() raises ValueError when TRIPO_API_KEY is not set and no api_key given."""
        from jarvis_engine.agent.tools.tripo_tool import TripoTool

        tool = TripoTool(output_dir=tmp_path)  # no api_key arg

        with patch.dict(os.environ, {}, clear=True):
            # Remove TRIPO_API_KEY if present
            env_without_key = {k: v for k, v in os.environ.items() if k != "TRIPO_API_KEY"}
            with patch.dict(os.environ, env_without_key, clear=True):
                with pytest.raises(ValueError, match="TRIPO_API_KEY"):
                    _run(tool.execute(prompt="a test"))

    def test_execute_image_to_model(self, tmp_path: Path) -> None:
        """execute with image_path calls TripoClient.image_to_model instead of text_to_model."""
        tool = _make_tool(tmp_path)
        mock_cm = _make_mock_client(task_id="task-004")

        with patch("jarvis_engine.agent.tools.tripo_tool.TripoClient", return_value=mock_cm):
            result = _run(tool.execute(prompt="from image", image_path="input.png"))

        assert result["task_id"] == "task-004"
        mock_cm.__aenter__.return_value.image_to_model.assert_called_once()
        mock_cm.__aenter__.return_value.text_to_model.assert_not_called()

    def test_execute_sdk_error_raised_as_runtime_error(self, tmp_path: Path) -> None:
        """SDK errors (network, auth) are caught and re-raised as RuntimeError."""
        tool = _make_tool(tmp_path)
        mock_cm = _make_mock_client()
        mock_cm.__aenter__.return_value.text_to_model = AsyncMock(
            side_effect=Exception("connection refused")
        )

        with patch("jarvis_engine.agent.tools.tripo_tool.TripoClient", return_value=mock_cm):
            with pytest.raises(RuntimeError, match="connection refused"):
                _run(tool.execute(prompt="a test"))
