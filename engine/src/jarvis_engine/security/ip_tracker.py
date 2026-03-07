"""IP-based threat tracking with automatic escalation.

Tracks threatening IPs in a SQLite table, automatically escalating
from ALLOW to THROTTLE to BLOCK based on attempt counts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import TypedDict

from jarvis_engine._compat import UTC


class ThreatReport(TypedDict):
    """Result from :meth:`IPThreatTracker.get_threat_report`."""

    ip: str
    first_seen: str
    last_seen: str
    total_attempts: int
    attack_types: list[str]
    threat_score: float
    blocked_until: str | None
    notes: str
from jarvis_engine._shared import now_iso as _now_iso

logger = logging.getLogger(__name__)

# Escalation thresholds
_THROTTLE_THRESHOLD = 3
_BLOCK_1H_THRESHOLD = 5
_BLOCK_24H_THRESHOLD = 10
_PERMANENT_BLOCK_THRESHOLD = 20


class IPTracker:
    """Track and auto-escalate threatening IP addresses.

    Parameters
    ----------
    db:
        Open ``sqlite3.Connection``.
    write_lock:
        A ``threading.Lock`` to serialise writes (shared with other
        components that write to the same database).
    """

    def __init__(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        self._db = db
        self._lock = write_lock
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._lock:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS threat_ips (
                    ip             TEXT PRIMARY KEY,
                    first_seen     TEXT NOT NULL,
                    last_seen      TEXT NOT NULL,
                    total_attempts INTEGER NOT NULL DEFAULT 0,
                    attack_types   TEXT NOT NULL DEFAULT '[]',
                    threat_score   REAL NOT NULL DEFAULT 0.0,
                    blocked_until  TEXT,
                    notes          TEXT DEFAULT ''
                )
                """
            )
            self._db.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_attempt(self, ip: str, attack_type: str) -> str:
        """Record an attack attempt from *ip* and return the action taken.

        Returns one of ``"ALLOW"``, ``"THROTTLE"``, or ``"BLOCK"``.
        Automatic escalation ladder:
        - 3 attempts  -> THROTTLE
        - 5 attempts  -> BLOCK 1 hour
        - 10 attempts -> BLOCK 24 hours
        - 20+ attempts -> permanent BLOCK
        """
        now_str = _now_iso()
        with self._lock:
            row = self._db.execute(
                "SELECT total_attempts, attack_types FROM threat_ips WHERE ip = ?",
                (ip,),
            ).fetchone()

            if row is None:
                types = [attack_type]
                total = 1
                score = min(total * 1.0, 100.0)
                self._db.execute(
                    """
                    INSERT INTO threat_ips
                        (ip, first_seen, last_seen, total_attempts, attack_types, threat_score)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (ip, now_str, now_str, total, json.dumps(types), score),
                )
            else:
                total = row[0] + 1
                try:
                    types = json.loads(row[1])
                except (json.JSONDecodeError, TypeError):
                    types = []
                if attack_type not in types:
                    types.append(attack_type)
                score = min(total * 1.0, 100.0)
                self._db.execute(
                    """
                    UPDATE threat_ips
                    SET last_seen = ?, total_attempts = ?, attack_types = ?,
                        threat_score = ?
                    WHERE ip = ?
                    """,
                    (now_str, total, json.dumps(types), score, ip),
                )

            # Escalation logic
            action = "ALLOW"
            if total >= _PERMANENT_BLOCK_THRESHOLD:
                self._db.execute(
                    "UPDATE threat_ips SET blocked_until = 'permanent' WHERE ip = ?",
                    (ip,),
                )
                action = "BLOCK"
            elif total >= _BLOCK_24H_THRESHOLD:
                until = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
                self._db.execute(
                    "UPDATE threat_ips SET blocked_until = ? WHERE ip = ?",
                    (until, ip),
                )
                action = "BLOCK"
            elif total >= _BLOCK_1H_THRESHOLD:
                until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
                self._db.execute(
                    "UPDATE threat_ips SET blocked_until = ? WHERE ip = ?",
                    (until, ip),
                )
                action = "BLOCK"
            elif total >= _THROTTLE_THRESHOLD:
                action = "THROTTLE"

            self._db.commit()

        return action

    def is_blocked(self, ip: str) -> bool:
        """Return True if *ip* is currently blocked."""
        with self._lock:
            row = self._db.execute(
                "SELECT blocked_until FROM threat_ips WHERE ip = ?", (ip,)
            ).fetchone()
        if row is None or row[0] is None:
            return False
        blocked_until = row[0]
        if blocked_until == "permanent":
            return True
        try:
            expiry = datetime.fromisoformat(blocked_until)
            # Ensure timezone-aware comparison
            if expiry.tzinfo is None:
                return True  # treat naive datetimes as still blocked
            return datetime.now(UTC) < expiry
        except (ValueError, TypeError) as exc:
            logger.debug("Invalid blocked_until timestamp for IP %s: %s", ip, exc)
            return False

    def get_threat_report(self, ip: str) -> ThreatReport | None:
        """Return full threat history for *ip*, or None if not tracked."""
        with self._lock:
            row = self._db.execute(
                """
                SELECT ip, first_seen, last_seen, total_attempts, attack_types,
                       threat_score, blocked_until, notes
                FROM threat_ips WHERE ip = ?
                """,
                (ip,),
            ).fetchone()
        if row is None:
            return None
        try:
            attack_types = json.loads(row[4])
        except (json.JSONDecodeError, TypeError):
            attack_types = []
        return {
            "ip": row[0],
            "first_seen": row[1],
            "last_seen": row[2],
            "total_attempts": row[3],
            "attack_types": attack_types,
            "threat_score": row[5],
            "blocked_until": row[6],
            "notes": row[7],
        }

    def get_all_threats(self, min_score: float = 0.0) -> list[dict]:
        """Return all tracked IPs with threat_score >= *min_score*."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT ip, first_seen, last_seen, total_attempts, attack_types,
                       threat_score, blocked_until, notes
                FROM threat_ips WHERE threat_score >= ?
                ORDER BY threat_score DESC
                """,
                (min_score,),
            ).fetchall()
        result = []
        for row in rows:
            try:
                attack_types = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                attack_types = []
            result.append({
                "ip": row[0],
                "first_seen": row[1],
                "last_seen": row[2],
                "total_attempts": row[3],
                "attack_types": attack_types,
                "threat_score": row[5],
                "blocked_until": row[6],
                "notes": row[7],
            })
        return result

    def block_ip(self, ip: str, duration_hours: int | None = None) -> None:
        """Manually block an IP.  *None* = permanent block."""
        now_str = _now_iso()
        if duration_hours is None:
            blocked_until = "permanent"
        else:
            blocked_until = (datetime.now(UTC) + timedelta(hours=duration_hours)).isoformat()

        with self._lock:
            row = self._db.execute(
                "SELECT ip FROM threat_ips WHERE ip = ?", (ip,)
            ).fetchone()
            if row is None:
                self._db.execute(
                    """
                    INSERT INTO threat_ips
                        (ip, first_seen, last_seen, total_attempts, attack_types,
                         threat_score, blocked_until, notes)
                    VALUES (?, ?, ?, 0, '[]', 0.0, ?, 'manual block')
                    """,
                    (ip, now_str, now_str, blocked_until),
                )
            else:
                self._db.execute(
                    "UPDATE threat_ips SET blocked_until = ?, notes = 'manual block' WHERE ip = ?",
                    (blocked_until, ip),
                )
            self._db.commit()

    def unblock_ip(self, ip: str) -> None:
        """Manually unblock an IP."""
        with self._lock:
            self._db.execute(
                "UPDATE threat_ips SET blocked_until = NULL WHERE ip = ?",
                (ip,),
            )
            self._db.commit()
