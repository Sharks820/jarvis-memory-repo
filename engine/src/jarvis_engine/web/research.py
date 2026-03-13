from __future__ import annotations

import re
from typing import TypedDict
from urllib.parse import urlparse

from jarvis_engine._constants import STOP_WORDS
from jarvis_engine._shared import now_iso
from jarvis_engine.web.fetch import (
    fetch_page_text as _fetch_page_text,
    search_web as _search_web,
)


class WebResearchResult(TypedDict):
    """Result from :func:`run_web_research`."""

    query: str
    scanned_url_count: int
    scanned_urls: list[str]
    finding_count: int
    findings: list[dict[str, str]]
    summary_lines: list[str]
    generated_utc: str


# Backward-compatible alias — consolidated into _constants.STOP_WORDS
STOPWORDS = STOP_WORDS


def _query_keywords(query: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]{3,}", query.lower())
    return {word for word in words if word not in STOP_WORDS}


# _is_safe_public_url, _search_web, _search_duckduckgo, _fetch_page_text imported from web_fetch


def _extract_snippet(text: str, *, query: str, max_sentences: int = 2) -> str:
    keywords = _query_keywords(query)
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        clean = sentence.strip()
        if len(clean) < 40 or len(clean) > 320:
            continue
        lowered = clean.lower()
        if keywords and not any(k in lowered for k in keywords):
            continue
        out.append(clean)
        if len(out) >= max(1, max_sentences):
            break
    if not out:
        return ""
    return " ".join(out)


def run_web_research(
    query: str,
    *,
    max_results: int = 8,
    max_pages: int = 6,
    max_summary_lines: int = 6,
) -> WebResearchResult:
    cleaned_query = query.strip()[:260]
    if not cleaned_query:
        raise ValueError("query is required")
    urls = _search_web(cleaned_query, limit=max(2, min(max_results, 20)))
    findings: list[dict[str, str]] = []
    scanned_urls: list[str] = []
    for url in urls[: max(1, min(max_pages, 20))]:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain:
            continue
        scanned_urls.append(url)
        page_text = _fetch_page_text(url)
        if not page_text:
            continue
        snippet = _extract_snippet(page_text, query=cleaned_query, max_sentences=2)
        if not snippet:
            continue
        findings.append({"url": url, "domain": domain, "snippet": snippet})

    summary_lines: list[str] = []
    seen = set()
    for item in findings:
        snippet = item.get("snippet", "").strip()
        if not snippet:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", snippet.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        summary_lines.append(snippet)
        if len(summary_lines) >= max(1, max_summary_lines):
            break

    return {
        "query": cleaned_query,
        "scanned_url_count": len(scanned_urls),
        "scanned_urls": scanned_urls,
        "finding_count": len(findings),
        "findings": findings,
        "summary_lines": summary_lines,
        "generated_utc": now_iso(),
    }
