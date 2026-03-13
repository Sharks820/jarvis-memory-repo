"""CQRS command dataclasses for the security defense system -- Wave 13.

Frozen dataclasses for security status, threat reports, forensic export,
containment overrides, IP blocking, quarantine review, and briefings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jarvis_engine.commands.base import ResultBase

if TYPE_CHECKING:
    from jarvis_engine.security.ip_tracker import ThreatReport
    from jarvis_engine.security.orchestrator import AllThreatsReport, SecurityStatus


# Security Status


@dataclass(frozen=True)
class SecurityStatusCommand:
    """Request the current security dashboard."""


@dataclass
class SecurityStatusResult(ResultBase):
    """Result containing the defense dashboard and summary."""

    dashboard: "SecurityStatus | dict[str, Any]" = field(default_factory=dict)


# Threat Report


@dataclass(frozen=True)
class ThreatReportCommand:
    """Request a threat report, optionally filtered by IP."""

    ip: str | None = None


@dataclass
class ThreatReportResult(ResultBase):
    """Result containing the threat report."""

    report: "ThreatReport | AllThreatsReport | dict[str, Any] | None" = field(default_factory=dict)


# Forensic Export


@dataclass(frozen=True)
class ExportForensicsCommand:
    """Request export of forensic log data for a date range."""

    start_date: str = ""
    end_date: str = ""


@dataclass
class ExportForensicsResult(ResultBase):
    """Result containing the export file path."""

    export_path: str = ""


# Containment Override


@dataclass(frozen=True)
class ContainmentOverrideCommand:
    """Override containment level or initiate recovery."""

    level: int = 0
    action: str = "recover"
    master_password: str = ""

    def __repr__(self) -> str:
        return (
            f"ContainmentOverrideCommand(level={self.level!r}, "
            f"action={self.action!r}, master_password='***')"
        )


@dataclass
class ContainmentOverrideResult(ResultBase):
    """Result of a containment override operation."""

    success: bool = False


# IP Blocking


@dataclass(frozen=True)
class BlockIPCommand:
    """Block an IP address for a specified duration."""

    ip: str = ""
    duration_hours: int = 24


@dataclass
class BlockIPResult(ResultBase):
    """Result of an IP block operation."""

    success: bool = False


@dataclass(frozen=True)
class UnblockIPCommand:
    """Unblock a previously blocked IP address."""

    ip: str = ""


@dataclass
class UnblockIPResult(ResultBase):
    """Result of an IP unblock operation."""

    success: bool = False


# Quarantine Review


@dataclass(frozen=True)
class ReviewQuarantineCommand:
    """Request a review of quarantined memory records."""


@dataclass
class ReviewQuarantineResult(ResultBase):
    """Result containing quarantined records."""

    records: list = field(default_factory=list)


# Security Briefing


@dataclass(frozen=True)
class SecurityBriefingCommand:
    """Request a human-readable security briefing."""


@dataclass
class SecurityBriefingResult(ResultBase):
    """Result containing the briefing text."""

    briefing: str = ""
