from __future__ import annotations

import imaplib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from jarvis_engine.connectors import (
    build_connector_prompts,
    evaluate_connector_statuses,
    serialize_statuses,
)


@dataclass
class SyncSummary:
    snapshot_path: str
    tasks: int
    calendar_events: int
    emails: int
    bills: int
    subscriptions: int
    connectors_ready: int
    connectors_pending: int
    connector_prompts: int


def build_live_snapshot(root: Path, output_path: Path) -> SyncSummary:
    planning = root / ".planning"
    planning.mkdir(parents=True, exist_ok=True)

    tasks = _read_json_list(planning / "tasks.json")
    bills = _read_json_list(planning / "bills.json")
    subscriptions = _read_json_list(planning / "subscriptions.json")
    calendar_events = load_calendar_events()
    emails = load_email_items()
    connector_statuses = evaluate_connector_statuses(root)
    connector_prompts = build_connector_prompts(connector_statuses)

    snapshot = {
        "date": datetime.now(UTC).date().isoformat(),
        "tasks": tasks,
        "calendar_events": calendar_events,
        "emails": emails,
        "bills": bills,
        "subscriptions": subscriptions,
        "connector_statuses": serialize_statuses(connector_statuses),
        "connector_prompts": connector_prompts,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, ensure_ascii=True, indent=2), encoding="utf-8")
    connectors_ready = sum(1 for status in connector_statuses if status.ready)
    return SyncSummary(
        snapshot_path=str(output_path),
        tasks=len(tasks),
        calendar_events=len(calendar_events),
        emails=len(emails),
        bills=len(bills),
        subscriptions=len(subscriptions),
        connectors_ready=connectors_ready,
        connectors_pending=len(connector_statuses) - connectors_ready,
        connector_prompts=len(connector_prompts),
    )


def _read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def load_calendar_events() -> list[dict]:
    json_path = os.getenv("JARVIS_CALENDAR_JSON", "").strip()
    if json_path:
        return _read_json_list(Path(json_path))

    ics_url = os.getenv("JARVIS_CALENDAR_ICS_URL", "").strip()
    ics_file = os.getenv("JARVIS_CALENDAR_ICS_FILE", "").strip()
    if ics_file:
        p = Path(ics_file)
        if p.exists():
            return _parse_ics(p.read_text(encoding="utf-8", errors="replace"))
    if ics_url:
        try:
            with urlopen(ics_url, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            return _parse_ics(text)
        except Exception:
            return []
    return []


def _parse_ics(text: str) -> list[dict]:
    lines = [line.strip() for line in text.splitlines()]
    events: list[dict] = []
    current: dict[str, str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                events.append(
                    {
                        "title": current.get("SUMMARY", "Untitled event"),
                        "time": current.get("DTSTART", ""),
                        "prep_needed": "yes",
                    }
                )
            current = None
            continue
        if current is None:
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.split(";", 1)[0]
        current[key] = value
    return events


def load_email_items(limit: int = 20) -> list[dict]:
    json_path = os.getenv("JARVIS_EMAIL_JSON", "").strip()
    if json_path:
        return _read_json_list(Path(json_path))

    host = os.getenv("JARVIS_IMAP_HOST", "").strip()
    user = os.getenv("JARVIS_IMAP_USER", "").strip()
    password = os.getenv("JARVIS_IMAP_PASS", "").strip()
    if not host or not user or not password:
        return []

    items: list[dict] = []
    try:
        with imaplib.IMAP4_SSL(host) as client:
            client.login(user, password)
            client.select("INBOX")
            typ, data = client.search(None, "UNSEEN")
            if typ != "OK":
                return []
            ids = data[0].split()[-limit:]
            for msg_id in ids:
                typ2, msg_data = client.fetch(msg_id, "(RFC822.HEADER)")
                if typ2 != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                raw_bytes = msg_data[0][1]
                msg = message_from_bytes(raw_bytes)
                subject = _decode_email_header(msg.get("Subject", "No subject"))
                importance = _email_importance(subject)
                items.append(
                    {
                        "subject": subject,
                        "read": False,
                        "importance": importance,
                    }
                )
    except Exception:
        return []
    return items


def _decode_email_header(value: str) -> str:
    decoded = decode_header(value)
    parts: list[str] = []
    for item, charset in decoded:
        if isinstance(item, bytes):
            enc = charset or "utf-8"
            try:
                parts.append(item.decode(enc, errors="replace"))
            except LookupError:
                parts.append(item.decode("utf-8", errors="replace"))
        else:
            parts.append(str(item))
    return "".join(parts).strip()


def _email_importance(subject: str) -> str:
    lowered = subject.lower()
    high_markers = ["urgent", "action required", "payment due", "invoice", "security", "incident"]
    return "high" if any(m in lowered for m in high_markers) else "normal"
