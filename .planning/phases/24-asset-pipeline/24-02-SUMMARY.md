---
phase: 24-asset-pipeline
plan: "02"
subsystem: agent/tools
tags: [asset-pipeline, unity, importer, routing, tripo, blender, tdd]
dependency_graph:
  requires: [24-01, 22-core-agent-loop]
  provides: [AssetTool, asset-routing, tool-registry-wiring]
  affects: [agent/tool_registry, app.py, agent/step_executor]
tech_stack:
  added: []
  patterns: [tool pattern (get_tool_spec -> ToolSpec), path jail, keyword routing, try/except SUBSYSTEM_ERRORS]
key_files:
  created:
    - engine/src/jarvis_engine/agent/tools/asset_tool.py
    - engine/tests/test_asset_tool.py
  modified:
    - engine/src/jarvis_engine/app.py
decisions:
  - "AssetTool delegates all Unity bridge calls to UnityTool.call() -- no direct WebSocket usage"
  - "route() checks BLENDER_KEYWORDS first (then TRIPO_KEYWORDS) to handle mixed descriptions; default is tripo"
  - "batch_import validates all paths before starting StartAssetEditing to avoid partial-open state on PermissionError"
  - "generate() catches PermissionError from model_path jail check -- graceful degradation when tool outputs outside jail"
  - "All three asset tools registered in _register_agent_handlers() with SUBSYSTEM_ERRORS try/except isolation"
metrics:
  duration: 8m
  completed_date: "2026-03-17"
  tasks_completed: 2
  tasks_total: 2
  new_tests: 16
  files_created: 2
  files_modified: 1
---

# Phase 24 Plan 02: Asset Pipeline -- AssetTool + ToolRegistry Wiring Summary

**One-liner:** AssetTool coordinates Unity asset imports (model/texture/audio) with per-type importer settings and routes generation requests to TripoTool (organic/character) or BlenderTool (architecture/terrain) via keyword classification; all three tools registered in ToolRegistry at startup.

## What Was Built

### Task 1: AssetTool -- Unity import coordination and asset routing (commits 3882cfbc, cb4911a2)

TDD approach: 16 failing tests written first, then implementation.

- `AssetTool` class with `__init__(unity_tool, tripo_tool=None, blender_tool=None)`
- `execute(*, action, **kwargs)` dispatcher with 6 actions:
  - **import_model**: `SetModelImporterSettings` (scale=1.0, materials, lightmapUVs, Medium compression) + `AssetDatabase.ImportAsset`
  - **import_texture**: `SetTextureImporterSettings` (sRGB=True, maxSize=2048, Normal compression, mipmaps) + `AssetDatabase.ImportAsset`
  - **import_audio**: `SetAudioImporterSettings` (Vorbis, quality=0.7, loadInBackground) + `AssetDatabase.ImportAsset`
  - **route**: keyword classification -- TRIPO_KEYWORDS (character, creature, organic, weapon, furniture, NPC...) vs BLENDER_KEYWORDS (wall, terrain, building, architecture...). Default: tripo.
  - **generate**: route + call tripo/blender + import result into Unity. Graceful degradation if tool is None.
  - **batch_import**: `StartAssetEditing` + per-type imports + `StopAssetEditing`. Extension detection: .fbx/.glb=model, .png/.jpg/.tga=texture, .wav/.mp3/.ogg=audio.
- `validate(**kwargs)` checks action is in `_KNOWN_ACTIONS`
- `get_tool_spec()` returns `ToolSpec(name="asset", requires_approval=False)`
- Path jail enforced on all import paths via `_assert_in_jail()` (same pattern as UnityTool)

### Task 2: Wire asset tools into ToolRegistry via app.py (commit 915c3851)

Added to `_register_agent_handlers()` after WebTool block:

```python
# TripoTool (output_dir=root / "agent_assets")
tripo_tool_instance = TripoTool(output_dir=root / "agent_assets")
registry.register(tripo_tool_instance.get_tool_spec())  # requires_approval=True

# BlenderTool (default path discovery)
blender_tool_instance = BlenderTool()
registry.register(blender_tool_instance.get_tool_spec())

# AssetTool (with UnityTool reference + tripo/blender instances)
asset_tool = AssetTool(unity_tool=UnityTool(), tripo_tool=..., blender_tool=...)
registry.register(asset_tool.get_tool_spec())
```

Each wrapped in `try: ... except SUBSYSTEM_ERRORS as exc: logger.debug(...)` for graceful degradation.

## Test Results

- **Before:** 6384 passing, 8 skipped
- **After:** 6398 passing, 10 skipped
- **New tests:** 16 (all in test_asset_tool.py)
- **ruff:** clean on both new files and app.py

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check: PASSED
