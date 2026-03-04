"""Scam Campaign Hunter — advanced scam call center detection and intelligence.

Goes beyond individual number scoring to detect coordinated scam campaigns:
- Prefix-based campaign clustering (rotating numbers from same NPA-NXX)
- STIR/SHAKEN verification status integration
- VoIP carrier identification
- Sequential number detection (robodialers cycling last 4 digits)
- Temporal burst analysis
- Campaign fingerprinting — link numbers to campaigns for group blocking
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis_engine._compat import UTC
from jarvis_engine._shared import safe_float as _safe_float
from jarvis_engine.phone_guard import _area_key, _normalize_number, _parse_ts

_CAMPAIGNS_LOCK = threading.Lock()


@dataclass
class ScamCampaign:
    """A detected cluster of related scam numbers."""

    campaign_id: str
    prefix: str  # NPA-NXX prefix (e.g. "+1555")
    numbers: list[str] = field(default_factory=list)
    total_calls: int = 0
    first_seen_utc: str = ""
    last_seen_utc: str = ""
    confidence: float = 0.0  # 0.0 - 1.0
    signals: list[str] = field(default_factory=list)
    carrier: str = ""  # VoIP carrier if identified
    line_type: str = ""  # "voip", "mobile", "landline", "non_fixed_voip"
    stir_failed_count: int = 0
    stir_not_verified_count: int = 0
    blocked: bool = False  # whether prefix is auto-blocked


@dataclass
class CallIntelReport:
    """Intelligence report for a single screened call."""

    number: str
    normalized: str = ""
    prefix: str = ""
    timestamp_utc: str = ""
    stir_status: str = ""  # "passed", "failed", "not_verified"
    presentation: str = ""  # "allowed", "restricted", "unknown", "payphone"
    duration_sec: float = 0.0
    direction: str = "incoming"
    answered: bool = False
    contact_name: str = ""
    carrier: str = ""
    line_type: str = ""
    campaign_id: str = ""  # populated if linked to a campaign


@dataclass
class CarrierIntel:
    """Carrier intelligence for a phone number or prefix."""

    number: str
    carrier: str = ""
    line_type: str = ""  # "voip", "mobile", "landline", "non_fixed_voip", "toll_free"
    is_voip: bool = False
    country: str = ""
    valid: bool = True
    lookup_source: str = ""  # "numverify", "ipqs", "cache"
    lookup_utc: str = ""
    risk_score: float = 0.0  # 0.0-1.0 from lookup provider


# ---------------------------------------------------------------------------
#  Campaign Detection
# ---------------------------------------------------------------------------

def detect_campaigns(
    call_reports: list[dict[str, Any]],
    *,
    window_hours: int = 72,
    min_numbers_for_campaign: int = 2,
    now_utc: datetime | None = None,
) -> list[ScamCampaign]:
    """Analyze call reports to detect scam campaigns.

    Groups calls by NPA-NXX prefix and applies heuristics:
    - 2+ distinct unknown numbers from same prefix in 72h → campaign
    - STIR/SHAKEN failures boost confidence
    - Sequential last-4-digits boost confidence
    - Short call durations (< 5s) boost confidence
    - All-missed calls boost confidence
    """
    now = now_utc or datetime.now(UTC)
    lookback = now - timedelta(hours=window_hours)

    # Group by prefix
    prefix_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in call_reports:
        number = _normalize_number(str(report.get("number", "")))
        if not number:
            continue
        ts = _parse_ts(report.get("timestamp_utc", ""))
        if ts and ts < lookback:
            continue
        prefix = _area_key(number)
        if not prefix:
            continue
        report["_normalized"] = number
        report["_prefix"] = prefix
        prefix_groups[prefix].append(report)

    campaigns: list[ScamCampaign] = []
    for prefix, reports in prefix_groups.items():
        # Get distinct numbers, excluding known contacts
        distinct_numbers: set[str] = set()
        contact_numbers: set[str] = set()
        for r in reports:
            num = r.get("_normalized", "")
            contact = str(r.get("contact_name", "")).strip()
            if contact:
                contact_numbers.add(num)
            else:
                distinct_numbers.add(num)

        # Skip if not enough unknown numbers
        if len(distinct_numbers) < min_numbers_for_campaign:
            continue

        # Build campaign
        signals: list[str] = []
        confidence = 0.0

        # Signal: multiple distinct numbers from same prefix
        if len(distinct_numbers) >= 5:
            confidence += 0.35
            signals.append(f"rotating_numbers_{len(distinct_numbers)}")
        elif len(distinct_numbers) >= 3:
            confidence += 0.25
            signals.append(f"multiple_numbers_{len(distinct_numbers)}")
        else:
            confidence += 0.15
            signals.append(f"number_pair")

        # Signal: sequential last-4 detection
        last4_values = []
        for num in distinct_numbers:
            digits = re.sub(r"\D", "", num)
            if len(digits) >= 4:
                try:
                    last4_values.append(int(digits[-4:]))
                except ValueError:
                    pass
        if len(last4_values) >= 2:
            last4_sorted = sorted(last4_values)
            sequential_count = sum(
                1 for i in range(len(last4_sorted) - 1)
                if last4_sorted[i + 1] - last4_sorted[i] <= 5
            )
            if sequential_count >= 1:
                confidence += 0.20
                signals.append("sequential_numbers")

        # Signal: STIR/SHAKEN failures
        stir_failed = sum(1 for r in reports if r.get("stir_status") == "failed")
        stir_not_verified = sum(1 for r in reports if r.get("stir_status") == "not_verified")
        if stir_failed >= 1:
            confidence += 0.25
            signals.append(f"stir_failed_{stir_failed}")
        elif stir_not_verified >= 2:
            confidence += 0.05
            signals.append("stir_not_verified")

        # Signal: all calls unanswered / short duration
        total = len(reports)
        unanswered = sum(1 for r in reports if not r.get("answered", False))
        short_calls = sum(
            1 for r in reports
            if _safe_float(r.get("duration_sec", 0)) < 5 and r.get("answered", False)
        )
        if total > 0 and unanswered / total >= 0.8:
            confidence += 0.10
            signals.append("mostly_unanswered")
        if short_calls >= 2:
            confidence += 0.10
            signals.append("short_duration_calls")

        # Signal: burst pattern (many calls in short window)
        timestamps = []
        for r in reports:
            ts = _parse_ts(r.get("timestamp_utc", ""))
            if ts:
                timestamps.append(ts)
        if len(timestamps) >= 3:
            timestamps.sort()
            span = (timestamps[-1] - timestamps[0]).total_seconds()
            if span > 0 and len(timestamps) / (span / 3600) >= 3:
                confidence += 0.10
                signals.append("burst_pattern")

        # Signal: restricted/unknown presentation
        restricted = sum(
            1 for r in reports
            if r.get("presentation") in ("restricted", "unknown")
        )
        if restricted >= 1:
            confidence += 0.10
            signals.append("restricted_presentation")

        confidence = min(confidence, 0.99)

        # Determine timestamps
        all_ts = [_parse_ts(r.get("timestamp_utc", "")) for r in reports]
        valid_ts = [t for t in all_ts if t]
        first_seen = min(valid_ts).isoformat() if valid_ts else ""
        last_seen = max(valid_ts).isoformat() if valid_ts else ""

        # Aggregate carrier info
        carrier = ""
        line_type = ""
        for r in reports:
            if r.get("carrier"):
                carrier = r["carrier"]
            if r.get("line_type"):
                line_type = r["line_type"]

        total_calls = sum(
            int(r.get("calls", 1)) if isinstance(r.get("calls"), (int, float)) else 1
            for r in reports
        )

        campaign_id = _generate_campaign_id(prefix, sorted(distinct_numbers))
        campaigns.append(ScamCampaign(
            campaign_id=campaign_id,
            prefix=prefix,
            numbers=sorted(distinct_numbers),
            total_calls=total_calls,
            first_seen_utc=first_seen,
            last_seen_utc=last_seen,
            confidence=round(confidence, 4),
            signals=signals,
            carrier=carrier,
            line_type=line_type,
            stir_failed_count=stir_failed,
            stir_not_verified_count=stir_not_verified,
        ))

    campaigns.sort(key=lambda c: c.confidence, reverse=True)
    return campaigns


def compute_enhanced_spam_score(
    base_score: float,
    stir_status: str = "",
    line_type: str = "",
    carrier_risk: float = 0.0,
    campaign_confidence: float = 0.0,
    presentation: str = "",
    is_in_contacts: bool = False,
) -> float:
    """Compute an enhanced spam score incorporating STIR/SHAKEN and VoIP signals.

    Augments the base phone_guard score with additional signals.
    """
    if is_in_contacts:
        return 0.0

    score = base_score

    # STIR/SHAKEN
    if stir_status == "failed":
        score += 0.40
    elif stir_status == "not_verified":
        score += 0.05

    # VoIP / line type
    if line_type == "non_fixed_voip":
        score += 0.20
    elif line_type == "voip":
        score += 0.12

    # Carrier risk
    score += carrier_risk * 0.15

    # Campaign membership
    score += campaign_confidence * 0.25

    # Presentation
    if presentation == "restricted":
        score += 0.10
    elif presentation == "unknown":
        score += 0.05

    return min(score, 0.99)


# ---------------------------------------------------------------------------
#  Call Intel Reporting
# ---------------------------------------------------------------------------

def create_call_intel_report(
    number: str,
    stir_status: str = "",
    presentation: str = "",
    duration_sec: float = 0.0,
    answered: bool = False,
    contact_name: str = "",
    direction: str = "incoming",
) -> CallIntelReport:
    """Create a call intelligence report from screening data."""
    normalized = _normalize_number(number)
    prefix = _area_key(normalized)
    return CallIntelReport(
        number=number,
        normalized=normalized,
        prefix=prefix,
        timestamp_utc=datetime.now(UTC).isoformat(),
        stir_status=stir_status,
        presentation=presentation,
        duration_sec=duration_sec,
        direction=direction,
        answered=answered,
        contact_name=contact_name,
    )


# ---------------------------------------------------------------------------
#  Campaign Persistence
# ---------------------------------------------------------------------------

def save_campaigns(path: Path, campaigns: list[ScamCampaign]) -> None:
    """Save detected campaigns to a JSON file (thread-safe)."""
    with _CAMPAIGNS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_utc": datetime.now(UTC).isoformat(),
            "campaigns": [asdict(c) for c in campaigns],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_campaigns(path: Path) -> list[ScamCampaign]:
    """Load campaigns from JSON file."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        campaigns = []
        for c in data.get("campaigns", []):
            campaigns.append(ScamCampaign(**{
                k: v for k, v in c.items()
                if k in ScamCampaign.__dataclass_fields__
            }))
        return campaigns
    except (json.JSONDecodeError, OSError, TypeError):
        return []


