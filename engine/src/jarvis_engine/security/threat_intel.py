"""Threat intelligence feed enrichment.

Enriches IP addresses with reputation data from free APIs:
- AbuseIPDB (requires ABUSEIPDB_API_KEY)
- AlienVault OTX (requires OTX_API_KEY)
- abuse.ch Feodo Tracker blocklist (no key required)

All network calls are fault-tolerant: missing keys or network errors
produce graceful degradation, never exceptions.
"""

from __future__ import annotations

import collections
import concurrent.futures
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, TypedDict


class ThreatEnrichment(TypedDict):
    """Structured result from :meth:`ThreatIntelFeed.enrich_ip`."""

    ip: str
    abuseipdb_score: int | None
    otx_pulses: int | None
    feodo_listed: bool
    is_known_bad: bool
    cache_hit: bool
    sources_checked: list[str]


class ThreatIntelStatus(TypedDict):
    """Structured result from :meth:`ThreatIntelFeed.status`."""

    cache_size: int
    api_keys_configured: list[str]
    last_feed_update: float | None
    requests_total: int

logger = logging.getLogger(__name__)

_ABUSEIPDB_CHECK_URL = "https://api.abuseipdb.com/api/v2/check"
_OTX_INDICATOR_URL = "https://otx.alienvault.com/api/v1/indicators/IPv4"
_FEODO_BLOCKLIST_URL = (
    "https://feodotracker.abuse.ch/downloads/ipblocklist.csv"
)

# AbuseIPDB confidence score at or above this threshold marks an IP as known bad.
_ABUSEIPDB_BAD_THRESHOLD = 80

# HTTP timeout for external API calls (seconds).
_REQUEST_TIMEOUT = 10


