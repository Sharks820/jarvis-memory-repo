from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from urllib.parse import urlparse
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path

from jarvis_engine.brain_memory import (
    build_context_packet,
    ingest_brain_record,
)
from jarvis_engine.config import repo_root
from jarvis_engine.ingest import IngestionPipeline
from jarvis_engine.learning_missions import load_missions
from jarvis_engine.memory_store import MemoryStore
from jarvis_engine.mobile_api import run_mobile_server
from jarvis_engine.owner_guard import (
    read_owner_guard,
    verify_master_password,
)
from jarvis_engine.persona import compose_persona_reply, load_persona_config
from jarvis_engine.runtime_control import (
    capture_runtime_resource_snapshot,
    read_control_state,
    recommend_daemon_sleep,
    write_resource_pressure_state,
)

from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.memory_commands import (
    BrainCompactCommand,
    BrainContextCommand,
    BrainRegressionCommand,
    BrainStatusCommand,
    IngestCommand,
    MemoryMaintenanceCommand,
    MemorySnapshotCommand,
)
from jarvis_engine.commands.voice_commands import (
    VoiceEnrollCommand,
    VoiceListCommand,
    VoiceListenCommand,
    VoiceRunCommand,
    VoiceSayCommand,
    VoiceVerifyCommand,
)
from jarvis_engine.commands.system_commands import (
    DaemonRunCommand,
    DesktopWidgetCommand,
    GamingModeCommand,
    LogCommand,
    MigrateMemoryCommand,
    MobileDesktopSyncCommand,
    OpenWebCommand,
    SelfHealCommand,
    StatusCommand,
    WeatherCommand,
)
from jarvis_engine.commands.task_commands import (
    QueryCommand,
    QueryResult,
    RouteCommand,
    RunTaskCommand,
    WebResearchCommand,
)
from jarvis_engine.commands.ops_commands import (
    AutomationRunCommand,
    GrowthAuditCommand,
    GrowthEvalCommand,
    GrowthReportCommand,
    IntelligenceDashboardCommand,
    MissionCancelCommand,
    MissionCreateCommand,
    MissionRunCommand,
    MissionStatusCommand,
    OpsAutopilotCommand,
    OpsBriefCommand,
    OpsExportActionsCommand,
    OpsSyncCommand,
)
from jarvis_engine.commands.security_commands import (
    ConnectBootstrapCommand,
    ConnectGrantCommand,
    ConnectStatusCommand,
    OwnerGuardCommand,
    PersonaConfigCommand,
    PhoneActionCommand,
    PhoneSpamGuardCommand,
    RuntimeControlCommand,
)
from jarvis_engine.commands.knowledge_commands import (
    ContradictionListCommand,
    ContradictionResolveCommand,
    FactLockCommand,
    KnowledgeRegressionCommand,
    KnowledgeStatusCommand,
)
from jarvis_engine.commands.harvest_commands import (
    HarvestBudgetCommand,
    HarvestTopicCommand,
    IngestSessionCommand,
)
from jarvis_engine.commands.learning_commands import (
    CrossBranchQueryCommand,
    FlagExpiredFactsCommand,
    LearnInteractionCommand,
)
from jarvis_engine.commands.proactive_commands import (
    CostReductionCommand,
    ProactiveCheckCommand,
    SelfTestCommand,
    WakeWordStartCommand,
)

logger = logging.getLogger(__name__)

PHONE_NUMBER_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{7,}\d)")
URL_RE = re.compile(r"\b((?:https?://|www\.)[^\s<>{}\[\]\"']+)", flags=re.IGNORECASE)


def _shorten_urls_for_speech(text: str) -> str:
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


def _escape_response(msg: str) -> str:
    """Escape backslashes and newlines so response= stays on one stdout line.

    The mobile API parser splits on newlines — multi-line LLM answers would
    be truncated without escaping.  The parser unescapes on receipt.
    """
    return msg.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")

# ---------------------------------------------------------------------------
# Command Bus factory -- respects monkeypatched repo_root() in tests
# ---------------------------------------------------------------------------

_cached_bus: CommandBus | None = None
_cached_bus_root: Path | None = None
_cached_bus_lock = threading.Lock()


def _get_bus() -> CommandBus:
    """Return a Command Bus wired to the current repo_root().

    Uses a cached bus when repo_root() hasn't changed (e.g. mobile API
    in-process calls).  Falls back to creating a fresh bus when
    repo_root() changes (e.g. tests monkeypatching repo_root).
    """
    global _cached_bus, _cached_bus_root
    from jarvis_engine.app import create_app

    root = repo_root()
    with _cached_bus_lock:
        if _cached_bus is not None and _cached_bus_root == root:
            return _cached_bus
        bus = create_app(root)
        _cached_bus = bus
        _cached_bus_root = root
        return bus


_auto_ingest_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Cached MemoryStore for auto-ingest (avoids recreating per call)
# ---------------------------------------------------------------------------
_auto_ingest_store: "MemoryStore | None" = None
_auto_ingest_store_lock = threading.Lock()


def _get_auto_ingest_store() -> "MemoryStore":
    """Return a cached MemoryStore for auto-ingest, creating once on first call."""
    global _auto_ingest_store
    if _auto_ingest_store is not None:
        return _auto_ingest_store
    with _auto_ingest_store_lock:
        if _auto_ingest_store is None:
            _auto_ingest_store = MemoryStore(repo_root())
        return _auto_ingest_store


# ---------------------------------------------------------------------------
# Daemon-scoped bus cache (avoids recreating MemoryEngine per periodic task)
# ---------------------------------------------------------------------------
_daemon_bus: CommandBus | None = None
_daemon_bus_lock = threading.Lock()


def _get_daemon_bus() -> CommandBus:
    """Return cached daemon bus, creating once on first call (thread-safe)."""
    global _daemon_bus
    if _daemon_bus is None:
        with _daemon_bus_lock:
            if _daemon_bus is None:
                _daemon_bus = _get_bus()
    return _daemon_bus


# ---------------------------------------------------------------------------
# Daemon cycle state for KG regression tracking
# ---------------------------------------------------------------------------
_daemon_kg_prev_metrics: dict | None = None


# ---------------------------------------------------------------------------
# Auto-harvest topic discovery for daemon cycle
# ---------------------------------------------------------------------------

# Stop words for filtering out low-quality single-word topic fragments
_HARVEST_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "of", "in",
    "to", "for", "with", "on", "at", "from", "by", "about", "as", "into",
    "through", "during", "before", "after", "above", "below", "between",
    "and", "but", "or", "nor", "not", "no", "so", "if", "then", "than",
    "too", "very", "just", "also", "that", "this", "it", "its", "my",
    "your", "his", "her", "our", "their", "what", "which", "who", "whom",
    "how", "when", "where", "why", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "only", "own", "same", "new",
    "old", "true", "false", "none", "null", "yes", "conner", "jarvis",
})


def _extract_topic_phrases(text: str) -> list[str]:
    """Extract multi-word topic phrases (2-5 words) from a text string.

    Uses simple heuristics: splits on punctuation, filters stop words,
    keeps capitalised/meaningful consecutive word runs of 2-5 words.
    No NLP libraries required.
    """
    # Split on sentence-level punctuation and common delimiters
    fragments = re.split(r'[.!?;:,\-\|/\(\)\[\]{}"\n]+', text)
    phrases: list[str] = []
    seen_lower: set[str] = set()

    for frag in fragments:
        words = frag.strip().split()
        # Filter out stop words and very short tokens
        meaningful = [w for w in words if w.lower() not in _HARVEST_STOP_WORDS and len(w) > 1]
        if len(meaningful) < 2:
            continue
        # Take up to 5 consecutive meaningful words
        phrase = " ".join(meaningful[:5])
        # Normalise and dedup
        phrase_lower = phrase.lower()
        if phrase_lower not in seen_lower and 2 <= len(phrase.split()) <= 5:
            phrases.append(phrase)
            seen_lower.add(phrase_lower)

    return phrases


def _get_recently_harvested_topics(root: Path) -> set[str]:
    """Return lowercase topic strings that were harvested in the last 14 days.

    Reads the activity feed for HARVEST events and extracts topic names
    so we can deduplicate against them.
    """
    recent: set[str] = set()
    try:
        from jarvis_engine.activity_feed import ActivityFeed, ActivityCategory
        from datetime import timedelta

        feed_db = root / ".planning" / "brain" / "activity_feed.db"
        if not feed_db.exists():
            return recent
        feed = ActivityFeed(db_path=feed_db)
        since = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        events = feed.query(limit=100, category=ActivityCategory.HARVEST, since=since)
        for ev in events:
            details = ev.details or {}
            # The auto-harvest log_activity stores {"topics": [...], ...}
            for t in details.get("topics", []):
                recent.add(str(t).lower().strip())
            # Also check the summary for "Auto-harvest: ..." patterns
            summary = ev.summary or ""
            if summary:
                recent.add(summary.lower().strip())
    except Exception as exc:
        logger.debug("Failed to read recent harvest topics from activity feed: %s", exc)
    return recent


def _discover_harvest_topics(root: Path) -> list[str]:
    """Discover 2-3 topics for autonomous knowledge harvesting.

    Topic sources (in priority order):
    1. Conversation-derived: recent memory entries (last 7 days) — multi-word phrases
    2. KG gap analysis: edge relation types with few instances or high-node/low-edge areas
    3. Complementary topics: strong KG areas expanded with "best practices"/"advanced"
    4. Activity feed: recent fact extraction summaries
    5. Fallback: completed learning mission topics

    All topics are 2-5 words.  Deduplicates against recently harvested topics.
    Returns up to 3 topic strings.  Never raises — returns [] on error.
    """
    _MAX_TOPICS = 3
    candidates: list[str] = []
    seen_lower: set[str] = set()

    # Load recently harvested topics for dedup
    recently_harvested = _get_recently_harvested_topics(root)

    def _add_candidate(topic: str) -> bool:
        """Add a topic candidate if unique and not recently harvested.  Returns True if added."""
        topic = topic.strip()
        if not topic or len(topic) < 4:
            return False
        tl = topic.lower()
        if tl in seen_lower or tl in recently_harvested:
            return False
        # Ensure 2-5 words
        word_count = len(topic.split())
        if word_count < 2 or word_count > 5:
            return False
        seen_lower.add(tl)
        candidates.append(topic)
        return len(candidates) >= _MAX_TOPICS

    # Open a single shared SQLite connection for sources 1-3 (memory + KG queries)
    import sqlite3 as _sqlite3
    from datetime import timedelta

    db_path = root / ".planning" / "brain" / "jarvis_memory.db"
    conn = None
    try:
        if db_path.exists():
            conn = _sqlite3.connect(str(db_path), timeout=5)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = _sqlite3.Row

        # --- Source 1: Conversation-derived topics from recent memories ---
        if conn is not None:
            try:
                cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
                rows = conn.execute(
                    """SELECT summary FROM records
                       WHERE ts >= ? AND source = 'user'
                       ORDER BY ts DESC
                       LIMIT 30""",
                    (cutoff,),
                ).fetchall()
                for row in rows:
                    summary = row["summary"] or ""
                    phrases = _extract_topic_phrases(summary)
                    for phrase in phrases:
                        if _add_candidate(phrase):
                            break
                    if len(candidates) >= _MAX_TOPICS:
                        break
            except Exception:
                pass  # Memory tables may not exist yet

        if len(candidates) >= _MAX_TOPICS:
            return candidates[:_MAX_TOPICS]

        # --- Source 2: KG gap analysis — relation types with few edges + sparse areas ---
        if conn is not None:
            try:
                # 2a: Find nodes that have few outgoing edges (surface-level knowledge)
                # These represent areas where we have facts but not much depth
                sparse_rows = conn.execute(
                    """SELECT n.label, COUNT(e.edge_id) AS edge_cnt
                       FROM kg_nodes n
                       LEFT JOIN kg_edges e ON n.node_id = e.source_id
                       WHERE n.confidence >= 0.3
                       GROUP BY n.node_id
                       HAVING edge_cnt BETWEEN 0 AND 1
                       ORDER BY n.updated_at DESC
                       LIMIT 10""",
                ).fetchall()
                for row in sparse_rows:
                    label = row["label"] or ""
                    phrases = _extract_topic_phrases(label)
                    for phrase in phrases:
                        if _add_candidate(phrase):
                            break
                    if len(candidates) >= _MAX_TOPICS:
                        break

                # 2b: Find relation types with few instances — structural KG gaps
                if len(candidates) < _MAX_TOPICS:
                    rel_rows = conn.execute(
                        """SELECT relation, COUNT(*) AS cnt
                           FROM kg_edges
                           GROUP BY relation
                           HAVING cnt BETWEEN 1 AND 3
                           ORDER BY cnt ASC
                           LIMIT 5""",
                    ).fetchall()
                    for row in rel_rows:
                        relation = row["relation"] or ""
                        # Turn relation into a topic: "causes" -> look up nodes
                        # Find a node connected by this rare relation for context
                        node_row = conn.execute(
                            """SELECT n.label FROM kg_nodes n
                               JOIN kg_edges e ON n.node_id = e.source_id
                               WHERE e.relation = ?
                               LIMIT 1""",
                            (relation,),
                        ).fetchone()
                        if node_row:
                            label = node_row["label"] or ""
                            phrases = _extract_topic_phrases(label)
                            for phrase in phrases:
                                if _add_candidate(phrase):
                                    break
                        if len(candidates) >= _MAX_TOPICS:
                            break
            except Exception:
                pass  # KG tables may not exist yet

        if len(candidates) >= _MAX_TOPICS:
            return candidates[:_MAX_TOPICS]

        # --- Source 3: Complementary topics — expand strong KG areas ---
        if conn is not None:
            try:
                # Find the most populated topic areas (first 2-3 words of node labels)
                strong_rows = conn.execute(
                    """SELECT
                         CASE
                           WHEN INSTR(SUBSTR(label, INSTR(label || ' ', ' ') + 1), ' ') > 0
                           THEN SUBSTR(label, 1,
                                  INSTR(label || ' ', ' ')
                                  + INSTR(SUBSTR(label, INSTR(label || ' ', ' ') + 1) || ' ', ' ') - 1)
                           ELSE SUBSTR(label, 1, INSTR(label || ' ', ' ') - 1)
                         END AS topic_prefix,
                         COUNT(*) AS cnt
                       FROM kg_nodes
                       WHERE confidence >= 0.5
                       GROUP BY topic_prefix
                       HAVING cnt >= 5 AND LENGTH(topic_prefix) > 3
                       ORDER BY cnt DESC
                       LIMIT 5""",
                ).fetchall()
                suffixes = ["best practices", "advanced techniques", "common patterns"]
                suffix_idx = 0
                for row in strong_rows:
                    prefix = (row["topic_prefix"] or "").strip()
                    if not prefix or len(prefix) < 3:
                        continue
                    expanded = f"{prefix} {suffixes[suffix_idx % len(suffixes)]}"
                    suffix_idx += 1
                    if _add_candidate(expanded):
                        break
                    if len(candidates) >= _MAX_TOPICS:
                        break
            except Exception as exc:
                logger.debug("Failed to discover harvest topics from knowledge graph: %s", exc)
    finally:
        if conn is not None:
            conn.close()

    if len(candidates) >= _MAX_TOPICS:
        return candidates[:_MAX_TOPICS]

    # --- Source 4: Activity feed fact-extraction summaries ---
    try:
        from jarvis_engine.activity_feed import ActivityFeed, ActivityCategory
        feed_db = root / ".planning" / "brain" / "activity_feed.db"
        if feed_db.exists():
            feed = ActivityFeed(db_path=feed_db)
            events = feed.query(limit=20, category=ActivityCategory.FACT_EXTRACTED)
            for ev in events:
                summary = ev.summary or ""
                if len(summary) > 5:
                    phrases = _extract_topic_phrases(summary)
                    for phrase in phrases:
                        if _add_candidate(phrase):
                            break
                    if len(candidates) >= _MAX_TOPICS:
                        break
    except Exception as exc:
        logger.debug("Failed to extract harvest topics from activity feed fact summaries: %s", exc)

    if len(candidates) >= _MAX_TOPICS:
        return candidates[:_MAX_TOPICS]

    # --- Source 5: Fallback — completed learning mission topics ---
    try:
        missions = load_missions(root)
        for m in reversed(missions):
            status = str(m.get("status", "")).lower()
            if status in ("completed", "done", "running"):
                topic = str(m.get("topic", "")).strip()
                if topic:
                    # If it's already multi-word, use as-is; else skip (single words are poor)
                    if len(topic.split()) >= 2:
                        if _add_candidate(topic):
                            break
    except Exception as exc:
        logger.debug("Failed to discover harvest topics from learning missions: %s", exc)

    return candidates[:_MAX_TOPICS]


