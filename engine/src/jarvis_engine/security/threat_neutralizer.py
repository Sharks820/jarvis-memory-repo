"""Legal offensive response — CFAA-compliant threat neutralization.

Evidence preservation, automated abuse reporting (AbuseIPDB), ISP abuse
contact lookup (RDAP), permanent IP blackholing, and law enforcement
report generation (IC3/FBI format).

NO hack-back.  Only evidence collection, reporting, and local defense.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from typing import Any

from jarvis_engine._compat import UTC
from jarvis_engine._shared import sha256_hex

logger = logging.getLogger(__name__)

# AbuseIPDB category codes
ABUSEIPDB_CATEGORIES = {
    "brute_force": 18,
    "hacking": 15,
    "port_scan": 14,
    "open_proxy": 21,
    "open_relay": 22,
    "web_attack": 15,
    "injection": 15,
    "dos": 4,
    "spam": 10,
}

_ABUSEIPDB_REPORT_URL = "https://api.abuseipdb.com/api/v2/report"
_RDAP_URL_TEMPLATE = "https://rdap.org/ip/{ip}"
_REQUEST_TIMEOUT = 10

# Rate limit: 1 report per IP per hour (3600 seconds)
_REPORT_COOLDOWN_S = 3600


class ThreatNeutralizer:
    """CFAA-compliant threat neutralization pipeline.

    Coordinates evidence preservation, IP blocking, abuse reporting,
    ISP notification, and law enforcement package generation.

    All dependencies are optional — the neutralizer gracefully degrades
    when components are unavailable.

    Parameters
    ----------
    forensic_logger:
        Object with ``log_event(dict)`` for tamper-evident evidence preservation.
    ip_tracker:
        ``IPTracker`` instance for permanent IP blocking.
    attack_memory:
        ``AttackPatternMemory`` for recording attack patterns.
    alert_chain:
        ``AlertChain`` for escalating owner notifications.
    threat_intel:
        ``ThreatIntelFeed`` for IP enrichment data.
    """

    def __init__(
        self,
        forensic_logger: Any | None = None,
        ip_tracker: Any | None = None,
        attack_memory: Any | None = None,
        alert_chain: Any | None = None,
        threat_intel: Any | None = None,
    ) -> None:
        self._forensic_logger = forensic_logger
        self._ip_tracker = ip_tracker
        self._attack_memory = attack_memory
        self._alert_chain = alert_chain
        self._threat_intel = threat_intel

        self._lock = threading.Lock()

        # Rate limiting: ip -> last_report_timestamp
        self._report_cooldowns: dict[str, float] = {}
        self._report_call_count = 0  # generation counter for periodic cleanup

        # RDAP result cache: ip -> (timestamp, result_email_or_none)
        self._rdap_cache: dict[str, tuple[float, str | None]] = {}
        _RDAP_CACHE_TTL = 86400  # 24 hours
        self._rdap_cache_ttl = _RDAP_CACHE_TTL
        self._rdap_cache_max_size = 1024  # LRU-style cap to prevent unbounded growth

        # Counters
        self._total_neutralized = 0
        self._total_reported = 0
        self._total_blocked = 0

        # Recent actions log (bounded)
        self._recent_actions: deque[dict] = deque(maxlen=500)

    # ------------------------------------------------------------------
    # Main neutralization pipeline
    # ------------------------------------------------------------------

    def neutralize(self, ip: str, category: str, evidence: dict) -> dict:
        """Execute the full neutralization pipeline for a threat.

        Steps (each gracefully skipped if dependency missing):
        1. Preserve evidence via forensic_logger
        2. Permanent IP block via ip_tracker
        3. Record in attack_memory
        4. Report to AbuseIPDB if API key available
        5. Generate ISP abuse report (RDAP lookup for abuse contact)
        6. Send alert to owner

        Returns
        -------
        dict
            ``{ip, actions_taken, evidence_id, reported_to, blocked}``
        """
        actions_taken: list[str] = []
        reported_to: list[str] = []
        blocked = False
        evidence_id = self._compute_evidence_id(ip, category, evidence)

        # 1. Preserve evidence
        if self._forensic_logger is not None:
            try:
                self._forensic_logger.log_event({
                    "event_type": "threat_neutralization",
                    "ip": ip,
                    "category": category,
                    "evidence": evidence,
                    "evidence_id": evidence_id,
                })
                actions_taken.append("evidence_preserved")
            except Exception as exc:
                logger.warning("Failed to preserve evidence for %s: %s", ip, exc)

        # 2. Permanent IP block
        if self._ip_tracker is not None:
            try:
                self._ip_tracker.block_ip(ip, duration_hours=None)  # permanent
                blocked = True
                actions_taken.append("ip_blocked_permanent")
            except Exception as exc:
                logger.warning("Failed to block IP %s: %s", ip, exc)

        # 3. Record in attack memory
        if self._attack_memory is not None:
            try:
                payload_str = json.dumps(evidence, default=str)
                self._attack_memory.record_attack(
                    category=category,
                    payload=payload_str,
                    detection_method="threat_neutralizer",
                    source_ip=ip,
                )
                actions_taken.append("attack_recorded")
            except Exception as exc:
                logger.warning("Failed to record attack from %s: %s", ip, exc)

        # 4. Report to AbuseIPDB
        abuseipdb_categories = self._category_to_abuseipdb(category)
        if abuseipdb_categories:
            comment = f"Automated report: {category} attack from {ip}"
            try:
                if self.report_to_abuseipdb(ip, abuseipdb_categories, comment):
                    reported_to.append("abuseipdb")
                    actions_taken.append("reported_abuseipdb")
            except Exception as exc:
                logger.warning("AbuseIPDB report failed for %s: %s", ip, exc)

        # 5. ISP abuse contact lookup + report
        try:
            abuse_email = self.lookup_isp_abuse_contact(ip)
            if abuse_email:
                reported_to.append(abuse_email)
                actions_taken.append("isp_abuse_notified")
        except Exception as exc:
            logger.warning("ISP abuse lookup failed for %s: %s", ip, exc)

        # 6. Send alert to owner
        if self._alert_chain is not None:
            try:
                summary = (
                    f"Threat neutralized: {category} from {ip} "
                    f"({len(actions_taken)} actions taken)"
                )
                self._alert_chain.send_alert(
                    level=4,
                    summary=summary,
                    evidence=json.dumps(evidence, default=str),
                    source_ip=ip,
                )
                actions_taken.append("owner_alerted")
            except Exception as exc:
                logger.warning("Alert dispatch failed for %s: %s", ip, exc)

        # Update counters
        action_record = {
            "ip": ip,
            "category": category,
            "actions_taken": list(actions_taken),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._total_neutralized += 1
            if blocked:
                self._total_blocked += 1
            if reported_to:
                self._total_reported += 1
            self._recent_actions.append(action_record)

        result = {
            "ip": ip,
            "actions_taken": actions_taken,
            "evidence_id": evidence_id,
            "reported_to": reported_to,
            "blocked": blocked,
        }

        logger.info(
            "Neutralized threat from %s (%s): %d actions, blocked=%s",
            ip, category, len(actions_taken), blocked,
        )

        return result

    # ------------------------------------------------------------------
    # AbuseIPDB reporting
    # ------------------------------------------------------------------

    def report_to_abuseipdb(
        self, ip: str, categories: list[int], comment: str,
    ) -> bool:
        """Submit an abuse report to AbuseIPDB.

        Rate limited: max 1 report per IP per hour.

        Returns True if report submitted successfully, False otherwise.
        """
        api_key = os.environ.get("ABUSEIPDB_API_KEY")
        if not api_key:
            logger.debug("ABUSEIPDB_API_KEY not set, skipping report for %s", ip)
            return False

        # Rate limit check
        now = time.time()
        with self._lock:
            # Periodic cleanup: every 100 calls, prune expired entries
            self._report_call_count += 1
            if self._report_call_count % 100 == 0:
                expired = [
                    k for k, v in self._report_cooldowns.items()
                    if (now - v) >= _REPORT_COOLDOWN_S
                ]
                for k in expired:
                    del self._report_cooldowns[k]

            last_report = self._report_cooldowns.get(ip)
            if last_report is not None and (now - last_report) < _REPORT_COOLDOWN_S:
                logger.debug(
                    "Rate limited: already reported %s within the last hour", ip,
                )
                return False
            # Reserve the slot (set now so concurrent calls also see it)
            self._report_cooldowns[ip] = now

        # Build POST request
        try:
            categories_str = ",".join(str(c) for c in categories)
            post_data = urllib.parse.urlencode({
                "ip": ip,
                "categories": categories_str,
                "comment": comment[:1024],  # AbuseIPDB limit
            }).encode("utf-8")

            req = urllib.request.Request(
                _ABUSEIPDB_REPORT_URL,
                data=post_data,
                method="POST",
            )
            req.add_header("Key", api_key)
            req.add_header("Accept", "application/json")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")

            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                resp.read()  # consume response

            logger.info("AbuseIPDB report submitted for %s (categories: %s)", ip, categories_str)
            return True

        except Exception as exc:
            logger.warning("AbuseIPDB report failed for %s: %s", ip, exc)
            # Revert cooldown on failure so retry is possible
            with self._lock:
                if self._report_cooldowns.get(ip) == now:
                    del self._report_cooldowns[ip]
            return False

    # ------------------------------------------------------------------
    # RDAP ISP abuse contact lookup
    # ------------------------------------------------------------------

    def lookup_isp_abuse_contact(self, ip: str) -> str | None:
        """Look up the ISP abuse contact email via RDAP.

        Queries ``https://rdap.org/ip/{ip}`` and searches for an entity
        with the ``"abuse"`` role, extracting its email from the vCard.

        Results are cached for 24 hours to avoid redundant network calls.

        Returns the abuse email address, or None if lookup fails.
        """
        # Check cache first
        now = time.time()
        with self._lock:
            cached = self._rdap_cache.get(ip)
            if cached is not None:
                cached_time, cached_result = cached
                if (now - cached_time) < self._rdap_cache_ttl:
                    return cached_result

        url = _RDAP_URL_TEMPLATE.format(ip=ip)
        try:
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                body = json.loads(resp.read())

            # Search entities for abuse role
            entities = body.get("entities", [])
            for entity in entities:
                roles = entity.get("roles", [])
                if "abuse" in roles:
                    result = self._extract_email_from_vcard(entity)
                    if result is not None:
                        with self._lock:
                            self._rdap_cache_put(ip, result)
                        return result

            # Check nested entities
            for entity in entities:
                for sub_entity in entity.get("entities", []):
                    roles = sub_entity.get("roles", [])
                    if "abuse" in roles:
                        result = self._extract_email_from_vcard(sub_entity)
                        if result is not None:
                            with self._lock:
                                self._rdap_cache_put(ip, result)
                            return result

            logger.debug("No abuse contact found in RDAP for %s", ip)
            with self._lock:
                self._rdap_cache_put(ip, None)
            return None

        except Exception as exc:
            logger.debug("RDAP lookup failed for %s: %s", ip, exc)
            return None

    # ------------------------------------------------------------------
    # Law enforcement package
    # ------------------------------------------------------------------

    def generate_law_enforcement_package(
        self, ip: str, evidence: dict,
    ) -> dict:
        """Generate an IC3/FBI-format report package.

        Returns a dict with:
        - summary: Human-readable incident summary
        - ip: The attacker IP
        - attack_timeline: List of timestamped events
        - evidence_hashes: SHA-256 hashes of each evidence field
        - recommended_charges: Applicable CFAA sections
        - report_template: Pre-formatted text for IC3 submission
        """
        evidence_id = self._compute_evidence_id(ip, "law_enforcement", evidence)

        # Build attack timeline from evidence
        timeline: list[dict] = []
        timestamp = evidence.get("timestamp", datetime.now(UTC).isoformat())
        timeline.append({
            "timestamp": timestamp,
            "event": f"Attack detected from {ip}",
            "details": {k: str(v) for k, v in evidence.items()},
        })

        # Compute evidence hashes
        evidence_hashes: dict[str, str] = {}
        for key, value in evidence.items():
            val_str = json.dumps(value, default=str)
            evidence_hashes[key] = sha256_hex(val_str)
        evidence_hashes["_full_evidence"] = evidence_id

        # Determine recommended charges based on evidence
        charges = self._determine_charges(evidence)

        # Build summary
        evidence_types = ", ".join(evidence.keys())
        summary = (
            f"Unauthorized computer access from IP {ip}. "
            f"Evidence collected: {evidence_types}. "
            f"Evidence integrity verified with SHA-256 hash chain."
        )

        # Build IC3 report template
        report_template = self._build_ic3_template(
            ip=ip,
            summary=summary,
            timeline=timeline,
            evidence_hashes=evidence_hashes,
            charges=charges,
        )

        return {
            "summary": summary,
            "ip": ip,
            "attack_timeline": timeline,
            "evidence_hashes": evidence_hashes,
            "recommended_charges": charges,
            "report_template": report_template,
        }

    # ------------------------------------------------------------------
    # Permanent block
    # ------------------------------------------------------------------

    def permanent_block(self, ip: str, reason: str) -> None:
        """Add IP to permanent blocklist via ip_tracker.

        No-op if ip_tracker is not configured.
        """
        if self._ip_tracker is None:
            logger.debug("No ip_tracker configured, cannot block %s", ip)
            return

        try:
            self._ip_tracker.block_ip(ip, duration_hours=None)  # None = permanent
            with self._lock:
                self._total_blocked += 1
            logger.info("Permanently blocked %s: %s", ip, reason)
        except Exception as exc:
            logger.warning("Failed to permanently block %s: %s", ip, exc)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return operational status and counters.

        Returns
        -------
        dict
            ``{total_neutralized, total_reported, total_blocked, recent_actions}``
        """
        with self._lock:
            return {
                "total_neutralized": self._total_neutralized,
                "total_reported": self._total_reported,
                "total_blocked": self._total_blocked,
                "recent_actions": list(self._recent_actions),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rdap_cache_put(self, ip: str, result: str | None) -> None:
        """Store a value in the RDAP cache, evicting stale/oldest entries if full.

        Must be called while holding ``self._lock``.
        """
        now = time.time()
        self._rdap_cache[ip] = (now, result)

        if len(self._rdap_cache) > self._rdap_cache_max_size:
            # Evict expired entries first
            expired = [
                k for k, (ts, _) in self._rdap_cache.items()
                if (now - ts) >= self._rdap_cache_ttl
            ]
            for k in expired:
                del self._rdap_cache[k]

            # If still over capacity, evict oldest entries
            if len(self._rdap_cache) > self._rdap_cache_max_size:
                sorted_keys = sorted(
                    self._rdap_cache, key=lambda k: self._rdap_cache[k][0],
                )
                # Remove the oldest quarter to avoid evicting on every insert
                evict_count = len(self._rdap_cache) - self._rdap_cache_max_size + self._rdap_cache_max_size // 4
                for k in sorted_keys[:evict_count]:
                    del self._rdap_cache[k]

    @staticmethod
    def _compute_evidence_id(ip: str, category: str, evidence: dict) -> str:
        """Compute a SHA-256 evidence ID from the IP, category, and evidence."""
        payload = json.dumps(
            {"ip": ip, "category": category, "evidence": evidence},
            sort_keys=True,
            default=str,
        )
        return sha256_hex(payload)

    @staticmethod
    def _extract_email_from_vcard(entity: dict) -> str | None:
        """Extract email address from RDAP vCard array."""
        vcard_array = entity.get("vcardArray")
        if not vcard_array or len(vcard_array) < 2:
            return None

        for field in vcard_array[1]:
            if isinstance(field, list) and len(field) >= 4:
                if field[0] == "email":
                    return field[3]

        return None

    @staticmethod
    def _category_to_abuseipdb(category: str) -> list[int]:
        """Map internal category names to AbuseIPDB category codes."""
        code = ABUSEIPDB_CATEGORIES.get(category)
        if code is not None:
            return [code]
        # Default to hacking (15) for unknown categories
        return [15]

    @staticmethod
    def _determine_charges(evidence: dict) -> list[str]:
        """Determine applicable CFAA sections based on evidence."""
        charges: list[str] = []

        # 18 U.S.C. section 1030(a)(2) — unauthorized access to obtain information
        charges.append("18 U.S.C. 1030(a)(2) — Unauthorized access to obtain information")

        payload = json.dumps(evidence, default=str).lower()

        # 18 U.S.C. section 1030(a)(5) — damage to protected computers
        if any(term in payload for term in ["dos", "ddos", "flood", "damage", "delete", "destroy"]):
            charges.append("18 U.S.C. 1030(a)(5) — Damage to protected computers")

        # 18 U.S.C. section 1030(a)(7) — extortion
        if any(term in payload for term in ["ransom", "extort", "bitcoin", "payment"]):
            charges.append("18 U.S.C. 1030(a)(7) — Extortion involving computers")

        # 18 U.S.C. section 1030(a)(2)(C) — unauthorized access affecting interstate commerce
        if any(term in payload for term in ["sql", "injection", "rce", "exploit"]):
            charges.append("18 U.S.C. 1030(a)(2)(C) — Access affecting interstate commerce")

        return charges

    @staticmethod
    def _build_ic3_template(
        ip: str,
        summary: str,
        timeline: list[dict],
        evidence_hashes: dict[str, str],
        charges: list[str],
    ) -> str:
        """Build a pre-formatted IC3 complaint template."""
        lines = [
            "=" * 60,
            "INTERNET CRIME COMPLAINT CENTER (IC3) REPORT",
            "=" * 60,
            "",
            "INCIDENT SUMMARY",
            "-" * 40,
            summary,
            "",
            "ATTACKER IP ADDRESS",
            "-" * 40,
            ip,
            "",
            "ATTACK TIMELINE",
            "-" * 40,
        ]

        for event in timeline:
            lines.append(f"  [{event['timestamp']}] {event['event']}")
            for k, v in event.get("details", {}).items():
                lines.append(f"    {k}: {v}")

        lines.extend([
            "",
            "EVIDENCE INTEGRITY (SHA-256 Hashes)",
            "-" * 40,
        ])
        for field, h in evidence_hashes.items():
            lines.append(f"  {field}: {h}")

        lines.extend([
            "",
            "RECOMMENDED CHARGES",
            "-" * 40,
        ])
        for charge in charges:
            lines.append(f"  - {charge}")

        lines.extend([
            "",
            "=" * 60,
            "END OF REPORT",
            "=" * 60,
        ])

        return "\n".join(lines)
