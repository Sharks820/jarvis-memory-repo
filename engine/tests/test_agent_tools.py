"""Tests for agent tools: FileTool, ShellTool, WebTool.

All tests use asyncio.run() pattern (no pytest-asyncio) to match project convention.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# FileTool tests
# ---------------------------------------------------------------------------


class TestFileTool:
    def setup_method(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._project_dir = Path(self._tmpdir)

    def _make_tool(self):
        from jarvis_engine.agent.tools.file_tool import FileTool

        return FileTool(project_dir=self._project_dir)

    def test_read_file_returns_contents(self) -> None:
        tool = self._make_tool()
        target = self._project_dir / "hello.txt"
        target.write_text("world", encoding="utf-8")

        result = asyncio.run(tool.read_file("hello.txt"))

        assert result == "world"

    def test_write_file_creates_file(self) -> None:
        tool = self._make_tool()

        asyncio.run(tool.write_file("output.txt", "hello"))

        assert (self._project_dir / "output.txt").read_text() == "hello"

    def test_write_file_returns_confirmation(self) -> None:
        tool = self._make_tool()

        result = asyncio.run(tool.write_file("out.txt", "data"))

        assert isinstance(result, str)
        assert len(result) > 0

    def test_write_file_creates_parent_dirs(self) -> None:
        tool = self._make_tool()

        asyncio.run(tool.write_file("subdir/nested/file.txt", "content"))

        assert (self._project_dir / "subdir" / "nested" / "file.txt").read_text() == "content"

    def test_read_file_path_traversal_raises(self) -> None:
        tool = self._make_tool()

        with pytest.raises(PermissionError):
            asyncio.run(tool.read_file("../outside.txt"))

    def test_write_file_path_traversal_raises(self) -> None:
        tool = self._make_tool()

        with pytest.raises(PermissionError):
            asyncio.run(tool.write_file("../evil.txt", "bad"))

    def test_read_file_absolute_outside_raises(self) -> None:
        tool = self._make_tool()
        # Try an absolute path outside project dir
        outside = tempfile.mkdtemp()
        outside_file = os.path.join(outside, "secret.txt")
        with open(outside_file, "w") as f:
            f.write("secret")

        with pytest.raises(PermissionError):
            asyncio.run(tool.read_file(outside_file))

    def test_get_tool_spec_name_is_file(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert spec.name == "file"

    def test_get_tool_spec_requires_approval_false(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert spec.requires_approval is False

    def test_get_tool_spec_has_execute(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert callable(spec.execute)

    def test_get_tool_spec_parameters_schema(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert "properties" in spec.parameters
        props = spec.parameters["properties"]
        assert "action" in props
        assert "path" in props


# ---------------------------------------------------------------------------
# ShellTool tests
# ---------------------------------------------------------------------------


class TestShellTool:
    def _make_tool(self, timeout: int = 30):
        from jarvis_engine.agent.tools.shell_tool import ShellTool

        return ShellTool(timeout=timeout)

    def test_execute_returns_stdout(self) -> None:
        tool = self._make_tool()
        result = asyncio.run(tool.execute("echo hello"))

        assert "hello" in result["stdout"]
        assert result["returncode"] == 0

    def test_execute_returns_dict_with_expected_keys(self) -> None:
        tool = self._make_tool()
        result = asyncio.run(tool.execute("echo test"))

        assert "stdout" in result
        assert "stderr" in result
        assert "returncode" in result

    def test_execute_timeout_raises(self) -> None:
        tool = self._make_tool(timeout=1)

        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            asyncio.run(tool.execute("python -c \"import time; time.sleep(10)\""))

    def test_execute_blocklist_rm_rf(self) -> None:
        tool = self._make_tool()

        with pytest.raises(PermissionError):
            asyncio.run(tool.execute("rm -rf /"))

    def test_execute_blocklist_format(self) -> None:
        tool = self._make_tool()

        with pytest.raises(PermissionError):
            asyncio.run(tool.execute("format c:"))

    def test_execute_blocklist_del_s(self) -> None:
        tool = self._make_tool()

        with pytest.raises(PermissionError):
            asyncio.run(tool.execute("del /s important_file"))

    def test_execute_blocklist_fork_bomb(self) -> None:
        tool = self._make_tool()

        with pytest.raises(PermissionError):
            asyncio.run(tool.execute(":(){:|:&};:"))

    def test_execute_nonzero_returncode(self) -> None:
        tool = self._make_tool()
        result = asyncio.run(tool.execute("python -c \"import sys; sys.exit(1)\""))

        assert result["returncode"] != 0

    def test_get_tool_spec_name_is_shell(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert spec.name == "shell"

    def test_get_tool_spec_requires_approval_true(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert spec.requires_approval is True

    def test_get_tool_spec_is_destructive_true(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert spec.is_destructive is True


# ---------------------------------------------------------------------------
# WebTool tests
# ---------------------------------------------------------------------------


class TestWebTool:
    def _make_tool(self):
        from jarvis_engine.agent.tools.web_tool import WebTool

        return WebTool()

    def test_execute_returns_string(self) -> None:
        tool = self._make_tool()
        fake_content = "This is fetched page content with enough text to be useful " * 5

        with patch("jarvis_engine.agent.tools.web_tool.fetch_page_text") as mock_fetch:
            mock_fetch.return_value = fake_content
            result = asyncio.run(tool.execute("https://example.com"))

        assert isinstance(result, str)
        assert result == fake_content

    def test_execute_truncates_to_10000_chars(self) -> None:
        tool = self._make_tool()
        long_content = "x" * 20000

        with patch("jarvis_engine.agent.tools.web_tool.fetch_page_text") as mock_fetch:
            mock_fetch.return_value = long_content
            result = asyncio.run(tool.execute("https://example.com"))

        assert len(result) <= 10000

    def test_execute_calls_fetch_with_url(self) -> None:
        tool = self._make_tool()

        with patch("jarvis_engine.agent.tools.web_tool.fetch_page_text") as mock_fetch:
            mock_fetch.return_value = "some content"
            asyncio.run(tool.execute("https://example.com/page"))

        mock_fetch.assert_called_once()
        call_args = mock_fetch.call_args
        assert "https://example.com/page" in str(call_args)

    def test_execute_returns_empty_on_fetch_failure(self) -> None:
        tool = self._make_tool()

        with patch("jarvis_engine.agent.tools.web_tool.fetch_page_text") as mock_fetch:
            mock_fetch.return_value = ""
            result = asyncio.run(tool.execute("https://example.com"))

        assert result == ""

    def test_get_tool_spec_name_is_web(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert spec.name == "web"

    def test_get_tool_spec_requires_approval_false(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert spec.requires_approval is False

    def test_get_tool_spec_has_execute(self) -> None:
        tool = self._make_tool()
        spec = tool.get_tool_spec()

        assert callable(spec.execute)