# ---------------------------------------------------------------------------
# Conversation history buffer for multi-turn context (persisted to disk)
# ---------------------------------------------------------------------------
_conversation_history: list[dict[str, str]] = []
_conversation_history_lock = threading.Lock()
_CONVERSATION_HISTORY_FILE: Path | None = None
_conversation_history_loaded = False


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read bounded integer env var, returning fallback on parse errors."""
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


_CONVERSATION_MAX_TURNS = _env_int("JARVIS_CONVERSATION_MAX_TURNS", 12, minimum=4, maximum=40)
_CONVERSATION_MAX_CHARS_PER_MESSAGE = _env_int(
    "JARVIS_CONVERSATION_MAX_CHARS",
    2000,
    minimum=400,
    maximum=8000,
)


def _conversation_history_path() -> Path:
    """Return the path for persisted conversation history."""
    global _CONVERSATION_HISTORY_FILE
    if _CONVERSATION_HISTORY_FILE is None:
        _CONVERSATION_HISTORY_FILE = repo_root() / ".planning" / "brain" / "conversation_history.json"
        _CONVERSATION_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    return _CONVERSATION_HISTORY_FILE


def _load_conversation_history() -> None:
    """Load persisted conversation history from disk.

    IMPORTANT: Caller must already hold ``_conversation_history_lock``.
    This function does NOT acquire the lock itself to avoid deadlock with
    the non-reentrant ``threading.Lock``.
    """
    global _conversation_history
    try:
        path = _conversation_history_path()
        if path.exists():
            import json as _json
            data = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _conversation_history = data[-((_CONVERSATION_MAX_TURNS * 2)):]
    except Exception as exc:
        logger.debug("Could not load conversation history: %s", exc)


def _save_conversation_history() -> None:
    """Persist current conversation history to disk (atomic write).

    The entire temp-write + replace is performed while holding the lock
    so concurrent callers cannot clobber the shared temp file.
    """
    try:
        import json as _json
        path = _conversation_history_path()
        with _conversation_history_lock:
            snapshot = list(_conversation_history)
            tmp = path.with_suffix(f".tmp.{os.getpid()}")
            tmp.write_text(_json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(path))
    except Exception as exc:
        logger.debug("Could not save conversation history: %s", exc)


def _add_to_history(role: str, content: str) -> None:
    """Append a message to the conversation history, capping at max turns."""
    with _conversation_history_lock:
        _conversation_history.append(
            {"role": role, "content": content[:_CONVERSATION_MAX_CHARS_PER_MESSAGE]}
        )
        # Keep only the last N user/assistant pairs
        while len(_conversation_history) > _CONVERSATION_MAX_TURNS * 2:
            _conversation_history.pop(0)
    _save_conversation_history()


def _get_history_messages() -> list[dict[str, str]]:
    """Return conversation history as message list for LLM context."""
    global _conversation_history_loaded
    with _conversation_history_lock:
        if not _conversation_history_loaded:
            _load_conversation_history()
            _conversation_history_loaded = True
        return list(_conversation_history)


_last_routed_model: str | None = None
_last_routed_model_lock = threading.Lock()


def _conversation_continuity_instruction(target_model: str, history_len: int) -> str | None:
    """Return continuity instruction when conversation switches models/providers."""
    if history_len <= 0:
        return None
    normalized_target = target_model.strip()
    if not normalized_target:
        return None
    with _last_routed_model_lock:
        previous = (_last_routed_model or "").strip()
    if not previous or previous == normalized_target:
        return None
    return (
        f"Continuity contract: previous turn used model '{previous}' and this turn uses '{normalized_target}'. "
        "Do not reset or restart context. Continue the same conversation using provided history, memory, and unresolved goals."
    )


def _mark_routed_model(model: str, provider: str) -> None:
    """Persist last routed model and log provider-switch continuity telemetry."""
    global _last_routed_model
    normalized_model = model.strip()
    if not normalized_model:
        return
    with _last_routed_model_lock:
        previous = _last_routed_model
        _last_routed_model = normalized_model

    if previous and previous != normalized_model:
        try:
            from jarvis_engine.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.LLM_ROUTING,
                f"Model continuity switch: {previous} -> {normalized_model}",
                {
                    "from_model": previous,
                    "to_model": normalized_model,
                    "provider": provider,
                    "event": "conversation_model_switch",
                },
            )
        except Exception as exc:
            logger.debug("Model continuity telemetry logging failed: %s", exc)


# ---------------------------------------------------------------------------
# Smart context builder — hybrid search + KG facts + conversation history
# ---------------------------------------------------------------------------
_MAX_TOKENS_BY_ROUTE: dict[str, int] = {
    "math_logic": 1024,
    "complex": 1024,
    "creative": 1024,
    "routine": 512,
    "simple_private": 1024,
    "web_research": 1024,
}

# ---------------------------------------------------------------------------
# Web search need detection — identifies queries requiring current information
# ---------------------------------------------------------------------------
_WEB_SIGNAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:latest|current|recent|today'?s|tonight'?s|yesterday'?s|this (?:week|month|year)'?s?)\b", re.I),
    re.compile(r"\b(?:right now|at the moment|as of (?:today|now|2\d{3}))\b", re.I),
    re.compile(r"\b(?:news|headlines|breaking|update|updates|happening)\b", re.I),
    re.compile(r"\b(?:stock|price|market|cryptocurrency|bitcoin|crypto|eth|btc)\s*(?:price|value|worth|cost|today)?\b", re.I),
    re.compile(r"\b(?:score|scores|game|match|playoff|championship|tournament|standings|results?)\b", re.I),
    re.compile(r"\b(?:weather|forecast|temperature|rain|snow|wind)\b", re.I),
    re.compile(r"\bwho (?:won|is winning|leads?|lost)\b", re.I),
    re.compile(r"\b(?:when (?:is|does|did|will)|what time (?:is|does))\b", re.I),
    re.compile(r"\b(?:release date|coming out|launched|announced|premiered)\b", re.I),
    re.compile(r"\b(?:how (?:much|many) (?:does|is|are|do))\b", re.I),
    re.compile(r"\b(?:look up|lookup|find out|check|search for)\b", re.I),
    re.compile(r"\b(?:what (?:is|are|was|were) the (?:best|top|most|biggest|highest|lowest))\b", re.I),
    re.compile(r"\b(?:compared? to|vs\.?|versus)\b", re.I),
    re.compile(r"\b(?:2024|2025|2026|2027)\b"),  # Queries mentioning recent/future years
    re.compile(r"\b(?:search|google|look up|find me|find out about)\b", re.I),
    re.compile(r"\b(?:what(?:'s| is) (?:the |)(?:status|situation|deal) (?:with|of|about))\b", re.I),
    re.compile(r"\b(?:tell me about|info on|information about|details about)\b", re.I),
    re.compile(r"\b(?:where (?:can i|do i|is the|are the))\b", re.I),
    re.compile(r"\b(?:how (?:do i|can i|to))\b", re.I),
]

# Exclusion patterns: queries that match web signals but are actually personal/private
_WEB_EXCLUSION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bmy (?:calendar|medication|prescription|bill|password|appointment|meeting|schedule)\b", re.I),
    re.compile(r"\b(?:remind me|set (?:a )?(?:reminder|alarm|timer))\b", re.I),
    re.compile(r"\b(?:what did (?:i|jarvis)|show me my|what'?s on my)\b", re.I),
]


def _needs_web_search(query: str) -> bool:
    """Detect if a query likely needs current web information to answer well.

    Uses signal patterns (current events, prices, scores, news) and exclusion
    patterns (personal/private queries) to decide.  Returns True when web
    search augmentation would improve the LLM response.
    """
    lowered = query.lower().strip()
    # Check exclusions first — personal queries should not trigger web search
    for exc_pat in _WEB_EXCLUSION_PATTERNS:
        if exc_pat.search(lowered):
            return False
    # Check for web search signals
    matches = sum(1 for pat in _WEB_SIGNAL_PATTERNS if pat.search(lowered))
    # Require at least 1 signal pattern match
    return matches >= 1


def _requires_fresh_web_confirmation(query: str) -> bool:
    """True when the user explicitly asks for up-to-date/live web confirmation."""
    q = query.lower().strip()
    strict_markers = (
        "latest", "current", "right now", "today", "tonight", "this week",
        "live", "breaking", "as of", "up to date", "real-time", "real time",
    )
    return any(m in q for m in strict_markers)


def _current_datetime_prompt_line() -> str:
    """Provide deterministic current date/time context for model grounding."""
    local_now = datetime.now().astimezone()
    utc_now = local_now.astimezone(UTC)
    local_iso = local_now.isoformat(timespec="seconds")
    utc_iso = utc_now.isoformat(timespec="seconds")
    unix_epoch = int(utc_now.timestamp())
    human_now = local_now.strftime("%A, %B %d, %Y %H:%M %Z")
    return (
        f"Current date/time: {human_now} (local ISO {local_iso}; UTC {utc_iso}; epoch {unix_epoch}). "
        "Treat this as the present unless the user explicitly specifies another date. "
        "If relative-time reasoning conflicts with this clock context, prioritize this clock context."
    )


def _build_smart_context(
    bus: "CommandBus",
    query: str,
    *,
    max_memory_items: int = 20,
    max_fact_items: int = 15,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Build context using best available retrieval method.

    Returns (memory_lines, fact_lines, cross_branch_lines, preference_lines)
    for system prompt injection.  Uses hybrid search (FTS5 + embeddings + RRF)
    when MemoryEngine is available, falls back to legacy token-overlap otherwise.
    """
    memory_lines: list[str] = []
    fact_lines: list[str] = []
    cross_branch_lines: list[str] = []

    engine = getattr(bus, "_engine", None)
    embed_service = getattr(bus, "_embed_service", None)

    # --- Path 1: Hybrid search (superior) ---
    if engine is not None and embed_service is not None:
        try:
            from jarvis_engine.memory.search import hybrid_search

            query_embedding = embed_service.embed_query(query)
            results = hybrid_search(
                engine, query, query_embedding, k=max_memory_items
            )
            for record in results:
                summary = str(record.get("summary", "")).strip()
                if summary:
                    memory_lines.append(summary)
        except Exception as exc:
            logger.debug("Hybrid search failed, falling back to legacy: %s", exc)

    # --- Path 2: Legacy token-overlap fallback ---
    if not memory_lines:
        try:
            packet = build_context_packet(
                repo_root(), query=query, max_items=max_memory_items, max_chars=1800
            )
            selected = packet.get("selected", [])
            if isinstance(selected, list):
                for row in selected:
                    if isinstance(row, dict):
                        summary = str(row.get("summary", "")).strip()
                        if summary:
                            memory_lines.append(summary)
        except Exception as exc:
            logger.debug("Legacy context packet fallback failed: %s", exc)

    # --- KG facts: personal knowledge about the user ---
    kg = None  # Retain reference for cross-branch query below
    if engine is not None:
        try:
            kg = getattr(bus, "_kg", None)
            if kg is None:
                from jarvis_engine.knowledge.graph import KnowledgeGraph
                kg = KnowledgeGraph(engine)
            # Extract keywords from query for fact lookup
            _stop = {"the", "a", "an", "is", "are", "was", "were", "do", "does",
                      "did", "will", "would", "can", "could", "should", "shall",
                      "have", "has", "had", "be", "been", "being", "what", "when",
                      "where", "how", "who", "which", "that", "this", "for", "with",
                      "from", "about", "into", "and", "but", "or", "not", "if",
                      "then", "than", "too", "very", "just", "my", "me", "i"}
            words = [
                w for w in re.findall(r"[a-zA-Z]{3,}", query.lower())
                if w not in _stop
            ][:10]
            if words:
                facts = kg.query_relevant_facts(words, limit=max_fact_items)
                seen_node_ids: dict[str, float] = {}
                for fact in facts:
                    label = str(fact.get("label", "")).strip()
                    conf = fact.get("confidence", 0.0)
                    nid = fact.get("node_id", "")
                    if label and conf >= 0.5:
                        fact_lines.append(label)
                        if nid:
                            seen_node_ids[nid] = conf

                # Semantic KG search (embedding-based) — complements keyword FTS5
                if embed_service is not None:
                    try:
                        sem_facts = kg.query_relevant_facts_semantic(
                            query, embed_service=embed_service,
                            limit=max_fact_items, min_confidence=0.5,
                        )
                        for fact in sem_facts:
                            nid = fact.get("node_id", "")
                            label = str(fact.get("label", "")).strip()
                            conf = fact.get("confidence", 0.0)
                            if not label or conf < 0.5:
                                continue
                            # Deduplicate: skip if already seen with equal/higher confidence
                            if nid and nid in seen_node_ids and seen_node_ids[nid] >= conf:
                                continue
                            fact_lines.append(label)
                            if nid:
                                seen_node_ids[nid] = conf
                    except Exception as sem_exc:
                        logger.debug("KG semantic fact query failed: %s", sem_exc)
        except Exception as exc:
            logger.debug("KG fact query failed: %s", exc)

    # --- Cross-branch connections: link knowledge across life domains ---
    if kg is not None and engine is not None and embed_service is not None:
        try:
            from jarvis_engine.learning.cross_branch import cross_branch_query

            cb_result = cross_branch_query(
                query=query,
                engine=engine,
                kg=kg,
                embed_service=embed_service,
                k=6,
            )
            for conn in cb_result.get("cross_branch_connections", []):
                src = conn.get("source", "")
                tgt = conn.get("target", "")
                src_branch = conn.get("source_branch", "unknown")
                tgt_branch = conn.get("target_branch", "unknown")
                relation = conn.get("relation", "related")
                cross_branch_lines.append(
                    f"[{src_branch}] \"{src}\" relates to [{tgt_branch}] \"{tgt}\" via {relation}"
                )
        except Exception as exc:
            logger.debug("Cross-branch query failed: %s", exc)

    # --- User preferences: personalize responses (LEARN-01) ---
    preference_lines: list[str] = []
    pref_tracker = getattr(bus, "_pref_tracker", None)
    if pref_tracker is not None:
        try:
            prefs = pref_tracker.get_preferences()
            if prefs:
                pref_str = ", ".join(f"{k}: {v}" for k, v in prefs.items())
                preference_lines.append(pref_str)
        except Exception as exc:
            logger.debug("Preference retrieval failed: %s", exc)

    return memory_lines, fact_lines, cross_branch_lines, preference_lines


def _auto_ingest_dedupe_path() -> Path:
    return repo_root() / ".planning" / "runtime" / "auto_ingest_dedupe.json"


def _sanitize_memory_content(content: str) -> str:
    content = content[:100_000]  # Truncate before regex to prevent catastrophic backtracking
    # Redact master password, tokens, API keys, secrets, signing keys, bearer tokens
    _CRED_KEYS = r'(?:master[\s_-]*)?password|passwd|pwd|token|api[_-]?key|secret|signing[_-]?key'
    # JSON-style: "key": "value"
    cleaned = re.sub(
        rf'(?i)"({_CRED_KEYS})"\s*:\s*"[^"]*"',
        r'"\1": "[redacted]"',
        content,
    )
    # Unquoted style: key=value or key: value
    cleaned = re.sub(
        rf"(?i)({_CRED_KEYS})\s*[:=]\s*\S+",
        r"\1=[redacted]",
        cleaned,
    )
    cleaned = re.sub(r"(?i)(bearer)\s+\S+", r"\1 [redacted]", cleaned)
    return cleaned.strip()[:2000]


def _load_auto_ingest_hashes(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, dict):
        return []
    values = raw.get("hashes", [])
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _store_auto_ingest_hashes(path: Path, hashes: list[str]) -> None:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    payload = {"hashes": hashes[-400:], "updated_utc": datetime.now(UTC).isoformat()}
    _atomic_write_json(path, payload)


_VALID_SOURCES = {"user", "claude", "opus", "gemini", "task_outcome", "conversation"}
_VALID_KINDS = {"episodic", "semantic", "procedural"}

def _auto_ingest_memory_sync(source: str, kind: str, task_id: str, content: str) -> str:
    """Synchronous core of auto-ingest (runs in background thread)."""
    safe_content = _sanitize_memory_content(content)
    if not safe_content:
        return ""
    safe_task_id = task_id[:128]
    dedupe_path = _auto_ingest_dedupe_path()
    dedupe_material = f"{source}|{kind}|{safe_task_id}|{safe_content.lower()}".encode("utf-8")
    dedupe_hash = hashlib.sha256(dedupe_material).hexdigest()
    # Lock prevents race condition when daemon + CLI ingest concurrently.
    # Check dedup under lock, but only persist hash AFTER successful ingestion
    # to allow retries on failure.
    with _auto_ingest_lock:
        seen = _load_auto_ingest_hashes(dedupe_path)
        seen_set = set(seen)
        if dedupe_hash in seen_set:
            return ""

    store = _get_auto_ingest_store()
    pipeline = IngestionPipeline(store)
    rec = pipeline.ingest(
        source=source,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        task_id=safe_task_id,
        content=safe_content,
    )
    try:
        ingest_brain_record(
            repo_root(),
            source=source,
            kind=kind,
            task_id=safe_task_id,
            content=safe_content,
            tags=[source, kind],
            confidence=0.74 if source == "task_outcome" else 0.68,
        )
    except ValueError:
        logger.warning("brain ingest failed for task_id=%s", safe_task_id[:32])

    # Mark as seen only AFTER successful ingestion so failures can be retried
    with _auto_ingest_lock:
        seen = _load_auto_ingest_hashes(dedupe_path)
        seen.append(dedupe_hash)
        _store_auto_ingest_hashes(dedupe_path, seen)

    return rec.record_id


def _auto_ingest_memory(source: str, kind: str, task_id: str, content: str) -> str:
    """Fire-and-forget auto-ingest — runs in a background thread to avoid blocking responses."""
    if os.getenv("JARVIS_AUTO_INGEST_DISABLE", "").strip().lower() in {"1", "true", "yes"}:
        return ""
    if source not in _VALID_SOURCES or kind not in _VALID_KINDS:
        return ""

    def _bg() -> None:
        try:
            _auto_ingest_memory_sync(source, kind, task_id, content)
        except Exception as exc:
            logger.debug("Background auto-ingest failed: %s", exc)

    t = threading.Thread(target=_bg, daemon=True)
    t.start()
    # Return empty — the record ID is no longer available synchronously,
    # but the ingest still happens in the background.
    return ""


def _windows_idle_seconds() -> float | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        last_input = LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)) == 0:  # type: ignore[attr-defined]
            return None
        tick_now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF  # type: ignore[attr-defined]
        idle_ms = (tick_now - last_input.dwTime) & 0xFFFFFFFF
        return max(0.0, idle_ms / 1000.0)
    except Exception:
        return None


def _gaming_mode_state_path() -> Path:
    return repo_root() / ".planning" / "runtime" / "gaming_mode.json"


def _gaming_processes_path() -> Path:
    return repo_root() / ".planning" / "gaming_processes.json"


DEFAULT_GAMING_PROCESSES = (
    "FortniteClient-Win64-Shipping.exe",
    "VALORANT-Win64-Shipping.exe",
    "r5apex.exe",
    "cs2.exe",
    "Overwatch.exe",
    "RocketLeague.exe",
    "GTA5.exe",
    "eldenring.exe",
)


def _read_gaming_mode_state() -> dict[str, object]:
    path = _gaming_mode_state_path()
    default: dict[str, object] = {"enabled": False, "auto_detect": False, "updated_utc": "", "reason": ""}
    if not path.exists():
        return default
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    if not isinstance(raw, dict):
        return default
    return {
        "enabled": bool(raw.get("enabled", False)),
        "auto_detect": bool(raw.get("auto_detect", False)),
        "updated_utc": str(raw.get("updated_utc", "")),
        "reason": str(raw.get("reason", "")),
    }


def _write_gaming_mode_state(state: dict[str, object]) -> dict[str, object]:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    path = _gaming_mode_state_path()
    payload = {
        "enabled": bool(state.get("enabled", False)),
        "auto_detect": bool(state.get("auto_detect", False)),
        "updated_utc": str(state.get("updated_utc", "")) or datetime.now(UTC).isoformat(),
        "reason": str(state.get("reason", "")).strip()[:200],
    }
    _atomic_write_json(path, payload)
    return payload


def _load_gaming_processes() -> list[str]:
    env_override = os.getenv("JARVIS_GAMING_PROCESSES", "").strip()
    if env_override:
        return [item.strip() for item in env_override.split(",") if item.strip()]

    path = _gaming_processes_path()
    if not path.exists():
        return list(DEFAULT_GAMING_PROCESSES)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return list(DEFAULT_GAMING_PROCESSES)

    if isinstance(raw, dict):
        values = raw.get("processes", [])
    elif isinstance(raw, list):
        values = raw
    else:
        values = []

    if not isinstance(values, list):
        return list(DEFAULT_GAMING_PROCESSES)
    processes = [str(item).strip() for item in values if str(item).strip()]
    return processes or list(DEFAULT_GAMING_PROCESSES)


def _detect_active_game_process() -> tuple[bool, str]:
    if os.name != "nt":
        return False, ""
    patterns = [name.lower() for name in _load_gaming_processes()]
    if not patterns:
        return False, ""
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=6,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    if result.returncode != 0:
        return False, ""

    running: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("info:"):
            continue
        try:
            row = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            continue
        if not row:
            continue
        running.append(row[0].strip().lower())

    for proc_name in running:
        for pattern in patterns:
            if proc_name == pattern or pattern in proc_name:
                return True, proc_name
    return False, ""


def cmd_gaming_mode(enable: bool | None, reason: str, auto_detect: str) -> int:
    result = _get_bus().dispatch(GamingModeCommand(enable=enable, reason=reason, auto_detect=auto_detect))
    state = result.state
    print("gaming_mode")
    print(f"enabled={bool(state.get('enabled', False))}")
    print(f"auto_detect={bool(state.get('auto_detect', False))}")
    print(f"auto_detect_active={result.detected}")
    if result.detected_process:
        print(f"detected_process={result.detected_process}")
    print(f"effective_enabled={result.effective_enabled}")
    print(f"updated_utc={state.get('updated_utc', '')}")
    if state.get("reason", ""):
        print(f"reason={state.get('reason', '')}")
    print("effect=daemon_autopilot_paused_when_enabled")
    print(f"process_config={_gaming_processes_path()}")
    return 0


def cmd_status() -> int:
    result = _get_bus().dispatch(StatusCommand())
    print("Jarvis Engine Status")
    print(f"profile={result.profile}")
    print(f"primary_runtime={result.primary_runtime}")
    print(f"secondary_runtime={result.secondary_runtime}")
    print(f"security_strictness={result.security_strictness}")
    print(f"operation_mode={result.operation_mode}")
    print(f"cloud_burst_enabled={result.cloud_burst_enabled}")
    print("recent_events:")
    if not result.events:
        print("- none")
    else:
        for event in result.events:
            print(f"- [{event.ts}] {event.event_type}: {event.message}")
    # Structured response for UI consumption (UI-05)
    print(f"response=Engine status: {result.profile} profile, {result.operation_mode} mode, "
          f"runtime={result.primary_runtime}")
    return 0


def cmd_log(event_type: str, message: str) -> int:
    result = _get_bus().dispatch(LogCommand(event_type=event_type, message=message))
    print(f"logged: [{result.ts}] {result.event_type}: {result.message}")
    return 0


def cmd_ingest(source: str, kind: str, task_id: str, content: str) -> int:
    result = _get_bus().dispatch(IngestCommand(source=source, kind=kind, task_id=task_id, content=content))
    print(f"ingested: id={result.record_id} source={result.source} kind={result.kind} task_id={result.task_id}")
    return 0


def cmd_serve_mobile(host: str, port: int, token: str | None, signing_key: str | None, allow_insecure_bind: bool = False, config_file: str | None = None, tls: bool | None = None) -> int:
    # Load credentials from config file if provided
    if config_file:
        config_path = Path(config_file)
        if not config_path.exists():
            print(f"error: config file not found: {config_file}")
            return 2
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            print(f"error: failed to read config file: {exc}")
            return 2
        # CLI args override config file values
        if not token:
            token = config_data.get("token")
        if not signing_key:
            signing_key = config_data.get("signing_key")

    effective_token = token or os.getenv("JARVIS_MOBILE_TOKEN", "").strip()
    effective_signing_key = signing_key or os.getenv("JARVIS_MOBILE_SIGNING_KEY", "").strip()
    if not effective_token:
        print("error: missing mobile token. pass --token or set JARVIS_MOBILE_TOKEN")
        return 2
    if not effective_signing_key:
        print("error: missing signing key. pass --signing-key or set JARVIS_MOBILE_SIGNING_KEY")
        return 2

    if allow_insecure_bind:
        os.environ["JARVIS_ALLOW_INSECURE_MOBILE_BIND"] = "true"

    # Token rotation warning: check config file age if loaded from file
    if config_file:
        try:
            _cfg_text = Path(config_file).read_text(encoding="utf-8")
            _cfg_data = json.loads(_cfg_text)
            _created_utc = _cfg_data.get("created_utc", "")
            if _created_utc:
                _created_dt = datetime.fromisoformat(_created_utc.replace("Z", "+00:00"))
                _now_utc = datetime.now(tz=_created_dt.tzinfo) if _created_dt.tzinfo else datetime.now(UTC)
                _age_days = (_now_utc - _created_dt).days
                if _age_days > 90:
                    print(
                        f"warning: mobile API credential bundle is {_age_days} days old. "
                        f"Consider rotating via: delete {config_file} and restart"
                    )
        except (ValueError, OSError, KeyError, TypeError):
            pass  # Non-fatal: skip warning if config can't be parsed

    # Set descriptive process title for Task Manager visibility
    try:
        import setproctitle
        setproctitle.setproctitle("jarvis-mobile-api")
    except ImportError:
        pass

    root = repo_root()
    # Register PID file for duplicate detection and dashboard visibility
    from jarvis_engine.process_manager import is_service_running, write_pid_file, remove_pid_file
    if is_service_running("mobile_api", root):
        print("error: mobile API is already running")
        return 4

    # NOTE: run_mobile_server is called directly here (not via bus) so that
    # tests can monkeypatch main_mod.run_mobile_server.
    try:
        write_pid_file("mobile_api", root)
        run_mobile_server(
            host=host,
            port=port,
            auth_token=effective_token,
            signing_key=effective_signing_key,
            repo_root=root,
            tls=tls,
        )
    except KeyboardInterrupt:
        print("\nmobile_api_stopped=true")
    except RuntimeError as exc:
        print(f"error: {exc}")
        return 3
    except OSError as exc:
        print(f"error: could not bind mobile API on {host}:{port}: {exc}")
        return 3
    finally:
        remove_pid_file("mobile_api", root)
    return 0


def cmd_route(risk: str, complexity: str) -> int:
    result = _get_bus().dispatch(RouteCommand(risk=risk, complexity=complexity))
    print(f"provider={result.provider}")
    print(f"reason={result.reason}")
    return 0


def cmd_growth_eval(
    model: str,
    endpoint: str,
    tasks_path: Path,
    history_path: Path,
    num_predict: int,
    temperature: float,
    think: bool | None,
    accept_thinking: bool,
    timeout_s: int,
) -> int:
    result = _get_bus().dispatch(GrowthEvalCommand(
        model=model, endpoint=endpoint, tasks_path=tasks_path,
        history_path=history_path, num_predict=num_predict,
        temperature=temperature, think=think,
        accept_thinking=accept_thinking, timeout_s=timeout_s,
    ))
    run = result.run
    if run is None:
        print("error: growth eval failed")
        return 2
    print("growth_eval_completed=true")
    print(f"model={run.model}")
    print(f"score_pct={run.score_pct}")
    print(f"avg_tps={run.avg_tps}")
    print(f"avg_latency_s={run.avg_latency_s}")
    for task_result in run.results:
        print(
            "task="
            f"{task_result.task_id} "
            f"coverage_pct={round(task_result.coverage * 100, 2)} "
            f"matched={task_result.matched}/{task_result.total} "
            f"response_sha256={task_result.response_sha256}"
        )
    return 0


def cmd_growth_report(history_path: Path, last: int) -> int:
    result = _get_bus().dispatch(GrowthReportCommand(history_path=history_path, last=last))
    summary = result.summary or {}
    print("growth_report")
    print(f"runs={summary.get('runs', 0)}")
    print(f"latest_model={summary.get('latest_model', '')}")
    print(f"latest_score_pct={summary.get('latest_score_pct', 0.0)}")
    print(f"delta_vs_prev_pct={summary.get('delta_vs_prev_pct', 0.0)}")
    print(f"window_avg_pct={summary.get('window_avg_pct', 0.0)}")
    print(f"latest_ts={summary.get('latest_ts', '')}")
    return 0


def cmd_growth_audit(history_path: Path, run_index: int) -> int:
    result = _get_bus().dispatch(GrowthAuditCommand(history_path=history_path, run_index=run_index))
    run = result.run or {}
    print("growth_audit")
    print(f"model={run.get('model', '')}")
    print(f"ts={run.get('ts', '')}")
    print(f"score_pct={run.get('score_pct', 0.0)}")
    print(f"tasks={run.get('tasks', 0)}")
    print(f"prev_run_sha256={run.get('prev_run_sha256', '')}")
    print(f"run_sha256={run.get('run_sha256', '')}")
    for audit_result in run.get("results", []):
        matched_tokens = ",".join(audit_result.get("matched_tokens", []))
        required_tokens = ",".join(audit_result.get("required_tokens", []))
        print(f"task={audit_result.get('task_id', '')}")
        print(f"required_tokens={required_tokens}")
        print(f"matched_tokens={matched_tokens}")
        print(f"prompt_sha256={audit_result.get('prompt_sha256', '')}")
        print(f"response_sha256={audit_result.get('response_sha256', '')}")
        print(f"response_source={audit_result.get('response_source', '')}")
        print(f"response={_escape_response(audit_result.get('response', ''))}")
    return 0


