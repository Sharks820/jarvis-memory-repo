"""Owner identity protection and social engineering detection.

Detects urgency manipulation, authority impersonation, emotional manipulation,
identity extraction attempts, and owner impersonation in incoming text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alert dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityAlert:
    """A social engineering or identity threat alert."""

    alert_type: str
    severity: str  # LOW / MEDIUM / HIGH / CRITICAL
    description: str
    evidence: dict = field(default_factory=dict)
    recommended_action: str = ""


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

_URGENCY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bimmediately\b",
        r"\bright\s+now\b",
        r"\btime[- ]sensitive\b",
        r"\bemergency\b",
        r"\burgent(ly)?\b",
        r"\basap\b",
        r"\bdo\s+it\s+now\b",
        r"\bwithout\s+delay\b",
        r"\bcritical\s+deadline\b",
        r"\bno\s+time\s+to\s+waste\b",
    ]
]

_AUTHORITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bthis\s+is\s+(the\s+)?(admin|administrator|CEO|boss|CTO|manager|director|IT|IT\s+department|supervisor)\b",
        r"\bi['\u2019]?m\s+authorized\b",
        r"\bmanagement\s+requires\b",
        r"\bexecutive\s+order\b",
        r"\bby\s+order\s+of\b",
        r"\bon\s+behalf\s+of\s+(the\s+)?(CEO|admin|management|board|director)\b",
        r"\bi\s+have\s+(full\s+)?authority\b",
        r"\boverride\s+(code|authorization|clearance)\b",
        r"\byour\s+(boss|supervisor|manager)\s+(told|asked|wants|ordered)\b",
    ]
]

_EMOTIONAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bplease\s+help\b",
        r"\bi['\u2019]?m\s+in\s+trouble\b",
        r"\blife\s+or\s+death\b",
        r"\bpeople\s+will\s+(die|suffer|get\s+hurt)\b",
        r"\bdesperate(ly)?\b",
        r"\bbeg(ging)?\s+you\b",
        r"\byou['\u2019]?re\s+my\s+only\s+hope\b",
        r"\bno\s+one\s+else\s+can\s+help\b",
        r"\bi['\u2019]?ll\s+(lose|be\s+fired|be\s+homeless)\b",
    ]
]

_IDENTITY_EXTRACTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bwhat\s+is\s+your\s+(password|passcode|PIN|SSN|social\s+security)\b",
        r"\bwhat\s+is\s+your\s+(credit\s+card|bank\s+account|routing\s+number)\b",
        r"\btell\s+me\s+your\s+(name|email|phone|address|password)\b",
        r"\bgive\s+me\s+your\s+(credentials|login|password|SSN|bank)\b",
        r"\bshare\s+your\s+(password|account|credentials|personal)\b",
        r"\bwhat['\u2019]?s\s+your\s+(password|SSN|credit\s+card|bank)\b",
        r"\bsend\s+(me\s+)?your\s+(password|credentials|credit\s+card|SSN)\b",
        r"\bconfirm\s+your\s+(password|SSN|identity|account\s+number)\b",
        r"\bverify\s+your\s+(password|SSN|credit\s+card|bank\s+details)\b",
    ]
]


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class IdentityMonitor:
    """Detect social engineering attacks and identity extraction attempts.

    Parameters
    ----------
    owner_config:
        Dictionary with optional keys: ``name``, ``email``, ``phone``,
        ``handles`` (list of social handles / usernames).
    """

    def __init__(self, owner_config: dict | None = None) -> None:
        self._owner_config = owner_config or {}
        self._owner_name: str = self._owner_config.get("name", "")
        self._owner_email: str = self._owner_config.get("email", "")
        self._owner_phone: str = self._owner_config.get("phone", "")
        self._owner_handles: list[str] = self._owner_config.get("handles", [])

        # Build impersonation patterns from owner config
        self._impersonation_patterns: list[re.Pattern[str]] = []
        if self._owner_name:
            escaped = re.escape(self._owner_name)
            self._impersonation_patterns.append(
                re.compile(
                    rf"\b(i\s+am|i['\u2019]?m|this\s+is)\s+{escaped}\b",
                    re.IGNORECASE,
                )
            )
            self._impersonation_patterns.append(
                re.compile(
                    rf"\bmy\s+name\s+is\s+{escaped}\b",
                    re.IGNORECASE,
                )
            )
        for handle in self._owner_handles:
            escaped = re.escape(handle)
            self._impersonation_patterns.append(
                re.compile(
                    rf"\b(i\s+am|i['\u2019]?m|this\s+is)\s+{escaped}\b",
                    re.IGNORECASE,
                )
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_request_for_social_engineering(self, text: str) -> IdentityAlert | None:
        """Scan *text* for social engineering indicators.

        Returns the highest-severity ``IdentityAlert`` found, or ``None``
        if the text appears benign.
        """
        if not text or not text.strip():
            return None

        alerts: list[IdentityAlert] = []

        alert = self._check_identity_extraction(text)
        if alert is not None:
            alerts.append(alert)

        alert = self._check_impersonation(text)
        if alert is not None:
            alerts.append(alert)

        alert = self._check_authority_impersonation(text)
        if alert is not None:
            alerts.append(alert)

        alert = self._check_urgency_manipulation(text)
        if alert is not None:
            alerts.append(alert)

        alert = self._check_emotional_manipulation(text)
        if alert is not None:
            alerts.append(alert)

        if not alerts:
            return None

        # Return highest severity alert
        severity_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        alerts.sort(key=lambda a: severity_rank.get(a.severity, 0), reverse=True)
        return alerts[0]

    # ------------------------------------------------------------------
    # Detection rules
    # ------------------------------------------------------------------

    def _check_urgency_manipulation(self, text: str) -> IdentityAlert | None:
        for pat in _URGENCY_PATTERNS:
            m = pat.search(text)
            if m:
                return IdentityAlert(
                    alert_type="urgency_manipulation",
                    severity="MEDIUM",
                    description="Urgency language detected — possible social engineering.",
                    evidence={"matched": m.group(), "pattern": pat.pattern},
                    recommended_action="Verify request authenticity before proceeding.",
                )
        return None

    def _check_authority_impersonation(self, text: str) -> IdentityAlert | None:
        for pat in _AUTHORITY_PATTERNS:
            m = pat.search(text)
            if m:
                return IdentityAlert(
                    alert_type="authority_impersonation",
                    severity="HIGH",
                    description="Authority claim detected — possible impersonation attack.",
                    evidence={"matched": m.group(), "pattern": pat.pattern},
                    recommended_action="Do not comply. Verify identity through a trusted channel.",
                )
        return None

    def _check_emotional_manipulation(self, text: str) -> IdentityAlert | None:
        for pat in _EMOTIONAL_PATTERNS:
            m = pat.search(text)
            if m:
                return IdentityAlert(
                    alert_type="emotional_manipulation",
                    severity="MEDIUM",
                    description="Emotional manipulation language detected.",
                    evidence={"matched": m.group(), "pattern": pat.pattern},
                    recommended_action="Evaluate request objectively without emotional pressure.",
                )
        return None

    def _check_identity_extraction(self, text: str) -> IdentityAlert | None:
        for pat in _IDENTITY_EXTRACTION_PATTERNS:
            m = pat.search(text)
            if m:
                return IdentityAlert(
                    alert_type="identity_extraction",
                    severity="CRITICAL",
                    description="Attempt to extract sensitive identity information detected.",
                    evidence={"matched": m.group(), "pattern": pat.pattern},
                    recommended_action="BLOCK immediately. Never disclose sensitive information.",
                )
        return None

    def _check_impersonation(self, text: str) -> IdentityAlert | None:
        if not self._impersonation_patterns:
            return None
        for pat in self._impersonation_patterns:
            m = pat.search(text)
            if m:
                return IdentityAlert(
                    alert_type="impersonation",
                    severity="CRITICAL",
                    description="Someone is claiming to be the owner.",
                    evidence={"matched": m.group(), "pattern": pat.pattern},
                    recommended_action="BLOCK immediately. Verify identity through trusted device.",
                )
        return None
