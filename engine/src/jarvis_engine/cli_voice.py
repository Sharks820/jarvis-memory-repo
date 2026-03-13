"""Voice-related CLI command handlers.

Extracted from main.py to improve file health and separation of concerns.
Contains: voice-list, voice-say, voice-enroll, voice-verify, voice-listen,
voice-run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jarvis_engine._bus import get_bus as _get_bus
from jarvis_engine._cli_helpers import cli_dispatch as _dispatch
from jarvis_engine._constants import ACTIONS_FILENAME as _ACTIONS_FILENAME
from jarvis_engine._constants import OPS_SNAPSHOT_FILENAME as _OPS_SNAPSHOT_FILENAME
from jarvis_engine.config import repo_root
from jarvis_engine.voice.extractors import shorten_urls_for_speech
from jarvis_engine.stt.contracts import VoiceUtterance

from jarvis_engine.commands.voice_commands import (
    VoiceEnrollCommand,
    VoiceListCommand,
    VoiceListenCommand,
    VoiceRunCommand,
    VoiceSayCommand,
    VoiceVerifyCommand,
)
from jarvis_engine.commands.proactive_commands import WakeWordStartCommand

logger = logging.getLogger(__name__)


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
    profile: str = "jarvis_like",
    voice_pattern: str = "",
    output_wav: str = "",
    rate: int = -1,
) -> int:
    speakable_text = shorten_urls_for_speech(text)
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

        payload: dict[str, object] = {"state": state}
        if details:
            payload.update(details)
        log_activity(
            ActivityCategory.VOICE,
            f"Voice listen state: {state}",
            payload,
        )
    except (ImportError, OSError, RuntimeError, ValueError, TypeError) as exc:
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
            utterance_mode="command" if execute else "conversation",
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
            utterance=result.utterance,
            execute=True,
            approve_privileged=False,
            speak=False,
            snapshot_path=Path(repo_root() / ".planning" / _OPS_SNAPSHOT_FILENAME),
            actions_path=Path(repo_root() / ".planning" / _ACTIONS_FILENAME),
            voice_user="conner",
            voice_auth_wav="",
            voice_threshold=0.82,
            master_password="",
        )

    _emit_voice_listen_state("idle", details={"reason": "transcription_complete", "confidence": result.confidence})
    return 0


def cmd_voice_run(
    text: str,
    *,
    utterance: VoiceUtterance | None = None,
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
        text=text, utterance=utterance, execute=execute, approve_privileged=approve_privileged,
        speak=speak, snapshot_path=snapshot_path, actions_path=actions_path,
        voice_user=voice_user, voice_auth_wav=voice_auth_wav,
        voice_threshold=voice_threshold, master_password=master_password,
        model_override=model_override,
        skip_voice_auth_guard=skip_voice_auth_guard,
    ))
    return result.return_code


def cmd_wake_word(threshold: float) -> int:
    import time

    result, _ = _dispatch(WakeWordStartCommand(threshold=threshold))
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
