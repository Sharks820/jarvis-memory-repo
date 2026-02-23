# Phase 4: Connectors and Daily Intelligence - Research

**Researched:** 2026-02-23
**Domain:** Calendar/email/task connectors, ICS parsing, IMAP email, LLM-powered briefing narrative
**Confidence:** HIGH

## Summary

Phase 4 replaces stub data with real integrations for three domains (calendar, email, tasks) and transforms the existing `build_daily_brief()` from a line-counting summary into a genuinely useful narrative briefing that weaves all data sources together with memory context. The codebase already has substantial scaffolding: `ops_sync.py` contains a working ICS parser (handles `VEVENT` blocks) and a real IMAP reader (using stdlib `imaplib`), `connectors.py` defines connector definitions with env-var and permission gates, `life_ops.py` generates a structured daily brief from an `OpsSnapshot` dataclass, and the Phase 3 `ModelGateway` provides LLM summarization. The main gaps are: (1) the ICS parser is minimal -- no recurring event (RRULE) expansion, no date filtering, no proper timezone handling; (2) the IMAP reader only fetches headers and classifies importance by subject keywords -- it needs body/sender triage; (3) there is no real task source integration (tasks come from a local JSON file); and (4) the daily briefing is a mechanical line count, not a narrative.

The recommended approach uses the `icalendar` library (v7.0.1) with `recurring-ical-events` (v3.8.1) for robust ICS parsing with RRULE expansion, retains stdlib `imaplib` (already in use) but upgrades the triage logic to include sender reputation and body scanning, and adds a flexible task source layer that reads from a local JSON file (default), a Todoist API, or a Google Tasks API depending on configuration. The daily briefing gets a two-stage generation: (1) deterministic data assembly (what the codebase already does well) followed by (2) LLM-powered narrative synthesis via the Phase 3 `ModelGateway` that weaves calendar + email + tasks + medications + memory context into a coherent paragraph the owner can act on.

