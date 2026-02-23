"""Voice handler classes -- adapter shims delegating to existing functions."""

from __future__ import annotations

from pathlib import Path

from jarvis_engine.commands.voice_commands import (
    VoiceEnrollCommand,
    VoiceEnrollResult,
    VoiceListCommand,
    VoiceListResult,
    VoiceRunCommand,
    VoiceRunResult,
    VoiceSayCommand,
    VoiceSayResult,
    VoiceVerifyCommand,
    VoiceVerifyResult,
)


class VoiceListHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: VoiceListCommand) -> VoiceListResult:
        from jarvis_engine.voice import list_edge_voices, list_windows_voices

        voices = list_windows_voices()
        edge_voices = [name for name in list_edge_voices() if name.lower().startswith("en-gb-")]
        return VoiceListResult(windows_voices=voices, edge_voices=edge_voices)


class VoiceSayHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: VoiceSayCommand) -> VoiceSayResult:
        from jarvis_engine.voice import speak_text

        result = speak_text(
            text=cmd.text,
            profile=cmd.profile,
            custom_voice_pattern=cmd.voice_pattern,
            output_wav=cmd.output_wav,
            rate=cmd.rate,
        )
        return VoiceSayResult(
            voice_name=result.voice_name,
            output_wav=result.output_wav,
            message=result.message,
        )


class VoiceEnrollHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: VoiceEnrollCommand) -> VoiceEnrollResult:
        enroll_impl, _, err = _load_voice_auth_impl()
        if enroll_impl is None:
            return VoiceEnrollResult(message=f"error: voice auth dependency missing ({err}). Install numpy/scipy and retry.")
        try:
            result = enroll_impl(
                self._root,
                user_id=cmd.user_id,
                wav_path=cmd.wav_path,
                replace=cmd.replace,
            )
        except (ValueError, OSError) as exc:
            return VoiceEnrollResult(message=f"error: {exc}")
        return VoiceEnrollResult(
            user_id=result.user_id,
            profile_path=result.profile_path,
            samples=result.samples,
            message=result.message,
        )


class VoiceVerifyHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: VoiceVerifyCommand) -> VoiceVerifyResult:
        _, verify_impl, err = _load_voice_auth_impl()
        if verify_impl is None:
            return VoiceVerifyResult(message=f"error: voice auth dependency missing ({err}). Install numpy/scipy and retry.")
        try:
            result = verify_impl(
                self._root,
                user_id=cmd.user_id,
                wav_path=cmd.wav_path,
                threshold=cmd.threshold,
            )
        except (ValueError, OSError) as exc:
            return VoiceVerifyResult(message=f"error: {exc}")
        return VoiceVerifyResult(
            user_id=result.user_id,
            score=result.score,
            threshold=result.threshold,
            matched=result.matched,
            message=result.message,
        )


class VoiceRunHandler:
    """Delegates to existing cmd_voice_run in main.py.

    The voice-run command is deeply integrated with other cmd_* functions and
    contains complex branching logic.  The handler simply calls the existing
    function to guarantee zero-regression.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: VoiceRunCommand) -> VoiceRunResult:
        # Voice-run is extremely complex (intent routing, auth checks, etc.)
        # We import and call the original function directly to guarantee
        # identical behaviour.  The cmd_voice_run function will internally
        # call other cmd_* functions which may themselves dispatch through
        # the bus -- that is fine because the bus is module-level singleton.
        from jarvis_engine import main as _main_mod

        rc = _main_mod._cmd_voice_run_impl(
            text=cmd.text,
            execute=cmd.execute,
            approve_privileged=cmd.approve_privileged,
            speak=cmd.speak,
            snapshot_path=cmd.snapshot_path,
            actions_path=cmd.actions_path,
            voice_user=cmd.voice_user,
            voice_auth_wav=cmd.voice_auth_wav,
            voice_threshold=cmd.voice_threshold,
            master_password=cmd.master_password,
        )
        return VoiceRunResult(return_code=rc)


def _load_voice_auth_impl():  # type: ignore[no-untyped-def]
    try:
        from jarvis_engine.voice_auth import enroll_voiceprint, verify_voiceprint
    except ModuleNotFoundError as exc:
        return None, None, str(exc)
    return enroll_voiceprint, verify_voiceprint, ""
