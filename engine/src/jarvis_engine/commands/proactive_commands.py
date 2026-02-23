"""Command dataclasses for proactive intelligence and wake word detection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProactiveCheckCommand:
    """Manually trigger proactive evaluation against snapshot data."""

    snapshot_path: str = ""


@dataclass
class ProactiveCheckResult:
    alerts_fired: int = 0
    alerts: str = "[]"
    message: str = ""


@dataclass(frozen=True)
class WakeWordStartCommand:
    """Start wake word detection."""

    threshold: float = 0.5


@dataclass
class WakeWordStartResult:
    started: bool = False
    message: str = ""
