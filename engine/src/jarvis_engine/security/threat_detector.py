"""Pluggable threat detection engine.

Runs a set of detection rules against every incoming request context and
produces a ``ThreatAssessment`` with an aggregated threat level and
recommended action.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Protocol

from jarvis_engine.security.ip_tracker import ThreatReport

logger = logging.getLogger(__name__)

# Detection patterns

_SQL_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER)\b\s)",
        r"(--|;)\s*(SELECT|DROP|INSERT|UPDATE|DELETE|ALTER)\b",
        r"'\s*(OR|AND)\s+\d+=\d+",
        r"'\s*(OR|AND)\s+'[^']*'\s*=\s*'[^']*'",
        r"UNION\s+(ALL\s+)?SELECT",
        r"/\*.*?\*/",
        r"0x[0-9a-fA-F]+",
        r"CHAR\(\d+\)",
        r"CONCAT\(",
        r"BENCHMARK\(",
        r"SLEEP\(",
        r"WAITFOR\s+DELAY",
    ]
]

_PATH_TRAVERSAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p)
    for p in [
        r"\.\./",
        r"\.\\.\\",
        r"%2e%2e%2f",
        r"%2e%2e/",
        r"\.\.%2f",
        r"%2e%2e%5c",
        r"etc/passwd",
        r"etc/shadow",
        r"windows/system32",
    ]
]

_DANGEROUS_CMDS = r"(?:rm|cat|curl|wget|chmod|chown|kill|dd|sh|bash|python|perl|ruby|nc|ncat|eval|exec|sudo)"
_COMMAND_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p)
    for p in [
        rf";\s*{_DANGEROUS_CMDS}\b",  # ; dangerous_cmd
        rf"\|\s*{_DANGEROUS_CMDS}\b",  # | dangerous_cmd
        r"`[^`]+`",  # `command`
        r"\$\([^)]+\)",  # $(command)
        r"\$\{[^}]+\}",  # ${variable}
        rf"&&\s*{_DANGEROUS_CMDS}\b",  # && dangerous_cmd
        rf"\|\|\s*{_DANGEROUS_CMDS}\b",  # || dangerous_cmd
    ]
]

_SUSPICIOUS_USER_AGENTS: list[str] = [
    "sqlmap",
    "nikto",
    "nmap",
    "masscan",
    "dirbuster",
    "gobuster",
    "wfuzz",
    "hydra",
    "burpsuite",
    "zap",
    "acunetix",
    "nessus",
    "openvas",
    "metasploit",
    "curl/",
    "wget/",
    "python-requests",
    "go-http-client",
]

# Data classes


@dataclass(frozen=True)
class ThreatSignal:
    """A single threat indicator produced by a detection rule."""

    severity: str  # LOW / MEDIUM / HIGH / CRITICAL
    category: str  # rule name (e.g. "payload_injection")
    confidence: float  # 0.0 – 1.0
    evidence: dict = field(default_factory=dict)


@dataclass
class ThreatAssessment:
    """Aggregated result from running all detection rules."""

    threat_level: str  # NONE / LOW / MEDIUM / HIGH / CRITICAL
    signals: list[ThreatSignal] = field(default_factory=list)
    recommended_action: str = "ALLOW"  # ALLOW / THROTTLE / CHALLENGE / BLOCK / KILL


# Severity helpers

_SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

# Detector


class _IPTrackerProtocol(Protocol):
    def get_threat_report(self, ip: str) -> ThreatReport | None:
        ...

    def is_blocked(self, ip: str) -> bool:
        ...


class ThreatDetector:
    """Run pluggable detection rules and aggregate into an assessment.

    Parameters
    ----------
    ip_tracker:
        Optional ``IPTracker`` instance for auth-brute-force lookups.
    nonce_ttl:
        Seconds before a seen nonce expires from the replay cache.
    """

    def __init__(
        self,
        *,
        ip_tracker: "_IPTrackerProtocol | None" = None,
        nonce_ttl: int = 300,
    ) -> None:
        self._ip_tracker = ip_tracker
        self._nonce_ttl = nonce_ttl
        self._lock = threading.Lock()
        # nonce -> timestamp last seen
        self._nonce_cache: dict[str, float] = {}
        self._nonce_cache_cap: int = 100000  # max nonces in cache
        self._nonce_prune_counter: int = 0
        # ip -> deque of request timestamps (for rate anomaly detection)
        # maxlen=200 caps per-IP memory: 200 entries is >3x the 60/min threshold
        self._request_log: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._request_log_cap: int = 10000  # max distinct IPs tracked
        # Counter for periodic stale-IP cleanup (avoid O(n) scan every request)
        self._rate_check_counter: int = 0

    # Public API

    def assess(self, request_context: dict) -> ThreatAssessment:
        """Run all detection rules and return an aggregated assessment.

        *request_context* should have (all optional):
        - ``ip``: client IP address
        - ``path``: request path
        - ``body``: request body string
        - ``method``: HTTP method
        - ``user_agent``: User-Agent header
        - ``nonce``: HMAC nonce value
        - ``headers``: dict of headers
        - ``timestamp``: request epoch time (float)
        """
        signals: list[ThreatSignal] = []
        # Rules that must fail-closed: if they error, treat as suspicious
        _critical_rules = {
            "_rule_replay_attack",
            "_rule_known_bad_ip",
            "_rule_auth_brute_force",
        }
        for rule in [
            self._rule_payload_injection,
            self._rule_path_traversal,
            self._rule_command_injection,
            self._rule_suspicious_user_agent,
            self._rule_replay_attack,
            self._rule_rate_anomaly,
            self._rule_auth_brute_force,
            self._rule_known_bad_ip,
        ]:
            try:
                signal = rule(request_context)
                if signal is not None:
                    signals.append(signal)
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                logger.warning("Rule %s raised an exception: %s", rule.__name__, exc)
                # Fail-closed for critical security rules
                if rule.__name__ in _critical_rules:
                    signals.append(
                        ThreatSignal(
                            severity="MEDIUM",
                            category=f"rule_error:{rule.__name__}",
                            confidence=0.50,
                            evidence={
                                "rule": rule.__name__,
                                "error": "rule raised exception",
                            },
                        )
                    )

        return self._aggregate(signals)

    # Aggregation

    def _aggregate(self, signals: list[ThreatSignal]) -> ThreatAssessment:
        if not signals:
            return ThreatAssessment(
                threat_level="NONE", signals=[], recommended_action="ALLOW"
            )

        counts: dict[str, int] = defaultdict(int)
        for s in signals:
            counts[s.severity] += 1

        # Determine threat level — any CRITICAL signal means CRITICAL
        if counts["CRITICAL"] >= 1:
            level = "CRITICAL"
        elif counts["HIGH"] >= 2:
            level = "CRITICAL"
        elif counts["HIGH"] >= 1:
            level = "HIGH"
        elif counts["MEDIUM"] >= 2:
            level = "HIGH"
        elif counts["MEDIUM"] >= 1:
            level = "MEDIUM"
        elif counts["LOW"] >= 2:
            level = "MEDIUM"
        else:
            level = "LOW"

        action_map = {
            "NONE": "ALLOW",
            "LOW": "ALLOW",
            "MEDIUM": "THROTTLE",
            "HIGH": "BLOCK",
            "CRITICAL": "KILL",
        }
        return ThreatAssessment(
            threat_level=level,
            signals=signals,
            recommended_action=action_map[level],
        )

    # Detection rules

    def _rule_payload_injection(self, ctx: dict) -> ThreatSignal | None:
        """Check for SQL injection patterns in body and path."""
        targets = [ctx.get("body", ""), ctx.get("path", "")]
        for target in targets:
            if not target:
                continue
            for pat in _SQL_INJECTION_PATTERNS:
                m = pat.search(target)
                if m:
                    return ThreatSignal(
                        severity="HIGH",
                        category="payload_injection",
                        confidence=0.85,
                        evidence={"pattern": pat.pattern, "match": m.group()[:100]},
                    )
        return None

    def _rule_path_traversal(self, ctx: dict) -> ThreatSignal | None:
        """Check for directory traversal sequences in path."""
        path = ctx.get("path", "")
        if not path:
            return None
        path_lower = path.lower()
        for pat in _PATH_TRAVERSAL_PATTERNS:
            m = pat.search(path_lower)
            if m:
                return ThreatSignal(
                    severity="HIGH",
                    category="path_traversal",
                    confidence=0.90,
                    evidence={"pattern": pat.pattern, "match": m.group()[:100]},
                )
        return None

    def _rule_command_injection(self, ctx: dict) -> ThreatSignal | None:
        """Check for shell command injection patterns in body."""
        body = ctx.get("body", "")
        if not body:
            return None
        for pat in _COMMAND_INJECTION_PATTERNS:
            m = pat.search(body)
            if m:
                return ThreatSignal(
                    severity="HIGH",
                    category="command_injection",
                    confidence=0.80,
                    evidence={"pattern": pat.pattern, "match": m.group()[:100]},
                )
        return None

    def _rule_suspicious_user_agent(self, ctx: dict) -> ThreatSignal | None:
        """Flag empty or scanner-associated user agents."""
        ua = ctx.get("user_agent") or ""
        if ua == "":
            return ThreatSignal(
                severity="MEDIUM",
                category="suspicious_user_agent",
                confidence=0.70,
                evidence={"user_agent": "(empty)"},
            )
        ua_lower = ua.lower()
        for scanner in _SUSPICIOUS_USER_AGENTS:
            if scanner in ua_lower:
                return ThreatSignal(
                    severity="MEDIUM",
                    category="suspicious_user_agent",
                    confidence=0.85,
                    evidence={"user_agent": ua, "matched_scanner": scanner},
                )
        return None

    def _rule_replay_attack(self, ctx: dict) -> ThreatSignal | None:
        """Detect nonce reuse within the TTL window."""
        nonce = ctx.get("nonce")
        if not nonce:
            return None

        now = time.monotonic()
        with self._lock:
            # Prune expired entries periodically (every 100 requests, not every request)
            self._nonce_prune_counter += 1
            if self._nonce_prune_counter >= 100:
                self._nonce_prune_counter = 0
                expired = [
                    k for k, v in self._nonce_cache.items() if now - v > self._nonce_ttl
                ]
                for k in expired:
                    del self._nonce_cache[k]

            # Hard cap: if cache is still too large after pruning, reject to prevent OOM
            if len(self._nonce_cache) >= self._nonce_cache_cap:
                return ThreatSignal(
                    severity="MEDIUM",
                    category="nonce_cache_overflow",
                    confidence=0.70,
                    evidence={
                        "cache_size": len(self._nonce_cache),
                        "nonce": nonce[:16],
                    },
                )

            if nonce in self._nonce_cache:
                return ThreatSignal(
                    severity="HIGH",
                    category="replay_attack",
                    confidence=0.95,
                    evidence={
                        "nonce": nonce,
                        "first_seen_ago_s": round(now - self._nonce_cache[nonce], 1),
                    },
                )
            self._nonce_cache[nonce] = now
        return None

    def _rule_rate_anomaly(self, ctx: dict) -> ThreatSignal | None:
        """Flag IPs exceeding 60 requests per minute."""
        ip = ctx.get("ip")
        if not ip:
            return None

        now = time.monotonic()
        window = 60.0  # 1 minute
        with self._lock:
            # Prevent unbounded growth from IP spoofing: cap distinct IPs
            if (
                ip not in self._request_log
                and len(self._request_log) >= self._request_log_cap
            ):
                # Evict the stalest IP before adding a new one
                stalest_ip = min(
                    self._request_log,
                    key=lambda k: (
                        self._request_log[k][-1] if self._request_log[k] else 0.0
                    ),
                )
                del self._request_log[stalest_ip]
            log = self._request_log[ip]
            log.append(now)

            # Prune entries outside the window — O(1) per pop with deque
            cutoff = now - window
            while log and log[0] < cutoff:
                log.popleft()

            count = len(log)

            # Periodic cleanup: evict stale IPs every 100 requests (not every request)
            self._rate_check_counter += 1
            if self._rate_check_counter >= 100:
                self._rate_check_counter = 0
                stale = [
                    k for k, v in self._request_log.items() if not v or v[-1] < cutoff
                ]
                for k in stale:
                    del self._request_log[k]

        if count > 60:
            return ThreatSignal(
                severity="MEDIUM",
                category="rate_anomaly",
                confidence=0.75,
                evidence={"ip": ip, "requests_per_minute": count},
            )
        return None

    def _rule_auth_brute_force(self, ctx: dict) -> ThreatSignal | None:
        """Check IP tracker for repeated auth failures."""
        if self._ip_tracker is None:
            return None
        ip = ctx.get("ip")
        if not ip:
            return None
        try:
            report = self._ip_tracker.get_threat_report(ip)
        except (sqlite3.Error, OSError) as exc:
            logger.debug("IP tracker threat report failed for %s: %s", ip, exc)
            return None
        if report is None:
            return None
        attempts = report.get("total_attempts", 0)
        if attempts >= 10:
            return ThreatSignal(
                severity="HIGH",
                category="auth_brute_force",
                confidence=0.90,
                evidence={"ip": ip, "total_attempts": attempts},
            )
        if attempts >= 5:
            return ThreatSignal(
                severity="MEDIUM",
                category="auth_brute_force",
                confidence=0.75,
                evidence={"ip": ip, "total_attempts": attempts},
            )
        return None

    def _rule_known_bad_ip(self, ctx: dict) -> ThreatSignal | None:
        """Check if IP is currently blocked by the IP tracker."""
        if self._ip_tracker is None:
            return None
        ip = ctx.get("ip")
        if not ip:
            return None
        try:
            if self._ip_tracker.is_blocked(ip):
                return ThreatSignal(
                    severity="CRITICAL",
                    category="known_bad_ip",
                    confidence=1.0,
                    evidence={"ip": ip, "status": "blocked"},
                )
        except (sqlite3.Error, OSError) as exc:
            logger.debug("IP tracker blocked check failed for %s: %s", ip, exc)
            return None
        return None