**Primary recommendation:** Upgrade `ops_sync.py` calendar/email/task functions to use real data sources with proper parsing, add `icalendar` + `recurring-ical-events` as dependencies, keep `imaplib` (stdlib) for email, add a pluggable task source layer, and wire `ModelGateway.complete()` into `build_daily_brief()` for narrative generation -- routing the briefing prompt through the local Ollama model (privacy-safe, zero cost).

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| CONN-01 | Calendar connector reads real events from Google Calendar or ICS feed | Upgrade existing `_parse_ics()` in `ops_sync.py` to use `icalendar` library with `recurring-ical-events` for RRULE expansion and timezone handling. Support three input modes: ICS file, ICS URL, JSON feed (all already env-gated via `JARVIS_CALENDAR_JSON`, `JARVIS_CALENDAR_ICS_FILE`, `JARVIS_CALENDAR_ICS_URL`). Google Calendar API (via google-api-python-client + OAuth2) is a stretch option but ICS secret URL is simpler and sufficient for single-user read-only use. |
| CONN-02 | Email connector reads and triages messages via IMAP (read-only initially) | Upgrade existing `load_email_items()` in `ops_sync.py` -- already uses `imaplib.IMAP4_SSL` with RFC822.HEADER fetch. Enhance: fetch From/Date headers alongside Subject, add sender-reputation triage (known contacts vs unknown), add body snippet extraction for importance classification. Keep read-only (no STORE, no DELETE). imap-tools library is optional upgrade if imaplib proves too low-level. |
| CONN-03 | Task connector integrates with actual task source (not just local file) | Add `TaskSourceConnector` abstraction with implementations for: (1) local JSON file (existing, default), (2) Todoist REST API, (3) Google Tasks API. Connector selected by env var `JARVIS_TASK_SOURCE`. Each returns normalized `list[dict]` with title, priority, due_date, status. |
| CONN-04 | Daily briefing combines real calendar events, email summaries, tasks, medications, and memory context into genuinely useful morning brief | Two-stage generation: (1) deterministic data assembly (upgraded `OpsSnapshot`), (2) LLM narrative via `ModelGateway.complete()` with a structured prompt that includes all data + relevant memory search results. Route to local Ollama (privacy: briefing contains personal data). |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| icalendar | >=7.0.1 | RFC 5545 ICS file parsing with timezone support | Most popular Python iCalendar library, production-stable, maintained by Plone Foundation, supports zoneinfo/dateutil/pytz timezones |
| recurring-ical-events | >=3.8.1 | RRULE/RDATE/EXDATE recurrence expansion | The standard companion to icalendar for expanding recurring events into concrete instances within a date range |
| python-dateutil | (transitive) | Date/time utilities and timezone handling | Already a dependency of icalendar, provides RRULE parsing and timezone objects |
| imaplib (stdlib) | built-in | IMAP4 email client | Already used in `ops_sync.py`, zero new dependencies, sufficient for read-only header+body fetching |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| imap-tools | >=1.11.1 | High-level IMAP wrapper | Optional upgrade if imaplib proves too cumbersome for body parsing and search queries. Zero external dependencies (wraps imaplib). |
| todoist-api-python | latest | Todoist REST API client | When user configures `JARVIS_TASK_SOURCE=todoist` and sets `JARVIS_TODOIST_TOKEN` |
| google-api-python-client | >=2.0 | Google Tasks API (and optionally Calendar API) | When user configures `JARVIS_TASK_SOURCE=google_tasks` and has OAuth credentials |
| google-auth-oauthlib | >=1.0 | OAuth2 flow for Google APIs | Required companion for google-api-python-client desktop OAuth flow |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| icalendar + recurring-ical-events | Hand-rolled ICS parser (existing `_parse_ics()`) | Existing parser handles simple VEVENT blocks but fails on RRULE, VTIMEZONE, multi-line property folding, quoted parameters. Libraries handle all edge cases from RFC 5545. |
| imaplib (stdlib) | imap-tools | imap-tools provides higher-level email attribute access (`.subject`, `.from_`, `.text`, `.html`) but adds a dependency. imaplib is already working in the codebase and sufficient for read-only header+body fetching. |
| Local JSON task file | Full Google Calendar API with OAuth2 | Google Calendar API requires OAuth2 setup (credentials.json, token.json, consent screen), adds google-api-python-client + google-auth-oauthlib dependencies. ICS secret URL provides the same read-only data with zero auth complexity. |
| LLM narrative briefing | Template string (current approach) | Template approach (current `build_daily_brief()`) produces mechanical counts ("Urgent tasks: 3") but not actionable narrative. LLM synthesis weaves context and produces natural language the owner can act on. The ModelGateway is already built in Phase 3. |

**Installation:**
```bash
pip install "icalendar>=7.0.1" "recurring-ical-events>=3.8.1"
```

Optional (only needed if user configures external task sources):
```bash
pip install "todoist-api-python" "google-api-python-client>=2.0" "google-auth-oauthlib>=1.0"
```

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
+-- connectors.py             # EXISTING: ConnectorDefinition, permissions, status evaluation
+-- ops_sync.py               # MODIFY: Upgrade calendar/email/task loading with real parsing
+-- life_ops.py               # MODIFY: Upgrade build_daily_brief() with LLM narrative stage
+-- connectors/               # NEW: Pluggable connector implementations
|   +-- __init__.py
|   +-- calendar_connector.py # ICS parsing with icalendar + recurring-ical-events
|   +-- email_connector.py    # IMAP reader with enhanced triage
|   +-- task_connector.py     # Task source abstraction (JSON, Todoist, Google Tasks)
+-- gateway/                  # EXISTING (Phase 3): ModelGateway for LLM narrative
+-- commands/
|   +-- ops_commands.py       # EXISTING: Extend with BriefingNarrativeCommand if needed
+-- handlers/
|   +-- ops_handlers.py       # EXISTING: Modify OpsBriefHandler to use narrative generation
```

### Pattern 1: Connector Abstraction with Environment-Based Selection
**What:** Each data source (calendar, email, tasks) has a connector function that reads real data and normalizes it into the existing `list[dict]` format used by `OpsSnapshot`. The connector is selected based on environment variables, falling back to local JSON files.
**When to use:** Every call to `build_live_snapshot()` in `ops_sync.py`.
**Example:**
```python
# Source: Existing pattern in ops_sync.py (env-based selection)
def load_calendar_events(date_range: tuple[date, date] | None = None) -> list[dict]:
    """Load calendar events, prioritizing real sources over JSON stubs."""
    # Priority 1: Pre-formatted JSON feed
    json_path = os.getenv("JARVIS_CALENDAR_JSON", "").strip()
    if json_path:
        return _read_json_list(Path(json_path))

    # Priority 2: ICS file (local export from Google Calendar)
    ics_file = os.getenv("JARVIS_CALENDAR_ICS_FILE", "").strip()
    if ics_file:
        return _parse_ics_file(Path(ics_file), date_range)

    # Priority 3: ICS URL (Google Calendar secret address)
    ics_url = os.getenv("JARVIS_CALENDAR_ICS_URL", "").strip()
    if ics_url:
        return _fetch_and_parse_ics_url(ics_url, date_range)

    return []
