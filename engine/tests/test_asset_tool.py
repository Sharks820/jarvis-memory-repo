"""Tests for AssetTool -- Unity asset import coordination and routing.

Tests cover:
- get_tool_spec() returns correct ToolSpec
- import_model sends correct ModelImporter settings via UnityTool
- import_texture sends correct TextureImporter settings
- import_audio sends correct AudioImporter settings
- route action classifies descriptions to tripo or blender
- generate action calls the correct tool and imports result
- batch_import uses StartAssetEditing/StopAssetEditing
- path jail enforcement (Assets/JarvisGenerated/ only)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_unity_mock() -> MagicMock:
    """Return a mock UnityTool with async call()."""
    mock = MagicMock()
    mock.call = AsyncMock(return_value={"ok": True})
    return mock


def _make_tripo_mock(model_path: str = "Assets/JarvisGenerated/Models/chest.fbx") -> MagicMock:
    """Return a mock TripoTool."""
    mock = MagicMock()
    mock.execute = AsyncMock(return_value={
        "model_path": model_path,
        "format": "fbx",
        "task_id": "task_abc",
    })
    return mock


def _make_blender_mock(output_path: str = "Assets/JarvisGenerated/Models/wall.fbx") -> MagicMock:
    """Return a mock BlenderTool."""
    mock = MagicMock()
    mock.execute = AsyncMock(return_value={
        "output_path": output_path,
        "stdout": "",
        "exit_code": "0",
    })
    return mock


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Import AssetTool (will fail until implementation exists)
# ---------------------------------------------------------------------------

from jarvis_engine.agent.tools.asset_tool import AssetTool  # noqa: E402


# ---------------------------------------------------------------------------
# Test 1: get_tool_spec returns ToolSpec with name="asset", requires_approval=False
# ---------------------------------------------------------------------------


def test_get_tool_spec_name_and_approval() -> None:
    """get_tool_spec() returns ToolSpec with name='asset', requires_approval=False."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)
    spec = tool.get_tool_spec()

    assert spec.name == "asset"
    assert spec.requires_approval is False
    assert spec.description  # non-empty
    assert "action" in spec.parameters.get("properties", {})


# ---------------------------------------------------------------------------
# Test 2: import_model sends correct ModelImporter settings
# ---------------------------------------------------------------------------


def test_import_model_sends_correct_settings() -> None:
    """execute(action='import_model', path='Assets/JarvisGenerated/crate.fbx')
    calls unity_tool.call with ModelImporter settings."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    result = _run(tool.execute(action="import_model", path="Assets/JarvisGenerated/crate.fbx"))

    assert result["imported"] == "Assets/JarvisGenerated/crate.fbx"
    assert result["type"] == "model"

    # Should have called unity_tool.call at least once
    assert unity.call.call_count >= 1
    # Find the ImportAsset call
    calls_flat = [str(c) for c in unity.call.call_args_list]
    combined = " ".join(calls_flat)
    assert "ImportAsset" in combined or "import" in combined.lower()


# ---------------------------------------------------------------------------
# Test 3: import_texture sends correct TextureImporter settings
# ---------------------------------------------------------------------------


def test_import_texture_sends_correct_settings() -> None:
    """execute(action='import_texture') sends sRGB=true, maxSize=2048,
    compression=normal."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    result = _run(tool.execute(action="import_texture", path="Assets/JarvisGenerated/tex.png"))

    assert result["imported"] == "Assets/JarvisGenerated/tex.png"
    assert result["type"] == "texture"

    # Verify unity.call was invoked with texture settings somewhere
    all_call_args = str(unity.call.call_args_list)
    assert "sRGB" in all_call_args or "TextureImporter" in all_call_args or "texture" in all_call_args.lower()
    assert "2048" in all_call_args


# ---------------------------------------------------------------------------
# Test 4: import_audio sends correct AudioImporter settings
# ---------------------------------------------------------------------------


def test_import_audio_sends_correct_settings() -> None:
    """execute(action='import_audio') sends Vorbis compression, quality=0.7."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    result = _run(tool.execute(action="import_audio", path="Assets/JarvisGenerated/sfx.wav"))

    assert result["imported"] == "Assets/JarvisGenerated/sfx.wav"
    assert result["type"] == "audio"

    all_call_args = str(unity.call.call_args_list)
    assert "Vorbis" in all_call_args or "vorbis" in all_call_args.lower()


# ---------------------------------------------------------------------------
# Test 5: route: organic object -> tripo
# ---------------------------------------------------------------------------


def test_route_organic_to_tripo() -> None:
    """execute(action='route', description='a wooden treasure chest') -> tool='tripo'."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    result = _run(tool.execute(action="route", description="a wooden treasure chest"))

    assert result["tool"] == "tripo"
    assert result.get("reason")  # reason is present and non-empty


# ---------------------------------------------------------------------------
# Test 6: route: architecture -> blender
# ---------------------------------------------------------------------------


def test_route_architecture_to_blender() -> None:
    """execute(action='route', description='a stone castle wall') -> tool='blender'."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    result = _run(tool.execute(action="route", description="a stone castle wall"))

    assert result["tool"] == "blender"


# ---------------------------------------------------------------------------
# Test 7: route: terrain -> blender
# ---------------------------------------------------------------------------


def test_route_terrain_to_blender() -> None:
    """execute(action='route', description='flat terrain with hills') -> tool='blender'."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    result = _run(tool.execute(action="route", description="flat terrain with hills"))

    assert result["tool"] == "blender"


