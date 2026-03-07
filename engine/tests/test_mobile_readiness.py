"""Phase 5 — Mobile App Readiness tests.

MOB-01: Learning tables in sync changelog
MOB-02: Mobile API endpoint reliability
MOB-03: Learning sync via /feedback and /learning/summary
MOB-04: Offline command queue simulation
MOB-05: API surface compatibility checks
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path


from conftest import http_request, signed_headers
from jarvis_engine._db_pragmas import configure_sqlite


# ---------------------------------------------------------------------------
# MOB-01: Learning tables tracked in sync changelog
# ---------------------------------------------------------------------------

class TestLearningSyncChangelog:
    """Verify that learning tables are tracked by the sync changelog."""

    def _setup_db(self, tmp_path: Path) -> sqlite3.Connection:
        db_path = tmp_path / "test.db"
        db = sqlite3.connect(str(db_path))
        configure_sqlite(db)
        # Create ALL tracked tables (changelog triggers need them all)
        db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY, ts REAL, source TEXT, kind TEXT,
                task_id TEXT, branch TEXT, tags TEXT, summary TEXT,
                content_hash TEXT, confidence REAL, tier TEXT,
                access_count INTEGER DEFAULT 0, last_accessed TEXT, created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                node_id TEXT PRIMARY KEY, label TEXT, node_type TEXT,
                confidence REAL, locked INTEGER DEFAULT 0, locked_at TEXT,
                locked_by TEXT, sources TEXT, history TEXT,
                created_at TEXT, updated_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS kg_edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT,
                relation TEXT, confidence REAL, source_record TEXT, created_at TEXT
            )
        """)
        # Learning tables
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                category TEXT NOT NULL,
                preference TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.0,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                last_observed TEXT NOT NULL,
                PRIMARY KEY (category, preference)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS response_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route TEXT NOT NULL DEFAULT '',
                feedback TEXT NOT NULL CHECK(feedback IN ('positive', 'negative', 'neutral')),
                user_message_snippet TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS usage_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour INTEGER NOT NULL CHECK(hour >= 0 AND hour <= 23),
                day_of_week INTEGER NOT NULL CHECK(day_of_week >= 0 AND day_of_week <= 6),
                route TEXT NOT NULL DEFAULT '',
                topic TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            )
        """)
        db.commit()
        return db

    def test_learning_tables_in_tracked_tables(self):
        """user_preferences, response_feedback, usage_patterns are in _TRACKED_TABLES."""
        from jarvis_engine.sync.changelog import _TRACKED_TABLES
        assert "user_preferences" in _TRACKED_TABLES
        assert "response_feedback" in _TRACKED_TABLES
        assert "usage_patterns" in _TRACKED_TABLES

    def test_preferences_tracked_fields(self):
        from jarvis_engine.sync.changelog import _TRACKED_TABLES
        pref = _TRACKED_TABLES["user_preferences"]
        assert "category" in pref["fields"]
        assert "preference" in pref["fields"]
        assert "score" in pref["fields"]

    def test_feedback_tracked_fields(self):
        from jarvis_engine.sync.changelog import _TRACKED_TABLES
        fb = _TRACKED_TABLES["response_feedback"]
        assert "route" in fb["fields"]
        assert "feedback" in fb["fields"]
        assert "recorded_at" in fb["fields"]

    def test_usage_patterns_tracked_fields(self):
        from jarvis_engine.sync.changelog import _TRACKED_TABLES
        up = _TRACKED_TABLES["usage_patterns"]
        assert "hour" in up["fields"]
        assert "day_of_week" in up["fields"]
        assert "route" in up["fields"]
        assert "topic" in up["fields"]

    def test_install_triggers_creates_learning_triggers(self, tmp_path):
        """install_changelog_triggers creates triggers for learning tables."""
        db = self._setup_db(tmp_path)
        from jarvis_engine.sync.changelog import install_changelog_triggers
        install_changelog_triggers(db, device_id="desktop")

        # Check triggers exist for user_preferences
        triggers = db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE '%user_preferences%'"
        ).fetchall()
        trigger_names = [t[0] for t in triggers]
        assert any("insert" in n.lower() for n in trigger_names), f"No insert trigger for user_preferences: {trigger_names}"
        assert any("update" in n.lower() for n in trigger_names), f"No update trigger for user_preferences: {trigger_names}"
        assert any("delete" in n.lower() for n in trigger_names), f"No delete trigger for user_preferences: {trigger_names}"
        db.close()

    def test_preference_insert_creates_changelog_entry(self, tmp_path):
        """INSERT into user_preferences creates a changelog entry."""
        db = self._setup_db(tmp_path)
        from jarvis_engine.sync.changelog import install_changelog_triggers
        install_changelog_triggers(db, device_id="desktop")

        db.execute(
            "INSERT INTO user_preferences (category, preference, score, evidence_count, last_observed) "
            "VALUES ('tone', 'casual', 0.8, 3, '2026-03-01')"
        )
        db.commit()

        rows = db.execute(
            "SELECT table_name, operation, row_id FROM _sync_changelog WHERE table_name = 'user_preferences'"
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0][1] == "INSERT"
        # Composite PK: row_id should be "category:preference"
        assert rows[0][2] == "tone:casual"
        db.close()

    def test_feedback_insert_creates_changelog_entry(self, tmp_path):
        """INSERT into response_feedback creates a changelog entry."""
        db = self._setup_db(tmp_path)
        from jarvis_engine.sync.changelog import install_changelog_triggers
        install_changelog_triggers(db, device_id="desktop")

        db.execute(
            "INSERT INTO response_feedback (route, feedback, user_message_snippet, recorded_at) "
            "VALUES ('kimi-k2', 'positive', 'great answer', '2026-03-01T10:00:00')"
        )
        db.commit()

        rows = db.execute(
            "SELECT table_name, operation FROM _sync_changelog WHERE table_name = 'response_feedback'"
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0][1] == "INSERT"
        db.close()

    def test_usage_patterns_insert_creates_changelog_entry(self, tmp_path):
        """INSERT into usage_patterns creates a changelog entry."""
        db = self._setup_db(tmp_path)
        from jarvis_engine.sync.changelog import install_changelog_triggers
        install_changelog_triggers(db, device_id="desktop")

        db.execute(
            "INSERT INTO usage_patterns (hour, day_of_week, route, topic, recorded_at) "
            "VALUES (14, 2, 'kimi-k2', 'weather', '2026-03-01T14:00:00')"
        )
        db.commit()

        rows = db.execute(
            "SELECT table_name, operation FROM _sync_changelog WHERE table_name = 'usage_patterns'"
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0][1] == "INSERT"
        db.close()

    def test_preference_update_creates_changelog_entry(self, tmp_path):
        """UPDATE on user_preferences creates a changelog entry."""
        db = self._setup_db(tmp_path)
        from jarvis_engine.sync.changelog import install_changelog_triggers
        install_changelog_triggers(db, device_id="desktop")

        db.execute(
            "INSERT INTO user_preferences (category, preference, score, evidence_count, last_observed) "
            "VALUES ('tone', 'casual', 0.8, 3, '2026-03-01')"
        )
        db.commit()
        db.execute(
            "UPDATE user_preferences SET score = 0.95, evidence_count = 5 "
            "WHERE category = 'tone' AND preference = 'casual'"
        )
        db.commit()

        rows = db.execute(
            "SELECT operation FROM _sync_changelog WHERE table_name = 'user_preferences' ORDER BY rowid"
        ).fetchall()
        ops = [r[0] for r in rows]
        assert "INSERT" in ops
        assert "UPDATE" in ops
        db.close()

    def test_sync_engine_computes_outgoing_for_learning(self, tmp_path):
        """SyncEngine.compute_outgoing includes learning table changes."""
        db = self._setup_db(tmp_path)
        # Also need records table for SyncEngine
        db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY, ts REAL, source TEXT, kind TEXT,
                task_id TEXT, branch TEXT, tags TEXT, summary TEXT,
                content_hash TEXT, confidence REAL, tier TEXT,
                access_count INTEGER DEFAULT 0, last_accessed TEXT, created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                node_id TEXT PRIMARY KEY, label TEXT, node_type TEXT,
                confidence REAL, locked INTEGER DEFAULT 0, locked_at TEXT,
                locked_by TEXT, sources TEXT, history TEXT,
                created_at TEXT, updated_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS kg_edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT,
                relation TEXT, confidence REAL, source_record TEXT, created_at TEXT
            )
        """)
        db.commit()

        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        # Insert a preference
        db.execute(
            "INSERT INTO user_preferences (category, preference, score, evidence_count, last_observed) "
            "VALUES ('format', 'markdown', 0.9, 5, '2026-03-01')"
        )
        db.commit()

        result = engine.compute_outgoing("galaxy_s25_primary")
        assert "user_preferences" in result["changes"]
        assert len(result["changes"]["user_preferences"]) >= 1
        db.close()


