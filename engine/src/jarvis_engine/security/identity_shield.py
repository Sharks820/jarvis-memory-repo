"""Identity and family protection — breach monitoring, typosquat detection,
impersonation detection, and family member registry.

Provides:
- **FamilyShield**: Registry of family members (names, emails, usernames, domains)
  persisted as JSON.
- **BreachMonitor**: HaveIBeenPwned integration (email breach lookup + k-anonymity
  password checking).
- **TyposquatMonitor**: Generates typosquat domain variants (omission, adjacent-key,
  doubling, homoglyph, TLD swap) and checks DNS registration.
- **ImpersonationDetector**: Generates username impersonation variants and checks
  public profile existence on major platforms.
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from jarvis_engine._shared import atomic_write_json
from pathlib import Path
from typing import TypedDict

try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _HAS_THREADPOOL = True
except ImportError:  # pragma: no cover
    _HAS_THREADPOOL = False

logger = logging.getLogger(__name__)


def _sha1_hexdigest_not_for_security(text: str) -> str:
    """Return the HIBP-compatible SHA-1 digest without declaring security use."""
    data = text.encode("utf-8")
    digest = hashlib.sha1(data, usedforsecurity=False)
    return digest.hexdigest().upper()


def _validated_https_url(url: str) -> str:
    """Allow only absolute HTTPS URLs for remote identity-shield requests."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Only absolute https URLs are allowed, got {url!r}")
    return url


class FamilyShieldStatus(TypedDict):
    """Result from :meth:`FamilyShield.status`."""

    module: str
    member_count: int
    total_emails: int
    total_usernames: int
    total_domains: int


class PasswordCheckResult(TypedDict):
    """Result from :meth:`BreachMonitor.check_password`."""

    compromised: bool
    count: int


class BreachMonitorStatus(TypedDict):
    """Result from :meth:`BreachMonitor.status`."""

    module: str
    api_key_configured: bool


class TyposquatMonitorStatus(TypedDict):
    """Result from :meth:`TyposquatMonitor.status`."""

    module: str


class PlatformCheckResult(TypedDict):
    """Result from :meth:`ImpersonationDetector.check_platform`."""

    platform: str
    username: str
    exists: bool
    url: str


class ImpersonationDetectorStatus(TypedDict):
    """Result from :meth:`ImpersonationDetector.status`."""

    module: str
    supported_platforms: list[str]


class BreachRecord(TypedDict):
    """Single breach entry returned by :meth:`BreachMonitor.check_email`."""

    name: str
    breach_date: str
    data_classes: list[str]


class BreachLookupResult(TypedDict):
    """Single DNS lookup result from :meth:`TyposquatMonitor.check_domain`."""

    variant: str
    registered: bool
    ips: list[str]


# Adjacent-key map (QWERTY layout)

_ADJACENT_KEYS: dict[str, str] = {
    "q": "wa",
    "w": "qeas",
    "e": "wrds",
    "r": "etdf",
    "t": "ryfg",
    "y": "tugh",
    "u": "yijh",
    "i": "uojk",
    "o": "iplk",
    "p": "ol",
    "a": "qwsz",
    "s": "wedxza",
    "d": "erfcxs",
    "f": "rtgvcd",
    "g": "tyhbvf",
    "h": "yujnbg",
    "j": "uikmnh",
    "k": "ioljm",
    "l": "opk",
    "z": "asx",
    "x": "zsdc",
    "c": "xdfv",
    "v": "cfgb",
    "b": "vghn",
    "n": "bhjm",
    "m": "njk",
}

# Homoglyph map

_HOMOGLYPHS: dict[str, list[str]] = {
    "a": ["@", "4"],
    "b": ["8", "6"],
    "e": ["3"],
    "g": ["9", "q"],
    "i": ["1", "!", "l"],
    "l": ["1", "I", "|"],
    "o": ["0"],
    "s": ["5", "$"],
    "t": ["7", "+"],
    "z": ["2"],
}

# Platform URL templates

_PLATFORM_URLS: dict[str, str] = {
    "twitter": "https://x.com/{username}",
    "github": "https://github.com/{username}",
    "instagram": "https://www.instagram.com/{username}/",
}


# FamilyShield


