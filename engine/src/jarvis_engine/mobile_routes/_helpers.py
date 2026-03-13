"""Shared utilities for mobile route modules.

Extracted from mobile_api.py to avoid circular imports between
mobile_api and the route mixin modules.
"""

from __future__ import annotations

import hashlib
import io
import logging
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Protocol, TypedDict, cast

from jarvis_engine._constants import SUBSYSTEM_ERRORS

if TYPE_CHECKING:
    from jarvis_engine.activity_feed import ActivityEvent

logger = logging.getLogger(__name__)


class HeaderProtocol(Protocol):
    """Minimal request-header interface shared by mobile route mixins."""

    def get(self, key: str, default: str | None = None) -> str | None:
        ...


class MobilePipelineProtocol(Protocol):
    """Subset of the ingestion pipeline used by route mixins."""

    def ingest(
        self,
        *,
        source: str,
        kind: str,
        task_id: str,
        content: str,
    ) -> Any:
        ...


class MobileRouteServerProtocol(Protocol):
    """Structural protocol for the MobileIngestServer surface used by mixins."""

    owner_session: Any
    auth_token: str
    signing_key: str
    server_address: tuple[str, int]
    tls_active: bool
    security: Any
    pipeline: MobilePipelineProtocol

    def check_bootstrap_rate(self, client_ip: str) -> bool:
        ...

    def record_bootstrap_attempt(self, client_ip: str) -> None:
        ...


class MobileRouteHandlerProtocol(Protocol):
    """Common request-handler interface consumed by mobile route mixins."""

    server: MobileRouteServerProtocol
    headers: HeaderProtocol
    client_address: tuple[str, int]
    path: str
    _root: Path

    def _read_json_body(
        self,
        *,
        max_content_length: int,
        auth: bool = True,
    ) -> tuple[dict[str, Any] | None, bytes | None]:
        ...

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        ...

    def _validate_auth(self, body: bytes) -> bool:
        ...

    def _validate_auth_flexible(self, body: bytes) -> bool:
        ...

    def _unauthorized(self, message: str) -> None:
        ...


class CommandReliability(TypedDict):
    """Aggregated command reliability metrics."""

    sampled_commands: int
    command_success_rate_pct: float
    retry_count: int
    timeout_count: int
    memory_pressure_incidents: int
    last_pressure_level: str

ALLOWED_SOURCES = {"user", "claude", "opus", "gemini", "task_outcome"}
ALLOWED_KINDS = {"episodic", "semantic", "procedural"}

# Shared thread-local storage for per-thread stdout capture and repo_root override.
# Both mobile_api.py and route mixin modules import this single instance to ensure
# repo_root_override set by command.py is visible to get_bus() in mobile_api.py.
_thread_local = threading.local()

THREAD_CAPTURE_MAX_CHARS = 200_000


