---
phase: 25-polish-and-integration
plan: "01"
subsystem: agent/learn_accumulator
tags: [learn-as-you-go, knowledge-graph, code-pattern, error-fix, accumulator]
dependency_graph:
  requires:
    - knowledge/graph.py (KnowledgeGraph.add_fact, query_relevant_facts)
    - agent/codegen/compile_fix_loop.py
    - agent/codegen/prompt_builder.py
  provides:
    - LearnAccumulator class with save_pattern, save_error_fix, query_patterns
    - learn-as-you-go hook in CompileFixLoop.run()
    - "Learned Patterns" section injection in UnityPromptBuilder.build_unity_system_prompt()
  affects:
    - agent/codegen/compile_fix_loop.py (optional accumulator param added)
    - agent/codegen/prompt_builder.py (optional accumulator param added)
tech_stack:
  added: []
  patterns:
    - MD5 node IDs (usedforsecurity=False) matching ReflectionLoop convention
    - Optional parameter pattern (None default, guarded all calls) for backward compat
    - try/except BLE001 swallowing for non-fatal accumulator failures
key_files:
  created:
    - engine/src/jarvis_engine/agent/learn_accumulator.py
    - engine/tests/test_learn_accumulator.py
  modified:
    - engine/src/jarvis_engine/agent/codegen/compile_fix_loop.py
    - engine/src/jarvis_engine/agent/codegen/prompt_builder.py
decisions:
  - LearnAccumulator uses query_relevant_facts (keyword-based) rather than query_relevant_facts_semantic -- no EmbeddingService dependency, simpler and sufficient for pattern retrieval
  - Accumulator parameter is optional (None default) in both CompileFixLoop and UnityPromptBuilder -- full backward compat, no existing tests needed changes
  - save_pattern stores on every success; save_error_fix only fires when iterations > 1 (meaning a fix was actually applied)
  - Truncation limits: snippets 500 chars, error messages 200 chars, before/after code 200 chars -- balances KG label size vs usefulness
  - query_patterns filters results to node_type in ("code_pattern", "error_fix") -- avoids injecting unity_api/unity_breaking facts that come from a different pipeline
metrics:
  duration: "~17 minutes"
  completed: "2026-03-17"
  tasks_completed: 2
  files_created: 2
  files_modified: 2
  tests_added: 32
  tests_total_after: 6456
---

# Phase 25 Plan 01: LearnAccumulator - Learn-as-you-go KG Accumulation Summary

**One-liner:** LearnAccumulator stores successful code patterns and error-fix pairs in KG, then injects accumulated history into LLM prompts to reduce repeat mistakes.

## What Was Built

A new `LearnAccumulator` module that closes the feedback loop between compile-fix cycle outcomes and future code generation prompts:

1. **`save_pattern()`** — after a successful compile-fix run, persists the final working C# code as a `code_pattern` KG fact (confidence=0.7, node_id `pattern:{md5[:12]}`)
2. **`save_error_fix()`** — when a fix was actually applied (iterations > 1), persists the error+fix pair as an `error_fix` KG fact (confidence=0.8, node_id `errfix:{md5[:12]}`)
3. **`query_patterns()`** — before generating code for a new task, retrieves matching code_pattern and error_fix facts by keyword search and returns label strings for prompt injection

Both `CompileFixLoop` and `UnityPromptBuilder` were updated to accept an optional `accumulator` parameter (backward-compat, no existing test changes required).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | LearnAccumulator with KG storage and query | `88de40fc` | learn_accumulator.py, test_learn_accumulator.py |
| 2 | Wire into compile_fix_loop and prompt_builder | `fa3db2d3` | compile_fix_loop.py, prompt_builder.py |

## Test Results

- **32 new tests** in test_learn_accumulator.py covering: save_pattern, save_error_fix, query_patterns, truncation, node_id format/uniqueness, empty-state, __all__ exports
- **86 tests** pass across all three test files (compile_fix_loop + prompt_builder + learn_accumulator)
- **6456 total tests pass**, 10 skipped — no regressions

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- `engine/src/jarvis_engine/agent/learn_accumulator.py` exists and exports `LearnAccumulator`
- `engine/tests/test_learn_accumulator.py` exists with 32 tests (> 80 lines minimum)
- `compile_fix_loop.py` contains `accumulator.save_` pattern calls
- `prompt_builder.py` contains `accumulator.query_` pattern call
- Commits `88de40fc` and `fa3db2d3` present in git log
- ruff check clean on all modified files
