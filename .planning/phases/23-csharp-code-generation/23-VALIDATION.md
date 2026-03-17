---
phase: 23
slug: csharp-code-generation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 23 — Validation Strategy

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (Python) + file validation (C#) |
| **Quick run command** | `python -m pytest engine/tests/ -x -q` |

## Phase Requirements → Test Map

| Requirement | Test File(s) | Strategy |
|-------------|-------------|----------|
| CODE-01 | test_prompt_builder.py | KG facts injected into prompt, Unity 6.3 API patterns present |
| CODE-02 | test_compile_fix_loop.py | Mock compile errors, verify fix loop with 5 retries |
| CODE-03 | test_nunit_generator.py | Generate test file, verify NUnit structure |
| UNITY-05 | File validation | JarvisPanel.cs exists with EditorWindow, progress UI |
| KNOW-03 | test_prompt_builder.py | KG queried during prompt construction |
| KNOW-04 | test_api_validator.py | Breaking changes surfaced, deprecated API flagged |

## Wave 0 Gaps

- [ ] engine/tests/test_prompt_builder.py
- [ ] engine/tests/test_api_validator.py
- [ ] engine/tests/test_nunit_generator.py
- [ ] engine/tests/test_compile_fix_loop.py
