"""Tests for BlenderTool -- headless Blender subprocess tool.

All subprocess calls are mocked (no real Blender required).
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


def _make_tool(blender_path: str | None = None) -> Any:
    from jarvis_engine.agent.tools.blender_tool import BlenderTool

    return BlenderTool(blender_path=blender_path)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_proc(returncode: int = 0, stdout: bytes = b"OK", stderr: bytes = b"") -> MagicMock:
    """Build a mock asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


# ---------------------------------------------------------------------------
# ToolSpec
# ---------------------------------------------------------------------------


class TestBlenderToolSpec:
    def test_get_tool_spec_name(self) -> None:
        """get_tool_spec() returns ToolSpec with name='blender'."""
        tool = _make_tool()
        spec = tool.get_tool_spec()
        assert spec.name == "blender"

    def test_get_tool_spec_requires_approval_false(self) -> None:
        """get_tool_spec() sets requires_approval=False (Blender is free/local)."""
        tool = _make_tool()
        spec = tool.get_tool_spec()
        assert spec.requires_approval is False

    def test_parameters_schema(self) -> None:
        """parameters schema includes 'script', 'input_path', 'output_path' properties."""
        tool = _make_tool()
        spec = tool.get_tool_spec()
        params = spec.parameters
        assert params["type"] == "object"
        assert "script" in params["properties"]
        assert "input_path" in params["properties"]
        assert "output_path" in params["properties"]
        assert "script" in params["required"]


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


class TestBlenderPathDiscovery:
    def test_explicit_arg_used(self) -> None:
        """Constructor arg takes precedence over env var."""
        tool = _make_tool(blender_path="/custom/blender")
        assert tool._blender_path == "/custom/blender"

    def test_env_var_used_when_no_arg(self) -> None:
        """BLENDER_PATH env var is used when no constructor arg given."""
        with patch.dict(os.environ, {"BLENDER_PATH": "/env/blender"}):
            tool = _make_tool()
        assert tool._blender_path == "/env/blender"

    def test_default_windows_path_used(self) -> None:
        """Falls back to default Windows Blender path when nothing else set."""
        env_without = {k: v for k, v in os.environ.items() if k != "BLENDER_PATH"}
        with patch.dict(os.environ, env_without, clear=True):
            tool = _make_tool()
        assert "blender" in tool._blender_path.lower()


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestBlenderToolValidate:
    def test_validate_known_script(self) -> None:
        """validate() returns True for known script names."""
        tool = _make_tool()
        spec = tool.get_tool_spec()
        assert spec.validate(script="optimize_mesh") is True
        assert spec.validate(script="generate_lod") is True
        assert spec.validate(script="generate_geometry") is True

    def test_validate_unknown_script(self) -> None:
        """validate() returns False for unknown script names."""
        tool = _make_tool()
        spec = tool.get_tool_spec()
        assert spec.validate(script="unknown_script_xyz") is False

    def test_validate_missing_script(self) -> None:
        """validate() returns False when script kwarg is absent."""
        tool = _make_tool()
        spec = tool.get_tool_spec()
        assert spec.validate() is False


# ---------------------------------------------------------------------------
# Execute -- success paths
# ---------------------------------------------------------------------------


class TestBlenderToolExecute:
    def test_execute_optimize_mesh(self) -> None:
        """execute optimize_mesh invokes blender subprocess with correct args."""
        tool = _make_tool(blender_path="/usr/bin/blender")
        proc = _make_proc(returncode=0, stdout=b"Mesh optimized")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
            result = _run(
                tool.execute(
                    script="optimize_mesh",
                    input_path="model.fbx",
                    output_path="out.fbx",
                )
            )

        # Blender called in background mode with the script
        args = mock_exec.call_args[0]
        assert args[0] == "/usr/bin/blender"
        assert "--background" in args
        assert "--python" in args
        assert "optimize_mesh.py" in " ".join(str(a) for a in args)

        assert result["output_path"] == "out.fbx"
        assert result["exit_code"] == "0"
        assert "stdout" in result

    def test_execute_generate_lod_passes_extra_args(self) -> None:
        """execute generate_lod passes lod_levels as extra arg."""
        tool = _make_tool(blender_path="/usr/bin/blender")
        proc = _make_proc(returncode=0, stdout=b"LODs generated")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
            _run(
                tool.execute(
                    script="generate_lod",
                    input_path="model.fbx",
                    output_path="out.fbx",
                    lod_levels="3",
                )
            )

        args = mock_exec.call_args[0]
        full_args = " ".join(str(a) for a in args)
        assert "lod_levels=3" in full_args

    def test_execute_generate_geometry_passes_extra_args(self) -> None:
        """execute generate_geometry passes geometry_type and dimensions."""
        tool = _make_tool(blender_path="/usr/bin/blender")
        proc = _make_proc(returncode=0, stdout=b"Geometry created")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
            _run(
                tool.execute(
                    script="generate_geometry",
                    input_path="",
                    output_path="out.fbx",
                    geometry_type="cube",
                    dimensions="2x2x2",
                )
            )

        args = mock_exec.call_args[0]
        full_args = " ".join(str(a) for a in args)
        assert "geometry_type=cube" in full_args
        assert "dimensions=2x2x2" in full_args

    def test_execute_returns_required_keys(self) -> None:
        """execute() returns dict with output_path, stdout, exit_code."""
        tool = _make_tool(blender_path="/usr/bin/blender")
        proc = _make_proc(returncode=0)

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = _run(
                tool.execute(
                    script="optimize_mesh",
                    input_path="m.fbx",
                    output_path="out.fbx",
                )
            )

        assert set(result.keys()) >= {"output_path", "stdout", "exit_code"}