```

### Pattern 2: ICS Parsing with Recurring Event Expansion
**What:** Use `icalendar.Calendar.from_ical()` to parse ICS data, then `recurring_ical_events.of(cal).between(start, end)` to expand RRULE/RDATE into concrete event instances within a date range.
**When to use:** Whenever loading calendar data from an ICS source.
**Example:**
```python
# Source: icalendar docs + recurring-ical-events docs
from icalendar import Calendar
import recurring_ical_events
from datetime import date, timedelta

def _parse_ics_to_events(ics_text: str, target_date: date | None = None) -> list[dict]:
    """Parse ICS text into normalized event dicts, expanding recurring events."""
    cal = Calendar.from_ical(ics_text)
    if target_date is None:
        target_date = date.today()
    start = target_date
    end = target_date + timedelta(days=1)

    events = recurring_ical_events.of(cal).between(start, end)
    result = []
    for event in events:
        summary = str(event.get("SUMMARY", "Untitled event"))
        dtstart = event.get("DTSTART")
        dt_val = dtstart.dt if dtstart else None
        time_str = dt_val.strftime("%H:%M") if hasattr(dt_val, 'hour') else "all-day"
        location = str(event.get("LOCATION", ""))
        result.append({
            "title": summary,
            "time": time_str,
            "location": location,
            "prep_needed": "yes",  # default; can be refined
        })
    return result
```

### Pattern 3: Enhanced IMAP Email Triage
**What:** Upgrade the existing IMAP reader to fetch From, Date, and Subject headers plus a body snippet, then classify importance using a multi-signal approach (sender reputation, subject keywords, body keywords, recency).
**When to use:** When loading emails for the daily briefing.
**Example:**
```python
# Source: Existing ops_sync.py pattern + stdlib imaplib docs
import imaplib
from email import message_from_bytes
from email.header import decode_header

def load_email_items(limit: int = 20) -> list[dict]:
    """Load recent unread emails with enhanced triage scoring."""
    # ... existing IMAP4_SSL connection setup ...
    for msg_id in ids:
        typ2, msg_data = client.fetch(msg_id, "(RFC822.HEADER)")
        # ... existing header parsing ...
        sender = _decode_email_header(msg.get("From", ""))
        subject = _decode_email_header(msg.get("Subject", "No subject"))
        date_str = msg.get("Date", "")
        importance = _triage_email(sender, subject)
        items.append({
            "subject": subject,
            "from": sender,
            "date": date_str,
            "read": False,
            "importance": importance,
        })
    return items

def _triage_email(sender: str, subject: str) -> str:
    """Multi-signal importance classification."""
    lowered_subject = subject.lower()
    lowered_sender = sender.lower()
    # High priority: known urgent patterns
    high_markers = ["urgent", "action required", "payment due",
                    "invoice", "security", "incident", "deadline"]
    if any(m in lowered_subject for m in high_markers):
        return "high"
    # Medium: known senders (configurable contact list)
    # Low: everything else
    return "normal"
