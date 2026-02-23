from __future__ import annotations

import imaplib
import json
import os
import socket
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email import message_from_bytes
from email.header import decode_header
from ipaddress import ip_address
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from jarvis_engine.connectors import (
    build_connector_prompts,
    evaluate_connector_statuses,
    serialize_statuses,
)

MAX_ICS_BYTES = 5 * 1024 * 1024


@dataclass
class SyncSummary:
    snapshot_path: str
    tasks: int
    calendar_events: int
    emails: int
    bills: int
    subscriptions: int
    medications: int
    school_items: int
    family_items: int
    projects: int
    connectors_ready: int
    connectors_pending: int
    connector_prompts: int


def build_live_snapshot(root: Path, output_path: Path) -> SyncSummary:
    planning = root / ".planning"
    planning.mkdir(parents=True, exist_ok=True)

    tasks = load_task_items(root)
    bills = _read_json_list(planning / "bills.json")
    subscriptions = _read_json_list(planning / "subscriptions.json")
    medications = _load_feed_json_list(root, "JARVIS_MEDICATIONS_JSON", planning / "medications.json")
    school_items = _load_feed_json_list(root, "JARVIS_SCHOOL_JSON", planning / "school.json")
    family_items = _load_feed_json_list(root, "JARVIS_FAMILY_JSON", planning / "family.json")
    projects = _load_feed_json_list(root, "JARVIS_PROJECTS_JSON", planning / "projects.json")
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
        "medications": medications,
        "school_items": school_items,
        "family_items": family_items,
        "projects": projects,
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
        medications=len(medications),
        school_items=len(school_items),
        family_items=len(family_items),
        projects=len(projects),
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


def _load_feed_json_list(repo_root: Path, env_key: str, default_path: Path) -> list[dict]:
    configured = os.getenv(env_key, "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        raw_path = str(configured_path)
        # Never allow UNC/network paths from env-configurable feeds.
        if raw_path.startswith("\\\\"):
            return []
        resolved = configured_path.resolve()
        if resolved.is_dir():
            return []
        allow_external = os.getenv("JARVIS_ALLOW_EXTERNAL_FEEDS", "").strip().lower() in {"1", "true", "yes"}
        if not allow_external:
            try:
                resolved.relative_to(repo_root.resolve())
            except ValueError:
                return []
        return _read_json_list(resolved)
    if not default_path.exists():
        default_path.parent.mkdir(parents=True, exist_ok=True)
        default_path.write_text("[]\n", encoding="utf-8")
    return _read_json_list(default_path)


def load_calendar_events(target_date: date | None = None) -> list[dict]:
    json_path = os.getenv("JARVIS_CALENDAR_JSON", "").strip()
    if json_path:
        return _read_json_list(Path(json_path))

    ics_url = os.getenv("JARVIS_CALENDAR_ICS_URL", "").strip()
    ics_file = os.getenv("JARVIS_CALENDAR_ICS_FILE", "").strip()
    allow_remote_url = os.getenv("JARVIS_ALLOW_REMOTE_CALENDAR_URLS", "").strip().lower() in {"1", "true", "yes"}
    if ics_file:
        p = Path(ics_file).expanduser()
        # Block symlinks and UNC paths to prevent path traversal
        if str(p).startswith("\\\\") or p.is_symlink():
            return []
        if p.exists():
            return _parse_ics(p.read_text(encoding="utf-8", errors="replace"), target_date=target_date)
    if ics_url:
        if not allow_remote_url:
            return []
        if not _is_safe_calendar_url(ics_url):
            return []
        try:
            with urlopen(ics_url, timeout=15) as resp:  # nosec B310
                payload = resp.read(MAX_ICS_BYTES + 1)
                if len(payload) > MAX_ICS_BYTES:
                    return []
                text = payload.decode("utf-8", errors="replace")
            return _parse_ics(text, target_date=target_date)
        except (URLError, TimeoutError, OSError):
            return []
    return []


def _parse_ics(text: str, target_date: date | None = None) -> list[dict]:
    """Parse ICS text using icalendar library with recurring event expansion.

    Falls back to a simple line-by-line parser if icalendar is not installed.
    """
    if not text.strip():
        return []
    try:
        from icalendar import Calendar
        import recurring_ical_events
    except ImportError:
        return _parse_ics_fallback(text)

    try:
        cal = Calendar.from_ical(text)
    except Exception:
        return _parse_ics_fallback(text)

    if target_date is None:
        target_date = date.today()
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=UTC)
    end = start + timedelta(days=1)

    try:
        expanded = recurring_ical_events.of(cal).between(start, end)
    except Exception:
        return _parse_ics_fallback(text)

    events: list[dict] = []
    for event in expanded:
        summary = str(event.get("SUMMARY", "Untitled event"))
        dtstart = event.get("DTSTART")
        dt_val = dtstart.dt if dtstart else None
        if dt_val is not None and hasattr(dt_val, "hour"):
            time_str = dt_val.strftime("%H:%M")
        else:
            time_str = "all-day"
        location = str(event.get("LOCATION", "")) if event.get("LOCATION") else ""
        description = str(event.get("DESCRIPTION", "")) if event.get("DESCRIPTION") else ""
        events.append(
            {
                "title": summary,
                "time": time_str,
                "location": location,
                "description": description[:200],
                "prep_needed": "yes",
            }
        )
    return sorted(events, key=lambda e: e["time"])


