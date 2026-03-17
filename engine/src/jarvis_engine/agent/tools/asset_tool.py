"""AssetTool -- Unity asset import coordination and intelligent routing.

Coordinates Unity asset imports (model, texture, audio) by delegating to the
UnityTool WebSocket bridge.  Also provides intelligent routing between the
TripoTool (organic/character assets) and BlenderTool (architecture/terrain)
backends based on keyword classification of the asset description.

Security model:
- Path jail: all asset paths must start with Assets/JarvisGenerated/.
- Delegates to UnityTool for all Unity bridge communication.

Usage::

    tool = AssetTool(unity_tool=unity, tripo_tool=tripo, blender_tool=blender)
    result = await tool.execute(action="import_model", path="Assets/JarvisGenerated/crate.fbx")
    result = await tool.execute(action="route", description="a dragon character")
    result = await tool.execute(action="generate", description="a wooden crate",
                                output_dir="Assets/JarvisGenerated/Models")
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from jarvis_engine.agent.tool_registry import ToolSpec

# ---------------------------------------------------------------------------
# Path jail
# ---------------------------------------------------------------------------

_JAIL_PREFIX = "Assets/JarvisGenerated"


def _assert_in_jail(path: str) -> None:
    """Raise PermissionError if *path* is outside Assets/JarvisGenerated/."""
    if not path:
        raise PermissionError(
            f"Empty path is not permitted; must be inside {_JAIL_PREFIX}/"
        )
    normalised = os.path.normpath(path.replace("\\", "/")).replace("\\", "/")
    required_prefix = _JAIL_PREFIX + "/"
    if not normalised.startswith(required_prefix):
        raise PermissionError(
            f"Path {path!r} (normalised: {normalised!r}) is outside the "
            f"asset path jail ({_JAIL_PREFIX}/)."
        )


# ---------------------------------------------------------------------------
# Extension-to-type mapping for batch_import
# ---------------------------------------------------------------------------

_MODEL_EXTS = frozenset({".fbx", ".glb", ".obj", ".gltf"})
_TEXTURE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".tga", ".tiff", ".bmp"})
_AUDIO_EXTS = frozenset({".wav", ".mp3", ".ogg", ".aiff"})


def _detect_asset_type(path: str) -> str:
    """Return 'model', 'texture', or 'audio' based on file extension."""
    ext = Path(path).suffix.lower()
    if ext in _MODEL_EXTS:
        return "model"
    if ext in _TEXTURE_EXTS:
        return "texture"
    if ext in _AUDIO_EXTS:
        return "audio"
    return "model"  # default


# ---------------------------------------------------------------------------
# Routing keyword sets
# ---------------------------------------------------------------------------

_TRIPO_KEYWORDS = frozenset(
    {
        "character", "creature", "animal", "organic", "prop", "weapon",
        "furniture", "plant", "tree", "food", "clothing", "accessory",
        "vehicle", "monster", "npc", "human", "body", "dragon", "person",
    }
)

_BLENDER_KEYWORDS = frozenset(
    {
        "wall", "floor", "ceiling", "terrain", "landscape", "building",
        "architecture", "road", "bridge", "stairs", "column", "pillar",
        "fence", "platform", "tile", "block", "primitive", "box",
        "cylinder", "sphere", "geometric",
    }
)


def _route_description(description: str) -> tuple[str, str]:
    """Return (tool_name, reason) based on keyword match in description.

    Checks BLENDER_KEYWORDS first, then TRIPO_KEYWORDS.  Default is 'tripo'
    since organic/character assets are more common in game pipelines.
    """
    words = description.lower().split()
    word_set = set(words)

    # Check full text for substring matches (catches multi-word keywords)
    desc_lower = description.lower()

    matched_blender = [kw for kw in _BLENDER_KEYWORDS if kw in desc_lower]
    matched_tripo = [kw for kw in _TRIPO_KEYWORDS if kw in desc_lower]

    if matched_blender and not matched_tripo:
        reason = f"matched architecture/terrain keywords: {', '.join(matched_blender)}"
        return "blender", reason

    if matched_tripo:
        reason = f"matched organic/character keywords: {', '.join(matched_tripo)}"
        return "tripo", reason

    if matched_blender:
        reason = f"matched architecture/terrain keywords: {', '.join(matched_blender)}"
        return "blender", reason

    # Default: organic is more common for game assets
    _ = word_set  # suppress unused variable warning
    return "tripo", "default: organic/prop assets are most common in game pipelines"


# ---------------------------------------------------------------------------
# Geometry type inference for BlenderTool
# ---------------------------------------------------------------------------

def _infer_geometry_type(description: str) -> str:
    """Infer a generate_geometry script type from description keywords."""
    desc_lower = description.lower()
    if any(kw in desc_lower for kw in ("terrain", "landscape", "floor", "plane", "ground")):
        return "plane"
    if any(kw in desc_lower for kw in ("column", "pillar", "cylinder", "tower")):
        return "cylinder"
    return "box"  # default for walls, buildings, blocks, etc.


# ---------------------------------------------------------------------------
# Known actions
# ---------------------------------------------------------------------------

_KNOWN_ACTIONS = frozenset(
    {"import_model", "import_texture", "import_audio", "route", "generate", "batch_import"}
)


# ---------------------------------------------------------------------------
# AssetTool
# ---------------------------------------------------------------------------


class AssetTool:
    """Asset import coordinator and routing tool for the Unity agent pipeline.

    Delegates all Unity bridge communication to *unity_tool*.
    Optional *tripo_tool* and *blender_tool* are used for generation actions.
    """

    def __init__(
        self,
        unity_tool: Any,
        tripo_tool: Any = None,
        blender_tool: Any = None,
    ) -> None:
        """Initialise AssetTool.

        Args:
            unity_tool: UnityTool instance for bridge communication.
            tripo_tool: TripoTool instance (optional; needed for generate action).
            blender_tool: BlenderTool instance (optional; needed for generate action).
        """
        self._unity = unity_tool
        self._tripo = tripo_tool
        self._blender = blender_tool

    # ------------------------------------------------------------------
    # Core dispatch
    # ------------------------------------------------------------------

    async def execute(self, *, action: str, **kwargs: Any) -> dict[str, Any]:
        """Dispatch to the appropriate handler based on *action*.

        Actions:
            import_model  -- set ModelImporter settings + ImportAsset
            import_texture -- set TextureImporter settings + ImportAsset
            import_audio  -- set AudioImporter settings + ImportAsset
            route         -- classify description to tripo or blender
            generate      -- generate + import a model end-to-end
            batch_import  -- import multiple assets with batch editing

        Raises:
            PermissionError: Path outside Assets/JarvisGenerated/.
            ValueError: Unknown action.
            RuntimeError: Dependent tool (tripo/blender) not available.
        """
        dispatch = {
            "import_model": self._import_model,
            "import_texture": self._import_texture,
            "import_audio": self._import_audio,
            "route": self._route,
            "generate": self._generate,
            "batch_import": self._batch_import,
        }
        handler = dispatch.get(action)
        if handler is None:
            raise ValueError(
                f"AssetTool: unknown action {action!r}. "
                f"Valid actions: {', '.join(sorted(dispatch))}"
            )
        return await handler(**kwargs)

    # ------------------------------------------------------------------
    # import_model
    # ------------------------------------------------------------------

    async def _import_model(self, *, path: str, **_kw: Any) -> dict[str, Any]:
        """Apply ModelImporter settings and call AssetDatabase.ImportAsset."""
        _assert_in_jail(path)

        # Set ModelImporter settings
        await self._unity.call(
            "SetModelImporterSettings",
            {
                "path": path,
                "scaleFactor": 1.0,
                "importMaterials": True,
                "generateLightmapUVs": True,
                "meshCompression": "Medium",
            },
        )
        # Trigger import
        await self._unity.call("AssetDatabase.ImportAsset", {"path": path})

        logger.debug("AssetTool: imported model %r", path)
        return {"imported": path, "type": "model"}

    # ------------------------------------------------------------------
    # import_texture
    # ------------------------------------------------------------------

    async def _import_texture(self, *, path: str, **_kw: Any) -> dict[str, Any]:
        """Apply TextureImporter settings and call AssetDatabase.ImportAsset."""
        _assert_in_jail(path)

        # Set TextureImporter settings: sRGB=true, maxSize=2048, compression=Normal, mipmaps=true
        await self._unity.call(
            "SetTextureImporterSettings",
            {
                "path": path,
                "sRGB": True,
                "maxTextureSize": 2048,
                "textureCompression": "Normal",
                "mipmapEnabled": True,
            },
        )
        await self._unity.call("AssetDatabase.ImportAsset", {"path": path})

        logger.debug("AssetTool: imported texture %r", path)
        return {"imported": path, "type": "texture"}

    # ------------------------------------------------------------------
    # import_audio
    # ------------------------------------------------------------------

    async def _import_audio(self, *, path: str, **_kw: Any) -> dict[str, Any]:
        """Apply AudioImporter settings and call AssetDatabase.ImportAsset."""
        _assert_in_jail(path)

        # Set AudioImporter settings: Vorbis compression, quality=0.7, loadInBackground=true
        await self._unity.call(
            "SetAudioImporterSettings",
            {
                "path": path,
                "loadInBackground": True,
                "compressionFormat": "Vorbis",
                "quality": 0.7,
            },
        )
        await self._unity.call("AssetDatabase.ImportAsset", {"path": path})

        logger.debug("AssetTool: imported audio %r", path)
        return {"imported": path, "type": "audio"}

    # ------------------------------------------------------------------
    # route
    # ------------------------------------------------------------------

    async def _route(self, *, description: str, **_kw: Any) -> dict[str, Any]:
        """Classify description to 'tripo' or 'blender'."""
        tool_name, reason = _route_description(description)
        logger.debug("AssetTool.route: %r -> %s (%s)", description, tool_name, reason)
        return {"tool": tool_name, "reason": reason}

    # ------------------------------------------------------------------
    # generate
    # ------------------------------------------------------------------

    async def _generate(
        self,
        *,
        description: str,
        output_dir: str,
        format: str = "fbx",
        **_kw: Any,
    ) -> dict[str, Any]:
        """Route to tripo or blender, generate model, then import into Unity."""
        tool_name, reason = _route_description(description)

        if tool_name == "tripo":
            if self._tripo is None:
                raise RuntimeError(
                    "TripoTool is not available. Pass tripo_tool= to AssetTool."
                )
            gen_result = await self._tripo.execute(
                prompt=description,
                format=format,
            )
            model_path = gen_result["model_path"]
        else:  # blender
            if self._blender is None:
                raise RuntimeError(
                    "BlenderTool is not available. Pass blender_tool= to AssetTool."
                )
            geometry_type = _infer_geometry_type(description)
            output_path = str(Path(output_dir) / f"generated_{geometry_type}.{format}")
            gen_result = await self._blender.execute(
                script="generate_geometry",
                input_path="",
                output_path=output_path,
                geometry_type=geometry_type,
            )
            model_path = gen_result["output_path"]

        logger.debug(
            "AssetTool.generate: generated %r via %s, reason: %s",
            model_path,
            tool_name,
            reason,
        )

        # Import the generated model if the path is inside the jail
        imported = False
        try:
            _assert_in_jail(model_path)
            await self._import_model(path=model_path)
            imported = True
        except PermissionError:
            logger.warning(
                "AssetTool.generate: model_path %r is outside jail, skipping import",
                model_path,
            )

        return {
            "model_path": model_path,
            "tool_used": tool_name,
            "imported": imported,
        }

    # ------------------------------------------------------------------
    # batch_import
    # ------------------------------------------------------------------

    async def _batch_import(self, *, paths: list[str], **_kw: Any) -> dict[str, Any]:
        """Import multiple assets with StartAssetEditing/StopAssetEditing batching."""
        # Validate all paths first
        for path in paths:
            _assert_in_jail(path)

        await self._unity.call("AssetDatabase.StartAssetEditing", {})
        try:
            for path in paths:
                asset_type = _detect_asset_type(path)
                if asset_type == "model":
                    await self._import_model(path=path)
                elif asset_type == "texture":
                    await self._import_texture(path=path)
                else:
                    await self._import_audio(path=path)
        finally:
            await self._unity.call("AssetDatabase.StopAssetEditing", {})

        logger.debug("AssetTool.batch_import: imported %d assets", len(paths))
        return {"imported_count": len(paths), "paths": list(paths)}

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, **kwargs: Any) -> bool:
        """Return True only when 'action' is a known action string."""
        action = kwargs.get("action", "")
        return bool(action and action in _KNOWN_ACTIONS)

    # ------------------------------------------------------------------
    # ToolSpec
    # ------------------------------------------------------------------

    def get_tool_spec(self) -> "ToolSpec":
        """Return a ToolSpec for registration in the agent ToolRegistry."""
        from jarvis_engine.agent.tool_registry import ToolSpec  # lazy import

        return ToolSpec(
            name="asset",
            description=(
                "Coordinate Unity asset imports and 3D model generation routing. "
                "Imports models (FBX/GLB), textures (PNG/JPG/TGA), and audio (WAV/MP3/OGG) "
                "with correct Unity importer settings. Routes generation requests to tripo.io "
                "(organic/character assets) or Blender (architecture/terrain)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": sorted(_KNOWN_ACTIONS),
                        "description": (
                            "Action to perform: import_model, import_texture, import_audio "
                            "(import existing file), route (classify description to tripo/blender), "
                            "generate (generate + import end-to-end), "
                            "batch_import (import multiple assets efficiently)."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Asset path inside Assets/JarvisGenerated/ "
                            "(required for import_model, import_texture, import_audio)."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Natural language description of the asset (required for route, generate).",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory inside Assets/JarvisGenerated/ (required for generate).",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["fbx", "glb", "obj"],
                        "description": "Model format for generate action (default: fbx).",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of asset paths for batch_import.",
                    },
                },
                "required": ["action"],
            },
            execute=self._dispatch,
            validate=self.validate,
            requires_approval=False,
            is_destructive=False,
        )

    async def _dispatch(self, **kwargs: Any) -> Any:
        """Dispatch from ToolSpec call convention (all kwargs)."""
        action = kwargs.pop("action")
        return await self.execute(action=action, **kwargs)
