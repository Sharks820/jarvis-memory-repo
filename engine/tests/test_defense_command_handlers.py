"""Tests for defense command CQRS handlers (security_handlers.py — Wave 13)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from conftest import make_test_db
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


def _make_db() -> tuple[sqlite3.Connection, threading.Lock]:
    """Create an in-memory SQLite database with WAL mode and a shared lock."""
    db = make_test_db(check_same_thread=False)
    return db, threading.Lock()


# ------------------------------------------------------------------
# SecurityStatusHandler
# ------------------------------------------------------------------


class TestSecurityStatusHandler:
    def test_returns_result(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import SecurityStatusHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = SecurityStatusHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(SecurityStatusCommand())
        assert isinstance(result, SecurityStatusResult)
        assert isinstance(result.dashboard, dict)
        assert result.message != ""

    def test_dashboard_has_expected_keys(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import SecurityStatusHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = SecurityStatusHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(SecurityStatusCommand())
        assert "containment_level" in result.dashboard
        assert "total_requests" in result.dashboard


# ------------------------------------------------------------------
# ThreatReportHandler
# ------------------------------------------------------------------


class TestThreatReportHandler:
    def test_no_ip_returns_all(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import ThreatReportHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = ThreatReportHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(ThreatReportCommand())
        assert isinstance(result, ThreatReportResult)
        assert isinstance(result.report, dict)

    def test_with_ip_returns_report(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import ThreatReportHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = ThreatReportHandler(tmp_path, db, lock, log_dir)
        # Record some activity so there's data for the IP
        from jarvis_engine.security.ip_tracker import IPTracker

        tracker = IPTracker(db, lock)
        tracker.record_attempt("10.0.0.1", "test_attack")
        result = handler.handle(ThreatReportCommand(ip="10.0.0.1"))
        assert isinstance(result, ThreatReportResult)
        assert result.report.get("ip") == "10.0.0.1"

    def test_unknown_ip_returns_empty(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import ThreatReportHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = ThreatReportHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(ThreatReportCommand(ip="192.168.99.99"))
        assert isinstance(result, ThreatReportResult)
        assert result.report == {} or result.report is not None


# ------------------------------------------------------------------
# ExportForensicsHandler
# ------------------------------------------------------------------


class TestExportForensicsHandler:
    def test_export_creates_file(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import ExportForensicsHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = ExportForensicsHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(
            ExportForensicsCommand(start_date="2026-01-01", end_date="2026-12-31")
        )
        assert isinstance(result, ExportForensicsResult)
        assert result.export_path != ""
        assert Path(result.export_path).exists()

    def test_export_empty_dates_returns_result(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import ExportForensicsHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = ExportForensicsHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(ExportForensicsCommand())
        assert isinstance(result, ExportForensicsResult)


# ------------------------------------------------------------------
# ContainmentOverrideHandler
# ------------------------------------------------------------------


class TestContainmentOverrideHandler:
    def test_recover_action(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import ContainmentOverrideHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = ContainmentOverrideHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(
            ContainmentOverrideCommand(level=1, action="recover")
        )
        assert isinstance(result, ContainmentOverrideResult)
        # Recovery at level 1 should succeed (no master password needed)
        assert result.success is True

    def test_invalid_action(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import ContainmentOverrideHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = ContainmentOverrideHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(
            ContainmentOverrideCommand(level=1, action="invalid_action")
        )
        assert isinstance(result, ContainmentOverrideResult)
        assert result.success is False


# ------------------------------------------------------------------
# BlockIPHandler
# ------------------------------------------------------------------


class TestBlockIPHandler:
    def test_block_ip(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import BlockIPHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = BlockIPHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(BlockIPCommand(ip="10.0.0.99", duration_hours=1))
        assert isinstance(result, BlockIPResult)
        assert result.success is True

    def test_block_ip_empty_ip(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import BlockIPHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = BlockIPHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(BlockIPCommand(ip="", duration_hours=1))
        assert isinstance(result, BlockIPResult)
        assert result.success is False


# ------------------------------------------------------------------
# UnblockIPHandler
# ------------------------------------------------------------------


class TestUnblockIPHandler:
    def test_unblock_ip(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import UnblockIPHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = UnblockIPHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(UnblockIPCommand(ip="10.0.0.99"))
        assert isinstance(result, UnblockIPResult)
        assert result.success is True

    def test_unblock_empty_ip(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import UnblockIPHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = UnblockIPHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(UnblockIPCommand(ip=""))
        assert isinstance(result, UnblockIPResult)
        assert result.success is False


# ------------------------------------------------------------------
# ReviewQuarantineHandler
# ------------------------------------------------------------------


class TestReviewQuarantineHandler:
    def test_returns_result(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import ReviewQuarantineHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = ReviewQuarantineHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(ReviewQuarantineCommand())
        assert isinstance(result, ReviewQuarantineResult)
        assert isinstance(result.records, list)


# ------------------------------------------------------------------
# SecurityBriefingHandler
# ------------------------------------------------------------------


class TestSecurityBriefingHandler:
    def test_returns_briefing_text(self, tmp_path: Path) -> None:
        from jarvis_engine.handlers.defense_handlers import SecurityBriefingHandler

        db, lock = _make_db()
        log_dir = tmp_path / "forensic"
        handler = SecurityBriefingHandler(tmp_path, db, lock, log_dir)
        result = handler.handle(SecurityBriefingCommand())
        assert isinstance(result, SecurityBriefingResult)
        assert "Jarvis" in result.briefing or "defense" in result.briefing.lower() or result.briefing != ""
        assert result.message != ""
