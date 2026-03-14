"""Voice intent routing — dispatch table for voice commands.

Split from voice_pipeline.py for separation of concerns.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jarvis_engine._bus import get_bus
from jarvis_engine.commands.learning_commands import LearnInteractionCommand
from jarvis_engine.security.owner_guard import read_owner_guard, verify_master_password
from jarvis_engine.memory.persona import compose_persona_reply, load_persona_config

from jarvis_engine._constants import OPS_SNAPSHOT_FILENAME
from jarvis_engine._shared import make_task_id

from jarvis_engine.memory.auto_ingest import auto_ingest_memory as _auto_ingest_memory

from jarvis_engine.voice.extractors import (
    escape_response,
    _extract_first_phone_number,
    _extract_weather_location,
    _extract_first_url,
    _is_read_only_voice_request,
)
from jarvis_engine.stt.contracts import VoiceUtterance

logger = logging.getLogger(__name__)


_CommandFn = Callable[..., int]
_RespondFn = Callable[[str], None]
_RepoRootFn = Callable[[], Path]
_RequireVoiceAuthFn = Callable[[], bool]
_WebConversationFn = Callable[..., int]


def _noop_command(**_: object) -> int:
    return 0


def _noop_repo_root() -> Path:
    return Path(".")


# Parameter bundle dataclasses

@dataclass
class VoiceAuthContext:
    """Bundle of parameters for voice authentication checks."""

    voice_user: str = ""
    voice_auth_wav: str = ""
    voice_threshold: float = 0.82
    master_password: str = ""
    master_password_ok: bool = False
    execute: bool = False
    approve_privileged: bool = False
    read_only_request: bool = False
    skip_voice_auth_guard: bool = False
    speak: bool = True
    cmd_voice_say: _CommandFn = _noop_command
    cmd_voice_verify: _CommandFn = _noop_command
    repo_root: _RepoRootFn = _noop_repo_root


@dataclass
class VoiceRunParams:
    """Bundle of common voice-run parameters shared across helpers."""

    text: str = ""
    utterance: VoiceUtterance | None = None
    execute: bool = False
    approve_privileged: bool = False
    speak: bool = True
    snapshot_path: Path = Path(".")
    actions_path: Path = Path(".")
    voice_user: str = ""
    voice_auth_wav: str = ""
    voice_threshold: float = 0.82
    master_password: str = ""
    model_override: str = ""
    skip_voice_auth_guard: bool = False


# Dispatch context — passed to each intent handler

class _DispatchCtx:
    """Holds all context needed by individual intent handlers."""

    __slots__ = (
        "text", "lowered", "execute", "approve_privileged", "speak",
        "snapshot_path", "actions_path", "voice_user", "voice_auth_wav",
        "voice_threshold", "master_password", "model_override",
        "skip_voice_auth_guard", "master_password_ok", "phone_queue",
        "phone_report", "phone_call_log", "repo_root_fn",
        "_respond", "_require_state_mutation_voice_auth",
        "_web_augmented_llm_conversation",
        # Lazy-imported command functions
        "cmd_voice_say", "cmd_voice_verify",
        "cmd_connect_bootstrap", "cmd_runtime_control",
        "cmd_gaming_mode", "cmd_weather", "cmd_open_web",
        "cmd_mobile_desktop_sync", "cmd_self_heal",
        "cmd_ops_autopilot", "cmd_phone_spam_guard",
        "cmd_phone_action", "cmd_ops_sync", "cmd_ops_brief",
        "cmd_automation_run", "cmd_run_task", "cmd_brain_context",
        "cmd_ingest", "cmd_brain_status", "cmd_mission_cancel",
        "cmd_mission_status", "cmd_status",
    )

    text: str
    lowered: str
    execute: bool
    approve_privileged: bool
    speak: bool
    snapshot_path: Path
    actions_path: Path
    voice_user: str
    voice_auth_wav: str
    voice_threshold: float
    master_password: str
    model_override: str
    skip_voice_auth_guard: bool
    master_password_ok: bool
    phone_queue: Path
    phone_report: Path
    phone_call_log: Path
    repo_root_fn: _RepoRootFn
    _respond: _RespondFn
    _require_state_mutation_voice_auth: _RequireVoiceAuthFn
    _web_augmented_llm_conversation: _WebConversationFn
    cmd_voice_say: _CommandFn
    cmd_voice_verify: _CommandFn
    cmd_connect_bootstrap: _CommandFn
    cmd_runtime_control: _CommandFn
    cmd_gaming_mode: _CommandFn
    cmd_weather: _CommandFn
    cmd_open_web: _CommandFn
    cmd_mobile_desktop_sync: _CommandFn
    cmd_self_heal: _CommandFn
    cmd_ops_autopilot: _CommandFn
    cmd_phone_spam_guard: _CommandFn
    cmd_phone_action: _CommandFn
    cmd_ops_sync: _CommandFn
    cmd_ops_brief: _CommandFn
    cmd_automation_run: _CommandFn
    cmd_run_task: _CommandFn
    cmd_brain_context: _CommandFn
    cmd_ingest: _CommandFn
    cmd_brain_status: _CommandFn
    cmd_mission_cancel: _CommandFn
    cmd_mission_status: _CommandFn
    cmd_status: _CommandFn


# Individual intent handlers

def _handle_connect_bootstrap(ctx: _DispatchCtx) -> tuple[str, int]:
    return "connect_bootstrap", ctx.cmd_connect_bootstrap(auto_open=ctx.execute)


def _handle_runtime_pause(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "runtime_pause", 2
    return "runtime_pause", ctx.cmd_runtime_control(
        pause=True, resume=False, safe_on=False, safe_off=False,
        reset=False, reason="voice_command",
    )


def _handle_runtime_resume(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "runtime_resume", 2
    return "runtime_resume", ctx.cmd_runtime_control(
        pause=False, resume=True, safe_on=False, safe_off=False,
        reset=False, reason="voice_command",
    )


def _handle_runtime_safe_on(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "runtime_safe_on", 2
    return "runtime_safe_on", ctx.cmd_runtime_control(
        pause=False, resume=False, safe_on=True, safe_off=False,
        reset=False, reason="voice_command",
    )


def _handle_runtime_safe_off(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "runtime_safe_off", 2
    return "runtime_safe_off", ctx.cmd_runtime_control(
        pause=False, resume=False, safe_on=False, safe_off=True,
        reset=False, reason="voice_command",
    )


def _handle_runtime_status(ctx: _DispatchCtx) -> tuple[str, int]:
    return "runtime_status", ctx.cmd_runtime_control(
        pause=False, resume=False, safe_on=False, safe_off=False,
        reset=False, reason="",
    )


def _handle_gaming_mode_auto_enable(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "gaming_mode_auto_enable", 2
    return "gaming_mode_auto_enable", ctx.cmd_gaming_mode(
        enable=None, reason="voice_command", auto_detect="on",
    )


def _handle_gaming_mode_auto_disable(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "gaming_mode_auto_disable", 2
    return "gaming_mode_auto_disable", ctx.cmd_gaming_mode(
        enable=None, reason="voice_command", auto_detect="off",
    )


def _handle_gaming_mode_enable(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "gaming_mode_enable", 2
    return "gaming_mode_enable", ctx.cmd_gaming_mode(
        enable=True, reason="voice_command", auto_detect="",
    )


def _handle_gaming_mode_disable(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "gaming_mode_disable", 2
    return "gaming_mode_disable", ctx.cmd_gaming_mode(
        enable=False, reason="voice_command", auto_detect="",
    )


def _handle_gaming_mode_status(ctx: _DispatchCtx) -> tuple[str, int]:
    return "gaming_mode_status", ctx.cmd_gaming_mode(
        enable=None, reason="", auto_detect="",
    )


def _handle_weather(ctx: _DispatchCtx) -> tuple[str, int]:
    rc = ctx.cmd_weather(location=_extract_weather_location(ctx.text))
    if rc != 0:
        logger.info("Weather handler failed (rc=%d), falling back to web-augmented LLM", rc)
        rc = ctx._web_augmented_llm_conversation(
            ctx.text, speak=ctx.speak, force_web_search=True,
        )
        return "llm_conversation_weather_fallback", rc
    return "weather", rc


def _handle_web_research(ctx: _DispatchCtx) -> tuple[str, int]:
    rc = ctx._web_augmented_llm_conversation(
        ctx.text, speak=ctx.speak, force_web_search=True,
    )
    return "web_research", rc


def _handle_open_web(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx.execute:
        print("reason=Set --execute to open browser URLs.")
        return "open_web", 2
    url = _extract_first_url(ctx.text)
    if not url:
        print("reason=No valid URL found. Include full URL like https://example.com")
        return "open_web", 2
    return "open_web", ctx.cmd_open_web(url)


def _handle_mobile_desktop_sync(ctx: _DispatchCtx) -> tuple[str, int]:
    return "mobile_desktop_sync", ctx.cmd_mobile_desktop_sync(
        auto_ingest=True, as_json=False,
    )


def _handle_self_heal(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "self_heal", 2
    return "self_heal", ctx.cmd_self_heal(
        force_maintenance=False, keep_recent=1800,
        snapshot_note="voice-self-heal", as_json=False,
    )


def _handle_ops_autopilot(ctx: _DispatchCtx) -> tuple[str, int]:
    return "ops_autopilot", ctx.cmd_ops_autopilot(
        snapshot_path=ctx.snapshot_path,
        actions_path=ctx.actions_path,
        execute=ctx.execute,
        approve_privileged=ctx.approve_privileged,
        auto_open_connectors=ctx.execute,
    )


def _handle_phone_spam_guard(ctx: _DispatchCtx) -> tuple[str, int]:
    return "phone_spam_guard", ctx.cmd_phone_spam_guard(
        call_log_path=ctx.phone_call_log,
        report_path=ctx.phone_report,
        queue_path=ctx.phone_queue,
        threshold=0.65,
        queue_actions=ctx.execute,
    )


def _handle_phone_send_sms(ctx: _DispatchCtx) -> tuple[str, int]:
    number = _extract_first_phone_number(ctx.text)
    if not number:
        print("intent=phone_send_sms")
        print("reason=No phone number found in voice command.")
        return "phone_send_sms", 2
    sms_body = ctx.text
    for _trigger in ["send a text to", "send a message to", "send text to", "send message to", "text to", "message to"]:
        if _trigger in ctx.lowered:
            sms_body = ctx.text[ctx.lowered.index(_trigger) + len(_trigger):].strip()
            break
    if number in sms_body:
        sms_body = sms_body.replace(number, "", 1).strip()
    if not sms_body and ":" in ctx.text:
        sms_body = ctx.text.split(":", 1)[1].strip()
    if not sms_body:
        sms_body = ctx.text
    if not ctx.execute:
        print("reason=Set --execute to queue phone actions.")
        return "phone_send_sms", 2
    return "phone_send_sms", ctx.cmd_phone_action(
        action="send_sms", number=number, message=sms_body,
        queue_path=ctx.phone_queue,
    )


def _handle_phone_ignore_call(ctx: _DispatchCtx) -> tuple[str, int]:
    number = _extract_first_phone_number(ctx.text)
    if not number:
        print("intent=phone_ignore_call")
        print("reason=No phone number found in voice command.")
        return "phone_ignore_call", 2
    if not ctx.execute:
        print("reason=Set --execute to queue phone actions.")
        return "phone_ignore_call", 2
    return "phone_ignore_call", ctx.cmd_phone_action(
        action="ignore_call", number=number, message="",
        queue_path=ctx.phone_queue,
    )


def _handle_phone_place_call(ctx: _DispatchCtx) -> tuple[str, int]:
    number = _extract_first_phone_number(ctx.text)
    if not number:
        print("reason=No phone number found in command.")
        return "phone_place_call", 2
    if not ctx.execute:
        print("reason=Set --execute to queue phone actions.")
        return "phone_place_call", 2
    return "phone_place_call", ctx.cmd_phone_action(
        action="place_call", number=number, message="",
        queue_path=ctx.phone_queue,
    )


def _handle_ops_sync(ctx: _DispatchCtx) -> tuple[str, int]:
    live_snapshot = ctx.snapshot_path.with_name(OPS_SNAPSHOT_FILENAME)
    return "ops_sync", ctx.cmd_ops_sync(live_snapshot)


def _handle_ops_brief(ctx: _DispatchCtx) -> tuple[str, int]:
    return "ops_brief", ctx.cmd_ops_brief(
        snapshot_path=ctx.snapshot_path, output_path=None,
    )


def _handle_automation_run(ctx: _DispatchCtx) -> tuple[str, int]:
    return "automation_run", ctx.cmd_automation_run(
        actions_path=ctx.actions_path,
        approve_privileged=ctx.approve_privileged,
        execute=ctx.execute,
    )


def _handle_generate_code(ctx: _DispatchCtx) -> tuple[str, int]:
    idx = ctx.lowered.index("generate code") + len("generate code")
    prompt = ctx.text[idx:].strip()
    prompt = prompt or "Generate high-quality production code for the requested task."
    return "generate_code", ctx.cmd_run_task(
        task_type="code", prompt=prompt, execute=ctx.execute,
        approve_privileged=ctx.approve_privileged,
        model="qwen3-coder:30b", endpoint="http://127.0.0.1:11434",
        quality_profile="max_quality", output_path=None,
    )


def _handle_generate_image(ctx: _DispatchCtx) -> tuple[str, int]:
    idx = ctx.lowered.index("generate image") + len("generate image")
    prompt = ctx.text[idx:].strip()
    prompt = prompt or "Generate a high-quality concept image."
    return "generate_image", ctx.cmd_run_task(
        task_type="image", prompt=prompt, execute=ctx.execute,
        approve_privileged=ctx.approve_privileged,
        model="qwen3-coder:30b", endpoint="http://127.0.0.1:11434",
        quality_profile="max_quality", output_path=None,
    )


def _handle_generate_video(ctx: _DispatchCtx) -> tuple[str, int]:
    idx = ctx.lowered.index("generate video") + len("generate video")
    prompt = ctx.text[idx:].strip()
    prompt = prompt or "Generate a high-quality short cinematic video."
    return "generate_video", ctx.cmd_run_task(
        task_type="video", prompt=prompt, execute=ctx.execute,
        approve_privileged=ctx.approve_privileged,
        model="qwen3-coder:30b", endpoint="http://127.0.0.1:11434",
        quality_profile="max_quality", output_path=None,
    )


def _handle_generate_model3d(ctx: _DispatchCtx) -> tuple[str, int]:
    return "generate_model3d", ctx.cmd_run_task(
        task_type="model3d", prompt=ctx.text, execute=ctx.execute,
        approve_privileged=ctx.approve_privileged,
        model="qwen3-coder:30b", endpoint="http://127.0.0.1:11434",
        quality_profile="max_quality", output_path=None,
    )


def _handle_schedule_calendar(ctx: _DispatchCtx) -> tuple[str, int]:
    return "ops_brief", ctx.cmd_ops_brief(
        snapshot_path=ctx.snapshot_path, output_path=None,
    )


def _handle_task_queries(ctx: _DispatchCtx) -> tuple[str, int]:
    return "ops_brief", ctx.cmd_ops_brief(
        snapshot_path=ctx.snapshot_path, output_path=None,
    )


def _handle_memory_search(ctx: _DispatchCtx) -> tuple[str, int]:
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
    query_text = ctx.text
    for trigger in _memory_triggers:
        if trigger in ctx.lowered:
            idx = ctx.lowered.index(trigger) + len(trigger)
            query_text = ctx.text[idx:].strip().rstrip("?").strip()
            break
    if not query_text:
        query_text = ctx.text
    return "brain_context", ctx.cmd_brain_context(
        query=query_text, max_items=5, max_chars=1200, as_json=False,
    )


def _handle_memory_forget(ctx: _DispatchCtx) -> tuple[str, int]:
    _forget_triggers = [
        "forget everything about",
        "forget about",
        "forget that",
        "delete memory of",
        "remove from memory",
        "stop remembering",
        "unlearn",
    ]
    topic = ctx.text
    for trigger in _forget_triggers:
        if trigger in ctx.lowered:
            idx = ctx.lowered.index(trigger) + len(trigger)
            topic = ctx.text[idx:].strip().rstrip(".").strip()
            break
    if not topic:
        ctx._respond("What should I forget? Try 'Forget about [topic]'.")
        return "memory_forget", 0
    bus = get_bus()
    kg = bus.ctx.kg
    if kg is not None:
        keywords = [w for w in topic.split() if len(w) > 2]
        if not keywords:
            keywords = [topic]
        count = kg.retract_facts(keywords)
        ctx._respond(f"Done. I've forgotten {count} fact(s) about '{topic}'.")
        return "memory_forget", 0
    ctx._respond("Knowledge graph is not available right now.")
    return "memory_forget", 1


def _handle_memory_ingest(ctx: _DispatchCtx) -> tuple[str, int]:
    content = ctx.text
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
        if trigger in ctx.lowered:
            idx = ctx.lowered.index(trigger) + len(trigger)
            content = ctx.text[idx:].strip()
            break
    if not content:
        content = ctx.text
    rc = ctx.cmd_ingest(
        source="user", kind="episodic",
        task_id=make_task_id("voice-remember"), content=content,
    )
    if rc == 0:
        ctx._respond("Got it, I'll remember that.")
    else:
        ctx._respond("Sorry, I couldn't save that to memory.")
    return "memory_ingest", rc


def _handle_brain_status(ctx: _DispatchCtx) -> tuple[str, int]:
    rc = ctx.cmd_brain_status(as_json=False)
    ctx._respond("Here's your brain status \u2014 check the details above.")
    return "brain_status", rc


def _handle_mission_cancel(ctx: _DispatchCtx) -> tuple[str, int]:
    if not ctx._require_state_mutation_voice_auth():
        return "mission_cancel", 2
    mission_id = ""
    for prefix in ["cancel mission ", "cancel the mission ", "stop mission ", "abort mission "]:
        if prefix in ctx.lowered:
            mission_id = ctx.text[ctx.lowered.index(prefix) + len(prefix):].strip()
            break
    if not mission_id:
        from jarvis_engine.learning.missions import load_missions as _load_missions
        missions = _load_missions(ctx.repo_root_fn())
        for m in reversed(missions):
            if str(m.get("status", "")).lower() == "pending":
                mission_id = str(m.get("mission_id", ""))
                break
    if not mission_id:
        ctx._respond("No pending missions to cancel.")
        return "mission_cancel", 0
    return "mission_cancel", ctx.cmd_mission_cancel(mission_id=mission_id)


def _handle_mission_status(ctx: _DispatchCtx) -> tuple[str, int]:
    return "mission_status", ctx.cmd_mission_status(last=5)


def _handle_system_status(ctx: _DispatchCtx) -> tuple[str, int]:
    rc = ctx.cmd_status()
    ctx._respond("System status check complete.")
    return "system_status", rc


# Fuzzy matching helpers for voice command recognition

def _fuzzy_match(text: str, target: str, threshold: float = 0.80) -> bool:
    """Return True if *text* contains a subsequence similar to *target*.

    Uses ``difflib.SequenceMatcher`` to compute a similarity ratio.
    Compares sliding windows of *target*-length words in *text* against
    *target* so the match works even when *target* is embedded inside a
    longer utterance.
    """
    if not text or not target:
        return False
    target_words = target.split()
    text_words = text.split()
    target_len = len(target_words)
    if target_len == 0:
        return False
    for i in range(max(1, len(text_words) - target_len + 1)):
        window = " ".join(text_words[i : i + target_len])
        ratio = difflib.SequenceMatcher(None, window, target).ratio()
        if ratio >= threshold:
            return True
    return False


# Critical commands eligible for fuzzy matching as a fallback.
# These are voice commands where a near-miss (STT error) should still
# be recognised to avoid silently ignoring the user's intent.
_CRITICAL_FUZZY_TARGETS: list[tuple[str, Callable[["_DispatchCtx"], tuple[str, int]]]] = []
# Populated after _DISPATCH_RULES is defined (see below).


# Matcher helpers — build predicate functions declaratively

# Type alias for a matcher/handler pair
_IntentRule = tuple[Callable[[str], bool], Callable[[_DispatchCtx], tuple[str, int]]]


def _match_any(*phrases: str) -> Callable[[str], bool]:
    """Return a matcher that succeeds if *any* phrase is found in the text."""
    return lambda low: any(p in low for p in phrases)


def _match_all_any(required: str, *any_of: str) -> Callable[[str], bool]:
    """Match when *required* phrase is present AND any of *any_of* is too."""
    return lambda low: required in low and any(p in low for p in any_of)


def _expand_natural_command_aliases(lowered: str) -> str:
    """Add canonical command aliases for natural sentence-shaped requests."""
    normalized = re.sub(r"\s+", " ", lowered.strip())
    if not normalized:
        return ""

    stripped = normalized
    wakeword_prefixes = (
        "hey jarvis ",
        "okay jarvis ",
        "ok jarvis ",
        "jarvis ",
    )
    changed = True
    while changed:
        changed = False
        for prefix in wakeword_prefixes:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
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
        and any(
            term in stripped
            for term in ("status", "health", "holding up", "doing", "doing today")
        )
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

    if (
        any(term in stripped for term in ("pause", "stop", "hold", "quiet"))
        and any(term in stripped for term in ("jarvis", "daemon", "yourself", "autopilot"))
    ):
        _add("pause jarvis")

    if (
        any(term in stripped for term in ("resume", "continue", "wake up", "start working again"))
        and any(term in stripped for term in ("jarvis", "daemon", "yourself", "autopilot"))
    ):
        _add("resume jarvis")

    if "safe mode" in stripped:
        if any(term in stripped for term in ("on", "enable", "turn on", "go into")):
            _add("safe mode on")
        if any(term in stripped for term in ("off", "disable", "turn off", "leave")):
            _add("safe mode off")

    return " ".join(aliases)


# Dispatch table — ordered list of (matcher, handler) rules.
#
# Order matters: first match wins, preserving the original if/elif semantics.
# Grouped by theme; comments mark group boundaries for readability.

_DISPATCH_RULES: list[_IntentRule] = [
    # -- System setup & runtime control --
    (
        lambda low: ("connect" in low or "setup" in low)
        and any(k in low for k in ("email", "calendar", "all", "everything")),
        _handle_connect_bootstrap,
    ),
    (_match_any("pause jarvis", "pause daemon", "pause autopilot",
                "go idle", "stand down", "pause yourself"),
     _handle_runtime_pause),
    (_match_any("resume jarvis", "resume daemon", "resume autopilot",
                "wake up", "start working again"),
     _handle_runtime_resume),
    (_match_any("safe mode on", "enable safe mode"),   _handle_runtime_safe_on),
    (_match_any("safe mode off", "disable safe mode"), _handle_runtime_safe_off),
    (_match_any("runtime status", "control status", "safe mode status"),
     _handle_runtime_status),

    # -- Gaming mode (auto-detect rules must precede generic) --
    (_match_all_any("auto gaming mode", "on", "enable", "start"),
     _handle_gaming_mode_auto_enable),
    (_match_all_any("auto gaming mode", "off", "disable", "stop"),
     _handle_gaming_mode_auto_disable),
    (_match_all_any("gaming mode", "on", "enable", "start"),
     _handle_gaming_mode_enable),
    (_match_all_any("gaming mode", "off", "disable", "stop"),
     _handle_gaming_mode_disable),
    (_match_all_any("gaming mode", "status", "state"),
     _handle_gaming_mode_status),

    # -- Info, web & ops --
    (lambda low: ("weather" in low or "forecast" in low) and "my calendar" not in low,
     _handle_weather),
    (_match_any("search the web for", "search web for",
                "search the internet for", "search online for",
                "web search", "find on the web", "search for",
                "look up", "google", "find me", "find out"),
     _handle_web_research),
    (_match_any("open website", "open webpage", "open page",
                "open url", "browse to", "go to "),
     _handle_open_web),
    (_match_any("sync mobile", "sync desktop", "cross-device sync", "sync devices"),
     _handle_mobile_desktop_sync),
    (_match_any("self heal", "self-heal", "repair yourself", "diagnose yourself"),
     _handle_self_heal),
    (_match_any("organize my day", "run autopilot", "daily autopilot",
                "plan my day", "plan today", "organize today",
                "help me prioritize"),
     _handle_ops_autopilot),

    # -- Phone actions --
    (
        lambda low: (
            ("block" in low and "spam" in low and "call" in low)
            or ("stop" in low and "scam" in low and "call" in low)
            or ("handle" in low and "spam" in low and "call" in low)
            or ("run" in low and "spam" in low and "scan" in low)
            or ("show" in low and "spam" in low and "report" in low)
        ),
        _handle_phone_spam_guard,
    ),
    (_match_any("send text", "send message", "send a text",
                "send a message", "text to ", "message to "),
     _handle_phone_send_sms),
    (_match_any("ignore call", "decline call", "reject call"),
     _handle_phone_ignore_call),
    (lambda low: low.startswith("call ") or "place a call" in low
                 or "make a call" in low or "phone call" in low,
     _handle_phone_place_call),

    # -- Ops sync, brief, automation & content generation --
    (_match_all_any("sync", "calendar", "email", "inbox", "ops"),
     _handle_ops_sync),
    (_match_any("daily brief", "ops brief", "morning brief",
                "give me a brief", "my brief", "run brief", "brief me"),
     _handle_ops_brief),
    (_match_all_any("automation", "run", "execute", "start"),
     _handle_automation_run),
    (_match_any("generate code"),  _handle_generate_code),
    (_match_any("generate image"), _handle_generate_image),
    (_match_any("generate video"), _handle_generate_video),
    (_match_any("generate 3d", "generate a 3d model", "generate 3d model"),
     _handle_generate_model3d),

    # -- Calendar & task queries --
    (_match_any("my schedule", "my calendar", "my meetings", "my agenda",
                "what's on today", "what is on today",
                "what do i have today", "what's happening today",
                "what is happening today", "today's schedule",
                "today's meetings", "upcoming meetings",
                "upcoming events", "next meeting", "next appointment",
                "daily briefing", "morning briefing",
                "give me a briefing", "give me my briefing"),
     _handle_schedule_calendar),
    (_match_any("my tasks", "my to-do", "my todo",
                "what are my tasks", "task list", "pending tasks",
                "open tasks", "what do i need to do",
                "what should i do", "what needs to be done"),
     _handle_task_queries),

    # -- Memory & knowledge --
    (_match_any("what do you know about", "what do you remember about",
                "do you remember when", "do you remember that",
                "do you remember my", "search memory for",
                "search your memory for", "search your memory about",
                "what did i tell you about", "what have i said about"),
     _handle_memory_search),
    (_match_any("forget about", "forget that", "forget everything about",
                "unlearn", "stop remembering", "delete memory of",
                "remove from memory"),
     _handle_memory_forget),
    (_match_any("remember that", "remember this", "save this",
                "make a note", "take a note", "note that",
                "don't forget"),
     _handle_memory_ingest),
    (_match_any("knowledge status", "knowledge graph",
                "how much do you know", "brain status", "memory status"),
     _handle_brain_status),

    # -- Missions & system status --
    (_match_any("cancel mission", "cancel the mission",
                "stop mission", "abort mission"),
     _handle_mission_cancel),
    (_match_any("mission status", "learning mission",
                "active missions", "my missions"),
     _handle_mission_status),
    (_match_any("system status", "jarvis status", "how are you",
                "status report", "health check",
                "are you working", "are you running"),
     _handle_system_status),
]

# Populate fuzzy targets for critical commands (pause, resume, safe mode,
# stop, status).  These phrases are the most important to recognise even
# when STT produces slight misheard variants (e.g. "paws jarvis").
_CRITICAL_FUZZY_TARGETS.extend([
    ("pause jarvis",    _handle_runtime_pause),
    ("pause daemon",    _handle_runtime_pause),
    ("resume jarvis",   _handle_runtime_resume),
    ("resume daemon",   _handle_runtime_resume),
    ("safe mode on",    _handle_runtime_safe_on),
    ("safe mode off",   _handle_runtime_safe_off),
    ("stop mission",    _handle_mission_cancel),
    ("jarvis status",   _handle_system_status),
    ("system status",   _handle_system_status),
    ("runtime status",  _handle_runtime_status),
])


# Main voice-run implementation

def _validate_voice_auth(auth: VoiceAuthContext) -> int | None:
    """Check owner-guard, voice-auth, and execution permission.

    Returns None if auth passes and the command may proceed, or an int
    return code (2) if the command should be rejected.
    """
    owner_guard = read_owner_guard(auth.repo_root())

    if bool(owner_guard.get("enabled", False)):
        expected_owner = str(owner_guard.get("owner_user_id", "")).strip().lower()
        incoming_owner = auth.voice_user.strip().lower()
        if expected_owner and incoming_owner != expected_owner and not auth.master_password_ok:
            print("intent=owner_guard_blocked")
            print("reason=voice_user_not_owner")
            if auth.speak:
                auth.cmd_voice_say(text="Owner guard blocked this command.")
            return 2
        if (
            not auth.skip_voice_auth_guard
            and not auth.voice_auth_wav.strip()
            and not auth.master_password_ok
            and not auth.read_only_request
        ):
            print("intent=owner_guard_blocked")
            print("reason=voice_auth_required_when_owner_guard_enabled")
            if auth.speak:
                auth.cmd_voice_say(
                    text="Owner guard requires voice authentication for state-changing commands.",
                )
            return 2

    if (
        (auth.execute or auth.approve_privileged)
        and not auth.read_only_request
        and not auth.skip_voice_auth_guard
        and not auth.voice_auth_wav.strip()
        and not auth.master_password_ok
    ):
        print("intent=voice_auth_required")
        print("reason=execute_or_privileged_voice_actions_require_voice_auth_wav")
        if auth.speak:
            auth.cmd_voice_say(text="Voice authentication is required for executable commands.")
        return 2

    if auth.voice_auth_wav.strip():
        verify_rc = auth.cmd_voice_verify(
            user_id=auth.voice_user,
            wav_path=auth.voice_auth_wav,
            threshold=auth.voice_threshold,
        )
        if verify_rc != 0:
            print("intent=voice_auth_failed")
            if auth.speak:
                auth.cmd_voice_say(text="Voice authentication failed. Command blocked.")
            return 2

    return None


def _build_dispatch_ctx(
    params: VoiceRunParams,
    lowered: str,
    *,
    master_password_ok: bool,
    repo_root: _RepoRootFn,
    respond_fn: _RespondFn,
    require_auth_fn: _RequireVoiceAuthFn,
    web_augmented_fn: _WebConversationFn,
    cmd_fns: dict[str, _CommandFn],
) -> _DispatchCtx:
    """Populate a _DispatchCtx with all the values needed by intent handlers."""
    ctx = _DispatchCtx()
    ctx.text = params.text
    ctx.lowered = lowered
    ctx.execute = params.execute
    ctx.approve_privileged = params.approve_privileged
    ctx.speak = params.speak
    ctx.snapshot_path = params.snapshot_path
    ctx.actions_path = params.actions_path
    ctx.voice_user = params.voice_user
    ctx.voice_auth_wav = params.voice_auth_wav
    ctx.voice_threshold = params.voice_threshold
    ctx.master_password = params.master_password
    ctx.model_override = params.model_override
    ctx.skip_voice_auth_guard = params.skip_voice_auth_guard
    ctx.master_password_ok = master_password_ok
    ctx.phone_queue = repo_root() / ".planning" / "phone_actions.jsonl"
    ctx.phone_report = repo_root() / ".planning" / "phone_spam_report.json"
    ctx.phone_call_log = Path(os.getenv(
        "JARVIS_CALL_LOG_JSON",
        str(repo_root() / ".planning" / "phone_call_log.json"),
    ))
    ctx.repo_root_fn = repo_root
    ctx._respond = respond_fn
    ctx._require_state_mutation_voice_auth = require_auth_fn
    ctx._web_augmented_llm_conversation = web_augmented_fn
    for attr, fn in cmd_fns.items():
        setattr(ctx, attr, fn)
    return ctx


def _post_dispatch_learn(
    intent: str,
    rc: int,
    text: str,
    utterance: VoiceUtterance | None,
    last_response: str,
    execute: bool,
    approve_privileged: bool,
    voice_user: str,
) -> None:
    """Auto-ingest memory and fire background enriched learning after dispatch."""
    try:
        _auto_ingest_memory(
            source="user",
            kind="episodic",
            task_id=make_task_id(f"voice-{intent}"),
            content=(
                f"Voice command accepted. intent={intent}; status_code={rc}; execute={execute}; "
                f"approve_privileged={approve_privileged}; voice_user={voice_user}; text={text[:500]}; "
                f"stt_backend={utterance.get('backend', '') if utterance else ''}; "
                f"stt_confidence={utterance.get('confidence', 0.0) if utterance else 0.0}; "
                f"raw_text={(utterance.get('raw_text', '') if utterance else '')[:500]}"
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("Auto-ingest of voice command memory failed: %s", exc)

    # Enriched learning for ALL successful commands (not just LLM path).
    # Runs in a daemon thread to avoid blocking the HTTP response -- the
    # enriched pipeline may lazy-load embedding models on first call.
    if intent != "llm_conversation":
        learn_response = last_response or f"[{intent}] Command executed successfully."
        try:
            _learn_bus = get_bus()
        except (OSError, RuntimeError) as exc:
            logger.debug("get_bus() failed for background learning: %s", exc)
            _learn_bus = None
        if _learn_bus is not None:
            learn_bus = _learn_bus
            _learn_cmd = LearnInteractionCommand(
                user_message=text[:1000],
                assistant_response=learn_response[:1000],
                task_id=make_task_id(f"learn-{intent}"),
                route=intent,
                topic=text[:100],
            )

            def _bg_learn() -> None:
                try:
                    learn_bus.dispatch(_learn_cmd)
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("Background enriched learning failed: %s", exc)

            threading.Thread(target=_bg_learn, daemon=True).start()


def _import_voice_commands():
    """Lazy-import all command functions needed by voice dispatch.

    Returns a tuple of (cmd_fns dict, repo_root callable,
    _web_augmented_llm_conversation callable).
    """
    from jarvis_engine.main import (
        cmd_voice_say, cmd_voice_verify,
        cmd_connect_bootstrap, cmd_runtime_control,
        cmd_gaming_mode, cmd_weather, cmd_open_web,
        cmd_mobile_desktop_sync, cmd_self_heal,
        cmd_phone_spam_guard, cmd_phone_action,
        cmd_run_task, cmd_ingest, cmd_status,
    )
    from jarvis_engine.cli.ops import (
        cmd_ops_autopilot, cmd_ops_sync, cmd_ops_brief,
        cmd_automation_run, cmd_mission_cancel, cmd_mission_status,
    )
    from jarvis_engine.cli.knowledge import cmd_brain_context, cmd_brain_status
    import jarvis_engine.voice.pipeline as _vp

    cmd_fns: dict[str, _CommandFn] = {
        "cmd_voice_say": cmd_voice_say,
        "cmd_voice_verify": cmd_voice_verify,
        "cmd_connect_bootstrap": cmd_connect_bootstrap,
        "cmd_runtime_control": cmd_runtime_control,
        "cmd_gaming_mode": cmd_gaming_mode,
        "cmd_weather": cmd_weather,
        "cmd_open_web": cmd_open_web,
        "cmd_mobile_desktop_sync": cmd_mobile_desktop_sync,
        "cmd_self_heal": cmd_self_heal,
        "cmd_ops_autopilot": cmd_ops_autopilot,
        "cmd_phone_spam_guard": cmd_phone_spam_guard,
        "cmd_phone_action": cmd_phone_action,
        "cmd_ops_sync": cmd_ops_sync,
        "cmd_ops_brief": cmd_ops_brief,
        "cmd_automation_run": cmd_automation_run,
        "cmd_run_task": cmd_run_task,
        "cmd_brain_context": cmd_brain_context,
        "cmd_ingest": cmd_ingest,
        "cmd_brain_status": cmd_brain_status,
        "cmd_mission_cancel": cmd_mission_cancel,
        "cmd_mission_status": cmd_mission_status,
        "cmd_status": cmd_status,
    }
    return cmd_fns, _vp.repo_root, _vp._web_augmented_llm_conversation


def _dispatch_voice_intent(
    lowered: str,
    text: str,
    ctx: "_DispatchCtx",
    web_augmented_fn: _WebConversationFn,
    speak: bool,
    model_override: str,
    respond_fn: _RespondFn,
) -> tuple[str, int]:
    """Match *lowered* against dispatch rules and run the matching handler.

    Falls back to web-augmented LLM conversation when no rule matches.
    Returns ``(intent, rc)``.
    """
    expanded_lowered = _expand_natural_command_aliases(lowered)
    _phone_place_call_has_number = (
        (lowered.startswith("call ") or "place a call" in lowered
         or "make a call" in lowered or "phone call" in lowered)
        and _extract_first_phone_number(text)
    )

    for matcher, handler in _DISPATCH_RULES:
        if handler is _handle_phone_place_call and not _phone_place_call_has_number:
            continue
        if matcher(expanded_lowered):
            return handler(ctx)

    # Fuzzy fallback: try critical commands with similarity matching.
    # Only reached when exact substring matching failed.
    for target_phrase, handler in _CRITICAL_FUZZY_TARGETS:
        if _fuzzy_match(expanded_lowered, target_phrase):
            logger.info(
                "Fuzzy match: '%s' matched critical command '%s'",
                expanded_lowered[:60], target_phrase,
            )
            return handler(ctx)

    rc = web_augmented_fn(
        text, speak=speak, force_web_search=False,
        model_override=model_override, default_route="routine",
        try_fallback_classifier=True, response_callback=respond_fn,
    )
    return "llm_conversation", rc


def _speak_persona_reply(
    intent: str,
    rc: int,
    repo_root_fn: _RepoRootFn,
    cmd_voice_say: _CommandFn,
) -> None:
    """Speak a persona-flavored reply for non-LLM intents."""
    persona = load_persona_config(repo_root_fn())
    persona_line = compose_persona_reply(
        persona, intent=intent, success=(rc == 0),
        reason="" if rc == 0 else "failed or requires approval",
    )
    cmd_voice_say(text=persona_line)


def _check_voice_auth(
    lowered: str,
    params: VoiceRunParams,
    cmd_fns: dict[str, _CommandFn],
    repo_root: _RepoRootFn,
) -> tuple[int | None, bool]:
    """Run master-password verification and voice auth checks.

    Returns ``(auth_rc, master_password_ok)`` where *auth_rc* is ``None``
    when auth passes or an int return code when it fails.
    """
    master_password_ok = False
    if params.master_password.strip():
        master_password_ok = verify_master_password(
            repo_root(), params.master_password.strip(),
        )

    read_only_request = _is_read_only_voice_request(
        lowered, execute=params.execute,
        approve_privileged=params.approve_privileged,
    )

    auth_ctx = VoiceAuthContext(
        voice_user=params.voice_user,
        voice_auth_wav=params.voice_auth_wav,
        voice_threshold=params.voice_threshold,
        master_password=params.master_password,
        master_password_ok=master_password_ok,
        execute=params.execute,
        approve_privileged=params.approve_privileged,
        read_only_request=read_only_request,
        skip_voice_auth_guard=params.skip_voice_auth_guard,
        speak=params.speak,
        cmd_voice_say=cmd_fns["cmd_voice_say"],
        cmd_voice_verify=cmd_fns["cmd_voice_verify"],
        repo_root=repo_root,
    )
    auth_rc = _validate_voice_auth(auth_ctx)
    return auth_rc, master_password_ok


def cmd_voice_run_impl(
    text: str,
    utterance: VoiceUtterance | None = None,
    *,
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
    cmd_fns, repo_root, _web_augmented_llm_conversation = _import_voice_commands()
    text = text.strip()  # Normalize whitespace so lowered-index math aligns with text
    lowered = text.lower()

    params = VoiceRunParams(
        text=text, utterance=utterance, execute=execute, approve_privileged=approve_privileged,
        speak=speak, snapshot_path=snapshot_path, actions_path=actions_path,
        voice_user=voice_user, voice_auth_wav=voice_auth_wav,
        voice_threshold=voice_threshold, master_password=master_password,
        model_override=model_override, skip_voice_auth_guard=skip_voice_auth_guard,
    )

    auth_rc, master_password_ok = _check_voice_auth(
        lowered, params, cmd_fns, repo_root,
    )
    if auth_rc is not None:
        return auth_rc

    _last_response = ""

    def _respond(msg: str) -> None:
        nonlocal _last_response
        _last_response = msg
        print(f"response={escape_response(msg)}")

    def _require_state_mutation_voice_auth() -> bool:
        if skip_voice_auth_guard:
            return True
        if voice_auth_wav.strip() or master_password_ok:
            return True
        print("intent=voice_auth_required")
        print("reason=state_mutating_voice_actions_require_voice_auth_wav")
        if speak:
            cmd_fns["cmd_voice_say"](text="Voice authentication is required for state changing commands.")
        return False

    ctx = _build_dispatch_ctx(
        params, lowered,
        master_password_ok=master_password_ok, repo_root=repo_root,
        respond_fn=_respond, require_auth_fn=_require_state_mutation_voice_auth,
        web_augmented_fn=_web_augmented_llm_conversation, cmd_fns=cmd_fns,
    )

    intent, rc = _dispatch_voice_intent(
        lowered, text, ctx, _web_augmented_llm_conversation,
        speak, model_override, _respond,
    )
    print(f"intent={intent}")
    print(f"status_code={rc}")

    if rc == 0:
        _post_dispatch_learn(
            intent, rc, text, utterance, _last_response,
            execute, approve_privileged, voice_user,
        )
    if speak and intent != "llm_conversation":
        _speak_persona_reply(intent, rc, repo_root, cmd_fns["cmd_voice_say"])

    return rc