# ---------------------------------------------------------------------------
# Execute -- error paths
# ---------------------------------------------------------------------------


class TestBlenderToolErrors:
    def test_execute_blender_not_found_raises(self) -> None:
        """execute raises FileNotFoundError when Blender executable not found."""
        tool = _make_tool(blender_path="/nonexistent/blender")

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("No such file")),
        ):
            with pytest.raises(FileNotFoundError):
                _run(
                    tool.execute(
                        script="optimize_mesh",
                        input_path="m.fbx",
                        output_path="out.fbx",
                    )
                )

    def test_execute_nonzero_exit_raises_runtime_error(self) -> None:
        """execute raises RuntimeError when subprocess returns non-zero exit code."""
        tool = _make_tool(blender_path="/usr/bin/blender")
        proc = _make_proc(returncode=1, stderr=b"Error: mesh corrupted")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(RuntimeError, match="mesh corrupted"):
                _run(
                    tool.execute(
                        script="optimize_mesh",
                        input_path="m.fbx",
                        output_path="out.fbx",
                    )
                )

    def test_execute_timeout_kills_subprocess(self) -> None:
        """Script timeout kills subprocess on expiry."""
        tool = _make_tool(blender_path="/usr/bin/blender")
        proc = _make_proc(returncode=0)
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(asyncio.TimeoutError):
                _run(
                    tool.execute(
                        script="optimize_mesh",
                        input_path="m.fbx",
                        output_path="out.fbx",
                    )
                )

        proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# Blender scripts structural tests
# ---------------------------------------------------------------------------


class TestBlenderScripts:
    def test_optimize_mesh_script_exists(self) -> None:
        """blender_scripts/optimize_mesh.py exists and is non-empty."""
        scripts_dir = (
            Path(__file__).parent.parent
            / "src"
            / "jarvis_engine"
            / "agent"
            / "tools"
            / "blender_scripts"
        )
        script = scripts_dir / "optimize_mesh.py"
        assert script.exists(), f"Missing: {script}"
        assert script.stat().st_size > 0

    def test_generate_lod_script_exists(self) -> None:
        """blender_scripts/generate_lod.py exists and is non-empty."""
        scripts_dir = (
            Path(__file__).parent.parent
            / "src"
            / "jarvis_engine"
            / "agent"
            / "tools"
            / "blender_scripts"
        )
        script = scripts_dir / "generate_lod.py"
        assert script.exists(), f"Missing: {script}"
        assert script.stat().st_size > 0

    def test_generate_geometry_script_exists(self) -> None:
        """blender_scripts/generate_geometry.py exists and is non-empty."""
        scripts_dir = (
            Path(__file__).parent.parent
            / "src"
            / "jarvis_engine"
            / "agent"
            / "tools"
            / "blender_scripts"
        )
        script = scripts_dir / "generate_geometry.py"
        assert script.exists(), f"Missing: {script}"
        assert script.stat().st_size > 0

    def test_optimize_mesh_uses_bpy(self) -> None:
        """optimize_mesh.py references bpy (Blender Python API)."""
        scripts_dir = (
            Path(__file__).parent.parent
            / "src"
            / "jarvis_engine"
            / "agent"
            / "tools"
            / "blender_scripts"
        )
        content = (scripts_dir / "optimize_mesh.py").read_text(encoding="utf-8")
        assert "bpy" in content

    def test_generate_lod_uses_decimate(self) -> None:
        """generate_lod.py applies decimate modifier for LOD generation."""
        scripts_dir = (
            Path(__file__).parent.parent
            / "src"
            / "jarvis_engine"
            / "agent"
            / "tools"
            / "blender_scripts"
        )
        content = (scripts_dir / "generate_lod.py").read_text(encoding="utf-8")
        assert "decimate" in content.lower()

    def test_generate_geometry_handles_cube(self) -> None:
        """generate_geometry.py handles geometry_type 'cube'."""
        scripts_dir = (
            Path(__file__).parent.parent
            / "src"
            / "jarvis_engine"
            / "agent"
            / "tools"
            / "blender_scripts"
        )
        content = (scripts_dir / "generate_geometry.py").read_text(encoding="utf-8")
        assert "cube" in content.lower()
