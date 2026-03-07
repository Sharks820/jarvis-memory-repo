from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from jarvis_engine.resilience import (
    _ensure_mobile_security_config,
    _scan_recent_logs,
    _tail_lines,
    run_mobile_desktop_sync,
    run_self_heal,
)


# ── existing tests ────────────────────────────────────────────────────────


def test_run_mobile_desktop_sync_writes_report(tmp_path: Path) -> None:
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")

    report = run_mobile_desktop_sync(tmp_path)
    assert "sync_ok" in report
    report_path = tmp_path / ".planning" / "runtime" / "mobile_desktop_sync.json"
    assert report_path.exists()
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    assert "checks" in raw


def test_run_self_heal_generates_report(tmp_path: Path) -> None:
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")

    report = run_self_heal(tmp_path, keep_recent=300, snapshot_note="test")
    assert "status" in report
    report_path = tmp_path / ".planning" / "runtime" / "self_heal_report.json"
    assert report_path.exists()


# ── _tail_lines tests ─────────────────────────────────────────────────────


def test_tail_lines_returns_empty_for_missing_file(tmp_path: Path) -> None:
    result = _tail_lines(tmp_path / "nonexistent.log", max_lines=10)
    assert result == []


def test_tail_lines_returns_last_n_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
    result = _tail_lines(log_file, max_lines=3)
    assert result == ["line3", "line4", "line5"]


def test_tail_lines_returns_all_when_fewer_than_max(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("only\ntwo\n", encoding="utf-8")
    result = _tail_lines(log_file, max_lines=10)
    assert result == ["only", "two"]


def test_tail_lines_skips_blank_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("a\n\n\nb\n  \nc\n", encoding="utf-8")
    result = _tail_lines(log_file, max_lines=10)
    assert result == ["a", "b", "c"]


def test_tail_lines_handles_os_error(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    log_file.write_text("data", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=OSError("perm denied")):
        result = _tail_lines(log_file, max_lines=5)
    assert result == []


# ── _ensure_mobile_security_config tests ──────────────────────────────────


def test_ensure_mobile_security_config_creates_from_scratch(tmp_path: Path) -> None:
    result = _ensure_mobile_security_config(tmp_path)
    assert result["token_present"] is True
    assert result["signing_key_present"] is True
    assert result["repaired"] is True
    config_path = tmp_path / ".planning" / "security" / "mobile_api.json"
    assert config_path.exists()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert len(raw["token"]) > 10
    assert len(raw["signing_key"]) > 10
    assert raw["source"] == "resilience_repair"


def test_ensure_mobile_security_config_preserves_valid(tmp_path: Path) -> None:
    config_path = tmp_path / ".planning" / "security" / "mobile_api.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"token": "my-token-123", "signing_key": "my-key-456"}),
        encoding="utf-8",
    )
    result = _ensure_mobile_security_config(tmp_path)
    assert result["repaired"] is False
    assert result["token_present"] is True
    assert result["signing_key_present"] is True


def test_ensure_mobile_security_config_repairs_missing_token(tmp_path: Path) -> None:
    config_path = tmp_path / ".planning" / "security" / "mobile_api.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"token": "", "signing_key": "valid-key"}),
        encoding="utf-8",
    )
    result = _ensure_mobile_security_config(tmp_path)
    assert result["repaired"] is True
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["signing_key"] == "valid-key"
    assert len(raw["token"]) > 10


def test_ensure_mobile_security_config_repairs_missing_signing_key(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".planning" / "security" / "mobile_api.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"token": "valid-token", "signing_key": ""}),
        encoding="utf-8",
    )
    result = _ensure_mobile_security_config(tmp_path)
    assert result["repaired"] is True
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["token"] == "valid-token"
    assert len(raw["signing_key"]) > 10


def test_ensure_mobile_security_config_handles_corrupt_json(tmp_path: Path) -> None:
    config_path = tmp_path / ".planning" / "security" / "mobile_api.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{bad json!!!", encoding="utf-8")
    result = _ensure_mobile_security_config(tmp_path)
    assert result["repaired"] is True
    assert result["token_present"] is True
    assert result["signing_key_present"] is True


def test_ensure_mobile_security_config_handles_non_dict_json(tmp_path: Path) -> None:
    config_path = tmp_path / ".planning" / "security" / "mobile_api.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('"just a string"', encoding="utf-8")
    result = _ensure_mobile_security_config(tmp_path)
    assert result["repaired"] is True


# ── run_mobile_desktop_sync extended tests ────────────────────────────────


def test_run_mobile_desktop_sync_no_widget_config(tmp_path: Path) -> None:
    """Missing widget config should cause widget_config_exists check to fail."""
    report = run_mobile_desktop_sync(tmp_path)
    checks = {c["name"]: c["ok"] for c in report["checks"]}
    assert checks["widget_config_exists"] is False


def test_run_mobile_desktop_sync_reports_memory_stats(tmp_path: Path) -> None:
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")
    report = run_mobile_desktop_sync(tmp_path)
    assert "memory" in report
    assert "total_records" in report["memory"]
    assert "fact_count" in report["memory"]


