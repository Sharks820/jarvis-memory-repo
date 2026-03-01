"""Attack pattern memory — Wave 10 security hardening.

Stores and retrieves attack patterns in SQLite for threat intelligence,
pattern deduplication, and forensic analysis.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS attack_patterns (
    pattern_id         TEXT PRIMARY KEY,
    category           TEXT NOT NULL,
    payload_signature  TEXT NOT NULL,
    detection_method   TEXT NOT NULL DEFAULT '',
    first_seen         TEXT NOT NULL,
    last_seen          TEXT NOT NULL,
    frequency          INTEGER NOT NULL DEFAULT 1,
    source_ips         TEXT NOT NULL DEFAULT '[]',
    notes              TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_CATEGORY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_attack_patterns_category
    ON attack_patterns (category)
"""

_CREATE_LAST_SEEN_INDEX = """
CREATE INDEX IF NOT EXISTS idx_attack_patterns_last_seen
    ON attack_patterns (last_seen)
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payload_hash(payload: str) -> str:
    """SHA-256 hex digest of the payload, used as the pattern_id."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """Simple whitespace + lowercased tokenization for Jaccard similarity."""
    return set(text.lower().split())


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# AttackPatternMemory
# ---------------------------------------------------------------------------

class AttackPatternMemory:
    """SQLite-backed memory of observed attack patterns.

    Usage::

        import sqlite3, threading
        db = sqlite3.connect(":memory:")
        lock = threading.Lock()
        mem = AttackPatternMemory(db, lock)
        mem.record_attack("injection", "ignore previous instructions", "pattern_scan")
    """

    def __init__(self, db: sqlite3.Connection, write_lock: threading.Lock) -> None:
        self._db = db
        self._lock = write_lock
        self._init_schema()

    # -- Schema -------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._db.cursor()
            cur.execute(_CREATE_TABLE)
            cur.execute(_CREATE_CATEGORY_INDEX)
            cur.execute(_CREATE_LAST_SEEN_INDEX)
            self._db.commit()

    # -- Record attack ------------------------------------------------------

    def record_attack(
        self,
        category: str,
        payload: str,
        detection_method: str,
        source_ip: str = "",
    ) -> str:
        """Record (or upsert) an attack pattern.

        Returns the pattern_id (SHA-256 of the payload).
        """
        pid = _payload_hash(payload)
        now = _now_iso()

        with self._lock:
            cur = self._db.cursor()
            cur.execute("SELECT pattern_id, frequency, source_ips FROM attack_patterns WHERE pattern_id = ?", (pid,))
            row = cur.fetchone()

            if row is None:
                # New pattern
                ips_json = json.dumps([source_ip] if source_ip else [])
                cur.execute(
                    """INSERT INTO attack_patterns
                       (pattern_id, category, payload_signature, detection_method,
                        first_seen, last_seen, frequency, source_ips, notes)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, '')""",
                    (pid, category, payload, detection_method, now, now, ips_json),
                )
            else:
                # Upsert — increment frequency, update last_seen, append IP
                _, freq, ips_raw = row
                try:
                    existing_ips: list[str] = json.loads(ips_raw)
                except (json.JSONDecodeError, TypeError):
                    existing_ips = []
                if source_ip and source_ip not in existing_ips:
                    existing_ips.append(source_ip)
                cur.execute(
                    """UPDATE attack_patterns
                       SET frequency = ?, last_seen = ?, source_ips = ?, detection_method = ?
                       WHERE pattern_id = ?""",
                    (freq + 1, now, json.dumps(existing_ips), detection_method, pid),
                )
            self._db.commit()

        return pid

    # -- Similarity search --------------------------------------------------

    def find_similar(self, payload: str, threshold: float = 0.8, limit: int = 1000) -> list[dict[str, Any]]:
        """Find patterns with token-overlap (Jaccard) similarity >= threshold.

        Parameters
        ----------
        limit:
            Maximum number of rows to scan from the database.  Prevents
            unbounded memory usage on large attack pattern tables.
        """
        target_tokens = _tokenize(payload)
        results: list[dict[str, Any]] = []

        with self._lock:
            cur = self._db.cursor()
            cur.execute(
                "SELECT pattern_id, category, payload_signature, detection_method, "
                "first_seen, last_seen, frequency, source_ips, notes "
                "FROM attack_patterns ORDER BY last_seen DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        for row in rows:
            pid, cat, sig, det, first, last, freq, ips, notes = row
            sim = _jaccard(target_tokens, _tokenize(sig))
            if sim >= threshold:
                results.append({
                    "pattern_id": pid,
                    "category": cat,
                    "payload_signature": sig,
                    "detection_method": det,
                    "first_seen": first,
                    "last_seen": last,
                    "frequency": freq,
                    "source_ips": json.loads(ips) if ips else [],
                    "notes": notes,
                    "similarity": round(sim, 4),
                })

        results.sort(key=lambda r: r["similarity"], reverse=True)
        return results

    # -- Intelligence summary -----------------------------------------------

    def get_attack_intelligence(self) -> dict[str, Any]:
        """Return summary statistics for stored attack patterns."""
        with self._lock:
            cur = self._db.cursor()

            # Total patterns
            cur.execute("SELECT COUNT(*) FROM attack_patterns")
            total = cur.fetchone()[0]

            # Top categories by frequency
            cur.execute(
                """SELECT category, SUM(frequency) AS total_freq
                   FROM attack_patterns
                   GROUP BY category
                   ORDER BY total_freq DESC
                   LIMIT 10"""
            )
            top_categories = [{"category": r[0], "total_frequency": r[1]} for r in cur.fetchall()]

            # Recent attacks (last 20)
            cur.execute(
                """SELECT pattern_id, category, payload_signature, last_seen, frequency
                   FROM attack_patterns
                   ORDER BY last_seen DESC
                   LIMIT 20"""
            )
            recent = [
                {
                    "pattern_id": r[0],
                    "category": r[1],
                    "payload_signature": r[2][:100],
                    "last_seen": r[3],
                    "frequency": r[4],
                }
                for r in cur.fetchall()
            ]

            # Frequency trends (patterns seen more than once)
            cur.execute(
                """SELECT COUNT(*) FROM attack_patterns WHERE frequency > 1"""
            )
            recurring = cur.fetchone()[0]

            # Total frequency sum
            cur.execute("SELECT COALESCE(SUM(frequency), 0) FROM attack_patterns")
            total_events = cur.fetchone()[0]

        return {
            "total_patterns": total,
            "total_events": total_events,
            "recurring_patterns": recurring,
            "top_categories": top_categories,
            "recent_attacks": recent,
        }

    # -- Query by category --------------------------------------------------

    def get_patterns_by_category(self, category: str) -> list[dict[str, Any]]:
        """Return all attack patterns in a given category."""
        with self._lock:
            cur = self._db.cursor()
            cur.execute(
                """SELECT pattern_id, category, payload_signature, detection_method,
                          first_seen, last_seen, frequency, source_ips, notes
                   FROM attack_patterns
                   WHERE category = ?
                   ORDER BY last_seen DESC""",
                (category,),
            )
            rows = cur.fetchall()
        return [
            {
                "pattern_id": r[0],
                "category": r[1],
                "payload_signature": r[2],
                "detection_method": r[3],
                "first_seen": r[4],
                "last_seen": r[5],
                "frequency": r[6],
                "source_ips": json.loads(r[7]) if r[7] else [],
                "notes": r[8],
            }
            for r in rows
        ]
