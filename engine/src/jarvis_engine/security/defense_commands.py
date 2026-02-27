"""CQRS command dataclasses for the security defense system -- Wave 13.

Frozen dataclasses for security status, threat reports, forensic export,
containment overrides, IP blocking, quarantine review, and briefings.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Security Status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecurityStatusCommand:
    """Request the current security dashboard."""


@dataclass(frozen=True)
class SecurityStatusResult:
    """Result containing the defense dashboard and summary."""

    dashboard: dict = field(default_factory=dict)
    message: str = ""


# ---------------------------------------------------------------------------
# Threat Report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThreatReportCommand:
    """Request a threat report, optionally filtered by IP."""

    ip: str | None = None


@dataclass(frozen=True)
class ThreatReportResult:
    """Result containing the threat report."""

    report: dict = field(default_factory=dict)
    message: str = ""


# ---------------------------------------------------------------------------
# Forensic Export
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportForensicsCommand:
    """Request export of forensic log data for a date range."""

    start_date: str = ""
    end_date: str = ""


@dataclass(frozen=True)
class ExportForensicsResult:
    """Result containing the export file path."""

    export_path: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Containment Override
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContainmentOverrideCommand:
    """Override containment level or initiate recovery."""

    level: int = 0
    action: str = "recover"


@dataclass(frozen=True)
class ContainmentOverrideResult:
    """Result of a containment override operation."""

    success: bool = False
    message: str = ""


# ---------------------------------------------------------------------------
# IP Blocking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockIPCommand:
    """Block an IP address for a specified duration."""

    ip: str = ""
    duration_hours: int = 24


@dataclass(frozen=True)
class BlockIPResult:
    """Result of an IP block operation."""

    success: bool = False
    message: str = ""


@dataclass(frozen=True)
class UnblockIPCommand:
    """Unblock a previously blocked IP address."""

    ip: str = ""


@dataclass(frozen=True)
class UnblockIPResult:
    """Result of an IP unblock operation."""

    success: bool = False
    message: str = ""


# ---------------------------------------------------------------------------
# Quarantine Review
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewQuarantineCommand:
    """Request a review of quarantined memory records."""


@dataclass(frozen=True)
class ReviewQuarantineResult:
    """Result containing quarantined records."""

    records: list = field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Security Briefing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecurityBriefingCommand:
    """Request a human-readable security briefing."""


@dataclass(frozen=True)
class SecurityBriefingResult:
    """Result containing the briefing text."""

    briefing: str = ""
    message: str = ""
