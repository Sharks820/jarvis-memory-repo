"""Command dataclasses for system operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StatusCommand:
    pass


@dataclass
class StatusResult:
    profile: str = ""
    primary_runtime: str = ""
    secondary_runtime: str = ""
    security_strictness: str = ""
    operation_mode: str = ""
    cloud_burst_enabled: bool = False
    events: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class LogCommand:
    event_type: str
    message: str


@dataclass
class LogResult:
    ts: str = ""
    event_type: str = ""
    message: str = ""


@dataclass(frozen=True)
class ServeMobileCommand:
    host: str = "127.0.0.1"
    port: int = 8787
    token: str | None = None
    signing_key: str | None = None


@dataclass
class ServeMobileResult:
    return_code: int = 0


@dataclass(frozen=True)
class DaemonRunCommand:
    interval_s: int = 180
    snapshot_path: Path = Path("ops_snapshot.live.json")
    actions_path: Path = Path("actions.generated.json")
    execute: bool = False
    approve_privileged: bool = False
    auto_open_connectors: bool = False
    max_cycles: int = 0
    idle_interval_s: int = 900
    idle_after_s: int = 300
    run_missions: bool = False
    sync_every_cycles: int = 5
    self_heal_every_cycles: int = 20


@dataclass
class DaemonRunResult:
    return_code: int = 0


@dataclass(frozen=True)
class MobileDesktopSyncCommand:
    auto_ingest: bool = False
    as_json: bool = False


@dataclass
class MobileDesktopSyncResult:
    report: dict[str, Any] = field(default_factory=dict)
    return_code: int = 0


@dataclass(frozen=True)
class SelfHealCommand:
    force_maintenance: bool = False
    keep_recent: int = 1800
    snapshot_note: str = "self-heal"
    as_json: bool = False


@dataclass
class SelfHealResult:
    report: dict[str, Any] = field(default_factory=dict)
    return_code: int = 0


@dataclass(frozen=True)
class DesktopWidgetCommand:
    pass


@dataclass
class DesktopWidgetResult:
    return_code: int = 0


@dataclass(frozen=True)
class GamingModeCommand:
    enable: bool | None = None
    reason: str = ""
    auto_detect: str = ""


@dataclass
class GamingModeResult:
    state: dict[str, Any] = field(default_factory=dict)
    detected: bool = False
    detected_process: str = ""
    effective_enabled: bool = False


@dataclass(frozen=True)
class OpenWebCommand:
    url: str


@dataclass
class OpenWebResult:
    return_code: int = 0
    opened_url: str = ""


@dataclass(frozen=True)
class WeatherCommand:
    location: str = ""


@dataclass
class WeatherResult:
    return_code: int = 0
    location: str = ""
    current: dict[str, Any] = field(default_factory=dict)
    description: str = ""
