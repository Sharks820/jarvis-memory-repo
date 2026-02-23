---
phase: 05-knowledge-harvesting
plan: 01
subsystem: harvesting
tags: [openai, google-genai, minimax, kimi, gemini, multi-provider, knowledge-extraction]

# Dependency graph
requires:
  - phase: 01-memory-revolution-and-architecture
    provides: EnrichedIngestPipeline for content ingestion
  - phase: 03-intelligence-routing
    provides: CostTracker for per-query cost logging
provides:
  - KnowledgeHarvester orchestrator for multi-provider topic queries
  - MiniMax, Kimi, KimiNvidia, and Gemini provider classes
  - HarvestCommand/HarvestResult dataclasses
  - Harvested content ingestion at lower confidence (0.50)
affects: [05-02-PLAN, knowledge-harvesting, daily-briefing]

# Tech tracking
tech-stack:
  added: [openai>=1.0.0, google-genai>=1.0.0]
  patterns: [lazy-sdk-import, provider-abstraction, graceful-degradation-on-missing-keys]

key-files:
  created:
    - engine/src/jarvis_engine/harvesting/__init__.py
    - engine/src/jarvis_engine/harvesting/providers.py
    - engine/src/jarvis_engine/harvesting/harvester.py
    - engine/tests/test_harvesting_providers.py
  modified:
    - engine/pyproject.toml

key-decisions:
  - "HarvestResult dataclass in harvester.py, imported by providers.py to avoid circular dependency"
  - "Provider tests inject mock client via _client attribute instead of patching lazy module-level imports"
  - "Harvested content appends (confidence:0.50) marker to content text before pipeline ingestion"
  - "GeminiProvider does NOT inherit HarvesterProvider (different SDK -- google-genai vs OpenAI)"
  - "KimiNvidiaProvider overrides query() to pass extra_body with thinking disabled for instant mode"

patterns-established:
  - "Lazy SDK import: from openai import OpenAI inside _get_client() method body, not module level"
  - "Provider abstraction: HarvesterProvider base with concrete subclasses for each API"
  - "is_available property pattern: check env var in __init__, expose read-only bool"
  - "Mock injection pattern: set provider._client directly in tests to bypass lazy import"

requirements-completed: [HARV-01, HARV-02, HARV-05]

# Metrics
duration: 6min
completed: 2026-02-23
---

# Phase 5 Plan 1: Knowledge Harvesting Providers Summary

**Multi-provider knowledge harvesting with MiniMax, Kimi (Moonshot + NVIDIA NIM), and Gemini via lazy-loaded OpenAI and google-genai SDKs, orchestrated through KnowledgeHarvester with pipeline ingestion and cost tracking**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-23T06:43:01Z
- **Completed:** 2026-02-23T06:49:25Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Built 4 provider classes (MiniMax, Kimi, KimiNvidia, Gemini) with correct API endpoints, models, and pricing
- KnowledgeHarvester orchestrator queries multiple providers, ingests via EnrichedIngestPipeline at confidence 0.50, and logs costs
- All providers gracefully degrade when API keys are missing (no crash, RuntimeError on query)
- 13 comprehensive tests covering all providers, orchestration, pipeline ingestion, cost logging, and filtering

## Task Commits

Each task was committed atomically:

1. **Task 1: Harvesting package with provider abstraction** - `07744c6` (feat)
2. **Task 2: Comprehensive tests for providers and harvester** - `5e2cb87` (test)

## Files Created/Modified
- `engine/src/jarvis_engine/harvesting/__init__.py` - Package exports for all harvesting classes
- `engine/src/jarvis_engine/harvesting/providers.py` - HarvesterProvider base + MiniMax, Kimi, KimiNvidia, Gemini (250 lines)
- `engine/src/jarvis_engine/harvesting/harvester.py` - KnowledgeHarvester orchestrator, HarvestCommand, HarvestResult (170 lines)
- `engine/tests/test_harvesting_providers.py` - 13 tests covering all providers and orchestration (394 lines)
- `engine/pyproject.toml` - Added openai>=1.0.0 and google-genai>=1.0.0 dependencies

## Decisions Made
- HarvestResult dataclass placed in harvester.py (not providers.py) per plan spec; providers.py imports it to avoid circular dependency since harvester receives providers as constructor args (no reverse import)
- Tests inject mock clients directly via `provider._client = mock_client` rather than patching lazy imports (OpenAI/genai are imported inside method bodies, not at module level)
- Harvested content marked with `(confidence:0.50)` appended to text since EnrichedIngestPipeline.ingest() doesn't accept a confidence parameter directly
- GeminiProvider stands alone (no inheritance) because it uses google-genai SDK instead of OpenAI-compatible API

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed mock patching strategy for lazy imports**
- **Found during:** Task 2 (Test implementation)
- **Issue:** Tests tried to patch `jarvis_engine.harvesting.providers.OpenAI` and `providers.genai` but these don't exist at module level (lazy imports inside method bodies)
- **Fix:** Changed to direct client injection pattern: `provider._client = mock_client`
- **Files modified:** engine/tests/test_harvesting_providers.py
- **Verification:** All 13 tests pass
- **Committed in:** `5e2cb87` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Test-only fix, no impact on production code. All tests pass correctly.

## Issues Encountered
None beyond the mock patching approach documented above.

## User Setup Required
None - no external service configuration required. API keys are read from environment variables at runtime.

## Next Phase Readiness
- Harvesting package is ready for scheduling integration (05-02-PLAN)
- All provider classes work with or without API keys
- Pipeline integration tested with mock; ready for real EnrichedIngestPipeline

---
*Phase: 05-knowledge-harvesting*
*Completed: 2026-02-23*

## Self-Check: PASSED

All 5 files verified present. Both task commits (07744c6, 5e2cb87) verified in git log.
