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
from typing import TYPE_CHECKING, Any, Callable, TypeVar, cast

from jarvis_engine._compat import UTC
from jarvis_engine._constants import SUBSYSTEM_ERRORS
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
    from jarvis_engine.security.orchestrator import SecurityOrchestrator
    from jarvis_engine.security.memory_provenance import MemoryProvenance

logger = logging.getLogger(__name__)

# Narrowed exception tuple shared by all defense command handlers.
# These are the only error families the security subsystem can raise
# during normal (non-catastrophic) operation.
_DEFENSE_HANDLER_ERRORS = (OSError, ValueError, RuntimeError, KeyError, TypeError)

_T = TypeVar("_T")

# Module-level singleton for shared SecurityOrchestrator and MemoryProvenance.
# Keyed by (id(db), id(write_lock), log_dir) so a different db/lock combo gets
# its own orchestrator (important for tests), but repeated calls with the same
# arguments reuse the existing instance.
_shared_orchestrator: "SecurityOrchestrator | None" = None
_shared_orchestrator_key: tuple | None = None
_shared_provenance: "MemoryProvenance | None" = None
_shared_orchestrator_lock = threading.Lock()


def _get_or_create_orchestrator(
    db: sqlite3.Connection,
    write_lock: threading.Lock,
    log_dir: Path,
) -> "SecurityOrchestrator | None":
    """Return the singleton ``SecurityOrchestrator``, creating it on first call.

    If called with different *db*/*write_lock*/*log_dir* arguments than the
    cached instance, creates a new one (handles test isolation).
    """
    global _shared_orchestrator, _shared_orchestrator_key, _shared_provenance
    key = (id(db), id(write_lock), str(log_dir))
    if _shared_orchestrator is not None and _shared_orchestrator_key == key:
        return _shared_orchestrator
    with _shared_orchestrator_lock:
        # Double-checked locking
        if _shared_orchestrator is not None and _shared_orchestrator_key == key:
            return _shared_orchestrator
        try:
            from jarvis_engine.security.orchestrator import SecurityOrchestrator

            orch = SecurityOrchestrator(
                db=db,
                write_lock=write_lock,
                log_dir=log_dir,
            )
            # Also create the shared MemoryProvenance
            if _shared_provenance is None:
                from jarvis_engine.security.memory_provenance import MemoryProvenance

                _shared_provenance = MemoryProvenance()
            _shared_orchestrator = orch
            _shared_orchestrator_key = key
            return orch
        except SUBSYSTEM_ERRORS as exc:
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

    def _safe_handle(
        self,
        action: Callable[[], _T],
        error_result_factory: Callable[[str], _T],
        handler_name: str,
    ) -> _T:
        """Run *action* wrapped in the standard defense error-handling envelope.

        On success returns the result of *action()*.  On failure logs the error
        and returns *error_result_factory(fail_message)*.
        """
        try:
            return action()
        except _DEFENSE_HANDLER_ERRORS as exc:
            logger.warning("%s failed: %s", handler_name, exc)
            return error_result_factory(str(exc))

    def _require_orchestrator(self, error_result_factory: Callable[[str], _T]) -> "SecurityOrchestrator | _T":
        """Return orchestrator or an error result if unavailable."""
        orch = self._ensure_orchestrator()
        if orch is None:
            return error_result_factory("Failed to initialize security orchestrator.")
        return orch

    @property
    def _forensic_logger(self) -> "ForensicLogger":
        if self._cached_forensic_logger is None:
            from jarvis_engine.security.forensic_logger import ForensicLogger

            self._cached_forensic_logger = ForensicLogger(self._log_dir)
        return self._cached_forensic_logger


# SecurityStatusHandler


