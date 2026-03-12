from __future__ import annotations

import http.client
import imaplib
import logging
import os
import socket
from typing import IO
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from jarvis_engine._compat import UTC
from email import message_from_bytes
from email.header import decode_header
from ipaddress import ip_address
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from jarvis_engine._shared import atomic_write_json as _atomic_write_json

from jarvis_engine.connectors import (
    build_connector_prompts,
    evaluate_connector_statuses,
    serialize_statuses,
)

logger = logging.getLogger(__name__)

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
    _atomic_write_json(output_path, snapshot, secure=False)
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
    from jarvis_engine._shared import load_json_file

    raw = load_json_file(path, None, expected_type=list)
    if raw is None:
        return []
    return [x for x in raw if isinstance(x, dict)]


def _load_feed_json_list(repo_root: Path, env_key: str, default_path: Path) -> list[dict]:
    configured = os.getenv(env_key, "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        raw_path = str(configured_path)
        # Never allow UNC/network paths from env-configurable feeds.
        if raw_path.startswith("\\\\") or raw_path.startswith("//"):
            return []
        resolved = configured_path.resolve()
        if resolved.is_dir():
            return []
        allow_external = os.getenv("JARVIS_ALLOW_EXTERNAL_FEEDS", "").strip().lower() in {"1", "true", "yes"}
        if not allow_external:
            try:
                resolved.relative_to(repo_root.resolve())
            except ValueError:
                logger.debug("Tasks file %s is outside repo root; skipping (set JARVIS_ALLOW_EXTERNAL_FEEDS=1 to allow)", resolved)
                return []
        return _read_json_list(resolved)
    if not default_path.exists():
        default_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(default_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                os.write(fd, b"[]\n")
            finally:
                os.close(fd)
        except FileExistsError:
            logger.debug("Tasks file already exists (race): %s", default_path)
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
        if p.is_file():
            try:
                return _parse_ics(p.read_text(encoding="utf-8", errors="replace"), target_date=target_date)
            except OSError as exc:
                logger.warning("Failed to read ICS file %s: %s", p, exc)
                return []
    if ics_url:
        if not allow_remote_url:
            return []
        safe_result = _is_safe_calendar_url(ics_url)
        if not safe_result:
            return []
        # Pin the resolved IP to prevent DNS rebinding between validation
        # and actual connection.  Replace the hostname with the validated IP
        # and pass the original hostname via Host header.
        pinned_ip, original_host = safe_result
        parsed_ics = urlparse(ics_url)
        pinned_url = parsed_ics._replace(netloc=f"{pinned_ip}:{parsed_ics.port or 443}").geturl()
        try:
            # Use a no-redirect opener to prevent SSRF bypass via HTTP redirect
            # to internal IPs after initial URL validation.
            opener = _build_no_redirect_opener()
            from urllib.request import Request as _IcsRequest
            pinned_req = _IcsRequest(pinned_url, headers={"Host": original_host})
            with opener.open(pinned_req, timeout=15) as resp:  # nosec B310
                payload = resp.read(MAX_ICS_BYTES + 1)
                if len(payload) > MAX_ICS_BYTES:
                    return []
                text = payload.decode("utf-8", errors="replace")
            return _parse_ics(text, target_date=target_date)
        except (URLError, TimeoutError, OSError) as exc:
            logger.debug("ICS calendar fetch failed for %s: %s", ics_url, exc)
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
        import recurring_ical_events  # type: ignore[import-untyped]
    except ImportError:
        return _parse_ics_fallback(text, target_date)

    try:
        cal = Calendar.from_ical(text)
    except (ValueError, TypeError, KeyError) as exc:
        logger.debug("Calendar parsing failed, using fallback: %s", exc)
        return _parse_ics_fallback(text, target_date)

    if target_date is None:
        target_date = date.today()
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=UTC)
    end = start + timedelta(days=1)

    try:
        expanded = recurring_ical_events.of(cal).between(start, end)
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        logger.debug("Recurring event expansion failed, using fallback: %s", exc)
        return _parse_ics_fallback(text, target_date)

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


def _parse_ics_fallback(text: str, target_date: date | None = None) -> list[dict]:
    """Simple line-by-line ICS parser (fallback when icalendar is not installed)."""
    # Unfold RFC 5545 line folding (CRLF + space/tab continuation)
    text = text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")
    lines = [line.strip() for line in text.splitlines()]
    events: list[dict] = []
    current: dict[str, str] | None = None
    target_str = target_date.strftime("%Y%m%d") if target_date is not None else None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {
                "SUMMARY": "",
                "DTSTART": "",
                "LOCATION": "",
                "DESCRIPTION": "",
            }
            continue
        if line == "END:VEVENT":
            if current is not None:
                raw_dt = current.get("DTSTART", "")
                # Filter by target_date if provided
                if target_str is not None:
                    dt_date_part = raw_dt[:8] if len(raw_dt) >= 8 else ""
                    if dt_date_part and dt_date_part != target_str:
                        current = None
                        continue
                # Format time from raw DTSTART
                if "T" in raw_dt and len(raw_dt) >= 15:
                    time_str = f"{raw_dt[9:11]}:{raw_dt[11:13]}"
                else:
                    time_str = "all-day"
                events.append(
                    {
                        "title": str(current.get("SUMMARY", "")).strip() or "Untitled event",
                        "time": time_str,
                        "location": current.get("LOCATION", ""),
                        "description": current.get("DESCRIPTION", "")[:200],
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
    return sorted(events, key=lambda e: e["time"])


def load_task_items(repo_root: Path) -> list[dict]:
    """Load tasks from the configured source (JSON file, Todoist, or Google Tasks).

    Source is selected via ``JARVIS_TASK_SOURCE`` env var (default: ``"json"``).
    """
    source = os.getenv("JARVIS_TASK_SOURCE", "json").strip().lower()

    if source == "todoist":
        return _load_todoist_tasks()
    if source == "google_tasks":
        # NOTE: Google Tasks integration requires OAuth2 with tasks.readonly scope.
        # Deferred -- see ROADMAP.md for future phase planning.
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
        from todoist_api_python.api import TodoistAPI  # type: ignore[import-not-found]

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
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        logger.warning("Todoist API call failed: %s", exc)
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
        with imaplib.IMAP4_SSL(host, timeout=10) as client:
            try:
                client.login(user, password)
            except (imaplib.IMAP4.error, imaplib.IMAP4.abort) as exc:
                logger.warning("IMAP auth failed for %s@%s: %s", user, host, exc)
                return []
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
    except (OSError, TimeoutError, imaplib.IMAP4.error, imaplib.IMAP4.abort) as exc:
        logger.warning("IMAP connection to %s failed: %s", host, exc)
        return []
    return items


def _decode_email_header(value: str) -> str:
    try:
        decoded = decode_header(value)
    except (ValueError, UnicodeDecodeError, LookupError):
        # Malformed headers can cause decode_header to raise; return raw value.
        return str(value).strip()
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


class _NoRedirectHandler(HTTPRedirectHandler):
    """Redirect handler that raises on any redirect to prevent SSRF via redirect."""

    def redirect_request(  # type: ignore[override]
        self,
        req: "Request",
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: "http.client.HTTPMessage",
        newurl: str,
    ) -> "Request":
        from urllib.error import HTTPError
        raise HTTPError(newurl, code, f"Redirects are not allowed (got {code})", headers, fp)


def _build_no_redirect_opener():
    """Build a urllib opener that blocks HTTP redirects."""
    return build_opener(_NoRedirectHandler)


def _is_safe_calendar_url(url: str) -> tuple[str, str] | None:
    """Validate a calendar URL for SSRF safety.

    Returns ``(pinned_ip, original_host)`` on success so the caller can
    connect directly to the validated IP (preventing DNS rebinding).
    Returns ``None`` if the URL is unsafe.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost"}:
        return None
    try:
        ip = ip_address(host)
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return None
        return (host, host)
    except ValueError as exc:
        logger.debug("Host is not an IP literal, resolving as hostname: %s", exc)
    # Resolve DNS without using the process-global socket.setdefaulttimeout()
    # which would affect all sockets in the process.  The resolved IP is
    # returned so the caller can pin it for the actual connection, preventing
    # DNS rebinding between validation and use.
    try:
        resolved = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        logger.debug("DNS resolution failed for calendar host %s: %s", host, exc)
        return None
    first_safe_ip: str | None = None
    for item in resolved:
        if not item[4]:
            continue
        raw_ip: str = str(item[4][0])
        try:
            ip = ip_address(raw_ip)
        except ValueError:
            logger.debug("Invalid IP address %r in DNS response for %s", raw_ip, host)
            return None
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return None
        if first_safe_ip is None:
            first_safe_ip = raw_ip
    if first_safe_ip is None:
        return None
    return (first_safe_ip, host)