class FamilyShield:
    """Registry of family members with associated emails, usernames, and domains.

    Data is persisted as a JSON file when *config_path* is provided.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path
        self._members: list[dict] = []
        self._lock = threading.Lock()

        if self._config_path:
            from jarvis_engine._shared import load_json_file

            data = load_json_file(self._config_path, None, expected_type=dict)
            if data is not None:
                self._members = data.get("members", [])

    # Public API

    def add_member(
        self,
        name: str,
        emails: list[str] | None = None,
        usernames: list[str] | None = None,
        domains: list[str] | None = None,
    ) -> None:
        """Add a family member to the registry and persist."""
        with self._lock:
            member = {
                "name": name,
                "emails": emails or [],
                "usernames": usernames or [],
                "domains": domains or [],
            }
            self._members.append(member)
            self._persist()

    def get_members(self) -> list[dict]:
        """Return all registered family members."""
        with self._lock:
            return list(self._members)

    def get_all_emails(self) -> list[str]:
        """Aggregate all emails across all members."""
        with self._lock:
            result: list[str] = []
            for m in self._members:
                result.extend(m.get("emails", []))
            return result

    def get_all_usernames(self) -> list[str]:
        """Aggregate all usernames across all members."""
        with self._lock:
            result: list[str] = []
            for m in self._members:
                result.extend(m.get("usernames", []))
            return result

    def get_all_domains(self) -> list[str]:
        """Aggregate all domains across all members."""
        with self._lock:
            result: list[str] = []
            for m in self._members:
                result.extend(m.get("domains", []))
            return result

    def status(self) -> FamilyShieldStatus:
        """Return a summary of the family registry."""
        with self._lock:
            return {
                "module": "FamilyShield",
                "member_count": len(self._members),
                "total_emails": sum(len(m.get("emails", [])) for m in self._members),
                "total_usernames": sum(len(m.get("usernames", [])) for m in self._members),
                "total_domains": sum(len(m.get("domains", [])) for m in self._members),
            }

    # Persistence

    def _persist(self) -> None:
        """Write current state to JSON config (caller holds _lock)."""
        if not self._config_path:
            return
        try:
            atomic_write_json(self._config_path, {"members": self._members})
        except OSError as exc:
            logger.error("Failed to persist FamilyShield config: %s", exc)


# BreachMonitor


class BreachMonitor:
    """HaveIBeenPwned integration for breach monitoring.

    - **Password checking** uses the k-anonymity API (no key required).
    - **Email breach lookup** requires a paid HIBP API key ($3.50/month).
    """

    _HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/{prefix}"
    _HIBP_BREACH_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/{email}"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    # Password k-anonymity check

    def check_password(self, password: str) -> PasswordCheckResult:
        """Check if *password* appears in known breaches using k-anonymity.

        Returns ``{compromised: bool, count: int}``.
        """
        sha1 = _sha1_hexdigest_not_for_security(password)
        prefix, suffix = sha1[:5], sha1[5:]

        url = _validated_https_url(self._HIBP_RANGE_URL.format(prefix=prefix))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-IdentityShield"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("HIBP password check failed: %s", type(exc).__name__)
            return {"compromised": False, "count": 0}

        for line in body.splitlines():
            parts = line.strip().split(":")
            if len(parts) == 2 and parts[0].upper() == suffix:
                return {"compromised": True, "count": int(parts[1])}

        return {"compromised": False, "count": 0}

    # Email breach check

    def check_email(self, email: str) -> list[BreachRecord]:
        """Query HIBP for breaches affecting *email*.

        Returns list of ``{name, breach_date, data_classes}``.
        Requires an API key; returns ``[]`` if none is configured.
        """
        if not self._api_key:
            logger.info("No HIBP API key — skipping email breach check for %s", email)
            return []

        url = _validated_https_url(
            self._HIBP_BREACH_URL.format(email=urllib.parse.quote(email, safe=""))
        )
        headers = {
            "hibp-api-key": self._api_key,
            "User-Agent": "Jarvis-IdentityShield",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # No breaches found
                return []
            logger.warning("HIBP email check error for %s: %s", email, exc)
            return []
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("HIBP email check failed for %s: %s", email, exc)
            return []

        results: list[BreachRecord] = []
        for breach in data:
            results.append({
                "name": breach.get("Name", "Unknown"),
                "breach_date": breach.get("BreachDate", "Unknown"),
                "data_classes": breach.get("DataClasses", []),
            })
        return results

    # Bulk check

    def check_all(self, family: FamilyShield) -> dict[str, list[BreachRecord]]:
        """Check all family emails for breaches.

        Returns ``{email: [breaches]}``.
        """
        results: dict[str, list[BreachRecord]] = {}
        for email in family.get_all_emails():
            results[email] = self.check_email(email)
        return results

    # Status

    def status(self) -> BreachMonitorStatus:
        """Return module status summary."""
        return {
            "module": "BreachMonitor",
            "api_key_configured": self._api_key is not None and len(self._api_key) > 0,
        }


# TyposquatMonitor


class TyposquatMonitor:
    """Generates typosquat domain variants and checks DNS registration."""

    # Variant generation

    def generate_variants(self, domain: str) -> list[str]:
        """Generate typosquat variants for *domain*.

        Techniques: character omission, adjacent-key substitution,
        character doubling, homoglyph substitution, TLD swap.
        """
        # Split domain into name and TLD at the LAST dot
        dot_idx = domain.rfind(".")
        if dot_idx < 0:
            name, tld = domain, ""
        else:
            name, tld = domain[:dot_idx], domain[dot_idx:]  # tld includes the dot

        seen: set[str] = set()
        variants: list[str] = []

        def _add(variant: str) -> None:
            if variant and variant != domain and variant not in seen:
                seen.add(variant)
                variants.append(variant)

        # 1. Character omission
        for i in range(len(name)):
            _add(name[:i] + name[i + 1:] + tld)

        # 2. Adjacent key substitution
        for i, ch in enumerate(name):
            for adj in _ADJACENT_KEYS.get(ch.lower(), ""):
                _add(name[:i] + adj + name[i + 1:] + tld)

        # 3. Character doubling
        for i, ch in enumerate(name):
            if ch.isalpha():
                _add(name[:i] + ch + ch + name[i + 1:] + tld)

        # 4. Homoglyph substitution
        for i, ch in enumerate(name):
            for glyph in _HOMOGLYPHS.get(ch.lower(), []):
                _add(name[:i] + glyph + name[i + 1:] + tld)

        # 5. TLD swap
        if tld:
            for alt_tld in [".com", ".net", ".org", ".io", ".co", ".info", ".biz"]:
                if alt_tld != tld:
                    _add(name + alt_tld)

        return variants

    # DNS check

    def check_domain(self, domain: str) -> list[BreachLookupResult]:
        """Check which typosquat variants of *domain* have DNS records.

        Uses parallel DNS lookups (max 20 threads) when available.

        Returns list of ``{variant, registered: bool, ips: list[str]}``.
        """
        variants = self.generate_variants(domain)

        def _lookup(variant: str) -> BreachLookupResult:
            entry: BreachLookupResult = {"variant": variant, "registered": False, "ips": []}
            try:
                infos = socket.getaddrinfo(variant, 80, socket.AF_INET, socket.SOCK_STREAM)
                ips = sorted({str(info[4][0]) for info in infos})
                if ips:
                    entry["registered"] = True
                    entry["ips"] = ips
            except (socket.gaierror, OSError) as exc:
                logger.debug("DNS lookup failed for variant %s: %s", variant, exc)
            return entry

        if _HAS_THREADPOOL and len(variants) > 1:
            results: list[BreachLookupResult] = []
            # Preserve order: submit all, collect by index
            with ThreadPoolExecutor(max_workers=20) as pool:
                future_to_idx = {
                    pool.submit(_lookup, v): i
                    for i, v in enumerate(variants)
                }
                ordered: list[BreachLookupResult | None] = [None] * len(variants)
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    ordered[idx] = future.result()
                results = [r for r in ordered if r is not None]
            return results

        # Fallback: sequential
        return [_lookup(v) for v in variants]

    # Family scan

    def scan_all(self, family: FamilyShield) -> dict[str, list[BreachLookupResult]]:
        """Check all family domains for typosquat variants.

        Returns ``{domain: [check_results]}``.
        """
        results: dict[str, list[BreachLookupResult]] = {}
        for domain in family.get_all_domains():
            results[domain] = self.check_domain(domain)
        return results

    # Status

    def status(self) -> TyposquatMonitorStatus:
        """Return module status summary."""
        return {
            "module": "TyposquatMonitor",
        }


# ImpersonationDetector


class ImpersonationDetector:
    """Detects potential social media impersonation by generating username
    variants and checking if they exist on major platforms."""

    _TIMEOUT_SECONDS = 5

    # Variant generation

    def generate_username_variants(self, username: str) -> list[str]:
        """Generate impersonation-style variants of *username*.

        Techniques: underscore add, official/real prefix/suffix,
        digit appending, homoglyph substitution.
        """
        seen: set[str] = set()
        variants: list[str] = []

        def _add(v: str) -> None:
            if v and v != username and v not in seen:
                seen.add(v)
                variants.append(v)

        # 1. Underscore addition
        _add(username + "_")
        _add("_" + username)
        _add(username + "__")
        _add("__" + username)

        # 2. "official" / "real" prefix/suffix
        for tag in ["official", "real", "the", "original", "verified"]:
            _add(f"{tag}_{username}")
            _add(f"{tag}{username}")
            _add(f"{username}_{tag}")
            _add(f"{username}{tag}")

        # 3. Digit appending
        for d in range(10):
            _add(f"{username}{d}")

        # 4. Homoglyph substitution
        for i, ch in enumerate(username):
            for glyph in _HOMOGLYPHS.get(ch.lower(), []):
                # Only use alphanumeric glyphs for usernames
                if glyph.isalnum():
                    _add(username[:i] + glyph + username[i + 1:])

        return variants

    # Platform profile check

    def check_platform(self, username: str, platform: str) -> PlatformCheckResult | None:
        """Check if *username* exists on *platform*.

        Returns ``{platform, username, exists: bool, url: str}``
        or ``None`` if the platform is not supported.
        """
        template = _PLATFORM_URLS.get(platform)
        if not template:
            logger.warning("Unsupported platform: %s", platform)
            return None

        url = _validated_https_url(template.format(username=username))
        result: PlatformCheckResult = {
            "platform": platform,
            "username": username,
            "exists": False,
            "url": url,
        }

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Jarvis-IdentityShield"},
            )
            with urllib.request.urlopen(req, timeout=self._TIMEOUT_SECONDS) as resp:  # nosec B310
                if resp.status == 200:
                    result["exists"] = True
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                logger.debug("HTTP %d checking %s on %s", exc.code, username, platform)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.debug("Network error checking %s on %s: %s", username, platform, exc)

        return result

    # Family scan

    def scan_all(self, family: FamilyShield) -> dict[str, list[PlatformCheckResult]]:
        """Check all family usernames for impersonation across platforms.

        Uses parallel HTTP checks (max 20 threads) when available.

        Returns ``{username: [check_results]}``.
        """
        results: dict[str, list[PlatformCheckResult]] = {}
        platforms = list(_PLATFORM_URLS.keys())

        for uname in family.get_all_usernames():
            # Build list of (variant, platform) pairs to check
            tasks: list[tuple[str, str]] = []
            for variant in self.generate_username_variants(uname):
                for platform in platforms:
                    tasks.append((variant, platform))

            checks: list[PlatformCheckResult] = []
            if _HAS_THREADPOOL and len(tasks) > 1:
                with ThreadPoolExecutor(max_workers=20) as pool:
                    futures = {
                        pool.submit(self.check_platform, v, p): i
                        for i, (v, p) in enumerate(tasks)
                    }
                    ordered: list[PlatformCheckResult | None] = [None] * len(tasks)
                    for future in as_completed(futures):
                        idx = futures[future]
                        try:
                            res = future.result()
                        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                            logger.debug("Platform check failed: %s", exc)
                            res = None
                        ordered[idx] = res
                    checks = [r for r in ordered if r is not None]
            else:
                # Fallback: sequential
                for variant, platform in tasks:
                    res = self.check_platform(variant, platform)
                    if res is not None:
                        checks.append(res)

            results[uname] = checks

        return results

    # Status

    def status(self) -> ImpersonationDetectorStatus:
        """Return module status summary."""
        return {
            "module": "ImpersonationDetector",
            "supported_platforms": list(_PLATFORM_URLS.keys()),
        }
