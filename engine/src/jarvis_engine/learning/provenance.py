"""SQLite-backed provenance storage for learning shadow mode."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from jarvis_engine._shared import now_iso as _now_iso

from jarvis_engine.learning.trust import LearningTrustMetadata, learning_provenance_enabled


class PolicyEventRecord(TypedDict):
    id: int
    subject_type: str
    subject_id: str
    action: str
    verdict: str
    policy_mode: str
    reason: str
    recorded_at: str
    metadata_json: str


def _serialize_payload(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


class LearningProvenanceStore:
    """Dual-write helper for trust metadata and shadow policy events."""

    def __init__(
        self,
        db: sqlite3.Connection,
        write_lock: threading.Lock | None = None,
        db_lock: threading.Lock | None = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock or threading.Lock()
        self._db_lock = db_lock or threading.Lock()
        self._enabled = learning_provenance_enabled()
        if self._enabled:
            self._init_schema()

    def _init_schema(self) -> None:
        with self._write_lock:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_provenance (
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    learning_lane TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    promotion_state TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_channel TEXT NOT NULL,
                    source_uri TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL,
                    artifact_kind TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT 'text/plain',
                    scanner_verdict TEXT NOT NULL DEFAULT '',
                    scanner_details TEXT NOT NULL DEFAULT '',
                    approved_by_owner INTEGER NOT NULL DEFAULT 0,
                    approved_at TEXT NOT NULL DEFAULT '',
                    correlation_id TEXT NOT NULL DEFAULT '',
                    mission_id TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    promotion_reason TEXT NOT NULL DEFAULT '',
                    blocked_reason TEXT NOT NULL DEFAULT '',
                    derived_from_artifact INTEGER NOT NULL DEFAULT 0,
                    policy_mode TEXT NOT NULL DEFAULT 'audit_only',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (subject_type, subject_id)
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS trust_policy_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    policy_mode TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    recorded_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_quarantine (
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    source_channel TEXT NOT NULL,
                    artifact_kind TEXT NOT NULL,
                    safe_summary TEXT NOT NULL,
                    raw_preview TEXT NOT NULL DEFAULT '',
                    quarantine_reason TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    released_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (subject_type, subject_id)
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS threat_memory_indicators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    indicator_type TEXT NOT NULL,
                    indicator_value TEXT NOT NULL,
                    source_hash TEXT NOT NULL DEFAULT '',
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_provenance_lane ON learning_provenance(learning_lane, trust_level)"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_trust_policy_events_subject ON trust_policy_events(subject_type, subject_id)"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifact_quarantine_expires ON artifact_quarantine(expires_at)"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_threat_indicators_subject ON threat_memory_indicators(subject_type, subject_id)"
            )
            self._db.commit()

    def record_subject(
        self,
        *,
        subject_type: str,
        subject_id: str,
        metadata: LearningTrustMetadata,
    ) -> None:
        if not self._enabled:
            return
        now = _now_iso()
        metadata_json = _serialize_payload(metadata)
        with self._write_lock:
            self._db.execute(
                """
                INSERT INTO learning_provenance (
                    subject_type, subject_id, learning_lane, trust_level,
                    promotion_state, source_type, source_channel, source_uri,
                    source_hash, artifact_kind, mime_type, scanner_verdict,
                    scanner_details, approved_by_owner, approved_at,
                    correlation_id, mission_id, first_seen_at, last_used_at,
                    promotion_reason, blocked_reason, derived_from_artifact,
                    policy_mode, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject_type, subject_id) DO UPDATE SET
                    learning_lane=excluded.learning_lane,
                    trust_level=excluded.trust_level,
                    promotion_state=excluded.promotion_state,
                    source_type=excluded.source_type,
                    source_channel=excluded.source_channel,
                    source_uri=excluded.source_uri,
                    source_hash=excluded.source_hash,
                    artifact_kind=excluded.artifact_kind,
                    mime_type=excluded.mime_type,
                    scanner_verdict=excluded.scanner_verdict,
                    scanner_details=excluded.scanner_details,
                    approved_by_owner=excluded.approved_by_owner,
                    approved_at=excluded.approved_at,
                    correlation_id=excluded.correlation_id,
                    mission_id=excluded.mission_id,
                    last_used_at=excluded.last_used_at,
                    promotion_reason=excluded.promotion_reason,
                    blocked_reason=excluded.blocked_reason,
                    derived_from_artifact=excluded.derived_from_artifact,
                    policy_mode=excluded.policy_mode,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    subject_type,
                    subject_id,
                    metadata["learning_lane"],
                    metadata["trust_level"],
                    metadata["promotion_state"],
                    metadata["source_type"],
                    metadata["source_channel"],
                    metadata["source_uri"],
                    metadata["source_hash"],
                    metadata["artifact_kind"],
                    metadata["mime_type"],
                    metadata["scanner_verdict"],
                    metadata["scanner_details"],
                    1 if metadata["approved_by_owner"] else 0,
                    metadata["approved_at"],
                    metadata["correlation_id"],
                    metadata["mission_id"],
                    metadata["first_seen_at"],
                    metadata["last_used_at"],
                    metadata["promotion_reason"],
                    metadata["blocked_reason"],
                    1 if metadata["derived_from_artifact"] else 0,
                    metadata["policy_mode"],
                    metadata_json,
                    now,
                ),
            )
            self._db.commit()

    def record_policy_event(
        self,
        *,
        subject_type: str,
        subject_id: str,
        action: str,
        verdict: str,
        policy_mode: str,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return
        with self._write_lock:
            self._db.execute(
                """
                INSERT INTO trust_policy_events (
                    subject_type, subject_id, action, verdict, policy_mode,
                    reason, metadata_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject_type,
                    subject_id,
                    action,
                    verdict,
                    policy_mode,
                    reason,
                    _serialize_payload(metadata or {}),
                    _now_iso(),
                ),
            )
            self._db.commit()

    def quarantine_artifact(
        self,
        *,
        subject_type: str,
        subject_id: str,
        source_hash: str,
        source_channel: str,
        artifact_kind: str,
        safe_summary: str,
        quarantine_reason: str,
        metadata: dict[str, Any] | None = None,
        raw_preview: str = "",
        ttl_days: int = 30,
    ) -> None:
        if not self._enabled:
            return
        first_seen_at = _now_iso()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
        with self._write_lock:
            self._db.execute(
                """
                INSERT INTO artifact_quarantine (
                    subject_type, subject_id, source_hash, source_channel,
                    artifact_kind, safe_summary, raw_preview,
                    quarantine_reason, metadata_json, first_seen_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject_type, subject_id) DO UPDATE SET
                    source_hash=excluded.source_hash,
                    source_channel=excluded.source_channel,
                    artifact_kind=excluded.artifact_kind,
                    safe_summary=excluded.safe_summary,
                    raw_preview=excluded.raw_preview,
                    quarantine_reason=excluded.quarantine_reason,
                    metadata_json=excluded.metadata_json,
                    expires_at=excluded.expires_at
                """,
                (
                    subject_type,
                    subject_id,
                    source_hash,
                    source_channel,
                    artifact_kind,
                    safe_summary,
                    raw_preview,
                    quarantine_reason,
                    _serialize_payload(metadata or {}),
                    first_seen_at,
                    expires_at,
                ),
            )
            self._db.commit()

    def record_threat_indicator(
        self,
        *,
        indicator_type: str,
        indicator_value: str,
        subject_type: str,
        subject_id: str,
        source_hash: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return
        with self._write_lock:
            self._db.execute(
                """
                INSERT INTO threat_memory_indicators (
                    indicator_type, indicator_value, source_hash, subject_type,
                    subject_id, reason, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    indicator_type,
                    indicator_value,
                    source_hash,
                    subject_type,
                    subject_id,
                    reason,
                    _serialize_payload(metadata or {}),
                    _now_iso(),
                ),
            )
            self._db.commit()

    def get_subject(self, subject_type: str, subject_id: str) -> dict[str, Any] | None:
        if not self._enabled:
            return None
        with self._db_lock:
            cur = self._db.execute(
                "SELECT * FROM learning_provenance WHERE subject_type = ? AND subject_id = ?",
                (subject_type, subject_id),
            )
            row = cur.fetchone()
            columns = [description[0] for description in cur.description or []]
        if row is None:
            return None
        return dict(zip(columns, row, strict=False))

    def get_policy_events(self, subject_type: str, subject_id: str) -> list[PolicyEventRecord]:
        if not self._enabled:
            return []
        with self._db_lock:
            cur = self._db.execute(
                "SELECT id, subject_type, subject_id, action, verdict, policy_mode, reason, recorded_at, metadata_json "
                "FROM trust_policy_events WHERE subject_type = ? AND subject_id = ? ORDER BY id",
                (subject_type, subject_id),
            )
            rows = cur.fetchall()
        return [
            {
                "id": row[0],
                "subject_type": row[1],
                "subject_id": row[2],
                "action": row[3],
                "verdict": row[4],
                "policy_mode": row[5],
                "reason": row[6],
                "recorded_at": row[7],
                "metadata_json": row[8],
            }
            for row in rows
        ]
