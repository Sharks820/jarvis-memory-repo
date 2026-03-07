from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from jarvis_engine._compat import UTC
from pathlib import Path
from typing import Any

from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine._shared import safe_float as _safe_float

_ACTIONS_LOCK = threading.Lock()


@dataclass
class SpamCandidate:
    number: str
    score: float
    calls: int
    missed_ratio: float
    avg_duration_s: float
    reasons: list[str]


@dataclass
class PhoneAction:
    action: str
    number: str
    message: str
    created_utc: str
    reason: str


def load_call_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    return out


def detect_spam_candidates(
    call_log: list[dict[str, Any]], now_utc: datetime | None = None
) -> list[SpamCandidate]:
    now = now_utc or datetime.now(UTC)
    lookback = now - timedelta(days=14)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in call_log:
        raw_number = str(item.get("number", "")).strip()
        number = _normalize_number(raw_number)
        if not number:
            continue
        ts = _parse_ts(
            item.get("ts_utc") or item.get("date_utc") or item.get("date", "")
        )
        if not ts or ts < lookback:
            continue
        grouped[number].append(item)

    area_distinct_numbers: dict[str, set[str]] = defaultdict(set)
    area_suspicious_events: dict[str, int] = defaultdict(int)
    for number, records in grouped.items():
        area = _area_key(number)
        if not area:
            continue
        area_distinct_numbers[area].add(number)
        for record in records:
            call_type = str(record.get("type", record.get("direction", ""))).lower()
            duration = _safe_float(
                record.get("duration_sec", record.get("duration", 0.0))
            )
            contact = str(record.get("contact_name", "")).strip()
            if (
                any(term in call_type for term in ["missed", "rejected", "declined"])
                and duration <= 12
                and not contact
            ):
                area_suspicious_events[area] += 1

    candidates: list[SpamCandidate] = []
    for number, records in grouped.items():
        calls = len(records)
        missed = 0
        total_duration = 0.0
        inbound = 0
        flagged_label = False
        no_contact = 0
        day_buckets: dict[str, int] = defaultdict(int)

        for r in records:
            call_type = str(r.get("type", r.get("direction", ""))).lower()
            duration = _safe_float(r.get("duration_sec", r.get("duration", 0.0)))
            total_duration += duration
            if any(
                term in call_type
                for term in ["missed", "rejected", "declined", "ignored"]
            ):
                missed += 1
            if any(
                term in call_type
                for term in ["incoming", "inbound", "missed", "rejected", "declined"]
            ):
                inbound += 1
            label = (
                str(r.get("caller_label", "")) + " " + str(r.get("contact_name", ""))
            ).lower()
            if any(term in label for term in ["spam", "scam", "telemarketer", "fraud"]):
                flagged_label = True
            if not str(r.get("contact_name", "")).strip():
                no_contact += 1
            ts = _parse_ts(r.get("ts_utc") or r.get("date_utc") or r.get("date", ""))
            if ts:
                day_buckets[ts.date().isoformat()] += 1

        avg_duration = total_duration / float(calls) if calls else 0.0
        missed_ratio = missed / float(calls) if calls else 0.0
        peak_day_calls = max(day_buckets.values()) if day_buckets else 0
        inbound_ratio = inbound / float(calls) if calls else 0.0
        no_contact_ratio = no_contact / float(calls) if calls else 0.0

        score = 0.0
        reasons: list[str] = []

        if calls >= 4:
            score += 0.32
            reasons.append("high_repeat_volume")
        elif calls >= 3:
            score += 0.22
            reasons.append("repeat_volume")

        if missed_ratio >= 0.8 and avg_duration <= 15:
            score += 0.24
            reasons.append("mostly_missed_short_calls")

        if inbound_ratio >= 0.9 and no_contact_ratio >= 0.9:
            score += 0.2
            reasons.append("unknown_inbound_pattern")

        if peak_day_calls >= 2:
            score += 0.12
            reasons.append("burst_day_pattern")

        if flagged_label:
            score += 0.35
            reasons.append("spam_or_scam_label")

        area = _area_key(number)
        if area:
            distinct_in_area = len(area_distinct_numbers.get(area, set()))
            suspicious_area_events = area_suspicious_events.get(area, 0)
            if distinct_in_area >= 6 and suspicious_area_events >= 8:
                score += 0.18
                reasons.append("rotating_number_area_pattern")

        score = min(score, 0.99)
        if score <= 0:
            continue

        candidates.append(
            SpamCandidate(
                number=number,
                score=round(score, 4),
                calls=calls,
                missed_ratio=round(missed_ratio, 4),
                avg_duration_s=round(avg_duration, 2),
                reasons=reasons,
            )
        )

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates


def build_spam_block_actions(
    candidates: list[SpamCandidate],
    *,
    threshold: float = 0.65,
    add_global_silence_rule: bool = True,
) -> list[PhoneAction]:
    actions: list[PhoneAction] = []
    high_risk_count = 0
    for candidate in candidates:
        if candidate.score < threshold:
            continue
        high_risk_count += 1
        actions.append(
            PhoneAction(
                action="block_number",
                number=candidate.number,
                message="",
                created_utc=_now_iso(),
                reason="spam_guard",
            )
        )
    if add_global_silence_rule and high_risk_count >= 5:
        actions.append(
            PhoneAction(
                action="silence_unknown_callers",
                number="",
                message="duration=24h",
                created_utc=_now_iso(),
                reason="high_spam_volume_detected",
            )
        )
    return actions


def build_phone_action(
    action: str, number: str, message: str = "", reason: str = "voice_or_text_request"
) -> PhoneAction:
    if action not in {
        "send_sms",
        "place_call",
        "ignore_call",
        "block_number",
        "silence_unknown_callers",
    }:
        raise ValueError(f"Unsupported action: {action}")
    normalized = _normalize_number(number)
    if action == "silence_unknown_callers":
        normalized = ""
    elif not normalized:
        raise ValueError("Invalid phone number.")
    if action == "send_sms" and not message.strip():
        raise ValueError("SMS action requires message.")
    return PhoneAction(
        action=action,
        number=normalized,
        message=message.strip(),
        created_utc=_now_iso(),
        reason=reason,
    )


def append_phone_actions(path: Path, actions: list[PhoneAction]) -> None:
    with _ACTIONS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for action in actions:
                handle.write(json.dumps(asdict(action), ensure_ascii=True) + "\n")


def write_spam_report(
    path: Path,
    candidates: list[SpamCandidate],
    actions: list[PhoneAction],
    threshold: float,
) -> None:
    payload = {
        "generated_utc": _now_iso(),
        "threshold": threshold,
        "candidates": [asdict(c) for c in candidates],
        "actions": [asdict(a) for a in actions],
        "prompt_options": {
            "voice": "Jarvis, block likely spam calls now",
            "tap_url": "https://www.samsung.com/us/support/answer/ANS10003465/",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _normalize_number(number: str) -> str:
    if not number:
        return ""
    cleaned = re.sub(r"[^\d+]", "", number)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("+") and len(cleaned) >= 8:
        return cleaned
    digits = re.sub(r"\D", "", cleaned)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) >= 8:
        return f"+{digits}"
    return ""


def _parse_ts(value: Any) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _area_key(number: str) -> str:
    if number.startswith("+1") and len(number) >= 8:
        # US/Canada: country + NPA-NXX (exchange-level precision)
        return number[:8]
    if number.startswith("+") and len(number) >= 6:
        return number[:6]
    return ""
