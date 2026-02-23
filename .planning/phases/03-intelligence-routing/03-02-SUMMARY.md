---
phase: 03-intelligence-routing
plan: 02
subsystem: api
tags: [intent-classification, embedding-routing, privacy-detection, query-routing, command-bus, ollama, anthropic]

# Dependency graph
requires:
  - phase: 03-intelligence-routing
    provides: "ModelGateway, CostTracker, GatewayResponse from Plan 01"
  - phase: 01-memory-revolution-and-architecture
    provides: "EmbeddingService for query/exemplar embeddings, CommandBus DI pattern"
provides:
  - "IntentClassifier with embedding-based routing and privacy keyword detection"
  - "RouteCommand with optional query field for intent-based routing (backward compatible)"
  - "QueryCommand/QueryResult for full LLM completions through Command Bus"
  - "QueryHandler dispatching through ModelGateway with auto-routing"
  - "Gateway components wired into create_app() with graceful degradation"
affects: [04-proactive-intelligence, 05-voice-pipeline, 06-mobile-sync]

# Tech tracking
tech-stack:
  added: [numpy]
  patterns: [embedding-centroid-classification, privacy-keyword-override, confidence-threshold-default-to-local, dual-path-routing]

key-files:
  created:
    - engine/src/jarvis_engine/gateway/classifier.py
    - engine/tests/test_gateway_classifier.py
  modified:
    - engine/src/jarvis_engine/gateway/__init__.py
    - engine/src/jarvis_engine/commands/task_commands.py
    - engine/src/jarvis_engine/handlers/task_handlers.py
    - engine/src/jarvis_engine/app.py

key-decisions:
  - "Privacy keywords always force local routing regardless of embedding similarity (privacy-safe default)"
  - "Low-confidence queries (below 0.35 threshold) default to local Ollama, not cloud (privacy-safe default)"
  - "IntentClassifier MODEL_MAP reads JARVIS_LOCAL_MODEL env var at classify() time for simple_private route"
  - "RouteHandler dual-path: query-based via IntentClassifier when query provided, legacy risk/complexity via ModelRouter otherwise"
  - "QueryHandler uses lazy imports for gateway.models to avoid import-time dependency on anthropic/ollama SDKs"
  - "Gateway wiring in create_app() wrapped in try/except for graceful degradation (no gateway = fallback handlers)"

patterns-established:
  - "Embedding centroid classification: precompute mean of exemplar embeddings per route, cosine similarity for routing"
  - "Privacy keyword override: checked before embedding similarity, forces local routing"
  - "Confidence threshold default: below 0.35 similarity defaults to local (not cloud)"
  - "Dual-path command handlers: old interface preserved, new interface added alongside"

requirements-completed: [INTL-02]

# Metrics
duration: 6min
completed: 2026-02-23
---

# Phase 3 Plan 2: Intent Classification and Command Bus Wiring Summary

**IntentClassifier routing queries to Opus/Sonnet/Ollama by embedding similarity with privacy keyword override and confidence threshold, wired into Command Bus**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-23T04:36:49Z
- **Completed:** 2026-02-23T04:43:02Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- IntentClassifier routes complex queries to Claude Opus, routine to Claude Sonnet, and private/ambiguous to local Ollama
- Privacy keywords (26 terms including calendar, medication, salary, bank, etc.) force local routing regardless of embedding similarity
- Low-confidence queries default to local routing (privacy-safe -- never sends uncertain data to cloud)
- RouteCommand backward compatible: existing risk/complexity callers unaffected, new query callers get intent-based routing
- QueryCommand enables full LLM completions through the Command Bus with auto-routing or explicit model selection
- All gateway components (ModelGateway, IntentClassifier, CostTracker) wired into create_app() with graceful degradation
- 25 gateway tests pass (14 from Plan 01 + 11 new), 235 total tests with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Create IntentClassifier and evolve RouteCommand with Command Bus wiring** - `125ce60` (feat)
2. **Task 2: Write tests for IntentClassifier routing, privacy detection, and integration** - `398261a` (test)

## Files Created/Modified
- `engine/src/jarvis_engine/gateway/classifier.py` - IntentClassifier with embedding centroid routing, privacy keywords, confidence threshold
- `engine/src/jarvis_engine/gateway/__init__.py` - Added IntentClassifier to package exports
- `engine/src/jarvis_engine/commands/task_commands.py` - Added query field to RouteCommand, new QueryCommand/QueryResult dataclasses
- `engine/src/jarvis_engine/handlers/task_handlers.py` - Updated RouteHandler with classifier support, new QueryHandler
- `engine/src/jarvis_engine/app.py` - Gateway wiring in create_app() with graceful degradation
- `engine/tests/test_gateway_classifier.py` - 11 tests for routing accuracy, privacy detection, backward compat

## Decisions Made
- Privacy keywords always force local routing regardless of embedding similarity (privacy is paramount)
- Low-confidence queries (cosine similarity below 0.35) default to local Ollama, not cloud
- IntentClassifier reads JARVIS_LOCAL_MODEL env var at classify() time for flexible local model selection
- RouteHandler uses dual-path: query-based via IntentClassifier when query provided, legacy risk/complexity via ModelRouter when not
- Gateway wiring wrapped in try/except for graceful degradation -- if gateway init fails, system continues without it
- QueryHandler fallback returns QueryResult with return_code=2 when gateway unavailable

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed mock EmbeddingService keyword collision in tests**
- **Found during:** Task 2 (test creation)
- **Issue:** Mock keyword "script" was matching inside "transcript", causing routine exemplars and queries to misroute to complex direction
- **Fix:** Replaced ambiguous keywords ("script", "code") with more specific ones ("binary search", "architectural", "threading code") that don't appear as substrings in other routes
- **Files modified:** engine/tests/test_gateway_classifier.py
- **Verification:** All 11 tests pass with correct routing
- **Committed in:** 398261a (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug in test mock)
**Impact on plan:** Test-only fix, no production code affected. No scope creep.

## Issues Encountered
None in production code. Mock keyword collision in tests resolved inline (see deviations).

## User Setup Required
None - no external service configuration required. Anthropic API key is optional (local-only mode works without it). JARVIS_LOCAL_MODEL env var is optional (defaults to qwen3:14b).

## Next Phase Readiness
- Intelligence routing is fully operational: any Jarvis interface can send queries and have them auto-routed to the optimal model
- Phase 3 complete: ModelGateway (Plan 01) + IntentClassifier (Plan 02) provide full intelligence routing
- Ready for Phase 4 (Proactive Intelligence) which can build on the routing infrastructure
- CostTracker logs all routed queries with route_reason for cost analysis

## Self-Check: PASSED

All 7 created/modified files verified present. Both task commits (125ce60, 398261a) verified in git log.

---
*Phase: 03-intelligence-routing*
*Completed: 2026-02-23*
