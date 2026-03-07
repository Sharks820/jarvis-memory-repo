"""Data ingestion, feedback, activity feed, and alert queue endpoints."""

from __future__ import annotations

import logging
from http import HTTPStatus
from jarvis_engine._constants import memory_db_path as _memory_db_path
from jarvis_engine.mobile_routes._helpers import (
    ALLOWED_KINDS,
    ALLOWED_SOURCES,
    _configure_db,
    _get_int_param,
    _parse_query_params,
    _serialize_activity_event,
)

logger = logging.getLogger(__name__)


class DataRoutesMixin:
    """Ingest, feedback, activity feed, and alert queue endpoints."""

    def _handle_post_ingest(self) -> None:
        payload, _ = self._read_json_body(max_content_length=50_000)
        if payload is None:
            return

        source = str(payload.get("source", "user"))
        kind = str(payload.get("kind", "episodic"))
        task_id = str(payload.get("task_id", "")).strip()
        content = str(payload.get("content", "")).strip()

        if source not in ALLOWED_SOURCES:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid source."})
            return
        if kind not in ALLOWED_KINDS:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid kind."})
            return
        if not task_id or len(task_id) > 128:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid task_id."})
            return
        if not content or len(content) > 20_000:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid content."})
            return

        rec = self.server.pipeline.ingest(
            source=source,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            task_id=task_id,
            content=content,
        )
        self._write_json(
            HTTPStatus.CREATED,
            {
                "ok": True,
                "record_id": rec.record_id,
                "ts": rec.ts,
                "source": rec.source,
                "kind": rec.kind,
                "task_id": rec.task_id,
            },
        )

    def _handle_post_feedback(self) -> None:
        payload, body_bytes = self._read_json_body(max_content_length=10_000)
        if payload is None:
            return
        quality = payload.get("quality")
        if quality not in ("positive", "negative", "neutral"):
            self._write_json(HTTPStatus.BAD_REQUEST, {
                "ok": False, "error": "quality must be 'positive', 'negative', or 'neutral'",
            })
            return
        route = str(payload.get("route", "")).strip()[:100]
        comment = str(payload.get("comment", "")).strip()[:500]
        db_path = _memory_db_path(self._root)
        if not db_path.exists():
            self._write_json(HTTPStatus.OK, {"ok": True, "recorded": False, "reason": "DB not available"})
            return
        fb_db = None
        try:
            import sqlite3 as _fb_sqlite3
            fb_db = _fb_sqlite3.connect(str(db_path), check_same_thread=False)
            _configure_db(fb_db)
            from jarvis_engine.learning.feedback import ResponseFeedbackTracker
            tracker = ResponseFeedbackTracker(fb_db)
            tracker.record_explicit_feedback(quality, route, comment)
            self._write_json(HTTPStatus.OK, {"ok": True, "recorded": True, "quality": quality, "route": route})
        except Exception as exc:
            logger.error("Feedback recording failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Feedback recording failed."})
        finally:
            if fb_db is not None:
                fb_db.close()

    def _handle_get_activity(self) -> None:
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine.activity_feed import get_activity_feed
        except ImportError:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Activity feed not available."})
            return
        # Parse query params
        qs = _parse_query_params(self.path)
        limit = _get_int_param(qs, "limit", 50)
        category = qs.get("category", [None])[0]
        since = qs.get("since", [None])[0]
        try:
            feed = get_activity_feed()
            events = feed.query(limit=limit, category=category, since=since)
            stats = feed.stats()
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "events": [_serialize_activity_event(e) for e in events],
                "stats": stats,
            })
        except Exception as exc:
            logger.error("activity feed query failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Activity feed query failed."})

    def _handle_get_alerts_pending(self) -> None:
        """Return and drain all pending proactive alerts for the phone."""
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine.proactive.alert_queue import drain_alerts
            alerts = drain_alerts(self._root, limit=50)
            self._write_json(HTTPStatus.OK, {"ok": True, "alerts": alerts})
        except Exception as exc:
            logger.error("Alert queue drain failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "alerts": []})
