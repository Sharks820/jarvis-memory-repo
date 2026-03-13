from __future__ import annotations

import json
from pathlib import Path

from jarvis_engine.memory.snapshots import (
    create_signed_snapshot,
    ensure_snapshot_key,
    run_memory_maintenance,
    verify_signed_snapshot,
)


# ── existing tests ────────────────────────────────────────────────────────

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


# ── ensure_snapshot_key tests ─────────────────────────────────────────────

def test_ensure_snapshot_key_creates_new_key(tmp_path: Path) -> None:
    key = ensure_snapshot_key(tmp_path)
    assert isinstance(key, str)
    assert len(key) > 40
    key_path = tmp_path / ".planning" / "security" / "snapshot_signing.key"
    assert key_path.exists()


def test_ensure_snapshot_key_returns_existing(tmp_path: Path) -> None:
    key_path = tmp_path / ".planning" / "security" / "snapshot_signing.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text("my-custom-key-1234", encoding="utf-8")
    key = ensure_snapshot_key(tmp_path)
    assert key == "my-custom-key-1234"


def test_ensure_snapshot_key_idempotent(tmp_path: Path) -> None:
    key1 = ensure_snapshot_key(tmp_path)
    key2 = ensure_snapshot_key(tmp_path)
    assert key1 == key2


# ── create_signed_snapshot tests ──────────────────────────────────────────

def test_create_snapshot_missing_target_files(tmp_path: Path) -> None:
    """Snapshot with no existing target files should create a valid zip with 0 files."""
    result = create_signed_snapshot(tmp_path, note="empty")
    assert result.snapshot_path.exists()
    assert result.file_count == 0
    verification = verify_signed_snapshot(tmp_path, result.snapshot_path)
    assert verification.ok is True


def test_create_snapshot_with_custom_targets(tmp_path: Path) -> None:
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    (custom_dir / "file1.txt").write_text("hello", encoding="utf-8")
    (custom_dir / "file2.txt").write_text("world", encoding="utf-8")
    result = create_signed_snapshot(tmp_path, note="custom-targets", targets=[custom_dir])
    assert result.file_count == 2


def test_create_snapshot_note_truncated(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    long_note = "a" * 1000
    result = create_signed_snapshot(tmp_path, note=long_note)
    meta = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert len(meta["note"]) <= 400


def test_create_snapshot_produces_all_output_files(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".planning" / "brain" / "data.json").write_text("{}", encoding="utf-8")
    result = create_signed_snapshot(tmp_path, note="artifacts")
    assert result.snapshot_path.exists()
    assert result.metadata_path.exists()
    assert result.signature_path.exists()
    meta = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert meta["sha256"] == result.sha256
    assert meta["file_count"] == result.file_count


def test_create_snapshot_large_payload(tmp_path: Path) -> None:
    """Large files should be handled without errors."""
    brain_dir = tmp_path / ".planning" / "brain"
    brain_dir.mkdir(parents=True, exist_ok=True)
    (brain_dir / "big.bin").write_bytes(b"x" * 500_000)
    result = create_signed_snapshot(tmp_path, note="large")
    assert result.file_count >= 1
    verification = verify_signed_snapshot(tmp_path, result.snapshot_path)
    assert verification.ok is True


# ── verify_signed_snapshot tests ──────────────────────────────────────────

def test_verify_nonexistent_snapshot(tmp_path: Path) -> None:
    fake_path = tmp_path / "nonexistent.zip"
    result = verify_signed_snapshot(tmp_path, fake_path)
    assert result.ok is False
    assert result.reason == "snapshot_not_found"


def test_verify_missing_metadata(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    snap_result = create_signed_snapshot(tmp_path, note="test")
    # Remove metadata file
    snap_result.metadata_path.unlink()
    result = verify_signed_snapshot(tmp_path, snap_result.snapshot_path)
    assert result.ok is False
    assert result.reason == "metadata_or_signature_missing"


def test_verify_missing_signature_file(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    snap_result = create_signed_snapshot(tmp_path, note="test")
    snap_result.signature_path.unlink()
    result = verify_signed_snapshot(tmp_path, snap_result.snapshot_path)
    assert result.ok is False
    assert result.reason == "metadata_or_signature_missing"


def test_verify_corrupt_metadata_json(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    snap_result = create_signed_snapshot(tmp_path, note="test")
    snap_result.metadata_path.write_text("{broken json", encoding="utf-8")
    result = verify_signed_snapshot(tmp_path, snap_result.snapshot_path)
    assert result.ok is False
    assert result.reason == "metadata_invalid_json"


def test_verify_tampered_signature(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".planning" / "brain" / "data.json").write_text("{}", encoding="utf-8")
    snap_result = create_signed_snapshot(tmp_path, note="sig-tamper")
    snap_result.signature_path.write_text("0" * 64, encoding="utf-8")
    result = verify_signed_snapshot(tmp_path, snap_result.snapshot_path)
    assert result.ok is False
    assert result.reason == "signature_mismatch"


def test_verify_sha256_mismatch(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".planning" / "brain" / "data.json").write_text("{}", encoding="utf-8")
    snap_result = create_signed_snapshot(tmp_path, note="sha-tamper")
    # Alter the expected sha256 in metadata
    meta = json.loads(snap_result.metadata_path.read_text(encoding="utf-8"))
    meta["sha256"] = "0" * 64
    snap_result.metadata_path.write_text(json.dumps(meta), encoding="utf-8")
    result = verify_signed_snapshot(tmp_path, snap_result.snapshot_path)
    assert result.ok is False
    assert result.reason == "sha256_mismatch"


def test_verify_wrong_key(tmp_path: Path) -> None:
    """Snapshot created with one key should fail verification with a different key."""
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".planning" / "brain" / "data.json").write_text("{}", encoding="utf-8")
    snap_result = create_signed_snapshot(tmp_path, note="key-test")
    # Overwrite the signing key
    key_path = tmp_path / ".planning" / "security" / "snapshot_signing.key"
    key_path.write_text("completely-different-key-now", encoding="utf-8")
    result = verify_signed_snapshot(tmp_path, snap_result.snapshot_path)
    assert result.ok is False
    assert result.reason == "signature_mismatch"


# ── run_memory_maintenance tests ──────────────────────────────────────────

def test_run_memory_maintenance_creates_maintenance_file(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    report = run_memory_maintenance(tmp_path, keep_recent=100, snapshot_note="maint-test")
    assert "report_path" in report
    report_path = Path(report["report_path"])
    assert report_path.exists()
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    assert raw["keep_recent"] == 100


def test_run_memory_maintenance_status_field(tmp_path: Path) -> None:
    (tmp_path / ".planning" / "brain").mkdir(parents=True, exist_ok=True)
    report = run_memory_maintenance(tmp_path, keep_recent=100, snapshot_note="status-test")
    assert report["status"] in {"pass", "warn"}
