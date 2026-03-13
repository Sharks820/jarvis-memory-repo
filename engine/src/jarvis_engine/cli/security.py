"""Security, phone, and connector CLI command handlers.

Extracted from main.py to improve file health and separation of concerns.
Contains: owner-guard, connect-status/grant/bootstrap,
phone-action, phone-spam-guard.
"""

from __future__ import annotations

from pathlib import Path

from jarvis_engine._bus import get_bus as _get_bus

from jarvis_engine.commands.security_commands import (
    ConnectBootstrapCommand,
    ConnectGrantCommand,
    ConnectStatusCommand,
    OwnerGuardCommand,
    PhoneActionCommand,
    PhoneSpamGuardCommand,
)


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