class SecurityStatusHandler(_DefenseHandlerBase):
    """Return aggregate security dashboard via SecurityOrchestrator.status()."""

    def handle(self, cmd: SecurityStatusCommand) -> SecurityStatusResult:
        def _error(msg: str) -> SecurityStatusResult:
            return SecurityStatusResult(dashboard={"error": "internal_error"}, message=msg)

        def _action() -> SecurityStatusResult:
            orch = self._require_orchestrator(_error)
            if isinstance(orch, SecurityStatusResult):
                return orch
            return SecurityStatusResult(dashboard=orch.status(), message="Security status retrieved successfully.")

        return self._safe_handle(_action, _error, "SecurityStatusHandler")


# ThreatReportHandler


class ThreatReportHandler(_DefenseHandlerBase):
    """Return threat report for a specific IP or all tracked IPs."""

    def handle(self, cmd: ThreatReportCommand) -> ThreatReportResult:
        def _error(msg: str) -> ThreatReportResult:
            return ThreatReportResult(report={"error": "internal_error"}, message=msg)

        def _action() -> ThreatReportResult:
            orch = self._require_orchestrator(_error)
            if isinstance(orch, ThreatReportResult):
                return orch
            report = orch.get_threat_report(cmd.ip if cmd.ip else None)
            if cmd.ip and not report:
                return ThreatReportResult(report={}, message=f"No threat data found for IP {cmd.ip}.")
            if cmd.ip:
                return ThreatReportResult(report=report, message=f"Threat report for {cmd.ip} retrieved.")
            tracked_report = cast(dict[str, Any], report) if isinstance(report, dict) else {}
            return ThreatReportResult(
                report=tracked_report,
                message=f"Found {tracked_report.get('total_tracked', 0)} tracked IP(s).",
            )

        return self._safe_handle(_action, _error, "ThreatReportHandler")


# ExportForensicsHandler


class ExportForensicsHandler(_DefenseHandlerBase):
    """Export forensic log entries to a ZIP archive."""

    def handle(self, cmd: ExportForensicsCommand) -> ExportForensicsResult:
        def _error(msg: str) -> ExportForensicsResult:
            return ExportForensicsResult(export_path="", message=msg)

        def _action() -> ExportForensicsResult:
            fl = self._forensic_logger
            start_date = cmd.start_date or "2020-01-01"
            end_date = cmd.end_date or datetime.now(UTC).strftime("%Y-%m-%d")
            from jarvis_engine._shared import runtime_dir

            export_dir = runtime_dir(self._root) / "forensic_exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            output_path = export_dir / f"forensic_export_{timestamp}.zip"
            fl.export_for_law_enforcement(start_date, end_date, output_path)
            return ExportForensicsResult(export_path=str(output_path), message=f"Forensic logs exported to {output_path}.")

        return self._safe_handle(_action, _error, "ExportForensicsHandler")


# ContainmentOverrideHandler


class ContainmentOverrideHandler(_DefenseHandlerBase):
    """Override containment level or initiate recovery.

    Delegates to the shared ``SecurityOrchestrator`` instead of constructing
    a separate ``ContainmentEngine``.
    """

    def handle(self, cmd: ContainmentOverrideCommand) -> ContainmentOverrideResult:
        def _error(msg: str) -> ContainmentOverrideResult:
            return ContainmentOverrideResult(success=False, message=msg)

        def _action() -> ContainmentOverrideResult:
            orch = self._require_orchestrator(_error)
            if isinstance(orch, ContainmentOverrideResult):
                return orch
            if cmd.action == "recover":
                result = orch.recover(level=cmd.level, master_password=cmd.master_password or None)
                return ContainmentOverrideResult(
                    success=result.get("recovered", False),
                    message=result.get("reason", "Recovery complete.")
                    if not result.get("recovered", False)
                    else f"Recovery from level {cmd.level} completed.",
                )
            elif cmd.action == "contain":
                orch.contain(ip="0.0.0.0", level=cmd.level, reason="Manual containment override via command bus")
                return ContainmentOverrideResult(success=True, message=f"Containment level {cmd.level} activated.")
            else:
                return _error(f"Unknown action: {cmd.action!r}. Use 'recover' or 'contain'.")

        return self._safe_handle(_action, _error, "ContainmentOverrideHandler")


