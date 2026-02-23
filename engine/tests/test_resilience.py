from __future__ import annotations

import json
from pathlib import Path

from jarvis_engine.resilience import run_mobile_desktop_sync, run_self_heal


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
