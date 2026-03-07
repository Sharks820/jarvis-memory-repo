from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Any

from jarvis_engine._compat import UTC
from jarvis_engine._constants import KG_METRICS_LOG as _KG_METRICS_LOG
from jarvis_engine._constants import SELF_TEST_HISTORY as _SELF_TEST_HISTORY
from jarvis_engine._constants import memory_db_path as _memory_db_path
from jarvis_engine._constants import runtime_dir as _runtime_dir

logger = logging.getLogger(__name__)


class IntelligenceRoutesMixin:
    """Endpoint handlers for intelligence growth, learning, and knowledge export."""

    def _gather_intelligence_growth(self, *, reliability_cache: dict[str, Any] | None = None) -> dict[str, Any]:
        """Collect real intelligence growth metrics from all subsystems."""
        from jarvis_engine.mobile_routes._helpers import _compute_command_reliability

        root = self._root
        metrics: dict[str, Any] = {
            "facts_total": 0,
            "facts_last_7d": 0,
            "corrections_applied": 0,
            "corrections_last_7d": 0,
            "consolidations_run": 0,
            "entities_merged": 0,
            "kg_nodes": 0,
            "kg_edges": 0,
            "memory_records": 0,
            "branches": {},
            "growth_trend": "stable",
            "last_self_test_score": 0.0,
            "command_success_rate_pct": 0.0,
            "timeout_count": 0,
            "memory_pressure_incidents": 0,
        }

        # --- Knowledge graph metrics from KG history ---
        try:
            from jarvis_engine.proactive.kg_metrics import kg_growth_trend, load_kg_history

            history_path = _runtime_dir(root) / _KG_METRICS_LOG
            history = load_kg_history(history_path, limit=50)
            if history:
                latest = history[-1]
                metrics["kg_nodes"] = int(latest.get("node_count", 0))
                metrics["kg_edges"] = int(latest.get("edge_count", 0))
                metrics["facts_total"] = metrics["kg_nodes"]
                branch_counts = latest.get("branch_counts", {})
                if isinstance(branch_counts, dict):
                    metrics["branches"] = {str(k): int(v) for k, v in branch_counts.items()}

                cutoff_7d = (datetime.now(UTC) - timedelta(days=7)).isoformat()
                recent_entries = [
                    e for e in history
                    if str(e.get("ts", "")) >= cutoff_7d
                ]
                if recent_entries and len(history) > len(recent_entries):
                    before_idx = len(history) - len(recent_entries) - 1
                    if before_idx >= 0:
                        old_count = int(history[before_idx].get("node_count", 0))
                        metrics["facts_last_7d"] = max(0, metrics["kg_nodes"] - old_count)

                try:
                    trend = kg_growth_trend(history)
                    if isinstance(trend, dict):
                        node_growth = trend.get("node_growth", 0)
                        if isinstance(node_growth, (int, float)):
                            if node_growth > 0:
                                metrics["growth_trend"] = "increasing"
                            elif node_growth < 0:
                                metrics["growth_trend"] = "declining"
                            else:
                                metrics["growth_trend"] = "stable"
                except (ValueError, TypeError, KeyError) as exc:
                    logger.debug("intelligence growth metric failed: %s", exc)
        except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
            logger.debug("Intelligence growth: KG metrics unavailable: %s", exc)

        # --- Activity feed: corrections ---
        try:
            from jarvis_engine.activity_feed import get_activity_feed

            feed = get_activity_feed()
            stats = feed.stats()
            if isinstance(stats, dict):
                metrics["corrections_applied"] = int(stats.get("correction_applied", 0))
                metrics["consolidations_run"] = int(stats.get("consolidation", 0))
            since_7d = (datetime.now(UTC) - timedelta(days=7)).isoformat()
            try:
                recent_events = feed.query(limit=500, category="correction_applied", since=since_7d)
                metrics["corrections_last_7d"] = len(recent_events)
            except (ImportError, RuntimeError, ValueError, TypeError) as exc:
                logger.debug("intelligence growth metric failed: %s", exc)
        except (ImportError, RuntimeError, ValueError, TypeError) as exc:
            logger.debug("Intelligence growth: activity feed unavailable: %s", exc)

        # --- Command reliability and pressure ---
        reliability = reliability_cache if reliability_cache is not None else _compute_command_reliability()
        metrics["command_success_rate_pct"] = reliability["command_success_rate_pct"]
        metrics["timeout_count"] = reliability["timeout_count"]
        metrics["memory_pressure_incidents"] = reliability["memory_pressure_incidents"]

        # --- Memory engine: record count ---
        try:
            server = self.server
            mem_engine = server.ensure_memory_engine()
            if mem_engine is not None:
                metrics["memory_records"] = mem_engine.count_records()
        except (ImportError, RuntimeError, OSError) as exc:
            logger.debug("Intelligence growth: memory records unavailable: %s", exc)

        # --- Self-test score ---
        try:
            from jarvis_engine._shared import load_jsonl_tail

            self_test_path = _runtime_dir(root) / _SELF_TEST_HISTORY
            tail = load_jsonl_tail(self_test_path, limit=1)
            if tail:
                latest_test = tail[-1]
                score = latest_test.get("average_score", 0.0)
                metrics["last_self_test_score"] = round(float(score), 3)
        except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            logger.debug("Intelligence growth: self-test history unavailable: %s", exc)

        # --- Capability history for overall trend confirmation ---
        try:
            from jarvis_engine.growth_tracker import read_history

            cap_path = root / ".planning" / "capability_history.jsonl"
            cap_rows = read_history(cap_path)
            if len(cap_rows) >= 2:
                latest_score = float(cap_rows[-1].get("score_pct", 0.0))
                prev_score = float(cap_rows[-2].get("score_pct", 0.0))
                if latest_score > prev_score:
                    metrics["growth_trend"] = "increasing"
                elif latest_score < prev_score:
                    metrics["growth_trend"] = "declining"
        except (ImportError, OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.debug("Intelligence growth: capability history unavailable: %s", exc)

        # --- Active learning missions ---
        try:
            mission_file = _runtime_dir(root) / "learning_missions.json"
            active_missions: list[dict[str, Any]] = []
            if mission_file.exists():
                from jarvis_engine._shared import load_json_file

                mission_data = load_json_file(mission_file, {}, expected_type=dict)
                if mission_data:
                    rows = mission_data.get("missions", [])
                    if isinstance(rows, list):
                        active_missions = [
                            m for m in rows
                            if isinstance(m, dict)
                            and str(m.get("status", "")).lower() not in {"completed", "failed", "cancelled", "exhausted"}
                        ]
            metrics["mission_count"] = len(active_missions)
            metrics["active_missions"] = [
                {
                    "topic": str(m.get("topic", "")),
                    "status": str(m.get("status", "")),
                    "findings": int(m.get("verified_findings", 0) or 0),
                }
                for m in active_missions[:5]
            ]
        except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            logger.debug("Intelligence growth: mission status unavailable: %s", exc)
            metrics["mission_count"] = 0
            metrics["active_missions"] = []

        return {"ok": True, "metrics": metrics}

    def _handle_get_intelligence_growth(self) -> None:
        if not self._validate_auth(b""):
            return
        self._write_json(HTTPStatus.OK, self._gather_intelligence_growth())

    def _handle_get_learning_summary(self) -> None:
        from jarvis_engine.mobile_routes._helpers import _configure_db

        if not self._validate_auth(b""):
            return
        db_path = _memory_db_path(self._root)
        summary: dict[str, Any] = {
            "preferences": {},
            "route_quality": {},
            "peak_hours": [],
            "hourly_distribution": {},
            "current_context": {},
        }
        if not db_path.exists():
            self._write_json(HTTPStatus.OK, summary)
            return
        lrn_db = None
        try:
            import sqlite3 as _lrn_sqlite3

            lrn_db = _lrn_sqlite3.connect(str(db_path), check_same_thread=False)
            _configure_db(lrn_db)
            try:
                from jarvis_engine.learning.preferences import PreferenceTracker

                pt = PreferenceTracker(lrn_db)
                summary["preferences"] = pt.get_preferences()
            except (ImportError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
                logger.debug("Learning summary: preferences unavailable: %s", exc)
            try:
                from jarvis_engine.learning.feedback import ResponseFeedbackTracker

                ft = ResponseFeedbackTracker(lrn_db)
                summary["route_quality"] = ft.get_all_route_quality()
            except (ImportError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
                logger.debug("Learning summary: route quality unavailable: %s", exc)
            try:
                from jarvis_engine.learning.usage_patterns import UsagePatternTracker

                ut = UsagePatternTracker(lrn_db)
                summary["peak_hours"] = ut.get_peak_hours()
                summary["hourly_distribution"] = ut.get_hourly_distribution()
                now = datetime.now(UTC)
                summary["current_context"] = ut.predict_context(now.hour, now.weekday())
            except (ImportError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
                logger.debug("Learning summary: usage patterns unavailable: %s", exc)
        except (ImportError, sqlite3.Error, OSError, ValueError, TypeError) as exc:
            logger.debug("Learning summary: DB unavailable: %s", exc)
        finally:
            if lrn_db is not None:
                lrn_db.close()
        self._write_json(HTTPStatus.OK, summary)

    def _handle_post_intelligence_merge(self) -> None:
        """Accept intelligence from the phone and merge into desktop knowledge."""
        payload, _ = self._read_json_body(max_content_length=500_000)
        if payload is None:
            return
        try:
            items = payload.get("items", [])
            if not items:
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "No items to merge."})
                return

            merged = 0
            from jarvis_engine.memory.store import MemoryStore

            store = MemoryStore(self._root)

            for item in items[:200]:
                content = item.get("content", "")
                category = item.get("category", "general")
                source = item.get("source", "phone")

                if not content or len(content) > 5000:
                    continue

                try:
                    store.add(
                        content=f"[phone-intelligence:{category}] {content}",
                        source=source,
                        kind="intelligence",
                        tags=f"phone,{category},auto-merged",
                        branch="phone-intelligence",
                    )
                    merged += 1
                except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
                    logger.debug("Phone intelligence merge failed: %s", exc)

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "merged": merged,
                "total_received": len(items),
            })
            logger.info("Intelligence merge: %d items from phone", merged)
        except Exception as exc:  # boundary: catch-all justified
            logger.error("intelligence/merge failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False, "error": "Intelligence merge failed.",
            })

    def _handle_post_intelligence_export(self) -> None:
        """Export desktop knowledge for the phone's local intelligence."""
        from jarvis_engine.mobile_routes._helpers import _configure_db

        payload, _ = self._read_json_body(max_content_length=10_000)
        if payload is None:
            return
        try:
            limit = min(int(payload.get("limit", 200)), 500)
            items = []

            # Export from knowledge graph and user preferences
            db_path = _memory_db_path(self._root)
            if db_path.exists():
                import sqlite3

                db = sqlite3.connect(str(db_path))
                try:
                    _configure_db(db)

                    # KG nodes
                    try:
                        rows = db.execute(
                            "SELECT label, node_type, confidence FROM kg_nodes "
                            "WHERE confidence >= 0.5 ORDER BY confidence DESC LIMIT ?",
                            (limit // 2,),
                        ).fetchall()
                        for row in rows:
                            items.append({
                                "content": f"{row['label']} ({row['node_type']})",
                                "category": "knowledge",
                                "confidence": row["confidence"],
                            })
                    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
                        logger.warning("KG nodes export failure: %s", exc)

                    # Memory records
                    try:
                        rows = db.execute(
                            "SELECT summary, kind, tags, confidence FROM records "
                            "WHERE confidence >= 0.5 AND summary != '' "
                            "ORDER BY ts DESC LIMIT ?",
                            (limit // 2,),
                        ).fetchall()
                        for row in rows:
                            items.append({
                                "content": row["summary"],
                                "category": row["kind"] or "memory",
                                "confidence": row["confidence"],
                            })
                    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
                        logger.warning("Memory records export failure: %s", exc)

                    # User preferences
                    try:
                        rows = db.execute(
                            "SELECT category, preference, score FROM user_preferences "
                            "WHERE score > 0 ORDER BY score DESC LIMIT 50",
                        ).fetchall()
                        for row in rows:
                            items.append({
                                "content": f"Preference: {row['category']} — {row['preference']} "
                                           f"(score: {row['score']:.1f})",
                                "category": "preference",
                                "confidence": min(row["score"] / 10.0, 1.0),
                            })
                    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
                        logger.warning("Preferences export failure: %s", exc)
                finally:
                    db.close()

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "items": items[:limit],
                "total": len(items),
            })
            logger.info("Intelligence export: %d items for phone", len(items))
        except Exception as exc:  # boundary: catch-all justified
            logger.error("intelligence/export failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False, "error": "Intelligence export failed.",
            })
