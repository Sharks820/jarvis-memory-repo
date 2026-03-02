"""Shared SSRF-safe web fetching utilities.

Consolidates duplicated URL safety, DNS rebinding checks, web search,
and HTML-to-text extraction from web_research.py and learning_missions.py.

Search engines:
- Brave Search API (preferred, requires BRAVE_SEARCH_API_KEY env var)
- DuckDuckGo HTML scrape (fallback, no API key needed)

Use ``search_web()`` for automatic engine selection with fallback.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import os
import re
import socket
from ipaddress import ip_address
from urllib.parse import quote_plus, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

logger = logging.getLogger(__name__)


def is_safe_public_url(url: str) -> bool:
    """Check whether *url* points to a safe, non-private destination.

    Rejects: non-HTTP(S) schemes, localhost, private/loopback/reserved IPs,
    and hostnames that resolve to private IPs.
    """
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


def resolve_and_check_ip(url: str) -> bool:
    """Re-resolve hostname immediately before fetch to prevent DNS rebinding.

    This is a second DNS check that should happen right before the actual HTTP
    connection to close the TOCTOU window between is_safe_public_url() and fetch.
    """
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


class SafeRedirectHandler(HTTPRedirectHandler):
    """Block redirects to non-public IPs to prevent redirect-based SSRF."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not is_safe_public_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SCRIPT_RE = re.compile(r"(?is)<script.*?>.*?</script>")
_STYLE_RE = re.compile(r"(?is)<style.*?>.*?</style>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def fetch_page_text(url: str, *, max_bytes: int = 250_000) -> str:
    """Fetch a URL and return cleaned plain text.

    Performs SSRF safety checks and DNS rebinding prevention.
    Strips HTML tags, scripts, and styles. Returns empty string on any failure.
    """
    if not is_safe_public_url(url):
        return ""
    if not resolve_and_check_ip(url):
        return ""
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    try:
        opener = build_opener(SafeRedirectHandler)
        with opener.open(req, timeout=12) as resp:  # nosec B310
            raw_ct = resp.headers.get("Content-Type") if resp.headers else None
            content_type = raw_ct.lower() if isinstance(raw_ct, str) else ""
            if content_type and not any(
                t in content_type
                for t in ("text/", "application/xhtml", "application/xml")
            ):
                return ""
            payload = resp.read(max_bytes)
    except (OSError, ValueError):
        return ""
    text = payload.decode("utf-8", errors="replace")
    text = _SCRIPT_RE.sub(" ", text)
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html_mod.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def search_duckduckgo(query: str, *, limit: int) -> list[str]:
    """Search DuckDuckGo HTML and return up to *limit* safe result URLs."""
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    req = Request(
        search_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    try:
        opener = build_opener(SafeRedirectHandler)
        with opener.open(req, timeout=12) as resp:  # nosec B310
            payload = resp.read(400_000)
    except OSError:
        return []
    text = payload.decode("utf-8", errors="replace")
    urls: list[str] = []
    # DDG returns redirect links: //duckduckgo.com/l/?uddg=<encoded_url>&rut=...
    # Extract target URLs from the uddg= parameter first (primary method)
    from urllib.parse import unquote
    for uddg_match in re.findall(r'uddg=([^&"]+)', text):
        candidate = html_mod.unescape(unquote(uddg_match)).strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if not is_safe_public_url(candidate):
            continue
        urls.append(candidate)
    # Fallback: also check for direct https:// hrefs (some DDG responses vary)
    if not urls:
        for match in re.findall(r'href="(https?://[^"]+)"', text):
            candidate = html_mod.unescape(match).strip()
            parsed = urlparse(candidate)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if "duckduckgo.com" in parsed.netloc.lower():
                continue
            if not is_safe_public_url(candidate):
                continue
            urls.append(candidate)
    return list(dict.fromkeys(urls))[:max(1, limit)]


def search_brave(query: str, *, limit: int) -> list[str]:
    """Search via the Brave Search API and return up to *limit* safe result URLs.

    Requires the ``BRAVE_SEARCH_API_KEY`` environment variable.  Uses only
    ``urllib.request`` (no extra dependencies).  Returns an empty list on any
    failure (missing key, HTTP error, malformed response).
    """
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        return []

    search_url = (
        f"https://api.search.brave.com/res/v1/web/search"
        f"?q={quote_plus(query)}&count={max(1, limit)}"
    )
    req = Request(
        search_url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "X-Subscription-Token": api_key,
        },
    )
    try:
        with urlopen(req, timeout=12) as resp:  # nosec B310
            payload = resp.read(500_000)
    except (OSError, ValueError) as exc:
        logger.warning("Brave Search request failed: %s", exc)
        return []

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Brave Search returned invalid JSON: %s", exc)
        return []

    results = data.get("web", {}).get("results", [])
    urls: list[str] = []
    for item in results:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if not is_safe_public_url(url):
            continue
        urls.append(url)

    return list(dict.fromkeys(urls))[:max(1, limit)]


def search_web(query: str, *, limit: int) -> list[str]:
    """Unified web search: tries Brave first, falls back to DuckDuckGo.

    Returns up to *limit* safe result URLs using the same format as
    ``search_duckduckgo()`` and ``search_brave()``.

    Engine selection:
    - If ``BRAVE_SEARCH_API_KEY`` is set, Brave is tried first.
    - If Brave returns results, they are used directly.
    - Otherwise (no key, empty results, or error), DuckDuckGo is used.
    """
    # Try Brave first if an API key is available
    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if brave_key:
        try:
            urls = search_brave(query, limit=limit)
            if urls:
                logger.info("search_web: used Brave Search (%d results)", len(urls))
                return urls
            logger.info("search_web: Brave returned no results, falling back to DuckDuckGo")
        except Exception as exc:
            logger.warning("search_web: Brave Search failed (%s), falling back to DuckDuckGo", exc)

    # Fallback to DuckDuckGo
    urls = search_duckduckgo(query, limit=limit)
    logger.info("search_web: used DuckDuckGo (%d results)", len(urls))
    return urls