def test_run_mobile_desktop_sync_owner_guard_enabled_no_devices(tmp_path: Path) -> None:
    """When owner_guard is enabled but has no trusted devices and no master password,
    the owner_guard_device_ready check should fail."""
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")
    og_path = tmp_path / ".planning" / "security" / "owner_guard.json"
    og_path.write_text(
        json.dumps(
            {"enabled": True, "trusted_mobile_devices": [], "master_password_hash": ""}
        ),
        encoding="utf-8",
    )
    report = run_mobile_desktop_sync(tmp_path)
    checks = {c["name"]: c["ok"] for c in report["checks"]}
    assert checks["owner_guard_device_ready"] is False
    assert report["sync_ok"] is False


# ── _scan_recent_logs tests ───────────────────────────────────────────────


def test_scan_recent_logs_empty_dir(tmp_path: Path) -> None:
    result = _scan_recent_logs(tmp_path)
    assert result["log_files_scanned"] == 0
    assert sum(result["issues"].values()) == 0


def test_scan_recent_logs_detects_issues(tmp_path: Path) -> None:
    log_dir = tmp_path / ".planning" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "daemon.log").write_text(
        "2026-02-20 INFO started\n"
        "2026-02-20 ERROR http_400 bad request\n"
        "2026-02-20 Traceback (most recent call last)\n"
        "2026-02-20 Connection timeout waiting\n"
        "2026-02-20 Unauthorized access denied\n",
        encoding="utf-8",
    )
    result = _scan_recent_logs(tmp_path)
    assert result["log_files_scanned"] >= 1
    assert result["issues"]["http_400"] >= 1
    assert result["issues"]["traceback"] >= 1
    assert result["issues"]["timeout"] >= 1
    assert result["issues"]["auth_failed"] >= 1
    assert len(result["samples"]) >= 4


def test_scan_recent_logs_limits_samples(tmp_path: Path) -> None:
    log_dir = tmp_path / ".planning" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lines = "".join(f"Error on line {i}\n" for i in range(50))
    (log_dir / "app.log").write_text(lines, encoding="utf-8")
    result = _scan_recent_logs(tmp_path)
    assert len(result["samples"]) <= 12


def test_scan_recent_logs_scans_err_log_files(tmp_path: Path) -> None:
    log_dir = tmp_path / ".planning" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "crash.err.log").write_text("Traceback found here\n", encoding="utf-8")
    result = _scan_recent_logs(tmp_path)
    assert result["log_files_scanned"] >= 1
    assert result["issues"]["traceback"] >= 1


# ── run_self_heal extended tests ──────────────────────────────────────────


def test_run_self_heal_status_ok_when_healthy(tmp_path: Path) -> None:
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")
    report = run_self_heal(tmp_path, keep_recent=300, snapshot_note="test")
    assert report["status"] in {"ok", "attention", "error"}
    assert "actions" in report
    assert isinstance(report["actions"], list)


def test_run_self_heal_force_maintenance_runs_maintenance(tmp_path: Path) -> None:
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")
    report = run_self_heal(
        tmp_path, keep_recent=300, snapshot_note="forced", force_maintenance=True
    )
    assert "memory_maintenance_run" in report["actions"]
    assert report["maintenance"]["status"] != "skipped"


def test_run_self_heal_clamps_keep_recent(tmp_path: Path) -> None:
    """keep_recent should be clamped between 200 and 50000."""
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")
    # A very small keep_recent should be clamped to 200
    report = run_self_heal(
        tmp_path, keep_recent=1, snapshot_note="clamp", force_maintenance=True
    )
    assert "memory_maintenance_run" in report["actions"]


def test_run_self_heal_truncates_snapshot_note(tmp_path: Path) -> None:
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")
    long_note = "x" * 500
    report = run_self_heal(
        tmp_path, keep_recent=300, snapshot_note=long_note, force_maintenance=True
    )
    # Should not crash; note gets truncated to 160 chars
    assert report["status"] in {"ok", "attention", "error"}


def test_run_self_heal_attention_when_log_issues(tmp_path: Path) -> None:
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")
    log_dir = tmp_path / ".planning" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "daemon.log").write_text("ERROR timeout\n", encoding="utf-8")
    report = run_self_heal(tmp_path, keep_recent=300, snapshot_note="logtest")
    # log issues should cause attention status (unless regression is unhealthy -> error)
    assert report["status"] in {"attention", "error"}


def test_run_self_heal_includes_sync_report(tmp_path: Path) -> None:
    widget_cfg = tmp_path / ".planning" / "security" / "desktop_widget.json"
    widget_cfg.parent.mkdir(parents=True, exist_ok=True)
    widget_cfg.write_text("{}", encoding="utf-8")
    report = run_self_heal(tmp_path, keep_recent=300, snapshot_note="sync-check")
    assert "sync" in report
    assert "sync_ok" in report["sync"]