class _ThreadCapturingStdout:
    """Wraps real stdout, routing writes to per-thread StringIO when active.

    Install once at server startup via ``_ThreadCapturingStdout.install()``.
    Each request thread calls ``start_capture()`` / ``stop_capture()`` to
    redirect its own prints to a thread-local buffer without affecting other
    threads.
    """

    _real_stdout = None  # set by install()

    def __init__(self, real_stdout: IO[str]) -> None:
        object.__setattr__(self, "_real", real_stdout)
        _ThreadCapturingStdout._real_stdout = real_stdout

    def write(self, s: str) -> int:
        buf = getattr(_thread_local, "capture_buf", None)
        if buf is not None:
            max_chars = int(getattr(_thread_local, "capture_max_chars", THREAD_CAPTURE_MAX_CHARS))
            used = int(getattr(_thread_local, "capture_chars", 0))
            remaining = max_chars - used
            if remaining <= 0:
                _thread_local.capture_truncated = True
                return len(s)
            if len(s) > remaining:
                buf.write(s[:remaining])
                _thread_local.capture_chars = max_chars
                _thread_local.capture_truncated = True
                return len(s)
            buf.write(s)
            _thread_local.capture_chars = used + len(s)
            return len(s)
        return object.__getattribute__(self, "_real").write(s)

    def flush(self) -> None:
        buf = getattr(_thread_local, "capture_buf", None)
        if buf is not None:
            buf.flush()
        object.__getattribute__(self, "_real").flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_real"), name)

    @staticmethod
    def start_capture(max_chars: int = THREAD_CAPTURE_MAX_CHARS) -> None:
        """Begin capturing stdout for the calling thread."""
        _thread_local.capture_buf = io.StringIO()
        _thread_local.capture_chars = 0
        _thread_local.capture_max_chars = max(10_000, int(max_chars))
        _thread_local.capture_truncated = False

    @staticmethod
    def stop_capture() -> tuple[str, bool]:
        """Stop capturing and return `(captured_text, truncated)`."""
        buf = getattr(_thread_local, "capture_buf", None)
        truncated = bool(getattr(_thread_local, "capture_truncated", False))
        _thread_local.capture_buf = None
        _thread_local.capture_chars = 0
        _thread_local.capture_max_chars = THREAD_CAPTURE_MAX_CHARS
        _thread_local.capture_truncated = False
        return (buf.getvalue() if buf is not None else "", truncated)

    @staticmethod
    def install() -> None:
        """Replace sys.stdout once at server startup."""
        if not isinstance(sys.stdout, _ThreadCapturingStdout):
            sys.stdout = _ThreadCapturingStdout(sys.stdout)  # type: ignore[assignment]


def _configure_db(conn: sqlite3.Connection) -> None:
    """Apply consistent SQLite PRAGMAs and Row factory."""
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("openssl fingerprint extraction failed, trying fallback: %s", exc)
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
    except (OSError, ValueError) as exc:
        logger.debug("PEM-to-DER fingerprint fallback failed: %s", exc)
    return None


def _parse_query_params(path: str) -> dict[str, list[str]]:
    """Parse URL query string parameters from a request path."""
    import urllib.parse

    if "?" not in path:
        return {}
    return urllib.parse.parse_qs(path.split("?", 1)[1])


def _get_int_param(
    params: dict[str, list[str]],
    key: str,
    default: int,
    min_val: int = 1,
    max_val: int = 500,
) -> int:
    """Extract an integer parameter with bounds clamping."""
    try:
        value = int(params.get(key, [str(default)])[0])
    except (TypeError, ValueError):
        value = default
    return max(min_val, min(value, max_val))


def _serialize_activity_event(event: ActivityEvent) -> dict[str, Any]:
    """Serialize an activity feed event to a JSON-safe dict."""
    details = event.details
    return dict(
        event_id=event.event_id,
        timestamp=event.timestamp,
        category=event.category,
        summary=event.summary,
        details=details,
    )


def _compute_command_reliability() -> CommandReliability:
    """Aggregate command reliability metrics from the activity feed."""
    result: CommandReliability = {
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
            ok_count = sum(1 for e in events if e.details.get("ok"))
            result["command_success_rate_pct"] = round(100.0 * ok_count / len(events), 1) if events else 0.0
            result["retry_count"] = sum(1 for e in events if e.details.get("retryable"))
            result["timeout_count"] = sum(
                1
                for e in events
                if str(e.details.get("error_code", "")).startswith("timeout")
            )
        # Pressure events
        pressure_events = feed.query(limit=50, category=ActivityCategory.RESOURCE_PRESSURE)
        result["memory_pressure_incidents"] = len(pressure_events)
        if pressure_events:
            latest_details = pressure_events[0].details
            if isinstance(latest_details, dict):
                result["last_pressure_level"] = str(latest_details.get("level", "none"))
    except SUBSYSTEM_ERRORS as exc:
        logger.debug("Command reliability metrics unavailable: %s", exc)
    return cast(CommandReliability, result)