```

### Pattern 4: Two-Stage Daily Briefing (Data Assembly + LLM Narrative)
**What:** Stage 1 assembles all data deterministically (same as current `build_daily_brief()` but with real data). Stage 2 passes the assembled data plus relevant memory context to `ModelGateway.complete()` with a narrative prompt, routed to local Ollama for privacy.
**When to use:** When generating the morning daily brief.
**Example:**
```python
# Source: Existing life_ops.py + Phase 3 gateway
def build_narrative_brief(snapshot: OpsSnapshot, gateway: ModelGateway,
                          memory_context: str = "") -> str:
    """Generate a coherent narrative daily briefing using LLM."""
    # Stage 1: Deterministic data assembly
    data_summary = _assemble_data_summary(snapshot)

    # Stage 2: LLM narrative synthesis (local model for privacy)
    prompt = f"""You are Jarvis, a personal AI assistant. Generate a concise, actionable
morning briefing for the owner based on this data. Prioritize by urgency.
Be specific about times and actions. Keep it under 300 words.

TODAY'S DATA:
{data_summary}

RELEVANT MEMORY CONTEXT:
{memory_context}

Generate the briefing now."""

    local_model = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3:14b")
    response = gateway.complete(
        messages=[{"role": "user", "content": prompt}],
        model=local_model,
        max_tokens=512,
        route_reason="daily_briefing_narrative",
    )

    if response.text:
        return response.text

    # Fallback: return deterministic brief if LLM fails
    return build_daily_brief(snapshot)  # existing function
```

### Pattern 5: Task Source Abstraction
**What:** A simple connector layer that normalizes tasks from different sources into the same `list[dict]` format.
**When to use:** When loading tasks for the daily briefing.
**Example:**
```python
def load_task_items(repo_root: Path) -> list[dict]:
    """Load tasks from configured source."""
    source = os.getenv("JARVIS_TASK_SOURCE", "json").strip().lower()

    if source == "todoist":
        return _load_todoist_tasks()
    elif source == "google_tasks":
        return _load_google_tasks()
    else:
        # Default: local JSON file (existing behavior)
        return _read_json_list(repo_root / ".planning" / "tasks.json")

def _load_todoist_tasks() -> list[dict]:
    """Fetch tasks from Todoist REST API."""
    token = os.getenv("JARVIS_TODOIST_TOKEN", "").strip()
    if not token:
        return []
    try:
        from todoist_api_python.api import TodoistAPI
        api = TodoistAPI(token)
        tasks = api.get_tasks(filter="today | overdue")
        return [
            {
                "title": t.content,
                "priority": {4: "urgent", 3: "high", 2: "normal", 1: "low"}[t.priority],
                "due_date": t.due.date if t.due else "",
                "status": "pending",
            }
            for t in tasks
        ]
    except Exception:
        return []
