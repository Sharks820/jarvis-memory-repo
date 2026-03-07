"""Voice intent routing — dispatch table for voice commands.

Split from voice_pipeline.py for separation of concerns.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable

from jarvis_engine._bus import get_bus
from jarvis_engine.command_bus import CommandBus
from jarvis_engine.commands.learning_commands import LearnInteractionCommand
from jarvis_engine.owner_guard import read_owner_guard, verify_master_password
from jarvis_engine.persona import compose_persona_reply, load_persona_config

from jarvis_engine._constants import (
    OPS_SNAPSHOT_FILENAME as _OPS_SNAPSHOT_FILENAME,
    make_task_id as _make_task_id,
)

from jarvis_engine.auto_ingest import auto_ingest_memory as _auto_ingest_memory

from jarvis_engine.voice_extractors import (
    escape_response,
    _extract_first_phone_number,
    _extract_weather_location,
    _extract_first_url,
    _is_read_only_voice_request,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dispatch context — passed to each intent handler
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Individual intent handlers
# ---------------------------------------------------------------------------

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
    if not ctx.execute:
        print("reason=Set --execute to queue phone actions.")
        return "phone_place_call", 2
    return "phone_place_call", ctx.cmd_phone_action(
        action="place_call", number=number, message="",
        queue_path=ctx.phone_queue,
    )


def _handle_ops_sync(ctx: _DispatchCtx) -> tuple[str, int]:
    live_snapshot = ctx.snapshot_path.with_name(_OPS_SNAPSHOT_FILENAME)
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
        task_id=_make_task_id("voice-remember"), content=content,
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
        from jarvis_engine.learning_missions import load_missions as _load_missions
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


# ---------------------------------------------------------------------------
# Matcher functions — each returns True if the lowered text matches
# ---------------------------------------------------------------------------

# Type alias for a matcher/handler pair
_IntentRule = tuple[Callable[[str], bool], Callable[[_DispatchCtx], tuple[str, int]]]


def _build_dispatch_rules() -> list[_IntentRule]:
    """Build the ordered list of (matcher, handler) rules.

    The order matters: first match wins, matching the original if/elif
    semantics exactly.
    """
    return [
        # connect/setup
        (
            lambda low: ("connect" in low or "setup" in low)
            and any(k in low for k in ["email", "calendar", "all", "everything"]),
            _handle_connect_bootstrap,
        ),
        # runtime pause
        (
            lambda low: any(k in low for k in [
                "pause jarvis", "pause daemon", "pause autopilot",
                "go idle", "stand down", "pause yourself",
            ]),
            _handle_runtime_pause,
        ),
        # runtime resume
        (
            lambda low: any(k in low for k in [
                "resume jarvis", "resume daemon", "resume autopilot",
                "wake up", "start working again",
            ]),
            _handle_runtime_resume,
        ),
        # safe mode on
        (
            lambda low: any(k in low for k in ["safe mode on", "enable safe mode"]),
            _handle_runtime_safe_on,
        ),
        # safe mode off
        (
            lambda low: any(k in low for k in ["safe mode off", "disable safe mode"]),
            _handle_runtime_safe_off,
        ),
        # runtime status
        (
            lambda low: any(k in low for k in [
                "runtime status", "control status", "safe mode status",
            ]),
            _handle_runtime_status,
        ),
        # gaming mode auto enable (must come before generic gaming mode)
        (
            lambda low: "auto gaming mode" in low
            and any(k in low for k in ["on", "enable", "start"]),
            _handle_gaming_mode_auto_enable,
        ),
        # gaming mode auto disable
        (
            lambda low: "auto gaming mode" in low
            and any(k in low for k in ["off", "disable", "stop"]),
            _handle_gaming_mode_auto_disable,
        ),
        # gaming mode enable
        (
            lambda low: "gaming mode" in low
            and any(k in low for k in ["on", "enable", "start"]),
            _handle_gaming_mode_enable,
        ),
        # gaming mode disable
        (
            lambda low: "gaming mode" in low
            and any(k in low for k in ["off", "disable", "stop"]),
            _handle_gaming_mode_disable,
        ),
        # gaming mode status
        (
            lambda low: "gaming mode" in low
            and any(k in low for k in ["status", "state"]),
            _handle_gaming_mode_status,
        ),
        # weather (but not "my calendar")
        (
            lambda low: ("weather" in low or "forecast" in low) and "my calendar" not in low,
            _handle_weather,
        ),
        # web research
        (
            lambda low: any(key in low for key in [
                "search the web for", "search web for",
                "search the internet for", "search online for",
                "web search", "find on the web", "search for",
                "look up", "google", "find me", "find out",
            ]),
            _handle_web_research,
        ),
        # open web
        (
            lambda low: any(key in low for key in [
                "open website", "open webpage", "open page",
                "open url", "browse to", "go to ",
            ]),
            _handle_open_web,
        ),
        # mobile/desktop sync
        (
            lambda low: any(key in low for key in [
                "sync mobile", "sync desktop", "cross-device sync", "sync devices",
            ]),
            _handle_mobile_desktop_sync,
        ),
        # self heal
        (
            lambda low: any(key in low for key in [
                "self heal", "self-heal", "repair yourself", "diagnose yourself",
            ]),
            _handle_self_heal,
        ),
        # ops autopilot
        (
            lambda low: any(k in low for k in [
                "organize my day", "run autopilot", "daily autopilot",
                "plan my day", "plan today", "organize today",
                "help me prioritize",
            ]),
            _handle_ops_autopilot,
        ),
        # phone spam guard
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
        # phone send SMS
        (
            lambda low: any(k in low for k in [
                "send text", "send message", "send a text",
                "send a message", "text to ", "message to ",
            ]),
            _handle_phone_send_sms,
        ),
        # phone ignore call
        (
            lambda low: any(k in low for k in [
                "ignore call", "decline call", "reject call",
            ]),
            _handle_phone_ignore_call,
        ),
        # phone place call (needs number extraction as part of match)
        (
            lambda low: (
                low.startswith("call ") or "place a call" in low
                or "make a call" in low or "phone call" in low
            ),
            _handle_phone_place_call,
        ),
        # ops sync (calendar/email/ops)
        (
            lambda low: "sync" in low
            and any(k in low for k in ["calendar", "email", "inbox", "ops"]),
            _handle_ops_sync,
        ),
        # ops brief
        (
            lambda low: any(k in low for k in [
                "daily brief", "ops brief", "morning brief",
                "give me a brief", "my brief", "run brief", "brief me",
            ]),
            _handle_ops_brief,
        ),
        # automation run
        (
            lambda low: "automation" in low
            and any(k in low for k in ["run", "execute", "start"]),
            _handle_automation_run,
        ),
        # generate code
        (
            lambda low: "generate code" in low,
            _handle_generate_code,
        ),
        # generate image
        (
            lambda low: "generate image" in low,
            _handle_generate_image,
        ),
        # generate video
        (
            lambda low: "generate video" in low,
            _handle_generate_video,
        ),
        # generate 3d model
        (
            lambda low: "generate 3d" in low
            or "generate a 3d model" in low
            or "generate 3d model" in low,
            _handle_generate_model3d,
        ),
        # schedule / calendar / meeting queries
        (
            lambda low: any(k in low for k in [
                "my schedule", "my calendar", "my meetings", "my agenda",
                "what's on today", "what is on today",
                "what do i have today", "what's happening today",
                "what is happening today", "today's schedule",
                "today's meetings", "upcoming meetings",
                "upcoming events", "next meeting", "next appointment",
                "daily briefing", "morning briefing",
                "give me a briefing", "give me my briefing",
            ]),
            _handle_schedule_calendar,
        ),
        # task queries
        (
            lambda low: any(k in low for k in [
                "my tasks", "my to-do", "my todo",
                "what are my tasks", "task list", "pending tasks",
                "open tasks", "what do i need to do",
                "what should i do", "what needs to be done",
            ]),
            _handle_task_queries,
        ),
        # memory search / knowledge queries
        (
            lambda low: any(k in low for k in [
                "what do you know about", "what do you remember about",
                "do you remember when", "do you remember that",
                "do you remember my", "search memory for",
                "search your memory for", "search your memory about",
                "what did i tell you about", "what have i said about",
            ]),
            _handle_memory_search,
        ),
        # forget / unlearn
        (
            lambda low: any(k in low for k in [
                "forget about", "forget that", "forget everything about",
                "unlearn", "stop remembering", "delete memory of",
                "remove from memory",
            ]),
            _handle_memory_forget,
        ),
        # memory save / remember
        (
            lambda low: any(k in low for k in [
                "remember that", "remember this", "save this",
                "make a note", "take a note", "note that",
                "don't forget",
            ]),
            _handle_memory_ingest,
        ),
        # knowledge graph queries
        (
            lambda low: any(k in low for k in [
                "knowledge status", "knowledge graph",
                "how much do you know", "brain status", "memory status",
            ]),
            _handle_brain_status,
        ),
        # cancel mission
        (
            lambda low: any(k in low for k in [
                "cancel mission", "cancel the mission",
                "stop mission", "abort mission",
            ]),
            _handle_mission_cancel,
        ),
        # mission / learning queries
        (
            lambda low: any(k in low for k in [
                "mission status", "learning mission",
                "active missions", "my missions",
            ]),
            _handle_mission_status,
        ),
        # system status
        (
            lambda low: any(k in low for k in [
                "system status", "jarvis status", "how are you",
                "status report", "health check",
                "are you working", "are you running",
            ]),
            _handle_system_status,
        ),
    ]


# Module-level singleton — built once at import time.
_DISPATCH_RULES: list[_IntentRule] = _build_dispatch_rules()


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
    import jarvis_engine.voice_pipeline as _vp
    _web_augmented_llm_conversation = _vp._web_augmented_llm_conversation
    # Look up repo_root through voice_pipeline so tests that monkeypatch
    # voice_pipeline_mod.repo_root see the override in this module too.
    repo_root = _vp.repo_root

    lowered = text.lower().strip()
    _last_response = ""  # Capture assistant response for learning pipeline

    def _respond(msg: str) -> None:
        """Print response= line and capture text for learning pipeline.

        Newlines in the message are escaped so the entire response stays on
        one stdout line -- the mobile API parser splits on newlines and would
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

    # --- Build dispatch context ---
    ctx = _DispatchCtx()
    ctx.text = text
    ctx.lowered = lowered
    ctx.execute = execute
    ctx.approve_privileged = approve_privileged
    ctx.speak = speak
    ctx.snapshot_path = snapshot_path
    ctx.actions_path = actions_path
    ctx.voice_user = voice_user
    ctx.voice_auth_wav = voice_auth_wav
    ctx.voice_threshold = voice_threshold
    ctx.master_password = master_password
    ctx.model_override = model_override
    ctx.skip_voice_auth_guard = skip_voice_auth_guard
    ctx.master_password_ok = master_password_ok
    ctx.phone_queue = phone_queue
    ctx.phone_report = phone_report
    ctx.phone_call_log = phone_call_log
    ctx.repo_root_fn = repo_root
    ctx._respond = _respond
    ctx._require_state_mutation_voice_auth = _require_state_mutation_voice_auth
    ctx._web_augmented_llm_conversation = _web_augmented_llm_conversation
    ctx.cmd_voice_say = cmd_voice_say
    ctx.cmd_voice_verify = cmd_voice_verify
    ctx.cmd_connect_bootstrap = cmd_connect_bootstrap
    ctx.cmd_runtime_control = cmd_runtime_control
    ctx.cmd_gaming_mode = cmd_gaming_mode
    ctx.cmd_weather = cmd_weather
    ctx.cmd_open_web = cmd_open_web
    ctx.cmd_mobile_desktop_sync = cmd_mobile_desktop_sync
    ctx.cmd_self_heal = cmd_self_heal
    ctx.cmd_ops_autopilot = cmd_ops_autopilot
    ctx.cmd_phone_spam_guard = cmd_phone_spam_guard
    ctx.cmd_phone_action = cmd_phone_action
    ctx.cmd_ops_sync = cmd_ops_sync
    ctx.cmd_ops_brief = cmd_ops_brief
    ctx.cmd_automation_run = cmd_automation_run
    ctx.cmd_run_task = cmd_run_task
    ctx.cmd_brain_context = cmd_brain_context
    ctx.cmd_ingest = cmd_ingest
    ctx.cmd_brain_status = cmd_brain_status
    ctx.cmd_mission_cancel = cmd_mission_cancel
    ctx.cmd_mission_status = cmd_mission_status
    ctx.cmd_status = cmd_status

    # --- Dispatch via rules table ---
    intent = "unknown"
    rc = 1

    # Special pre-check: phone_place_call requires a number in the text
    # to avoid false positives on "call" in other contexts.
    _phone_place_call_has_number = (
        (lowered.startswith("call ") or "place a call" in lowered
         or "make a call" in lowered or "phone call" in lowered)
        and _extract_first_phone_number(text)
    )

    for matcher, handler in _DISPATCH_RULES:
        # The phone_place_call matcher needs the additional number check
        if handler is _handle_phone_place_call:
            if not _phone_place_call_has_number:
                continue
        if matcher(lowered):
            intent, rc = handler(ctx)
            break
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
        # Runs in a daemon thread to avoid blocking the HTTP response -- the
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
