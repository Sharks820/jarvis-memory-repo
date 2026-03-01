"""Voice handler classes -- adapter shims delegating to existing functions."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from jarvis_engine.commands.voice_commands import (
    PersonaComposeCommand,
    PersonaComposeResult,
    VoiceEnrollCommand,
    VoiceEnrollResult,
    VoiceListCommand,
    VoiceListenCommand,
    VoiceListenResult,
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
        try:
            from jarvis_engine.voice import list_edge_voices, list_windows_voices
        except ImportError as exc:
            logger.warning("voice module not available: %s", exc)
            return VoiceListResult()

        voices = list_windows_voices()
        edge_voices = [name for name in list_edge_voices() if name.lower().startswith("en-gb-")]
        return VoiceListResult(windows_voices=voices, edge_voices=edge_voices)


class VoiceSayHandler:
    def __init__(self, root: Path) -> None:
        self._root = root

    def handle(self, cmd: VoiceSayCommand) -> VoiceSayResult:
        try:
            from jarvis_engine.voice import speak_text
        except ImportError as exc:
            logger.warning("voice module not available: %s", exc)
            return VoiceSayResult(message="error: voice module not available.")

        try:
            result = speak_text(
                text=cmd.text,
                profile=cmd.profile,
                custom_voice_pattern=cmd.voice_pattern,
                output_wav=cmd.output_wav,
                rate=cmd.rate,
            )
        except Exception as exc:
            logger.error("TTS speak_text failed: %s", exc, exc_info=True)
            return VoiceSayResult(message="error: TTS failed.")
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
        except (ValueError, OSError):
            return VoiceEnrollResult(message="error: voice enrollment failed.")
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
        except (ValueError, OSError):
            return VoiceVerifyResult(message="error: voice verification failed.")
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


class VoiceListenHandler:
    """Capture microphone audio and transcribe via faster-whisper."""

    def __init__(self, root: Path, gateway: object | None = None) -> None:
        self._root = root
        self._gateway = gateway

    def handle(self, cmd: VoiceListenCommand) -> VoiceListenResult:
        try:
            from jarvis_engine.stt import listen_and_transcribe
        except ImportError:
            return VoiceListenResult(message="error: STT module not available.")

        try:
            result = listen_and_transcribe(
                max_duration_seconds=cmd.max_duration_seconds,
                language=cmd.language,
                root_dir=self._root,
                gateway=self._gateway,
            )
        except Exception as exc:
            logger.debug("Voice listen failed: %s", exc)
            return VoiceListenResult(message="error: voice listen failed.")

        return VoiceListenResult(
            text=result.text,
            confidence=result.confidence,
            duration_seconds=result.duration_seconds,
        )


class PersonaComposeHandler:
    """Compose a personality-aware LLM response via the Intelligence Gateway."""

    def __init__(self, root: Path, gateway: object | None = None) -> None:
        self._root = root
        self._gateway = gateway

    def handle(self, cmd: PersonaComposeCommand) -> PersonaComposeResult:
        if self._gateway is None:
            return PersonaComposeResult(message="error: gateway not available")

        try:
            from jarvis_engine.persona import (
                _resolve_tone,
                compose_persona_system_prompt,
                load_persona_config,
            )
            from jarvis_engine.gateway.models import ModelGateway, GatewayResponse
        except ImportError as exc:
            logger.warning("persona/gateway modules not available: %s", exc)
            return PersonaComposeResult(message="error: persona modules not available.")

        gateway: ModelGateway = self._gateway  # type: ignore[assignment]
        cfg = load_persona_config(self._root)
        tone = _resolve_tone(cmd.branch)
        system_prompt = compose_persona_system_prompt(cfg, branch=cmd.branch)

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": cmd.query})

        model = cmd.model or "claude-sonnet-4-5-20250929"
        try:
            resp: GatewayResponse = gateway.complete(
                messages=messages,
                model=model,
                route_reason="persona_reply",
            )
        except (ConnectionError, OSError, RuntimeError) as exc:
            logger.debug("Persona compose failed: %s", exc)
            return PersonaComposeResult(
                branch=cmd.branch,
                tone=tone,
                message="error: persona composition failed.",
            )

        return PersonaComposeResult(
            text=resp.text,
            branch=cmd.branch,
            tone=tone,
        )


def _load_voice_auth_impl():  # type: ignore[no-untyped-def]
    try:
        from jarvis_engine.voice_auth import enroll_voiceprint, verify_voiceprint
    except ModuleNotFoundError as exc:
        return None, None, str(exc)
    return enroll_voiceprint, verify_voiceprint, ""
