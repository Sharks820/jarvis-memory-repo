---
phase: quick-2
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - engine/src/jarvis_engine/web/fetch.py
  - engine/tests/test_web_fetch.py
autonomous: true
requirements: [QUICK-2]

must_haves:
  truths:
    - "fetch_page_text tries curl_cffi with Chrome TLS impersonation first"
    - "fetch_page_text falls back to httpx HTTP/2 when curl_cffi fails or returns too little text"
    - "fetch_page_text falls back to urllib when both curl_cffi and httpx fail"
    - "SSRF safety checks run before every fetch attempt (curl_cffi, httpx, urllib)"
    - "Redirect targets are validated against private IPs for all three clients"
    - "curl_cffi and httpx are optional — graceful degradation to urllib if not installed"
    - "Existing function signature fetch_page_text(url, *, max_bytes=250_000) -> str unchanged"
    - "All existing tests pass without modification"
  artifacts:
    - path: "engine/src/jarvis_engine/web/fetch.py"
      provides: "Multi-tier fetch with curl_cffi, httpx, urllib chain"
      contains: "_BROWSER_HEADERS"
    - path: "engine/tests/test_web_fetch.py"
      provides: "Tests for curl_cffi and httpx fetch tiers"
      contains: "test_curl_cffi"
  key_links:
    - from: "engine/src/jarvis_engine/web/fetch.py"
      to: "curl_cffi"
      via: "optional import with try/except"
      pattern: "curl_cffi"
    - from: "engine/src/jarvis_engine/web/fetch.py"
      to: "httpx"
      via: "optional import with try/except"
      pattern: "httpx"
    - from: "engine/src/jarvis_engine/web/fetch.py"
      to: "is_safe_public_url"
      via: "SSRF check before each tier"
      pattern: "is_safe_public_url|resolve_and_check_ip"
---

<objective>
Replace the single-tier urllib fetcher in fetch.py with a 3-tier HTTP client stack (curl_cffi -> httpx -> urllib) that bypasses bot-blocking on sites like Medium, npmjs, LinkedIn, and Reddit.

Purpose: Learning missions currently fail to fetch content from most modern websites because urllib gets blocked by Cloudflare and similar bot detection. The multi-tier approach uses Chrome TLS fingerprint impersonation (curl_cffi) and HTTP/2 (httpx) to get through these defenses.

Output: Updated fetch.py with multi-tier fetching, new tests covering the curl_cffi and httpx tiers.
</objective>

<execution_context>
@C:/Users/Conner/.claude/get-shit-done/workflows/execute-plan.md
@C:/Users/Conner/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@engine/src/jarvis_engine/web/fetch.py
@engine/tests/test_web_fetch.py
@engine/src/jarvis_engine/learning/missions.py (calls fetch_page_text_with_fallbacks)
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add multi-tier fetch internals to fetch.py</name>
  <files>engine/src/jarvis_engine/web/fetch.py</files>
  <action>
Modify `engine/src/jarvis_engine/web/fetch.py` to add the multi-tier fetch chain inside `fetch_page_text()`. The existing function signature MUST NOT change: `fetch_page_text(url: str, *, max_bytes: int = 250_000) -> str`.

1. Add `_BROWSER_HEADERS` dict at module level (after `_USER_AGENT`):
```python
_BROWSER_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
```

2. Add two private helper functions that each attempt a single fetch and return raw HTML bytes or empty bytes on failure:

`_fetch_with_curl_cffi(url: str, max_bytes: int) -> bytes`:
- Try `from curl_cffi import requests as curl_requests` at function scope (lazy import)
- If ImportError, return `b""` immediately
- Call `curl_requests.get(url, headers=_BROWSER_HEADERS, timeout=15, impersonate="chrome", allow_redirects=True, max_recv_speed=0)`
- SSRF on redirects: After the request completes, check `response.url` (the final URL after redirects). If `response.url != url`, call `is_safe_public_url(response.url)` — if False, return `b""` and log a warning about redirect to unsafe URL.
- Check `response.status_code == 200`, check content-type is text/html/xhtml/xml (same logic as urllib path)
- Return `response.content[:max_bytes]`
- Catch `Exception` broadly (curl_cffi can raise various errors) — log debug, return `b""`

`_fetch_with_httpx(url: str, max_bytes: int) -> bytes`:
- Try `import httpx` at function scope (lazy import)
- If ImportError, return `b""` immediately
- Create an `httpx.Client(http2=True, follow_redirects=True, timeout=15.0)` in a with-block
- Set headers to `_BROWSER_HEADERS`
- Use an `httpx.EventHook` or check `response.url` after completion — if the final URL differs from the original, call `is_safe_public_url(str(response.url))` and return `b""` if unsafe
- Check status 200, content-type is text/html/xhtml/xml
- Return `response.content[:max_bytes]`
- Catch `(httpx.HTTPError, OSError, ValueError)` — log debug, return `b""`

