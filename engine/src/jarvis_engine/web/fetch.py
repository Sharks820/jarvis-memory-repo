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
import http.client
import json
import logging
import os
import re
import socket
from ipaddress import ip_address
from typing import IO
from urllib.parse import quote_plus, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

logger = logging.getLogger(__name__)

# Complete Chrome UA — truncated UAs get blocked by many sites.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Minimum chars of cleaned text for a page to be considered useful.
# Pages below this are JS-rendered shells or blocked responses.
_MIN_USEFUL_TEXT = 100


def _rewrite_reddit_url(url: str) -> str:
    """Rewrite www.reddit.com URLs to old.reddit.com for server-rendered HTML.

    Modern reddit.com is JS-rendered and returns ~34 chars via urllib.
    old.reddit.com is server-rendered and returns full content.
    """
    parsed = urlparse(url)
    if parsed.hostname in ("www.reddit.com", "reddit.com"):
        return url.replace("://www.reddit.com", "://old.reddit.com", 1).replace(
            "://reddit.com", "://old.reddit.com", 1
        )
    return url


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
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError as exc:
        logger.debug("Host is not an IP literal, resolving as hostname: %s", exc)
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        resolved = socket.getaddrinfo(
            host, parsed.port or default_port, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        logger.debug("DNS resolution failed for %s: %s", host, exc)
        return False
    for item in resolved:
        raw_ip = item[4][0]
        try:
            ip = ip_address(raw_ip)
        except ValueError:
            logger.debug("Invalid IP address %r in DNS response for %s", raw_ip, host)
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
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
        resolved = socket.getaddrinfo(
            host, parsed.port or default_port, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        logger.debug("DNS re-resolution failed for %s: %s", host, exc)
        return False
    for item in resolved:
        raw_ip = item[4][0]
        try:
            ip = ip_address(raw_ip)
        except ValueError:
            logger.debug("Invalid IP %r in DNS re-resolution for %s", raw_ip, host)
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


class SafeRedirectHandler(HTTPRedirectHandler):
    """Block redirects to non-public IPs to prevent redirect-based SSRF."""

    def redirect_request(  # type: ignore[override]
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> Request | None:
        if not is_safe_public_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SCRIPT_RE = re.compile(r"(?is)<script.*?>.*?</script>")
_STYLE_RE = re.compile(r"(?is)<style.*?>.*?</style>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

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

_HTML_CONTENT_TYPES = ("text/", "application/xhtml", "application/xml")


def _html_to_text(raw: bytes) -> str:
    """Convert raw HTML bytes to plain text with tag/script/style stripping."""
    text = raw.decode("utf-8", errors="replace")
    # Prefer proper HTML parser over regex for tag stripping (security)
    try:
        from lxml.html.clean import Cleaner  # type: ignore[import-untyped]
        from lxml.html import fromstring as _lxml_parse  # type: ignore[import-untyped]

        cleaner = Cleaner(scripts=True, javascript=True, style=True, comments=True, page_structure=False)
        doc = _lxml_parse(text)
        cleaned = cleaner.clean_html(doc)
        text = cleaned.text_content()
    except (ImportError, Exception):
        # Fallback to regex if lxml not available
        text = _SCRIPT_RE.sub(" ", text)
        text = _STYLE_RE.sub(" ", text)
        text = _TAG_RE.sub(" ", text)
        text = html_mod.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _fetch_with_curl_cffi(url: str, max_bytes: int) -> bytes:
    """Attempt fetch using curl_cffi with Chrome TLS impersonation.

    Returns raw HTML bytes on success, or empty bytes on any failure.
    curl_cffi is optional — returns b"" immediately if not installed.
    """
    try:
        from curl_cffi import requests as curl_requests  # type: ignore[import-untyped]
    except ImportError:
        return b""
    try:
        response = curl_requests.get(
            url,
            headers=_BROWSER_HEADERS,
            timeout=15,
            impersonate="chrome",
            allow_redirects=True,
        )
        # Check for redirect to unsafe destination
        final_url = str(response.url) if response.url else url
        if final_url != url and not is_safe_public_url(final_url):
            logger.warning("curl_cffi: redirect to unsafe URL blocked: %s -> %s", url, final_url)
            return b""
        if response.status_code != 200:
            logger.debug("curl_cffi: non-200 status %d for %s", response.status_code, url)
            return b""
        raw_ct = response.headers.get("Content-Type") if response.headers else None
        content_type = raw_ct.lower() if isinstance(raw_ct, str) else ""
        if content_type and not any(t in content_type for t in _HTML_CONTENT_TYPES):
            logger.debug("curl_cffi: non-HTML content-type %r for %s", content_type, url)
            return b""
        return response.content[:max_bytes]
    except Exception as exc:  # nosec B110
        logger.debug("curl_cffi fetch failed for %s: %s", url, exc)
        return b""


def _fetch_with_httpx(url: str, max_bytes: int) -> bytes:
    """Attempt fetch using httpx with HTTP/2 support.

    Returns raw HTML bytes on success, or empty bytes on any failure.
    httpx is optional — returns b"" immediately if not installed.
    """
    try:
        import httpx  # type: ignore[import-untyped]
    except ImportError:
        return b""
    try:
        with httpx.Client(http2=True, follow_redirects=True, timeout=15.0) as client:
            response = client.get(url, headers=_BROWSER_HEADERS)
            # Check for redirect to unsafe destination
            final_url = str(response.url) if response.url else url
            if final_url != url and not is_safe_public_url(final_url):
                logger.warning("httpx: redirect to unsafe URL blocked: %s -> %s", url, final_url)
                return b""
            if response.status_code != 200:
                logger.debug("httpx: non-200 status %d for %s", response.status_code, url)
                return b""
            raw_ct = response.headers.get("content-type") if response.headers else None
            content_type = raw_ct.lower() if isinstance(raw_ct, str) else ""
            if content_type and not any(t in content_type for t in _HTML_CONTENT_TYPES):
                logger.debug("httpx: non-HTML content-type %r for %s", content_type, url)
                return b""
            return response.content[:max_bytes]
    except (OSError, ValueError) as exc:
        logger.debug("httpx fetch failed for %s: %s", url, exc)
        return b""
    except Exception as exc:  # nosec B110
        logger.debug("httpx fetch failed for %s: %s", url, exc)
        return b""


def fetch_page_text(url: str, *, max_bytes: int = 250_000) -> str:
    """Fetch a URL and return cleaned plain text.

    Uses a 3-tier HTTP client stack for bot-bypass:
      Tier 1: curl_cffi with Chrome TLS impersonation (bypasses Cloudflare)
      Tier 2: httpx with HTTP/2 (bypasses basic bot detection)
      Tier 3: urllib fallback (always available)

    Performs SSRF safety checks and DNS rebinding prevention before any tier.
    Strips HTML tags, scripts, and styles. Returns empty string on any failure.
    Rewrites reddit.com → old.reddit.com for server-rendered content.
    """
    url = _rewrite_reddit_url(url)
    if not is_safe_public_url(url):
        return ""
    if not resolve_and_check_ip(url):
        return ""

    # Tier 1: curl_cffi with Chrome TLS impersonation
    raw = _fetch_with_curl_cffi(url, max_bytes)
    if raw:
        text = _html_to_text(raw)
        if len(text) >= _MIN_USEFUL_TEXT:
            return text
        logger.debug("curl_cffi: insufficient text (%d chars) for %s, falling through", len(text), url)

    # Tier 2: httpx with HTTP/2
    raw = _fetch_with_httpx(url, max_bytes)
    if raw:
        text = _html_to_text(raw)
        if len(text) >= _MIN_USEFUL_TEXT:
            return text
        logger.debug("httpx: insufficient text (%d chars) for %s, falling through", len(text), url)

    # Tier 3: urllib fallback
    # NOTE: DNS TOCTOU — resolve_and_check_ip() resolves the hostname above,
    # but urllib re-resolves it independently when opening the connection.  A
    # malicious DNS server could return a safe IP here and a private IP on the
    # second resolution.  Fully closing this gap would require pinning the
    # resolved IP and connecting via IP with a Host header, which urllib does
    # not support natively.  The SafeRedirectHandler mitigates redirect-based
    # rebinding, and the short window between the two resolutions makes
    # exploitation difficult in practice.
    req = Request(
        url,
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        opener = build_opener(SafeRedirectHandler)
        with opener.open(req, timeout=12) as resp:  # nosec B310
            raw_ct = resp.headers.get("Content-Type") if resp.headers else None
            content_type = raw_ct.lower() if isinstance(raw_ct, str) else ""
            if content_type and not any(t in content_type for t in _HTML_CONTENT_TYPES):
                return ""
            payload = resp.read(max_bytes)
    except (OSError, ValueError) as exc:
        logger.debug("urllib fetch failed for %s: %s", url, exc)
        return ""
    text = _html_to_text(payload)
    if len(text) < _MIN_USEFUL_TEXT:
        logger.debug(
            "Page text too short (%d chars < %d minimum) for %s — likely JS-rendered shell",
            len(text), _MIN_USEFUL_TEXT, url,
        )
        return ""
    return text


def fetch_page_text_with_fallbacks(url: str, *, max_bytes: int = 250_000) -> str:
    """Fetch a URL with a 3-tier fallback chain.

    1. Direct fetch via ``fetch_page_text``.
    2. Google Webcache (``webcache.googleusercontent.com``).
    3. Internet Archive Wayback Machine (``web.archive.org``).

    Returns the first non-empty result, or empty string if all three fail.
    All SSRF safety checks are applied internally by ``fetch_page_text``.
    """
    # Tier 1: direct fetch
    logger.debug("fetch_page_text_with_fallbacks: trying direct fetch for %s", url)
    result = fetch_page_text(url, max_bytes=max_bytes)
    if result:
        return result

    # Tier 2: Google Webcache
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected
    # SSRF safety is enforced inside fetch_page_text (is_safe_public_url + resolve_and_check_ip).
    # The fallback URL prefix is a hardcoded public HTTPS host; only the path varies.
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
    logger.debug("fetch_page_text_with_fallbacks: direct fetch empty, trying Google Webcache for %s", url)
    result = fetch_page_text(cache_url, max_bytes=max_bytes)
    if result:
        logger.info("fetch_page_text_with_fallbacks: Google Webcache succeeded for %s", url)
        return result

    # Tier 3: archive.org Wayback Machine
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected
    archive_url = f"https://web.archive.org/web/{url}"
    logger.debug("fetch_page_text_with_fallbacks: Webcache empty, trying archive.org for %s", url)
    result = fetch_page_text(archive_url, max_bytes=max_bytes)
    if result:
        logger.info("fetch_page_text_with_fallbacks: archive.org succeeded for %s", url)
        return result

    logger.warning("fetch_page_text_with_fallbacks: all 3 fetch tiers failed for %s", url)
    return ""


def search_duckduckgo(query: str, *, limit: int) -> list[str]:
    """Search DuckDuckGo HTML and return up to *limit* safe result URLs."""
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    req = Request(
        search_url,
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        opener = build_opener(SafeRedirectHandler)
        with opener.open(req, timeout=12) as resp:  # nosec B310
            payload = resp.read(400_000)
    except OSError as exc:
        logger.debug("DuckDuckGo search request failed: %s", exc)
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
            if parsed.netloc.lower().endswith((".duckduckgo.com", "duckduckgo.com")):
                continue
            if not is_safe_public_url(candidate):
                continue
            urls.append(candidate)
    return list(dict.fromkeys(urls))[: max(1, limit)]


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
        with urlopen(req, timeout=12) as resp:  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
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

    return list(dict.fromkeys(urls))[: max(1, limit)]


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
            logger.info(
                "search_web: Brave returned no results, falling back to DuckDuckGo"
            )
        except (OSError, ValueError, TimeoutError) as exc:
            logger.warning(
                "search_web: Brave Search failed (%s), falling back to DuckDuckGo", exc
            )

    # Fallback to DuckDuckGo
    urls = search_duckduckgo(query, limit=limit)
    logger.info("search_web: used DuckDuckGo (%d results)", len(urls))
    return urls
