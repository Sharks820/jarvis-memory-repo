"""CQRS handlers for security defense commands (Wave 13).

Each handler instantiates the relevant security module internally and
delegates to its public API.  Constructor signature is uniform:

    ``__init__(self, root: Path, db: sqlite3.Connection, write_lock: threading.Lock, log_dir: Path)``

This keeps all security infrastructure isolated behind the command bus.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from jarvis_engine._compat import UTC
from jarvis_engine.security.defense_commands import (
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

logger = logging.getLogger(__name__)


class _DefenseHandlerBase:
    """Shared dependency wiring for defense command handlers."""

    def __init__(
        self,
        root: Path,
        db: sqlite3.Connection,
        write_lock: threading.Lock,
        log_dir: Path,
    ) -> None:
        self._root = root
        self._db = db
        self._write_lock = write_lock
        self._log_dir = log_dir


# ---------------------------------------------------------------------------
# SecurityStatusHandler
# ---------------------------------------------------------------------------


class SecurityStatusHandler(_DefenseHandlerBase):
    """Return aggregate security dashboard via SecurityOrchestrator.status()."""

    def __init__(
        self,
        root: Path,
        db: sqlite3.Connection,
        write_lock: threading.Lock,
        log_dir: Path,
    ) -> None:
        super().__init__(root, db, write_lock, log_dir)
        self._orchestrator = None
        try:
            from jarvis_engine.security.orchestrator import SecurityOrchestrator

            self._orchestrator = SecurityOrchestrator(
                db=self._db,
                write_lock=self._write_lock,
                log_dir=self._log_dir,
            )
        except Exception as exc:
            logger.warning("SecurityOrchestrator init failed, will retry per-call: %s", exc)

    def handle(self, cmd: SecurityStatusCommand) -> SecurityStatusResult:
        try:
            orch = self._orchestrator
            if orch is None:
                from jarvis_engine.security.orchestrator import SecurityOrchestrator

                orch = SecurityOrchestrator(
                    db=self._db,
                    write_lock=self._write_lock,
                    log_dir=self._log_dir,
                )
                self._orchestrator = orch
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
            from jarvis_engine.security.ip_tracker import IPTracker

            tracker = IPTracker(self._db, self._write_lock)

            if cmd.ip:
                report = tracker.get_threat_report(cmd.ip)
                if report is None:
                    return ThreatReportResult(
                        report={},
                        message=f"No threat data found for IP {cmd.ip}.",
                    )
                return ThreatReportResult(
                    report=report,
                    message=f"Threat report for {cmd.ip} retrieved.",
                )
            else:
                all_threats = tracker.get_all_threats(min_score=0.0)
                return ThreatReportResult(
                    report={
                        "total_tracked": len(all_threats),
                        "threats": all_threats,
                    },
                    message=f"Found {len(all_threats)} tracked IP(s).",
                )
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
            from jarvis_engine.security.forensic_logger import ForensicLogger

            fl = ForensicLogger(self._log_dir)

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
    """Override containment level or initiate recovery."""

    def handle(self, cmd: ContainmentOverrideCommand) -> ContainmentOverrideResult:
        try:
            from jarvis_engine.security.containment import ContainmentEngine
            from jarvis_engine.security.forensic_logger import ForensicLogger
            from jarvis_engine.security.ip_tracker import IPTracker

            fl = ForensicLogger(self._log_dir)
            ip_tracker = IPTracker(self._db, self._write_lock)
            engine = ContainmentEngine(
                forensic_logger=fl,
                ip_tracker=ip_tracker,
            )

            if cmd.action == "recover":
                result = engine.recover(
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
                result = engine.contain(
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
    """Manually block an IP address."""

    def handle(self, cmd: BlockIPCommand) -> BlockIPResult:
        if not cmd.ip.strip():
            return BlockIPResult(
                success=False,
                message="IP address must not be empty.",
            )
        try:
            from jarvis_engine.security.ip_tracker import IPTracker

            tracker = IPTracker(self._db, self._write_lock)
            duration = cmd.duration_hours if cmd.duration_hours > 0 else None
            tracker.block_ip(cmd.ip, duration_hours=duration)
            duration_str = f"{cmd.duration_hours}h" if cmd.duration_hours > 0 else "permanent"
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
    """Manually unblock an IP address."""

    def handle(self, cmd: UnblockIPCommand) -> UnblockIPResult:
        if not cmd.ip.strip():
            return UnblockIPResult(
                success=False,
                message="IP address must not be empty.",
            )
        try:
            from jarvis_engine.security.ip_tracker import IPTracker

            tracker = IPTracker(self._db, self._write_lock)
            tracker.unblock_ip(cmd.ip)
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
    """Return quarantined memory records from MemoryProvenance."""

    def handle(self, cmd: ReviewQuarantineCommand) -> ReviewQuarantineResult:
        try:
            from jarvis_engine.security.memory_provenance import MemoryProvenance

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
    """Return a human-readable security briefing."""

    def handle(self, cmd: SecurityBriefingCommand) -> SecurityBriefingResult:
        try:
            from jarvis_engine.security.adaptive_defense import AdaptiveDefenseEngine
            from jarvis_engine.security.attack_memory import AttackPatternMemory
            from jarvis_engine.security.ip_tracker import IPTracker

            ip_tracker = IPTracker(self._db, self._write_lock)
            attack_memory = AttackPatternMemory(self._db, self._write_lock)
            adaptive = AdaptiveDefenseEngine(
                attack_memory=attack_memory,
                ip_tracker=ip_tracker,
            )
            briefing = adaptive.generate_briefing()
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