3. Refactor `fetch_page_text()` to use a 3-tier chain:
- Keep the existing Reddit URL rewrite and SSRF checks at the top (unchanged)
- Extract the HTML-to-text cleaning logic into a private `_html_to_text(raw: bytes) -> str` helper (the lxml/regex cleanup + whitespace collapse that currently lives inside fetch_page_text). This avoids duplicating the cleaning code.
- Tier 1: Call `_fetch_with_curl_cffi(url, max_bytes)`. If non-empty bytes returned, clean via `_html_to_text()`. If cleaned text >= `_MIN_USEFUL_TEXT`, return it.
- Tier 2: Call `_fetch_with_httpx(url, max_bytes)`. Same clean + check.
- Tier 3: Existing urllib fetch (keep `build_opener(SafeRedirectHandler)` path as-is). Same clean + check.
- If all three return insufficient text, log debug and return `""`.

CRITICAL CONSTRAINTS:
- The `build_opener` call MUST remain in the urllib tier — existing tests mock `build_opener` at `jarvis_engine.web.fetch.build_opener`.
- `is_safe_public_url()` and `resolve_and_check_ip()` are called ONCE at the top of `fetch_page_text()` (before any tier), not per-tier. The per-tier redirect safety checks are ADDITIONAL checks on the final redirect destination only.
- `_rewrite_reddit_url()` is called ONCE at the top, before all tiers.
- Thread safety: curl_cffi and httpx clients are created per-call (not module-level singletons) to avoid thread issues with the 4-worker ThreadPoolExecutor in missions.py.

Do NOT modify `fetch_page_text_with_fallbacks()`, `search_web()`, `search_duckduckgo()`, `search_brave()`, or any SSRF safety functions.
  </action>
  <verify>
    <automated>cd C:/Users/Conner/jarvis-memory-repo && python -m pytest engine/tests/test_web_fetch.py -x -q</automated>
  </verify>
  <done>
- fetch_page_text() uses 3-tier chain: curl_cffi -> httpx -> urllib
- _BROWSER_HEADERS constant defined with full Chrome headers
- _fetch_with_curl_cffi() and _fetch_with_httpx() are private helpers with lazy imports
- _html_to_text() extracts the shared cleaning logic
- SSRF checks remain at top of fetch_page_text, plus redirect checks in each tier
- All existing tests pass (they mock build_opener which still exists in the urllib tier)
- curl_cffi/httpx import failures gracefully skip to next tier
  </done>
</task>

<task type="auto">
  <name>Task 2: Add tests for curl_cffi and httpx fetch tiers</name>
  <files>engine/tests/test_web_fetch.py</files>
  <action>
Add new test classes to `engine/tests/test_web_fetch.py` covering the curl_cffi and httpx tiers. All network calls MUST be mocked.

1. **TestFetchWithCurlCffi** (tests for `_fetch_with_curl_cffi`):
- Import `_fetch_with_curl_cffi` from `jarvis_engine.web.fetch`
- `test_returns_content_on_success`: Mock `curl_cffi.requests.get` to return a response with status_code=200, content=b"<html>...(>100 chars of text)...</html>", headers with content-type text/html, and url=original url. Assert non-empty bytes returned.
- `test_returns_empty_on_import_error`: Patch `builtins.__import__` or use `unittest.mock.patch.dict('sys.modules', {'curl_cffi': None})` to simulate curl_cffi not installed. Assert `b""` returned.
- `test_returns_empty_on_403`: Mock response with status_code=403. Assert `b""` returned.
- `test_returns_empty_on_exception`: Mock `curl_cffi.requests.get` to raise an Exception. Assert `b""` returned.
- `test_rejects_redirect_to_private_ip`: Mock response where `response.url` is `http://192.168.1.1/admin` (different from input url). Assert `b""` returned (redirect SSRF protection).
- `test_respects_max_bytes`: Mock response with 500KB content. Assert returned bytes are truncated to max_bytes.

2. **TestFetchWithHttpx** (tests for `_fetch_with_httpx`):
- Import `_fetch_with_httpx` from `jarvis_engine.web.fetch`
- `test_returns_content_on_success`: Mock `httpx.Client` context manager, mock `client.get()` returning response with status_code=200, content=bytes, headers, url matching input. Assert non-empty bytes.
- `test_returns_empty_on_import_error`: Simulate httpx not installed. Assert `b""`.
- `test_returns_empty_on_http_error`: Mock `client.get` raising `httpx.HTTPError`. Assert `b""`.
- `test_rejects_redirect_to_private_ip`: Mock response.url pointing to private IP. Assert `b""`.
- `test_uses_http2`: Verify `httpx.Client` is called with `http2=True`.

