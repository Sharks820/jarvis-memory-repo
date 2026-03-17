---
phase: 23-csharp-code-generation
plan: "01"
subsystem: agent/codegen
tags: [unity, codegen, knowledge-graph, prompt-engineering, api-validation]
dependency_graph:
  requires:
    - 20-02 (Unity KG seeding -- unity_api, unity_breaking, unity_error node types)
    - knowledge/graph.py KnowledgeGraph.query_relevant_facts()
  provides:
    - agent/codegen/prompt_builder.py -- UnityPromptBuilder, build_unity_system_prompt
    - agent/codegen/api_validator.py -- ApiValidator, validate_csharp_against_kg, ValidationResult
  affects:
    - Phase 24 (3D asset pipeline) -- prompt builder can be reused for Blender scripts
    - Phase 25 (agent orchestration) -- ApiValidator feeds into compilation step
tech_stack:
  added: []
  patterns:
    - TYPE_CHECKING guard for KnowledgeGraph import (lazy, consistent with project pattern)
    - Compiled regex patterns for C# scanning (re.compile at module level)
    - Dataclass for result type (ValidationResult)
    - Module-level convenience functions wrapping class methods
key_files:
  created:
    - engine/src/jarvis_engine/agent/codegen/__init__.py
    - engine/src/jarvis_engine/agent/codegen/prompt_builder.py
    - engine/src/jarvis_engine/agent/codegen/api_validator.py
    - engine/tests/test_codegen_prompt_builder.py
    - engine/tests/test_codegen_api_validator.py
  modified: []
decisions:
  - "UnityPromptBuilder queries KG twice: once for unity_api facts (API reference), once for unity_breaking facts (warnings) -- separate calls allow distinct filtering by node_type"
  - "ApiValidator produces soft warnings for unknown APIs (not hard blocks) -- KG coverage is intentionally incomplete; hard blocks would break legitimate code"
  - "Baseline rules (SerializeField, Experimental namespaces, URP, JarvisGenerated path) are hardcoded strings -- they are stable Unity 6.3 invariants, not KG facts"
  - "ValidationResult is a plain dataclass (not a Result/Either monad) -- consistent with existing project style (no monadic patterns in codebase)"
metrics:
  duration_minutes: 20
  completed_date: "2026-03-17"
  tasks_completed: 2
  files_created: 5
  tests_added: 42
---

# Phase 23 Plan 01: UnityPromptBuilder + ApiValidator Summary

**One-liner:** KG-seeded Unity 6.3 system prompt builder and pre-compilation API validator catching Experimental namespace usage, SerializeField misuse, and providing CS0117/CS0619 alternatives from KG facts.

## What Was Built

Two modules in the new `agent/codegen/` subpackage:

### UnityPromptBuilder (`prompt_builder.py`)

Queries the KG for `unity_api` and `unity_breaking` node types and assembles a multi-section LLM system prompt. Sections:

1. Role declaration ("You are a Unity 6.3 C# code generator...")
2. Unity 6.3 API Reference (from KG `unity_api` facts, up to 15)
3. Breaking Change Warnings (from KG `unity_breaking` facts, up to 10)
4. Baseline rules (always present regardless of KG data):
   - `[field: SerializeField]` on auto-properties
   - No `UnityEngine.Experimental.*`
   - No URP Compatibility Mode render graph calls
   - Always include `using UnityEngine;`
   - MonoBehaviour lifecycle methods
   - `Assets/JarvisGenerated/` root path
5. Optional `extra_context` parameter appended at the end

### ApiValidator (`api_validator.py`)

Scans C# code with compiled regex patterns:

- **Experimental namespace check**: detects `using UnityEngine.Experimental.*` and queries KG for a non-Experimental alternative
- **SerializeField property check**: regex `\[SerializeField\]\s*\n?\s*public\s+\w...\{` detects wrong attribute on auto-properties; suggests `[field: SerializeField]`
- **Unknown API soft-warning**: checks `using` directives against KG; warns if namespace not found (non-blocking)
- **`query_alternative(error_code, error_msg)`**: for CS0117/CS0619, extracts the type name from the error message, queries KG for both api and breaking facts, returns a suggestion string or None

## Test Results

- 42 new tests: 20 prompt_builder + 22 api_validator
- Full suite: 6277 passing, 10 skipped (was 6185)
- ruff: clean on `engine/src/jarvis_engine/agent/codegen/`
- No regressions

## Deviations from Plan

None -- plan executed exactly as written.

## Self-Check: PASSED
