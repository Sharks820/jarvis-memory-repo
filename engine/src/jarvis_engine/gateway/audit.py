"""Gateway decision audit trail for transparency.

Logs every LLM routing decision to a JSONL file so the user has full
visibility into which provider was chosen, why, and how it performed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from jarvis_engine._compat import UTC
from jarvis_engine._shared import now_iso as _now_iso

logger = logging.getLogger(__name__)


class AuditSummary(TypedDict):
    """Summary of gateway routing decisions over a time window."""

    period_hours: int
    total_decisions: int
    provider_breakdown: dict[str, int]
    total_cost_usd: float
    avg_latency_ms: float
    failure_count: int
    failure_rate_pct: float
    privacy_routed_count: int


# Maximum audit log size before rotation (5 MB)
_MAX_AUDIT_LOG_BYTES = 5 * 1024 * 1024


class GatewayAudit:
    """Logs every LLM routing decision to a JSONL file.

    Thread-safe: uses a lock around file writes so concurrent gateway
    calls from different threads don't corrupt the audit log.

    Performs simple size-based rotation: when the log exceeds 5 MB,
    the current file is renamed to .1 and a fresh log is started.
    """

    def __init__(self, audit_path: Path) -> None:
        self._path = audit_path
        self._lock = threading.Lock()

    def log_decision(
        self,
        *,
        provider: str,
        model: str,
        reason: str,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        success: bool,
        fallback_from: str = "",
        privacy_routed: bool = False,
    ) -> None:
        """Append a routing decision record to the JSONL audit log."""
        record = {
            "ts": _now_iso(),
            "provider": provider,
            "model": model,
            "reason": reason,
            "latency_ms": round(latency_ms, 1),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "success": success,
            "fallback_from": fallback_from,
            "privacy_routed": privacy_routed,
        }
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except OSError:
                logger.warning("Failed to write audit record to %s", self._path)

    def _rotate_if_needed(self) -> None:
        """Rotate the audit log if it exceeds the size limit.

        Must be called with self._lock held.  Renames the current file
        to ``<name>.1`` (overwriting any previous rotation) and lets the
        next write create a fresh file.
        """
        try:
            size = os.path.getsize(self._path)
        except OSError:
            return  # File doesn't exist yet — nothing to rotate
        if size < _MAX_AUDIT_LOG_BYTES:
            return
        rotated = self._path.with_suffix(self._path.suffix + ".1")
        try:
            # Atomic replace — avoids TOCTOU race of exists()+unlink()+rename()
            self._path.replace(rotated)
            logger.info(
                "Rotated audit log %s -> %s (%d bytes)", self._path, rotated, size
            )
        except OSError as exc:
            logger.warning("Failed to rotate audit log: %s", exc)

    def recent(self, n: int = 50) -> list[dict]:
        """Return the last *n* audit records from the log file.

        Reads only the tail of the file (estimated at ~500 bytes per line)
        to avoid loading the entire audit log into memory.
        """
        if n <= 0 or not self._path.exists():
            return []
        try:
            with open(self._path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return []
                # Estimate ~500 bytes per JSONL line; add 2x buffer for safety
                read_size = min(size, 500 * n * 2)
                f.seek(max(0, size - read_size))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        lines = tail.strip().splitlines()
        # If we seeked into the middle of a line, the first line is partial;
        # drop it unless we read from the very beginning of the file.
        if size > read_size and lines:
            lines = lines[1:]
        result: list[dict] = []
        for line in lines[-n:]:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Skipping malformed audit log line")
                continue
        return result

    def summary(self, hours: int = 24) -> AuditSummary:
        """Summarize routing decisions over the last *hours* hours."""
        hours = max(1, hours)
        # Scale read size with time window — ~20 calls/hr baseline, 2x headroom
        read_limit = max(500, hours * 40)
        records = self.recent(read_limit)
        cutoff = datetime.now(UTC).timestamp() - (hours * 3600)
        recent: list[dict] = []
        for r in records:
            try:
                ts_str = r["ts"]
                # Python 3.10 fromisoformat() cannot parse timezone suffixes;
                # strip the +00:00 suffix since all timestamps are UTC anyway.
                if ts_str.endswith("+00:00"):
                    ts_str = ts_str[:-6]
                ts = datetime.fromisoformat(ts_str).replace(tzinfo=UTC).timestamp()
                if ts >= cutoff:
                    recent.append(r)
            except (KeyError, ValueError):
                logger.debug("Skipping audit record with missing/invalid timestamp")
                continue

        provider_counts: dict[str, int] = {}
        total_cost = 0.0
        total_latency = 0.0
        failures = 0
        privacy_count = 0

        for r in recent:
            p = r.get("provider", "unknown")
            provider_counts[p] = provider_counts.get(p, 0) + 1
            total_cost += r.get("cost_usd", 0.0)
            total_latency += r.get("latency_ms", 0.0)
            if not r.get("success", True):
                failures += 1
            if r.get("privacy_routed", False):
                privacy_count += 1

        count = len(recent)
        return {
            "period_hours": hours,
            "total_decisions": count,
            "provider_breakdown": provider_counts,
            "total_cost_usd": round(total_cost, 4),
            "avg_latency_ms": round(total_latency / count, 1) if count else 0.0,
            "failure_count": failures,
            "failure_rate_pct": round(failures / count * 100, 1) if count else 0.0,
            "privacy_routed_count": privacy_count,
        }