3. **TestFetchPageTextMultiTier** (integration tests for the 3-tier chain):
- `test_curl_cffi_success_skips_other_tiers`: Mock `_fetch_with_curl_cffi` to return good HTML bytes. Assert fetch_page_text returns cleaned text. Assert `_fetch_with_httpx` and `build_opener` are NOT called.
- `test_curl_cffi_empty_falls_to_httpx`: Mock curl_cffi returning `b""`, httpx returning good HTML. Assert text returned from httpx tier. Assert `build_opener` NOT called.
- `test_both_empty_falls_to_urllib`: Mock both curl_cffi and httpx returning `b""`. Mock build_opener as existing tests do. Assert urllib path used and text returned.
- `test_all_tiers_fail_returns_empty`: All three return empty/error. Assert `""` returned.
- `test_curl_cffi_short_text_falls_through`: curl_cffi returns valid HTML but with < 100 chars of cleaned text. Assert falls through to httpx.

For all mocking of the tier helpers, patch at `jarvis_engine.web.fetch._fetch_with_curl_cffi` and `jarvis_engine.web.fetch._fetch_with_httpx` for integration tests. For unit tests of the helpers themselves, mock the library imports.

IMPORTANT: Keep all existing test classes UNCHANGED. Only ADD new classes at the end of the file.
  </action>
  <verify>
    <automated>cd C:/Users/Conner/jarvis-memory-repo && python -m pytest engine/tests/test_web_fetch.py -x -q -v</automated>
  </verify>
  <done>
- New test classes TestFetchWithCurlCffi, TestFetchWithHttpx, TestFetchPageTextMultiTier added
- All new tests pass with mocked network calls
- All existing tests still pass unchanged
- Redirect SSRF protection tested for both curl_cffi and httpx
- Graceful degradation on import failure tested for both libraries
- Multi-tier fallback chain integration tested (curl_cffi -> httpx -> urllib)
  </done>
</task>

<task type="auto">
  <name>Task 3: Run full test suite and static analysis</name>
  <files>engine/src/jarvis_engine/web/fetch.py, engine/tests/test_web_fetch.py</files>
  <action>
Run the full verification suite to confirm nothing is broken:

1. Run `python -m pytest engine/tests/ -x -q` — all ~5979 tests must pass
2. Run `ruff check engine/src/jarvis_engine/web/fetch.py engine/tests/test_web_fetch.py` — must be clean
3. Run `pylint --errors-only engine/src/jarvis_engine/web/fetch.py` — must be clean
4. Run `bandit -r engine/src/jarvis_engine/web/fetch.py -ll -q` — no medium+ severity findings

If any failures, fix them before declaring done. Common issues to watch for:
- ruff may flag unused imports if curl_cffi/httpx type hints are used at module level — keep imports lazy/function-scoped
- bandit may flag the broad Exception catch in curl_cffi helper — add `# nosec B110` if needed, or narrow to specific exception types from curl_cffi
- If existing tests fail because they mock `build_opener` but the new code tries curl_cffi/httpx first, the fix is: those tests already mock `is_safe_public_url` and `resolve_and_check_ip`, and the curl_cffi/httpx helpers will fail gracefully (no real network), returning `b""`, so urllib tier should still be reached. Verify this is the case.
  </action>
  <verify>
    <automated>cd C:/Users/Conner/jarvis-memory-repo && python -m pytest engine/tests/ -x -q 2>&1 | tail -5</automated>
  </verify>
  <done>
- Full test suite passes (all ~5979 tests)
- ruff clean on modified files
- pylint --errors-only clean on fetch.py
- bandit clean (no new medium+ findings)
- No regressions in any module that imports from web.fetch
  </done>
</task>

</tasks>

<verification>
1. `python -m pytest engine/tests/test_web_fetch.py -x -q` — all fetch tests pass
2. `python -m pytest engine/tests/ -x -q` — full suite passes
3. `ruff check engine/src/jarvis_engine/web/fetch.py` — clean
4. `bandit -r engine/src/jarvis_engine/web/fetch.py -ll -q` — clean
5. Manual spot check: `python -c "from jarvis_engine.web.fetch import fetch_page_text; print(len(fetch_page_text('https://medium.com')))"` — should return >100 chars (requires curl_cffi installed)
</verification>

<success_criteria>
- fetch_page_text() uses curl_cffi -> httpx -> urllib chain with graceful fallback
- All SSRF safety checks preserved (is_safe_public_url, resolve_and_check_ip, redirect validation)
- curl_cffi and httpx are optional imports — missing libraries skip gracefully to next tier
- Function signature unchanged: fetch_page_text(url, *, max_bytes=250_000) -> str
- All existing tests pass without modification
- New tests cover both new tiers and the fallback chain
- Full test suite green, ruff clean, bandit clean
</success_criteria>

<output>
After completion, create `.planning/quick/2-comprehensive-bot-bypass-web-fetching-wi/2-SUMMARY.md`
</output>
