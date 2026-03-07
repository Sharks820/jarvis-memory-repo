from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime
from jarvis_engine._compat import UTC
from jarvis_engine._constants import runtime_dir as _runtime_dir
from jarvis_engine._shared import now_iso as _now_iso
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SnapshotResult:
    snapshot_path: Path
    metadata_path: Path
    signature_path: Path
    sha256: str
    file_count: int


@dataclass
class SnapshotVerification:
    ok: bool
    reason: str
    expected_sha256: str
    actual_sha256: str
    expected_signature: str
    actual_signature: str


def _snapshot_dir(root: Path) -> Path:
    return root / ".planning" / "brain" / "snapshots"


def _key_path(root: Path) -> Path:
    return root / ".planning" / "security" / "snapshot_signing.key"


def _default_targets(root: Path) -> list[Path]:
    return [
        root / ".planning" / "brain",
        root / ".planning" / "events.jsonl",
        root / ".planning" / "capability_history.jsonl",
        _runtime_dir(root),
    ]


def _safe_rel(path: Path, root: Path) -> str:
    rel = path.resolve().relative_to(root.resolve())
    return str(rel).replace("\\", "/")


def ensure_snapshot_key(root: Path) -> str:
    key_path = _key_path(root)
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.urandom(64)).hexdigest() + hashlib.sha256(os.urandom(32)).hexdigest()
    key_path.write_text(key, encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
    except OSError as exc:
        logger.debug("Cannot set snapshot key permissions: %s", exc)
    return key


def _load_snapshot_key(root: Path) -> str:
    key = ensure_snapshot_key(root)
    if not key:
        raise RuntimeError("Snapshot signing key is empty.")
    return key


def create_signed_snapshot(
    root: Path,
    *,
    note: str = "",
    targets: list[Path] | None = None,
) -> SnapshotResult:
    root_resolved = root.resolve()
    snapshot_dir = _snapshot_dir(root)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    snapshot_path = snapshot_dir / f"brain-snapshot-{ts}.zip"
    metadata_path = snapshot_path.with_suffix(".json")
    signature_path = snapshot_path.with_suffix(".sig")

    include_targets = targets if targets is not None else _default_targets(root)
    file_count = 0
    archived_files: list[str] = []

    with zipfile.ZipFile(snapshot_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for target in include_targets:
            if not target.exists():
                continue
            if target.is_file():
                rel = _safe_rel(target, root_resolved)
                zf.write(target, arcname=rel)
                archived_files.append(rel)
                file_count += 1
                continue
            for path in target.rglob("*"):
                if not path.is_file():
                    continue
                # Skip the snapshots directory to avoid recursive self-inclusion
                try:
                    path.resolve().relative_to(snapshot_dir.resolve())
                    continue
                except ValueError as exc:
                    logger.debug("Path not within snapshot dir (including): %s", exc)
                rel = _safe_rel(path, root_resolved)
                zf.write(path, arcname=rel)
                archived_files.append(rel)
                file_count += 1

    payload = snapshot_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    key = _load_snapshot_key(root)
    signature = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    metadata: dict[str, Any] = {
        "snapshot_file": snapshot_path.name,
        "created_utc": _now_iso(),
        "sha256": digest,
        "hmac_alg": "sha256",
        "signature_file": signature_path.name,
        "file_count": file_count,
        "note": note.strip()[:400],
        "archived_files": archived_files,
    }

    # Attempt to include knowledge graph metrics in snapshot metadata
    try:
        from jarvis_engine.knowledge.regression import RegressionChecker
        from jarvis_engine.knowledge.graph import KnowledgeGraph
        from jarvis_engine.memory.engine import MemoryEngine

        from jarvis_engine._constants import memory_db_path as _memory_db_path
        db_path = _memory_db_path(root_resolved)
        if db_path.exists():
            _kg_engine = MemoryEngine(db_path)
            try:
                _kg = KnowledgeGraph(_kg_engine)
                _checker = RegressionChecker(_kg)
                metadata["kg_metrics"] = _checker.capture_metrics()
            finally:
                _kg_engine.close()
    except (ImportError, OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        logger.warning("KG metrics capture failed: %s", exc)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
    signature_path.write_text(signature, encoding="utf-8")

    try:
        os.chmod(snapshot_path, 0o600)
        os.chmod(metadata_path, 0o600)
        os.chmod(signature_path, 0o600)
    except OSError as exc:
        logger.debug("Cannot set snapshot file permissions: %s", exc)

    return SnapshotResult(
        snapshot_path=snapshot_path,
        metadata_path=metadata_path,
        signature_path=signature_path,
        sha256=digest,
        file_count=file_count,
    )


def verify_signed_snapshot(root: Path, snapshot_path: Path) -> SnapshotVerification:
    if not snapshot_path.exists() or not snapshot_path.is_file():
        return SnapshotVerification(
            ok=False,
            reason="snapshot_not_found",
            expected_sha256="",
            actual_sha256="",
            expected_signature="",
            actual_signature="",
        )

    metadata_path = snapshot_path.with_suffix(".json")
    signature_path = snapshot_path.with_suffix(".sig")
    if not metadata_path.exists() or not signature_path.exists():
        return SnapshotVerification(
            ok=False,
            reason="metadata_or_signature_missing",
            expected_sha256="",
            actual_sha256="",
            expected_signature="",
            actual_signature="",
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SnapshotVerification(
            ok=False,
            reason="metadata_invalid_json",
            expected_sha256="",
            actual_sha256="",
            expected_signature="",
            actual_signature="",
        )

    payload = snapshot_path.read_bytes()
    actual_sha = hashlib.sha256(payload).hexdigest()
    expected_sha = str(metadata.get("sha256", ""))

    key = _load_snapshot_key(root)
    actual_sig = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    expected_sig = signature_path.read_text(encoding="utf-8").strip()

    if expected_sha != actual_sha:
        return SnapshotVerification(
            ok=False,
            reason="sha256_mismatch",
            expected_sha256=expected_sha,
            actual_sha256=actual_sha,
            expected_signature=expected_sig,
            actual_signature=actual_sig,
        )
    if not hmac.compare_digest(expected_sig, actual_sig):
        return SnapshotVerification(
            ok=False,
            reason="signature_mismatch",
            expected_sha256=expected_sha,
            actual_sha256=actual_sha,
            expected_signature=expected_sig,
            actual_signature=actual_sig,
        )
    return SnapshotVerification(
        ok=True,
        reason="verified",
        expected_sha256=expected_sha,
        actual_sha256=actual_sha,
        expected_signature=expected_sig,
        actual_signature=actual_sig,
    )


def run_memory_maintenance(root: Path, *, keep_recent: int = 1800, snapshot_note: str = "nightly") -> dict[str, Any]:
    from jarvis_engine.brain_memory import brain_compact, brain_regression_report

    compact_result = brain_compact(root, keep_recent=keep_recent)
    regression = brain_regression_report(root)
    snapshot = create_signed_snapshot(root, note=snapshot_note)

    # Knowledge graph regression (compare with previous snapshot kg_metrics)
    kg_regression: dict[str, Any] = {}
    try:
        snapshot_dir = _snapshot_dir(root)
        if snapshot_dir.exists():
            # Find most recent snapshot metadata with kg_metrics (skip the one we just created)
            prev_kg_metrics = None
            meta_files = sorted(snapshot_dir.glob("brain-snapshot-*.json"), reverse=True)
            for meta_file in meta_files:
                if meta_file.resolve() == snapshot.metadata_path.resolve():
                    continue  # Skip the snapshot we just created
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    if "kg_metrics" in meta:
                        prev_kg_metrics = meta["kg_metrics"]
                        break
                except (json.JSONDecodeError, OSError) as exc:
                    logger.debug("Skipping unreadable snapshot metadata %s: %s", meta_file, exc)
                    continue

            # Load current KG metrics from the snapshot we just created
            current_meta = json.loads(snapshot.metadata_path.read_text(encoding="utf-8"))
            current_kg_metrics = current_meta.get("kg_metrics")

            if current_kg_metrics:
                from jarvis_engine.knowledge.regression import RegressionChecker
                from jarvis_engine.knowledge.graph import KnowledgeGraph
                from jarvis_engine.memory.engine import MemoryEngine

                from jarvis_engine._constants import memory_db_path as _memory_db_path
                db_path = _memory_db_path(root)
                if db_path.exists():
                    _kg_engine = MemoryEngine(db_path)
                    try:
                        _kg = KnowledgeGraph(_kg_engine)
                        _checker = RegressionChecker(_kg)
                        kg_regression = _checker.compare(prev_kg_metrics, current_kg_metrics)
                    finally:
                        _kg_engine.close()
    except (ImportError, OSError, sqlite3.Error, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        logger.warning("KG regression comparison failed: %s", exc)

    report = {
        "ts": _now_iso(),
        "keep_recent": keep_recent,
        "compact": compact_result,
        "regression": regression,
        "kg_regression": kg_regression,
        "snapshot": {
            "path": str(snapshot.snapshot_path),
            "metadata": str(snapshot.metadata_path),
            "signature": str(snapshot.signature_path),
            "sha256": snapshot.sha256,
            "file_count": snapshot.file_count,
        },
        "status": "pass" if str(regression.get("status", "pass")) == "pass" else "warn",
    }

    maintenance_dir = root / ".planning" / "brain" / "maintenance"
    maintenance_dir.mkdir(parents=True, exist_ok=True)
    out_path = maintenance_dir / f"maintenance-{datetime.now(UTC).strftime('%Y%m%d')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    report["report_path"] = str(out_path)
    return report
