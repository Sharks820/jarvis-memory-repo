from __future__ import annotations

import re
from datetime import datetime
from jarvis_engine._compat import UTC
from typing import Any
from urllib.parse import urlparse

from jarvis_engine.web_fetch import (
    fetch_page_text as _fetch_page_text,
    is_safe_public_url as _is_safe_public_url,
    search_duckduckgo as _search_duckduckgo,
    search_web as _search_web,
)

STOPWORDS = {
    "about",
    "after",
    "also",
    "because",
    "between",
    "could",
    "does",
    "from",
    "have",
    "into",
    "just",
    "more",
    "other",
    "over",
    "some",
    "than",
    "that",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "your",
}


def _query_keywords(query: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]{4,}", query.lower())
    return {word for word in words if word not in STOPWORDS}


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
) -> dict[str, Any]:
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
        "generated_utc": datetime.now(UTC).isoformat(),
    }