class ThreatIntelFeed:
    """Enrich IP addresses with threat intelligence from multiple feeds.

    Parameters
    ----------
    cache_ttl:
        Time-to-live for cached enrichment results, in seconds.
        Defaults to 3600 (1 hour).
    """

    MAX_CACHE_SIZE = 10_000

    def __init__(self, cache_ttl: int = 3600) -> None:
        self._cache_ttl = cache_ttl

        # API keys (optional — graceful degrade when absent)
        self._abuseipdb_key: str | None = os.environ.get("ABUSEIPDB_API_KEY") or None
        self._otx_key: str | None = os.environ.get("OTX_API_KEY") or None

        # In-memory LRU cache: ip -> (enrichment_dict, timestamp)
        # OrderedDict for O(1) eviction of oldest entry at capacity
        self._cache: collections.OrderedDict[str, tuple[ThreatEnrichment, float]] = collections.OrderedDict()
        self._lock = threading.Lock()

        # Feodo blocklist (set of IPs), cached with its own timestamp
        self._feodo_ips: set[str] = set()
        self._feodo_last_update: float = 0.0

        # Metrics
        self._requests_total: int = 0
        self._last_feed_update: float | None = None

        # Shared thread pool for parallel feed queries (reused across calls)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)

    def close(self) -> None:
        """Shut down the internal thread pool.

        Safe to call multiple times.  After ``close()`` the instance must
        not be used for further enrichment.
        """
        self._pool.shutdown(wait=False)

    def __enter__(self) -> "ThreatIntelFeed":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_ip(self, ip: str) -> ThreatEnrichment:
        """Enrich *ip* with threat intelligence data.

        Returns a dict with keys:
            ip, abuseipdb_score, otx_pulses, feodo_listed,
            is_known_bad, cache_hit, sources_checked
        """
        with self._lock:
            cached = self._cache.get(ip)
            if cached is not None:
                data, ts = cached
                if time.time() - ts < self._cache_ttl:
                    self._cache.move_to_end(ip)  # LRU: mark as recently used
                    result = dict(data)
                    result["cache_hit"] = True
                    return result

        # Build fresh enrichment — run all applicable feeds in parallel
        abuseipdb_score: int | None = None
        otx_pulses: int | None = None
        feodo_listed = False
        sources_checked: list[str] = []
        known_bad = False

        futures: dict[str, concurrent.futures.Future[Any]] = {}
        if self._abuseipdb_key:
            futures["abuseipdb"] = self._pool.submit(self._query_abuseipdb, ip)
        if self._otx_key:
            futures["otx"] = self._pool.submit(self._query_otx, ip)
        futures["feodo"] = self._pool.submit(self._refresh_feodo_blocklist)

        # Wait for all futures to complete before collecting results
        concurrent.futures.wait(futures.values())

        # Collect results
        if "abuseipdb" in futures:
            abuseipdb_score = futures["abuseipdb"].result()
            sources_checked.append("abuseipdb")
            if abuseipdb_score is not None and abuseipdb_score >= _ABUSEIPDB_BAD_THRESHOLD:
                known_bad = True

        if "otx" in futures:
            otx_pulses = futures["otx"].result()
            sources_checked.append("otx")
            if otx_pulses is not None and otx_pulses > 0:
                known_bad = True

        # Feodo — future already completed via pool shutdown
        sources_checked.append("feodo")
        feodo_listed = ip in self._feodo_ips
        if feodo_listed:
            known_bad = True

        result: ThreatEnrichment = {
            "ip": ip,
            "abuseipdb_score": abuseipdb_score,
            "otx_pulses": otx_pulses,
            "feodo_listed": feodo_listed,
            "is_known_bad": known_bad,
            "cache_hit": False,
            "sources_checked": sources_checked,
        }

        # Store in cache (O(1) LRU eviction via OrderedDict)
        now = time.time()
        with self._lock:
            if ip in self._cache:
                self._cache.move_to_end(ip)
            elif len(self._cache) >= self.MAX_CACHE_SIZE:
                self._cache.popitem(last=False)  # evict oldest
            self._cache[ip] = (dict(result), now)
            self._requests_total += 1

        return result

    def is_known_bad(self, ip: str) -> bool:
        """Quick check whether *ip* is flagged in any threat feed."""
        data = self.enrich_ip(ip)
        return data.get("is_known_bad", False)

    def status(self) -> ThreatIntelStatus:
        """Return operational status of the threat intel feed."""
        with self._lock:
            cache_size = len(self._cache)
            requests = self._requests_total

        keys_configured: list[str] = []
        if self._abuseipdb_key:
            keys_configured.append("abuseipdb")
        if self._otx_key:
            keys_configured.append("otx")

        return {
            "cache_size": cache_size,
            "api_keys_configured": keys_configured,
            "last_feed_update": self._last_feed_update,
            "requests_total": requests,
        }

    # ------------------------------------------------------------------
    # Private — AbuseIPDB
    # ------------------------------------------------------------------

    def _query_abuseipdb(self, ip: str) -> int | None:
        """Query AbuseIPDB for *ip* and return the abuse confidence score."""
        try:
            url = f"{_ABUSEIPDB_CHECK_URL}?ipAddress={urllib.parse.quote(ip, safe='')}&maxAgeInDays=90"
            req = urllib.request.Request(url)
            req.add_header("Key", self._abuseipdb_key)  # type: ignore[arg-type]
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                body = json.loads(resp.read())

            score = body.get("data", {}).get("abuseConfidenceScore")
            return int(score) if score is not None else None
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("AbuseIPDB query failed for %s: %s", ip, exc)
            return None

    # ------------------------------------------------------------------
    # Private — AlienVault OTX
    # ------------------------------------------------------------------

    def _query_otx(self, ip: str) -> int | None:
        """Query OTX for *ip* and return the number of pulses."""
        try:
            url = f"{_OTX_INDICATOR_URL}/{urllib.parse.quote(ip, safe='')}/general"
            req = urllib.request.Request(url)
            req.add_header("X-OTX-API-KEY", self._otx_key)  # type: ignore[arg-type]
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                body = json.loads(resp.read())

            pulse_info = body.get("pulse_info", {})
            count = pulse_info.get("count")
            return int(count) if count is not None else None
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("OTX query failed for %s: %s", ip, exc)
            return None

    # ------------------------------------------------------------------
    # Private — Feodo Tracker blocklist
    # ------------------------------------------------------------------

    def _refresh_feodo_blocklist(self) -> None:
        """Download and cache the Feodo Tracker IP blocklist.

        Re-downloads only if the cached copy is older than the cache TTL.
        """
        now = time.time()
        with self._lock:
            if self._feodo_ips and (now - self._feodo_last_update < self._cache_ttl):
                return  # still fresh

        try:
            req = urllib.request.Request(_FEODO_BLOCKLIST_URL)
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")

            ips: set[str] = set()
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Each line is either a bare IP or comma-separated fields;
                # take the first token.
                token = line.split(",")[0].strip()
                if token:
                    ips.add(token)

            with self._lock:
                self._feodo_ips = ips
                self._feodo_last_update = now
                self._last_feed_update = now

            logger.info("Feodo blocklist refreshed: %d IPs", len(ips))
        except (OSError, TimeoutError, ValueError) as exc:
            logger.debug("Feodo blocklist download failed: %s", exc)
