"""Command dataclasses for security / phone / persona operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis_engine.commands.base import ResultBase

if TYPE_CHECKING:
    from jarvis_engine.connectors import ConnectorStatus
    from jarvis_engine.persona import PersonaConfig
    from jarvis_engine.phone_guard import PhoneAction


@dataclass(frozen=True)
class RuntimeControlCommand:
    pause: bool = False
    resume: bool = False
    safe_on: bool = False
    safe_off: bool = False
    reset: bool = False
    reason: str = ""


@dataclass
class RuntimeControlResult(ResultBase):
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

    def __repr__(self) -> str:
        return (
            f"OwnerGuardCommand(enable={self.enable!r}, disable={self.disable!r}, "
            f"owner_user={self.owner_user!r}, trust_device={self.trust_device!r}, "
            f"revoke_device={self.revoke_device!r}, "
            f"set_master_password_value='***', "
            f"clear_master_password_value={self.clear_master_password_value!r})"
        )


@dataclass
class OwnerGuardResult(ResultBase):
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectStatusCommand:
    pass


@dataclass
class ConnectStatusResult(ResultBase):
    statuses: list[ConnectorStatus] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    ready: int = 0
    pending: int = 0


@dataclass(frozen=True)
class ConnectGrantCommand:
    connector_id: str
    scopes: list[str] = field(default_factory=list)


@dataclass
class ConnectGrantResult(ResultBase):
    granted: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectBootstrapCommand:
    auto_open: bool = False


@dataclass
class ConnectBootstrapResult(ResultBase):
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
class PhoneActionResult(ResultBase):
    record: PhoneAction | None = None


@dataclass(frozen=True)
class PhoneSpamGuardCommand:
    call_log_path: Path
    report_path: Path
    queue_path: Path
    threshold: float = 0.65
    queue_actions: bool = True


@dataclass
class PhoneSpamGuardResult(ResultBase):
    candidates_count: int = 0
    queued_actions_count: int = 0


@dataclass(frozen=True)
class PersonaConfigCommand:
    enable: bool = False
    disable: bool = False
    humor_level: int | None = None
    mode: str = ""
    style: str = ""


@dataclass
class PersonaConfigResult(ResultBase):
    config: PersonaConfig | dict[str, str] | None = None