def save_call_intel(path: Path, report: CallIntelReport) -> None:
    """Append a call intelligence report to JSONL file (thread-safe)."""
    with _CAMPAIGNS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(report)) + "\n")


def load_call_intel(path: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    """Load recent call intel reports from JSONL file."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        reports = []
        for line in lines[-limit:]:
            try:
                reports.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return reports
    except OSError:
        return []


# ---------------------------------------------------------------------------
#  Carrier Lookup Cache
# ---------------------------------------------------------------------------

def save_carrier_intel(path: Path, intel: CarrierIntel) -> None:
    """Cache carrier lookup result."""
    with _CAMPAIGNS_LOCK:
        cache = _load_carrier_cache(path)
        cache[intel.number] = asdict(intel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def lookup_carrier_cached(path: Path, number: str) -> CarrierIntel | None:
    """Check carrier cache for a number or its prefix."""
    cache = _load_carrier_cache(path)
    if number in cache:
        entry = cache[number]
        return CarrierIntel(**{
            k: v for k, v in entry.items()
            if k in CarrierIntel.__dataclass_fields__
        })
    return None


def _load_carrier_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
#  Prefix Blocking
# ---------------------------------------------------------------------------

def build_prefix_block_actions(
    campaigns: list[ScamCampaign],
    *,
    confidence_threshold: float = 0.60,
) -> list[dict[str, Any]]:
    """Generate block actions for entire prefixes when campaign confidence is high."""
    actions = []
    for campaign in campaigns:
        if campaign.confidence < confidence_threshold:
            continue
        # Block all known numbers in the campaign
        for number in campaign.numbers:
            actions.append({
                "action": "block_number",
                "number": number,
                "reason": f"scam_campaign_{campaign.campaign_id}",
                "campaign_id": campaign.campaign_id,
                "prefix": campaign.prefix,
                "confidence": campaign.confidence,
                "created_utc": datetime.now(UTC).isoformat(),
            })
        # If high confidence, recommend prefix-level silencing
        if campaign.confidence >= 0.75 and len(campaign.numbers) >= 3:
            actions.append({
                "action": "silence_prefix",
                "prefix": campaign.prefix,
                "reason": f"high_confidence_scam_campaign_{campaign.campaign_id}",
                "campaign_id": campaign.campaign_id,
                "confidence": campaign.confidence,
                "created_utc": datetime.now(UTC).isoformat(),
            })
    return actions


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _generate_campaign_id(prefix: str, numbers: list[str]) -> str:
    """Generate a stable campaign ID from prefix and number set."""
    content = prefix + "|" + ",".join(numbers)
    return hashlib.sha256(content.encode()).hexdigest()[:12]
