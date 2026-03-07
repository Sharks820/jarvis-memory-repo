"""CQRS handlers for security defense commands (Wave 13).

Each handler delegates to a shared ``SecurityOrchestrator`` instance to avoid
duplicating threat-response logic (containment, attack memory, adaptive
defense).  The orchestrator is created once by the composition root and shared
across all defense handlers.

Constructor signature is uniform:

    ``__init__(self, root: Path, db: sqlite3.Connection, write_lock: threading.Lock, log_dir: Path)``

This keeps all security infrastructure isolated behind the command bus.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis_engine._compat import UTC
from jarvis_engine.commands.defense_commands import (
    BlockIPCommand,
    BlockIPResult,
    ContainmentOverrideCommand,
    ContainmentOverrideResult,
    ExportForensicsCommand,
    ExportForensicsResult,
    ReviewQuarantineCommand,
    ReviewQuarantineResult,
    SecurityBriefingCommand,
    SecurityBriefingResult,
    SecurityStatusCommand,
    SecurityStatusResult,
    ThreatReportCommand,
    ThreatReportResult,
    UnblockIPCommand,
    UnblockIPResult,
)

if TYPE_CHECKING:
    from jarvis_engine.security.forensic_logger import ForensicLogger
    from jarvis_engine.security.memory_provenance import MemoryProvenance
    from jarvis_engine.security.orchestrator import SecurityOrchestrator

logger = logging.getLogger(__name__)


def _get_or_create_orchestrator(
    db: sqlite3.Connection,
    write_lock: threading.Lock,
    log_dir: Path,
) -> "SecurityOrchestrator | None":
    """Create a ``SecurityOrchestrator`` with graceful degradation."""
    try:
        from jarvis_engine.security.orchestrator import SecurityOrchestrator

        return SecurityOrchestrator(
            db=db,
            write_lock=write_lock,
            log_dir=log_dir,
        )
    except Exception as exc:
        logger.warning("SecurityOrchestrator init failed: %s", exc)
        return None


class _DefenseHandlerBase:
    """Shared dependency wiring for defense command handlers.

    All handlers share a single ``SecurityOrchestrator`` to avoid duplicating
    threat-response infrastructure (containment engine, attack memory, adaptive
    defense, IP tracker, forensic logger).
    """

    def __init__(
        self,
        root: Path,
        db: sqlite3.Connection,
        write_lock: threading.Lock,
        log_dir: Path,
        orchestrator: "SecurityOrchestrator | None" = None,
    ) -> None:
        self._root = root
        self._db = db
        self._write_lock = write_lock
        self._log_dir = log_dir
        self._orchestrator = orchestrator
        self._cached_forensic_logger: "ForensicLogger | None" = None

    def _ensure_orchestrator(self) -> "SecurityOrchestrator | None":
        """Return the shared orchestrator, creating one if needed."""
        if self._orchestrator is None:
            self._orchestrator = _get_or_create_orchestrator(
                self._db,
                self._write_lock,
                self._log_dir,
            )
        return self._orchestrator

    @property
    def _forensic_logger(self):
        if self._cached_forensic_logger is None:
            from jarvis_engine.security.forensic_logger import ForensicLogger

            self._cached_forensic_logger = ForensicLogger(self._log_dir)
        return self._cached_forensic_logger


# ---------------------------------------------------------------------------
# SecurityStatusHandler
# ---------------------------------------------------------------------------


class SecurityStatusHandler(_DefenseHandlerBase):
    """Return aggregate security dashboard via SecurityOrchestrator.status()."""

    def handle(self, cmd: SecurityStatusCommand) -> SecurityStatusResult:
        try:
            orch = self._ensure_orchestrator()
            if orch is None:
                return SecurityStatusResult(
                    dashboard={"error": "orchestrator_unavailable"},
                    message="Failed to initialize security orchestrator.",
                )
            dashboard = orch.status()
            return SecurityStatusResult(
                dashboard=dashboard,
                message="Security status retrieved successfully.",
            )
        except Exception as exc:
            logger.warning("SecurityStatusHandler failed: %s", exc)
            return SecurityStatusResult(
                dashboard={"error": "internal_error"},
                message="Failed to retrieve security status.",
            )


# ---------------------------------------------------------------------------
# ThreatReportHandler
# ---------------------------------------------------------------------------


class ThreatReportHandler(_DefenseHandlerBase):
    """Return threat report for a specific IP or all tracked IPs."""

    def handle(self, cmd: ThreatReportCommand) -> ThreatReportResult:
        try:
            orch = self._ensure_orchestrator()
            if orch is None:
                return ThreatReportResult(
                    report={"error": "orchestrator_unavailable"},
                    message="Failed to initialize security orchestrator.",
                )

            report = orch.get_threat_report(cmd.ip if cmd.ip else None)
            if cmd.ip and not report:
                return ThreatReportResult(
                    report={},
                    message=f"No threat data found for IP {cmd.ip}.",
                )
            msg = (
                f"Threat report for {cmd.ip} retrieved."
                if cmd.ip
                else f"Found {report.get('total_tracked', 0)} tracked IP(s)."
            )
            return ThreatReportResult(report=report, message=msg)
        except Exception as exc:
            logger.warning("ThreatReportHandler failed: %s", exc)
            return ThreatReportResult(
                report={"error": "internal_error"},
                message="Failed to retrieve threat report.",
            )


# ---------------------------------------------------------------------------
# ExportForensicsHandler
# ---------------------------------------------------------------------------


class ExportForensicsHandler(_DefenseHandlerBase):
    """Export forensic log entries to a ZIP archive."""

    def handle(self, cmd: ExportForensicsCommand) -> ExportForensicsResult:
        try:
            fl = self._forensic_logger

            start_date = cmd.start_date or "2020-01-01"
            end_date = cmd.end_date or datetime.now(UTC).strftime("%Y-%m-%d")

            from jarvis_engine._constants import runtime_dir

            export_dir = runtime_dir(self._root) / "forensic_exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            output_path = export_dir / f"forensic_export_{timestamp}.zip"

            fl.export_for_law_enforcement(start_date, end_date, output_path)

            return ExportForensicsResult(
                export_path=str(output_path),
                message=f"Forensic logs exported to {output_path}.",
            )
        except Exception as exc:
            logger.warning("ExportForensicsHandler failed: %s", exc)
            return ExportForensicsResult(
                export_path="",
                message="Failed to export forensic logs.",
            )


# ---------------------------------------------------------------------------
# ContainmentOverrideHandler
# ---------------------------------------------------------------------------


class ContainmentOverrideHandler(_DefenseHandlerBase):
    """Override containment level or initiate recovery.

    Delegates to the shared ``SecurityOrchestrator`` instead of constructing
    a separate ``ContainmentEngine``.
    """

    def handle(self, cmd: ContainmentOverrideCommand) -> ContainmentOverrideResult:
        try:
            orch = self._ensure_orchestrator()
            if orch is None:
                return ContainmentOverrideResult(
                    success=False,
                    message="Failed to initialize security orchestrator.",
                )

            if cmd.action == "recover":
                result = orch.recover(
                    level=cmd.level,
                    master_password=cmd.master_password or None,
                )
                return ContainmentOverrideResult(
                    success=result.get("recovered", False),
                    message=result.get("reason", "Recovery complete.")
                    if not result.get("recovered", False)
                    else f"Recovery from level {cmd.level} completed.",
                )
            elif cmd.action == "contain":
                orch.contain(
                    ip="0.0.0.0",  # system-wide containment
                    level=cmd.level,
                    reason="Manual containment override via command bus",
                )
                return ContainmentOverrideResult(
                    success=True,
                    message=f"Containment level {cmd.level} activated.",
                )
            else:
                return ContainmentOverrideResult(
                    success=False,
                    message=f"Unknown action: {cmd.action!r}. Use 'recover' or 'contain'.",
                )
        except Exception as exc:
            logger.warning("ContainmentOverrideHandler failed: %s", exc)
            return ContainmentOverrideResult(
                success=False,
                message="Containment override failed.",
            )


# ---------------------------------------------------------------------------
# BlockIPHandler
# ---------------------------------------------------------------------------


class BlockIPHandler(_DefenseHandlerBase):
    """Manually block an IP address via the shared orchestrator."""

    def handle(self, cmd: BlockIPCommand) -> BlockIPResult:
        if not cmd.ip.strip():
            return BlockIPResult(
                success=False,
                message="IP address must not be empty.",
            )
        try:
            orch = self._ensure_orchestrator()
            if orch is None:
                return BlockIPResult(
                    success=False,
                    message="Failed to initialize security orchestrator.",
                )
            duration = cmd.duration_hours if cmd.duration_hours > 0 else None
            orch.block_ip(cmd.ip, duration_hours=duration)
            duration_str = (
                f"{cmd.duration_hours}h" if cmd.duration_hours > 0 else "permanent"
            )
            return BlockIPResult(
                success=True,
                message=f"IP {cmd.ip} blocked for {duration_str}.",
            )
        except Exception as exc:
            logger.warning("BlockIPHandler failed: %s", exc)
            return BlockIPResult(
                success=False,
                message="Failed to block IP.",
            )


# ---------------------------------------------------------------------------
# UnblockIPHandler
# ---------------------------------------------------------------------------


class UnblockIPHandler(_DefenseHandlerBase):
    """Manually unblock an IP address via the shared orchestrator."""

    def handle(self, cmd: UnblockIPCommand) -> UnblockIPResult:
        if not cmd.ip.strip():
            return UnblockIPResult(
                success=False,
                message="IP address must not be empty.",
            )
        try:
            orch = self._ensure_orchestrator()
            if orch is None:
                return UnblockIPResult(
                    success=False,
                    message="Failed to initialize security orchestrator.",
                )
            orch.unblock_ip(cmd.ip)
            return UnblockIPResult(
                success=True,
                message=f"IP {cmd.ip} unblocked.",
            )
        except Exception as exc:
            logger.warning("UnblockIPHandler failed: %s", exc)
            return UnblockIPResult(
                success=False,
                message="Failed to unblock IP.",
            )


# ---------------------------------------------------------------------------
# ReviewQuarantineHandler
# ---------------------------------------------------------------------------


class ReviewQuarantineHandler(_DefenseHandlerBase):
    """Return quarantined memory records from MemoryProvenance.

    Uses the shared ``MemoryProvenance`` instance from the bus/engine rather
    than creating a fresh (empty) one each call.
    """

    _shared_provenance: "MemoryProvenance | None" = None

    def handle(self, cmd: ReviewQuarantineCommand) -> ReviewQuarantineResult:
        try:
            provenance = self._shared_provenance
            if provenance is None:
                # Fallback: try to get it from the orchestrator
                orch = self._ensure_orchestrator()
                if orch is not None and hasattr(orch, "memory_provenance"):
                    provenance = orch.memory_provenance
            if provenance is None:
                # Last resort: create one, but warn that it will be empty
                from jarvis_engine.security.memory_provenance import MemoryProvenance

                logger.warning(
                    "ReviewQuarantineHandler: no shared MemoryProvenance available, "
                    "creating empty instance (quarantine data will not be visible)"
                )
                provenance = MemoryProvenance()
            records = provenance.get_quarantined(limit=50)
            return ReviewQuarantineResult(
                records=records,
                message=f"{len(records)} quarantined record(s) found.",
            )
        except Exception as exc:
            logger.warning("ReviewQuarantineHandler failed: %s", exc)
            return ReviewQuarantineResult(
                records=[],
                message="Failed to review quarantine.",
            )


# ---------------------------------------------------------------------------
# SecurityBriefingHandler
# ---------------------------------------------------------------------------


class SecurityBriefingHandler(_DefenseHandlerBase):
    """Return a human-readable security briefing.

    Delegates to the shared ``SecurityOrchestrator.generate_briefing()``
    instead of constructing separate ``AttackPatternMemory`` and
    ``AdaptiveDefenseEngine`` instances.
    """

    def handle(self, cmd: SecurityBriefingCommand) -> SecurityBriefingResult:
        try:
            orch = self._ensure_orchestrator()
            if orch is None:
                return SecurityBriefingResult(
                    briefing="",
                    message="Failed to initialize security orchestrator.",
                )
            briefing = orch.generate_briefing()
            return SecurityBriefingResult(
                briefing=briefing,
                message="Security briefing generated.",
            )
        except Exception as exc:
            logger.warning("SecurityBriefingHandler failed: %s", exc)
            return SecurityBriefingResult(
                briefing="",
                message="Failed to generate security briefing.",
            )
