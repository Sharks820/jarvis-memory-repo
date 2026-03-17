# Jarvis Unity Agent — Design Spec

**Date**: 2026-03-17
**Status**: Approved by user, implementation in progress

## Overview

Autonomous development agent for Jarvis that can build complete Unity games from text/voice instructions. Guided autonomous workflow with visibility — Jarvis plans, user approves, Jarvis executes while user watches, checks in at milestones.

## User Decisions

- **Workflow**: Guided autonomous with visibility (plan → approve → execute → checkpoint)
- **Interface**: Unity Editor plugin + CLI scripting with real-time widget progress
- **Unity setup**: Fresh install, Unity 6.3, learning stage
- **Asset creation**: Hybrid (tripo.io for organic, Blender for architecture) + pluggable tools
- **Safety**: Smart approval (create=auto, destroy/spend=approve)
- **Knowledge**: All 3 layers (LLM + seeded KG + learn-as-you-go) with DEEP upfront seeding
- **Testing**: Self-testing + debugging loop with 5-retry cap, error pattern learning

## Architecture

### Agent Core (Python)
- `AgentOrchestrator` — top-level coordinator, manages agent lifecycle
- `TaskPlanner` — LLM-driven task decomposition (high-level goal → phases → steps)
- `StepExecutor` — executes individual steps using Tools, handles errors
- `ToolRegistry` — pluggable tool management, dynamic tool registration
- `ReflectionLoop` — verify results, diagnose failures, learn from outcomes

### Tool Layer (Python, pluggable)
Standard interface: `execute(action, params) -> ToolResult`, `validate()`, `estimate_cost()`
Risk levels: safe (auto), destructive (approve), costly (approve + show estimate)

Built-in tools:
- **UnityTool** — Unity CLI + JarvisEditorBridge JSON-RPC
- **BlenderTool** — `blender --background --python script.py`
- **TripoTool** — tripo.io REST API for 3D model generation
- **FileTool** — project file read/write, confined to project directory
- **ShellTool** — subprocess with policy gate + timeout
- **WebTool** — existing web fetch + search pipeline
- **AssetTool** — Unity asset import + material setup

Pluggable tools added at runtime (Mixamo, Sketchfab, etc.)

### Unity Editor Plugin (C#)
- **JarvisEditorBridge** — reflection-based JSON-RPC command dispatch
- Covers FULL Unity Editor API surface via reflection (not hardcoded wrappers)
- HTTP server inside Unity Editor for bidirectional communication with Agent Core
- Jarvis panel: progress display, approval dialogs, console log streaming
- Auto-installs as a UPM local package in any Unity project

### Self-Testing & Debugging
- Compile → Unit Test → Play Mode → Runtime Validation → Visual Sanity
- 5-retry cap with escalation to user
- Error pattern learning (error + fix stored in KG, reused instantly)
- Auto-generates NUnit tests alongside game scripts

### Knowledge System
- LLM baseline (qwen3.5 local + cloud fallback)
- Deep-seeded Unity 6.3 KG (API, patterns, common errors, best practices)
- Learn-as-you-go (successful code patterns + error fixes accumulated)

## Implementation Phases

1. Agent Core + Tool interface + FileTool + ShellTool
2. UnityTool + JarvisEditorBridge (C#)
3. BlenderTool + TripoTool
4. Widget integration + real-time progress streaming
5. Unity Editor Plugin UI
6. Deep Unity 6.3 knowledge seeding
7. Self-testing & debugging loop
8. Command bus integration + voice commands
9. Pluggable tool system + learn-as-you-go

## Hardware Context
- RTX 4060 Ti 8GB, 32GB RAM, Ryzen 7 5700, Windows 11
- Unity 6.3 installed via Unity Hub
- Blender installed
- Local LLM: qwen3.5 via Ollama
