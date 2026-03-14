"""Security / phone / persona handler classes -- adapter shims."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from jarvis_engine._shared import check_path_within_root

from jarvis_engine.commands.security_commands import (
    ConnectBootstrapCommand,
    ConnectBootstrapResult,
    ConnectGrantCommand,
    ConnectGrantResult,
    ConnectStatusCommand,
    ConnectStatusResult,
    OwnerGuardCommand,
    OwnerGuardResult,
    PersonaConfigCommand,
    PersonaConfigResult,
    PhoneActionCommand,
    PhoneActionResult,
    PhoneSpamGuardCommand,
    PhoneSpamGuardResult,
    RuntimeControlCommand,
    RuntimeControlResult,
)

logger = logging.getLogger(__name__)


class _SecurityHandlerBase:
    """Shared base for security handler classes.

    All security handlers receive a *root* path in their constructor and
    store it as ``self._root``.  This base class eliminates the repeated
    ``__init__`` boilerplate.
    """

    def __init__(self, root: Path) -> None:
        self._root = root


class RuntimeControlHandler(_SecurityHandlerBase):
    def handle(self, cmd: RuntimeControlCommand) -> RuntimeControlResult:
        from jarvis_engine.ops.runtime_control import (
            read_control_state,
            reset_control_state,
            write_control_state,
        )

        # Reject conflicting flags
        if cmd.pause and cmd.resume:
            return RuntimeControlResult(
                state={"error": "Cannot pause and resume simultaneously."}
            )
        if cmd.safe_on and cmd.safe_off:
            return RuntimeControlResult(
                state={"error": "Cannot enable and disable safe mode simultaneously."}
            )

        if cmd.reset:
            state = reset_control_state(self._root)
        else:
            updates: dict[str, bool | None] = {"daemon_paused": None, "safe_mode": None}
            if cmd.pause:
                updates["daemon_paused"] = True
            if cmd.resume:
                updates["daemon_paused"] = False
            if cmd.safe_on:
                updates["safe_mode"] = True
            if cmd.safe_off:
                updates["safe_mode"] = False
            if updates["daemon_paused"] is not None or updates["safe_mode"] is not None:
                state = write_control_state(
                    self._root,
                    daemon_paused=updates["daemon_paused"],
                    safe_mode=updates["safe_mode"],
                    reason=cmd.reason,
                )
            else:
                state = read_control_state(self._root)
        return RuntimeControlResult(state=dict(state))


class OwnerGuardHandler(_SecurityHandlerBase):
    def handle(self, cmd: OwnerGuardCommand) -> OwnerGuardResult:
        from jarvis_engine.security.owner_guard import (
            clear_master_password,
            read_owner_guard,
            revoke_mobile_device,
            set_master_password,
            trust_mobile_device,
            write_owner_guard,
        )

        try:
            if cmd.set_master_password_value.strip():
                state = set_master_password(
                    self._root, cmd.set_master_password_value.strip()
                )
            elif cmd.clear_master_password_value:
                state = clear_master_password(self._root)
            elif cmd.trust_device.strip():
                state = trust_mobile_device(self._root, cmd.trust_device.strip())
            elif cmd.revoke_device.strip():
                state = revoke_mobile_device(self._root, cmd.revoke_device.strip())
            elif cmd.enable:
                if not cmd.owner_user.strip():
                    return OwnerGuardResult(return_code=2)
                state = write_owner_guard(
                    self._root, enabled=True, owner_user_id=cmd.owner_user.strip()
                )
            elif cmd.disable:
                state = write_owner_guard(self._root, enabled=False)
            elif cmd.owner_user.strip():
                state = write_owner_guard(
                    self._root, owner_user_id=cmd.owner_user.strip()
                )
            else:
                state = read_owner_guard(self._root)
        except ValueError as exc:
            logger.warning("OwnerGuard operation failed: %s", exc)
            return OwnerGuardResult(return_code=2)
        return OwnerGuardResult(state=dict(state), return_code=0)


class ConnectStatusHandler(_SecurityHandlerBase):
    def handle(self, cmd: ConnectStatusCommand) -> ConnectStatusResult:
        from jarvis_engine.ops.connectors import (
            build_connector_prompts,
            evaluate_connector_statuses,
        )

        statuses = evaluate_connector_statuses(self._root)
        prompts = build_connector_prompts(statuses)
        ready = sum(1 for s in statuses if s.ready)
        return ConnectStatusResult(
            statuses=statuses,
            prompts=prompts,
            ready=ready,
            pending=len(statuses) - ready,
        )


class ConnectGrantHandler(_SecurityHandlerBase):
    def handle(self, cmd: ConnectGrantCommand) -> ConnectGrantResult:
        from jarvis_engine.ops.connectors import grant_connector_permission

        try:
            granted = grant_connector_permission(
                self._root,
                connector_id=cmd.connector_id,
                scopes=cmd.scopes,
            )
        except ValueError as exc:
            logger.warning("ConnectGrant permission failed: %s", exc)
            return ConnectGrantResult(return_code=2)
        return ConnectGrantResult(granted=granted, return_code=0)


class ConnectBootstrapHandler(_SecurityHandlerBase):
    def handle(self, cmd: ConnectBootstrapCommand) -> ConnectBootstrapResult:
        import webbrowser

        from jarvis_engine.ops.connectors import (
            build_connector_prompts,
            evaluate_connector_statuses,
        )

        statuses = evaluate_connector_statuses(self._root)
        prompts = build_connector_prompts(statuses)
        if not prompts:
            return ConnectBootstrapResult(prompts=[], ready=True)
        if cmd.auto_open:
            for prompt in prompts:
                url = prompt.get("option_tap_url", "").strip()
                if url and re.match(r"^https?://", url, re.IGNORECASE):
                    webbrowser.open(url)
        return ConnectBootstrapResult(prompts=prompts, ready=False)


class PhoneActionHandler(_SecurityHandlerBase):
    def handle(self, cmd: PhoneActionCommand) -> PhoneActionResult:
        from jarvis_engine.phone.guard import append_phone_actions, build_phone_action

        try:
            record = build_phone_action(
                action=cmd.action,
                number=cmd.number,
                message=cmd.message,
                reason="manual_or_voice_request",
            )
        except ValueError as exc:
            logger.warning("PhoneAction build failed: %s", exc)
            return PhoneActionResult(return_code=2)
        if cmd.queue_action:
            try:
                check_path_within_root(cmd.queue_path, self._root, "queue_path")
            except ValueError as exc:
                logger.warning("PhoneAction queue path check failed: %s", exc)
                return PhoneActionResult(return_code=2)
            append_phone_actions(cmd.queue_path, [record])
        return PhoneActionResult(record=record, return_code=0)


class PhoneSpamGuardHandler(_SecurityHandlerBase):
    def handle(self, cmd: PhoneSpamGuardCommand) -> PhoneSpamGuardResult:
        from jarvis_engine.phone.guard import (
            append_phone_actions,
            build_spam_block_actions,
            detect_spam_candidates,
            load_call_log,
            write_spam_report,
        )

        try:
            check_path_within_root(cmd.call_log_path, self._root, "call_log_path")
            check_path_within_root(cmd.report_path, self._root, "report_path")
            check_path_within_root(cmd.queue_path, self._root, "queue_path")
        except ValueError as exc:
            logger.warning("PhoneSpamGuard path check failed: %s", exc)
            return PhoneSpamGuardResult(return_code=2)
        if not cmd.call_log_path.exists():
            return PhoneSpamGuardResult(return_code=2)
        try:
            call_log = load_call_log(cmd.call_log_path)
        except json.JSONDecodeError as exc:
            logger.warning("PhoneSpamGuard call log parse failed: %s", exc)
            return PhoneSpamGuardResult(return_code=2)
        candidates = detect_spam_candidates(call_log)
        actions = build_spam_block_actions(
            candidates, threshold=cmd.threshold, add_global_silence_rule=True
        )
        write_spam_report(cmd.report_path, candidates, actions, cmd.threshold)
        if actions and cmd.queue_actions:
            append_phone_actions(cmd.queue_path, actions)
        return PhoneSpamGuardResult(
            candidates_count=len(candidates),
            queued_actions_count=len(actions) if cmd.queue_actions else 0,
            return_code=0,
        )


class PersonaConfigHandler(_SecurityHandlerBase):
    def handle(self, cmd: PersonaConfigCommand) -> PersonaConfigResult:
        from jarvis_engine.memory.persona import load_persona_config, save_persona_config

        # Reject conflicting flags
        if cmd.enable and cmd.disable:
            return PersonaConfigResult(
                config={"error": "Cannot enable and disable persona simultaneously."}
            )

        enabled_opt: bool | None = None
        if cmd.enable:
            enabled_opt = True
        elif cmd.disable:
            enabled_opt = False

        mode_val = cmd.mode.strip() or None
        style_val = cmd.style.strip() or None

        if (
            enabled_opt is not None
            or cmd.humor_level is not None
            or mode_val
            or style_val
        ):
            cfg = save_persona_config(
                self._root,
                enabled=enabled_opt,
                humor_level=cmd.humor_level,
                mode=mode_val,
                style=style_val,
            )
        else:
            cfg = load_persona_config(self._root)
        return PersonaConfigResult(config=cfg)