def cmd_intelligence_dashboard(last_runs: int, output_path: str, as_json: bool) -> int:
    result = _get_bus().dispatch(IntelligenceDashboardCommand(last_runs=last_runs, output_path=output_path, as_json=as_json))
    dashboard = result.dashboard
    if as_json:
        text = json.dumps(dashboard, ensure_ascii=True, indent=2)
        print(text)
        if output_path.strip():
            out = Path(output_path).resolve()
            try:
                out.relative_to(repo_root().resolve())
            except ValueError:
                print("error: output path must be within project root.")
                return 2
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            print(f"dashboard_saved={out}")
        return 0

    jarvis = dashboard.get("jarvis", {})
    methodology = dashboard.get("methodology", {})
    etas = dashboard.get("etas", [])
    achievements = dashboard.get("achievements", {})
    ranking = dashboard.get("ranking", [])

    print("intelligence_dashboard")
    print(f"generated_utc={dashboard.get('generated_utc', '')}")
    print(f"jarvis_score_pct={jarvis.get('score_pct', 0.0)}")
    print(f"jarvis_delta_vs_prev_pct={jarvis.get('delta_vs_prev_pct', 0.0)}")
    print(f"jarvis_window_avg_pct={jarvis.get('window_avg_pct', 0.0)}")
    print(f"latest_model={jarvis.get('latest_model', '')}")
    print(f"history_runs={methodology.get('history_runs', 0)}")
    print(f"slope_score_pct_per_run={methodology.get('slope_score_pct_per_run', 0.0)}")
    print(f"avg_days_per_run={methodology.get('avg_days_per_run', 0.0)}")
    for idx, item in enumerate(ranking, start=1):
        print(f"rank_{idx}={item.get('name','')}:{item.get('score_pct', 0.0)}")
    for row in etas:
        eta = row.get("eta", {})
        print(
            "eta "
            f"target={row.get('target_name','')} "
            f"target_score_pct={row.get('target_score_pct', 0.0)} "
            f"status={eta.get('status','')} "
            f"runs={eta.get('runs', '')} "
            f"days={eta.get('days', '')}"
        )
    new_unlocks = achievements.get("new", [])
    if isinstance(new_unlocks, list):
        for item in new_unlocks:
            if not isinstance(item, dict):
                continue
            print(f"achievement_unlocked={item.get('label', '')}")

    if output_path.strip():
        out = Path(output_path).resolve()
        try:
            out.relative_to(repo_root().resolve())
        except ValueError:
            print("error: output path must be within project root.")
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(dashboard, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"dashboard_saved={out}")
    return 0


def cmd_brain_status(as_json: bool) -> int:
    result = _get_bus().dispatch(BrainStatusCommand(as_json=as_json))
    status = result.status
    if as_json:
        print(json.dumps(status, ensure_ascii=True, indent=2))
        return 0
    print("brain_status")
    print(f"updated_utc={status.get('updated_utc', '')}")
    branch_count = status.get("branch_count", 0)
    print(f"branch_count={branch_count}")
    branches = status.get("branches", [])
    if isinstance(branches, list):
        for row in branches[:12]:
            if not isinstance(row, dict):
                continue
            print(
                f"branch={row.get('branch','')} count={row.get('count', 0)} "
                f"last_ts={row.get('last_ts','')} summary={row.get('last_summary','')}"
            )
    # Structured response for UI consumption (UI-05)
    branch_names = [str(row.get("branch", "")) for row in branches[:6] if isinstance(row, dict)]
    summary = f"Brain has {branch_count} branch(es)"
    if branch_names:
        summary += f": {', '.join(branch_names)}"
    print(f"response={summary}")
    return 0


def cmd_brain_context(query: str, max_items: int, max_chars: int, as_json: bool) -> int:
    if not query.strip():
        print("error: query is required")
        return 2
    result = _get_bus().dispatch(BrainContextCommand(query=query, max_items=max_items, max_chars=max_chars, as_json=as_json))
    packet = result.packet
    if as_json:
        print(json.dumps(packet, ensure_ascii=True, indent=2))
        return 0
    print("brain_context")
    print(f"query={packet.get('query', '')}")
    print(f"selected_count={packet.get('selected_count', 0)}")
    selected = packet.get("selected", [])
    if isinstance(selected, list):
        for idx, row in enumerate(selected, start=1):
            if not isinstance(row, dict):
                continue
            print(
                f"context_{idx}=branch:{row.get('branch','')} "
                f"source:{row.get('source','')} "
                f"kind:{row.get('kind','')} "
                f"summary:{row.get('summary','')}"
            )
    facts = packet.get("canonical_facts", [])
    if isinstance(facts, list):
        for idx, item in enumerate(facts, start=1):
            if not isinstance(item, dict):
                continue
            print(
                f"fact_{idx}=key:{item.get('key','')} "
                f"value:{item.get('value','')} "
                f"confidence:{item.get('confidence', 0.0)}"
            )
    return 0


def cmd_brain_compact(keep_recent: int, as_json: bool) -> int:
    bus_result = _get_bus().dispatch(BrainCompactCommand(keep_recent=keep_recent, as_json=as_json))
    result = bus_result.result
    if as_json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    print("brain_compact")
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


def cmd_brain_regression(as_json: bool) -> int:
    result = _get_bus().dispatch(BrainRegressionCommand(as_json=as_json))
    report = result.report
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0
    print("brain_regression_report")
    for key, value in report.items():
        print(f"{key}={value}")
    return 0


def cmd_knowledge_status(as_json: bool) -> int:
    result = _get_bus().dispatch(KnowledgeStatusCommand(as_json=as_json))
    if as_json:
        print(json.dumps({
            "node_count": result.node_count,
            "edge_count": result.edge_count,
            "locked_count": result.locked_count,
            "pending_contradictions": result.pending_contradictions,
            "graph_hash": result.graph_hash,
        }, ensure_ascii=True, indent=2))
        return 0
    print("knowledge_status")
    print(f"node_count={result.node_count}")
    print(f"edge_count={result.edge_count}")
    print(f"locked_count={result.locked_count}")
    print(f"pending_contradictions={result.pending_contradictions}")
    print(f"graph_hash={result.graph_hash}")
    return 0


def cmd_contradiction_list(status: str, limit: int, as_json: bool) -> int:
    result = _get_bus().dispatch(ContradictionListCommand(status=status, limit=limit))
    if as_json:
        print(json.dumps({"contradictions": result.contradictions}, ensure_ascii=True, indent=2, default=str))
        return 0
    if not result.contradictions:
        print("No contradictions found.")
        return 0
    for c in result.contradictions:
        print(f"id={c.get('contradiction_id')} node={c.get('node_id')} "
              f"existing={c.get('existing_value')!r} incoming={c.get('incoming_value')!r} "
              f"status={c.get('status')} created={c.get('created_at')}")
    return 0


def cmd_contradiction_resolve(contradiction_id: int, resolution: str, merge_value: str) -> int:
    result = _get_bus().dispatch(ContradictionResolveCommand(
        contradiction_id=contradiction_id,
        resolution=resolution,
        merge_value=merge_value,
    ))
    if result.success:
        print(f"resolved=true node_id={result.node_id} resolution={result.resolution}")
        print(result.message)
    else:
        print("resolved=false")
        print(result.message)
        return 1
    return 0


def cmd_fact_lock(node_id: str, action: str) -> int:
    result = _get_bus().dispatch(FactLockCommand(node_id=node_id, action=action))
    if result.success:
        print(f"success=true node_id={result.node_id} locked={result.locked}")
    else:
        print(f"success=false node_id={result.node_id}")
        return 1
    return 0


def cmd_knowledge_regression(snapshot_path: str, as_json: bool) -> int:
    result = _get_bus().dispatch(KnowledgeRegressionCommand(
        snapshot_path=snapshot_path,
        as_json=as_json,
    ))
    report = result.report or {}
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2, default=str))
        return 0
    status = report.get("status", "unknown")
    print(f"knowledge_regression status={status}")
    if report.get("message"):
        print(report["message"])
    for d in report.get("discrepancies", []):
        print(f"  [{d.get('severity')}] {d.get('type')}: {d.get('message')}")
    current = report.get("current", {})
    if current:
        print(f"  current: nodes={current.get('node_count', 0)} edges={current.get('edge_count', 0)} "
              f"locked={current.get('locked_count', 0)} hash={current.get('graph_hash', '')}")
    return 0


def cmd_memory_snapshot(create: bool, verify_path: str | None, note: str) -> int:
    result = _get_bus().dispatch(MemorySnapshotCommand(create=create, verify_path=verify_path, note=note))
    if result.created:
        print("memory_snapshot_created=true")
        print(f"snapshot_path={result.snapshot_path}")
        print(f"metadata_path={result.metadata_path}")
        print(f"signature_path={result.signature_path}")
        print(f"sha256={result.sha256}")
        print(f"file_count={result.file_count}")
        return 0
    if result.verified:
        print("memory_snapshot_verification")
        print(f"ok={result.ok}")
        print(f"reason={result.reason}")
        print(f"expected_sha256={result.expected_sha256}")
        print(f"actual_sha256={result.actual_sha256}")
        return 0 if result.ok else 2
    print("error: choose --create or --verify-path")
    return 2


def cmd_memory_maintenance(keep_recent: int, snapshot_note: str) -> int:
    result = _get_bus().dispatch(MemoryMaintenanceCommand(keep_recent=keep_recent, snapshot_note=snapshot_note))
    report = result.report
    print("memory_maintenance")
    print(f"status={report.get('status', 'unknown')}")
    print(f"report_path={report.get('report_path', '')}")
    compact = report.get("compact", {})
    if isinstance(compact, dict):
        print(f"compacted={compact.get('compacted', False)}")
        print(f"total_records={compact.get('total_records', 0)}")
        print(f"kept_records={compact.get('kept_records', 0)}")
    regression = report.get("regression", {})
    if isinstance(regression, dict):
        print(f"regression_status={regression.get('status', '')}")
        print(f"duplicate_ratio={regression.get('duplicate_ratio', 0.0)}")
        print(f"unresolved_conflicts={regression.get('unresolved_conflicts', 0)}")
    snapshot = report.get("snapshot", {})
    if isinstance(snapshot, dict):
        print(f"snapshot_path={snapshot.get('path', '')}")
    return 0


def cmd_persona_config(
    *,
    enable: bool,
    disable: bool,
    humor_level: int | None,
    mode: str,
    style: str,
) -> int:
    result = _get_bus().dispatch(PersonaConfigCommand(
        enable=enable, disable=disable, humor_level=humor_level, mode=mode, style=style,
    ))
    cfg = result.config

    # Handler returns a dict with "error" key on conflicting flags
    if isinstance(cfg, dict) and "error" in cfg:
        print(f"error={cfg['error']}")
        return 1

    print("persona_config")
    print(f"enabled={cfg.enabled}")
    print(f"mode={cfg.mode}")
    print(f"style={cfg.style}")
    print(f"humor_level={cfg.humor_level}")
    print(f"updated_utc={cfg.updated_utc}")
    return 0


def cmd_desktop_widget() -> int:
    try:
        import setproctitle
        setproctitle.setproctitle("jarvis-widget")
    except ImportError:
        pass
    root = repo_root()
    from jarvis_engine.process_manager import is_service_running, write_pid_file, remove_pid_file
    if is_service_running("widget", root):
        print("error: widget is already running")
        return 4
    try:
        write_pid_file("widget", root)
        result = _get_bus().dispatch(DesktopWidgetCommand())
        if result.return_code != 0:
            print("error: desktop widget unavailable")
        return result.return_code
    finally:
        remove_pid_file("widget", root)


def cmd_run_task(
    task_type: str,
    prompt: str,
    execute: bool,
    approve_privileged: bool,
    model: str,
    endpoint: str,
    quality_profile: str,
    output_path: str | None,
) -> int:
    result = _get_bus().dispatch(RunTaskCommand(
        task_type=task_type, prompt=prompt, execute=execute,
        approve_privileged=approve_privileged, model=model,
        endpoint=endpoint, quality_profile=quality_profile,
        output_path=output_path,
    ))
    print(f"allowed={result.allowed}")
    print(f"provider={result.provider}")
    print(f"plan={result.plan}")
    print(f"reason={result.reason}")
    if result.output_path:
        print(f"output_path={result.output_path}")
    if result.output_text:
        print("output_text_begin")
        print(result.output_text)
        print("output_text_end")
    if result.auto_ingest_record_id:
        print(f"auto_ingest_record_id={result.auto_ingest_record_id}")
    return result.return_code


def cmd_ops_brief(snapshot_path: Path, output_path: Path | None) -> int:
    result = _get_bus().dispatch(OpsBriefCommand(snapshot_path=snapshot_path, output_path=output_path))
    print(result.brief)
    if result.saved_path:
        print(f"brief_saved={result.saved_path}")
    return 0


def cmd_ops_export_actions(snapshot_path: Path, actions_path: Path) -> int:
    result = _get_bus().dispatch(OpsExportActionsCommand(snapshot_path=snapshot_path, actions_path=actions_path))
    print(f"actions_exported={result.actions_path}")
    print(f"action_count={result.action_count}")
    return 0


def cmd_ops_sync(output_path: Path) -> int:
    result = _get_bus().dispatch(OpsSyncCommand(output_path=output_path))
    summary = result.summary
    if summary is None:
        print("error: ops sync failed")
        return 2
    print(f"snapshot_path={summary.snapshot_path}")
    print(f"tasks={summary.tasks}")
    print(f"calendar_events={summary.calendar_events}")
    print(f"emails={summary.emails}")
    print(f"bills={summary.bills}")
    print(f"subscriptions={summary.subscriptions}")
    print(f"medications={summary.medications}")
    print(f"school_items={summary.school_items}")
    print(f"family_items={summary.family_items}")
    print(f"projects={summary.projects}")
    print(f"connectors_ready={summary.connectors_ready}")
    print(f"connectors_pending={summary.connectors_pending}")
    print(f"connector_prompts={summary.connector_prompts}")
    if summary.connector_prompts > 0:
        try:
            raw = json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        prompts_raw = raw.get("connector_prompts", [])
        if not isinstance(prompts_raw, list):
            prompts_raw = []
        raw["connector_prompts"] = prompts_raw
        prompts = prompts_raw
        for item in prompts:
            if not isinstance(item, dict):
                continue
            print(
                "connector_prompt "
                f"id={item.get('connector_id','')} "
                f"voice=\"{item.get('option_voice','')}\" "
                f"tap={item.get('option_tap_url','')}"
            )
    return 0


def _cmd_ops_autopilot_impl(
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
) -> int:
    """Implementation body for ops-autopilot (called by handler via callback)."""
    cmd_connect_bootstrap(auto_open=auto_open_connectors)
    sync_rc = cmd_ops_sync(snapshot_path)
    if sync_rc != 0:
        return sync_rc
    brief_rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    if brief_rc != 0:
        return brief_rc
    export_rc = cmd_ops_export_actions(snapshot_path=snapshot_path, actions_path=actions_path)
    if export_rc != 0:
        return export_rc
    return cmd_automation_run(
        actions_path=actions_path,
        approve_privileged=approve_privileged,
        execute=execute,
    )


def cmd_ops_autopilot(
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
) -> int:
    result = _get_bus().dispatch(OpsAutopilotCommand(
        snapshot_path=snapshot_path, actions_path=actions_path,
        execute=execute, approve_privileged=approve_privileged,
        auto_open_connectors=auto_open_connectors,
    ))
    return result.return_code


def cmd_automation_run(actions_path: Path, approve_privileged: bool, execute: bool) -> int:
    result = _get_bus().dispatch(AutomationRunCommand(
        actions_path=actions_path, approve_privileged=approve_privileged, execute=execute,
    ))
    for out in result.outcomes:
        print(
            f"title={out.title} allowed={out.allowed} executed={out.executed} "
            f"return_code={out.return_code} reason={out.reason}"
        )
        if out.stderr:
            print(f"stderr={out.stderr.strip()}")
    return 0


def cmd_mission_create(topic: str, objective: str, sources: list[str]) -> int:
    result = _get_bus().dispatch(MissionCreateCommand(topic=topic, objective=objective, sources=sources))
    if result.return_code != 0:
        print("error: mission creation failed")
        return result.return_code
    mission = result.mission
    print("learning_mission_created=true")
    print(f"mission_id={mission.get('mission_id', '')}")
    print(f"topic={mission.get('topic', '')}")
    print(f"sources={','.join(str(s) for s in mission.get('sources', []))}")
    return 0


def cmd_mission_status(last: int) -> int:
    result = _get_bus().dispatch(MissionStatusCommand(last=last))
    if not result.missions:
        print("learning_missions=none")
        print("learning_missions_active=false")
        print("learning_mission_count=0")
        print("response=No active learning missions at the moment.")
        return 0

    counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "other": 0}
    active_count = 0
    for mission in result.missions:
        status = str(mission.get("status", "")).strip().lower()
        if status in ("pending", "running"):
            active_count += 1
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1

    print(f"learning_mission_count={result.total_count}")
    print(f"learning_missions_active={'true' if active_count > 0 else 'false'}")
    print(f"learning_missions_active_count={active_count}")
    print(f"learning_missions_pending={counts['pending']}")
    print(f"learning_missions_running={counts['running']}")
    print(f"learning_missions_completed={counts['completed']}")
    print(f"learning_missions_failed={counts['failed']}")
    print(f"learning_missions_cancelled={counts['cancelled']}")

    summary_parts: list[str] = []
    for mission in result.missions:
        mission_id = str(mission.get("mission_id", ""))
        status = str(mission.get("status", ""))
        progress_pct = int(mission.get("progress_pct", 0) or 0)
        topic = str(mission.get("topic", ""))
        findings = int(mission.get("verified_findings", 0) or 0)
        updated_utc = str(mission.get("updated_utc", ""))
        status_detail = str(mission.get("status_detail", "")).strip()

        print(
            f"mission_id={mission_id} "
            f"status={status} "
            f"progress_pct={progress_pct} "
            f"topic={topic} "
            f"verified_findings={findings} "
            f"updated_utc={updated_utc}"
        )
        if status_detail:
            print(f"mission_status_detail={status_detail}")
        if mission.get("progress_bar"):
            print(f"progress_bar={mission.get('progress_bar', '')}")

        summary = f"{topic} ({status}, {progress_pct}%, {findings} findings)"
        if status_detail:
            summary += f" — {status_detail}"
        summary_parts.append(summary)

    print(f"response=Learning missions ({result.total_count} total, {active_count} active): " + " | ".join(summary_parts))
    return 0


def cmd_mission_cancel(mission_id: str) -> int:
    result = _get_bus().dispatch(MissionCancelCommand(mission_id=mission_id))
    if not result.cancelled:
        print(f"error: {result.error or 'cancel failed'}")
        print(f"response=Could not cancel mission: {result.error or 'unknown error'}")
        return 2
    mission = result.mission
    print("mission_cancelled=true")
    print(f"mission_id={mission.get('mission_id', '')}")
    print(f"topic={mission.get('topic', '')}")
    print(f"response=Cancelled mission: {mission.get('topic', '')}")
    return 0


def cmd_consolidate(branch: str, max_groups: int, dry_run: bool) -> int:
    from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand
    result = _get_bus().dispatch(ConsolidateMemoryCommand(
        branch=branch, max_groups=max_groups, dry_run=dry_run,
    ))
    print(f"consolidation_groups={result.groups_found}")
    print(f"consolidation_records={result.records_consolidated}")
    print(f"consolidation_new_facts={result.new_facts_created}")
    if result.errors:
        print(f"consolidation_errors={len(result.errors)}")
        for e in result.errors:
            print(f"  {e}")
    print(f"response={_escape_response(result.message)}")
    return 0 if not result.errors else 2


def cmd_mission_run(mission_id: str, max_results: int, max_pages: int, auto_ingest: bool) -> int:
    result = _get_bus().dispatch(MissionRunCommand(
        mission_id=mission_id, max_results=max_results, max_pages=max_pages, auto_ingest=auto_ingest,
    ))
    if result.return_code != 0:
        print("error: mission run failed")
        return result.return_code

    report = result.report
    print("learning_mission_completed=true")
    print(f"mission_id={report.get('mission_id', '')}")
    print(f"candidate_count={report.get('candidate_count', 0)}")
    print(f"verified_count={report.get('verified_count', 0)}")
    verified = report.get("verified_findings", [])
    if isinstance(verified, list):
        for idx, finding in enumerate(verified[:10], start=1):
            statement = str(finding.get("statement", "")) if isinstance(finding, dict) else ""
            sources = ",".join(finding.get("source_domains", [])) if isinstance(finding, dict) else ""
            print(f"verified_{idx}={statement}")
            print(f"verified_{idx}_sources={sources}")

    if result.ingested_record_id:
        print(f"mission_ingested_record_id={result.ingested_record_id}")
    return 0


def _run_next_pending_mission(*, max_results: int = 6, max_pages: int = 10) -> int:
    missions = load_missions(repo_root())
    for mission in missions:
        if str(mission.get("status", "")).lower() != "pending":
            continue
        mission_id = str(mission.get("mission_id", "")).strip()
        if not mission_id:
            continue
        print(f"mission_autorun_id={mission_id}")
        return cmd_mission_run(
            mission_id=mission_id,
            max_results=max_results,
            max_pages=max_pages,
            auto_ingest=True,
        )
    return 0


def cmd_runtime_control(
    *,
    pause: bool,
    resume: bool,
    safe_on: bool,
    safe_off: bool,
    reset: bool,
    reason: str,
) -> int:

    _bus = _get_bus()

    result = _bus.dispatch(RuntimeControlCommand(
        pause=pause, resume=resume, safe_on=safe_on, safe_off=safe_off, reset=reset, reason=reason,
    ))

    state = result.state
    print("runtime_control")
    print(f"daemon_paused={bool(state.get('daemon_paused', False))}")
    print(f"safe_mode={bool(state.get('safe_mode', False))}")
    print(f"updated_utc={state.get('updated_utc', '')}")
    if state.get("reason", ""):
        print(f"reason={state.get('reason', '')}")
    print("effect=daemon_paused_skips_autopilot,safe_mode_forces_non_executing_cycles")
    return 0


def cmd_owner_guard(
    *,
    enable: bool,
    disable: bool,
    owner_user: str,
    trust_device: str,
    revoke_device: str,
    set_master_password_value: str,
    clear_master_password_value: bool,
) -> int:
    result = _get_bus().dispatch(OwnerGuardCommand(
        enable=enable, disable=disable, owner_user=owner_user,
        trust_device=trust_device, revoke_device=revoke_device,
        set_master_password_value=set_master_password_value,
        clear_master_password_value=clear_master_password_value,
    ))
    if result.return_code != 0:
        if enable and not owner_user.strip():
            print("error: --owner-user is required with --enable")
        else:
            print("error: owner guard operation failed")
        return result.return_code
    state = result.state

    print("owner_guard")
    print(f"enabled={bool(state.get('enabled', False))}")
    print(f"owner_user_id={state.get('owner_user_id', '')}")
    trusted = state.get("trusted_mobile_devices", [])
    if isinstance(trusted, list):
        print(f"trusted_mobile_devices={','.join(str(x) for x in trusted)}")
        print(f"trusted_mobile_device_count={len(trusted)}")
    has_master_password = bool(state.get("master_password_hash", ""))
    print(f"master_password_set={has_master_password}")
    print(f"updated_utc={state.get('updated_utc', '')}")
    print("effect=voice_run_restricted_to_owner_and_mobile_api_restricted_to_trusted_devices_when_enabled")
    return 0


def cmd_connect_status() -> int:
    result = _get_bus().dispatch(ConnectStatusCommand())
    print("connector_status")
    print(f"ready={result.ready}")
    print(f"pending={result.pending}")
    for status in result.statuses:
        print(
            f"id={status.connector_id} ready={status.ready} "
            f"permission={status.permission_granted} configured={status.configured} message={status.message}"
        )
    if result.prompts:
        print("connector_prompts_begin")
        for prompt in result.prompts:
            print(
                f"id={prompt.get('connector_id','')} "
                f"voice={prompt.get('option_voice','')} "
                f"tap={prompt.get('option_tap_url','')}"
            )
        print("connector_prompts_end")
    return 0


