"""Voice intent routing — the large if/elif dispatch for voice commands.

Split from voice_pipeline.py for separation of concerns.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

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
