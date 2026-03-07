from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

from jarvis_engine._constants import runtime_dir as _runtime_dir
from jarvis_engine.brain_memory import brain_regression_report, brain_status
from jarvis_engine.memory_snapshots import run_memory_maintenance
from jarvis_engine.owner_guard import read_owner_guard
from jarvis_engine.runtime_control import read_control_state


from jarvis_engine._shared import atomic_write_json as _atomic_write_json
from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine._shared import safe_float as _safe_float
from jarvis_engine._shared import safe_int as _safe_int


def _tail_lines(path: Path, *, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return lines[-max(1, max_lines) :]


def _ensure_mobile_security_config(root: Path) -> dict[str, Any]:
    path = root / ".planning" / "security" / "mobile_api.json"
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                raw = loaded
        except (json.JSONDecodeError, OSError):
            raw = {}
    raw = {
        "token": raw.get("token", ""),
        "signing_key": raw.get("signing_key", ""),
    }
    token = str(raw.get("token", "")).strip()
    signing_key = str(raw.get("signing_key", "")).strip()
    repaired = False
    if not token:
        token = secrets.token_urlsafe(48)
        repaired = True
    if not signing_key:
        signing_key = secrets.token_urlsafe(64)
        repaired = True
    if repaired or not path.exists():
        _atomic_write_json(
            path,
            {
                "token": token,
                "signing_key": signing_key,
                "updated_utc": _now_iso(),
                "source": "resilience_repair",
            },
        )
    return {
        "path": str(path),
        "exists": path.exists(),
        "token_present": bool(token),
        "signing_key_present": bool(signing_key),
        "repaired": repaired,
    }


def run_mobile_desktop_sync(root: Path) -> dict[str, Any]:
    security = _ensure_mobile_security_config(root)
    owner_guard = read_owner_guard(root)
    control_state = read_control_state(root)
    memory_stats = brain_status(root)

    widget_cfg_path = root / ".planning" / "security" / "desktop_widget.json"
    trusted_devices = owner_guard.get("trusted_mobile_devices", [])
    trusted_count = len(trusted_devices) if isinstance(trusted_devices, list) else 0
    has_master_password = bool(str(owner_guard.get("master_password_hash", "")).strip())

    checks = [
        {
            "name": "mobile_security_config",
            "ok": bool(security.get("token_present"))
            and bool(security.get("signing_key_present")),
        },
        {"name": "widget_config_exists", "ok": widget_cfg_path.exists()},
        {
            "name": "owner_guard_device_ready",
            "ok": (not bool(owner_guard.get("enabled", False)))
            or trusted_count > 0
            or has_master_password,
        },
    ]
    sync_ok = all(bool(item.get("ok", False)) for item in checks)
    report = {
        "sync_ok": sync_ok,
        "generated_utc": _now_iso(),
        "security": security,
        "owner_guard": {
            "enabled": bool(owner_guard.get("enabled", False)),
            "owner_user_id": str(owner_guard.get("owner_user_id", "")),
            "trusted_mobile_device_count": trusted_count,
            "has_master_password": has_master_password,
        },
        "runtime_control": {
            "daemon_paused": bool(control_state.get("daemon_paused", False)),
            "safe_mode": bool(control_state.get("safe_mode", False)),
            "updated_utc": str(control_state.get("updated_utc", "")),
        },
        "memory": {
            "total_records": _safe_int(memory_stats.get("total_records", 0)),
            "fact_count": _safe_int(memory_stats.get("fact_count", 0)),
        },
        "checks": checks,
    }
    report_path = _runtime_dir(root) / "mobile_desktop_sync.json"
    _atomic_write_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def _scan_recent_logs(root: Path, *, max_lines: int = 200) -> dict[str, Any]:
    log_dir = root / ".planning" / "logs"
    # *.log already matches *.err.log -- no need for a separate glob
    # (the old code double-counted .err.log files)
    files = sorted(log_dir.glob("*.log"))
    issues = {
        "http_400": 0,
        "traceback": 0,
        "timeout": 0,
        "auth_failed": 0,
    }
    samples: list[str] = []
    for path in files:
        for line in _tail_lines(path, max_lines=max_lines):
            lowered = line.lower()
            if re.search(r"\bhttp[_ ]?400\b", lowered):
                issues["http_400"] += 1
            if "traceback" in lowered:
                issues["traceback"] += 1
            if "timeout" in lowered:
                issues["timeout"] += 1
            if "unauthorized" in lowered or "untrusted mobile device" in lowered:
                issues["auth_failed"] += 1
            if len(samples) < 12 and re.search(
                r"(error|failed|traceback|timeout|unauthorized)", lowered
            ):
                samples.append(f"{path.name}: {line[:220]}")
    return {
        "log_files_scanned": len(files),
        "issues": issues,
        "samples": samples,
    }


def run_self_heal(
    root: Path,
    *,
    keep_recent: int = 1800,
    snapshot_note: str = "self-heal",
    force_maintenance: bool = False,
) -> dict[str, Any]:
    now = _now_iso()
    actions: list[str] = []
    security = _ensure_mobile_security_config(root)
    if bool(security.get("repaired", False)):
        actions.append("repaired_mobile_security_config")

    sync_report = run_mobile_desktop_sync(root)
    if not bool(sync_report.get("sync_ok", False)):
        actions.append("mobile_desktop_sync_attention")

    regression = brain_regression_report(root)
    status = str(regression.get("status", "unknown")).strip().lower()
    regression_healthy = status in {"healthy", "pass"}
    unresolved_conflicts = _safe_int(regression.get("unresolved_conflicts", 0))
    duplicate_ratio = _safe_float(regression.get("duplicate_ratio", 0.0))

    maintenance: dict[str, Any] = {"status": "skipped"}
    should_maintain = (
        force_maintenance
        or (not regression_healthy)
        or unresolved_conflicts > 0
        or duplicate_ratio > 0.25
    )
    if should_maintain:
        maintenance = run_memory_maintenance(
            root,
            keep_recent=max(200, min(keep_recent, 50000)),
            snapshot_note=snapshot_note[:160],
        )
        actions.append("memory_maintenance_run")

    logs = _scan_recent_logs(root)
    issue_counts = logs.get("issues", {})
    issue_total = 0
    if isinstance(issue_counts, dict):
        issue_total = sum(_safe_int(v) for v in issue_counts.values())

    overall = "ok"
    if (not regression_healthy) or (
        isinstance(maintenance, dict) and maintenance.get("status") == "error"
    ):
        overall = "error"
    elif issue_total > 0 or not bool(sync_report.get("sync_ok", False)):
        overall = "attention"

    report = {
        "status": overall,
        "generated_utc": now,
        "actions": actions,
        "regression": regression,
        "maintenance": maintenance,
        "sync": sync_report,
        "log_scan": logs,
    }
    report_path = _runtime_dir(root) / "self_heal_report.json"
    _atomic_write_json(report_path, report)
    report["report_path"] = str(report_path)
    return report