def _parse_ics_fallback(text: str) -> list[dict]:
    """Simple line-by-line ICS parser (fallback when icalendar is not installed)."""
    # Unfold RFC 5545 line folding (CRLF + space/tab continuation)
    text = text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")
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


def load_task_items(repo_root: Path) -> list[dict]:
    """Load tasks from the configured source (JSON file, Todoist, or Google Tasks).

    Source is selected via ``JARVIS_TASK_SOURCE`` env var (default: ``"json"``).
    """
    source = os.getenv("JARVIS_TASK_SOURCE", "json").strip().lower()

    if source == "todoist":
        return _load_todoist_tasks()
    if source == "google_tasks":
        # TODO: Google Tasks requires OAuth2 -- deferred to future phase.
        return []

    # Default: local JSON file
    json_path = os.getenv("JARVIS_TASKS_JSON", "").strip()
    if json_path:
        return _read_json_list(Path(json_path))
    return _read_json_list(repo_root / ".planning" / "tasks.json")


def _load_todoist_tasks() -> list[dict]:
    """Fetch today's and overdue tasks from Todoist REST API."""
    token = os.getenv("JARVIS_TODOIST_TOKEN", "").strip()
    if not token:
        return []
    try:
        from todoist_api_python.api import TodoistAPI

        api = TodoistAPI(token)
        tasks = api.get_tasks(filter="today | overdue")
        priority_map = {4: "urgent", 3: "high", 2: "normal", 1: "low"}
        return [
            {
                "title": t.content,
                "priority": priority_map.get(t.priority, "normal"),
                "due_date": t.due.date if t.due else "",
                "status": "pending",
            }
            for t in tasks
        ]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Todoist API call failed: %s", exc)
        return []


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
        with imaplib.IMAP4_SSL(host, timeout=30) as client:
            client.login(user, password)
            client.select("INBOX", readonly=True)
            typ, data = client.search(None, "UNSEEN")
            if typ != "OK" or not data or not data[0]:
                return []
            ids = data[0].split()[-limit:]
            for msg_id in ids:
                typ2, msg_data = client.fetch(msg_id, "(RFC822.HEADER)")
                if typ2 != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                raw_bytes = msg_data[0][1]
                msg = message_from_bytes(raw_bytes)
                subject = _decode_email_header(msg.get("Subject", "No subject"))
                sender = _decode_email_header(msg.get("From", ""))
                date_str = msg.get("Date", "")
                importance = _triage_email(sender, subject)
                items.append(
                    {
                        "subject": subject,
                        "from": sender,
                        "date": date_str,
                        "read": False,
                        "importance": importance,
                    }
                )
    except (imaplib.IMAP4.error, OSError, TimeoutError):
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


def _triage_email(sender: str, subject: str) -> str:
    """Multi-signal email importance triage using sender and subject keywords."""
    lowered_subject = subject.lower()
    lowered_sender = sender.lower()
    high_subject_markers = [
        "urgent", "action required", "payment due", "invoice",
        "security", "incident", "deadline", "expiring", "overdue",
    ]
    high_sender_markers = ["noreply@", "alert@", "billing@", "security@", "no-reply@"]
    if any(m in lowered_subject for m in high_subject_markers):
        return "high"
    # Extract email address portion for sender matching to avoid substring false positives
    sender_email = lowered_sender
    if "<" in sender_email:
        sender_email = sender_email.split("<")[-1].rstrip(">")
    if any(sender_email.startswith(m) or sender_email.split("@")[0] + "@" == m for m in high_sender_markers):
        return "high"
    return "normal"


def _email_importance(subject: str) -> str:
    """Legacy single-signal importance (kept for backward compatibility)."""
    return _triage_email("", subject)


def _is_safe_calendar_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost"}:
        return False
    try:
        ip = ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        pass
    try:
        resolved = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for item in resolved:
        raw_ip = item[4][0]
        try:
            ip = ip_address(raw_ip)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    return True