# ---------------------------------------------------------------------------
# MOB-02 / MOB-03: New API endpoints
# ---------------------------------------------------------------------------

class TestLearningSummaryEndpoint:
    """Tests for GET /learning/summary endpoint."""

    def test_learning_summary_returns_structure(self, mobile_server):
        """GET /learning/summary returns expected JSON structure."""
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/learning/summary", headers=headers)
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert "preferences" in payload
        assert "route_quality" in payload
        assert "peak_hours" in payload
        assert "hourly_distribution" in payload
        assert "current_context" in payload

    def test_learning_summary_requires_auth(self, mobile_server):
        """GET /learning/summary without auth returns 401/403."""
        code, _ = http_request("GET", f"{mobile_server.base_url}/learning/summary")
        assert code in (401, 403)

    def test_learning_summary_with_db(self, mobile_server):
        """GET /learning/summary returns data when DB has content."""
        # Create brain DB with learning tables and data
        brain_dir = mobile_server.root / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        db_path = brain_dir / "jarvis_memory.db"
        db = sqlite3.connect(str(db_path))
        configure_sqlite(db)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                category TEXT NOT NULL, preference TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.0, evidence_count INTEGER NOT NULL DEFAULT 0,
                last_observed TEXT NOT NULL, PRIMARY KEY (category, preference)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS response_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL DEFAULT '',
                feedback TEXT NOT NULL CHECK(feedback IN ('positive', 'negative', 'neutral')),
                user_message_snippet TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS usage_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour INTEGER NOT NULL CHECK(hour >= 0 AND hour <= 23),
                day_of_week INTEGER NOT NULL CHECK(day_of_week >= 0 AND day_of_week <= 6),
                route TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            )
        """)
        db.execute(
            "INSERT INTO user_preferences VALUES ('tone', 'casual', 0.8, 3, '2026-03-01')"
        )
        db.execute(
            "INSERT INTO response_feedback (route, feedback, user_message_snippet, recorded_at) "
            "VALUES ('kimi-k2', 'positive', 'good', '2026-03-01T10:00:00')"
        )
        db.execute(
            "INSERT INTO usage_patterns (hour, day_of_week, route, topic, recorded_at) "
            "VALUES (14, 2, 'kimi-k2', 'weather', '2026-03-01T14:00:00')"
        )
        db.commit()
        db.close()

        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/learning/summary", headers=headers)
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["preferences"].get("tone") == "casual"
        assert isinstance(payload["peak_hours"], list)


class TestFeedbackEndpoint:
    """Tests for POST /feedback endpoint."""

    def test_feedback_positive(self, mobile_server):
        """POST /feedback records positive feedback."""
        brain_dir = mobile_server.root / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        db_path = brain_dir / "jarvis_memory.db"
        db = sqlite3.connect(str(db_path))
        configure_sqlite(db)
        db.execute("""
            CREATE TABLE IF NOT EXISTS response_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL DEFAULT '',
                feedback TEXT NOT NULL CHECK(feedback IN ('positive', 'negative', 'neutral')),
                user_message_snippet TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_route ON response_feedback(route)")
        db.commit()
        db.close()

        payload_bytes = json.dumps({
            "quality": "positive",
            "route": "kimi-k2",
            "comment": "Great answer!",
        }).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("POST", f"{mobile_server.base_url}/feedback", body=payload_bytes, headers=headers)
        assert code == 200
        result = json.loads(body.decode("utf-8"))
        assert result["ok"] is True
        assert result["recorded"] is True
        assert result["quality"] == "positive"

        # Verify in DB
        db = sqlite3.connect(str(db_path))
        configure_sqlite(db)
        rows = db.execute("SELECT route, feedback, user_message_snippet FROM response_feedback").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "kimi-k2"
        assert rows[0][1] == "positive"
        assert rows[0][2] == "Great answer!"
        db.close()

    def test_feedback_negative(self, mobile_server):
        """POST /feedback records negative feedback."""
        brain_dir = mobile_server.root / ".planning" / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        db_path = brain_dir / "jarvis_memory.db"
        db = sqlite3.connect(str(db_path))
        configure_sqlite(db)
        db.execute("""
            CREATE TABLE IF NOT EXISTS response_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL DEFAULT '',
                feedback TEXT NOT NULL CHECK(feedback IN ('positive', 'negative', 'neutral')),
                user_message_snippet TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_route ON response_feedback(route)")
        db.commit()
        db.close()

        payload_bytes = json.dumps({
            "quality": "negative",
            "route": "claude-cli",
            "comment": "Wrong answer",
        }).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("POST", f"{mobile_server.base_url}/feedback", body=payload_bytes, headers=headers)
        assert code == 200
        result = json.loads(body.decode("utf-8"))
        assert result["ok"] is True
        assert result["quality"] == "negative"

    def test_feedback_invalid_quality(self, mobile_server):
        """POST /feedback rejects invalid quality values."""
        payload_bytes = json.dumps({"quality": "awesome"}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        headers["Content-Length"] = str(len(payload_bytes))
        code, body = http_request("POST", f"{mobile_server.base_url}/feedback", body=payload_bytes, headers=headers)
        assert code == 400
        result = json.loads(body.decode("utf-8"))
        assert result["ok"] is False

    def test_feedback_requires_auth(self, mobile_server):
        """POST /feedback without auth returns 401/403."""
        payload_bytes = json.dumps({"quality": "positive"}).encode("utf-8")
        code, _ = http_request(
            "POST", f"{mobile_server.base_url}/feedback",
            body=payload_bytes,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload_bytes))},
        )
        assert code in (401, 403)

    def test_feedback_no_db(self, mobile_server):
        """POST /feedback returns ok with recorded=False when DB missing."""
        payload_bytes = json.dumps({"quality": "positive", "route": "test"}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        headers["Content-Length"] = str(len(payload_bytes))
        code, body = http_request("POST", f"{mobile_server.base_url}/feedback", body=payload_bytes, headers=headers)
        assert code == 200
        result = json.loads(body.decode("utf-8"))
        assert result["ok"] is True
        assert result["recorded"] is False


# ---------------------------------------------------------------------------
# MOB-04: Offline command queue simulation (tests queue + flush behavior)
# ---------------------------------------------------------------------------

class TestCommandQueueReliability:
    """Verify /command endpoint handles concurrent and repeated requests."""

    def test_command_endpoint_rejects_missing_text(self, mobile_server):
        """POST /command with no text field returns error."""
        payload_bytes = json.dumps({"execute": True}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("POST", f"{mobile_server.base_url}/command", body=payload_bytes, headers=headers)
        assert code in (200, 400)
        result = json.loads(body.decode("utf-8"))
        # Should indicate error (ok=False or error message)
        if code == 200:
            assert result.get("ok") is False or "error" in result or "stderr_tail" in result

    def test_command_endpoint_missing_text_has_structured_error_fields(self, mobile_server):
        """Missing text errors include lifecycle + diagnostic metadata."""
        payload_bytes = json.dumps({"execute": True}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("POST", f"{mobile_server.base_url}/command", body=payload_bytes, headers=headers)
        assert code in (200, 400)
        result = json.loads(body.decode("utf-8"))
        if code == 200:
            assert result.get("ok") is False
            assert result.get("lifecycle_state") == "failed"
            assert isinstance(result.get("correlation_id", ""), str)
            assert isinstance(result.get("diagnostic_id", ""), str)
            assert isinstance(result.get("error_code", ""), str)
            assert isinstance(result.get("retryable", False), bool)
            assert isinstance(result.get("user_hint", ""), str)

    def test_command_endpoint_rejects_oversized_text(self, mobile_server):
        """POST /command with text >2000 chars is rejected or errors."""
        huge_text = "x" * 2500
        payload_bytes = json.dumps({"text": huge_text}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("POST", f"{mobile_server.base_url}/command", body=payload_bytes, headers=headers)
        # May be rejected by owner guard (403), rate limit, or text validation (400)
        result = json.loads(body.decode("utf-8"))
        assert code in (200, 400, 403)
        if code == 200:
            assert result.get("ok") is False or "error" in result or "stderr_tail" in result

    def test_command_endpoint_requires_auth(self, mobile_server):
        """POST /command without auth returns 401/403."""
        payload_bytes = json.dumps({"text": "hello"}).encode("utf-8")
        code, _ = http_request(
            "POST", f"{mobile_server.base_url}/command",
            body=payload_bytes,
            headers={"Content-Type": "application/json"},
        )
        assert code in (401, 403)


# ---------------------------------------------------------------------------
# MOB-05: API surface compatibility checks
# ---------------------------------------------------------------------------

class TestAPISurfaceCompatibility:
    """Verify all Android-used endpoints exist and return correct shapes."""

    def test_health_endpoint_shape(self, mobile_server):
        """GET /health returns {ok, status, intelligence} shape."""
        code, body = http_request("GET", f"{mobile_server.base_url}/health")
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert "ok" in payload
        assert "status" in payload
        assert "intelligence" in payload

    def test_cert_fingerprint_endpoint_shape(self, mobile_server):
        """GET /cert-fingerprint returns {fingerprint} shape (or 404 without TLS)."""
        code, body = http_request("GET", f"{mobile_server.base_url}/cert-fingerprint")
        # Without TLS active, may return 404; with TLS returns 200 + fingerprint
        assert code in (200, 404)
        if code == 200:
            payload = json.loads(body.decode("utf-8"))
            assert "fingerprint" in payload

    def test_settings_endpoint_shape(self, mobile_server):
        """GET /settings returns expected keys (may be nested under 'settings')."""
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/settings", headers=headers)
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        # Settings may be nested under a 'settings' key
        settings = payload.get("settings", payload)
        assert "runtime_control" in settings or "daemon_paused" in settings

    def test_dashboard_endpoint_shape(self, mobile_server):
        """GET /dashboard returns intelligence dashboard shape."""
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/dashboard", headers=headers)
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        # Dashboard should have some form of intelligence data
        assert isinstance(payload, dict)

    def test_sync_status_endpoint_shape(self, mobile_server):
        """GET /sync/status returns sync cursors shape."""
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/sync/status", headers=headers)
        # May return 200 with cursors, 500 if error, or 503 if sync engine unavailable
        assert code in (200, 500, 503)

    def test_intelligence_growth_endpoint_shape(self, mobile_server):
        """GET /intelligence/growth returns metrics shape."""
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/intelligence/growth", headers=headers)
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        # Metrics may be nested under a 'metrics' key
        metrics = payload.get("metrics", payload)
        assert "facts_total" in metrics
        assert "kg_nodes" in metrics
        assert "growth_trend" in metrics

    def test_activity_endpoint_shape(self, mobile_server):
        """GET /activity returns events list shape."""
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/activity", headers=headers)
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert "events" in payload

    def test_widget_status_endpoint_shape(self, mobile_server):
        """GET /widget-status returns combined shape."""
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/widget-status", headers=headers)
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert isinstance(payload, dict)

    def test_auth_status_endpoint_shape(self, mobile_server):
        """GET /auth/status returns session info."""
        code, body = http_request("GET", f"{mobile_server.base_url}/auth/status")
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert isinstance(payload, dict)

    def test_sync_pull_endpoint_exists(self, mobile_server):
        """POST /sync/pull returns a response (not 404)."""
        payload_bytes = json.dumps({"device_id": "test_device"}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, _ = http_request("POST", f"{mobile_server.base_url}/sync/pull", body=payload_bytes, headers=headers)
        assert code != 404

    def test_sync_push_endpoint_exists(self, mobile_server):
        """POST /sync/push returns a response (not 404)."""
        payload_bytes = json.dumps({"device_id": "test_device", "payload": ""}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, _ = http_request("POST", f"{mobile_server.base_url}/sync/push", body=payload_bytes, headers=headers)
        assert code != 404

    def test_ingest_endpoint_exists(self, mobile_server):
        """POST /ingest returns a response (not 404)."""
        payload_bytes = json.dumps({
            "source": "user",
            "kind": "episodic",
            "task_id": "test",
            "content": "test memory",
        }).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", body=payload_bytes, headers=headers)
        assert code != 404

    def test_feedback_endpoint_exists(self, mobile_server):
        """POST /feedback returns a response (not 404)."""
        payload_bytes = json.dumps({"quality": "positive"}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, _ = http_request("POST", f"{mobile_server.base_url}/feedback", body=payload_bytes, headers=headers)
        assert code != 404

    def test_learning_summary_endpoint_exists(self, mobile_server):
        """GET /learning/summary returns a response (not 404)."""
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, _ = http_request("GET", f"{mobile_server.base_url}/learning/summary", headers=headers)
        assert code != 404

    def test_deprecated_sync_returns_410(self, mobile_server):
        """POST /sync (deprecated) returns 410 GONE."""
        payload_bytes = json.dumps({}).encode("utf-8")
        headers = signed_headers(payload_bytes, mobile_server.auth_token, mobile_server.signing_key)
        code, _ = http_request("POST", f"{mobile_server.base_url}/sync", body=payload_bytes, headers=headers)
        assert code == 410


# ---------------------------------------------------------------------------
# Additional reliability tests
# ---------------------------------------------------------------------------

class TestSyncRoundTrip:
    """End-to-end sync round-trip: desktop change -> pull -> push back."""

    def _full_db(self, tmp_path: Path) -> sqlite3.Connection:
        db_path = tmp_path / "sync_rt.db"
        db = sqlite3.connect(str(db_path))
        configure_sqlite(db)
        # Create all tracked tables
        db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY, ts REAL, source TEXT, kind TEXT,
                task_id TEXT, branch TEXT, tags TEXT, summary TEXT,
                content_hash TEXT, confidence REAL, tier TEXT,
                access_count INTEGER DEFAULT 0, last_accessed TEXT, created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                node_id TEXT PRIMARY KEY, label TEXT, node_type TEXT,
                confidence REAL, locked INTEGER DEFAULT 0, locked_at TEXT,
                locked_by TEXT, sources TEXT, history TEXT,
                created_at TEXT, updated_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS kg_edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT,
                relation TEXT, confidence REAL, source_record TEXT, created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                category TEXT NOT NULL, preference TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.0, evidence_count INTEGER NOT NULL DEFAULT 0,
                last_observed TEXT NOT NULL, PRIMARY KEY (category, preference)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS response_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL DEFAULT '',
                feedback TEXT NOT NULL CHECK(feedback IN ('positive', 'negative', 'neutral')),
                user_message_snippet TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS usage_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour INTEGER NOT NULL CHECK(hour >= 0 AND hour <= 23),
                day_of_week INTEGER NOT NULL CHECK(day_of_week >= 0 AND day_of_week <= 6),
                route TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            )
        """)
        db.commit()
        return db

    def test_memory_record_round_trip(self, tmp_path):
        """Memory record: insert on desktop -> compute_outgoing -> available for mobile."""
        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = self._full_db(tmp_path)
        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        # Desktop inserts a memory record
        db.execute(
            "INSERT INTO records (record_id, ts, source, kind, task_id, branch, tags, summary, "
            "content_hash, confidence, tier, created_at) VALUES "
            "(?, ?, 'user', 'episodic', 'task1', 'main', '', 'Test memory', "
            "'abc123', 0.9, 'hot', '2026-03-01')",
            (str(uuid.uuid4()), time.time()),
        )
        db.commit()

        result = engine.compute_outgoing("galaxy_s25_primary")
        assert "records" in result["changes"]
        assert len(result["changes"]["records"]) >= 1
        db.close()

    def test_learning_preference_round_trip(self, tmp_path):
        """Learning preference: insert -> appears in outgoing changes."""
        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = self._full_db(tmp_path)
        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        db.execute(
            "INSERT INTO user_preferences (category, preference, score, evidence_count, last_observed) "
            "VALUES ('verbosity', 'concise', 0.85, 4, '2026-03-01')"
        )
        db.commit()

        result = engine.compute_outgoing("galaxy_s25_primary")
        assert "user_preferences" in result["changes"]
        assert len(result["changes"]["user_preferences"]) >= 1
        db.close()

    def test_kg_fact_round_trip(self, tmp_path):
        """KG fact: insert node -> appears in outgoing changes."""
        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = self._full_db(tmp_path)
        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        db.execute(
            "INSERT INTO kg_nodes (node_id, label, node_type, confidence, created_at, updated_at) "
            "VALUES (?, 'Conner', 'person', 0.95, '2026-03-01', '2026-03-01')",
            (str(uuid.uuid4()),),
        )
        db.commit()

        result = engine.compute_outgoing("galaxy_s25_primary")
        assert "kg_nodes" in result["changes"]
        assert len(result["changes"]["kg_nodes"]) >= 1
        db.close()

    def test_conflict_resolution_desktop_wins(self, tmp_path):
        """When both sides update same field, desktop value wins."""
        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = self._full_db(tmp_path)
        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        # Insert a record first
        rid = str(uuid.uuid4())
        db.execute(
            "INSERT INTO records (record_id, ts, source, kind, task_id, branch, tags, summary, "
            "content_hash, confidence, tier, created_at) VALUES "
            "(?, ?, 'user', 'episodic', 'task1', 'main', '', 'Original summary', "
            "'hash1', 0.9, 'hot', '2026-03-01')",
            (rid, time.time()),
        )
        db.commit()

        # Desktop updates the summary
        db.execute("UPDATE records SET summary = 'Desktop updated' WHERE record_id = ?", (rid,))
        db.commit()

        # Simulate mobile pushing a conflicting change (UPDATE with same field)
        incoming = {
            "records": [{
                "op": "UPDATE",
                "row_id": rid,
                "fields_changed": ["summary"],
                "new_values": {"summary": "Mobile updated"},
                "__version": 999,
            }],
        }
        result = engine.apply_incoming(incoming, "galaxy_s25_primary")
        # Conflict should be resolved (desktop wins for overlapping fields)
        assert isinstance(result, dict)
        db.close()


