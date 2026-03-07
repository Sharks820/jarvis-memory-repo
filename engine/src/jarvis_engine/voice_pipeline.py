"""Voice command pipeline — extracted from main.py for better separation of concerns.

Sub-modules (split for separation of concerns):
- voice_extractors: phone/URL/weather extraction, text cleaning
- voice_context: smart context building and system prompt assembly
- voice_intents: intent routing dispatch (the large if/elif chain)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from jarvis_engine._bus import get_bus
from jarvis_engine._shared import env_int as _env_int
from jarvis_engine.auto_ingest import auto_ingest_memory as _auto_ingest_memory
from jarvis_engine.brain_memory import build_context_packet  # noqa: F401 — tests monkeypatch this
from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.learning_commands import LearnInteractionCommand
from jarvis_engine.commands.task_commands import QueryCommand, QueryResult
from jarvis_engine.config import repo_root

from jarvis_engine._constants import (
    ENV_MODEL_PRIORITY as _ENV_MODEL_PRIORITY,
    get_local_model as _get_local_model,
    is_privacy_sensitive as _is_privacy_sensitive,
    make_task_id as _make_task_id,
)

# ---------------------------------------------------------------------------
# Re-exports from sub-modules — keeps backward compatibility for tests,
# handlers, mobile_api, and main.py that import from voice_pipeline.
# ---------------------------------------------------------------------------
from jarvis_engine.voice_extractors import (  # noqa: F401 — re-exports
    PHONE_NUMBER_RE,
    URL_RE,
    shorten_urls_for_speech,
    escape_response,
    _extract_first_phone_number,
    _extract_weather_location,
    _extract_web_query,
    _extract_first_url,
    _is_read_only_voice_request,
)

from jarvis_engine.voice_context import (  # noqa: F401 — re-exports
    _current_datetime_prompt_line,
    _build_smart_context,
    _build_system_parts,
)

from jarvis_engine.voice_intents import (  # noqa: F401 — re-exports
    cmd_voice_run_impl,
)

logger = logging.getLogger(__name__)


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

# Cached fallback IntentClassifier (lazy singleton — avoids recreating on every call)
_fallback_classifier_lock = threading.Lock()
_fallback_classifier: Any = None
_fallback_classifier_embed_id: int | None = None

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
                global _fallback_classifier, _fallback_classifier_embed_id  # noqa: PLW0603
                with _fallback_classifier_lock:
                    if _fallback_classifier is None or _fallback_classifier_embed_id != id(embed):
                        _fallback_classifier = IntentClassifier(embed)
                        _fallback_classifier_embed_id = id(embed)
                    cls = _fallback_classifier
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
