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
        normalized = (
            raw if raw.lower().startswith(("http://", "https://")) else f"https://{raw}"
        )
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


# Wake word prefixes used for stripping "jarvis"/"hey jarvis" from transcribed text.
_WAKE_WORD_PREFIXES = ("hey jarvis, ", "hey jarvis ", "jarvis, ", "jarvis ")


def strip_wake_word(text: str) -> str:
    """Remove a leading wake-word prefix (e.g. 'jarvis', 'hey jarvis') from *text*.

    Returns the remainder with leading whitespace stripped, or the original
    text unchanged if no wake-word prefix is found.
    """
    lower = text.lower()
    for prefix in _WAKE_WORD_PREFIXES:
        if lower.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


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
    match = re.search(
        r"(?:weather|forecast)\s+(?:in|for|at)\s+(.+)", text, flags=re.IGNORECASE
    )
    if match:
        location = match.group(1).strip().rstrip("?.!,;:")
        return location[:120]
    # Fallback: grab text after weather/forecast, filter noise words
    match = re.search(r"(?:weather|forecast)\s+(.+)", text, flags=re.IGNORECASE)
    if not match:
        return ""
    location = match.group(1).strip().rstrip("?.!,;:")
    noise = {
        "like",
        "today",
        "right",
        "now",
        "outside",
        "currently",
        "report",
        "update",
        "check",
        "please",
        "is",
        "the",
        "what",
        "how",
        "look",
    }
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
    cleaned = strip_wake_word(lowered)
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


_MUTATION_MARKERS = frozenset(
    [
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
)

_READ_ONLY_MARKERS = frozenset(
    [
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
)

_GREETING_WAKE_WORDS = frozenset(
    [
        "jarvis",
        "hey jarvis",
        "hi jarvis",
        "hello jarvis",
        "ok jarvis",
        "a jarvis",
        "ay jarvis",
        "jarvis activate",
    ]
)

_SYNC_MUTATION_TARGETS = frozenset(
    [
        "calendar",
        "email",
        "inbox",
        "mobile",
        "desktop",
        "devices",
        "tasks",
        "subscriptions",
        "bills",
        "ops",
    ]
)


def _expand_read_only_aliases(lowered: str) -> str:
    """Expand sentence-shaped status requests into canonical read-only markers."""
    normalized = re.sub(r"\s+", " ", lowered.strip())
    if not normalized:
        return ""

    stripped = normalized
    wakeword_prefixes = ("hey jarvis ", "okay jarvis ", "ok jarvis ", "jarvis ")
    changed = True
    while changed:
        changed = False
        for prefix in wakeword_prefixes:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :].strip()
                changed = True

    stripped = re.sub(
        r"\b(?:please|can you|could you|would you|will you|i need you to|i want you to)\b",
        " ",
        stripped,
    )
    stripped = re.sub(r"\s+", " ", stripped).strip()

    aliases: list[str] = [normalized]
    if stripped and stripped != normalized:
        aliases.append(stripped)

    def _add(alias: str) -> None:
        if alias not in aliases:
            aliases.append(alias)

    if (
        any(term in stripped for term in ("brain", "memory", "knowledge graph", "knowledge"))
        and any(term in stripped for term in ("status", "health", "holding up", "doing"))
    ):
        _add("brain status")

    if (
        any(term in stripped for term in ("system", "jarvis", "you"))
        and any(
            term in stripped
            for term in ("status", "health", "running", "working", "holding up", "doing")
        )
    ):
        _add("system status")

    return " ".join(aliases)


def _looks_like_sync_or_connect_mutation(expanded: str) -> bool:
    """Detect common spoken sync/connect requests that mutate external state."""
    if not expanded:
        return False
    has_sync_like_verb = any(term in expanded for term in ("sync", "connect", "grant connector"))
    if not has_sync_like_verb:
        return False
    return any(target in expanded for target in _SYNC_MUTATION_TARGETS)


def _is_read_only_voice_request(
    lowered: str, *, execute: bool, approve_privileged: bool
) -> bool:
    if approve_privileged:
        return False
    expanded = _expand_read_only_aliases(lowered)
    if _looks_like_sync_or_connect_mutation(expanded):
        return False
    if any(marker in expanded for marker in _MUTATION_MARKERS):
        return False
    if any(marker in expanded for marker in _READ_ONLY_MARKERS):
        return True
    if lowered.strip() in _GREETING_WAKE_WORDS:
        return True
    if execute:
        return False
    return False