def cmd_connect_grant(connector_id: str, scopes: list[str]) -> int:
    result = _get_bus().dispatch(ConnectGrantCommand(connector_id=connector_id, scopes=scopes))
    if result.return_code != 0:
        print("error: connector grant failed")
        return result.return_code
    print(f"connector_id={connector_id}")
    print("granted=true")
    print(f"scopes={','.join(result.granted.get('scopes', []))}")
    print(f"granted_utc={result.granted.get('granted_utc', '')}")
    return 0


def cmd_connect_bootstrap(auto_open: bool) -> int:
    result = _get_bus().dispatch(ConnectBootstrapCommand(auto_open=auto_open))
    if result.ready:
        print("connectors_ready=true")
        return 0
    print("connectors_ready=false")
    for prompt in result.prompts:
        print(
            "connector_prompt "
            f"id={prompt.get('connector_id','')} "
            f"voice=\"{prompt.get('option_voice','')}\" "
            f"tap={prompt.get('option_tap_url','')}"
        )
    return 0


def cmd_phone_action(action: str, number: str, message: str, queue_path: Path, queue_action: bool = True) -> int:
    result = _get_bus().dispatch(PhoneActionCommand(
        action=action, number=number, message=message, queue_path=queue_path, queue_action=queue_action,
    ))
    if result.return_code != 0:
        print("error: phone action failed")
        return result.return_code
    record = result.record
    print(f"phone_action_queued={queue_action}")
    print(f"action={record.action}")
    print(f"number={record.number}")
    if record.message:
        print(f"message={record.message}")
    print(f"queue_path={queue_path}")
    return 0


def cmd_phone_spam_guard(
    call_log_path: Path,
    report_path: Path,
    queue_path: Path,
    threshold: float,
    *,
    queue_actions: bool = True,
) -> int:
    result = _get_bus().dispatch(PhoneSpamGuardCommand(
        call_log_path=call_log_path, report_path=report_path, queue_path=queue_path,
        threshold=threshold, queue_actions=queue_actions,
    ))
    if result.return_code != 0:
        if not call_log_path.exists():
            print(f"error: call log not found: {call_log_path}")
        else:
            print("error: invalid call log JSON.")
        return result.return_code

    print(f"spam_candidates={result.candidates_count}")
    print(f"queued_actions={result.queued_actions_count}")
    print(f"report_path={report_path}")
    print(f"queue_path={queue_path}")
    print("option_voice=Jarvis, block likely spam calls now")
    print("option_tap=https://www.samsung.com/us/support/answer/ANS10003465/")
    return 0


def cmd_weather(location: str) -> int:
    result = _get_bus().dispatch(WeatherCommand(location=location))
    if result.return_code != 0:
        print("error: weather lookup failed")
        return result.return_code

    print("weather_report")
    print(f"location={result.location}")
    print(f"temperature_f={result.current.get('temp_F', '')}")
    print(f"temperature_c={result.current.get('temp_C', '')}")
    print(f"feels_like_f={result.current.get('FeelsLikeF', '')}")
    print(f"humidity={result.current.get('humidity', '')}")
    if result.description:
        print(f"conditions={result.description}")
    return 0


def cmd_migrate_memory() -> int:
    """Migrate JSONL/JSON memory data into SQLite (one-time command)."""
    result = _get_bus().dispatch(MigrateMemoryCommand())
    if result.return_code != 0:
        print("error: memory migration failed")
        return result.return_code
    summary = result.summary
    totals = summary.get("totals", {})
    print("memory_migration_complete")
    print(f"total_inserted={totals.get('inserted', 0)}")
    print(f"total_skipped={totals.get('skipped', 0)}")
    print(f"total_errors={totals.get('errors', 0)}")
    print(f"db_path={summary.get('db_path', '')}")
    return 0


def cmd_web_research(query: str, *, max_results: int, max_pages: int, auto_ingest: bool) -> int:
    cleaned = query.strip()
    if not cleaned:
        print("error: query is required for web research.")
        return 2
    result = _get_bus().dispatch(WebResearchCommand(
        query=query, max_results=max_results, max_pages=max_pages, auto_ingest=auto_ingest,
    ))
    if result.return_code != 0:
        print("error: web research failed")
        return result.return_code

    report = result.report
    print("web_research")
    print(f"query={report.get('query', '')}")
    print(f"scanned_url_count={report.get('scanned_url_count', 0)}")
    findings = report.get("findings", [])
    if isinstance(findings, list):
        for idx, row in enumerate(findings[:6], start=1):
            if not isinstance(row, dict):
                continue
            print(f"source_{idx}={row.get('domain', '')} {row.get('url', '')}")
            snippet = str(row.get("snippet", "")).strip()
            if snippet:
                print(f"finding_{idx}={snippet[:260]}")

    # Emit a response= summary so the Quick Panel and TTS can display findings.
    summary_parts: list[str] = []
    if isinstance(findings, list):
        for row in findings[:4]:
            if not isinstance(row, dict):
                continue
            snippet = str(row.get("snippet", "")).strip()
            domain = str(row.get("domain", "")).strip()
            if snippet:
                summary_parts.append(f"{snippet} ({domain})" if domain else snippet)
    if summary_parts:
        print("response=" + _escape_response("Here's what I found: " + " | ".join(summary_parts)))
    else:
        _query = report.get("query", "")
        print("response=" + _escape_response(f"I searched the web for '{_query}' but couldn't find clear results."))

    if result.auto_ingest_record_id:
        print(f"auto_ingest_record_id={result.auto_ingest_record_id}")
    return 0


def cmd_mobile_desktop_sync(*, auto_ingest: bool, as_json: bool) -> int:
    bus_result = _get_bus().dispatch(MobileDesktopSyncCommand(auto_ingest=auto_ingest, as_json=as_json))
    report = bus_result.report
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print("mobile_desktop_sync")
        print(f"sync_ok={report.get('sync_ok', False)}")
        print(f"report_path={report.get('report_path', '')}")
        checks = report.get("checks", [])
        if isinstance(checks, list):
            for row in checks:
                if not isinstance(row, dict):
                    continue
                print(f"check_{row.get('name','')}={row.get('ok', False)}")
    if auto_ingest:
        rec_id = _auto_ingest_memory(
            source="task_outcome",
            kind="episodic",
            task_id=f"sync-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            content=(
                f"Mobile/Desktop sync executed. "
                f"sync_ok={report.get('sync_ok', False)}; "
                f"trusted_mobile_devices={report.get('owner_guard', {}).get('trusted_mobile_device_count', 0)}"
            ),
        )
        if rec_id:
            print(f"auto_ingest_record_id={rec_id}")
    return bus_result.return_code


def cmd_self_heal(*, force_maintenance: bool, keep_recent: int, snapshot_note: str, as_json: bool) -> int:
    bus_result = _get_bus().dispatch(SelfHealCommand(
        force_maintenance=force_maintenance, keep_recent=keep_recent,
        snapshot_note=snapshot_note, as_json=as_json,
    ))
    report = bus_result.report
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        print("self_heal")
        print(f"status={report.get('status', 'unknown')}")
        print(f"report_path={report.get('report_path', '')}")
        actions = report.get("actions", [])
        if isinstance(actions, list):
            for action in actions:
                print(f"action={action}")
        regression = report.get("regression", {})
        if isinstance(regression, dict):
            print(f"regression_status={regression.get('status', '')}")
            print(f"duplicate_ratio={regression.get('duplicate_ratio', 0.0)}")
            print(f"unresolved_conflicts={regression.get('unresolved_conflicts', 0)}")
    return bus_result.return_code


def cmd_harvest(topic: str, providers: str | None, max_tokens: int) -> int:
    provider_list = None
    if providers:
        provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    result = _get_bus().dispatch(HarvestTopicCommand(
        topic=topic,
        providers=provider_list,
        max_tokens=max_tokens,
    ))
    print(f"harvest_topic={result.topic}")
    for entry in result.results:
        status = entry.get("status", "unknown")
        provider = entry.get("provider", "unknown")
        records = entry.get("records_created", 0)
        cost = entry.get("cost_usd", 0.0)
        print(f"provider={provider} status={status} records={records} cost_usd={cost:.6f}")
    return result.return_code


def cmd_ingest_session(source: str, session_path: str | None, project_path: str | None) -> int:
    result = _get_bus().dispatch(IngestSessionCommand(
        source=source,
        session_path=session_path,
        project_path=project_path,
    ))
    print(f"ingest_session_source={result.source}")
    print(f"sessions_processed={result.sessions_processed}")
    print(f"records_created={result.records_created}")
    return result.return_code


def cmd_harvest_budget(action: str, provider: str | None, period: str | None,
                       limit_usd: float | None, limit_requests: int | None) -> int:
    result = _get_bus().dispatch(HarvestBudgetCommand(
        action=action,
        provider=provider,
        period=period,
        limit_usd=limit_usd,
        limit_requests=limit_requests,
    ))
    summary = result.summary
    if action == "set":
        print(f"budget_set provider={summary.get('provider', '')} period={summary.get('period', '')} "
              f"limit_usd={summary.get('limit_usd', 0.0)}")
    else:
        print(f"budget_period_days={summary.get('period_days', 30)}")
        print(f"budget_total_cost_usd={summary.get('total_cost_usd', 0.0):.6f}")
        for entry in summary.get("providers", []):
            print(f"provider={entry.get('provider', '')} "
                  f"cost_usd={entry.get('total_cost_usd', 0.0):.6f} "
                  f"requests={entry.get('total_requests', 0)}")
    return result.return_code


# ---------------------------------------------------------------------------
# Learning CLI commands
# ---------------------------------------------------------------------------

def cmd_learn(user_message: str, assistant_response: str) -> int:
    result = _get_bus().dispatch(LearnInteractionCommand(
        user_message=user_message,
        assistant_response=assistant_response,
        route="manual",
        topic=user_message[:100],
    ))
    print(f"records_created={result.records_created}")
    print(f"message={result.message}")
    return 0


def cmd_cross_branch_query(query: str, k: int) -> int:
    result = _get_bus().dispatch(CrossBranchQueryCommand(
        query=query,
        k=k,
    ))
    print(f"direct_results={len(result.direct_results)}")
    for dr in result.direct_results:
        print(f"  record_id={dr.get('record_id', '')} distance={dr.get('distance', 0.0):.4f}")
    print(f"cross_branch_connections={len(result.cross_branch_connections)}")
    for cb in result.cross_branch_connections:
        print(f"  {cb.get('source_branch', '?')}->{cb.get('target_branch', '?')} relation={cb.get('relation', '')}")
    print(f"branches_involved={result.branches_involved}")
    return 0


def cmd_flag_expired() -> int:
    result = _get_bus().dispatch(FlagExpiredFactsCommand())
    print(f"expired_count={result.expired_count}")
    print(f"message={result.message}")
    return 0


def cmd_memory_eval() -> int:
    from jarvis_engine.growth_tracker import (
        DEFAULT_MEMORY_TASKS,
        run_memory_eval,
    )

    from jarvis_engine.config import repo_root as _repo_root

    root = _repo_root()
    db_path = root / ".planning" / "brain" / "jarvis_memory.db"

    engine = None
    embed_service = None
    if db_path.exists():
        try:
            from jarvis_engine.memory.embeddings import EmbeddingService
            from jarvis_engine.memory.engine import MemoryEngine

            embed_service = EmbeddingService()
            engine = MemoryEngine(db_path, embed_service=embed_service)
        except Exception as exc:
            print(f"error=failed to init memory engine: {exc}")
            return 1

    try:
        results = run_memory_eval(DEFAULT_MEMORY_TASKS, engine, embed_service)
    except RuntimeError as exc:
        print(f"error={exc}")
        return 1

    for r in results:
        print(
            f"task={r.task_id} score={r.overall_score:.2f} "
            f"results={r.results_found} branch_cov={r.branch_coverage:.2f} "
            f"kw_cov={r.keyword_coverage:.2f}"
        )

    if results:
        avg = sum(r.overall_score for r in results) / len(results)
        print(f"average_score={avg:.4f}")
    else:
        print("average_score=0.0000")
    return 0


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
    # Commands that don't match any mutation marker are conversational queries
    # routed to the LLM. These are read-only (no state changes) and should
    # not be blocked by owner guard. Only explicit mutation commands above
    # require authentication.
    return True


def cmd_open_web(url: str) -> int:
    result = _get_bus().dispatch(OpenWebCommand(url=url))
    if result.return_code != 0:
        print("error=No URL provided or invalid URL.")
        return result.return_code
    print(f"opened_url={result.opened_url}")
    return 0


