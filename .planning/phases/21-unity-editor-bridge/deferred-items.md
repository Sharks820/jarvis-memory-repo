# Phase 21 Deferred Items

## Pre-existing xdist crash: test_write_script_enters_waiting

**File:** `engine/tests/test_unity_tool.py::TestBridgeStateMachine::test_write_script_enters_waiting`

**Discovered during:** Plan 21-01 execution (running Python test suite)

**Root cause:** `asyncio.run()` inside pytest-xdist workers crashes on Windows when the async test creates an asyncio.Event and an asyncio.Task in the same run context. The issue predates this plan — confirmed by git stash check showing same failure on commit `0e8140df`.

**Workaround:** Test passes in single-process mode: `python -m pytest engine/tests/test_unity_tool.py -x --override-ini="addopts="`

**Resolution path:** Replace `asyncio.run()` pattern in the test with the project's standard async test pattern (see MEMORY.md: "Heavy model init in tests"). Alternatively add `@pytest.mark.timeout(30)` and restructure the async mock to avoid task creation.

**Impact:** 1 test flaky under xdist on Windows. All 6072 other tests green.
