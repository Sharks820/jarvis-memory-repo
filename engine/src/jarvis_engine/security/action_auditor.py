"""Bot governance audit trail — every AI action logged for transparency.

Records action type, detail, trigger, resource usage and an input hash for
each action the bot takes.  Entries persist to a JSONL file and are kept in
a fixed-size in-memory ring buffer for fast dashboard queries.
"""

from __future__ import annotations

import collections
import json
import logging
import threading
from pathlib import Path

from typing import TypedDict

from jarvis_engine._shared import now_iso, sha256_hex

logger = logging.getLogger(__name__)


class DailySummary(TypedDict):
    """Result from :meth:`ActionAuditor.daily_summary`."""

    total_actions: int
    by_type: dict[str, int]
    by_trigger: dict[str, int]


_RING_BUFFER_SIZE = 500


class ActionAuditor:
    """Log every action the AI takes for full governance transparency.

    Parameters
    ----------
    log_dir:
        Directory for the ``action_audit.jsonl`` file.  Created if missing.
    """

    def __init__(self, log_dir: Path) -> None:
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "action_audit.jsonl"
        self._lock = threading.Lock()
        self._recent: collections.deque[dict] = collections.deque(
            maxlen=_RING_BUFFER_SIZE,
        )
        self._total_count: int = 0

    # Public API

    def log_action(
        self,
        action_type: str,
        detail: str,
        trigger: str,
        resource_usage: dict | None = None,
    ) -> None:
        """Record an action entry.

        Parameters
        ----------
        action_type:
            One of ``command``, ``api_call``, ``file_access``, ``proactive``,
            ``learning``.
        detail:
            Human-readable description (truncated to 500 characters).
        trigger:
            What caused the action: ``user_command``, ``proactive_engine``,
            ``scheduled``, ``internal``.
        resource_usage:
            Optional dict with keys like ``tokens``, ``cpu_time``, ``memory``.
        """
        truncated_detail = detail[:500] if len(detail) > 500 else detail
        input_hash = sha256_hex(detail)[:16]

        entry: dict = {
            "timestamp": now_iso(),
            "action_type": action_type,
            "detail": truncated_detail,
            "trigger": trigger,
            "input_hash": input_hash,
        }
        if resource_usage is not None:
            entry["resource_usage"] = resource_usage

        with self._lock:
            self._recent.append(entry)
            self._total_count += 1
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n"
                    )
            except OSError as exc:
                logger.warning(
                    "Failed to write action audit entry to %s: %s", self._path, exc
                )

    def action_count(self) -> int:
        """Return total actions logged this session."""
        with self._lock:
            return self._total_count

    def recent_actions(self, limit: int = 50) -> list[dict]:
        """Return the most recent actions (up to *limit*).

        Returns entries in chronological order (oldest first within the
        returned window).
        """
        with self._lock:
            buf = list(self._recent)
        # Return the last *limit* entries in chronological order
        return buf[-limit:] if limit < len(buf) else buf

    def daily_summary(self) -> DailySummary:
        """Return a summary of today's actions.

        Returns
        -------
        dict with keys:
            ``total_actions`` — int
            ``by_type`` — dict[str, int]
            ``by_trigger`` — dict[str, int]
        """
        with self._lock:
            entries = list(self._recent)

        today = now_iso()[:10]
        by_type: dict[str, int] = {}
        by_trigger: dict[str, int] = {}
        total = 0

        for entry in entries:
            # Only count entries from today
            ts = entry.get("timestamp", "")
            if not ts.startswith(today):
                continue
            total += 1
            atype = entry.get("action_type", "unknown")
            by_type[atype] = by_type.get(atype, 0) + 1
            trigger = entry.get("trigger", "unknown")
            by_trigger[trigger] = by_trigger.get(trigger, 0) + 1

        return {
            "total_actions": total,
            "by_type": by_type,
            "by_trigger": by_trigger,
        }
