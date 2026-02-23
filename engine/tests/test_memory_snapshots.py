from __future__ import annotations

from pathlib import Path

from jarvis_engine.memory_snapshots import create_signed_snapshot, run_memory_maintenance, verify_signed_snapshot


def test_create_and_verify_signed_snapshot(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".planning" / "brain" / "records.jsonl").write_text("{}\n", encoding="utf-8")
    result = create_signed_snapshot(tmp_path, note="unit-test")
    assert result.snapshot_path.exists()
    verification = verify_signed_snapshot(tmp_path, result.snapshot_path)
    assert verification.ok is True


def test_verify_fails_after_tamper(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".planning" / "brain" / "records.jsonl").write_text("{}\n", encoding="utf-8")
    result = create_signed_snapshot(tmp_path, note="tamper-test")
    with result.snapshot_path.open("ab") as f:
        f.write(b"tamper")
    verification = verify_signed_snapshot(tmp_path, result.snapshot_path)
    assert verification.ok is False


def test_run_memory_maintenance_outputs_report(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".planning" / "brain" / "records.jsonl").write_text("{}\n", encoding="utf-8")
    report = run_memory_maintenance(tmp_path, keep_recent=100, snapshot_note="nightly")
    assert "snapshot" in report
    assert "regression" in report
    assert Path(str(report["report_path"])).exists()