```

### Anti-Patterns to Avoid
- **Google Calendar API OAuth2 for read-only access:** The ICS secret URL provides the same data with zero auth complexity. OAuth2 requires a Google Cloud project, consent screen, credentials.json, token.json, and periodic refresh token maintenance. Only use the full API if the user needs write access.
- **Fetching full email bodies for triage:** Downloading RFC822 full bodies for 50+ emails is slow and bandwidth-heavy. Fetch headers first (HEADER or ENVELOPE), triage, then fetch bodies only for high-importance emails.
- **Calling cloud LLM for the briefing narrative:** The daily briefing contains highly personal data (calendar, medications, emails). Always route to local Ollama. The Phase 3 `IntentClassifier` would already classify this as `simple_private`, but enforce it explicitly.
- **Re-parsing ICS on every briefing request:** ICS files and URLs should be cached with a configurable TTL (default 15 minutes). Re-parsing a 5MB ICS file with hundreds of recurring events on every brief request is wasteful.
- **Building a custom recurrence rule expander:** RFC 5545 RRULE is deceptively complex (BYDAY with positions, BYSETPOS, timezone transitions during DST, exception dates). The `recurring-ical-events` library handles all edge cases. Never hand-roll this.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| ICS recurring event expansion | Custom RRULE parser | `recurring-ical-events` library | RRULE has 13+ parameters with complex interactions (BYDAY, BYSETPOS, BYMONTH, UNTIL, COUNT, INTERVAL). DST transitions change event times. Exception dates (EXDATE) and recurrence dates (RDATE) add further complexity. The library is 3.8.1, well-tested, handles all RFC 5545 edge cases. |
| ICS file parsing | Custom line-by-line parser (current `_parse_ics()`) | `icalendar.Calendar.from_ical()` | The existing parser does not handle: property folding (long lines split with CRLF+space), VTIMEZONE blocks, quoted parameters (`;LANGUAGE=en`), multiple values (CATEGORIES), escape sequences. The library handles all of these per RFC 5545. |
| Email MIME parsing | Raw bytes splitting | `email.message_from_bytes()` (stdlib) | Already used in existing code. Never parse MIME headers by hand -- character encoding, RFC 2047 encoded words, multipart boundaries are all handled by stdlib. |
| Daily briefing narrative | Template string concatenation | LLM via `ModelGateway.complete()` | A template produces "Urgent tasks: 3, Unread emails: 2" -- not actionable. An LLM produces "Start your day by responding to the production incident email from Dave, then tackle the model router patch before your 3pm architecture call." The gateway is already built. |
| Timezone conversion | Manual UTC offset math | `python-dateutil` / `zoneinfo` | Timezone math is one of the most error-prone domains in programming. DST transitions, historical timezone changes, and political timezone updates make this impossible to get right by hand. |

**Key insight:** The existing `_parse_ics()` in `ops_sync.py` handles only the simplest case (flat VEVENT blocks with single-line properties). Real-world Google Calendar ICS exports contain RRULE, VTIMEZONE, property folding, and EXDATE. Replacing 25 lines of hand-rolled parsing with `icalendar` + `recurring-ical-events` handles all these cases and reduces maintenance burden.

## Common Pitfalls

### Pitfall 1: ICS Timezone Handling
**What goes wrong:** Events show up at wrong times because DTSTART contains a TZID reference (e.g., `DTSTART;TZID=America/New_York:20260223T090000`) and the parser either ignores TZID or converts to UTC incorrectly.
**Why it happens:** Google Calendar ICS exports use VTIMEZONE blocks that reference IANA timezone identifiers. The existing `_parse_ics()` splits on `:` and ignores everything before the value, losing the timezone.
**How to avoid:** Use `icalendar.Calendar.from_ical()` which automatically resolves VTIMEZONE blocks and returns timezone-aware datetime objects. When displaying times, convert to the user's local timezone (configurable via `JARVIS_TIMEZONE` env var, default to system local).
**Warning signs:** Morning briefing says "Meeting at 14:00" when it's actually at 9:00 AM local time.

### Pitfall 2: IMAP Connection Timeouts and App Passwords
**What goes wrong:** IMAP connection fails with timeout or authentication error because Gmail requires an App Password (not the regular account password) when 2FA is enabled.
**Why it happens:** Google requires 2-Step Verification and an App Password for IMAP access. The regular password will always fail. Additionally, IMAP connections can hang if the server is slow.
**How to avoid:** Document the App Password requirement in connector setup prompts (the existing `ConnectorDefinition.setup_url` already points to the Google App Password page). Set explicit timeouts on `IMAP4_SSL` connections (the existing code uses no timeout on the constructor -- add one). Wrap all IMAP operations in try/except with graceful fallback to empty list.
**Warning signs:** Authentication failures on every sync, or sync hanging indefinitely.

### Pitfall 3: Daily Briefing LLM Prompt Too Large
**What goes wrong:** The briefing prompt includes raw data for 50 calendar events, 100 emails, and 30 tasks, exceeding the local Ollama model's context window.
**Why it happens:** Passing all raw data instead of a pre-summarized version.
**How to avoid:** Stage 1 (deterministic assembly) should produce a condensed summary, not raw data. Limit to today's events only, top-10 emails by importance, top-10 tasks by priority. Include medication list (typically short). Cap the prompt at ~2000 tokens. The local model (qwen3:14b) has a large context window but quality degrades with longer prompts.
**Warning signs:** LLM response is truncated, garbled, or ignores parts of the data.

### Pitfall 4: ICS URL SSRF Vulnerability
**What goes wrong:** A user-configured ICS URL points to an internal network resource (e.g., `http://192.168.1.1/admin`), allowing Jarvis to make requests to internal services.
**Why it happens:** The existing `_is_safe_calendar_url()` in `ops_sync.py` already prevents this (checks for private IPs, loopback, requires HTTPS), but only when `JARVIS_ALLOW_REMOTE_CALENDAR_URLS=true` is set. Without the flag, remote URLs are blocked entirely.
**How to avoid:** Keep the existing safety checks. The `_is_safe_calendar_url()` function is already well-implemented (resolves DNS to check for private IPs, blocks localhost, requires HTTPS). Do not weaken these checks when upgrading the ICS parser.
**Warning signs:** Calendar shows events from unexpected sources.

