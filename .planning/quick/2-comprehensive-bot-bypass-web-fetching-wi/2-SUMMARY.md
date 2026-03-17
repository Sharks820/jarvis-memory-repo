---
phase: quick-2
plan: "01"
subsystem: web
tags: [web-fetch, bot-bypass, curl_cffi, httpx, ssrf, tls-impersonation]
dependency_graph:
  requires: [engine/src/jarvis_engine/web/fetch.py]
  provides: [multi-tier HTTP fetch with Chrome TLS impersonation]
  affects: [learning/missions.py, web/fetch.py]
tech_stack:
  added: [curl_cffi (optional), httpx (optional, http2)]
  patterns: [lazy import, per-call client instantiation, tier fallback chain]
key_files:
  modified:
    - engine/src/jarvis_engine/web/fetch.py
    - engine/tests/test_web_fetch.py
decisions:
  - "curl_cffi and httpx imported lazily at function scope to keep them optional and avoid import-time failures"
  - "Clients created per-call (not module globals) for thread safety with 4-worker ThreadPoolExecutor in missions.py"
  - "SSRF checks run once at top of fetch_page_text before any tier; each tier additionally checks redirect destination"
  - "nosec B110 annotations removed after bandit confirmed they were stale/unused"
metrics:
  duration: "~15 minutes"
  completed: "2026-03-16"
  tasks_completed: 3
  files_modified: 2
  tests_added: 20
  tests_total: 95
---

# Phase quick-2 Plan 01: Comprehensive Bot-Bypass Web Fetching Summary

**One-liner:** 3-tier Chrome TLS impersonation (curl_cffi) + HTTP/2 (httpx) + urllib chain replacing single-tier urllib fetch to bypass Cloudflare and bot detection on Medium, npmjs, LinkedIn, Reddit.

## What Was Built

Replaced the single-tier urllib fetcher in `fetch_page_text()` with a 3-tier HTTP client stack that gracefully falls through to the next tier when each fails or returns insufficient content:

- **Tier 1:** `_fetch_with_curl_cffi()` — uses curl_cffi with `impersonate="chrome"` for Chrome TLS fingerprint spoofing (bypasses Cloudflare, most bot detectors). Optional import.
- **Tier 2:** `_fetch_with_httpx()` — uses httpx with `http2=True` for HTTP/2 connections (bypasses basic bot detection). Optional import.
- **Tier 3:** Original urllib path — `build_opener(SafeRedirectHandler)` unchanged for backward compatibility.

Additional changes:
- `_BROWSER_HEADERS` dict added at module level with full Chrome header set
- `_html_to_text(raw: bytes) -> str` helper extracted from `fetch_page_text()` to avoid duplicating HTML cleaning logic across 3 tiers
- `_HTML_CONTENT_TYPES` constant extracted from inline logic

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add multi-tier fetch internals | fd7e8059 | fetch.py |
| 2 | Add tests for new tiers | 33298435 | test_web_fetch.py |
| 3 | Static analysis cleanup | 5740b387 | fetch.py |

## Test Results

- **95 tests passing** in test_web_fetch.py (75 existing + 20 new)
- Full suite: **5951 passed, 6 skipped** (excluding pre-existing flaky test)
- ruff: clean on both modified files
- pylint --errors-only: clean
- bandit: clean (no medium+ findings)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed stale nosec B110 annotations**
- **Found during:** Task 3 (bandit static analysis)
- **Issue:** Added `# nosec B110` to two broad `except Exception` catches in curl_cffi/httpx helpers, but bandit did not flag those lines as B110 violations — triggering bandit warnings about unused nosec annotations
- **Fix:** Removed the stale `# nosec B110` comments; the broad catches are needed for curl_cffi which can raise various opaque exception types
- **Files modified:** engine/src/jarvis_engine/web/fetch.py
- **Commit:** 5740b387

### Pre-existing Failure (Out of Scope)

`test_daemon_reliability.py::TestLearningMissionPerformance::test_mission_uses_parallel_fetching` was already failing before this task (confirmed by git stash test). The test patches `learning_missions._fetch_page_text` but `run_learning_mission` calls `_fetch_page_text_with_fallbacks` — a different function — so the mock is never hit and all external fallbacks fail. This is a bug in the test written in quick task 1; not introduced by this task.

## SSRF Safety Preserved

All SSRF safety mechanisms are intact:
- `is_safe_public_url()` + `resolve_and_check_ip()` called once at top of `fetch_page_text()` before any tier
- Per-tier redirect safety: each helper checks `response.url` against `is_safe_public_url()` if URL changed
- `SafeRedirectHandler` still active in the urllib tier
- Thread-safe: curl_cffi sessions and httpx clients created per-call

## Self-Check: PASSED

Files verified to exist:
- engine/src/jarvis_engine/web/fetch.py — FOUND
- engine/tests/test_web_fetch.py — FOUND

Commits verified:
- fd7e8059 feat(quick-2-01): add 3-tier curl_cffi/httpx/urllib... — FOUND
- 33298435 test(quick-2-01): add tests for curl_cffi... — FOUND
- 5740b387 fix(quick-2-01): remove stale nosec B110... — FOUND
