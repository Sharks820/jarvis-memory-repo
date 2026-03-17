---
phase: 24-asset-pipeline
plan: "01"
subsystem: agent/tools
tags: [3d-assets, tripo3d, blender, tools, tdd]
dependency_graph:
  requires: [22-core-agent-loop]
  provides: [TripoTool, BlenderTool, blender_scripts]
  affects: [agent/tool_registry, agent/step_executor]
tech_stack:
  added: [tripo3d SDK (lazy import), asyncio.create_subprocess_exec]
  patterns: [tool pattern (get_tool_spec -> ToolSpec), lazy import, approval gate, subprocess with timeout]
key_files:
  created:
    - engine/src/jarvis_engine/agent/tools/tripo_tool.py
    - engine/src/jarvis_engine/agent/tools/blender_tool.py
    - engine/src/jarvis_engine/agent/tools/blender_scripts/__init__.py
    - engine/src/jarvis_engine/agent/tools/blender_scripts/optimize_mesh.py
    - engine/src/jarvis_engine/agent/tools/blender_scripts/generate_lod.py
    - engine/src/jarvis_engine/agent/tools/blender_scripts/generate_geometry.py
    - engine/tests/test_tripo_tool.py
    - engine/tests/test_blender_tool.py
  modified: []
decisions:
  - "TripoTool lazy-imports tripo3d inside execute() to avoid ImportError when SDK not installed"
  - "BlenderTool path discovery: constructor arg > BLENDER_PATH env > default Windows path (not validated at init)"
  - "Blender scripts are bpy-only (run inside Blender embedded Python) -- not importable by pytest directly"
  - "BlenderTool script tests are structural (file existence + content assertions) not execution tests"
  - "estimate_cost returns 1.0 (constant) for TripoTool -- nonzero value is the trigger, exact credit cost not modeled"
metrics:
  duration: 16m
  completed_date: "2026-03-17"
  tasks_completed: 2
  tasks_total: 2
  new_tests: 35
  files_created: 8
---

# Phase 24 Plan 01: Asset Pipeline Tools Summary

**One-liner:** TripoTool wraps tripo3d SDK (requires_approval=True) and BlenderTool runs headless Blender subprocess (requires_approval=False) for 3D asset generation in the Unity agent pipeline.

## What Was Built

### Task 1: TripoTool (commit aa630db5)
- `TripoTool` class following FileTool/ShellTool pattern
- `get_tool_spec()` returns ToolSpec with `requires_approval=True` and `estimate_cost=1.0`
- Calls `client.text_to_model()` or `client.image_to_model()` based on `image_path` arg
- `api_key` falls back to `TRIPO_API_KEY` env var at execute() time
- Raises `ValueError` on missing key, `RuntimeError` on SDK errors
- tripo3d imported lazily to avoid errors when SDK not installed
- 13 tests (mocked TripoClient, no real API calls)

### Task 2: BlenderTool + bpy scripts (commit b2161c6a)
- `BlenderTool` class with `asyncio.create_subprocess_exec` for headless Blender
- Path discovery: constructor arg > `BLENDER_PATH` env > default Windows path
- `asyncio.wait_for` with 120s timeout kills subprocess on expiry
- `RuntimeError` raised on non-zero exit code with stderr context
- Three bpy scripts (run inside Blender's embedded Python):
  - `optimize_mesh.py`: decimate + normal recalc + UV unwrap + FBX export
  - `generate_lod.py`: 3-level LOD chain (100%/50%/25%) with separate FBX exports
  - `generate_geometry.py`: cube/box, plane/terrain, cylinder/pillar primitives
- 22 tests (structural + subprocess mock, no real Blender required)

## Test Results

- **Before:** 6347 passing, 10 skipped
- **After:** 6384 passing, 8 skipped
- **New tests:** 35 (13 tripo + 22 blender)
- **ruff:** clean

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check: PASSED
