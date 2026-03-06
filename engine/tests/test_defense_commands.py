"""Tests for security.defense_commands -- Wave 13 CQRS command dataclasses."""

from __future__ import annotations

import dataclasses

import pytest

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


# ---------------------------------------------------------------------------
# Frozen check
# ---------------------------------------------------------------------------


class TestFrozen:
    """Command dataclasses should be frozen; Results are mutable (matching codebase convention)."""

    @pytest.mark.parametrize(
        "cls",
        [
            SecurityStatusCommand,
            ThreatReportCommand,
            ExportForensicsCommand,
            ContainmentOverrideCommand,
            BlockIPCommand,
            UnblockIPCommand,
            ReviewQuarantineCommand,
            SecurityBriefingCommand,
        ],
    )
    def test_command_is_frozen(self, cls: type) -> None:
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "cls",
        [
            SecurityStatusResult,
            ThreatReportResult,
            ExportForensicsResult,
            ContainmentOverrideResult,
            BlockIPResult,
            UnblockIPResult,
            ReviewQuarantineResult,
            SecurityBriefingResult,
        ],
    )
    def test_result_is_mutable(self, cls: type) -> None:
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SecurityStatusCommand / Result
# ---------------------------------------------------------------------------


class TestSecurityStatus:
    def test_command_no_fields(self) -> None:
        cmd = SecurityStatusCommand()
        assert dataclasses.fields(cmd) == ()

    def test_result_defaults(self) -> None:
        r = SecurityStatusResult()
        assert r.dashboard == {}
        assert r.message == ""

    def test_result_custom(self) -> None:
        r = SecurityStatusResult(dashboard={"key": "val"}, message="ok")
        assert r.dashboard == {"key": "val"}
        assert r.message == "ok"


# ---------------------------------------------------------------------------
# ThreatReportCommand / Result
# ---------------------------------------------------------------------------


class TestThreatReport:
    def test_command_default_ip_none(self) -> None:
        cmd = ThreatReportCommand()
        assert cmd.ip is None

    def test_command_custom_ip(self) -> None:
        cmd = ThreatReportCommand(ip="10.0.0.1")
        assert cmd.ip == "10.0.0.1"

    def test_result_defaults(self) -> None:
        r = ThreatReportResult()
        assert r.report == {}
        assert r.message == ""

    def test_result_custom(self) -> None:
        r = ThreatReportResult(report={"threats": 5}, message="found")
        assert r.report["threats"] == 5


# ---------------------------------------------------------------------------
# ExportForensicsCommand / Result
# ---------------------------------------------------------------------------


class TestExportForensics:
    def test_command_defaults(self) -> None:
        cmd = ExportForensicsCommand()
        assert cmd.start_date == ""
        assert cmd.end_date == ""

    def test_command_custom(self) -> None:
        cmd = ExportForensicsCommand(start_date="2026-01-01", end_date="2026-02-01")
        assert cmd.start_date == "2026-01-01"
        assert cmd.end_date == "2026-02-01"

    def test_result_defaults(self) -> None:
        r = ExportForensicsResult()
        assert r.export_path == ""
        assert r.message == ""

    def test_result_custom(self) -> None:
        r = ExportForensicsResult(export_path="/tmp/export.json", message="done")
        assert r.export_path == "/tmp/export.json"


# ---------------------------------------------------------------------------
# ContainmentOverrideCommand / Result
# ---------------------------------------------------------------------------


class TestContainmentOverride:
    def test_command_defaults(self) -> None:
        cmd = ContainmentOverrideCommand()
        assert cmd.level == 0
        assert cmd.action == "recover"

    def test_command_custom(self) -> None:
        cmd = ContainmentOverrideCommand(level=3, action="isolate")
        assert cmd.level == 3
        assert cmd.action == "isolate"

    def test_result_defaults(self) -> None:
        r = ContainmentOverrideResult()
        assert r.success is False
        assert r.message == ""

    def test_result_custom(self) -> None:
        r = ContainmentOverrideResult(success=True, message="recovered")
        assert r.success is True


# ---------------------------------------------------------------------------
# BlockIPCommand / Result
# ---------------------------------------------------------------------------


class TestBlockIP:
    def test_command_defaults(self) -> None:
        cmd = BlockIPCommand()
        assert cmd.ip == ""
        assert cmd.duration_hours == 24

    def test_command_custom(self) -> None:
        cmd = BlockIPCommand(ip="192.168.1.1", duration_hours=48)
        assert cmd.ip == "192.168.1.1"
        assert cmd.duration_hours == 48

    def test_result_defaults(self) -> None:
        r = BlockIPResult()
        assert r.success is False
        assert r.message == ""


# ---------------------------------------------------------------------------
# UnblockIPCommand / Result
# ---------------------------------------------------------------------------


class TestUnblockIP:
    def test_command_defaults(self) -> None:
        cmd = UnblockIPCommand()
        assert cmd.ip == ""

    def test_command_custom(self) -> None:
        cmd = UnblockIPCommand(ip="192.168.1.1")
        assert cmd.ip == "192.168.1.1"

    def test_result_defaults(self) -> None:
        r = UnblockIPResult()
        assert r.success is False
        assert r.message == ""


# ---------------------------------------------------------------------------
# ReviewQuarantineCommand / Result
# ---------------------------------------------------------------------------


class TestReviewQuarantine:
    def test_command_no_fields(self) -> None:
        cmd = ReviewQuarantineCommand()
        assert dataclasses.fields(cmd) == ()

    def test_result_defaults(self) -> None:
        r = ReviewQuarantineResult()
        assert r.records == []
        assert r.message == ""

    def test_result_custom(self) -> None:
        r = ReviewQuarantineResult(records=[{"hash": "abc"}], message="1 record")
        assert len(r.records) == 1


# ---------------------------------------------------------------------------
# SecurityBriefingCommand / Result
# ---------------------------------------------------------------------------


class TestSecurityBriefing:
    def test_command_no_fields(self) -> None:
        cmd = SecurityBriefingCommand()
        assert dataclasses.fields(cmd) == ()

    def test_result_defaults(self) -> None:
        r = SecurityBriefingResult()
        assert r.briefing == ""
        assert r.message == ""

    def test_result_custom(self) -> None:
        r = SecurityBriefingResult(briefing="All clear", message="ok")
        assert r.briefing == "All clear"


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_command_cannot_be_modified(self) -> None:
        cmd = BlockIPCommand(ip="10.0.0.1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cmd.ip = "10.0.0.2"  # type: ignore[misc]

    def test_result_can_be_modified(self) -> None:
        r = SecurityStatusResult(message="ok")
        r.message = "updated"
        assert r.message == "updated"