# ---------------------------------------------------------------------------
# Test 8: route: character/creature -> tripo
# ---------------------------------------------------------------------------


def test_route_character_to_tripo() -> None:
    """execute(action='route', description='a dragon character') -> tool='tripo'."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    result = _run(tool.execute(action="route", description="a dragon character"))

    assert result["tool"] == "tripo"


# ---------------------------------------------------------------------------
# Test 9: generate organic -> calls tripo, then imports
# ---------------------------------------------------------------------------


def test_generate_organic_calls_tripo_and_imports() -> None:
    """execute(action='generate', description='a wooden crate') routes to tripo,
    calls tripo.execute, then imports the model into Unity."""
    unity = _make_unity_mock()
    tripo = _make_tripo_mock("Assets/JarvisGenerated/Models/crate.fbx")
    blender = _make_blender_mock()
    tool = AssetTool(unity_tool=unity, tripo_tool=tripo, blender_tool=blender)

    result = _run(
        tool.execute(
            action="generate",
            description="a wooden crate",
            output_dir="Assets/JarvisGenerated/Models",
        )
    )

    tripo.execute.assert_called_once()
    assert result["tool_used"] == "tripo"
    assert result["model_path"] == "Assets/JarvisGenerated/Models/crate.fbx"
    assert result["imported"] is True


# ---------------------------------------------------------------------------
# Test 10: generate architecture -> calls blender, then imports
# ---------------------------------------------------------------------------


def test_generate_architecture_calls_blender_and_imports() -> None:
    """execute(action='generate', description='a brick wall segment') routes to blender,
    calls blender.execute with generate_geometry, then imports."""
    unity = _make_unity_mock()
    tripo = _make_tripo_mock()
    blender = _make_blender_mock("Assets/JarvisGenerated/Models/wall.fbx")
    tool = AssetTool(unity_tool=unity, tripo_tool=tripo, blender_tool=blender)

    result = _run(
        tool.execute(
            action="generate",
            description="a brick wall segment",
            output_dir="Assets/JarvisGenerated/Models",
        )
    )

    blender.execute.assert_called_once()
    assert result["tool_used"] == "blender"
    assert result["imported"] is True


# ---------------------------------------------------------------------------
# Test 11: batch_import uses StartAssetEditing / StopAssetEditing
# ---------------------------------------------------------------------------


def test_batch_import_uses_asset_editing_batch() -> None:
    """execute(action='batch_import', paths=[...]) calls StartAssetEditing and
    StopAssetEditing around per-asset imports."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    paths = [
        "Assets/JarvisGenerated/crate.fbx",
        "Assets/JarvisGenerated/tex.png",
        "Assets/JarvisGenerated/sfx.wav",
    ]
    result = _run(tool.execute(action="batch_import", paths=paths))

    assert result["imported_count"] == 3
    assert result["paths"] == paths

    all_calls = str(unity.call.call_args_list)
    assert "StartAssetEditing" in all_calls
    assert "StopAssetEditing" in all_calls


# ---------------------------------------------------------------------------
# Test 12: path jail enforcement - rejects paths outside Assets/JarvisGenerated/
# ---------------------------------------------------------------------------


def test_path_jail_rejects_outside_paths() -> None:
    """Paths outside Assets/JarvisGenerated/ must raise PermissionError."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    with pytest.raises(PermissionError):
        _run(tool.execute(action="import_model", path="Assets/SomeOtherFolder/evil.fbx"))


def test_path_jail_rejects_traversal() -> None:
    """Path traversal attempts must raise PermissionError."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    with pytest.raises(PermissionError):
        _run(tool.execute(action="import_model", path="Assets/JarvisGenerated/../../../evil.fbx"))


# ---------------------------------------------------------------------------
# Test 13: validate() rejects unknown actions
# ---------------------------------------------------------------------------


def test_validate_unknown_action() -> None:
    """validate(action='unknown') returns False."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity)

    assert tool.validate(action="unknown") is False
    assert tool.validate(action="import_model") is True
    assert tool.validate(action="import_texture") is True
    assert tool.validate(action="import_audio") is True
    assert tool.validate(action="route") is True
    assert tool.validate(action="generate") is True
    assert tool.validate(action="batch_import") is True
    assert tool.validate(action="") is False


# ---------------------------------------------------------------------------
# Test 14: generate with no tripo_tool raises RuntimeError gracefully
# ---------------------------------------------------------------------------


def test_generate_no_tripo_tool_raises() -> None:
    """generate with organic description when tripo_tool=None raises RuntimeError."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity, tripo_tool=None, blender_tool=None)

    with pytest.raises(RuntimeError, match="[Tt]ripo"):
        _run(
            tool.execute(
                action="generate",
                description="a wooden crate",
                output_dir="Assets/JarvisGenerated/Models",
            )
        )


# ---------------------------------------------------------------------------
# Test 15: generate with no blender_tool raises RuntimeError gracefully
# ---------------------------------------------------------------------------


def test_generate_no_blender_tool_raises() -> None:
    """generate with architecture description when blender_tool=None raises RuntimeError."""
    unity = _make_unity_mock()
    tool = AssetTool(unity_tool=unity, tripo_tool=None, blender_tool=None)

    with pytest.raises(RuntimeError, match="[Bb]lender"):
        _run(
            tool.execute(
                action="generate",
                description="a stone castle wall",
                output_dir="Assets/JarvisGenerated/Models",
            )
        )
