"""Command dataclasses for security / phone / persona operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeControlCommand:
    pause: bool = False
    resume: bool = False
    safe_on: bool = False
    safe_off: bool = False
    reset: bool = False
    reason: str = ""


@dataclass
class RuntimeControlResult:
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OwnerGuardCommand:
    enable: bool = False
    disable: bool = False
    owner_user: str = ""
    trust_device: str = ""
    revoke_device: str = ""
    set_master_password_value: str = ""
    clear_master_password_value: bool = False


@dataclass
class OwnerGuardResult:
    state: dict[str, Any] = field(default_factory=dict)
    return_code: int = 0


@dataclass(frozen=True)
class ConnectStatusCommand:
    pass


@dataclass
class ConnectStatusResult:
    statuses: list[Any] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    ready: int = 0
    pending: int = 0


@dataclass(frozen=True)
class ConnectGrantCommand:
    connector_id: str
    scopes: list[str] = field(default_factory=list)


@dataclass
class ConnectGrantResult:
    granted: dict[str, Any] = field(default_factory=dict)
    return_code: int = 0


@dataclass(frozen=True)
class ConnectBootstrapCommand:
    auto_open: bool = False


@dataclass
class ConnectBootstrapResult:
    prompts: list[dict[str, Any]] = field(default_factory=list)
    ready: bool = False


@dataclass(frozen=True)
class PhoneActionCommand:
    action: str
    number: str = ""
    message: str = ""
    queue_path: Path = Path("phone_actions.jsonl")
    queue_action: bool = True


@dataclass
class PhoneActionResult:
    record: Any = None
    return_code: int = 0


@dataclass(frozen=True)
class PhoneSpamGuardCommand:
    call_log_path: Path
    report_path: Path
    queue_path: Path
    threshold: float = 0.65
    queue_actions: bool = True


@dataclass
class PhoneSpamGuardResult:
    candidates_count: int = 0
    queued_actions_count: int = 0
    return_code: int = 0


@dataclass(frozen=True)
class PersonaConfigCommand:
    enable: bool = False
    disable: bool = False
    humor_level: int | None = None
    mode: str = ""
    style: str = ""


@dataclass
class PersonaConfigResult:
    config: Any = None