# BlockIPHandler


class BlockIPHandler(_DefenseHandlerBase):
    """Manually block an IP address via the shared orchestrator."""

    def handle(self, cmd: BlockIPCommand) -> BlockIPResult:
        if not cmd.ip.strip():
            return BlockIPResult(success=False, message="IP address must not be empty.")

        def _error(msg: str) -> BlockIPResult:
            return BlockIPResult(success=False, message=msg)

        def _action() -> BlockIPResult:
            orch = self._require_orchestrator(_error)
            if isinstance(orch, BlockIPResult):
                return orch
            duration = cmd.duration_hours if cmd.duration_hours > 0 else None
            orch.block_ip(cmd.ip, duration_hours=duration)
            duration_str = f"{cmd.duration_hours}h" if cmd.duration_hours > 0 else "permanent"
            return BlockIPResult(success=True, message=f"IP {cmd.ip} blocked for {duration_str}.")

        return self._safe_handle(_action, _error, "BlockIPHandler")


# UnblockIPHandler


class UnblockIPHandler(_DefenseHandlerBase):
    """Manually unblock an IP address via the shared orchestrator."""

    def handle(self, cmd: UnblockIPCommand) -> UnblockIPResult:
        if not cmd.ip.strip():
            return UnblockIPResult(success=False, message="IP address must not be empty.")

        def _error(msg: str) -> UnblockIPResult:
            return UnblockIPResult(success=False, message=msg)

        def _action() -> UnblockIPResult:
            orch = self._require_orchestrator(_error)
            if isinstance(orch, UnblockIPResult):
                return orch
            orch.unblock_ip(cmd.ip)
            return UnblockIPResult(success=True, message=f"IP {cmd.ip} unblocked.")

        return self._safe_handle(_action, _error, "UnblockIPHandler")


# ReviewQuarantineHandler


class ReviewQuarantineHandler(_DefenseHandlerBase):
    """Return quarantined memory records from MemoryProvenance.

    Uses the module-level shared ``MemoryProvenance`` singleton rather
    than creating a fresh (empty) one each call.
    """

    def handle(self, cmd: ReviewQuarantineCommand) -> ReviewQuarantineResult:
        def _error(msg: str) -> ReviewQuarantineResult:
            return ReviewQuarantineResult(records=[], message=msg)

        def _action() -> ReviewQuarantineResult:
            global _shared_provenance
            provenance = _shared_provenance
            if provenance is None:
                orch = self._ensure_orchestrator()
                if orch is not None and hasattr(orch, "memory_provenance"):
                    provenance = orch.memory_provenance
            if provenance is None:
                from jarvis_engine.security.memory_provenance import MemoryProvenance

                with _shared_orchestrator_lock:
                    if _shared_provenance is None:
                        _shared_provenance = MemoryProvenance()
                    provenance = _shared_provenance
            records = provenance.get_quarantined(limit=50)
            return ReviewQuarantineResult(records=records, message=f"{len(records)} quarantined record(s) found.")

        return self._safe_handle(_action, _error, "ReviewQuarantineHandler")


# SecurityBriefingHandler


class SecurityBriefingHandler(_DefenseHandlerBase):
    """Return a human-readable security briefing.

    Delegates to the shared ``SecurityOrchestrator.generate_briefing()``
    instead of constructing separate ``AttackPatternMemory`` and
    ``AdaptiveDefenseEngine`` instances.
    """

    def handle(self, cmd: SecurityBriefingCommand) -> SecurityBriefingResult:
        def _error(msg: str) -> SecurityBriefingResult:
            return SecurityBriefingResult(briefing="", message=msg)

        def _action() -> SecurityBriefingResult:
            orch = self._require_orchestrator(_error)
            if isinstance(orch, SecurityBriefingResult):
                return orch
            return SecurityBriefingResult(briefing=orch.generate_briefing(), message="Security briefing generated.")

        return self._safe_handle(_action, _error, "SecurityBriefingHandler")
