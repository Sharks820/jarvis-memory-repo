"""Voice command extraction helpers — phone numbers, URLs, weather, web queries.

Split from voice_pipeline.py for separation of concerns.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

PHONE_NUMBER_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{7,}\d)")
URL_RE = re.compile(r"\b((?:https?://|www\.)[^\s<>{}\[\]\"']+)", flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# Text cleaning helpers
# ---------------------------------------------------------------------------


def shorten_urls_for_speech(text: str) -> str:
    """Replace raw URLs with short, speakable references for TTS."""

    def _replacement(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        normalized = raw if raw.lower().startswith(("http://", "https://")) else f"https://{raw}"
        parsed = urlparse(normalized)
        host = parsed.netloc.lower().strip()
        if host.startswith("www."):
            host = host[4:]
        host = host or "this source"
        return f"[{host} link]"

    return URL_RE.sub(_replacement, text)


def escape_response(msg: str) -> str:
    """Escape backslashes and newlines so response= stays on one stdout line.

    The mobile API parser splits on newlines — multi-line LLM answers would
    be truncated without escaping.  The parser unescapes on receipt.
    """
    return msg.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


# ---------------------------------------------------------------------------
# Extraction functions for voice commands
# ---------------------------------------------------------------------------


def _extract_first_phone_number(text: str) -> str:
    if len(text) > 256:
        text = text[:256]
    match = PHONE_NUMBER_RE.search(text)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_weather_location(text: str) -> str:
    # Try explicit "in/for <location>" first
    match = re.search(r"(?:weather|forecast)\s+(?:in|for|at)\s+(.+)", text, flags=re.IGNORECASE)
    if match:
        location = match.group(1).strip().rstrip("?.!,;:")
        return location[:120]
    # Fallback: grab text after weather/forecast, filter noise words
    match = re.search(r"(?:weather|forecast)\s+(.+)", text, flags=re.IGNORECASE)
    if not match:
        return ""
    location = match.group(1).strip().rstrip("?.!,;:")
    noise = {"like", "today", "right", "now", "outside", "currently", "report",
             "update", "check", "please", "is", "the", "what", "how", "look"}
    words = [w for w in location.split() if w.lower() not in noise]
    return " ".join(words)[:120]


def _extract_web_query(text: str) -> str:
    lowered = text.lower().strip()
    patterns = [
        r"(?:search(?:\s+the)?\s+(?:web|internet|online)\s+for)\s+(.+)",
        r"(?:research)\s+(.+)",
        r"(?:look\s*up|lookup)\s+(.+)",
        r"(?:find(?:\s+on\s+the\s+web)?)\s+(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip().rstrip("?.!,;:")
        if value:
            return value[:260]
    cleaned = lowered
    for prefix in ("jarvis,", "jarvis"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    return cleaned[:260]


def _extract_first_url(text: str) -> str:
    if len(text) > 1200:
        text = text[:1200]
    match = URL_RE.search(text)
    if not match:
        return ""
    raw = match.group(1).strip().rstrip(").,!?;:")
    if raw.lower().startswith("www."):
        raw = f"https://{raw}"
    return raw[:500]


def _is_read_only_voice_request(lowered: str, *, execute: bool, approve_privileged: bool) -> bool:
    if execute or approve_privileged:
        return False
    mutation_markers = [
        "pause jarvis",
        "pause daemon",
        "pause autopilot",
        "go idle",
        "stand down",
        "resume jarvis",
        "resume daemon",
        "resume autopilot",
        "safe mode on",
        "enable safe mode",
        "safe mode off",
        "disable safe mode",
        "auto gaming mode",
        "gaming mode on",
        "gaming mode off",
        "self heal",
        "self-heal",
        "repair yourself",
        "diagnose yourself",
        "sync mobile",
        "sync desktop",
        "cross-device sync",
        "sync devices",
        "send text",
        "send message",
        "ignore call",
        "decline call",
        "reject call",
        "place call",
        "make call",
        "dial ",
        "block likely spam",
        "automation run",
        "open website",
        "open webpage",
        "open page",
        "open url",
        "browse to",
        "go to ",
        "generate code",
        "generate image",
        "generate video",
        "generate 3d",
    ]
    if any(marker in lowered for marker in mutation_markers):
        return False
    read_only_markers = [
        "runtime status",
        "control status",
        "safe mode status",
        "gaming mode status",
        "gaming mode state",
        "what time",
        "time is it",
        "current time",
        "what date",
        "what day",
        "weather",
        "forecast",
        "search web",
        "search the web",
        "search internet",
        "search online",
        "look up",
        "lookup",
        "research ",
        "daily brief",
        "ops brief",
        "morning brief",
        "my brief",
        "brief me",
        "give me a brief",
        "run brief",
        "my schedule",
        "my calendar",
        "my meetings",
        "my agenda",
        "my tasks",
        "my todo",
        "my to-do",
        "what do you know",
        "what do you remember",
        "do you remember",
        "search memory",
        "what did i tell you",
        "what have i said",
        "knowledge status",
        "knowledge graph",
        "brain status",
        "memory status",
        "mission status",
        "system status",
        "jarvis status",
        "how are you",
        "status report",
        "health check",
        "are you working",
        "are you running",
    ]
    if any(marker in lowered for marker in read_only_markers):
        return True
    # Bare wake words or very short greetings (e.g. "jarvis", "hey jarvis")
    # are not state-mutating — treat as read-only so owner guard doesn't block them.
    stripped = lowered.strip()
    if stripped in ("jarvis", "hey jarvis", "hi jarvis", "hello jarvis", "ok jarvis", "a jarvis", "ay jarvis", "jarvis activate"):
        return True
    # Default-deny: unrecognised commands may be mutations not listed above.
    # Owner guard must authenticate them to prevent privilege bypass.
    return False
