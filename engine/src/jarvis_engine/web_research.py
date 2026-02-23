from __future__ import annotations

import html
import re
import socket
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import quote_plus, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

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


# TODO: deduplicate with learning_missions.py
def _is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost":
        return False
    try:
        ip = ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
    except ValueError:
        pass
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        resolved = socket.getaddrinfo(host, parsed.port or default_port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for item in resolved:
        raw_ip = item[4][0]
        try:
            ip = ip_address(raw_ip)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _search_duckduckgo(query: str, *, limit: int) -> list[str]:
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    req = Request(
        search_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    try:
        with urlopen(req, timeout=12) as resp:  # nosec B310
            payload = resp.read(400_000)
    except OSError:
        return []
    text = payload.decode("utf-8", errors="replace")
    urls: list[str] = []
    for match in re.findall(r'href="(https?://[^"]+)"', text):
        candidate = html.unescape(match).strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if "duckduckgo.com" in parsed.netloc.lower():
            continue
        if not _is_safe_public_url(candidate):
            continue
        urls.append(candidate)
    return list(dict.fromkeys(urls))[: max(1, limit)]


def _resolve_and_check_ip(url: str) -> bool:
    """Re-resolve hostname immediately before fetch to prevent DNS rebinding."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        resolved = socket.getaddrinfo(host, parsed.port or default_port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for item in resolved:
        raw_ip = item[4][0]
        try:
            ip = ip_address(raw_ip)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Block redirects to non-public IPs to prevent redirect-based SSRF."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _is_safe_public_url(newurl):
            return None  # Block redirect to unsafe URL
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# TODO: deduplicate with learning_missions.py
def _fetch_page_text(url: str, *, max_bytes: int = 250_000) -> str:
    if not _is_safe_public_url(url):
        return ""
    # Second DNS check immediately before fetch to prevent TOCTOU / DNS rebinding
    if not _resolve_and_check_ip(url):
        return ""
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    try:
        opener = build_opener(_SafeRedirectHandler)
        with opener.open(req, timeout=12) as resp:  # nosec B310
            payload = resp.read(max_bytes)
    except (OSError, ValueError):
        return ""
    text = payload.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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
    urls = _search_duckduckgo(cleaned_query, limit=max(2, min(max_results, 20)))
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