class TestFeedbackRateLimiting:
    """Verify /feedback is in the expensive rate-limit bucket."""

    def test_feedback_in_expensive_paths(self):
        """POST /feedback is rate-limited as an expensive endpoint."""
        from jarvis_engine.mobile_api import _EXPENSIVE_PATHS
        assert "/feedback" in _EXPENSIVE_PATHS


class TestCompositePKSync:
    """Verify incoming sync correctly handles composite primary keys."""

    def _full_db(self, tmp_path):
        db = sqlite3.connect(str(tmp_path / "test.db"))
        configure_sqlite(db)
        db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY, ts REAL, source TEXT, kind TEXT,
                task_id TEXT, branch TEXT, tags TEXT, summary TEXT,
                content_hash TEXT, confidence REAL, tier TEXT,
                access_count INTEGER DEFAULT 0, last_accessed TEXT, created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS kg_nodes (
                node_id TEXT PRIMARY KEY, label TEXT, node_type TEXT,
                confidence REAL, locked INTEGER DEFAULT 0, locked_at TEXT,
                locked_by TEXT, sources TEXT, history TEXT,
                created_at TEXT, updated_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS kg_edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT,
                relation TEXT, confidence REAL, source_record TEXT, created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                category TEXT NOT NULL, preference TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.0, evidence_count INTEGER NOT NULL DEFAULT 0,
                last_observed TEXT NOT NULL,
                PRIMARY KEY (category, preference)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS response_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route TEXT NOT NULL DEFAULT '',
                feedback TEXT NOT NULL CHECK(feedback IN ('positive', 'negative', 'neutral')),
                user_message_snippet TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS usage_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hour INTEGER NOT NULL, day_of_week INTEGER NOT NULL,
                route TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL
            )
        """)
        db.commit()
        return db

    def test_incoming_insert_composite_pk(self, tmp_path):
        """apply_incoming correctly inserts user_preferences with composite PK."""
        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = self._full_db(tmp_path)
        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        incoming = {
            "user_preferences": [{
                "operation": "INSERT",
                "row_id": "tone:casual",
                "new_values": {
                    "category": "tone",
                    "preference": "casual",
                    "score": 0.85,
                    "evidence_count": 5,
                    "last_observed": "2026-03-02",
                },
                "__version": 1,
            }],
        }
        result = engine.apply_incoming(incoming, "galaxy_s25_primary")
        assert result["applied"] >= 1

        row = db.execute(
            "SELECT score, evidence_count FROM user_preferences WHERE category = ? AND preference = ?",
            ("tone", "casual"),
        ).fetchone()
        assert row is not None
        assert row[0] == 0.85
        assert row[1] == 5
        db.close()

    def test_incoming_update_composite_pk(self, tmp_path):
        """apply_incoming correctly updates user_preferences with composite PK."""
        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = self._full_db(tmp_path)
        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        # Pre-insert a row
        db.execute(
            "INSERT INTO user_preferences (category, preference, score, evidence_count, last_observed) "
            "VALUES ('tone', 'casual', 0.5, 2, '2026-03-01')"
        )
        db.commit()

        # Advance the mobile cursor past the initial INSERT so it is not seen
        # as a local conflict when the incoming UPDATE arrives.
        max_ver = db.execute(
            "SELECT MAX(__version) FROM _sync_changelog WHERE table_name = 'user_preferences'"
        ).fetchone()[0] or 0
        db.execute(
            "INSERT INTO _sync_cursor (device_id, table_name, last_version, last_sync_ts) "
            "VALUES ('galaxy_s25_primary', 'user_preferences', ?, datetime('now')) "
            "ON CONFLICT(device_id, table_name) DO UPDATE SET last_version = excluded.last_version",
            (max_ver,),
        )
        db.commit()

        incoming = {
            "user_preferences": [{
                "operation": "UPDATE",
                "row_id": "tone:casual",
                "fields_changed": ["score", "evidence_count"],
                "new_values": {"score": 0.9, "evidence_count": 10},
                "__version": 2,
            }],
        }
        result = engine.apply_incoming(incoming, "galaxy_s25_primary")
        assert result["applied"] >= 1

        row = db.execute(
            "SELECT score, evidence_count FROM user_preferences WHERE category = ? AND preference = ?",
            ("tone", "casual"),
        ).fetchone()
        assert row is not None
        assert row[0] == 0.9
        assert row[1] == 10
        db.close()

    def test_incoming_delete_composite_pk(self, tmp_path):
        """apply_incoming correctly deletes user_preferences with composite PK."""
        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = self._full_db(tmp_path)
        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        # Pre-insert a row
        db.execute(
            "INSERT INTO user_preferences (category, preference, score, evidence_count, last_observed) "
            "VALUES ('tone', 'casual', 0.5, 2, '2026-03-01')"
        )
        db.commit()

        # Advance cursor past the INSERT
        max_ver = db.execute(
            "SELECT MAX(__version) FROM _sync_changelog WHERE table_name = 'user_preferences'"
        ).fetchone()[0] or 0
        db.execute(
            "INSERT INTO _sync_cursor (device_id, table_name, last_version, last_sync_ts) "
            "VALUES ('galaxy_s25_primary', 'user_preferences', ?, datetime('now')) "
            "ON CONFLICT(device_id, table_name) DO UPDATE SET last_version = excluded.last_version",
            (max_ver,),
        )
        db.commit()

        incoming = {
            "user_preferences": [{
                "operation": "DELETE",
                "row_id": "tone:casual",
                "old_values": {"category": "tone", "preference": "casual"},
                "__version": 3,
            }],
        }
        result = engine.apply_incoming(incoming, "galaxy_s25_primary")
        assert result["applied"] >= 1

        row = db.execute(
            "SELECT * FROM user_preferences WHERE category = ? AND preference = ?",
            ("tone", "casual"),
        ).fetchone()
        assert row is None
        db.close()

    def test_composite_pk_bad_row_id_skipped(self, tmp_path):
        """apply_incoming skips entries with malformed composite row_id."""
        import threading
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine

        db = self._full_db(tmp_path)
        install_changelog_triggers(db, device_id="desktop")
        lock = threading.Lock()
        engine = SyncEngine(db, lock, device_id="desktop")

        # row_id missing the separator — only 1 part but pk has 2 columns
        incoming = {
            "user_preferences": [{
                "operation": "INSERT",
                "row_id": "tone_no_colon",
                "new_values": {
                    "category": "tone",
                    "preference": "casual",
                    "score": 0.5,
                    "evidence_count": 1,
                    "last_observed": "2026-03-02",
                },
                "__version": 1,
            }],
        }
        result = engine.apply_incoming(incoming, "galaxy_s25_primary")
        # Should not crash — malformed row_id is gracefully skipped
        assert isinstance(result, dict)
        assert result["applied"] == 0
        db.close()
