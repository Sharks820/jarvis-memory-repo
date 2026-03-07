"""Voice command pipeline — extracted from main.py for better separation of concerns."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from jarvis_engine._bus import get_bus
from jarvis_engine._shared import env_int as _env_int
from jarvis_engine.auto_ingest import auto_ingest_memory as _auto_ingest_memory
from jarvis_engine.brain_memory import build_context_packet
from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.learning_commands import LearnInteractionCommand
from jarvis_engine.commands.task_commands import QueryCommand, QueryResult
from jarvis_engine.config import repo_root
from jarvis_engine.owner_guard import read_owner_guard, verify_master_password
from jarvis_engine.persona import compose_persona_reply, load_persona_config

from jarvis_engine._constants import (
    ENV_MODEL_PRIORITY as _ENV_MODEL_PRIORITY,
    STOP_WORDS as _HARVEST_STOP_WORDS,
    OPS_SNAPSHOT_FILENAME as _OPS_SNAPSHOT_FILENAME,
    ACTIONS_FILENAME as _ACTIONS_FILENAME,
    get_local_model as _get_local_model,
    is_privacy_sensitive as _is_privacy_sensitive,
    make_task_id as _make_task_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text cleaning helpers
# ---------------------------------------------------------------------------

PHONE_NUMBER_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{7,}\d)")
URL_RE = re.compile(r"\b((?:https?://|www\.)[^\s<>{}\[\]\"']+)", flags=re.IGNORECASE)


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
# Conversation history buffer for multi-turn context (persisted to disk)
# ---------------------------------------------------------------------------

_CONVERSATION_MAX_TURNS = _env_int("JARVIS_CONVERSATION_MAX_TURNS", 12, minimum=4, maximum=40)
_CONVERSATION_MAX_CHARS_PER_MESSAGE = _env_int(
    "JARVIS_CONVERSATION_MAX_CHARS",
    2000,
    minimum=400,
    maximum=8000,
)


class ConversationState:
    """Encapsulates all conversation-related mutable state with thread-safe access.

    Replaces the former module-level globals (_conversation_history,
    _conversation_history_lock, _conversation_history_loaded, _last_routed_model,
    _last_routed_model_lock, _CONVERSATION_HISTORY_FILE) with a single object
    that owns its own locks and data.

    Parameters
    ----------
    history_file : Path | None
        Optional override for the conversation history JSON file path.
        When *None* (the default), the standard
        ``<repo>/.planning/brain/conversation_history.json`` is used.
    """

    _SAVE_DEBOUNCE_SECONDS = 5.0

    def __init__(self, history_file: "Path | None" = None) -> None:
        self._conversation_history: list[dict[str, str]] = []
        self._conversation_history_lock = threading.RLock()
        self._history_file: Path | None = history_file
        self._conversation_history_loaded = False
        self._last_routed_model: str | None = None
        self._last_routed_model_lock = threading.Lock()
        self._last_save_time: float = 0.0
        self._dirty: bool = False

    # -- history file path ---------------------------------------------------

    def _conversation_history_path(self) -> Path:
        """Return the path for persisted conversation history.

        This is a pure getter — no directory creation side effects.
        Directories are created only in ``save_conversation_history()``.
        """
        if self._history_file is None:
            self._history_file = repo_root() / ".planning" / "brain" / "conversation_history.json"
        return self._history_file

    # -- load / save ---------------------------------------------------------

    def load_conversation_history(self) -> None:
        """Load persisted conversation history from disk.

        Acquires the conversation history lock (reentrant) so callers
        do not need to hold it beforehand.  Safe to call from within
        ``get_history_messages`` which already holds the same lock.
        """
        with self._conversation_history_lock:
            try:
                path = self._conversation_history_path()
                if path.exists():
                    import json as _json
                    data = _json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        self._conversation_history.clear()
                        self._conversation_history.extend(data[-((_CONVERSATION_MAX_TURNS * 2)):])
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.debug("Could not load conversation history: %s", exc)

    def save_conversation_history(self, *, force: bool = False) -> None:
        """Persist current conversation history to disk (atomic write).

        When *force* is False (default), the write is debounced: it only
        happens if at least ``_SAVE_DEBOUNCE_SECONDS`` have elapsed since
        the last successful write **and** the history is dirty.  Pass
        ``force=True`` to bypass the debounce (used by the atexit handler
        and explicit ``save_conversation_history()`` module-level calls).
        """
        import time as _time

        now = _time.monotonic()
        if not force:
            if not self._dirty:
                return
            if (now - self._last_save_time) < self._SAVE_DEBOUNCE_SECONDS:
                return

        try:
            import json as _json
            path = self._conversation_history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._conversation_history_lock:
                snapshot = list(self._conversation_history)
                tmp = path.with_suffix(f".tmp.{os.getpid()}")
                tmp.write_text(_json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
                os.replace(str(tmp), str(path))
            self._last_save_time = now
            self._dirty = False
        except OSError as exc:
            logger.debug("Could not save conversation history: %s", exc)

    # -- add / get history ---------------------------------------------------

    def add_to_history(self, role: str, content: str) -> None:
        """Append a message to the conversation history, capping at max turns."""
        with self._conversation_history_lock:
            self._conversation_history.append(
                {"role": role, "content": content[:_CONVERSATION_MAX_CHARS_PER_MESSAGE]}
            )
            # Keep only the last N user/assistant pairs
            while len(self._conversation_history) > _CONVERSATION_MAX_TURNS * 2:
                self._conversation_history.pop(0)
            self._dirty = True
        self.save_conversation_history()  # respects debounce unless forced

    def get_history_messages(self) -> list[dict[str, str]]:
        """Return conversation history as message list for LLM context."""
        with self._conversation_history_lock:
            if not self._conversation_history_loaded:
                self.load_conversation_history()
                self._conversation_history_loaded = True
            return list(self._conversation_history)

    def clear_history(self) -> None:
        """Clear conversation history (thread-safe)."""
        with self._conversation_history_lock:
            self._conversation_history.clear()

    # -- routed model tracking -----------------------------------------------

    def mark_routed_model(self, model: str, provider: str) -> None:
        """Persist last routed model and log provider-switch continuity telemetry."""
        normalized_model = model.strip()
        if not normalized_model:
            return
        with self._last_routed_model_lock:
            previous = self._last_routed_model
            self._last_routed_model = normalized_model

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
            except (ImportError, OSError, ValueError) as exc:
                logger.debug("Model continuity telemetry logging failed: %s", exc)

    def conversation_continuity_instruction(self, target_model: str, history_len: int) -> str | None:
        """Return continuity instruction when conversation switches models/providers."""
        if history_len <= 0:
            return None
        normalized_target = target_model.strip()
        if not normalized_target:
            return None
        with self._last_routed_model_lock:
            previous = (self._last_routed_model or "").strip()
        if not previous or previous == normalized_target:
            return None
        return (
            f"Continuity contract: previous turn used model '{previous}' and this turn uses '{normalized_target}'. "
            "Do not reset or restart context. Continue the same conversation using provided history, memory, and unresolved goals."
        )


# Module-level singleton
_state = ConversationState()

# Flush dirty conversation history on interpreter shutdown
import atexit as _atexit


def _flush_history_atexit() -> None:
    """Flush any unsaved conversation history on process exit."""
    try:
        _state.save_conversation_history(force=True)
    except Exception:  # noqa: BLE001 — best-effort at shutdown
        pass


_atexit.register(_flush_history_atexit)

# ---------------------------------------------------------------------------
# Backward-compatible module-level aliases for external consumers
# (mobile_api.py, tests, etc.)
#
# Reference-type aliases (list, Lock) share the same object with _state so
# mutations are visible everywhere.  Scalar aliases (_conversation_history_loaded,
# _last_routed_model) are proxied via a custom module class so that both reads
# AND writes (e.g. monkeypatch.setattr in tests, or direct assignment in
# mobile_api.py) are forwarded to the _state singleton.
# ---------------------------------------------------------------------------
_conversation_history_lock = _state._conversation_history_lock
_last_routed_model_lock = _state._last_routed_model_lock


class _ConversationHistoryProxy:
    """Proxy that routes attribute access through the _state lock.

    External code previously held a direct reference to the internal list,
    which allowed lock-free mutation.  This proxy forces all operations
    through thread-safe ConversationState methods while keeping the same
    API surface (``len()``, ``clear()``, iteration, subscript).
    """

    def clear(self) -> None:  # noqa: D401
        _state.clear_history()

    def append(self, item: dict) -> None:
        raise RuntimeError("Use _add_to_history() instead of direct append")

    def __len__(self) -> int:
        with _state._conversation_history_lock:
            return len(_state._conversation_history)

    def __iter__(self):
        return iter(_state.get_history_messages())

    def __getitem__(self, idx):
        return _state.get_history_messages()[idx]

    def __bool__(self) -> bool:
        with _state._conversation_history_lock:
            return bool(_state._conversation_history)


_conversation_history = _ConversationHistoryProxy()

import sys as _sys
import types as _types


class _VoicePipelineModule(_types.ModuleType):
    """Custom module class to proxy scalar attributes to _state."""

    @property
    def _conversation_history_loaded(self) -> bool:
        return _state._conversation_history_loaded

    @_conversation_history_loaded.setter
    def _conversation_history_loaded(self, value: bool) -> None:
        _state._conversation_history_loaded = value

    @property
    def _last_routed_model(self) -> "str | None":
        return _state._last_routed_model

    @_last_routed_model.setter
    def _last_routed_model(self, value: "str | None") -> None:
        _state._last_routed_model = value


_sys.modules[__name__].__class__ = _VoicePipelineModule


def _conversation_history_path() -> Path:
    """Return the path for persisted conversation history."""
    return _state._conversation_history_path()


def save_conversation_history() -> None:
    """Persist current conversation history to disk (atomic write).

    This is an explicit save request, so it always forces a write
    regardless of debounce timing.
    """
    _state.save_conversation_history(force=True)


def _add_to_history(role: str, content: str) -> None:
    """Append a message to the conversation history, capping at max turns."""
    _state.add_to_history(role, content)


def _get_history_messages() -> list[dict[str, str]]:
    """Return conversation history as message list for LLM context."""
    return _state.get_history_messages()


def _learn_conversation(
    bus: "CommandBus",
    text: str,
    response: str,
    route: str,
    model: str,
) -> None:
    """Dispatch a LearnInteractionCommand with JSONL fallback on failure."""
    try:
        bus.dispatch(LearnInteractionCommand(
            user_message=text[:1000],
            assistant_response=response[:1000],
            task_id=_make_task_id(f"conv-{route}"),
            route=route,
            topic=text[:100],
        ))
    except (OSError, RuntimeError, ValueError) as exc_learn:
        logger.warning("Enriched learning failed for conversation: %s", exc_learn)
        try:
            _auto_ingest_memory(
                source="conversation",
                kind="episodic",
                task_id=_make_task_id(f"conv-{route}"),
                content=(
                    f"User asked: {text[:400]}\n"
                    f"Jarvis responded ({model}): {response[:600]}"
                ),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Auto-ingest fallback also failed: %s", exc)


def _conversation_continuity_instruction(target_model: str, history_len: int) -> str | None:
    """Return continuity instruction when conversation switches models/providers."""
    return _state.conversation_continuity_instruction(target_model, history_len)


def _mark_routed_model(model: str, provider: str) -> None:
    """Persist last routed model and log provider-switch continuity telemetry."""
    _state.mark_routed_model(model, provider)


# ---------------------------------------------------------------------------
# LLM token budget and web search detection
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
    from jarvis_engine.temporal import get_datetime_prompt

    return get_datetime_prompt()


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

    engine = bus.ctx.engine
    embed_service = bus.ctx.embed_service

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
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
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
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Legacy context packet fallback failed: %s", exc)

    # --- KG facts: personal knowledge about the user ---
    kg = None  # Retain reference for cross-branch query below
    if engine is not None:
        try:
            kg = bus.ctx.kg
            if kg is None:
                from jarvis_engine.knowledge.graph import KnowledgeGraph
                kg = KnowledgeGraph(engine)
            # Extract keywords from query for fact lookup
            words = [
                w for w in re.findall(r"[a-zA-Z]{3,}", query.lower())
                if w not in _HARVEST_STOP_WORDS
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
                    except (ImportError, OSError, RuntimeError, ValueError, KeyError) as sem_exc:
                        logger.debug("KG semantic fact query failed: %s", sem_exc)
        except (ImportError, OSError, RuntimeError, KeyError) as exc:
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
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.debug("Cross-branch query failed: %s", exc)

    # --- User preferences: personalize responses (LEARN-01) ---
    preference_lines: list[str] = []
    pref_tracker = bus.ctx.pref_tracker
    if pref_tracker is not None:
        try:
            prefs = pref_tracker.get_preferences()
            if prefs:
                pref_str = ", ".join(f"{k}: {v}" for k, v in prefs.items())
                preference_lines.append(pref_str)
        except (OSError, RuntimeError, KeyError) as exc:
            logger.debug("Preference retrieval failed: %s", exc)

    return memory_lines, fact_lines, cross_branch_lines, preference_lines


def _build_system_parts(
    memory_lines: list[str],
    fact_lines: list[str],
    cross_branch_lines: list[str],
    preference_lines: list[str],
) -> list[str]:
    """Assemble the system prompt parts for an LLM conversation.

    Called from ``_web_augmented_llm_conversation`` to assemble
    the LLM system prompt.
    """
    from jarvis_engine.persona import get_persona_prompt
    persona = load_persona_config(repo_root())
    parts = [_current_datetime_prompt_line(), get_persona_prompt(persona)]
    if fact_lines:
        parts.append(
            "Known facts about the user (use these to personalize your response):\n"
            + "\n".join(f"- {line}" for line in fact_lines[:6])
        )
    if memory_lines:
        parts.append(
            "Relevant memories (recent interactions and context):\n"
            + "\n".join(f"- {line}" for line in memory_lines[:8])
        )
    if cross_branch_lines:
        parts.append(
            "Cross-domain connections:\n"
            + "\n".join(f"- {line}" for line in cross_branch_lines[:6])
        )
    if preference_lines:
        parts.append(
            "User preferences (adjust your response style accordingly): "
            + "; ".join(preference_lines)
        )
    return parts


def _classify_and_route(
    bus: Any,
    text: str,
    *,
    default_route: str = "routine",
    try_fallback_classifier: bool = False,
) -> tuple[str, str]:
    """Classify intent and select the target LLM model.

    Used by ``_web_augmented_llm_conversation`` for intent classification
    and LLM model selection.

    Returns ``(route, llm_model)``.
    """
    llm_model: str | None = None
    route: str = default_route
    intent_cls = bus.ctx.intent_classifier
    avail_models = None
    gw = bus.ctx.gateway
    if gw is not None:
        avail_models = getattr(gw, "available_model_names", lambda: None)()
    if intent_cls is not None:
        try:
            route, llm_model, conf = intent_cls.classify(text, available_models=avail_models)
            logger.debug("Intent route: %s model=%s confidence=%.2f", route, llm_model, conf)
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.debug("Intent classification failed: %s", exc)
            llm_model = None
    if llm_model is None and try_fallback_classifier:
        try:
            from jarvis_engine.gateway.classifier import IntentClassifier

            embed = bus.ctx.embed_service
            if embed is not None:
                cls = IntentClassifier(embed)
                route, llm_model, conf = cls.classify(text, available_models=avail_models)
                logger.debug("Fallback route: %s model=%s confidence=%.2f", route, llm_model, conf)
        except (ImportError, RuntimeError, ValueError, TypeError) as exc:
            logger.debug("Fallback IntentClassifier classification failed: %s", exc)
    if llm_model is None:
        if _is_privacy_sensitive(text):
            llm_model = _get_local_model()
            route = "simple_private"
            logger.debug("Privacy fallback: classifier failed, forcing local for private query")
        else:
            for env_key, model_alias in _ENV_MODEL_PRIORITY:
                if os.environ.get(env_key, ""):
                    llm_model = model_alias
                    break
    if llm_model is None:
        llm_model = _get_local_model()
    return route, llm_model


# ---------------------------------------------------------------------------
# Extraction helpers for voice commands
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


# ---------------------------------------------------------------------------
# Web-augmented LLM conversation
# ---------------------------------------------------------------------------

def _web_augmented_llm_conversation(
    text: str,
    *,
    speak: bool = False,
    force_web_search: bool = False,
    model_override: str = "",
    default_route: str = "web_research",
    try_fallback_classifier: bool = False,
    response_callback: "Callable[[str], None] | None" = None,
) -> int:
    """Run a web-search-augmented LLM conversation for a single query.

    This is the shared implementation used by:
    - Explicit "search the web for X" voice commands (force_web_search=True)
    - Weather fallback when the dedicated handler fails (force_web_search=True)
    - The general LLM conversation fallback in cmd_voice_run_impl

    Parameters
    ----------
    text : str
        The user's query text.
    speak : bool
        Whether to speak the response aloud via TTS.
    force_web_search : bool
        When True, always attempt web search regardless of route/query signals.
        When False, only search when the classified route is "web_research" or
        ``_needs_web_search(text)`` returns True.
    model_override : str
        If non-empty, override the classified model with this value (used by
        widget Tab-cycling).
    default_route : str
        Default intent route when classification fails.
    try_fallback_classifier : bool
        Whether to attempt a fallback IntentClassifier when the bus classifier
        is unavailable.
    response_callback : Callable[[str], None] | None
        Optional callback invoked with the response text.  Used by
        ``cmd_voice_run_impl`` to capture ``_last_response`` for the
        learning pipeline.

    Returns 0 on success, 1 on failure.
    """
    from jarvis_engine.main import cmd_voice_say

    bus = get_bus()

    # --- Smart context + system prompt assembly ---
    memory_lines, fact_lines, cross_branch_lines, preference_lines = _build_smart_context(bus, text)
    system_parts = _build_system_parts(memory_lines, fact_lines, cross_branch_lines, preference_lines)

    # --- Intent classification + model routing ---
    _route, _llm_model = _classify_and_route(
        bus, text, default_route=default_route, try_fallback_classifier=try_fallback_classifier,
    )

    # --- Model override from widget Tab-cycling ---
    if model_override:
        _llm_model = model_override
        logger.debug("Model overridden by user selection: %s", model_override)

    # --- Web search augmentation ---
    _web_searched = False
    _web_attempted = False
    _web_result: dict[str, object] = {}

    _should_search = force_web_search or _route == "web_research" or _needs_web_search(text)
    if _should_search:
        _web_attempted = True
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
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("Web search failed for query %r: %s", text[:80], exc)

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

    # --- Dynamic max_tokens ---
    _max_tokens = _MAX_TOKENS_BY_ROUTE.get(_route, 512)
    if _web_searched or force_web_search:
        _max_tokens = max(_max_tokens, 768)

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
        _response = result.text.strip()
        if result.return_code != 0:
            if _web_searched:
                fallback_lines = _web_result.get("summary_lines", []) if isinstance(_web_result, dict) else []
                if isinstance(fallback_lines, list) and fallback_lines:
                    fallback_text = "Based on live web results: " + " ".join(str(x) for x in fallback_lines[:3])
                    print(f"response={escape_response(fallback_text)}")
                    if response_callback is not None:
                        response_callback(fallback_text)
                    print("model=web-research-fallback")
                    print("provider=web")
                    print("web_search_used=true")
                    return 0
            print("intent=llm_unavailable")
            print(f"reason={_response or 'LLM gateway not available.'}")
            return 1
        elif _response:
            _add_to_history("assistant", _response)
            print(f"response={escape_response(_response)}")
            if response_callback is not None:
                response_callback(_response)
            print(f"model={result.model}")
            print(f"provider={result.provider}")
            _mark_routed_model(result.model, result.provider)
            if _web_searched:
                print("web_search_used=true")
            _learn_conversation(bus, text, _response, _route, result.model)
            if speak:
                cmd_voice_say(text=_response)
            return 0
        else:
            print("intent=llm_empty_response")
            print("reason=LLM returned empty response.")
            return 1
    except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
        print("intent=llm_error")
        print(f"reason={exc}")
        if speak:
            cmd_voice_say(
                text="I'm having trouble connecting to my language model. Please try again.",
            )
        return 1


# ---------------------------------------------------------------------------
# Main voice-run implementation
# ---------------------------------------------------------------------------

def cmd_voice_run_impl(
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
    from jarvis_engine.main import (
        cmd_voice_say, cmd_voice_verify,
        cmd_connect_bootstrap, cmd_runtime_control,
        cmd_gaming_mode, cmd_weather, cmd_open_web,
        cmd_mobile_desktop_sync, cmd_self_heal,
        cmd_ops_autopilot, cmd_phone_spam_guard,
        cmd_phone_action, cmd_ops_sync, cmd_ops_brief,
        cmd_automation_run, cmd_run_task, cmd_brain_context,
        cmd_ingest, cmd_brain_status, cmd_mission_cancel,
        cmd_mission_status, cmd_status,
    )

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
        print(f"response={escape_response(msg)}")

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
        live_snapshot = snapshot_path.with_name(_OPS_SNAPSHOT_FILENAME)
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
            bus = get_bus()
            kg = bus.ctx.kg
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
            task_id=_make_task_id("voice-remember"),
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
        rc = _web_augmented_llm_conversation(
            text,
            speak=speak,
            force_web_search=False,
            model_override=model_override,
            default_route="routine",
            try_fallback_classifier=True,
            response_callback=_respond,
        )

    print(f"intent={intent}")
    print(f"status_code={rc}")
    if rc == 0:
        try:
            auto_id = _auto_ingest_memory(
                source="user",
                kind="episodic",
                task_id=_make_task_id(f"voice-{intent}"),
                content=(
                    f"Voice command accepted. intent={intent}; status_code={rc}; execute={execute}; "
                    f"approve_privileged={approve_privileged}; voice_user={voice_user}; text={text[:500]}"
                ),
            )
            if auto_id:
                print(f"auto_ingest_record_id={auto_id}")
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Auto-ingest of voice command memory failed: %s", exc)
        # Enriched learning for ALL successful commands (not just LLM path).
        # Runs in a daemon thread to avoid blocking the HTTP response — the
        # enriched pipeline may lazy-load embedding models on first call.
        if intent != "llm_conversation":
            learn_response = _last_response or f"[{intent}] Command executed successfully."
            # Capture bus reference on current thread (where repo_root override is active)
            try:
                _learn_bus = get_bus()
            except (OSError, RuntimeError) as exc:
                logger.debug("get_bus() failed for background learning: %s", exc)
                _learn_bus = None
            if _learn_bus is not None:
                _learn_cmd = LearnInteractionCommand(
                    user_message=text[:1000],
                    assistant_response=learn_response[:1000],
                    task_id=_make_task_id(f"learn-{intent}"),
                    route=intent,
                    topic=text[:100],
                )

                def _bg_learn(_bus: "CommandBus" = _learn_bus, _cmd: "LearnInteractionCommand" = _learn_cmd) -> None:
                    try:
                        _bus.dispatch(_cmd)
                    except (OSError, RuntimeError, ValueError) as exc:
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
        )
    return rc
