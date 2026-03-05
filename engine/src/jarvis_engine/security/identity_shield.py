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
import urllib.request
from pathlib import Path

try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _HAS_THREADPOOL = True
except ImportError:  # pragma: no cover
    _HAS_THREADPOOL = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Adjacent-key map (QWERTY layout)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Homoglyph map
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Platform URL templates
# ---------------------------------------------------------------------------

_PLATFORM_URLS: dict[str, str] = {
    "twitter": "https://x.com/{username}",
    "github": "https://github.com/{username}",
    "instagram": "https://www.instagram.com/{username}/",
}


# ========================================================================
# FamilyShield
# ========================================================================


class FamilyShield:
    """Registry of family members with associated emails, usernames, and domains.

    Data is persisted as a JSON file when *config_path* is provided.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path
        self._members: list[dict] = []
        self._lock = threading.Lock()

        if self._config_path and self._config_path.exists():
            try:
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
                self._members = data.get("members", [])
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load FamilyShield config: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

    def status(self) -> dict:
        """Return a summary of the family registry."""
        with self._lock:
            return {
                "module": "FamilyShield",
                "member_count": len(self._members),
                "total_emails": sum(len(m.get("emails", [])) for m in self._members),
                "total_usernames": sum(len(m.get("usernames", [])) for m in self._members),
                "total_domains": sum(len(m.get("domains", [])) for m in self._members),
            }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        """Write current state to JSON config (caller holds _lock)."""
        if not self._config_path:
            return
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(
                json.dumps({"members": self._members}, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Failed to persist FamilyShield config: %s", exc)


# ========================================================================
# BreachMonitor
# ========================================================================


class BreachMonitor:
    """HaveIBeenPwned integration for breach monitoring.

    - **Password checking** uses the k-anonymity API (no key required).
    - **Email breach lookup** requires a paid HIBP API key ($3.50/month).
    """

    _HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/{prefix}"
    _HIBP_BREACH_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/{email}"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    # ------------------------------------------------------------------
    # Password k-anonymity check
    # ------------------------------------------------------------------

    def check_password(self, password: str) -> dict:
        """Check if *password* appears in known breaches using k-anonymity.

        Returns ``{compromised: bool, count: int}``.
        """
        sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        prefix, suffix = sha1[:5], sha1[5:]

        url = self._HIBP_RANGE_URL.format(prefix=prefix)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-IdentityShield"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("HIBP password check failed: %s", exc)
            return {"compromised": False, "count": 0}

        for line in body.splitlines():
            parts = line.strip().split(":")
            if len(parts) == 2 and parts[0].upper() == suffix:
                return {"compromised": True, "count": int(parts[1])}

        return {"compromised": False, "count": 0}

    # ------------------------------------------------------------------
    # Email breach check
    # ------------------------------------------------------------------

    def check_email(self, email: str) -> list[dict]:
        """Query HIBP for breaches affecting *email*.

        Returns list of ``{name, breach_date, data_classes}``.
        Requires an API key; returns ``[]`` if none is configured.
        """
        if not self._api_key:
            logger.info("No HIBP API key — skipping email breach check for %s", email)
            return []

        url = self._HIBP_BREACH_URL.format(email=urllib.request.quote(email, safe=""))
        headers = {
            "hibp-api-key": self._api_key,
            "User-Agent": "Jarvis-IdentityShield",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # No breaches found
                return []
            logger.warning("HIBP email check error for %s: %s", email, exc)
            return []
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("HIBP email check failed for %s: %s", email, exc)
            return []

        results: list[dict] = []
        for breach in data:
            results.append({
                "name": breach.get("Name", "Unknown"),
                "breach_date": breach.get("BreachDate", "Unknown"),
                "data_classes": breach.get("DataClasses", []),
            })
        return results

    # ------------------------------------------------------------------
    # Bulk check
    # ------------------------------------------------------------------

    def check_all(self, family: FamilyShield) -> dict:
        """Check all family emails for breaches.

        Returns ``{email: [breaches]}``.
        """
        results: dict[str, list[dict]] = {}
        for email in family.get_all_emails():
            results[email] = self.check_email(email)
        return results

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return module status summary."""
        return {
            "module": "BreachMonitor",
            "api_key_configured": self._api_key is not None and len(self._api_key) > 0,
        }