### Pitfall 5: Task Source API Rate Limits
**What goes wrong:** Todoist API returns 429 (rate limited) because the briefing syncs too frequently.
**Why it happens:** Todoist allows 1000 requests per 15 minutes. If the briefing runs on a daemon loop (every 60 seconds), it could exceed limits with multiple API calls per sync.
**How to avoid:** Cache task responses with a TTL (default 5 minutes for tasks, 15 minutes for calendar). Only re-fetch when the cache expires. Implement exponential backoff on 429 responses.
**Warning signs:** Intermittent empty task lists, 429 error logs.

### Pitfall 6: Memory Context Retrieval Slows Briefing
**What goes wrong:** The narrative briefing includes memory context search (e.g., "relevant context about today's meetings"), but the embedding model loads lazily and takes 10-15 seconds on first use.
**Why it happens:** The `EmbeddingService` lazy-loads the sentence-transformer model on first `embed()` call.
**How to avoid:** Ensure the briefing runs after the embedding model is already warm (e.g., the daemon has already served a search query). Alternatively, make memory context optional -- if the embedding service hasn't loaded yet, skip the memory context and generate the briefing from data alone.
**Warning signs:** First morning briefing takes 15+ seconds; subsequent ones are fast.

## Code Examples

### ICS File Parsing with icalendar + recurring-ical-events
```python
# Source: icalendar 7.0.1 PyPI docs + recurring-ical-events 3.8.1 PyPI docs
from datetime import date, timedelta
from icalendar import Calendar
import recurring_ical_events

def parse_ics_for_date(ics_text: str, target_date: date) -> list[dict]:
    """Parse ICS text and return events for a specific date."""
    cal = Calendar.from_ical(ics_text)
    start = target_date
    end = target_date + timedelta(days=1)

    events = recurring_ical_events.of(cal).between(start, end)
    result = []
    for event in events:
        summary = str(event.get("SUMMARY", "Untitled event"))
        dtstart = event.get("DTSTART")
        dt_val = dtstart.dt if dtstart else None
        if hasattr(dt_val, "hour"):
            time_str = dt_val.strftime("%H:%M")
        else:
            time_str = "all-day"
        location = str(event.get("LOCATION", ""))
        description = str(event.get("DESCRIPTION", ""))
        result.append({
            "title": summary,
            "time": time_str,
            "location": location,
            "description": description[:200],  # Truncate long descriptions
            "prep_needed": "yes",
        })
    return sorted(result, key=lambda e: e["time"])
```

### IMAP Email Reading with Enhanced Triage
```python
# Source: Existing ops_sync.py + Python stdlib imaplib docs
import imaplib
from email import message_from_bytes
from email.header import decode_header

def load_email_items_enhanced(limit: int = 20) -> list[dict]:
    """Load recent unread emails with sender, subject, and date."""
    host = os.getenv("JARVIS_IMAP_HOST", "").strip()
    user = os.getenv("JARVIS_IMAP_USER", "").strip()
    password = os.getenv("JARVIS_IMAP_PASS", "").strip()
    if not host or not user or not password:
        return []

    items = []
    try:
        with imaplib.IMAP4_SSL(host, timeout=30) as client:
            client.login(user, password)
            client.select("INBOX", readonly=True)  # Read-only!
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
                sender = _decode_email_header(msg.get("From", ""))
                date_str = msg.get("Date", "")
                importance = _triage_email(sender, subject)
                items.append({
                    "subject": subject,
                    "from": sender,
                    "date": date_str,
                    "read": False,
                    "importance": importance,
                })
    except (imaplib.IMAP4.error, OSError, TimeoutError):
        return []
    return items
```

