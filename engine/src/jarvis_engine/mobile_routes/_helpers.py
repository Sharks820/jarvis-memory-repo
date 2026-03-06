"""Shared utilities for mobile route modules.

Extracted from mobile_api.py to avoid circular imports between
mobile_api and the route mixin modules.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_SOURCES = {"user", "claude", "opus", "gemini", "task_outcome"}
ALLOWED_KINDS = {"episodic", "semantic", "procedural"}

# Shared thread-local storage for per-thread stdout capture and repo_root override.
# Both mobile_api.py and route mixin modules import this single instance to ensure
# repo_root_override set by command.py is visible to get_bus() in mobile_api.py.
_thread_local = threading.local()


def _configure_db(conn: Any) -> None:
    """Apply consistent SQLite PRAGMAs and Row factory."""
    import sqlite3

    from jarvis_engine._db_pragmas import configure_sqlite

    conn.row_factory = sqlite3.Row
    configure_sqlite(conn, full=True)


def _parse_bool(value: Any) -> bool:
    """Safely parse a boolean from JSON payload (handles string "false"/"true")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _get_cert_fingerprint(cert_path: str) -> str | None:
    """Return the SHA-256 fingerprint of a PEM certificate."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-fingerprint", "-sha256"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output: "sha256 Fingerprint=AA:BB:CC:..."
            for line in result.stdout.splitlines():
                if "=" in line:
                    return line.split("=", 1)[1].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Fallback: pure-Python PEM -> DER -> SHA-256
    try:
        import base64
        from pathlib import Path

        pem_data = Path(cert_path).read_text(encoding="utf-8")
        # Strip PEM header/footer and decode base64
        lines = [
            line for line in pem_data.splitlines()
            if line and not line.startswith("-----")
        ]
        der_bytes = base64.b64decode("".join(lines))
        digest = hashlib.sha256(der_bytes).hexdigest().upper()
        return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))
    except (OSError, ValueError):
        pass
    return None


def _serialize_activity_event(event: Any) -> dict[str, Any]:
    """Serialize an activity feed event to a JSON-safe dict."""
    details = event.details if isinstance(getattr(event, "details", None), dict) else {}
    return dict(
        event_id=event.event_id,
        timestamp=event.timestamp,
        category=event.category,
        summary=event.summary,
        details=details,
    )


def _compute_command_reliability() -> dict[str, Any]:
    """Aggregate command reliability metrics from the activity feed."""
    result: dict[str, Any] = {
        "sampled_commands": 0,
        "command_success_rate_pct": 0.0,
        "retry_count": 0,
        "timeout_count": 0,
        "memory_pressure_incidents": 0,
        "last_pressure_level": "none",
    }
    try:
        from jarvis_engine.activity_feed import ActivityCategory, get_activity_feed

        feed = get_activity_feed()
        events = feed.query(limit=200, category=ActivityCategory.COMMAND_LIFECYCLE)
        if events:
            result["sampled_commands"] = len(events)
            ok_count = sum(
                1
                for e in events
                if isinstance(getattr(e, "details", None), dict) and e.details.get("ok")
            )
            result["command_success_rate_pct"] = round(100.0 * ok_count / len(events), 1) if events else 0.0
            result["retry_count"] = sum(
                1
                for e in events
                if isinstance(getattr(e, "details", None), dict) and e.details.get("retryable")
            )
            result["timeout_count"] = sum(
                1
                for e in events
                if isinstance(getattr(e, "details", None), dict)
                and str(e.details.get("error_code", "")).startswith("timeout")
            )
        # Pressure events
        pressure_events = feed.query(limit=50, category=ActivityCategory.RESOURCE_PRESSURE)
        result["memory_pressure_incidents"] = len(pressure_events)
        if pressure_events:
            latest_details = getattr(pressure_events[0], "details", {})
            if isinstance(latest_details, dict):
                result["last_pressure_level"] = str(latest_details.get("level", "none"))
    except Exception as exc:
        logger.debug("Command reliability metrics unavailable: %s", exc)
    return result
