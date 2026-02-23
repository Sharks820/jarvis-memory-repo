---
phase: 04-connectors-and-daily-intelligence
plan: 01
subsystem: connectors
tags: [icalendar, recurring-ical-events, ics-parsing, todoist, task-source, calendar]

# Dependency graph
requires:
  - phase: 01-memory-revolution-and-architecture
    provides: "Command Bus, ops_sync.py, connectors.py framework"
provides:
  - "RFC 5545 ICS parsing with RRULE recurring event expansion via icalendar library"
  - "Date-filtered calendar event loading (target_date parameter)"
  - "Pluggable task source abstraction (JSON, Todoist, Google Tasks)"
  - "Graceful degradation to fallback parser when icalendar unavailable"
affects: [04-02-daily-intelligence-briefing, calendar-connector, task-connector]

# Tech tracking
tech-stack:
  added: [icalendar>=7.0.1, recurring-ical-events>=3.8.1, todoist-api-python (optional)]
  patterns: [lazy-import-with-fallback, env-var-source-selection, ics-date-range-filtering]

key-files:
  created:
    - engine/tests/test_calendar_tasks.py
  modified:
    - engine/pyproject.toml
    - engine/src/jarvis_engine/ops_sync.py
    - engine/src/jarvis_engine/connectors.py

key-decisions:
  - "Lazy import icalendar inside _parse_ics() with fallback to line-by-line parser for graceful degradation"
  - "UTC-based date range for recurring event expansion (midnight-to-midnight UTC)"
  - "Task source selection via JARVIS_TASK_SOURCE env var with default json, todoist, and google_tasks options"
  - "Google Tasks returns empty list with TODO comment -- requires OAuth2, deferred to future phase"

patterns-established:
  - "Lazy import with fallback: try/except ImportError around library-dependent code, fallback to simpler implementation"
  - "Source abstraction via env var: JARVIS_TASK_SOURCE selects which backend to use, new backends added as elif branches"

requirements-completed: [CONN-01, CONN-03]

# Metrics
duration: 5min
completed: 2026-02-23
---

# Phase 4 Plan 1: Calendar & Task Connector Upgrade Summary

**RFC 5545 ICS parsing with icalendar library for RRULE expansion, date filtering, and pluggable task source abstraction supporting JSON/Todoist/Google Tasks**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-23T05:48:02Z
- **Completed:** 2026-02-23T05:52:41Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Replaced hand-rolled ICS parser with icalendar + recurring-ical-events for full RFC 5545 support (RRULE expansion, VTIMEZONE, property folding)
- Added date-filtered calendar loading with target_date parameter for querying events on specific days
- Created pluggable task source layer with env-var selection (JSON default, Todoist API, Google Tasks stub)
- Added 17 comprehensive tests covering ICS parsing, calendar loading, task sources, and fallback behavior

## Task Commits

Each task was committed atomically:

1. **Task 1: Add icalendar dependencies and upgrade ICS parsing** - `2e4efa3` (feat)
2. **Task 2: Update connector definitions and write comprehensive tests** - `5d66f8a` (test)

## Files Created/Modified
- `engine/pyproject.toml` - Added icalendar>=7.0.1, recurring-ical-events>=3.8.1 dependencies and optional todoist-api-python group
- `engine/src/jarvis_engine/ops_sync.py` - Replaced _parse_ics() with icalendar-based parser, added load_task_items() and _load_todoist_tasks(), updated build_live_snapshot() to use task abstraction
- `engine/src/jarvis_engine/connectors.py` - Added JARVIS_TASK_SOURCE and JARVIS_TODOIST_TOKEN to tasks ConnectorDefinition required_any_env
- `engine/tests/test_calendar_tasks.py` - 17 tests for ICS parsing (simple, all-day, recurring, out-of-range, fallback, empty), calendar loading (JSON, ICS file, no config), task sources (default JSON, env path, Todoist without token, Google Tasks stub, missing file), and fallback parser

## Decisions Made
- Used lazy imports inside _parse_ics() (not at module level) so icalendar is only required when ICS parsing is actually triggered -- keeps the module importable even without the library
- UTC-based datetime range for recurring event expansion to avoid timezone ambiguity in the between() call
- Kept _parse_ics_fallback() as the old line-by-line parser for environments where icalendar cannot be installed
- Google Tasks source returns empty list with TODO comment rather than raising errors -- OAuth2 integration deferred to a future phase
- DTEND computed as 1 hour after DTSTART in test helpers to avoid confusing the recurring events library with invalid time ranges

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed ICS test helper generating invalid DTEND**
- **Found during:** Task 2 (test writing)
- **Issue:** The _simple_event_ics() test helper hardcoded DTEND as `{dtstart[:8]}T100000Z` regardless of the actual DTSTART hour, producing events where DTEND was before DTSTART (e.g., start at 14:00, end at 10:00)
- **Fix:** Computed DTEND dynamically as 1 hour after DTSTART
- **Files modified:** engine/tests/test_calendar_tasks.py
- **Verification:** All 17 tests pass including the previously failing ICS file test
- **Committed in:** 5d66f8a (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Test helper fix only, no impact on production code.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Calendar connector now supports real ICS files with recurring events -- ready for daily briefing integration (Plan 02)
- Task source abstraction in place -- ready for Todoist API key configuration when user sets up the integration
- All existing tests pass with zero regressions

## Self-Check: PASSED

- FOUND: engine/pyproject.toml
- FOUND: engine/src/jarvis_engine/ops_sync.py
- FOUND: engine/src/jarvis_engine/connectors.py
- FOUND: engine/tests/test_calendar_tasks.py
- FOUND: .planning/phases/04-connectors-and-daily-intelligence/04-01-SUMMARY.md
- FOUND: commit 2e4efa3 (Task 1)
- FOUND: commit 5d66f8a (Task 2)

---
*Phase: 04-connectors-and-daily-intelligence*
*Completed: 2026-02-23*