# ========================================================================
# TyposquatMonitor
# ========================================================================


class TyposquatMonitor:
    """Generates typosquat domain variants and checks DNS registration."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Variant generation
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # DNS check
    # ------------------------------------------------------------------

    def check_domain(self, domain: str) -> list[dict]:
        """Check which typosquat variants of *domain* have DNS records.

        Uses parallel DNS lookups (max 20 threads) when available.

        Returns list of ``{variant, registered: bool, ips: list[str]}``.
        """
        variants = self.generate_variants(domain)

        def _lookup(variant: str) -> dict:
            entry: dict = {"variant": variant, "registered": False, "ips": []}
            try:
                infos = socket.getaddrinfo(variant, 80, socket.AF_INET, socket.SOCK_STREAM)
                ips = list({info[4][0] for info in infos})
                if ips:
                    entry["registered"] = True
                    entry["ips"] = ips
            except (socket.gaierror, OSError):
                pass
            return entry

        if _HAS_THREADPOOL and len(variants) > 1:
            results: list[dict] = []
            # Preserve order: submit all, collect by index
            with ThreadPoolExecutor(max_workers=20) as pool:
                future_to_idx = {
                    pool.submit(_lookup, v): i
                    for i, v in enumerate(variants)
                }
                ordered = [None] * len(variants)
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    ordered[idx] = future.result()
                results = [r for r in ordered if r is not None]
            return results

        # Fallback: sequential
        return [_lookup(v) for v in variants]

    # ------------------------------------------------------------------
    # Family scan
    # ------------------------------------------------------------------

    def scan_all(self, family: FamilyShield) -> dict:
        """Check all family domains for typosquat variants.

        Returns ``{domain: [check_results]}``.
        """
        results: dict[str, list[dict]] = {}
        for domain in family.get_all_domains():
            results[domain] = self.check_domain(domain)
        return results

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return module status summary."""
        return {
            "module": "TyposquatMonitor",
        }


# ========================================================================
# ImpersonationDetector
# ========================================================================


class ImpersonationDetector:
    """Detects potential social media impersonation by generating username
    variants and checking if they exist on major platforms."""

    _TIMEOUT_SECONDS = 5

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Variant generation
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Platform profile check
    # ------------------------------------------------------------------

    def check_platform(self, username: str, platform: str) -> dict | None:
        """Check if *username* exists on *platform*.

        Returns ``{platform, username, exists: bool, url: str}``
        or ``None`` if the platform is not supported.
        """
        template = _PLATFORM_URLS.get(platform)
        if not template:
            logger.warning("Unsupported platform: %s", platform)
            return None

        url = template.format(username=username)
        result: dict = {
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
            with urllib.request.urlopen(req, timeout=self._TIMEOUT_SECONDS) as resp:
                if resp.status == 200:
                    result["exists"] = True
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                logger.debug("HTTP %d checking %s on %s", exc.code, username, platform)
        except (urllib.error.URLError, OSError) as exc:
            logger.debug("Network error checking %s on %s: %s", username, platform, exc)

        return result

    # ------------------------------------------------------------------
    # Family scan
    # ------------------------------------------------------------------

    def scan_all(self, family: FamilyShield) -> dict:
        """Check all family usernames for impersonation across platforms.

        Uses parallel HTTP checks (max 20 threads) when available.

        Returns ``{username: [check_results]}``.
        """
        results: dict[str, list[dict]] = {}
        platforms = list(_PLATFORM_URLS.keys())

        for uname in family.get_all_usernames():
            # Build list of (variant, platform) pairs to check
            tasks: list[tuple[str, str]] = []
            for variant in self.generate_username_variants(uname):
                for platform in platforms:
                    tasks.append((variant, platform))

            checks: list[dict] = []
            if _HAS_THREADPOOL and len(tasks) > 1:
                with ThreadPoolExecutor(max_workers=20) as pool:
                    futures = {
                        pool.submit(self.check_platform, v, p): i
                        for i, (v, p) in enumerate(tasks)
                    }
                    ordered = [None] * len(tasks)
                    for future in as_completed(futures):
                        idx = futures[future]
                        try:
                            res = future.result()
                        except Exception:
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

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return module status summary."""
        return {
            "module": "ImpersonationDetector",
            "supported_platforms": list(_PLATFORM_URLS.keys()),
        }
