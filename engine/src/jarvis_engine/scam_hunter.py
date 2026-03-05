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

    # Group by prefix — shallow-copy to avoid mutating caller's data
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
        entry = dict(report)
        entry["_normalized"] = number
        entry["_prefix"] = prefix
        prefix_groups[prefix].append(entry)

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
            signals.append("number_pair")

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
            if span == 0:
                # All calls at exact same second — extreme burst
                confidence += 0.15
                signals.append("burst_pattern_instant")
            elif len(timestamps) / (span / 3600) >= 3:
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
    caller_display_name: str = "",
    gateway_domain: str = "",
    setup_latency_ms: int = 0,
) -> float:
    """Compute an enhanced spam score incorporating ALL available signals.

    Augments the base phone_guard score with STIR/SHAKEN, VoIP detection,
    carrier labels, gateway info, call setup latency, and campaign data.
    """
    if is_in_contacts:
        return 0.0

    score = base_score

    # Carrier SCAM/SPAM label (Samsung Smart Call, T-Mobile Scam Shield)
    if caller_display_name:
        name_upper = caller_display_name.upper()
        if any(label in name_upper for label in _SCAM_LABELS):
            score += 0.50

    # STIR/SHAKEN
    if stir_status == "failed":
        score += 0.40
    elif stir_status == "not_verified" and score > 0:
        score += 0.05

    # VoIP / line type
    if line_type == "non_fixed_voip":
        score += 0.20
    elif line_type == "voip":
        score += 0.12

    # VoIP gateway domain (check dot boundary to avoid spoofing)
    if gateway_domain:
        gw = gateway_domain.lower()
        known_voip = any(gw == d or gw.endswith("." + d) for d in _KNOWN_VOIP_DOMAINS)
        if known_voip:
            score += 0.15

    # Call setup latency (VoIP transcoding > 1500ms)
    if setup_latency_ms > 1500:
        score += 0.08

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


# Known VoIP wholesale/gateway domains
_KNOWN_VOIP_DOMAINS = frozenset({
    "bandwidth.com", "twilio.com", "vonage.com", "lingo.com",
    "telnyx.com", "sinch.com", "peerless.com", "intelepeer.com",
    "magicjack.com", "level3.com", "commio.com",
})

# Carrier-applied scam labels (checked via substring match — avoid broad terms)
_SCAM_LABELS = frozenset({
    "SCAM LIKELY", "SPAM RISK", "POTENTIAL SPAM", "SUSPECTED SPAM",
    "FRAUD RISK", "TELEMARKETER", "ROBOCALL", "SPAM", "SCAM", "FRAUD",
})


# ---------------------------------------------------------------------------
#  NANPA NPA-NXX carrier identification (free, no API key)
# ---------------------------------------------------------------------------

# Known VoIP wholesale provider OCNs and their NPA-NXX blocks.
# This is a subset — the full NANPA file has ~50k entries.
# These are the top robocall-associated carriers by FCC enforcement data.
KNOWN_VOIP_OCNS: dict[str, str] = {
    "981E": "Bandwidth.com",
    "6943": "Twilio",
    "8561": "Vonage",
    "B067": "Telnyx",
    "692E": "Lingo Telecom",
    "8825": "Peerless Network",
    "6529": "Level 3 / Lumen",
    "935F": "Commio",
    "5765": "Intelepeer",
    "508E": "MagicJack",
}


def identify_voip_carrier_from_prefix(number: str) -> str | None:
    """Check if a number's NPA-NXX is assigned to a known VoIP provider.

    Uses the area key to look up against known VoIP OCN prefixes.
    Returns the carrier name if known VoIP, None otherwise.
    """
    # This is a stub for NANPA integration. When the NANPA CSV is loaded,
    # this function would look up the NPA-NXX block assignment.
    # For now it returns None — the carrier cache handles external lookups.
    return None


# ---------------------------------------------------------------------------
#  Time-of-day scoring
# ---------------------------------------------------------------------------

