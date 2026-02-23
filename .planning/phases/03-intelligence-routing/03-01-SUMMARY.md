---
phase: 03-intelligence-routing
plan: 01
subsystem: api
tags: [anthropic, ollama, llm-gateway, cost-tracking, sqlite, fallback-chain]

# Dependency graph
requires:
  - phase: 01-memory-revolution-and-architecture
    provides: "SQLite WAL-mode pattern, threading.Lock write serialization, _init_schema pattern"
provides:
  - "ModelGateway class with complete() dispatching to Anthropic or Ollama"
  - "GatewayResponse dataclass for unified completion responses"
  - "CostTracker with SQLite query_costs table and per-model summaries"
  - "Pricing table with calculate_cost() for Anthropic models"
  - "Automatic fallback chain: cloud failure -> local Ollama -> graceful error"
  - "Local-only mode when no API key configured"
affects: [03-intelligence-routing, 04-proactive-intelligence]

# Tech tracking
tech-stack:
  added: [anthropic>=0.81.0, ollama>=0.4.0]
  patterns: [provider-dispatch-by-model-prefix, automatic-fallback-chain, per-query-cost-logging]

key-files:
  created:
    - engine/src/jarvis_engine/gateway/__init__.py
    - engine/src/jarvis_engine/gateway/models.py
    - engine/src/jarvis_engine/gateway/costs.py
    - engine/src/jarvis_engine/gateway/pricing.py
    - engine/tests/test_gateway.py
  modified:
    - engine/pyproject.toml

key-decisions:
  - "GatewayResponse is a non-frozen dataclass for mutability (fallback sets fields after creation)"
  - "Provider resolution uses model prefix startswith() matching (claude-* -> Anthropic, everything else -> Ollama)"
  - "Fallback model configurable via JARVIS_LOCAL_MODEL env var, defaults to qwen3:14b"
  - "CostTracker uses WAL mode + threading.Lock (same pattern as MemoryEngine)"
  - "Imports of anthropic/ollama SDKs happen in models.py (not __init__.py) so package remains importable if SDKs missing at module scan time"

patterns-established:
  - "Provider dispatch: _resolve_provider() determines backend by model name prefix"
  - "Fallback chain: try cloud -> catch API errors -> try local -> catch connection errors -> graceful empty response"
  - "Cost logging: every completion logs to SQLite via CostTracker.log() with auto-calculation from pricing table"

requirements-completed: [INTL-01, INTL-03, INTL-04]

# Metrics
duration: 4min
completed: 2026-02-23
---

# Phase 3 Plan 1: Model Gateway Foundation Summary

**Unified ModelGateway wrapping Anthropic SDK and Ollama client with automatic fallback chains, per-query SQLite cost tracking, and local-only mode**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-23T04:28:01Z
- **Completed:** 2026-02-23T04:32:23Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- ModelGateway.complete() dispatches to Anthropic or Ollama based on model name prefix
- Automatic fallback chain: Anthropic failure -> local Ollama -> graceful error response with full reason chain
- CostTracker logs every completion to SQLite query_costs table with auto-calculated USD costs from pricing table
- 14 passing tests covering all gateway behaviors including mocked SDK calls, fallback scenarios, cost logging, and pricing
- 224 total tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Create gateway package with ModelGateway, CostTracker, and pricing** - `5c9cb59` (feat)
2. **Task 2: Write tests for ModelGateway, CostTracker, and fallback behavior** - `9438844` (test)

## Files Created/Modified
- `engine/pyproject.toml` - Added anthropic>=0.81.0 and ollama>=0.4.0 dependencies
- `engine/src/jarvis_engine/gateway/__init__.py` - Package init exporting ModelGateway, GatewayResponse, CostTracker
- `engine/src/jarvis_engine/gateway/models.py` - ModelGateway with complete(), _call_anthropic(), _call_ollama(), _fallback_to_ollama(), check_ollama(), check_anthropic(); GatewayResponse dataclass
- `engine/src/jarvis_engine/gateway/costs.py` - CostTracker with SQLite WAL-mode DB, log(), summary(), _init_schema()
- `engine/src/jarvis_engine/gateway/pricing.py` - PRICING dict and calculate_cost() for Anthropic model pricing
- `engine/tests/test_gateway.py` - 14 tests covering CostTracker (4), GatewayResponse (1), ModelGateway (6), Pricing (3)

## Decisions Made
- GatewayResponse is a non-frozen dataclass to allow mutation during fallback flow
- Provider resolution uses startswith() prefix matching: claude-* goes to Anthropic, everything else to Ollama
- Fallback model defaults to qwen3:14b but is configurable via JARVIS_LOCAL_MODEL env var
- CostTracker follows the MemoryEngine pattern: WAL mode, busy_timeout=5000, threading.Lock for write serialization
- SDK imports (anthropic, ollama) live in models.py, not __init__.py, so the gateway package can be imported even if SDKs are not installed

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required. Anthropic API key is optional (local-only mode works without it).

## Next Phase Readiness
- Gateway foundation is ready for intelligence routing logic (Plan 02: router, complexity scoring, model selection)
- CostTracker is ready to receive routing metadata (route_reason field)
- Fallback chain is operational for resilient LLM access

## Self-Check: PASSED

All 6 created files verified present. Both task commits (5c9cb59, 9438844) verified in git log.

---
*Phase: 03-intelligence-routing*
*Completed: 2026-02-23*
