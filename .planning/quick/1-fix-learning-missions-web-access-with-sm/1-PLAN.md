---
phase: quick-1
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - engine/src/jarvis_engine/web/fetch.py
  - engine/src/jarvis_engine/learning/missions.py
  - engine/tests/test_web_fetch.py
  - engine/tests/test_learning_missions.py
autonomous: true
requirements: []

must_haves:
  truths:
    - "When a direct page fetch returns empty, Google Webcache and archive.org are tried before giving up"
    - "Mission queries target topic-aware high-quality sources (StackOverflow, Wikipedia, official docs)"
    - "No single domain contributes more than 3 pages to mission content"
  artifacts:
    - path: "engine/src/jarvis_engine/web/fetch.py"
      provides: "fetch_page_text_with_fallbacks function or fallback chain in fetch_page_text"
      contains: "webcache.googleusercontent.com"
    - path: "engine/src/jarvis_engine/learning/missions.py"
      provides: "Topic-aware query generation and domain diversity enforcement"
      contains: "domain_counts"
  key_links:
    - from: "engine/src/jarvis_engine/learning/missions.py"
      to: "engine/src/jarvis_engine/web/fetch.py"
      via: "_fetch_page_cached calls fetch_page_text which tries fallbacks"
      pattern: "fetch_page_text"
---

<objective>
Add smart fallback fetching (Google Webcache -> archive.org) when direct page fetches fail, improve mission query diversification to target high-quality fetchable sources, and enforce domain diversity in page selection.

Purpose: Learning missions currently fail silently when sites block scraping (JS-rendered, 403s). Instead of blocking domains, we work around blocks with cache/archive fallbacks and spread across diverse sources.
Output: Updated fetch.py with fallback chain, updated missions.py with smarter queries and domain caps.
</objective>

<execution_context>
@C:/Users/Conner/.claude/get-shit-done/workflows/execute-plan.md
@C:/Users/Conner/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@engine/src/jarvis_engine/web/fetch.py
@engine/src/jarvis_engine/learning/missions.py
@engine/tests/test_web_fetch.py
@engine/tests/test_learning_missions.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add fallback fetch chain to fetch.py</name>
  <files>engine/src/jarvis_engine/web/fetch.py, engine/tests/test_web_fetch.py</files>
  <action>
Add a `fetch_page_text_with_fallbacks(url, *, max_bytes=250_000) -> str` function to `fetch.py` that implements a 3-tier fetch strategy:

1. **Direct fetch** — call existing `fetch_page_text(url, max_bytes=max_bytes)`. If it returns non-empty text, return it immediately.

2. **Google Webcache** — construct `https://webcache.googleusercontent.com/search?q=cache:{url}` and call `fetch_page_text(cache_url, max_bytes=max_bytes)`. The cache URL is a public HTTPS URL so it will pass the existing SSRF checks. If it returns non-empty text, log at INFO level and return it.

3. **archive.org Wayback Machine** — construct `https://web.archive.org/web/{url}` and call `fetch_page_text(archive_url, max_bytes=max_bytes)`. Same SSRF safety. If it returns non-empty text, log at INFO level and return it.

4. If all three fail, return empty string and log a WARNING with the original URL.

Each fallback URL must be validated by the same `is_safe_public_url` + `resolve_and_check_ip` checks that `fetch_page_text` already applies internally — no need to duplicate, since we call `fetch_page_text` which does both checks.

Log at DEBUG level when trying each fallback so mission runs are diagnosable.

Add tests in `test_web_fetch.py`:
- Test that direct success skips fallbacks (mock `fetch_page_text` to return content on first call)
- Test that empty direct triggers Google cache attempt, and if that works, archive.org is not tried
- Test that both direct and cache fail triggers archive.org attempt
- Test that all three failing returns empty string
- Test that the fallback URLs are correctly constructed (assert the URLs passed to the underlying fetch)

Do NOT modify the existing `fetch_page_text` function — add the new function alongside it.
  </action>
  <verify>
    <automated>python -m pytest engine/tests/test_web_fetch.py -x -q</automated>
  </verify>
  <done>fetch_page_text_with_fallbacks exists, tries 3 tiers in order, all new tests pass</done>
</task>

<task type="auto">
  <name>Task 2: Improve mission query diversification and domain diversity</name>
  <files>engine/src/jarvis_engine/learning/missions.py, engine/tests/test_learning_missions.py</files>
  <action>