### LLM Narrative Briefing via ModelGateway
```python
# Source: Phase 3 gateway/models.py
def generate_narrative_brief(
    data_summary: str,
    memory_context: str,
    gateway: ModelGateway,
) -> str:
    """Use local LLM to generate a narrative morning briefing."""
    local_model = os.environ.get("JARVIS_LOCAL_MODEL", "qwen3:14b")
    prompt = (
        "You are Jarvis, a personal AI assistant with a British butler personality. "
        "Generate a concise morning briefing for the owner. Prioritize by urgency. "
        "Be specific about times and required actions. Keep it under 250 words.\n\n"
        f"TODAY'S DATA:\n{data_summary}\n\n"
        f"RELEVANT MEMORY:\n{memory_context}\n\n"
        "Generate the morning briefing:"
    )
    response = gateway.complete(
        messages=[{"role": "user", "content": prompt}],
        model=local_model,
        max_tokens=512,
        route_reason="daily_briefing_narrative",
    )
    return response.text if response.text else ""
```

### Memory Context Retrieval for Briefing
```python
# Source: Existing memory/search.py hybrid_search
def get_briefing_memory_context(
    engine: MemoryEngine,
    embed_service: EmbeddingService,
    events: list[dict],
    tasks: list[dict],
) -> str:
    """Search memory for context relevant to today's events and tasks."""
    queries = []
    for event in events[:5]:
        queries.append(event.get("title", ""))
    for task in tasks[:5]:
        queries.append(task.get("title", ""))

    context_lines = []
    seen_ids = set()
    for query in queries:
        if not query.strip():
            continue
        query_embedding = embed_service.embed_query(query)
        results = hybrid_search(engine, query, query_embedding, k=2)
        for record in results:
            rid = record.get("record_id", "")
            if rid not in seen_ids:
                seen_ids.add(rid)
                content = str(record.get("content", ""))[:200]
                context_lines.append(f"- {content}")

    return "\n".join(context_lines[:10]) if context_lines else "No relevant memory context."
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hand-rolled ICS line parser | `icalendar` library (v7.0.1) with `recurring-ical-events` | icalendar 7.0 released Feb 2026 | Full RFC 5545 support including RRULE, VTIMEZONE, property folding |
| Gmail regular password for IMAP | App Passwords required (Google 2FA mandatory) | Google enforced May 2025 | All IMAP integrations MUST use App Passwords. Regular passwords always fail. |
| Template-string daily briefing | LLM-powered narrative synthesis | 2025-2026 (common pattern) | Transforms mechanical data dumps into actionable narrative. Cost: zero (local Ollama). |
| Todoist Python SDK (sync API) | todoist-api-python (REST API v2) | 2024 | Old sync API deprecated. New REST API is simpler and better documented. |

**Deprecated/outdated:**
- Google "Less Secure App Access" for IMAP: Removed by Google. App Passwords are the only way.
- icalendar 5.x: Major breaking changes in 6.0+ (timezone handling rewrote). Use >=7.0.1.
- todoist-python (sync API): Deprecated in favor of todoist-api-python (REST API).

## Open Questions

1. **Which ICS input mode will the owner actually use?**
   - What we know: The connectors framework supports three modes (JSON feed, ICS file, ICS URL). Google Calendar provides a "secret ICS address" that is stable and doesn't require API keys.
   - What's unclear: Whether the owner prefers to export ICS periodically (file) or use the live secret URL (auto-updating).
   - Recommendation: Support all three modes (already env-gated). Document the Google Calendar secret ICS URL as the recommended setup -- it auto-updates and requires no maintenance.

2. **Should the email triage use the ModelGateway for classification?**
   - What we know: Current triage is keyword-based (subject line only). LLM-based triage could analyze sender + subject + body snippet for much better classification.
   - What's unclear: Whether the latency cost (calling local LLM per email) is acceptable for 20+ emails.
   - Recommendation: Start with enhanced keyword+sender triage (fast, deterministic). Add optional LLM triage as a future enhancement (`JARVIS_EMAIL_LLM_TRIAGE=true`). The keyword approach handles 80% of cases.

3. **How does memory context integrate with the briefing?**
   - What we know: The hybrid search system (Phase 1) can find semantically relevant records. The daily briefing needs context like "last time you met with X" or "your notes about project Y".
   - What's unclear: What queries to run against memory -- search for each calendar event title? Each task title? A general "today's context" query?
   - Recommendation: Search memory for the top-5 calendar event titles and top-5 task titles. Deduplicate results. Include up to 10 memory snippets in the narrative prompt. Cap total memory context at 500 tokens to avoid bloating the prompt.

4. **Should the task connector support write operations?**
   - What we know: CONN-03 says "integrates with actual task source." The daily briefing only needs read access.
   - What's unclear: Whether Phase 4 should include "mark task as done" or just reading.
   - Recommendation: Read-only for Phase 4. Write operations (create/update/complete) are a natural Phase 9 proactive intelligence feature.

5. **What happens when the local LLM (Ollama) is not running?**
   - What we know: The ModelGateway handles Ollama failures gracefully (returns empty GatewayResponse with fallback_reason).
   - What's unclear: Should the briefing fall back to the deterministic template, or should it fail silently?
   - Recommendation: Fall back to the existing deterministic `build_daily_brief()` when the LLM is unavailable. The owner gets a useful (if less polished) briefing rather than nothing.

## Sources

### Primary (HIGH confidence)
- icalendar 7.0.1 PyPI page (https://pypi.org/project/icalendar/) -- Version, dependencies, Python version support
- recurring-ical-events 3.8.1 PyPI page (https://pypi.org/project/recurring-ical-events/) -- Version, features, RRULE expansion
- imap-tools 1.11.1 PyPI page (https://pypi.org/project/imap-tools/) -- Version, features, zero external dependencies
- Python imaplib stdlib docs (https://docs.python.org/3/library/imaplib.html) -- IMAP4_SSL, read-only mode, header fetching
- Google Calendar API Python quickstart (https://developers.google.com/workspace/calendar/api/quickstart/python) -- OAuth2 flow, credentials.json pattern
- Context7 `/googleapis/google-api-python-client` -- Calendar events list API, OAuth2 service account docs
- Existing codebase: `ops_sync.py`, `connectors.py`, `life_ops.py`, `gateway/models.py`, `gateway/classifier.py`

### Secondary (MEDIUM confidence)
- Google App Password docs (https://support.google.com/mail/answer/185833) -- App Password requirement for IMAP with 2FA
- Todoist API docs (https://developer.todoist.com/api/v1/) -- REST API v2, task fetching, rate limits (1000/15min)
- Google Calendar ICS secret URL (https://support.google.com/calendar/answer/37648) -- How to get secret ICS address

### Tertiary (LOW confidence)
- LLM narrative briefing patterns -- Community patterns for daily briefing generation. Quality of output depends on local model capability. qwen3:14b should handle 250-word narrative synthesis well, but needs validation.
- Todoist API Python SDK version -- todoist-api-python is the current recommended SDK but exact latest version needs validation at install time.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - icalendar and recurring-ical-events are well-established, version-verified via PyPI. imaplib is stdlib. ModelGateway is already built.
- Architecture: HIGH - Pattern follows existing codebase conventions (env-var connector selection, OpsSnapshot dataclass, CommandBus handlers). Two-stage briefing (data + LLM) is a proven pattern.
- Pitfalls: HIGH - Based on direct codebase analysis (existing `_parse_ics()` limitations, missing IMAP timeouts, no RRULE support) and verified security patterns (`_is_safe_calendar_url()`).

**Research date:** 2026-02-23
**Valid until:** 2026-03-23 (30 days -- stable domain, library versions may update)