# US area code → timezone mapping (major codes, covers >80% of US numbers)
_AREA_CODE_TZ: dict[str, str] = {
    # Eastern
    "201": "ET", "202": "ET", "203": "ET", "212": "ET", "215": "ET",
    "216": "ET", "240": "ET", "267": "ET", "301": "ET", "302": "ET",
    "305": "ET", "313": "ET", "315": "ET", "321": "ET", "336": "ET",
    "347": "ET", "352": "ET", "386": "ET", "401": "ET", "404": "ET",
    "407": "ET", "410": "ET", "412": "ET", "413": "ET", "443": "ET",
    "484": "ET", "508": "ET", "516": "ET", "518": "ET", "540": "ET",
    "551": "ET", "561": "ET", "571": "ET", "585": "ET", "586": "ET",
    "601": "ET", "603": "ET", "609": "ET", "610": "ET", "614": "ET",
    "616": "ET", "617": "ET", "631": "ET", "646": "ET", "678": "ET",
    "703": "ET", "704": "ET", "706": "ET", "716": "ET", "717": "ET",
    "718": "ET", "732": "ET", "757": "ET", "770": "ET", "772": "ET",
    "774": "ET", "781": "ET", "786": "ET", "802": "ET", "803": "ET",
    "804": "ET", "813": "ET", "828": "ET", "843": "ET", "845": "ET",
    "848": "ET", "856": "ET", "860": "ET", "862": "ET", "863": "ET",
    "904": "ET", "908": "ET", "910": "ET", "914": "ET", "917": "ET",
    "919": "ET", "929": "ET", "941": "ET", "954": "ET", "973": "ET",
    # Central
    "205": "CT", "210": "CT", "214": "CT", "225": "CT", "228": "CT",
    "254": "CT", "262": "CT", "281": "CT", "309": "CT", "312": "CT",
    "314": "CT", "316": "CT", "318": "CT", "319": "CT", "320": "CT",
    "325": "CT", "331": "CT", "334": "CT", "346": "CT", "361": "CT",
    "402": "CT", "405": "CT", "409": "CT", "414": "CT", "417": "CT",
    "430": "CT", "432": "CT", "469": "CT", "479": "CT", "501": "CT",
    "502": "CT", "504": "CT", "507": "CT", "512": "CT", "515": "CT",
    "563": "CT", "573": "CT", "612": "CT", "615": "CT", "618": "CT",
    "630": "CT", "636": "CT", "641": "CT", "651": "CT", "682": "CT",
    "708": "CT", "713": "CT", "715": "CT", "731": "CT", "737": "CT",
    "763": "CT", "769": "CT", "773": "CT", "779": "CT", "806": "CT",
    "808": "HT", "815": "CT", "816": "CT", "817": "CT", "830": "CT",
    "832": "CT", "847": "CT", "850": "CT", "870": "CT", "901": "CT",
    "903": "CT", "913": "CT", "915": "CT", "918": "CT", "920": "CT",
    "936": "CT", "940": "CT", "952": "CT", "956": "CT", "972": "CT",
    "979": "CT",
    # Mountain
    "303": "MT", "307": "MT", "385": "MT", "406": "MT", "435": "MT",
    "480": "MT", "505": "MT", "520": "MT", "575": "MT", "602": "MT",
    "623": "MT", "719": "MT", "720": "MT", "801": "MT",
    # Pacific
    "206": "PT", "209": "PT", "213": "PT", "253": "PT", "310": "PT",
    "323": "PT", "360": "PT", "408": "PT", "415": "PT", "424": "PT",
    "425": "PT", "442": "PT", "503": "PT", "509": "PT", "510": "PT",
    "530": "PT", "541": "PT", "559": "PT", "562": "PT", "619": "PT",
    "626": "PT", "650": "PT", "657": "PT", "661": "PT", "669": "PT",
    "707": "PT", "714": "PT", "747": "PT", "760": "PT", "805": "PT",
    "818": "PT", "831": "PT", "858": "PT", "909": "PT", "916": "PT",
    "925": "PT", "949": "PT", "951": "PT", "971": "PT",
}

# UTC offsets for time zone abbreviations (standard time)
_TZ_UTC_OFFSETS = {"ET": -5, "CT": -6, "MT": -7, "PT": -8, "HT": -10}


def score_time_of_day(number: str, call_utc: datetime | None = None) -> float:
    """Score a call based on time of day in the caller's area code timezone.

    Legitimate business calls happen 8 AM - 9 PM. Calls outside this
    window from unknown numbers are more suspicious.

    Returns: 0.0 (normal hours) to 0.15 (extremely unusual hours)
    """
    now = call_utc or datetime.now(UTC)
    digits = re.sub(r"\D", "", number)
    # Only handle US/Canadian numbers (country code +1).
    # Non-US numbers with "+" prefix and a different country code return 0.0.
    if number.lstrip().startswith("+") and not digits.startswith("1"):
        return 0.0
    if digits.startswith("1") and len(digits) >= 4:
        area_code = digits[1:4]
    elif len(digits) >= 3:
        area_code = digits[:3]
    else:
        return 0.0

    tz_abbr = _AREA_CODE_TZ.get(area_code)
    if not tz_abbr:
        return 0.0

    utc_offset = _TZ_UTC_OFFSETS.get(tz_abbr, 0)
    caller_hour = (now.hour + utc_offset) % 24

    # Extremely suspicious: 11 PM - 6 AM caller local time
    if caller_hour >= 23 or caller_hour < 6:
        return 0.15
    # Suspicious: 9 PM - 11 PM or 6 AM - 8 AM
    if caller_hour >= 21 or caller_hour < 8:
        return 0.05
    return 0.0


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
    """Save detected campaigns to a JSON file (thread-safe, atomic write)."""
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    with _CAMPAIGNS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_utc": datetime.now(UTC).isoformat(),
            "campaigns": [asdict(c) for c in campaigns],
        }
        _atomic_write_json(path, payload)


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
    """Load recent call intel reports from JSONL file (tail-read for efficiency)."""
    if not path.exists():
        return []
    try:
        # Tail-read: estimate ~256 bytes per line, read only what we need.
        _BYTES_PER_LINE = 256
        fsize = path.stat().st_size
        tail_bytes = min(fsize, limit * _BYTES_PER_LINE * 2)
        with open(path, "r", encoding="utf-8") as f:
            if fsize > tail_bytes:
                f.seek(fsize - tail_bytes)
                f.readline()  # skip partial first line
            lines = f.read().strip().splitlines()
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
    from jarvis_engine._shared import atomic_write_json
    with _CAMPAIGNS_LOCK:
        cache = _load_carrier_cache(path)
        cache[intel.number] = asdict(intel)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, cache, secure=False)


def lookup_carrier_cached(path: Path, number: str) -> CarrierIntel | None:
    """Check carrier cache for a number or its prefix."""
    with _CAMPAIGNS_LOCK:
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