**Part A — Smarter `_mission_queries()`:**

Replace the current simplistic query generation with topic-aware diversification. The function should generate queries that target high-quality, reliably fetchable sources:

```python
def _mission_queries(topic: str, sources: list[str]) -> list[str]:
    queries = [topic]
    # Always include broad educational targets
    queries.append(f"{topic} tutorial")
    queries.append(f"{topic} explained site:en.wikipedia.org")
    queries.append(f"{topic} guide site:stackoverflow.com OR site:stackexchange.com")
    queries.append(f"{topic} documentation")

    # Source-specific queries (existing logic, kept)
    lowered = {s.lower().strip() for s in sources}
    if "reddit" in lowered:
        queries.append(f"site:old.reddit.com {topic}")
    if "official_docs" in lowered:
        queries.append(f"{topic} official documentation")

    # Domain-targeted queries for reliable fetchable sites
    queries.append(f"site:developer.mozilla.org {topic}")  # MDN for web/programming
    queries.append(f"site:docs.python.org {topic}")  # Python docs
    queries.append(f"{topic} overview site:medium.com OR site:dev.to")

    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))
```

Adjust the exact queries based on what makes sense — the key principle is: generate 6-10 diverse queries that target sites known to serve full HTML (Wikipedia, StackOverflow, MDN, dev.to, medium, official docs) rather than JS-rendered shells. Keep the function deterministic (no randomness).

**Part B — Domain diversity in `_fetch_mission_content()`:**

Update `_fetch_mission_content()` to enforce a maximum of 3 pages per domain. After deduplicating URLs, before selecting the top `max_pages`, filter by domain count:

```python
# After: urls = list(dict.fromkeys(urls))
# Add domain diversity enforcement:
domain_counts: dict[str, int] = {}
diverse_urls: list[str] = []
max_per_domain = 3
for url in urls:
    domain = urlparse(url).netloc.lower()
    if domain_counts.get(domain, 0) < max_per_domain:
        diverse_urls.append(url)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
urls = diverse_urls
```

This goes BEFORE the `urls[:max_pages]` slicing so the domain cap is applied first.

**Part C — Wire fallback fetching:**

In `_fetch_page_cached()`, change the internal call from `_fetch_page_text(url, max_bytes=max_bytes)` to use the new `fetch_page_text_with_fallbacks` from `web/fetch.py`. Update the import at the top of `missions.py` to include it:

```python
from jarvis_engine.web.fetch import (
    fetch_page_text_with_fallbacks as _fetch_page_text_with_fallbacks,
    fetch_page_text as _fetch_page_text,
    search_web as _search_web,
)
```

Then in `_fetch_page_cached`, replace:
```python
value = _fetch_page_text(url, max_bytes=max_bytes)
```
with:
```python
value = _fetch_page_text_with_fallbacks(url, max_bytes=max_bytes)
```

Keep the direct `_fetch_page_text` import for any other callers that need it.

**Tests:**
- Test that `_mission_queries` generates Wikipedia and StackOverflow targeted queries for any topic
- Test that `_mission_queries` still includes reddit queries when "reddit" is in sources
- Test domain diversity: given 10 URLs with 5 from the same domain, verify only 3 from that domain survive
- Test that `_fetch_page_cached` calls the fallback-enabled fetch function
  </action>
  <verify>
    <automated>python -m pytest engine/tests/test_learning_missions.py engine/tests/test_web_fetch.py -x -q</automated>
  </verify>
  <done>Mission queries target diverse high-quality sources, domain diversity enforced at max 3 per domain, fallback fetching wired into mission content pipeline</done>
</task>

</tasks>

<verification>
python -m pytest engine/tests/test_web_fetch.py engine/tests/test_learning_missions.py engine/tests/test_learning_missions_v5.py -x -q
ruff check engine/src/jarvis_engine/web/fetch.py engine/src/jarvis_engine/learning/missions.py
</verification>

<success_criteria>
- fetch_page_text_with_fallbacks tries Google Cache then archive.org when direct fetch fails
- Mission queries include Wikipedia, StackOverflow, and other reliably fetchable source targets
- No single domain contributes more than 3 pages to mission content
- All existing tests continue to pass
- No ruff violations in modified files
</success_criteria>

<output>
After completion, create `.planning/quick/1-fix-learning-missions-web-access-with-sm/1-SUMMARY.md`
</output>
