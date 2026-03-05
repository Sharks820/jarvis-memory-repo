# Follow-up Reliability + Learning Report (2026-03-04)

## Easy-to-read issue list (what was found)

### A) Learning + progress visibility
1. Mission status lacked clear progress percentage / stage text during execution.
2. User-facing mission status outputs did not expose a progress bar style indicator.

### B) Context continuity across LLM/CLI swaps
3. Conversation responses could re-introduce assistant persona after route/model changes.
4. No hard instruction existed to force continuity behavior across provider swaps.

### C) Web freshness / outdated answers
5. If web augmentation path failed, flows could still degrade to non-web model output for freshness-critical prompts.
6. No explicit guard existed for "latest/current/right now" hard-freshness requests.

### D) Reliability / command ergonomics
7. Web-research voice route depended on LLM availability even when web results were already fetched.

## Fixes implemented in this follow-up

### 1) Learning mission progress instrumentation
- Added mission progress fields in mission records:
  - `progress_pct`
  - `status_detail`
  - `progress_bar` (10-step visual bar)
- Added internal progress update helper to safely persist mission phase changes.
- Mission run now updates progress at key phases:
  - startup
  - source collection
  - page scanning
  - finding verification
  - report finalization
  - completion/failure
- CLI mission status now prints progress percentage and progress bar when available.

### 2) Context continuity hardening
- Added explicit anti-reintroduction instruction in LLM system instructions:
  - "Do not re-introduce yourself unless explicitly asked."
- Applied in both web-augmented and general conversational routing paths.

### 3) Web freshness guardrails
- Added `_requires_fresh_web_confirmation(query)` helper for strict freshness intent.
- If query is freshness-critical and web search fetch fails, command now returns a clear `web_confirmation_unavailable` intent instead of silently degrading to stale guidance.

### 4) Web route fallback resilience
- In web-augmented conversation path, if LLM provider is unavailable but web search already returned lines, system now emits a web-derived fallback response instead of hard failing.

## Validation executed
- `ruff check src tests`
- `pytest -q tests/test_learning_missions.py tests/test_ui_backend.py tests/test_mobile_api.py tests/test_sync_changelog.py tests/test_platform_stability.py tests/test_main.py::test_cmd_voice_run_routes_web_research`
- `pytest -q -x` exploratory full-suite progression
- `python -m compileall -q src tests`
- `python -m bandit -q -r src -f txt`
- `pip-audit`

## Current known remaining items
- Full-suite exploratory run still halts in this environment at desktop tray icon image test when image backend dependencies are missing.
- Security and dependency backlog remains from global scans:
  - Bandit: high/medium/low findings require dedicated triage pass.
  - pip-audit: pip CVE requiring toolchain upgrade.