def _restart_mobile_api(service_name: str) -> None:
    """Watchdog callback: restart mobile_api if it crashed.

    Only handles ``mobile_api`` — daemon restart is circular and widget is
    optional, so those are intentionally ignored.
    """
    import sys as _sys

    if service_name != "mobile_api":
        return
    root = repo_root()
    config_path = root / ".planning" / "security" / "mobile_api.json"
    if not config_path.exists():
        logger.warning("Watchdog: cannot restart mobile_api — config file missing: %s", config_path)
        return
    python = _sys.executable
    engine_src = str(root / "engine" / "src")
    cmd = [
        python, "-m", "jarvis_engine.main", "serve-mobile",
        "--host", "127.0.0.1", "--port", "8787",
        "--config-file", str(config_path),
    ]
    env = os.environ.copy()
    # Ensure engine source is on PYTHONPATH
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = engine_src + (os.pathsep + existing_pp if existing_pp else "")
    try:
        if _sys.platform == "win32":
            # Detach from parent console so it survives daemon restarts
            subprocess.Popen(
                cmd,
                env=env,
                cwd=str(root / "engine"),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                cmd,
                env=env,
                cwd=str(root / "engine"),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        logger.info("Watchdog: restarted mobile_api via subprocess.")
        print("watchdog_restart_mobile_api=ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Watchdog: failed to restart mobile_api: %s", exc)
        print(f"watchdog_restart_mobile_api_error={exc}")


def _cmd_daemon_run_impl(
    interval_s: int,
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
    max_cycles: int,
    idle_interval_s: int,
    idle_after_s: int,
    run_missions: bool,
    sync_every_cycles: int = 5,
    self_heal_every_cycles: int = 20,
    self_test_every_cycles: int = 20,
    watchdog_every_cycles: int = 5,
) -> int:
    """Implementation body for daemon-run (called by handler via callback)."""
    # Set descriptive process title for Task Manager visibility
    try:
        import setproctitle
        setproctitle.setproctitle("jarvis-daemon")
    except ImportError:
        pass

    root = repo_root()
    # Register PID file for duplicate detection and dashboard visibility
    from jarvis_engine.process_manager import is_service_running, write_pid_file, remove_pid_file
    if is_service_running("daemon", root):
        print("error: daemon is already running")
        return 4
    write_pid_file("daemon", root)

    active_interval = max(30, interval_s)
    idle_interval = max(30, idle_interval_s)
    idle_after = max(60, idle_after_s)
    max_consecutive_failures = 10
    consecutive_failures = 0
    cycles = 0
    last_pressure_level = "none"
    print("jarvis_daemon_started=true")
    print(f"active_interval_s={active_interval}")
    print(f"idle_interval_s={idle_interval}")
    print(f"idle_after_s={idle_after}")
    try:
        while True:
            cycles += 1
            idle_seconds = _windows_idle_seconds()
            is_active = True if idle_seconds is None else idle_seconds < idle_after
            sleep_seconds = active_interval if is_active else idle_interval
            resource_snapshot = capture_runtime_resource_snapshot(root)
            write_resource_pressure_state(root, resource_snapshot)
            throttle = recommend_daemon_sleep(sleep_seconds, resource_snapshot)
            sleep_seconds = int(throttle.get("sleep_s", sleep_seconds))
            pressure_level = str(throttle.get("pressure_level", "none"))
            skip_heavy_tasks = bool(throttle.get("skip_heavy_tasks", False))
            gaming_state = _read_gaming_mode_state()
            control_state = read_control_state(repo_root())
            auto_detect = bool(gaming_state.get("auto_detect", False))
            auto_detect_hit = False
            detected_process = ""
            if auto_detect:
                auto_detect_hit, detected_process = _detect_active_game_process()
            gaming_mode_enabled = bool(gaming_state.get("enabled", False)) or auto_detect_hit
            daemon_paused = bool(control_state.get("daemon_paused", False))
            safe_mode = bool(control_state.get("safe_mode", False))
            cycle_start_ts = datetime.now(UTC).isoformat()
            print(f"cycle={cycles} ts={cycle_start_ts}")
            # --- Activity feed: log cycle start ---
            try:
                from jarvis_engine.activity_feed import log_activity, ActivityCategory
                log_activity(
                    ActivityCategory.DAEMON_CYCLE,
                    f"Daemon cycle {cycles} started",
                    {"cycle": cycles, "ts": cycle_start_ts, "phase": "start"},
                )
            except Exception:  # noqa: BLE001
                pass  # Activity feed is optional; never crash daemon
            print(f"daemon_paused={daemon_paused}")
            print(f"safe_mode={safe_mode}")
            print(f"gaming_mode={gaming_mode_enabled}")
            print(f"gaming_mode_auto_detect={auto_detect}")
            if detected_process:
                print(f"gaming_mode_detected_process={detected_process}")
            if gaming_state.get("reason", ""):
                print(f"gaming_mode_reason={gaming_state.get('reason', '')}")
            if control_state.get("reason", ""):
                print(f"runtime_control_reason={control_state.get('reason', '')}")
            print(f"device_active={is_active}")
            print(f"resource_pressure_level={pressure_level}")
            try:
                _m = resource_snapshot.get("metrics", {})
                _rss = _m.get("process_memory_mb", {}).get("current", 0.0)
                _cpu = _m.get("process_cpu_pct", {}).get("current", 0.0)
                _emb = _m.get("embedding_cache_mb", {}).get("current", 0.0)
                print(f"resource_process_memory_mb={_rss}")
                print(f"resource_process_cpu_pct={_cpu}")
                print(f"resource_embedding_cache_mb={_emb}")
            except Exception as exc:
                logger.debug("Resource metric print failed: %s", exc)
            if pressure_level in {"mild", "severe"}:
                print(f"resource_throttle_sleep_s={sleep_seconds}")
                if skip_heavy_tasks:
                    print("resource_skip_heavy_tasks=true")
            if pressure_level != "none" and (pressure_level != last_pressure_level or cycles % 5 == 0):
                try:
                    from jarvis_engine.activity_feed import ActivityCategory, log_activity

                    log_activity(
                        ActivityCategory.RESOURCE_PRESSURE,
                        f"Resource pressure {pressure_level}",
                        {
                            "pressure_level": pressure_level,
                            "cycle": cycles,
                            "correlation_id": f"daemon-cycle-{cycles}",
                            "metrics": resource_snapshot.get("metrics", {}),
                            "sleep_s": sleep_seconds,
                            "skip_heavy_tasks": skip_heavy_tasks,
                        },
                    )
                except Exception as exc:
                    logger.debug("Resource pressure activity log failed: %s", exc)
            elif pressure_level == "none" and last_pressure_level != "none":
                try:
                    from jarvis_engine.activity_feed import ActivityCategory, log_activity

                    log_activity(
                        ActivityCategory.RESOURCE_PRESSURE,
                        "Resource pressure recovered",
                        {
                            "pressure_level": "none",
                            "cycle": cycles,
                            "correlation_id": f"daemon-cycle-{cycles}",
                            "sleep_s": sleep_seconds,
                            "skip_heavy_tasks": skip_heavy_tasks,
                        },
                    )
                except Exception as exc:
                    logger.debug("Resource pressure recovery log failed: %s", exc)
            last_pressure_level = pressure_level
            if idle_seconds is not None:
                print(f"idle_seconds={round(idle_seconds, 1)}")
            if daemon_paused:
                print("cycle_skipped=runtime_control_daemon_paused")
                if max_cycles > 0 and cycles >= max_cycles:
                    break
                sleep_seconds = max(idle_interval, 600)
                print(f"sleep_s={sleep_seconds}")
                time.sleep(sleep_seconds)
                continue
            if gaming_mode_enabled:
                print("cycle_skipped=gaming_mode_enabled")
                if max_cycles > 0 and cycles >= max_cycles:
                    break
                sleep_seconds = max(idle_interval, 600)
                print(f"sleep_s={sleep_seconds}")
                time.sleep(sleep_seconds)
                continue
            # --- Non-core subsystems: isolated so failures never affect circuit breaker ---
            if run_missions:
                try:
                    mission_rc = _run_next_pending_mission()
                except Exception as exc:  # noqa: BLE001
                    mission_rc = 2
                    print(f"mission_cycle_error={exc}")
                else:
                    print(f"mission_cycle_rc={mission_rc}")
                # Auto-generate new missions when queue is empty (every 50 cycles)
                if cycles % 50 == 0:
                    if skip_heavy_tasks:
                        print("mission_autogen_skipped=resource_pressure")
                    else:
                        try:
                            from jarvis_engine.learning_missions import (
                                auto_generate_missions,
                                retry_failed_missions,
                            )
                            # First, retry any failed missions
                            retried = retry_failed_missions(root)
                            if retried:
                                print(f"mission_retried={retried}")
                            # Then auto-generate if still no pending
                            generated = auto_generate_missions(root, max_new=3)
                            if generated:
                                topics = ", ".join(m.get("topic", "") for m in generated)
                                print(f"mission_auto_generated={len(generated)} topics=[{topics}]")
                        except Exception as exc:  # noqa: BLE001
                            print(f"mission_autogen_error={exc}")
            if sync_every_cycles > 0 and (cycles == 1 or cycles % sync_every_cycles == 0):
                try:
                    sync_rc = cmd_mobile_desktop_sync(auto_ingest=True, as_json=False)
                except Exception as exc:  # noqa: BLE001
                    sync_rc = 2
                    print(f"sync_cycle_error={exc}")
                else:
                    print(f"sync_cycle_rc={sync_rc}")
            # --- Watchdog: check if mobile_api crashed and restart it ---
            if watchdog_every_cycles > 0 and cycles % watchdog_every_cycles == 0:
                try:
                    from jarvis_engine.process_manager import check_and_restart_services
                    dead = check_and_restart_services(root, restart_callback=_restart_mobile_api)
                    if dead:
                        print(f"watchdog_dead_services={','.join(dead)}")
                except Exception as exc:  # noqa: BLE001
                    print(f"watchdog_error={exc}")
            if self_heal_every_cycles > 0 and (cycles == 1 or cycles % self_heal_every_cycles == 0):
                if skip_heavy_tasks:
                    print("self_heal_cycle_skipped=resource_pressure")
                else:
                    try:
                        heal_rc = cmd_self_heal(
                            force_maintenance=False,
                            keep_recent=1800,
                            snapshot_note="daemon-self-heal",
                            as_json=False,
                        )
                    except Exception as exc:  # noqa: BLE001
                        heal_rc = 2
                        print(f"self_heal_cycle_error={exc}")
                    else:
                        print(f"self_heal_cycle_rc={heal_rc}")
                    # Collect KG growth metrics alongside self-heal
                    try:
                        import sqlite3 as _sqlite3
                        from jarvis_engine.proactive.kg_metrics import collect_kg_metrics, append_kg_metrics
                        db_path = root / ".planning" / "brain" / "jarvis_memory.db"
                        if db_path.exists():
                            _kg_conn = _sqlite3.connect(str(db_path), timeout=5)
                            _kg_conn.execute("PRAGMA busy_timeout=5000")
                            try:
                                # collect_kg_metrics uses kg.db — provide a lightweight shim
                                class _KGShim:
                                    def __init__(self, conn: _sqlite3.Connection) -> None:
                                        self.db = conn
                                metrics = collect_kg_metrics(_KGShim(_kg_conn))
                            finally:
                                _kg_conn.close()
                        else:
                            metrics = {"node_count": 0, "edge_count": 0}
                        history_path = root / ".planning" / "runtime" / "kg_metrics.jsonl"
                        append_kg_metrics(metrics, history_path)
                        print(f"kg_metrics_nodes={metrics.get('node_count', 0)} edges={metrics.get('edge_count', 0)}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"kg_metrics_error={exc}")
            # --- Adversarial self-test: memory quiz + regression detection ---
            if self_test_every_cycles > 0 and cycles % self_test_every_cycles == 0:
                if skip_heavy_tasks:
                    print("self_test_skipped=resource_pressure")
                else:
                    try:
                        from jarvis_engine.proactive.self_test import AdversarialSelfTest
                        bus = _get_daemon_bus()
                        engine = getattr(bus, "_engine", None)
                        embed_svc = getattr(bus, "_embed_service", None)
                        if engine is not None and embed_svc is not None:
                            tester = AdversarialSelfTest(engine, embed_svc, score_threshold=0.5)
                            quiz_result = tester.run_memory_quiz()
                            quiz_history = root / ".planning" / "runtime" / "self_test_history.jsonl"
                            tester.save_quiz_result(quiz_result, quiz_history)
                            regression = tester.check_regression(quiz_history)
                            print(f"self_test_score={quiz_result.get('average_score', 0.0):.4f}")
                            print(f"self_test_tasks={quiz_result.get('tasks_run', 0)}")
                            if regression.get("regression_detected"):
                                print(f"self_test_regression=true drop_pct={regression.get('drop_pct', 0.0)}")
                        else:
                            print("self_test_skipped=engine_not_initialized")
                    except Exception as exc:  # noqa: BLE001
                        print(f"self_test_error={exc}")
            # --- SQLite optimize: ANALYZE every 100 cycles, VACUUM every 500 ---
            if cycles % 100 == 0:
                if skip_heavy_tasks:
                    print("db_optimize_skipped=resource_pressure")
                else:
                    try:
                        bus = _get_daemon_bus()
                        engine = getattr(bus, "_engine", None)
                        if engine is not None:
                            do_vacuum = (cycles % 500 == 0)
                            opt_result = engine.optimize(vacuum=do_vacuum)
                            print(f"db_optimize_analyzed={opt_result.get('analyzed', False)}")
                            if do_vacuum:
                                print(f"db_optimize_vacuumed={opt_result.get('vacuumed', False)}")
                            if opt_result.get("errors"):
                                print(f"db_optimize_errors={len(opt_result['errors'])}")
                        else:
                            print("db_optimize_skipped=engine_not_initialized")
                    except Exception as exc:  # noqa: BLE001
                        print(f"db_optimize_error={exc}")
            # --- Knowledge graph regression check (every 10 cycles) ---
            if cycles % 10 == 0:
                try:
                    from jarvis_engine.knowledge.regression import RegressionChecker
                    from jarvis_engine.activity_feed import log_activity, ActivityCategory
                    bus = _get_daemon_bus()
                    kg = getattr(bus, "_kg", None)
                    if kg is not None:
                        rc_checker = RegressionChecker(kg)
                        current_metrics = rc_checker.capture_metrics()
                        # Compare against previous snapshot stored in module state
                        global _daemon_kg_prev_metrics
                        prev_metrics = _daemon_kg_prev_metrics
                        comparison = rc_checker.compare(prev_metrics, current_metrics)
                        _daemon_kg_prev_metrics = current_metrics
                        print(f"kg_regression_status={comparison.get('status', 'unknown')}")
                        if comparison.get("status") in ("fail", "warn"):
                            discrepancies = comparison.get("discrepancies", [])
                            print(f"kg_regression_discrepancies={len(discrepancies)}")
                            log_activity(
                                ActivityCategory.REGRESSION_CHECK,
                                f"KG regression detected: {comparison['status']}",
                                {"status": comparison["status"], "discrepancies": discrepancies},
                            )
                            # Auto-restore from backup on failure
                            if comparison["status"] == "fail":
                                backup_dir = root / ".planning" / "runtime" / "kg_backups"
                                if backup_dir.exists():
                                    backups = sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime)
                                    if backups:
                                        restored = rc_checker.restore_graph(backups[-1])
                                        print(f"kg_regression_auto_restore={'ok' if restored else 'failed'}")
                                        log_activity(
                                            ActivityCategory.REGRESSION_CHECK,
                                            f"KG auto-restore {'succeeded' if restored else 'failed'}",
                                            {"backup": str(backups[-1]), "restored": restored},
                                        )
                    else:
                        print("kg_regression_skipped=kg_not_initialized")
                except Exception as exc:  # noqa: BLE001
                    print(f"kg_regression_error={exc}")
            # --- Usage pattern prediction (LEARN-03, every 10 cycles) ---
            if cycles % 10 == 0:
                try:
                    bus = _get_daemon_bus()
                    usage_tracker = getattr(bus, "_usage_tracker", None)
                    if usage_tracker is not None:
                        from datetime import datetime as _dt
                        _now = _dt.now(UTC)
                        prediction = usage_tracker.predict_context(_now.hour, _now.weekday())
                        if prediction["interaction_count"] > 0:
                            print(f"usage_predicted_route={prediction['likely_route']}")
                            if prediction["common_topics"]:
                                print(f"usage_predicted_topics={','.join(prediction['common_topics'][:3])}")
                            print(f"usage_interaction_count={prediction['interaction_count']}")
                except Exception as exc:  # noqa: BLE001
                    print(f"usage_prediction_error={exc}")
            # --- Memory consolidation (every 50 cycles) ---
            if cycles % 50 == 0:
                if skip_heavy_tasks:
                    print("consolidation_skipped=resource_pressure")
                else:
                    try:
                        from jarvis_engine.commands.learning_commands import ConsolidateMemoryCommand
                        bus = _get_daemon_bus()
                        result = bus.dispatch(ConsolidateMemoryCommand())
                        print(f"consolidation_groups={result.groups_found}")
                        print(f"consolidation_new_facts={result.new_facts_created}")
                        if result.errors:
                            print(f"consolidation_errors={len(result.errors)}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"consolidation_error={exc}")
            # --- Entity resolution (every 100 cycles) ---
            if cycles % 100 == 0:
                if skip_heavy_tasks:
                    print("entity_resolve_skipped=resource_pressure")
                else:
                    try:
                        from jarvis_engine.knowledge.entity_resolver import EntityResolver
                        from jarvis_engine.knowledge.regression import RegressionChecker
                        from jarvis_engine.activity_feed import log_activity, ActivityCategory
                        bus = _get_daemon_bus()
                        kg = getattr(bus, "_kg", None)
                        embed_svc = getattr(bus, "_embed_service", None)
                        if kg is not None:
                            # Backup KG state before entity resolution
                            try:
                                rc_checker = RegressionChecker(kg)
                                rc_checker.backup_graph(tag="pre-entity-resolve")
                                print("entity_resolve_kg_backup=ok")
                            except Exception as exc:  # noqa: BLE001
                                print(f"entity_resolve_kg_backup_error={exc}")
                            resolver = EntityResolver(kg, embed_service=embed_svc)
                            resolve_result = resolver.auto_resolve()
                            print(f"entity_resolve_candidates={resolve_result.candidates_found}")
                            print(f"entity_resolve_merges={resolve_result.merges_applied}")
                            if resolve_result.errors:
                                print(f"entity_resolve_errors={len(resolve_result.errors)}")
                            log_activity(
                                ActivityCategory.CONSOLIDATION,
                                f"Entity resolution: {resolve_result.merges_applied} merges from {resolve_result.candidates_found} candidates",
                                {
                                    "candidates_found": resolve_result.candidates_found,
                                    "merges_applied": resolve_result.merges_applied,
                                    "errors": resolve_result.errors,
                                },
                            )
                        else:
                            print("entity_resolve_skipped=kg_not_initialized")
                    except Exception as exc:  # noqa: BLE001
                        print(f"entity_resolve_error={exc}")
            # --- Auto-harvest: autonomous knowledge growth (every 200 cycles) ---
            if cycles % 200 == 0:
                if skip_heavy_tasks:
                    print("auto_harvest_skipped=resource_pressure")
                else:
                    try:
                        from jarvis_engine.harvesting.harvester import KnowledgeHarvester, HarvestCommand
                        from jarvis_engine.harvesting.providers import (
                            GeminiProvider,
                            KimiNvidiaProvider,
                            KimiProvider,
                            MiniMaxProvider,
                        )
                        from jarvis_engine.harvesting.budget import BudgetManager
                        from jarvis_engine.activity_feed import log_activity, ActivityCategory

                        harvest_topics = _discover_harvest_topics(root)
                        if harvest_topics:
                            # Build harvester with ingest pipeline so results are stored
                            harvest_db_path = root / ".planning" / "brain" / "jarvis_memory.db"
                            h_budget = None
                            if harvest_db_path.exists():
                                h_budget = BudgetManager(harvest_db_path)
                            try:
                                h_providers = [MiniMaxProvider(), KimiProvider(), KimiNvidiaProvider(), GeminiProvider()]
                                h_available = [p for p in h_providers if p.is_available]
                                # Get pipeline components from daemon bus
                                h_bus = _get_daemon_bus()
                                h_engine = getattr(h_bus, "_engine", None)
                                h_embed = getattr(h_bus, "_embed_service", None)
                                h_kg = getattr(h_bus, "_kg", None)
                                h_pipeline = None
                                if h_engine is not None and h_embed is not None:
                                    try:
                                        from jarvis_engine.memory.classify import BranchClassifier
                                        from jarvis_engine.memory.ingest import EnrichedIngestPipeline
                                        h_classifier = BranchClassifier(h_embed)
                                        h_pipeline = EnrichedIngestPipeline(
                                            h_engine, h_embed, h_classifier, knowledge_graph=h_kg,
                                        )
                                    except Exception as exc_pipe:
                                        logger.debug("Auto-harvest pipeline init failed: %s", exc_pipe)
                                if h_available and h_pipeline is not None:
                                    harvester = KnowledgeHarvester(
                                        providers=h_available,
                                        pipeline=h_pipeline,
                                        cost_tracker=None,
                                        budget_manager=h_budget,
                                    )
                                    total_records = 0
                                    for topic in harvest_topics:
                                        topic_records = 0
                                        h_result = harvester.harvest(HarvestCommand(topic=topic, max_tokens=1024))
                                        for entry in h_result.get("results", []):
                                            topic_records += entry.get("records_created", 0)
                                        total_records += topic_records
                                        print(f"auto_harvest_topic={topic} records={topic_records}")
                                    log_activity(
                                        ActivityCategory.HARVEST,
                                        f"Auto-harvest: {len(harvest_topics)} topics, {total_records} records",
                                        {"topics": harvest_topics, "total_records": total_records},
                                    )
                                elif not h_available:
                                    print("auto_harvest_skipped=no_providers_available")
                                else:
                                    print("auto_harvest_skipped=no_ingest_pipeline")
                            finally:
                                if h_budget is not None:
                                    h_budget.close()
                        else:
                            print("auto_harvest_skipped=no_topics_discovered")
                    except Exception as exc:  # noqa: BLE001
                        print(f"auto_harvest_error={exc}")
            # --- Core autopilot: only this drives the circuit breaker ---
            exec_cycle = execute and not safe_mode
            approve_cycle = approve_privileged and not safe_mode
            if safe_mode and (execute or approve_privileged):
                print("safe_mode_override=execute_and_privileged_flags_forced_false")
            try:
                rc = cmd_ops_autopilot(
                    snapshot_path=snapshot_path,
                    actions_path=actions_path,
                    execute=exec_cycle,
                    approve_privileged=approve_cycle,
                    auto_open_connectors=auto_open_connectors,
                )
            except Exception as exc:  # noqa: BLE001
                rc = 2
                print(f"cycle_error={exc}")
            print(f"cycle_rc={rc}")
            # --- Activity feed: log cycle end ---
            try:
                from jarvis_engine.activity_feed import log_activity, ActivityCategory
                log_activity(
                    ActivityCategory.DAEMON_CYCLE,
                    f"Daemon cycle {cycles} ended (rc={rc})",
                    {"cycle": cycles, "rc": rc, "phase": "end"},
                )
            except Exception:  # noqa: BLE001
                pass  # Activity feed is optional; never crash daemon
            # Circuit breaker: only autopilot (rc) counts toward consecutive failures.
            # Mission, sync, and self-heal failures are logged but never trigger shutdown.
            if rc == 0:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                print(f"consecutive_failures={consecutive_failures}")
                if consecutive_failures >= max_consecutive_failures:
                    print("daemon_circuit_breaker_open=true cooldown=300s")
                    consecutive_failures = 0  # Reset counter after cooldown
                    time.sleep(300)  # 5-minute cooldown instead of exit
            if max_cycles > 0 and cycles >= max_cycles:
                break
            print(f"sleep_s={sleep_seconds}")
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        print("jarvis_daemon_stopped=true")
    finally:
        remove_pid_file("daemon", root)
    return 0


def cmd_daemon_run(
    interval_s: int,
    snapshot_path: Path,
    actions_path: Path,
    *,
    execute: bool,
    approve_privileged: bool,
    auto_open_connectors: bool,
    max_cycles: int,
    idle_interval_s: int,
    idle_after_s: int,
    run_missions: bool,
    sync_every_cycles: int = 5,
    self_heal_every_cycles: int = 20,
    self_test_every_cycles: int = 20,
) -> int:
    result = _get_bus().dispatch(DaemonRunCommand(
        interval_s=interval_s, snapshot_path=snapshot_path, actions_path=actions_path,
        execute=execute, approve_privileged=approve_privileged,
        auto_open_connectors=auto_open_connectors, max_cycles=max_cycles,
        idle_interval_s=idle_interval_s, idle_after_s=idle_after_s,
        run_missions=run_missions, sync_every_cycles=sync_every_cycles,
        self_heal_every_cycles=self_heal_every_cycles,
        self_test_every_cycles=self_test_every_cycles,
    ))
    return result.return_code


def cmd_voice_list() -> int:
    result = _get_bus().dispatch(VoiceListCommand())
    print("voices_windows:")
    if result.windows_voices:
        for name in result.windows_voices:
            print(f"- {name}")
    else:
        print("- none")

    print("voices_edge_en_gb:")
    if result.edge_voices:
        for name in result.edge_voices:
            print(f"- {name}")
    else:
        print("- none")
    return 0 if (result.windows_voices or result.edge_voices) else 1


def cmd_voice_say(
    text: str,
    profile: str,
    voice_pattern: str,
    output_wav: str,
    rate: int,
) -> int:
    speakable_text = _shorten_urls_for_speech(text)
    result = _get_bus().dispatch(VoiceSayCommand(
        text=speakable_text, profile=profile, voice_pattern=voice_pattern,
        output_wav=output_wav, rate=rate,
    ))
    print(f"voice={result.voice_name}")
    if result.output_wav:
        print(f"wav={result.output_wav}")
    print(result.message)
    return 0


def cmd_voice_enroll(user_id: str, wav_path: str, replace: bool) -> int:
    result = _get_bus().dispatch(VoiceEnrollCommand(user_id=user_id, wav_path=wav_path, replace=replace))
    if result.message.startswith("error:"):
        print(result.message)
        return 2
    print(f"user_id={result.user_id}")
    print(f"profile_path={result.profile_path}")
    print(f"samples={result.samples}")
    print(result.message)
    return 0


def cmd_voice_verify(user_id: str, wav_path: str, threshold: float) -> int:
    result = _get_bus().dispatch(VoiceVerifyCommand(user_id=user_id, wav_path=wav_path, threshold=threshold))
    if result.message.startswith("error:"):
        print(result.message)
        return 2
    print(f"user_id={result.user_id}")
    print(f"score={result.score}")
    print(f"threshold={result.threshold}")
    print(f"matched={result.matched}")
    print(result.message)
    return 0 if result.matched else 2


def _emit_voice_listen_state(state: str, *, details: dict[str, object] | None = None) -> None:
    """Emit voice listening state to stdout + activity feed (best effort)."""
    print(f"listening_state={state}")
    try:
        from jarvis_engine.activity_feed import ActivityCategory, log_activity

        payload = {"state": state}
        if details:
            payload.update(details)
        log_activity(
            ActivityCategory.VOICE,
            f"Voice listen state: {state}",
            payload,
        )
    except Exception as exc:
        logger.debug("Voice listen state activity logging failed: %s", exc)


def cmd_voice_listen(
    duration: float,
    language: str,
    execute: bool,
) -> int:
    """Record from microphone, transcribe, optionally execute as voice command."""
    _emit_voice_listen_state("arming", details={"duration_s": duration, "language": language, "execute": execute})
    _emit_voice_listen_state("listening", details={"duration_s": duration, "language": language})

    result = _get_bus().dispatch(
        VoiceListenCommand(
            max_duration_seconds=duration,
            language=language,
        )
    )

    _emit_voice_listen_state("processing", details={"duration_s": result.duration_seconds})

    if result.message.startswith("error:"):
        _emit_voice_listen_state("error", details={"reason": result.message[:200]})
        print(result.message)
        return 2
    if not result.text:
        _emit_voice_listen_state("idle", details={"reason": "no_speech_detected"})
        print("(no speech detected)")
        return 0

    print(f"transcription={result.text}")
    print(f"confidence={result.confidence}")
    print(f"duration={result.duration_seconds}s")

    if execute and result.text:
        _emit_voice_listen_state("executing", details={"transcription_chars": len(result.text)})
        print("executing transcribed command...")
        return cmd_voice_run(
            text=result.text,
            execute=True,
            approve_privileged=False,
            speak=False,
            snapshot_path=Path(repo_root() / ".planning" / "ops_snapshot.live.json"),
            actions_path=Path(repo_root() / ".planning" / "actions.generated.json"),
            voice_user="conner",
            voice_auth_wav="",
            voice_threshold=0.82,
            master_password="",
        )

    _emit_voice_listen_state("idle", details={"reason": "transcription_complete", "confidence": result.confidence})
    return 0


def _web_augmented_llm_conversation(
    text: str,
    *,
    speak: bool = False,
    force_web_search: bool = False,
) -> int:
    """Run a web-search-augmented LLM conversation for a single query.

    This is the shared implementation used by:
    - Explicit "search the web for X" voice commands
    - Weather fallback when the dedicated handler fails
    - Any keyword branch that needs web-augmented LLM responses

    Returns 0 on success, 1 on failure.
    """
    bus = _get_bus()

    # --- Smart context: hybrid search + KG facts + cross-branch ---
    memory_lines, fact_lines, cross_branch_lines, preference_lines = _build_smart_context(bus, text)

    # --- Persona + structured context ---
    persona = load_persona_config(repo_root())
    if persona.enabled:
        persona_desc = (
            "You are Jarvis, an intelligent personal AI assistant. "
            "You are witty, knowledgeable, and speak like a refined British butler "
            "with dry humor. Keep responses concise and natural. "
            "Never repeat the same phrases. Vary your language. "
            "You have full access to the internet and web search. "
            "Never say you cannot access the web or that it is outside your protocol."
        )
    else:
        persona_desc = (
            "You are Jarvis, a helpful personal AI assistant. Keep responses concise. "
            "You have full access to the internet and web search. "
            "Never say you cannot access the web or that it is outside your protocol."
        )
    system_parts = [_current_datetime_prompt_line(), persona_desc]
    if fact_lines:
        system_parts.append(
            "Known facts about the user (use these to personalize your response):\n"
            + "\n".join(f"- {line}" for line in fact_lines[:6])
        )
    if memory_lines:
        system_parts.append(
            "Relevant memories (recent interactions and context):\n"
            + "\n".join(f"- {line}" for line in memory_lines[:8])
        )
    if cross_branch_lines:
        system_parts.append(
            "Cross-domain connections:\n"
            + "\n".join(f"- {line}" for line in cross_branch_lines[:6])
        )
    if preference_lines:
        system_parts.append(
            "User preferences (adjust your response style accordingly): "
            + "; ".join(preference_lines)
        )

    # --- Intent classification + model routing ---
    _llm_model: str | None = None
    _route: str = "web_research"
    _intent_cls = getattr(bus, "_intent_classifier", None)
    _avail_models = None
    _gw = getattr(bus, "_gateway", None)
    if _gw is not None:
        _avail_models = getattr(_gw, "available_model_names", lambda: None)()
    if _intent_cls is not None:
        try:
            _route, _llm_model, _conf = _intent_cls.classify(text, available_models=_avail_models)
            logger.debug("Web-augmented route: %s model=%s confidence=%.2f", _route, _llm_model, _conf)
        except Exception:
            _llm_model = None
    if _llm_model is None:
        # Privacy guard: if classifier failed, check privacy keywords
        _privacy_kws = {"password", "ssn", "bank", "credit card", "social security",
                        "medical", "health", "prescription", "salary", "income",
                        "secret", "private", "personal", "confidential", "nude",
                        "naked", "sex", "porn", "drug", "affair"}
        _lower_text = text.lower()
        if any(kw in _lower_text for kw in _privacy_kws):
            _llm_model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")
            _route = "simple_private"
            logger.debug("Privacy fallback: classifier failed, forcing local for private query")
        else:
            for _env_key, _model_alias in [
                ("GROQ_API_KEY", "kimi-k2"),
                ("MISTRAL_API_KEY", "devstral-2"),
                ("ZAI_API_KEY", "glm-4.7-flash"),
            ]:
                if os.environ.get(_env_key, ""):
                    _llm_model = _model_alias
                    break
    if _llm_model is None:
        _llm_model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")

    # --- Web search (always performed for this code path) ---
    _web_searched = False
    _web_context_text = ""
    _web_result: dict[str, object] = {}
    try:
        from jarvis_engine.web_research import run_web_research
        _web_result = run_web_research(text, max_results=5, max_pages=3, max_summary_lines=4)
        _web_lines = _web_result.get("summary_lines", [])
        if _web_lines:
            _web_searched = True
            _web_context_text = (
                "Web search results (use these to answer with current information):\n"
                + "\n".join(f"- {line}" for line in _web_lines[:4])
            )
            _web_urls = _web_result.get("scanned_urls", [])
            if _web_urls:
                _web_context_text += "\nSources: " + ", ".join(_web_urls[:3])
            system_parts.append(_web_context_text)
            # Emit source URLs for widget display
            _findings = _web_result.get("findings", [])
            if isinstance(_findings, list):
                for _idx, _row in enumerate(_findings[:4], start=1):
                    if isinstance(_row, dict):
                        _src = f"{_row.get('domain', '')} {_row.get('url', '')}".strip()
                        if _src:
                            print(f"source_{_idx}={_src}")
            logger.info("Web search augmented context for query: %s (%d results)", text[:80], len(_web_lines))
        else:
            logger.warning("Web search returned no summary lines for query: %s", text[:80])
    except Exception as exc:
        logger.warning("Web search failed for query %r: %s", text[:80], exc)

    # --- Instructions ---
    if _web_searched:
        system_parts.append(
            "Instructions: You have web search results above. Use them to give a current, accurate answer. "
            "Cite the source when using web search results. "
            "If the web results don't fully answer the question, say what you found and note what's missing. "
            "Do not re-introduce yourself unless explicitly asked."
        )
    else:
        system_parts.append(
            "Instructions: Answer the question using your knowledge. "
            "Do NOT say you cannot access the web or that you are not wired for web access. "
            "Simply provide the best answer you can with the information available. "
            "Do not re-introduce yourself unless explicitly asked."
        )
    system_prompt = "\n\n".join(system_parts)

    if not _web_searched and _requires_fresh_web_confirmation(text):
        print("intent=web_confirmation_unavailable")
        print("reason=Unable to fetch current web results right now. Please retry or check network access.")
        return 1

    # --- Dynamic max_tokens ---
    _max_tokens = max(_MAX_TOKENS_BY_ROUTE.get(_route, 512), 768)

    # --- Build messages with conversation history ---
    _hist = _get_history_messages()
    _continuity_instruction = _conversation_continuity_instruction(_llm_model, len(_hist))
    if _continuity_instruction:
        system_parts.append(_continuity_instruction)
        system_prompt = "\n\n".join(system_parts)
    _hist_tuples = tuple((m["role"], m["content"]) for m in _hist)
    _add_to_history("user", text)
    try:
        result: QueryResult = bus.dispatch(QueryCommand(
            query=text,
            system_prompt=system_prompt,
            max_tokens=_max_tokens,
            model=_llm_model,
            history=_hist_tuples,
        ))
        if result.return_code != 0:
            if _web_searched:
                fallback_lines = _web_result.get("summary_lines", []) if isinstance(_web_result, dict) else []
                if isinstance(fallback_lines, list) and fallback_lines:
                    fallback_text = "Based on live web results: " + " ".join(str(x) for x in fallback_lines[:3])
                    print(f"response={_escape_response(fallback_text)}")
                    print("model=web-research-fallback")
                    print("provider=web")
                    print("web_search_used=true")
                    return 0
            print("intent=llm_unavailable")
            print(f"reason={result.text.strip() or 'LLM gateway not available.'}")
            return 1
        elif result.text.strip():
            _add_to_history("assistant", result.text.strip())
            print(f"response={_escape_response(result.text.strip())}")
            print(f"model={result.model}")
            print(f"provider={result.provider}")
            _mark_routed_model(result.model, result.provider)
            if _web_searched:
                print("web_search_used=true")
            # Auto-learn
            try:
                bus.dispatch(LearnInteractionCommand(
                    user_message=text[:1000],
                    assistant_response=result.text.strip()[:1000],
                    task_id=f"conv-web-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                    route=_route,
                    topic=text[:100],
                ))
            except Exception as exc_learn:
                logger.warning("Enriched learning failed for web conversation: %s", exc_learn)
                try:
                    _auto_ingest_memory(
                        source="conversation",
                        kind="episodic",
                        task_id=f"conv-web-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                        content=(
                            f"User asked: {text[:400]}\n"
                            f"Jarvis responded ({result.model}): {result.text.strip()[:600]}"
                        ),
                    )
                except Exception as exc:
                    logger.warning("Auto-ingest fallback also failed for web conversation: %s", exc)
            if speak:
                cmd_voice_say(
                    text=result.text.strip(),
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            return 0
        else:
            print("intent=llm_empty_response")
            print("reason=LLM returned empty response.")
            return 1
    except Exception as exc:
        print("intent=llm_error")
        print(f"reason={exc}")
        if speak:
            cmd_voice_say(
                text="I'm having trouble connecting to my language model. Please try again.",
                profile="jarvis_like",
                voice_pattern="",
                output_wav="",
                rate=-1,
            )
        return 1


def _cmd_voice_run_impl(
    text: str,
    execute: bool,
    approve_privileged: bool,
    speak: bool,
    snapshot_path: Path,
    actions_path: Path,
    voice_user: str,
    voice_auth_wav: str,
    voice_threshold: float,
    master_password: str,
    model_override: str = "",
    skip_voice_auth_guard: bool = False,
) -> int:
    """Implementation body for voice-run (called by handler via callback)."""
    lowered = text.lower().strip()
    intent = "unknown"
    rc = 1
    _last_response = ""  # Capture assistant response for learning pipeline

    def _respond(msg: str) -> None:
        """Print response= line and capture text for learning pipeline.

        Newlines in the message are escaped so the entire response stays on
        one stdout line — the mobile API parser splits on newlines and would
        otherwise truncate multi-line LLM answers.
        """
        nonlocal _last_response
        _last_response = msg
        print(f"response={_escape_response(msg)}")

    phone_queue = repo_root() / ".planning" / "phone_actions.jsonl"

    phone_report = repo_root() / ".planning" / "phone_spam_report.json"
    phone_call_log = Path(os.getenv("JARVIS_CALL_LOG_JSON", str(repo_root() / ".planning" / "phone_call_log.json")))
    owner_guard = read_owner_guard(repo_root())
    master_password_ok = False
    if master_password.strip():
        master_password_ok = verify_master_password(repo_root(), master_password.strip())

    read_only_request = _is_read_only_voice_request(
        lowered,
        execute=execute,
        approve_privileged=approve_privileged,
    )


    def _require_state_mutation_voice_auth() -> bool:
        if skip_voice_auth_guard:
            return True
        if voice_auth_wav.strip() or master_password_ok:
            return True
        print("intent=voice_auth_required")
        print("reason=state_mutating_voice_actions_require_voice_auth_wav")
        if speak:
            cmd_voice_say(
                text="Voice authentication is required for state changing commands.",
                profile="jarvis_like",
                voice_pattern="",
                output_wav="",
                rate=-1,
            )
        return False

    if bool(owner_guard.get("enabled", False)):
        expected_owner = str(owner_guard.get("owner_user_id", "")).strip().lower()
        incoming_owner = voice_user.strip().lower()
        if expected_owner and incoming_owner != expected_owner and not master_password_ok:
            print("intent=owner_guard_blocked")
            print("reason=voice_user_not_owner")
            if speak:
                cmd_voice_say(
                    text="Owner guard blocked this command.",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            return 2
        if (
            not skip_voice_auth_guard
            and not voice_auth_wav.strip()
            and not master_password_ok
            and not read_only_request
        ):
            print("intent=owner_guard_blocked")
            print("reason=voice_auth_required_when_owner_guard_enabled")
            if speak:
                cmd_voice_say(
                    text="Owner guard requires voice authentication for state-changing commands.",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            return 2

    if (
        (execute or approve_privileged)
        and not read_only_request
        and not skip_voice_auth_guard
        and not voice_auth_wav.strip()
        and not master_password_ok
    ):
        print("intent=voice_auth_required")
        print("reason=execute_or_privileged_voice_actions_require_voice_auth_wav")
        if speak:
            cmd_voice_say(
                text="Voice authentication is required for executable commands.",
                profile="jarvis_like",
                voice_pattern="",
                output_wav="",
                rate=-1,
            )
        return 2

    if voice_auth_wav.strip():
        verify_rc = cmd_voice_verify(
            user_id=voice_user,
            wav_path=voice_auth_wav,
            threshold=voice_threshold,
        )
        if verify_rc != 0:
            print("intent=voice_auth_failed")
            if speak:
                cmd_voice_say(
                    text="Voice authentication failed. Command blocked.",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            return 2

    if ("connect" in lowered or "setup" in lowered) and any(k in lowered for k in ["email", "calendar", "all", "everything"]):
        intent = "connect_bootstrap"
        rc = cmd_connect_bootstrap(auto_open=execute)
    elif any(
        k in lowered
        for k in ["pause jarvis", "pause daemon", "pause autopilot", "go idle", "stand down", "pause yourself"]
    ):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "runtime_pause"
        rc = cmd_runtime_control(
            pause=True,
            resume=False,
            safe_on=False,
            safe_off=False,
            reset=False,
            reason="voice_command",
        )
    elif any(
        k in lowered
        for k in ["resume jarvis", "resume daemon", "resume autopilot", "wake up", "start working again"]
    ):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "runtime_resume"
        rc = cmd_runtime_control(
            pause=False,
            resume=True,
            safe_on=False,
            safe_off=False,
            reset=False,
            reason="voice_command",
        )
    elif any(k in lowered for k in ["safe mode on", "enable safe mode"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "runtime_safe_on"
        rc = cmd_runtime_control(
            pause=False,
            resume=False,
            safe_on=True,
            safe_off=False,
            reset=False,
            reason="voice_command",
        )
    elif any(k in lowered for k in ["safe mode off", "disable safe mode"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "runtime_safe_off"
        rc = cmd_runtime_control(
            pause=False,
            resume=False,
            safe_on=False,
            safe_off=True,
            reset=False,
            reason="voice_command",
        )
    elif any(k in lowered for k in ["runtime status", "control status", "safe mode status"]):

        intent = "runtime_status"
        rc = cmd_runtime_control(
            pause=False,
            resume=False,
            safe_on=False,
            safe_off=False,
            reset=False,
            reason="",
        )
    elif "auto gaming mode" in lowered and any(k in lowered for k in ["on", "enable", "start"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "gaming_mode_auto_enable"
        rc = cmd_gaming_mode(enable=None, reason="voice_command", auto_detect="on")
    elif "auto gaming mode" in lowered and any(k in lowered for k in ["off", "disable", "stop"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "gaming_mode_auto_disable"
        rc = cmd_gaming_mode(enable=None, reason="voice_command", auto_detect="off")
    elif "gaming mode" in lowered and any(k in lowered for k in ["on", "enable", "start"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "gaming_mode_enable"
        rc = cmd_gaming_mode(enable=True, reason="voice_command", auto_detect="")
    elif "gaming mode" in lowered and any(k in lowered for k in ["off", "disable", "stop"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "gaming_mode_disable"
        rc = cmd_gaming_mode(enable=False, reason="voice_command", auto_detect="")
    elif "gaming mode" in lowered and any(k in lowered for k in ["status", "state"]):
        intent = "gaming_mode_status"
        rc = cmd_gaming_mode(enable=None, reason="", auto_detect="")
    elif ("weather" in lowered or "forecast" in lowered) and "my calendar" not in lowered:
        intent = "weather"
        rc = cmd_weather(location=_extract_weather_location(text))
        if rc != 0:
            # Weather handler failed -- fall through to web-augmented LLM conversation
            logger.info("Weather handler failed (rc=%d), falling back to web-augmented LLM", rc)
            intent = "llm_conversation_weather_fallback"
            rc = _web_augmented_llm_conversation(text, speak=speak, force_web_search=True)
    elif any(
        key in lowered
        for key in [
            "search the web for",
            "search web for",
            "search the internet for",
            "search online for",
            "web search",
            "find on the web",
            "search for",
            "look up",
            "google",
            "find me",
            "find out",
        ]
    ):
        # Route through LLM with forced web search for a conversational answer.
        # Previously this called cmd_web_research which returned raw snippets
        # without LLM synthesis.
        intent = "web_research"
        rc = _web_augmented_llm_conversation(text, speak=speak, force_web_search=True)
    elif any(
        key in lowered
        for key in [
            "open website",
            "open webpage",
            "open page",
            "open url",
            "browse to",
            "go to ",
        ]
    ):
        intent = "open_web"
        if not execute:
            print("reason=Set --execute to open browser URLs.")
            return 2
        url = _extract_first_url(text)
        if not url:
            print("reason=No valid URL found. Include full URL like https://example.com")
            return 2
        rc = cmd_open_web(url)
    elif any(key in lowered for key in ["sync mobile", "sync desktop", "cross-device sync", "sync devices"]):
        intent = "mobile_desktop_sync"
        rc = cmd_mobile_desktop_sync(auto_ingest=True, as_json=False)
    elif any(key in lowered for key in ["self heal", "self-heal", "repair yourself", "diagnose yourself"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "self_heal"
        rc = cmd_self_heal(
            force_maintenance=False,
            keep_recent=1800,
            snapshot_note="voice-self-heal",
            as_json=False,
        )
    elif any(
        k in lowered
        for k in [
            "organize my day",
            "run autopilot",
            "daily autopilot",
            "plan my day",
            "plan today",
            "organize today",
            "help me prioritize",
        ]
    ):
        intent = "ops_autopilot"
        rc = cmd_ops_autopilot(
            snapshot_path=snapshot_path,
            actions_path=actions_path,
            execute=execute,
            approve_privileged=approve_privileged,
            auto_open_connectors=execute,
        )
    elif (
        ("block" in lowered and "spam" in lowered and "call" in lowered)
        or ("stop" in lowered and "scam" in lowered and "call" in lowered)
        or ("handle" in lowered and "spam" in lowered and "call" in lowered)
        or ("run" in lowered and "spam" in lowered and "scan" in lowered)
        or ("show" in lowered and "spam" in lowered and "report" in lowered)
    ):
        intent = "phone_spam_guard"
        rc = cmd_phone_spam_guard(
            call_log_path=phone_call_log,
            report_path=phone_report,
            queue_path=phone_queue,
            threshold=0.65,
            queue_actions=execute,
        )
    elif any(k in lowered for k in ["send text", "send message", "send a text", "send a message", "text to ", "message to "]):
        number = _extract_first_phone_number(text)
        intent = "phone_send_sms"
        if not number:
            print("intent=phone_send_sms")
            print("reason=No phone number found in voice command.")
            return 2
        # Extract SMS body: strip trigger phrase and number, use remainder
        sms_body = text
        for _trigger in ["send a text to", "send a message to", "send text to", "send message to", "text to", "message to"]:
            if _trigger in lowered:
                sms_body = text[lowered.index(_trigger) + len(_trigger):].strip()
                break
        # Remove the phone number from the body if present
        if number in sms_body:
            sms_body = sms_body.replace(number, "", 1).strip()
        # Fall back to colon-delimited body
        if not sms_body and ":" in text:
            sms_body = text.split(":", 1)[1].strip()
        if not sms_body:
            sms_body = text
        if not execute:
            print("reason=Set --execute to queue phone actions.")
            return 2
        rc = cmd_phone_action(
            action="send_sms",
            number=number,
            message=sms_body,
            queue_path=phone_queue,
        )
    elif any(k in lowered for k in ["ignore call", "decline call", "reject call"]):
        number = _extract_first_phone_number(text)
        intent = "phone_ignore_call"
        if not number:
            print("intent=phone_ignore_call")
            print("reason=No phone number found in voice command.")
            return 2
        if not execute:
            print("reason=Set --execute to queue phone actions.")
            return 2
        rc = cmd_phone_action(
            action="ignore_call",
            number=number,
            message="",
            queue_path=phone_queue,
        )
    elif (lowered.startswith("call ") or "place a call" in lowered or "make a call" in lowered or "phone call" in lowered) and _extract_first_phone_number(text):
        number = _extract_first_phone_number(text)
        intent = "phone_place_call"
        if not execute:
            print("reason=Set --execute to queue phone actions.")
            return 2
        rc = cmd_phone_action(
            action="place_call",
            number=number,
            message="",
            queue_path=phone_queue,
        )
    elif ("sync" in lowered) and any(k in lowered for k in ["calendar", "email", "inbox", "ops"]):
        intent = "ops_sync"
        live_snapshot = snapshot_path.with_name("ops_snapshot.live.json")
        rc = cmd_ops_sync(live_snapshot)
    elif any(k in lowered for k in ["daily brief", "ops brief", "morning brief", "give me a brief", "my brief", "run brief", "brief me"]):
        intent = "ops_brief"
        rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    elif "automation" in lowered and any(k in lowered for k in ["run", "execute", "start"]):
        intent = "automation_run"
        rc = cmd_automation_run(
            actions_path=actions_path,
            approve_privileged=approve_privileged,
            execute=execute,
        )
    elif "generate code" in lowered:
        intent = "generate_code"
        idx = lowered.index("generate code") + len("generate code")
        prompt = text[idx:].strip()
        prompt = prompt or "Generate high-quality production code for the requested task."
        rc = cmd_run_task(
            task_type="code",
            prompt=prompt,
            execute=execute,
            approve_privileged=approve_privileged,
            model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
            output_path=None,
        )
    elif "generate image" in lowered:
        intent = "generate_image"
        idx = lowered.index("generate image") + len("generate image")
        prompt = text[idx:].strip()
        prompt = prompt or "Generate a high-quality concept image."
        rc = cmd_run_task(
            task_type="image",
            prompt=prompt,
            execute=execute,
            approve_privileged=approve_privileged,
            model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
            output_path=None,
        )
    elif "generate video" in lowered:
        intent = "generate_video"
        idx = lowered.index("generate video") + len("generate video")
        prompt = text[idx:].strip()
        prompt = prompt or "Generate a high-quality short cinematic video."
        rc = cmd_run_task(
            task_type="video",
            prompt=prompt,
            execute=execute,
            approve_privileged=approve_privileged,
            model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
            output_path=None,
        )
    elif "generate 3d" in lowered or "generate a 3d model" in lowered or "generate 3d model" in lowered:
        intent = "generate_model3d"
        rc = cmd_run_task(
            task_type="model3d",
            prompt=text,
            execute=execute,
            approve_privileged=approve_privileged,
            model="qwen3-coder:30b",
            endpoint="http://127.0.0.1:11434",
            quality_profile="max_quality",
            output_path=None,
        )
    # --- Schedule / calendar / meeting queries ---
    elif any(
        k in lowered
        for k in [
            "my schedule",
            "my calendar",
            "my meetings",
            "my agenda",
            "what's on today",
            "what is on today",
            "what do i have today",
            "what's happening today",
            "what is happening today",
            "today's schedule",
            "today's meetings",
            "upcoming meetings",
            "upcoming events",
            "next meeting",
            "next appointment",
            "daily briefing",
            "morning briefing",
            "give me a briefing",
            "give me my briefing",
        ]
    ):
        intent = "ops_brief"
        rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    # --- Task queries ---
    elif any(
        k in lowered
        for k in [
            "my tasks",
            "my to-do",
            "my todo",
            "what are my tasks",
            "task list",
            "pending tasks",
            "open tasks",
            "what do i need to do",
            "what should i do",
            "what needs to be done",
        ]
    ):
        intent = "ops_brief"
        rc = cmd_ops_brief(snapshot_path=snapshot_path, output_path=None)
    # --- Memory search / knowledge queries ---
    elif any(
        k in lowered
        for k in [
            "what do you know about",
            "what do you remember about",
            "do you remember when",
            "do you remember that",
            "do you remember my",
            "search memory for",
            "search your memory for",
            "search your memory about",
            "what did i tell you about",
            "what have i said about",
        ]
    ):
        intent = "brain_context"
        # Extract the query portion after the trigger phrase (longest-first to avoid partial matches)
        _memory_triggers = [
            "what do you remember about",
            "what do you know about",
            "search your memory about",
            "search your memory for",
            "what did i tell you about",
            "what have i said about",
            "do you remember when",
            "do you remember that",
            "do you remember my",
            "search memory for",
        ]
        query_text = text
        for trigger in _memory_triggers:
            if trigger in lowered:
                idx = lowered.index(trigger) + len(trigger)
                query_text = text[idx:].strip().rstrip("?").strip()
                break
        if not query_text:
            query_text = text
        rc = cmd_brain_context(query=query_text, max_items=5, max_chars=1200, as_json=False)
    # --- Forget / unlearn ---
    elif any(
        k in lowered
        for k in [
            "forget about",
            "forget that",
            "forget everything about",
            "unlearn",
            "stop remembering",
            "delete memory of",
            "remove from memory",
        ]
    ):
        intent = "memory_forget"
        _forget_triggers = [
            "forget everything about",
            "forget about",
            "forget that",
            "delete memory of",
            "remove from memory",
            "stop remembering",
            "unlearn",
        ]
        topic = text
        for trigger in _forget_triggers:
            if trigger in lowered:
                idx = lowered.index(trigger) + len(trigger)
                topic = text[idx:].strip().rstrip(".").strip()
                break
        if not topic:
            _respond("What should I forget? Try 'Forget about [topic]'.")
            rc = 0
        else:
            bus = _get_bus()
            kg = getattr(bus, "_kg", None)
            if kg is not None:
                keywords = [w for w in topic.split() if len(w) > 2]
                if not keywords:
                    keywords = [topic]
                count = kg.retract_facts(keywords)
                _respond(f"Done. I've forgotten {count} fact(s) about '{topic}'.")
                rc = 0
            else:
                _respond("Knowledge graph is not available right now.")
                rc = 1
    # --- Memory save / remember ---
    elif any(
        k in lowered
        for k in [
            "remember that",
            "remember this",
            "save this",
            "make a note",
            "take a note",
            "note that",
            "don't forget",
        ]
    ):
        intent = "memory_ingest"
        # Extract content after the trigger phrase (include both colon and non-colon variants)
        content = text
        _remember_triggers = [
            "remember that",
            "remember this:",
            "remember this",
            "save this:",
            "save this",
            "make a note:",
            "make a note that",
            "make a note",
            "take a note:",
            "take a note that",
            "take a note",
            "note that",
            "don't forget that",
            "don't forget",
        ]
        for trigger in _remember_triggers:
            if trigger in lowered:
                idx = lowered.index(trigger) + len(trigger)
                content = text[idx:].strip()
                break
        if not content:
            content = text
        rc = cmd_ingest(
            source="user",
            kind="episodic",
            task_id=f"voice-remember-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            content=content,
        )
        if rc == 0:
            _respond("Got it, I'll remember that.")
        else:
            _respond("Sorry, I couldn't save that to memory.")
    # --- Knowledge graph queries ---
    elif any(
        k in lowered
        for k in [
            "knowledge status",
            "knowledge graph",
            "how much do you know",
            "brain status",
            "memory status",
        ]
    ):
        intent = "brain_status"
        rc = cmd_brain_status(as_json=False)
        _respond("Here's your brain status \u2014 check the details above.")
    # --- Cancel mission ---
    elif any(k in lowered for k in ["cancel mission", "cancel the mission", "stop mission", "abort mission"]):
        if not _require_state_mutation_voice_auth():
            return 2
        intent = "mission_cancel"
        # Try to extract mission ID; if not specified, cancel the most recent pending one
        mission_id = ""
        for prefix in ["cancel mission ", "cancel the mission ", "stop mission ", "abort mission "]:
            if prefix in lowered:
                mission_id = text[lowered.index(prefix) + len(prefix):].strip()
                break
        if not mission_id:
            # Auto-cancel the most recent pending mission
            from jarvis_engine.learning_missions import load_missions as _load_missions
            missions = _load_missions(repo_root())
            for m in reversed(missions):
                if str(m.get("status", "")).lower() == "pending":
                    mission_id = str(m.get("mission_id", ""))
                    break
        if not mission_id:
            _respond("No pending missions to cancel.")
            rc = 0
        else:
            rc = cmd_mission_cancel(mission_id=mission_id)
    # --- Mission / learning queries ---
    elif any(
        k in lowered
        for k in [
            "mission status",
            "learning mission",
            "active missions",
            "my missions",
        ]
    ):
        intent = "mission_status"
        rc = cmd_mission_status(last=5)
    # --- System status ---
    elif any(
        k in lowered
        for k in [
            "system status",
            "jarvis status",
            "how are you",
            "status report",
            "health check",
            "are you working",
            "are you running",
        ]
    ):
        intent = "system_status"
        rc = cmd_status()
        _respond("System status check complete.")
    else:
        # No keyword match -- route through LLM for a conversational response.
        intent = "llm_conversation"
        bus = _get_bus()

        # --- Smart context: hybrid search + KG facts + cross-branch ---
        memory_lines, fact_lines, cross_branch_lines, preference_lines = _build_smart_context(bus, text)

        # --- Persona + structured context ---
        persona = load_persona_config(repo_root())
        if persona.enabled:
            persona_desc = (
                "You are Jarvis, an intelligent personal AI assistant. "
                "You are witty, knowledgeable, and speak like a refined British butler "
                "with dry humor. Keep responses concise and natural. "
                "Never repeat the same phrases. Vary your language. "
                "You have full access to the internet and web search. "
                "Never say you cannot access the web or that it is outside your protocol."
            )
        else:
            persona_desc = (
                "You are Jarvis, a helpful personal AI assistant. Keep responses concise. "
                "You have full access to the internet and web search. "
                "Never say you cannot access the web or that it is outside your protocol."
            )
        system_parts = [_current_datetime_prompt_line(), persona_desc]
        if fact_lines:
            system_parts.append(
                "Known facts about the user (use these to personalize your response):\n"
                + "\n".join(f"- {line}" for line in fact_lines[:6])
            )
        if memory_lines:
            system_parts.append(
                "Relevant memories (recent interactions and context):\n"
                + "\n".join(f"- {line}" for line in memory_lines[:8])
            )
        if cross_branch_lines:
            system_parts.append(
                "Cross-domain connections:\n"
                + "\n".join(f"- {line}" for line in cross_branch_lines[:6])
            )
        if preference_lines:
            system_parts.append(
                "User preferences (adjust your response style accordingly): "
                + "; ".join(preference_lines)
            )
        # Defer final instructions until after web search (see below)

        # --- Intent classification + model routing (reuse bus classifier) ---
        _llm_model: str | None = None
        _route: str = "routine"
        _intent_cls = getattr(bus, "_intent_classifier", None)
        _avail_models = None
        _gw = getattr(bus, "_gateway", None)
        if _gw is not None:
            _avail_models = getattr(_gw, "available_model_names", lambda: None)()
        if _intent_cls is not None:
            try:
                _route, _llm_model, _conf = _intent_cls.classify(text, available_models=_avail_models)
                logger.debug("Conversation route: %s model=%s confidence=%.2f", _route, _llm_model, _conf)
            except Exception:
                _llm_model = None
        if _llm_model is None:
            try:
                from jarvis_engine.gateway.classifier import IntentClassifier
                _embed = getattr(bus, "_embed_service", None)
                if _embed is not None:
                    _cls = IntentClassifier(_embed)
                    _route, _llm_model, _conf = _cls.classify(text, available_models=_avail_models)
                    logger.debug("Conversation route: %s model=%s confidence=%.2f", _route, _llm_model, _conf)
            except Exception as exc:
                logger.debug("Fallback IntentClassifier classification failed: %s", exc)
        if _llm_model is None:
            # Privacy guard: if both classifiers failed, check privacy keywords
            # manually to guarantee private queries never leave the device.
            _privacy_kws = {"password", "ssn", "bank", "credit card", "social security",
                            "medical", "health", "prescription", "salary", "income",
                            "secret", "private", "personal", "confidential", "nude",
                            "naked", "sex", "porn", "drug", "affair"}
            _lower_text = text.lower()
            if any(kw in _lower_text for kw in _privacy_kws):
                _llm_model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")
                _route = "simple_private"
                logger.debug("Privacy fallback: classifier failed, forcing local for private query")
            else:
                for _env_key, _model_alias in [
                    ("GROQ_API_KEY", "kimi-k2"),
                    ("MISTRAL_API_KEY", "devstral-2"),
                    ("ZAI_API_KEY", "glm-4.7-flash"),
                ]:
                    if os.environ.get(_env_key, ""):
                        _llm_model = _model_alias
                        break
        if _llm_model is None:
            _llm_model = os.environ.get("JARVIS_LOCAL_MODEL", "gemma3:4b")

        # --- Model override from widget Tab-cycling ---
        if model_override:
            _llm_model = model_override
            logger.debug("Model overridden by user selection: %s", model_override)

        # --- Web search augmentation for queries needing current info ---
        _web_searched = False
        _web_attempted = False
        if _route == "web_research" or _needs_web_search(text):
            _web_attempted = True
            try:
                from jarvis_engine.web_research import run_web_research
                _web_result = run_web_research(text, max_results=5, max_pages=3, max_summary_lines=4)
                _web_lines = _web_result.get("summary_lines", [])
                if _web_lines:
                    _web_searched = True
                    _web_context = (
                        "Web search results (use these to answer with current information):\n"
                        + "\n".join(f"- {line}" for line in _web_lines[:4])
                    )
                    _web_urls = _web_result.get("scanned_urls", [])
                    if _web_urls:
                        _web_context += "\nSources: " + ", ".join(_web_urls[:3])
                    system_parts.append(_web_context)
                    # Emit source URLs for widget display
                    _findings = _web_result.get("findings", [])
                    if isinstance(_findings, list):
                        for _idx, _row in enumerate(_findings[:4], start=1):
                            if isinstance(_row, dict):
                                _src = f"{_row.get('domain', '')} {_row.get('url', '')}".strip()
                                if _src:
                                    print(f"source_{_idx}={_src}")
                    logger.info("Web search augmented context for query: %s (%d results)", text[:80], len(_web_lines))
                else:
                    logger.warning("Web search returned no summary lines for query: %s", text[:80])
            except Exception as exc:
                logger.warning("Web search augmentation failed for %r: %s", text[:80], exc)

        # --- Finalize system prompt with context-aware instructions ---
        if _web_searched:
            system_parts.append(
                "Instructions: Reference the user's known facts and memories when relevant. "
                "If the user asks about something you have facts for, use those facts directly. "
                "You have web search results above -- use them to give current, accurate answers. "
                "Cite the source when using web search results. "
                "If you don't have relevant information, say so honestly. "
                "Do not re-introduce yourself unless explicitly asked."
            )
        elif _web_attempted:
            system_parts.append(
                "Instructions: Reference the user's known facts and memories when relevant. "
                "If the user asks about something you have facts for, use those facts directly. "
                "Answer the question using your knowledge. "
                "Do NOT say you cannot access the web or that you are not wired for web access. "
                "Simply provide the best answer you can. "
                "Do not re-introduce yourself unless explicitly asked."
            )
        else:
            system_parts.append(
                "Instructions: Reference the user's known facts and memories when relevant. "
                "If the user asks about something you have facts for, use those facts directly. "
                "Do NOT say you cannot access the web, the internet, or that it is outside your protocol. "
                "If you don't have relevant information, say so honestly. "
                "Do not re-introduce yourself unless explicitly asked."
            )
        system_prompt = "\n\n".join(system_parts)

        if _web_attempted and not _web_searched and _requires_fresh_web_confirmation(text):
            print("intent=web_confirmation_unavailable")
            print("reason=Unable to fetch current web results right now. Please retry or check network access.")
            return 1

        # --- Dynamic max_tokens based on query complexity ---
        _max_tokens = _MAX_TOKENS_BY_ROUTE.get(_route, 512)
        if _web_searched:
            _max_tokens = max(_max_tokens, 768)  # Ensure enough tokens for web-augmented responses

        # --- Build messages with conversation history ---
        _hist = _get_history_messages()
        _continuity_instruction = _conversation_continuity_instruction(_llm_model, len(_hist))
        if _continuity_instruction:
            system_parts.append(_continuity_instruction)
            system_prompt = "\n\n".join(system_parts)
        # Don't include the current query in history (it goes as the main query)
        _hist_tuples = tuple((m["role"], m["content"]) for m in _hist)
        _add_to_history("user", text)
        try:
            result: QueryResult = bus.dispatch(QueryCommand(
                query=text,
                system_prompt=system_prompt,
                max_tokens=_max_tokens,
                model=_llm_model,
                history=_hist_tuples,
            ))
            if result.return_code != 0:
                print("intent=llm_unavailable")
                print(f"reason={result.text.strip() or 'LLM gateway not available.'}")
                rc = 1
            elif result.text.strip():
                _add_to_history("assistant", result.text.strip())
                _respond(result.text.strip())
                print(f"model={result.model}")
                print(f"provider={result.provider}")
                _mark_routed_model(result.model, result.provider)
                if _web_searched:
                    print("web_search_used=true")
                # Auto-learn: ingest through enriched pipeline (embeddings + KG)
                # when available, with legacy JSONL fallback
                try:
                    bus.dispatch(LearnInteractionCommand(
                        user_message=text[:1000],
                        assistant_response=result.text.strip()[:1000],
                        task_id=f"conv-{_route}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                        route=_route,
                        topic=text[:100],
                    ))
                except Exception as exc_learn:
                    logger.warning("Enriched learning failed for conversation: %s", exc_learn)
                    # Fallback to legacy JSONL ingest
                    try:
                        _auto_ingest_memory(
                            source="conversation",
                            kind="episodic",
                            task_id=f"conv-{_route}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                            content=(
                                f"User asked: {text[:400]}\n"
                                f"Jarvis responded ({result.model}): {result.text.strip()[:600]}"
                            ),
                        )
                    except Exception as exc:
                        logger.warning("Legacy JSONL auto-ingest fallback also failed: %s", exc)
                if speak:
                    cmd_voice_say(
                        text=result.text.strip(),
                        profile="jarvis_like",
                        voice_pattern="",
                        output_wav="",
                        rate=-1,
                    )
                rc = 0
            else:
                print("intent=llm_empty_response")
                print("reason=LLM returned empty response.")
                rc = 1
        except Exception as exc:
            print("intent=llm_error")
            print(f"reason={exc}")
            if speak:
                cmd_voice_say(
                    text="I'm having trouble connecting to my language model. Please try again.",
                    profile="jarvis_like",
                    voice_pattern="",
                    output_wav="",
                    rate=-1,
                )
            rc = 1

    print(f"intent={intent}")
    print(f"status_code={rc}")
    if rc == 0:
        try:
            auto_id = _auto_ingest_memory(
                source="user",
                kind="episodic",
                task_id=f"voice-{intent}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                content=(
                    f"Voice command accepted. intent={intent}; status_code={rc}; execute={execute}; "
                    f"approve_privileged={approve_privileged}; voice_user={voice_user}; text={text[:500]}"
                ),
            )
            if auto_id:
                print(f"auto_ingest_record_id={auto_id}")
        except Exception as exc:
            logger.debug("Auto-ingest of voice command memory failed: %s", exc)
        # Enriched learning for ALL successful commands (not just LLM path).
        # Runs in a daemon thread to avoid blocking the HTTP response — the
        # enriched pipeline may lazy-load embedding models on first call.
        if intent != "llm_conversation":
            learn_response = _last_response or f"[{intent}] Command executed successfully."
            # Capture bus reference on current thread (where repo_root override is active)
            try:
                _learn_bus = _get_bus()
            except Exception:
                _learn_bus = None
            if _learn_bus is not None:
                _learn_cmd = LearnInteractionCommand(
                    user_message=text[:1000],
                    assistant_response=learn_response[:1000],
                    task_id=f"learn-{intent}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
                    route=intent,
                    topic=text[:100],
                )

                def _bg_learn(_bus: "CommandBus" = _learn_bus, _cmd: "LearnInteractionCommand" = _learn_cmd) -> None:
                    try:
                        _bus.dispatch(_cmd)
                    except Exception as exc:
                        logger.warning("Background enriched learning failed: %s", exc)

                threading.Thread(target=_bg_learn, daemon=True).start()
    if speak and intent != "llm_conversation":
        persona = load_persona_config(repo_root())
        persona_line = compose_persona_reply(
            persona,
            intent=intent,
            success=(rc == 0),
            reason="" if rc == 0 else "failed or requires approval",
        )
        cmd_voice_say(
            text=persona_line,
            profile="jarvis_like",
            voice_pattern="",
            output_wav="",
            rate=-1,
        )
    return rc


def cmd_voice_run(
    text: str,
    execute: bool,
    approve_privileged: bool,
    speak: bool,
    snapshot_path: Path,
    actions_path: Path,
    voice_user: str,
    voice_auth_wav: str,
    voice_threshold: float,
    master_password: str,
    model_override: str = "",
    skip_voice_auth_guard: bool = False,
) -> int:
    result = _get_bus().dispatch(VoiceRunCommand(
        text=text, execute=execute, approve_privileged=approve_privileged,
        speak=speak, snapshot_path=snapshot_path, actions_path=actions_path,
        voice_user=voice_user, voice_auth_wav=voice_auth_wav,
        voice_threshold=voice_threshold, master_password=master_password,
        model_override=model_override,
        skip_voice_auth_guard=skip_voice_auth_guard,
    ))
    return result.return_code


def cmd_proactive_check(snapshot_path: str) -> int:
    result = _get_bus().dispatch(ProactiveCheckCommand(snapshot_path=snapshot_path))
    print(f"alerts_fired={result.alerts_fired}")
    if result.alerts_fired:
        try:
            alerts = json.loads(result.alerts)
        except (json.JSONDecodeError, TypeError):
            alerts = []
        for a in alerts:
            if not isinstance(a, dict):
                continue
            print(f"  [{a.get('rule_id', '?')}] {a.get('message', '')}")
    print(f"message={result.message}")
    if result.diagnostics:
        print(f"diagnostics={result.diagnostics}")
    return 0


def cmd_wake_word(threshold: float) -> int:
    result = _get_bus().dispatch(WakeWordStartCommand(threshold=threshold))
    print(f"started={result.started}")
    print(f"message={result.message}")
    if result.started:
        # Block until interrupted
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Wake word detection stopped.")
    return 0


def cmd_cost_reduction(days: int) -> int:
    result = _get_bus().dispatch(CostReductionCommand(days=days))
    print(f"local_pct={result.local_pct}")
    print(f"cloud_cost_usd={result.cloud_cost_usd}")
    print(f"failed_count={result.failed_count}")
    print(f"failed_cost_usd={result.failed_cost_usd}")
    print(f"trend={result.trend}")
    print(f"message={result.message}")
    return 0


def cmd_self_test(threshold: float) -> int:
    result = _get_bus().dispatch(SelfTestCommand(score_threshold=threshold))
    print(f"average_score={result.average_score:.4f}")
    print(f"tasks_run={result.tasks_run}")
    print(f"regression_detected={result.regression_detected}")
    for task_score in result.per_task_scores:
        print(f"  task={task_score.get('task_id', '?')} score={task_score.get('score', 0.0):.4f}")
    print(f"message={result.message}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis engine bootstrap CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show engine bootstrap status.")

    p_log = sub.add_parser("log", help="Append an event to memory log.")
    p_log.add_argument("--type", required=True, help="Event type label.")
    p_log.add_argument("--message", required=True, help="Event description.")

    p_ingest = sub.add_parser("ingest", help="Append structured memory from a source.")
    p_ingest.add_argument(
        "--source",
        required=True,
        choices=["user", "claude", "opus", "gemini", "task_outcome"],
    )
    p_ingest.add_argument(
        "--kind",
        required=True,
        choices=["episodic", "semantic", "procedural"],
    )
    p_ingest.add_argument("--task-id", required=True, help="Task/session id.")
    p_ingest.add_argument("--content", required=True, help="Memory content.")

    p_mobile = sub.add_parser("serve-mobile", help="Run secure mobile ingestion API.")
    p_mobile.add_argument("--host", default="127.0.0.1")
    p_mobile.add_argument("--port", type=int, default=8787)
    p_mobile.add_argument("--token", help="Shared token. Falls back to JARVIS_MOBILE_TOKEN env var.")
    p_mobile.add_argument(
        "--signing-key",
        help="HMAC signing key. Falls back to JARVIS_MOBILE_SIGNING_KEY env var.",
    )
    p_mobile.add_argument(
        "--config-file",
        help="JSON config file with token and signing_key (avoids exposing secrets in process command line).",
    )
    p_mobile.add_argument(
        "--allow-insecure-bind",
        action="store_true",
        help="Allow non-loopback HTTP bind (for trusted LAN). Falls back to JARVIS_ALLOW_INSECURE_MOBILE_BIND env var.",
    )
    _tls_group = p_mobile.add_mutually_exclusive_group()
    _tls_group.add_argument(
        "--tls",
        action="store_true",
        default=None,
        help="Require TLS (generate self-signed cert if needed). Default: auto-detect.",
    )
    _tls_group.add_argument(
        "--no-tls",
        action="store_true",
        default=False,
        help="Explicitly disable TLS (plain HTTP).",
    )

    p_route = sub.add_parser("route", help="Get a route decision.")
    p_route.add_argument("--risk", default="low", choices=["low", "medium", "high", "critical"])
    p_route.add_argument(
        "--complexity",
        default="normal",
        choices=["easy", "normal", "hard", "very_hard"],
    )

    p_growth_eval = sub.add_parser("growth-eval", help="Run golden-task model growth evaluation.")
    p_growth_eval.add_argument("--model", required=True, help="Ollama model id.")
    p_growth_eval.add_argument("--endpoint", default="http://127.0.0.1:11434")
    p_growth_eval.add_argument(
        "--tasks-path",
        default=str(repo_root() / ".planning" / "golden_tasks.json"),
    )
    p_growth_eval.add_argument(
        "--history-path",
        default=str(repo_root() / ".planning" / "capability_history.jsonl"),
    )
    p_growth_eval.add_argument("--num-predict", type=int, default=256)
    p_growth_eval.add_argument("--temperature", type=float, default=0.0)
    p_growth_eval.add_argument("--timeout-s", type=int, default=120)
    p_growth_eval.add_argument(
        "--accept-thinking",
        action="store_true",
        help="Allow scoring from thinking text when final response is empty.",
    )
    p_growth_eval.add_argument(
        "--think",
        choices=["auto", "on", "off"],
        default="auto",
        help="Set thinking mode for supported models.",
    )

    p_growth_report = sub.add_parser("growth-report", help="Show growth trend from eval history.")
    p_growth_report.add_argument(
        "--history-path",
        default=str(repo_root() / ".planning" / "capability_history.jsonl"),
    )
    p_growth_report.add_argument("--last", type=int, default=10)

    p_growth_audit = sub.add_parser("growth-audit", help="Show auditable prompt/response evidence.")
    p_growth_audit.add_argument(
        "--history-path",
        default=str(repo_root() / ".planning" / "capability_history.jsonl"),
    )
    p_growth_audit.add_argument(
        "--run-index",
        type=int,
        default=-1,
        help="Python-style index. -1 means latest run.",
    )

    p_intelligence = sub.add_parser(
        "intelligence-dashboard",
        help="Build intelligence ranking/ETA dashboard from local growth history.",
    )
    p_intelligence.add_argument("--last-runs", type=int, default=20)
    p_intelligence.add_argument("--output-path", default=str(repo_root() / ".planning" / "intelligence_dashboard.json"))
    p_intelligence.add_argument("--json", action="store_true", help="Print full JSON payload.")

    p_brain_status = sub.add_parser("brain-status", help="Show high-level brain memory branch stats.")
    p_brain_status.add_argument("--json", action="store_true")

    p_brain_context = sub.add_parser(
        "brain-context",
        help="Build compact context packet from long-term brain memory.",
    )
    p_brain_context.add_argument("--query", required=True)
    p_brain_context.add_argument("--max-items", type=int, default=10)
    p_brain_context.add_argument("--max-chars", type=int, default=2400)
    p_brain_context.add_argument("--json", action="store_true")

    p_brain_compact = sub.add_parser("brain-compact", help="Compact old brain records into summary groups.")
    p_brain_compact.add_argument("--keep-recent", type=int, default=1800)
    p_brain_compact.add_argument("--json", action="store_true")

    p_brain_regression = sub.add_parser("brain-regression", help="Run anti-regression health checks for brain memory.")
    p_brain_regression.add_argument("--json", action="store_true")

    p_kg_status = sub.add_parser("knowledge-status", help="Show knowledge graph node/edge/locked/contradiction counts.")
    p_kg_status.add_argument("--json", action="store_true")

    p_clist = sub.add_parser("contradiction-list", help="List knowledge graph contradictions.")
    p_clist.add_argument("--status", default="pending", help="Filter by status (pending, resolved, or empty for all).")
    p_clist.add_argument("--limit", type=int, default=20)
    p_clist.add_argument("--json", action="store_true")

    p_cresolve = sub.add_parser("contradiction-resolve", help="Resolve a knowledge graph contradiction.")
    p_cresolve.add_argument("contradiction_id", type=int, help="Contradiction ID to resolve.")
    p_cresolve.add_argument("--resolution", required=True, choices=["accept_new", "keep_old", "merge"])
    p_cresolve.add_argument("--merge-value", default="", help="Merged value (required for merge resolution).")

    p_flock = sub.add_parser("fact-lock", help="Lock or unlock a knowledge graph fact node.")
    p_flock.add_argument("node_id", help="Node ID to lock or unlock.")
    p_flock.add_argument("--action", default="lock", choices=["lock", "unlock"])

    p_kg_regression = sub.add_parser("knowledge-regression", help="Run knowledge graph regression check.")
    p_kg_regression.add_argument("--snapshot", default="", help="Path to previous snapshot metadata JSON.")
    p_kg_regression.add_argument("--json", action="store_true")

    p_snapshot = sub.add_parser("memory-snapshot", help="Create or verify signed memory snapshot.")
    p_snapshot_group = p_snapshot.add_mutually_exclusive_group(required=True)
    p_snapshot_group.add_argument("--create", action="store_true")
    p_snapshot_group.add_argument("--verify-path")
    p_snapshot.add_argument("--note", default="")

    p_maintenance = sub.add_parser("memory-maintenance", help="Run compact + regression + signed snapshot maintenance.")
    p_maintenance.add_argument("--keep-recent", type=int, default=1800)
    p_maintenance.add_argument("--snapshot-note", default="nightly")

    p_web_research = sub.add_parser("web-research", help="Search the public web and summarize findings with source links.")
    p_web_research.add_argument("--query", required=True)
    p_web_research.add_argument("--max-results", type=int, default=8)
    p_web_research.add_argument("--max-pages", type=int, default=6)
    p_web_research.add_argument("--no-ingest", action="store_true")

    p_sync = sub.add_parser("mobile-desktop-sync", help="Run cross-device state checks and write sync report.")
    p_sync.add_argument("--json", action="store_true")
    p_sync.add_argument("--no-ingest", action="store_true")

    p_self_heal = sub.add_parser("self-heal", help="Run Jarvis self-healing checks and safe repairs.")
    p_self_heal.add_argument("--force-maintenance", action="store_true")
    p_self_heal.add_argument("--keep-recent", type=int, default=1800)
    p_self_heal.add_argument("--snapshot-note", default="self-heal")
    p_self_heal.add_argument("--json", action="store_true")

    p_persona = sub.add_parser("persona-config", help="Configure Jarvis personality response style.")
    p_persona.add_argument("--enable", action="store_true")
    p_persona.add_argument("--disable", action="store_true")
    p_persona.add_argument("--humor-level", type=int)
    p_persona.add_argument("--mode", default="")
    p_persona.add_argument("--style", default="")

    sub.add_parser("migrate-memory", help="Migrate JSONL/JSON memory data into SQLite (one-time).")

    sub.add_parser("desktop-widget", help="Launch desktop-native Jarvis widget window.")

    p_run_task = sub.add_parser("run-task", help="Run multimodal Jarvis task.")
    p_run_task.add_argument("--type", required=True, choices=["image", "code", "video", "model3d"])
    p_run_task.add_argument("--prompt", required=True)
    p_run_task.add_argument("--execute", action="store_true", help="Execute instead of dry-run plan.")
    p_run_task.add_argument(
        "--approve-privileged",
        action="store_true",
        help="Allow privileged task classes (video/3d).",
    )
    p_run_task.add_argument("--model", default="qwen3-coder:30b")
    p_run_task.add_argument("--endpoint", default="http://127.0.0.1:11434")
    p_run_task.add_argument(
        "--quality-profile",
        default="max_quality",
        choices=["max_quality", "balanced", "fast"],
    )
    p_run_task.add_argument("--output-path")

    p_ops_brief = sub.add_parser("ops-brief", help="Generate daily life operations brief.")
    p_ops_brief.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_ops_brief.add_argument("--output-path")

    p_ops_actions = sub.add_parser("ops-export-actions", help="Export suggested actions from ops snapshot.")
    p_ops_actions.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_ops_actions.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )

    p_ops_sync = sub.add_parser("ops-sync", help="Build live operations snapshot from connectors.")
    p_ops_sync.add_argument(
        "--output-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )

    p_ops_autopilot = sub.add_parser("ops-autopilot", help="Run connector check, sync, brief, action export, and automation.")
    p_ops_autopilot.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_ops_autopilot.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )
    p_ops_autopilot.add_argument("--execute", action="store_true")
    p_ops_autopilot.add_argument("--approve-privileged", action="store_true")
    p_ops_autopilot.add_argument("--auto-open-connectors", action="store_true")

    p_daemon = sub.add_parser("daemon-run", help="Run Jarvis autopilot loop continuously.")
    p_daemon.add_argument("--interval-s", type=int, default=180)
    p_daemon.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_daemon.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )
    p_daemon.add_argument("--execute", action="store_true")
    p_daemon.add_argument("--approve-privileged", action="store_true")
    p_daemon.add_argument("--auto-open-connectors", action="store_true")
    p_daemon.add_argument("--idle-interval-s", type=int, default=900)
    p_daemon.add_argument("--idle-after-s", type=int, default=300)
    p_daemon.add_argument("--max-cycles", type=int, default=0, help="For testing; 0 means run forever.")
    p_daemon.add_argument("--skip-missions", action="store_true", help="Disable background learning mission execution.")
    p_daemon.add_argument("--sync-every-cycles", type=int, default=5)
    p_daemon.add_argument("--self-heal-every-cycles", type=int, default=20)
    p_daemon.add_argument("--self-test-every-cycles", type=int, default=20)

    p_mission_create = sub.add_parser("mission-create", help="Create a learning mission.")
    p_mission_create.add_argument("--topic", required=True)
    p_mission_create.add_argument("--objective", default="")
    p_mission_create.add_argument(
        "--source",
        action="append",
        default=[],
        help="Learning source profile (repeatable), e.g. google, reddit, official_docs",
    )

    p_mission_status = sub.add_parser("mission-status", help="Show recent learning missions.")
    p_mission_status.add_argument("--last", type=int, default=10)

    p_mission_run = sub.add_parser("mission-run", help="Run one learning mission with source verification.")
    p_mission_run.add_argument("--id", required=True, help="Mission id from mission-create.")
    p_mission_run.add_argument("--max-results", type=int, default=8)
    p_mission_run.add_argument("--max-pages", type=int, default=12)
    p_mission_run.add_argument("--no-ingest", action="store_true", help="Do not ingest verified findings.")

    p_mission_cancel = sub.add_parser("mission-cancel", help="Cancel a pending learning mission.")
    p_mission_cancel.add_argument("--id", required=True, help="Mission id to cancel.")

    p_consolidate = sub.add_parser("consolidate", help="Consolidate episodic memories into semantic facts.")
    p_consolidate.add_argument("--branch", default="", help="Restrict to specific branch (empty = all).")
    p_consolidate.add_argument("--max-groups", type=int, default=20, help="Max groups to process.")
    p_consolidate.add_argument("--dry-run", action="store_true", help="Compute clusters but don't write.")

    p_runtime = sub.add_parser("runtime-control", help="Pause/resume daemon and toggle safe mode.")
    p_runtime_group = p_runtime.add_mutually_exclusive_group()
    p_runtime_group.add_argument("--pause", action="store_true")
    p_runtime_group.add_argument("--resume", action="store_true")
    p_runtime_group.add_argument("--reset", action="store_true")
    p_runtime.add_argument("--safe-on", action="store_true")
    p_runtime.add_argument("--safe-off", action="store_true")
    p_runtime.add_argument("--reason", default="")

    p_owner = sub.add_parser("owner-guard", help="Lock Jarvis to owner voice and trusted mobile devices.")
    p_owner.add_argument("--enable", action="store_true")
    p_owner.add_argument("--disable", action="store_true")
    p_owner.add_argument("--owner-user", default="")
    p_owner.add_argument("--trust-device", default="")
    p_owner.add_argument("--revoke-device", default="")
    p_owner.add_argument(
        "--set-master-password", default="",
        help="DEPRECATED: use JARVIS_MASTER_PASSWORD env var instead. "
             "CLI passwords are visible in process listings.",
    )
    p_owner.add_argument("--clear-master-password", action="store_true")

    p_gaming = sub.add_parser("gaming-mode", help="Enable/disable low-impact mode for gaming sessions.")
    p_gaming_group = p_gaming.add_mutually_exclusive_group()
    p_gaming_group.add_argument("--enable", action="store_true")
    p_gaming_group.add_argument("--disable", action="store_true")
    p_gaming.add_argument("--auto-detect", choices=["on", "off"], default="")
    p_gaming.add_argument("--reason", default="")

    p_automation = sub.add_parser("automation-run", help="Run planned actions with capability gates.")
    p_automation.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )
    p_automation.add_argument(
        "--approve-privileged",
        action="store_true",
        help="Required to execute privileged actions.",
    )
    p_automation.add_argument(
        "--execute",
        action="store_true",
        help="Execute commands (default is dry-run).",
    )

    sub.add_parser("connect-status", help="Show connector readiness and prompts.")

    p_connect_grant = sub.add_parser("connect-grant", help="Grant connector permission.")
    p_connect_grant.add_argument("--id", required=True, help="Connector id (for example: email, calendar).")
    p_connect_grant.add_argument("--scope", action="append", default=[], help="Optional scope (repeatable).")

    p_connect_bootstrap = sub.add_parser("connect-bootstrap", help="Show connector prompts and optionally open setup links.")
    p_connect_bootstrap.add_argument("--auto-open", action="store_true", help="Open tap URLs in browser.")

    p_phone_action = sub.add_parser("phone-action", help="Queue phone action (send SMS/place call/ignore/block).")
    p_phone_action.add_argument("--action", required=True, choices=["send_sms", "place_call", "ignore_call", "block_number", "silence_unknown_callers"])
    p_phone_action.add_argument("--number", default="")
    p_phone_action.add_argument("--message", default="")
    p_phone_action.add_argument(
        "--queue-path",
        default=str(repo_root() / ".planning" / "phone_actions.jsonl"),
    )

    p_phone_spam = sub.add_parser("phone-spam-guard", help="Analyze call logs and queue spam-block actions.")
    p_phone_spam.add_argument(
        "--call-log-path",
        default=str(repo_root() / ".planning" / "phone_call_log.json"),
    )
    p_phone_spam.add_argument(
        "--report-path",
        default=str(repo_root() / ".planning" / "phone_spam_report.json"),
    )
    p_phone_spam.add_argument(
        "--queue-path",
        default=str(repo_root() / ".planning" / "phone_actions.jsonl"),
    )
    p_phone_spam.add_argument("--threshold", type=float, default=0.65)

    sub.add_parser("voice-list", help="List available local Windows voices.")

    p_voice = sub.add_parser("voice-say", help="Speak text with local Windows voice synthesis.")
    p_voice.add_argument("--text", required=True)
    p_voice.add_argument("--profile", default="jarvis_like", choices=["jarvis_like", "default"])
    p_voice.add_argument("--voice-pattern", default="")
    p_voice.add_argument("--output-wav", default="")
    p_voice.add_argument("--rate", type=int, default=-1)

    p_voice_enroll = sub.add_parser("voice-enroll", help="Enroll a user voiceprint from WAV.")
    p_voice_enroll.add_argument("--user-id", required=True, help="Identity label, e.g. conner.")
    p_voice_enroll.add_argument("--wav", required=True, help="Path to WAV sample of your voice.")
    p_voice_enroll.add_argument("--replace", action="store_true", help="Replace existing profile.")

    p_voice_verify = sub.add_parser("voice-verify", help="Verify WAV sample against enrolled voiceprint.")
    p_voice_verify.add_argument("--user-id", required=True)
    p_voice_verify.add_argument("--wav", required=True)
    p_voice_verify.add_argument("--threshold", type=float, default=0.82)

    p_voice_run = sub.add_parser("voice-run", help="Run a voice/text command through intent mapping.")
    p_voice_run.add_argument("--text", required=True)
    p_voice_run.add_argument("--execute", action="store_true")
    p_voice_run.add_argument("--approve-privileged", action="store_true")
    p_voice_run.add_argument("--speak", action="store_true", help="Speak completion status.")
    p_voice_run.add_argument("--voice-user", default="conner")
    p_voice_run.add_argument("--voice-auth-wav", default="", help="Optional WAV path for voice authentication.")
    p_voice_run.add_argument("--voice-threshold", type=float, default=0.82)
    p_voice_run.add_argument(
        "--master-password", default="",
        help="DEPRECATED: use JARVIS_MASTER_PASSWORD env var instead. "
             "CLI passwords are visible in process listings.",
    )
    p_voice_run.add_argument(
        "--snapshot-path",
        default=str(repo_root() / ".planning" / "ops_snapshot.live.json"),
    )
    p_voice_run.add_argument(
        "--actions-path",
        default=str(repo_root() / ".planning" / "actions.generated.json"),
    )
    p_voice_run.add_argument(
        "--model-override",
        default="",
        help="Optional explicit model alias to force for this command.",
    )
    p_voice_run.add_argument(
        "--skip-voice-auth-guard",
        action="store_true",
        help="Bypass voice-auth requirement guard (owner identity checks still apply).",
    )

    p_voice_listen = sub.add_parser("voice-listen", help="Record from microphone and transcribe speech-to-text.")
    p_voice_listen.add_argument("--duration", type=float, default=30.0, help="Max recording duration in seconds.")
    p_voice_listen.add_argument("--language", default="en", help="Language code hint for transcription.")
    p_voice_listen.add_argument("--execute", action="store_true", help="Execute transcribed text as a voice command.")

    # -- Harvesting --
    p_harvest = sub.add_parser("harvest", help="Harvest knowledge about a topic from external AI sources.")
    p_harvest.add_argument("--topic", required=True, help="Topic to harvest knowledge about.")
    p_harvest.add_argument("--providers", default=None, help="Comma-separated list of providers (default: all available).")
    p_harvest.add_argument("--max-tokens", type=int, default=2048, help="Max tokens per provider response.")

    p_ingest_session = sub.add_parser("ingest-session", help="Ingest knowledge from Claude Code or Codex session files.")
    p_ingest_session.add_argument("--source", required=True, choices=["claude", "codex"], help="Session source type.")
    p_ingest_session.add_argument("--session-path", default=None, help="Specific session file path (optional).")
    p_ingest_session.add_argument("--project-path", default=None, help="Claude Code project path to scope search (optional).")

    p_harvest_budget = sub.add_parser("harvest-budget", help="View or set harvest budget limits.")
    p_harvest_budget.add_argument("--action", default="status", choices=["status", "set"], help="Budget action.")
    p_harvest_budget.add_argument("--provider", default=None, help="Provider name.")
    p_harvest_budget.add_argument("--period", default=None, choices=["daily", "monthly"], help="Budget period.")
    p_harvest_budget.add_argument("--limit-usd", type=float, default=None, help="USD limit.")
    p_harvest_budget.add_argument("--limit-requests", type=int, default=None, help="Request count limit.")

    # -- Learning --
    p_learn = sub.add_parser("learn", help="Manually trigger learning from text input.")
    p_learn.add_argument("--user-message", required=True, help="User message text.")
    p_learn.add_argument("--assistant-response", required=True, help="Assistant response text.")

    p_cbq = sub.add_parser("cross-branch-query", help="Query across knowledge branches.")
    p_cbq.add_argument("query", help="Natural language query.")
    p_cbq.add_argument("--k", type=int, default=10, help="Max results to return.")

    sub.add_parser("flag-expired", help="Flag expired knowledge graph facts.")

    sub.add_parser("memory-eval", help="Run memory-recall golden task evaluation.")

    p_proactive = sub.add_parser("proactive-check", help="Manually trigger proactive evaluation.")
    p_proactive.add_argument("--snapshot-path", default="", help="Path to ops snapshot JSON.")

    p_wakeword = sub.add_parser("wake-word", help="Start wake word detection (blocking).")
    p_wakeword.add_argument("--threshold", type=float, default=0.5, help="Detection threshold.")

    p_cost_red = sub.add_parser("cost-reduction", help="Show local vs cloud query ratio and trend.")
    p_cost_red.add_argument("--days", type=int, default=30, help="Number of days to look back.")

    p_selftest = sub.add_parser("self-test", help="Run adversarial memory quiz.")
    p_selftest.add_argument("--threshold", type=float, default=0.5, help="Score threshold for alerts.")

    args = parser.parse_args()
    if args.command == "status":
        return cmd_status()
    if args.command == "log":
        return cmd_log(event_type=args.type, message=args.message)
    if args.command == "ingest":
        return cmd_ingest(
            source=args.source,
            kind=args.kind,
            task_id=args.task_id,
            content=args.content,
        )
    if args.command == "serve-mobile":
        # Resolve --tls / --no-tls into a tri-state: True, False, or None (auto)
        _tls_flag: bool | None = None
        if getattr(args, "tls", None):
            _tls_flag = True
        elif getattr(args, "no_tls", False):
            _tls_flag = False
        return cmd_serve_mobile(
            host=args.host,
            port=args.port,
            token=args.token,
            signing_key=args.signing_key,
            allow_insecure_bind=args.allow_insecure_bind,
            config_file=args.config_file,
            tls=_tls_flag,
        )
    if args.command == "route":
        return cmd_route(risk=args.risk, complexity=args.complexity)
    if args.command == "growth-eval":
        think_opt = None
        if args.think == "on":
            think_opt = True
        elif args.think == "off":
            think_opt = False
        return cmd_growth_eval(
            model=args.model,
            endpoint=args.endpoint,
            tasks_path=Path(args.tasks_path),
            history_path=Path(args.history_path),
            num_predict=args.num_predict,
            temperature=args.temperature,
            think=think_opt,
            accept_thinking=args.accept_thinking,
            timeout_s=args.timeout_s,
        )
    if args.command == "growth-report":
        return cmd_growth_report(
            history_path=Path(args.history_path),
            last=args.last,
        )
    if args.command == "growth-audit":
        return cmd_growth_audit(
            history_path=Path(args.history_path),
            run_index=args.run_index,
        )
    if args.command == "intelligence-dashboard":
        return cmd_intelligence_dashboard(
            last_runs=args.last_runs,
            output_path=args.output_path,
            as_json=args.json,
        )
    if args.command == "brain-status":
        return cmd_brain_status(as_json=args.json)
    if args.command == "brain-context":
        return cmd_brain_context(
            query=args.query,
            max_items=args.max_items,
            max_chars=args.max_chars,
            as_json=args.json,
        )
    if args.command == "brain-compact":
        return cmd_brain_compact(
            keep_recent=args.keep_recent,
            as_json=args.json,
        )
    if args.command == "brain-regression":
        return cmd_brain_regression(as_json=args.json)
    if args.command == "knowledge-status":
        return cmd_knowledge_status(as_json=args.json)
    if args.command == "contradiction-list":
        return cmd_contradiction_list(
            status=args.status,
            limit=args.limit,
            as_json=args.json,
        )
    if args.command == "contradiction-resolve":
        return cmd_contradiction_resolve(
            contradiction_id=args.contradiction_id,
            resolution=args.resolution,
            merge_value=args.merge_value,
        )
    if args.command == "fact-lock":
        return cmd_fact_lock(
            node_id=args.node_id,
            action=args.action,
        )
    if args.command == "knowledge-regression":
        return cmd_knowledge_regression(
            snapshot_path=args.snapshot,
            as_json=args.json,
        )
    if args.command == "memory-snapshot":
        return cmd_memory_snapshot(
            create=args.create,
            verify_path=args.verify_path,
            note=args.note,
        )
    if args.command == "memory-maintenance":
        return cmd_memory_maintenance(
            keep_recent=args.keep_recent,
            snapshot_note=args.snapshot_note,
        )
    if args.command == "web-research":
        return cmd_web_research(
            query=args.query,
            max_results=args.max_results,
            max_pages=args.max_pages,
            auto_ingest=not args.no_ingest,
        )
    if args.command == "mobile-desktop-sync":
        return cmd_mobile_desktop_sync(
            auto_ingest=not args.no_ingest,
            as_json=args.json,
        )
    if args.command == "self-heal":
        return cmd_self_heal(
            force_maintenance=args.force_maintenance,
            keep_recent=args.keep_recent,
            snapshot_note=args.snapshot_note,
            as_json=args.json,
        )
    if args.command == "persona-config":
        return cmd_persona_config(
            enable=args.enable,
            disable=args.disable,
            humor_level=args.humor_level,
            mode=args.mode,
            style=args.style,
        )
    if args.command == "migrate-memory":
        return cmd_migrate_memory()
    if args.command == "desktop-widget":
        return cmd_desktop_widget()
    if args.command == "run-task":
        return cmd_run_task(
            task_type=args.type,
            prompt=args.prompt,
            execute=args.execute,
            approve_privileged=args.approve_privileged,
            model=args.model,
            endpoint=args.endpoint,
            quality_profile=args.quality_profile,
            output_path=args.output_path,
        )
    if args.command == "ops-brief":
        out_path = Path(args.output_path) if args.output_path else None
        return cmd_ops_brief(
            snapshot_path=Path(args.snapshot_path),
            output_path=out_path,
        )
    if args.command == "ops-export-actions":
        return cmd_ops_export_actions(
            snapshot_path=Path(args.snapshot_path),
            actions_path=Path(args.actions_path),
        )
    if args.command == "ops-sync":
        return cmd_ops_sync(
            output_path=Path(args.output_path),
        )
    if args.command == "ops-autopilot":
        return cmd_ops_autopilot(
            snapshot_path=Path(args.snapshot_path),
            actions_path=Path(args.actions_path),
            execute=args.execute,
            approve_privileged=args.approve_privileged,
            auto_open_connectors=args.auto_open_connectors,
        )
    if args.command == "daemon-run":
        return cmd_daemon_run(
            interval_s=args.interval_s,
            snapshot_path=Path(args.snapshot_path),
            actions_path=Path(args.actions_path),
            execute=args.execute,
            approve_privileged=args.approve_privileged,
            auto_open_connectors=args.auto_open_connectors,
            max_cycles=args.max_cycles,
            idle_interval_s=args.idle_interval_s,
            idle_after_s=args.idle_after_s,
            run_missions=not args.skip_missions,
            sync_every_cycles=args.sync_every_cycles,
            self_heal_every_cycles=args.self_heal_every_cycles,
            self_test_every_cycles=args.self_test_every_cycles,
        )
    if args.command == "mission-create":
        return cmd_mission_create(
            topic=args.topic,
            objective=args.objective,
            sources=list(args.source),
        )
    if args.command == "mission-status":
        return cmd_mission_status(last=args.last)
    if args.command == "mission-run":
        return cmd_mission_run(
            mission_id=args.id,
            max_results=args.max_results,
            max_pages=args.max_pages,
            auto_ingest=not args.no_ingest,
        )
    if args.command == "mission-cancel":
        return cmd_mission_cancel(mission_id=args.id)
    if args.command == "consolidate":
        return cmd_consolidate(
            branch=args.branch, max_groups=args.max_groups, dry_run=args.dry_run,
        )
    if args.command == "runtime-control":
        return cmd_runtime_control(
            pause=args.pause,
            resume=args.resume,
            safe_on=args.safe_on,
            safe_off=args.safe_off,
            reset=args.reset,
            reason=args.reason,
        )
    if args.command == "owner-guard":
        return cmd_owner_guard(
            enable=args.enable,
            disable=args.disable,
            owner_user=args.owner_user,
            trust_device=args.trust_device,
            revoke_device=args.revoke_device,
            set_master_password_value=os.getenv("JARVIS_MASTER_PASSWORD", "").strip() or args.set_master_password,
            clear_master_password_value=args.clear_master_password,
        )
    if args.command == "gaming-mode":
        enable_opt: bool | None = None
        if args.enable:
            enable_opt = True
        elif args.disable:
            enable_opt = False
        return cmd_gaming_mode(enable=enable_opt, reason=args.reason, auto_detect=args.auto_detect)
    if args.command == "automation-run":
        return cmd_automation_run(
            actions_path=Path(args.actions_path),
            approve_privileged=args.approve_privileged,
            execute=args.execute,
        )
    if args.command == "connect-status":
        return cmd_connect_status()
    if args.command == "connect-grant":
        return cmd_connect_grant(
            connector_id=args.id,
            scopes=list(args.scope),
        )
    if args.command == "connect-bootstrap":
        return cmd_connect_bootstrap(auto_open=args.auto_open)
    if args.command == "phone-action":
        return cmd_phone_action(
            action=args.action,
            number=args.number,
            message=args.message,
            queue_path=Path(args.queue_path),
        )
    if args.command == "phone-spam-guard":
        return cmd_phone_spam_guard(
            call_log_path=Path(args.call_log_path),
            report_path=Path(args.report_path),
            queue_path=Path(args.queue_path),
            threshold=args.threshold,
        )
    if args.command == "voice-list":
        return cmd_voice_list()
    if args.command == "voice-say":
        return cmd_voice_say(
            text=args.text,
            profile=args.profile,
            voice_pattern=args.voice_pattern,
            output_wav=args.output_wav,
            rate=args.rate,
        )
    if args.command == "voice-enroll":
        return cmd_voice_enroll(
            user_id=args.user_id,
            wav_path=args.wav,
            replace=args.replace,
        )
    if args.command == "voice-verify":
        return cmd_voice_verify(
            user_id=args.user_id,
            wav_path=args.wav,
            threshold=args.threshold,
        )
    if args.command == "voice-run":
        return cmd_voice_run(
            text=args.text,
            execute=args.execute,
            approve_privileged=args.approve_privileged,
            speak=args.speak,
            snapshot_path=Path(args.snapshot_path),
            actions_path=Path(args.actions_path),
            voice_user=args.voice_user,
            voice_auth_wav=args.voice_auth_wav,
            voice_threshold=args.voice_threshold,
            master_password=os.getenv("JARVIS_MASTER_PASSWORD", "").strip() or args.master_password,
            model_override=args.model_override,
            skip_voice_auth_guard=args.skip_voice_auth_guard,
        )
    if args.command == "voice-listen":
        return cmd_voice_listen(
            duration=args.duration,
            language=args.language,
            execute=args.execute,
        )
    if args.command == "harvest":
        return cmd_harvest(
            topic=args.topic,
            providers=args.providers,
            max_tokens=args.max_tokens,
        )
    if args.command == "ingest-session":
        return cmd_ingest_session(
            source=args.source,
            session_path=args.session_path,
            project_path=args.project_path,
        )
    if args.command == "harvest-budget":
        return cmd_harvest_budget(
            action=args.action,
            provider=args.provider,
            period=args.period,
            limit_usd=args.limit_usd,
            limit_requests=args.limit_requests,
        )
    if args.command == "learn":
        return cmd_learn(
            user_message=args.user_message,
            assistant_response=args.assistant_response,
        )
    if args.command == "cross-branch-query":
        return cmd_cross_branch_query(
            query=args.query,
            k=args.k,
        )
    if args.command == "flag-expired":
        return cmd_flag_expired()
    if args.command == "memory-eval":
        return cmd_memory_eval()
    if args.command == "proactive-check":
        return cmd_proactive_check(snapshot_path=args.snapshot_path)
    if args.command == "wake-word":
        return cmd_wake_word(threshold=args.threshold)
    if args.command == "cost-reduction":
        return cmd_cost_reduction(days=args.days)
    if args.command == "self-test":
        return cmd_self_test(threshold=args.threshold)
    print(f"error: unhandled command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
