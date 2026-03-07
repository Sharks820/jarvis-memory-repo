"""Persistent alert queue for mobile-side polling.

Alerts are appended as JSON lines, and retrieved + cleared by the phone
via GET /alerts/pending.  This bridges the desktop proactive engine
with the Android ProactiveAlertReceiver.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_QUEUE_LOCK = threading.Lock()


def _queue_path(root: Path) -> Path:
    from jarvis_engine._constants import runtime_dir
    return runtime_dir(root) / "pending_alerts.jsonl"


def enqueue_alert(
    root: Path,
    alert: dict[str, Any],
    *,
    dedup_window_sec: int = 300,
) -> str:
    """Append an alert to the pending queue.  Returns the alert id.

    *alert* must contain at least ``type``, ``title``, ``body``.
    ``group_key`` and ``priority`` are optional.

    Duplicate alerts (same type+title within *dedup_window_sec*) are
    silently dropped.
    """
    alert_id = alert.get("id") or str(uuid.uuid4())[:12]
    now_ts = time.time()
    record = {
        "id": alert_id,
        "type": str(alert.get("type", "general")),
        "title": str(alert.get("title", "Jarvis"))[:200],
        "body": str(alert.get("body", ""))[:500],
        "group_key": str(alert.get("group_key", "jarvis_default")),
        "priority": str(alert.get("priority", "normal")),
        "ts": now_ts,
    }

    path = _queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _QUEUE_LOCK:
        # Dedup check: read recent alerts with same type+title
        existing: list[dict[str, Any]] = []
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            existing.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.debug("Skipping malformed alert queue entry")
                            continue
            except OSError as exc:
                logger.debug("Failed to read alert queue: %s", exc)

        for prev in existing:
            if (
                prev.get("type") == record["type"]
                and prev.get("title") == record["title"]
                and now_ts - float(prev.get("ts", 0)) < dedup_window_sec
            ):
                logger.debug("Dedup: alert '%s' already queued", record["title"])
                return str(prev.get("id", alert_id))

        # Append
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                # Re-write existing + new (prune stale > 1 hour)
                cutoff = now_ts - 3600
                for prev in existing:
                    if float(prev.get("ts", 0)) >= cutoff:
                        fh.write(json.dumps(prev, ensure_ascii=True) + "\n")
                fh.write(json.dumps(record, ensure_ascii=True) + "\n")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("Failed to write alert queue: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                logger.debug("Failed to clean up alert queue temp file: %s", cleanup_exc)
            raise

    logger.info("Queued alert: %s — %s", record["type"], record["title"])
    return alert_id


def drain_alerts(root: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return all pending alerts and clear the queue.

    Called by GET /alerts/pending from the phone.
    """
    path = _queue_path(root)
    with _QUEUE_LOCK:
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []

        alerts: list[dict[str, Any]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                alerts.append(json.loads(line))
            except json.JSONDecodeError:
                logger.debug("Skipping malformed alert entry during drain")
                continue

        # Clear the queue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Failed to clear alert queue file: %s", exc)

    return alerts[:limit]


def peek_alerts(root: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return pending alerts WITHOUT clearing the queue."""
    path = _queue_path(root)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    alerts: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            alerts.append(json.loads(line))
        except json.JSONDecodeError:
            logger.debug("Skipping malformed alert entry during peek")
            continue
    return alerts[:limit]
