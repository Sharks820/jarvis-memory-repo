from __future__ import annotations

import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from pathlib import Path

from conftest import http_request, signed_headers
from jarvis_engine import mobile_api
from jarvis_engine.ingest import IngestionPipeline
from jarvis_engine.memory_store import MemoryStore
from jarvis_engine.mobile_api import MobileIngestHandler, MobileIngestServer
from jarvis_engine.owner_guard import set_master_password, trust_mobile_device, write_owner_guard


def test_health_endpoint(mobile_server) -> None:
    code, body = http_request("GET", f"{mobile_server.base_url}/health")
    assert code == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    # Intelligence status should always be present (defaults when no history file)
    assert "intelligence" in payload
    intel = payload["intelligence"]
    assert isinstance(intel["score"], (int, float))
    assert isinstance(intel["regression"], bool)
    assert isinstance(intel["last_test"], str)


def test_health_endpoint_with_self_test_history(mobile_server) -> None:
    """Health endpoint reads intelligence score from self_test_history.jsonl."""
    runtime_dir = mobile_server.root / ".planning" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    history_path = runtime_dir / "self_test_history.jsonl"
    record = json.dumps({
        "average_score": 0.85,
        "timestamp": "2026-02-25T10:00:00Z",
        "below_threshold": False,
    })
    history_path.write_text(record + "\n", encoding="utf-8")

    code, body = http_request("GET", f"{mobile_server.base_url}/health")
    assert code == 200
    payload = json.loads(body.decode("utf-8"))
    intel = payload["intelligence"]
    assert intel["score"] == 0.85
    assert intel["last_test"] == "2026-02-25T10:00:00Z"
    assert intel["regression"] is False


def test_health_endpoint_with_regression_detected(mobile_server) -> None:
    """Health endpoint reports regression when below_threshold is True."""
    runtime_dir = mobile_server.root / ".planning" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    history_path = runtime_dir / "self_test_history.jsonl"
    records = [
        json.dumps({"average_score": 0.85, "timestamp": "2026-02-24T10:00:00Z", "below_threshold": False}),
        json.dumps({"average_score": 0.42, "timestamp": "2026-02-25T10:00:00Z", "below_threshold": True}),
    ]
    history_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    code, body = http_request("GET", f"{mobile_server.base_url}/health")
    assert code == 200
    payload = json.loads(body.decode("utf-8"))
    intel = payload["intelligence"]
    assert intel["score"] == 0.42
    assert intel["regression"] is True


# ---------------------------------------------------------------------------
# TestHealthIntelligence — intelligence regression feature in /health
# ---------------------------------------------------------------------------


class TestHealthIntelligence:
    """Tests for the intelligence regression status returned by GET /health."""

    def test_health_includes_intelligence_data(self, mobile_server) -> None:
        """Create self_test_history.jsonl with valid data, verify /health response."""
        runtime_dir = mobile_server.root / ".planning" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        history_path = runtime_dir / "self_test_history.jsonl"
        record = json.dumps({
            "average_score": 0.85,
            "timestamp": "2026-02-25T12:00:00",
            "below_threshold": False,
        })
        history_path.write_text(record + "\n", encoding="utf-8")

        code, body = http_request("GET", f"{mobile_server.base_url}/health")
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is True
        assert payload["status"] == "healthy"
        intel = payload["intelligence"]
        assert intel["score"] == 0.85
        assert intel["regression"] is False
        assert intel["last_test"] == "2026-02-25T12:00:00"

    def test_health_intelligence_defaults_when_no_file(self, mobile_server) -> None:
        """Verify default values when self_test_history.jsonl doesn't exist."""
        # Ensure the file does not exist
        history_path = mobile_server.root / ".planning" / "runtime" / "self_test_history.jsonl"
        if history_path.exists():
            history_path.unlink()

        code, body = http_request("GET", f"{mobile_server.base_url}/health")
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is True
        assert payload["status"] == "healthy"
        intel = payload["intelligence"]
        assert intel["score"] == 0.0
        assert intel["regression"] is False
        assert intel["last_test"] == ""

    def test_health_intelligence_handles_corrupt_file(self, mobile_server) -> None:
        """Malformed JSONL doesn't crash /health — defaults are returned."""
        runtime_dir = mobile_server.root / ".planning" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        history_path = runtime_dir / "self_test_history.jsonl"
        # Write corrupt content that is not valid JSON
        history_path.write_text("{not valid json!!\n", encoding="utf-8")

        code, body = http_request("GET", f"{mobile_server.base_url}/health")
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is True
        assert payload["status"] == "healthy"
        # Should fall back to defaults since parsing failed
        intel = payload["intelligence"]
        assert intel["score"] == 0.0
        assert intel["regression"] is False
        assert intel["last_test"] == ""

    def test_health_intelligence_shows_regression(self, mobile_server) -> None:
        """Verify regression=true when below_threshold=true in history."""
        runtime_dir = mobile_server.root / ".planning" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        history_path = runtime_dir / "self_test_history.jsonl"
        records = [
            json.dumps({"average_score": 0.90, "timestamp": "2026-02-24T08:00:00", "below_threshold": False}),
            json.dumps({"average_score": 0.35, "timestamp": "2026-02-25T14:30:00", "below_threshold": True}),
        ]
        history_path.write_text("\n".join(records) + "\n", encoding="utf-8")

        code, body = http_request("GET", f"{mobile_server.base_url}/health")
        assert code == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is True
        intel = payload["intelligence"]
        assert intel["score"] == 0.35
        assert intel["regression"] is True
        assert intel["last_test"] == "2026-02-25T14:30:00"


def test_ingest_valid_request_writes_event(mobile_server) -> None:
    payload = {
        "source": "user",
        "kind": "semantic",
        "task_id": "mobile-001",
        "content": "Preference: keep summaries concise.",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 201
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert resp["task_id"] == "mobile-001"
    assert resp["kind"] == "semantic"
    assert isinstance(resp["record_id"], str)
    assert len(resp["record_id"]) == 32

    events_path = mobile_server.root / ".planning" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert lines
    event = json.loads(lines[-1])
    assert event["event_type"] == "ingest:user:semantic"
    assert "mobile-001" in event["message"]


def test_ingest_rejects_invalid_bearer(mobile_server) -> None:
    payload = {
        "source": "user",
        "kind": "episodic",
        "task_id": "mobile-002",
        "content": "Task done.",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    headers["Authorization"] = "Bearer wrong-token"
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 401


def test_ingest_rejects_invalid_signature(mobile_server) -> None:
    payload = {
        "source": "user",
        "kind": "episodic",
        "task_id": "mobile-003",
        "content": "Task done.",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    headers["X-Jarvis-Signature"] = "deadbeef"
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 401


def test_ingest_rejects_replay_nonce(mobile_server) -> None:
    payload = {
        "source": "user",
        "kind": "episodic",
        "task_id": "mobile-replay",
        "content": "replay test",
    }
    raw = json.dumps(payload).encode("utf-8")
    fixed_nonce = "abcd1234efgh5678"
    ts = time.time()
    headers = signed_headers(
        raw,
        mobile_server.auth_token,
        mobile_server.signing_key,
        timestamp=ts,
        nonce=fixed_nonce,
    )
    first_code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    second_code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert first_code == 201
    assert second_code == 401


def test_ingest_rejects_expired_timestamp(mobile_server) -> None:
    payload = {
        "source": "user",
        "kind": "episodic",
        "task_id": "mobile-expired",
        "content": "expired test",
    }
    raw = json.dumps(payload).encode("utf-8")
    old_ts = time.time() - 1200
    headers = signed_headers(
        raw,
        mobile_server.auth_token,
        mobile_server.signing_key,
        timestamp=old_ts,
    )
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 401


def test_ingest_rejects_invalid_json(mobile_server) -> None:
    raw = b"{bad json"
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 400


def test_ingest_rejects_invalid_utf8(mobile_server) -> None:
    raw = b"{\"source\": \"user\", \"kind\": \"semantic\", \"task_id\": \"t\", \"content\": \"\x80\"}"
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 400


def test_ingest_rejects_invalid_source(mobile_server) -> None:
    payload = {
        "source": "unknown",
        "kind": "semantic",
        "task_id": "mobile-004",
        "content": "x",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 400


def test_ingest_rejects_invalid_content_length_header(mobile_server) -> None:
    request = (
        "POST /ingest HTTP/1.1\r\n"
        f"Host: {mobile_server.host}:{mobile_server.port}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: abc\r\n"
        "\r\n"
    )
    sock = socket.create_connection((mobile_server.host, mobile_server.port), timeout=5)
    try:
        sock.sendall(request.encode("utf-8"))
        resp = sock.recv(2048).decode("utf-8", errors="replace")
        assert "400 Bad Request" in resp.splitlines()[0]
    finally:
        sock.close()


def test_concurrent_ingest_writes_are_jsonl_safe(mobile_server) -> None:
    # Clear rate limiter state so 80 concurrent requests aren't rate-limited
    mobile_server.server._api_rate_normal.clear()
    mobile_server.server._api_rate_expensive.clear()

    def worker(i: int) -> int:
        payload = {
            "source": "user",
            "kind": "episodic",
            "task_id": f"parallel-{i}",
            "content": f"parallel content {i}",
        }
        raw = json.dumps(payload).encode("utf-8")
        headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
        code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
        return code

    total = 80
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(worker, range(total)))

    assert all(code == 201 for code in results)

    events_path = mobile_server.root / ".planning" / "events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == total
    for line in lines:
        parsed = json.loads(line)
        assert parsed["event_type"].startswith("ingest:user:")


def test_settings_requires_auth(mobile_server) -> None:
    code, _ = http_request("GET", f"{mobile_server.base_url}/settings")
    assert code == 401


def test_settings_get_and_update_controls(mobile_server) -> None:
    get_headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    get_code, get_body = http_request("GET", f"{mobile_server.base_url}/settings", headers=get_headers)
    assert get_code == 200
    get_payload = json.loads(get_body.decode("utf-8"))
    assert get_payload["ok"] is True
    assert get_payload["settings"]["runtime_control"]["daemon_paused"] is False
    assert get_payload["settings"]["gaming_mode"]["enabled"] is False

    update_payload = {
        "daemon_paused": True,
        "safe_mode": True,
        "gaming_enabled": True,
        "gaming_auto_detect": True,
        "reason": "mobile test",
    }
    raw = json.dumps(update_payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/settings", raw, headers)
    assert code == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["settings"]["runtime_control"]["daemon_paused"] is True
    assert payload["settings"]["runtime_control"]["safe_mode"] is True
    assert payload["settings"]["gaming_mode"]["enabled"] is True
    assert payload["settings"]["gaming_mode"]["auto_detect"] is True


def test_settings_reset(mobile_server) -> None:
    update_payload = {
        "daemon_paused": True,
        "safe_mode": True,
        "gaming_enabled": True,
        "gaming_auto_detect": True,
        "reason": "before reset",
    }
    update_raw = json.dumps(update_payload).encode("utf-8")
    update_headers = signed_headers(update_raw, mobile_server.auth_token, mobile_server.signing_key)
    code, _ = http_request("POST", f"{mobile_server.base_url}/settings", update_raw, update_headers)
    assert code == 200

    reset_payload = {"reset": True, "reason": "reset"}
    reset_raw = json.dumps(reset_payload).encode("utf-8")
    reset_headers = signed_headers(reset_raw, mobile_server.auth_token, mobile_server.signing_key)
    reset_code, reset_body = http_request("POST", f"{mobile_server.base_url}/settings", reset_raw, reset_headers)
    assert reset_code == 200
    payload = json.loads(reset_body.decode("utf-8"))
    assert payload["settings"]["runtime_control"]["daemon_paused"] is False
    assert payload["settings"]["runtime_control"]["safe_mode"] is False
    assert payload["settings"]["gaming_mode"]["enabled"] is False
    assert payload["settings"]["gaming_mode"]["auto_detect"] is False


def test_settings_rejects_invalid_reset_type(mobile_server) -> None:
    payload = {"reset": "yes"}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, _ = http_request("POST", f"{mobile_server.base_url}/settings", raw, headers)
    assert code == 400


def test_quick_panel_endpoint_serves_html(mobile_server) -> None:
    quick_path = mobile_server.root / "mobile" / "quick_access.html"
    quick_path.parent.mkdir(parents=True, exist_ok=True)
    quick_path.write_text("<html><body>quick</body></html>", encoding="utf-8")
    code, body = http_request("GET", f"{mobile_server.base_url}/quick")
    assert code == 200
    assert b"quick" in body


def test_dashboard_endpoint_requires_auth(mobile_server) -> None:
    code, _ = http_request("GET", f"{mobile_server.base_url}/dashboard")
    assert code == 401


def test_dashboard_endpoint_returns_payload(mobile_server) -> None:
    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/dashboard", headers=headers)
    assert code == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert "dashboard" in payload
    assert "ranking" in payload["dashboard"]


def test_command_endpoint_executes_voice_route(mobile_server) -> None:
    payload = {
        "text": "Jarvis, runtime status",
        "execute": False,
        "approve_privileged": False,
        "speak": False,
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/command", raw, headers)
    assert code == 200
    parsed = json.loads(body.decode("utf-8"))
    assert parsed["ok"] is True
    assert int(parsed["command_exit_code"]) == 0


def test_command_endpoint_returns_200_with_structured_failure(mobile_server) -> None:
    from unittest.mock import patch
    from jarvis_engine.gateway.models import GatewayResponse

    def _mock_complete(self, messages, model="claude-sonnet-4-5-20250929", max_tokens=1024, route_reason=""):
        return GatewayResponse(
            text="", model=model, provider="none",
            fallback_used=True, fallback_reason="mocked for test",
        )

    payload = {
        "text": "Jarvis, this intent does not exist",
        "execute": False,
        "approve_privileged": False,
        "speak": False,
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    mock_cls = type("MockClassifier", (), {
        "classify": lambda self, q: ("routine", "mock-model", 0.9),
    })
    with patch("jarvis_engine.gateway.models.ModelGateway.complete", _mock_complete), \
         patch("jarvis_engine.main._build_smart_context", return_value=([], [], [], [])), \
         patch("jarvis_engine.gateway.classifier.IntentClassifier", mock_cls):
        code, body = http_request("POST", f"{mobile_server.base_url}/command", raw, headers)
    assert code == 200
    parsed = json.loads(body.decode("utf-8"))
    assert parsed["ok"] is False
    assert int(parsed["command_exit_code"]) != 0


def test_owner_guard_requires_trusted_device_header(mobile_server) -> None:
    write_owner_guard(mobile_server.root, enabled=True, owner_user_id="conner")

    payload = {
        "source": "user",
        "kind": "semantic",
        "task_id": "owner-guard-mobile",
        "content": "locked ingress test",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 401


def test_owner_guard_allows_trusted_device_header(mobile_server) -> None:
    write_owner_guard(mobile_server.root, enabled=True, owner_user_id="conner")
    trust_mobile_device(mobile_server.root, "galaxy_s25_primary")

    payload = {
        "source": "user",
        "kind": "semantic",
        "task_id": "owner-guard-mobile-2",
        "content": "allowed ingress test",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    headers["X-Jarvis-Device-Id"] = "galaxy_s25_primary"
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 201


def test_owner_guard_bootstrap_trust_with_master_password(mobile_server) -> None:
    write_owner_guard(mobile_server.root, enabled=True, owner_user_id="conner")
    set_master_password(mobile_server.root, "VeryStrongPassword123!")

    payload = {
        "source": "user",
        "kind": "semantic",
        "task_id": "owner-guard-bootstrap-1",
        "content": "bootstrap device trust",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    headers["X-Jarvis-Device-Id"] = "galaxy_s25_primary"
    headers["X-Jarvis-Master-Password"] = "VeryStrongPassword123!"
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 201

    payload2 = {
        "source": "user",
        "kind": "semantic",
        "task_id": "owner-guard-bootstrap-2",
        "content": "trusted device follow up",
    }
    raw2 = json.dumps(payload2).encode("utf-8")
    headers2 = signed_headers(raw2, mobile_server.auth_token, mobile_server.signing_key)
    headers2["X-Jarvis-Device-Id"] = "galaxy_s25_primary"
    code2, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw2, headers2)
    assert code2 == 201


def test_bootstrap_endpoint_returns_session_and_trusts_device(mobile_server) -> None:
    set_master_password(mobile_server.root, "VeryStrongPassword123!")
    payload = {
        "master_password": "VeryStrongPassword123!",
        "device_id": "galaxy_s25_primary",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Host": f"{mobile_server.host}:{mobile_server.port}"}
    code, body = http_request("POST", f"{mobile_server.base_url}/bootstrap", raw, headers)
    assert code == 200
    parsed = json.loads(body.decode("utf-8"))
    assert parsed["ok"] is True
    assert parsed["session"]["token"] == mobile_server.auth_token
    assert parsed["session"]["signing_key"] == mobile_server.signing_key
    assert parsed["session"]["device_id"] == "galaxy_s25_primary"
    assert parsed["session"]["trusted_device"] is True


def test_bootstrap_endpoint_rejects_invalid_master_password(mobile_server) -> None:
    set_master_password(mobile_server.root, "VeryStrongPassword123!")
    payload = {
        "master_password": "wrong",
        "device_id": "galaxy_s25_primary",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    code, _ = http_request("POST", f"{mobile_server.base_url}/bootstrap", raw, headers)
    assert code == 401


def test_sync_endpoint_redirects_to_new_endpoints(mobile_server, monkeypatch) -> None:
    """Old /sync endpoint returns 410 Gone with migration pointers."""
    payload = {"auto_ingest": True}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/sync", raw, headers)
    assert code == 410
    parsed = json.loads(body.decode("utf-8"))
    assert parsed["ok"] is False
    assert "/sync/pull" in parsed["endpoints"]
    assert "/sync/push" in parsed["endpoints"]


def test_self_heal_endpoint_calls_main_cli(mobile_server, monkeypatch) -> None:
    called: list[tuple[list[str], int]] = []

    def fake_run_main_cli(self, args, timeout_s=240):  # noqa: ANN001, ANN202
        called.append((list(args), int(timeout_s)))
        return {"ok": True, "command_exit_code": 0, "stdout_tail": ["heal ok"], "stderr_tail": []}

    monkeypatch.setattr(mobile_api.MobileIngestHandler, "_run_main_cli", fake_run_main_cli)
    payload = {"keep_recent": 2300, "force_maintenance": True, "snapshot_note": "api-test"}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/self-heal", raw, headers)
    assert code == 200
    parsed = json.loads(body.decode("utf-8"))
    assert parsed["ok"] is True
    assert called == [
        (
            ["self-heal", "--keep-recent", "2300", "--snapshot-note", "api-test", "--force-maintenance"],
            240,
        )
    ]


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_health_response_includes_security_headers(mobile_server) -> None:
    """All responses should include security headers."""
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(mobile_server.base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    resp.read()

    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert resp.getheader("X-Frame-Options") == "DENY"
    assert resp.getheader("X-XSS-Protection") == "1; mode=block"
    assert resp.getheader("Cache-Control") == "no-store"
    assert resp.getheader("Referrer-Policy") == "no-referrer"
    conn.close()


# ---------------------------------------------------------------------------
# Global API rate limiting
# ---------------------------------------------------------------------------


def test_api_rate_limiter_allows_normal_requests(mobile_server) -> None:
    """A few requests should not be rate-limited."""
    for _ in range(3):
        code, _ = http_request("GET", f"{mobile_server.base_url}/health")
        assert code == 200


def test_api_rate_limiter_blocks_excessive_post_requests(mobile_server) -> None:
    """Exceeding _API_RATE_LIMIT_EXPENSIVE on /command should yield 429."""
    import unittest.mock as _mock
    from jarvis_engine.gateway.models import GatewayResponse

    def _mock_complete(self, messages, model="", max_tokens=1024, route_reason=""):
        return GatewayResponse(text="hi", model=model, provider="mock")

    mock_cls = type("MockClassifier", (), {
        "classify": lambda self, q: ("routine", "mock-model", 0.9),
    })
    # Temporarily lower the limit for testing
    with _mock.patch.object(mobile_api, "_API_RATE_LIMIT_EXPENSIVE", 2), \
         _mock.patch("jarvis_engine.gateway.models.ModelGateway.complete", _mock_complete), \
         _mock.patch("jarvis_engine.main._build_smart_context", return_value=([], [], [], [])), \
         _mock.patch("jarvis_engine.gateway.classifier.IntentClassifier", mock_cls):
        # Clear any existing rate state for our IP
        mobile_server.server._api_rate_normal.clear()
        mobile_server.server._api_rate_expensive.clear()

        payload = {"text": "hello jarvis"}
        raw = json.dumps(payload).encode("utf-8")

        for i in range(4):
            headers = signed_headers(
                raw, mobile_server.auth_token, mobile_server.signing_key,
            )
            code, body = http_request("POST", f"{mobile_server.base_url}/command", raw, headers)
            if i >= 2:
                # Should be rate-limited after 2 requests
                assert code == 429, f"Expected 429 on request {i + 1}, got {code}"
                resp = json.loads(body.decode("utf-8"))
                assert "rate limit" in resp["error"].lower()
                break


# ---------------------------------------------------------------------------
# Audit endpoint
# ---------------------------------------------------------------------------


def test_audit_endpoint_requires_auth(mobile_server) -> None:
    """GET /audit without auth should return 401."""
    code, _ = http_request("GET", f"{mobile_server.base_url}/audit")
    assert code == 401


def test_audit_endpoint_returns_empty_when_no_file(mobile_server) -> None:
    """GET /audit with auth should return empty list if no audit file."""
    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/audit", headers=headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert resp["audit"] == []
    assert resp["total"] == 0


def test_audit_endpoint_returns_records(mobile_server) -> None:
    """GET /audit returns audit records from JSONL file."""
    audit_path = mobile_server.root / ".planning" / "runtime" / "gateway_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        '{"ts":"2026-02-24T10:00:00","provider":"groq","model":"mixtral","reason":"primary","latency_ms":120.5}',
        '{"ts":"2026-02-24T10:01:00","provider":"ollama","model":"qwen3:8b","reason":"privacy","latency_ms":450.0}',
    ]
    audit_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/audit", headers=headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert resp["total"] == 2
    assert resp["audit"][0]["provider"] == "groq"
    assert resp["audit"][1]["provider"] == "ollama"


def test_processes_endpoint_returns_services(mobile_server) -> None:
    """GET /processes returns service statuses with auth."""
    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/processes", headers=headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert isinstance(resp["services"], list)
    assert len(resp["services"]) == 3
    names = {s["service"] for s in resp["services"]}
    assert names == {"daemon", "mobile_api", "widget"}


def test_processes_kill_requires_auth(mobile_server) -> None:
    """POST /processes/kill requires HMAC auth."""
    raw = json.dumps({"service": "daemon"}).encode("utf-8")
    code, _body = http_request("POST", f"{mobile_server.base_url}/processes/kill", raw,
                               {"Content-Type": "application/json"})
    assert code == 401


def test_processes_kill_rejects_unknown_service(mobile_server) -> None:
    """POST /processes/kill returns 400 for unknown service name."""
    raw = json.dumps({"service": "nonexistent"}).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/processes/kill", raw, headers)
    assert code == 400
    resp = json.loads(body.decode("utf-8"))
    assert "Unknown service" in resp["error"]


# ---------------------------------------------------------------------------
# /sync/pull endpoint
# ---------------------------------------------------------------------------


def test_sync_pull_success_with_valid_auth(mobile_server, monkeypatch) -> None:
    """POST /sync/pull returns encrypted payload when sync engine is available."""
    import base64

    mock_outgoing = {"changes": {"memories": []}, "cursors": {"memories": 42}}
    mock_encrypted = b"fake-encrypted-data"

    class FakeSyncEngine:
        def compute_outgoing(self, device_id):
            return mock_outgoing

    class FakeSyncTransport:
        def encrypt(self, payload):
            return mock_encrypted

    mobile_server.server._sync_engine = FakeSyncEngine()
    mobile_server.server._sync_transport = FakeSyncTransport()

    payload = {"device_id": "galaxy_s25_primary"}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/sync/pull", raw, headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert resp["encrypted_payload"] == base64.b64encode(mock_encrypted).decode("ascii")
    assert resp["new_cursors"] == {"memories": 42}
    assert resp["has_more"] is False


def test_sync_pull_rejects_invalid_auth(mobile_server) -> None:
    """POST /sync/pull with bad bearer token returns 401."""
    payload = {"device_id": "galaxy_s25_primary"}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    headers["Authorization"] = "Bearer wrong-token"
    code, _ = http_request("POST", f"{mobile_server.base_url}/sync/pull", raw, headers)
    assert code == 401


def test_sync_pull_rejects_missing_device_id(mobile_server) -> None:
    """POST /sync/pull with empty device_id returns 400."""
    payload = {"device_id": ""}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/sync/pull", raw, headers)
    assert code == 400
    resp = json.loads(body.decode("utf-8"))
    assert "device_id" in resp["error"].lower()


def test_sync_pull_returns_503_when_sync_unavailable(mobile_server) -> None:
    """POST /sync/pull when no sync engine returns 503."""
    mobile_server.server._sync_engine = None
    mobile_server.server._sync_transport = None

    payload = {"device_id": "galaxy_s25_primary"}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/sync/pull", raw, headers)
    assert code == 503
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is False


# ---------------------------------------------------------------------------
# /sync/push endpoint
# ---------------------------------------------------------------------------


def test_sync_push_success_with_valid_auth(mobile_server) -> None:
    """POST /sync/push applies incoming changes successfully."""
    import base64

    mock_result = {"applied": 5, "conflicts_resolved": 1, "errors": []}

    class FakeSyncEngine:
        def apply_incoming(self, changes, device_id):
            return mock_result

    class FakeSyncTransport:
        def decrypt(self, token, ttl=3600):
            return {"changes": []}

    mobile_server.server._sync_engine = FakeSyncEngine()
    mobile_server.server._sync_transport = FakeSyncTransport()

    encrypted = base64.b64encode(b"fake-encrypted").decode("ascii")
    payload = {"device_id": "galaxy_s25_primary", "encrypted_payload": encrypted}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/sync/push", raw, headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert resp["applied"] == 5
    assert resp["conflicts_resolved"] == 1
    assert resp["errors"] == []


def test_sync_push_rejects_invalid_auth(mobile_server) -> None:
    """POST /sync/push with bad signature returns 401."""
    import base64

    encrypted = base64.b64encode(b"fake").decode("ascii")
    payload = {"device_id": "galaxy_s25_primary", "encrypted_payload": encrypted}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    headers["X-Jarvis-Signature"] = "deadbeef"
    code, _ = http_request("POST", f"{mobile_server.base_url}/sync/push", raw, headers)
    assert code == 401


def test_sync_push_rejects_missing_encrypted_payload(mobile_server) -> None:
    """POST /sync/push with empty encrypted_payload returns 400."""
    payload = {"device_id": "galaxy_s25_primary", "encrypted_payload": ""}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/sync/push", raw, headers)
    assert code == 400
    resp = json.loads(body.decode("utf-8"))
    assert "encrypted_payload" in resp["error"].lower()


def test_sync_push_rejects_missing_device_id(mobile_server) -> None:
    """POST /sync/push with empty device_id returns 400."""
    import base64

    encrypted = base64.b64encode(b"data").decode("ascii")
    payload = {"device_id": "", "encrypted_payload": encrypted}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/sync/push", raw, headers)
    assert code == 400
    resp = json.loads(body.decode("utf-8"))
    assert "device_id" in resp["error"].lower()


def test_sync_push_returns_503_when_sync_unavailable(mobile_server) -> None:
    """POST /sync/push when no sync engine returns 503."""
    import base64

    mobile_server.server._sync_engine = None
    mobile_server.server._sync_transport = None

    encrypted = base64.b64encode(b"data").decode("ascii")
    payload = {"device_id": "galaxy_s25_primary", "encrypted_payload": encrypted}
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("POST", f"{mobile_server.base_url}/sync/push", raw, headers)
    assert code == 503
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is False


# ---------------------------------------------------------------------------
# /sync/status endpoint
# ---------------------------------------------------------------------------


def test_sync_status_returns_status_when_available(mobile_server) -> None:
    """GET /sync/status returns sync status from engine."""
    mock_status = {"last_sync": "2026-02-25T10:00:00", "pending_changes": 3}

    class FakeSyncEngine:
        def sync_status(self):
            return mock_status

    mobile_server.server._sync_engine = FakeSyncEngine()

    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/sync/status", headers=headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert resp["sync_status"] == mock_status


def test_sync_status_returns_503_when_unavailable(mobile_server) -> None:
    """GET /sync/status when no sync engine returns 503."""
    mobile_server.server._sync_engine = None

    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/sync/status", headers=headers)
    assert code == 503
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is False
    assert "not available" in resp["error"].lower()


def test_sync_status_requires_auth(mobile_server) -> None:
    """GET /sync/status without auth returns 401."""
    code, _ = http_request("GET", f"{mobile_server.base_url}/sync/status")
    assert code == 401


def test_sync_status_handles_engine_exception(mobile_server) -> None:
    """GET /sync/status returns 500 when engine raises."""

    class BrokenSyncEngine:
        def sync_status(self):
            raise RuntimeError("db locked")

    mobile_server.server._sync_engine = BrokenSyncEngine()

    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/sync/status", headers=headers)
    assert code == 500
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is False


# ---------------------------------------------------------------------------
# CORS handling
# ---------------------------------------------------------------------------


def test_options_returns_cors_headers(mobile_server) -> None:
    """OPTIONS request returns 204 with CORS headers."""
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(mobile_server.base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    conn.request("OPTIONS", "/health", headers={"Origin": "http://localhost:3000"})
    resp = conn.getresponse()
    resp.read()

    assert resp.status == 204
    assert "GET" in resp.getheader("Access-Control-Allow-Methods", "")
    assert "POST" in resp.getheader("Access-Control-Allow-Methods", "")
    assert "OPTIONS" in resp.getheader("Access-Control-Allow-Methods", "")
    assert "X-Jarvis-Signature" in resp.getheader("Access-Control-Allow-Headers", "")
    assert resp.getheader("Access-Control-Max-Age") == "3600"
    conn.close()


def test_cors_allows_localhost_origin(mobile_server) -> None:
    """CORS headers include Access-Control-Allow-Origin for localhost."""
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(mobile_server.base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    conn.request("GET", "/health", headers={"Origin": "http://localhost:8080"})
    resp = conn.getresponse()
    resp.read()

    assert resp.getheader("Access-Control-Allow-Origin") == "http://localhost:8080"
    assert resp.getheader("Vary") == "Origin"
    conn.close()


def test_cors_blocks_unknown_origin(mobile_server) -> None:
    """CORS headers omit Access-Control-Allow-Origin for unknown origins."""
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(mobile_server.base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    conn.request("GET", "/health", headers={"Origin": "https://evil.com"})
    resp = conn.getresponse()
    resp.read()

    assert resp.getheader("Access-Control-Allow-Origin") is None
    conn.close()


def test_is_cors_origin_allowed_patterns(mobile_server) -> None:
    """is_cors_origin_allowed returns correct results for various origins."""
    server = mobile_server.server
    assert server.is_cors_origin_allowed("http://localhost") is True
    assert server.is_cors_origin_allowed("http://localhost:3000") is True
    assert server.is_cors_origin_allowed("https://localhost:443") is True
    assert server.is_cors_origin_allowed("http://127.0.0.1") is True
    assert server.is_cors_origin_allowed("http://127.0.0.1:8787") is True
    assert server.is_cors_origin_allowed("http://[::1]") is True
    assert server.is_cors_origin_allowed("http://[::1]:8080") is True
    assert server.is_cors_origin_allowed("file:///C:/Users/test/page.html") is True
    assert server.is_cors_origin_allowed("file:///home/user/page.html") is False  # Unix path rejected
    assert server.is_cors_origin_allowed("https://evil.com") is False
    assert server.is_cors_origin_allowed("http://192.168.1.100") is False
    assert server.is_cors_origin_allowed("") is False


# ---------------------------------------------------------------------------
# Gaming state management
# ---------------------------------------------------------------------------


def _make_handler_stub(server):
    """Create a minimal object that can call MobileIngestHandler methods
    that only need ``self.server`` (gaming state, nonce cleanup, _run_main_cli)."""
    class _Stub:
        pass
    stub = _Stub()
    stub.server = server
    # Bind unbound methods from the real handler class
    stub._gaming_state_path = mobile_api.MobileIngestHandler._gaming_state_path.__get__(stub)
    stub._read_gaming_state = mobile_api.MobileIngestHandler._read_gaming_state.__get__(stub)
    stub._write_gaming_state = mobile_api.MobileIngestHandler._write_gaming_state.__get__(stub)
    stub._cleanup_nonces = mobile_api.MobileIngestHandler._cleanup_nonces.__get__(stub)
    stub._run_main_cli = mobile_api.MobileIngestHandler._run_main_cli.__get__(stub)
    return stub


def test_read_gaming_state_file_missing(mobile_server) -> None:
    """_read_gaming_state returns defaults when file does not exist."""
    stub = _make_handler_stub(mobile_server.server)
    state = stub._read_gaming_state()
    assert state["enabled"] is False
    assert state["auto_detect"] is False
    assert state["reason"] == ""
    assert state["updated_utc"] == ""


def test_read_gaming_state_file_exists(mobile_server) -> None:
    """_read_gaming_state reads valid JSON from disk."""
    runtime_dir = mobile_server.root / ".planning" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    state = {"enabled": True, "auto_detect": True, "reason": "playing", "updated_utc": "2026-02-25T10:00:00"}
    (runtime_dir / "gaming_mode.json").write_text(json.dumps(state), encoding="utf-8")

    stub = _make_handler_stub(mobile_server.server)
    result = stub._read_gaming_state()
    assert result["enabled"] is True
    assert result["auto_detect"] is True
    assert result["reason"] == "playing"


def test_read_gaming_state_corrupt_json(mobile_server) -> None:
    """_read_gaming_state returns defaults for corrupt JSON."""
    runtime_dir = mobile_server.root / ".planning" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "gaming_mode.json").write_text("{bad json", encoding="utf-8")

    stub = _make_handler_stub(mobile_server.server)
    result = stub._read_gaming_state()
    assert result["enabled"] is False
    assert result["auto_detect"] is False


def test_write_gaming_state_roundtrip(mobile_server) -> None:
    """_write_gaming_state writes state that _read_gaming_state can read back."""
    runtime_dir = mobile_server.root / ".planning" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    stub = _make_handler_stub(mobile_server.server)
    written = stub._write_gaming_state(enabled=True, auto_detect=False, reason="test roundtrip")
    assert written["enabled"] is True
    assert written["auto_detect"] is False
    assert written["reason"] == "test roundtrip"
    assert written["updated_utc"] != ""

    read_back = stub._read_gaming_state()
    assert read_back["enabled"] is True
    assert read_back["auto_detect"] is False
    assert read_back["reason"] == "test roundtrip"


def test_gaming_state_path_returns_expected_path(mobile_server) -> None:
    """_gaming_state_path returns correct path under repo root."""
    stub = _make_handler_stub(mobile_server.server)
    path = stub._gaming_state_path()
    assert path.name == "gaming_mode.json"
    assert "runtime" in str(path)
    assert ".planning" in str(path)


# ---------------------------------------------------------------------------
# _run_main_cli method
# ---------------------------------------------------------------------------


def test_run_main_cli_successful_command(mobile_server) -> None:
    """_run_main_cli returns success result for subprocess that exits 0."""
    from unittest.mock import MagicMock, patch

    engine_dir = mobile_server.root / "engine"
    engine_dir.mkdir(parents=True, exist_ok=True)

    stub = _make_handler_stub(mobile_server.server)

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "output line 1\noutput line 2\n"
    fake_result.stderr = ""

    with patch("subprocess.run", return_value=fake_result):
        result = stub._run_main_cli(["self-heal"])
    assert result["ok"] is True
    assert result["command_exit_code"] == 0
    assert "output line 1" in result["stdout_tail"]


def test_run_main_cli_command_timeout(mobile_server) -> None:
    """_run_main_cli handles TimeoutExpired gracefully."""
    import subprocess
    from unittest.mock import patch

    engine_dir = mobile_server.root / "engine"
    engine_dir.mkdir(parents=True, exist_ok=True)

    stub = _make_handler_stub(mobile_server.server)

    exc = subprocess.TimeoutExpired(cmd=["python"], timeout=30)
    exc.stdout = "partial output"
    exc.stderr = "partial error"

    with patch("subprocess.run", side_effect=exc):
        result = stub._run_main_cli(["self-heal"], timeout_s=30)
    assert result["ok"] is False
    assert "timed out" in result["error"].lower()
    assert result["command_exit_code"] == 2


def test_run_main_cli_command_failure(mobile_server) -> None:
    """_run_main_cli handles non-zero exit code."""
    from unittest.mock import MagicMock, patch

    engine_dir = mobile_server.root / "engine"
    engine_dir.mkdir(parents=True, exist_ok=True)

    stub = _make_handler_stub(mobile_server.server)

    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stdout = "some output\n"
    fake_result.stderr = "error occurred\n"

    with patch("subprocess.run", return_value=fake_result):
        result = stub._run_main_cli(["bad-cmd"])
    assert result["ok"] is False
    assert result["command_exit_code"] == 1
    assert "error occurred" in result["stderr_tail"]


def test_run_main_cli_engine_dir_missing(mobile_server) -> None:
    """_run_main_cli returns error when engine directory does not exist."""
    stub = _make_handler_stub(mobile_server.server)

    result = stub._run_main_cli(["self-heal"])
    assert result["ok"] is False
    assert result["command_exit_code"] == 2
    assert "not found" in result["error"].lower()


def test_run_main_cli_os_error(mobile_server) -> None:
    """_run_main_cli handles OSError from subprocess."""
    from unittest.mock import patch

    engine_dir = mobile_server.root / "engine"
    engine_dir.mkdir(parents=True, exist_ok=True)

    stub = _make_handler_stub(mobile_server.server)

    with patch("subprocess.run", side_effect=OSError("No such file")):
        result = stub._run_main_cli(["self-heal"])
    assert result["ok"] is False
    assert result["command_exit_code"] == 2
    assert result["error"] == "Command execution failed."
    assert result["stderr_tail"] == []


# ---------------------------------------------------------------------------
# _cleanup_nonces method
# ---------------------------------------------------------------------------


def test_cleanup_nonces_removes_old_nonces(mobile_server) -> None:
    """_cleanup_nonces purges nonces older than REPLAY_WINDOW_SECONDS."""
    stub = _make_handler_stub(mobile_server.server)

    now = time.time()
    old_ts = now - mobile_api.REPLAY_WINDOW_SECONDS - 60  # expired
    recent_ts = now - 10  # still valid

    mobile_server.server.nonce_seen["old_nonce"] = old_ts
    mobile_server.server.nonce_seen["recent_nonce"] = recent_ts

    stub._cleanup_nonces(now, force=True)

    assert "old_nonce" not in mobile_server.server.nonce_seen
    assert "recent_nonce" in mobile_server.server.nonce_seen


def test_cleanup_nonces_retains_recent(mobile_server) -> None:
    """_cleanup_nonces keeps nonces within the replay window."""
    stub = _make_handler_stub(mobile_server.server)

    now = time.time()
    mobile_server.server.nonce_seen.clear()

    for i in range(5):
        mobile_server.server.nonce_seen[f"nonce_{i}"] = now - i * 10

    stub._cleanup_nonces(now, force=True)

    assert len(mobile_server.server.nonce_seen) == 5


def test_cleanup_nonces_skips_when_not_due(mobile_server) -> None:
    """_cleanup_nonces skips cleanup when interval hasn't elapsed (without force)."""
    stub = _make_handler_stub(mobile_server.server)

    now = time.time()
    # Set next cleanup far in the future
    mobile_server.server.next_nonce_cleanup_ts = now + 9999

    old_ts = now - mobile_api.REPLAY_WINDOW_SECONDS - 60
    mobile_server.server.nonce_seen["expired_nonce"] = old_ts

    stub._cleanup_nonces(now, force=False)

    # Should NOT have been cleaned because we are not due yet
    assert "expired_nonce" in mobile_server.server.nonce_seen


# ---------------------------------------------------------------------------
# Nonce persistence across restarts
# ---------------------------------------------------------------------------


def test_persist_nonces_writes_jsonl_file(mobile_server) -> None:
    """_persist_nonces writes valid nonces to JSONL file on disk."""
    now = time.time()
    mobile_server.server.nonce_seen.clear()
    mobile_server.server.nonce_seen["nonce_alpha"] = now - 10
    mobile_server.server.nonce_seen["nonce_beta"] = now - 20

    mobile_server.server._persist_nonces()

    cache_path = mobile_server.server._nonce_cache_path
    assert cache_path.exists()
    lines = cache_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    nonces_on_disk = set()
    for line in lines:
        entry = json.loads(line)
        assert "nonce" in entry
        assert "ts" in entry
        nonces_on_disk.add(entry["nonce"])
    assert nonces_on_disk == {"nonce_alpha", "nonce_beta"}


def test_persist_nonces_excludes_expired(mobile_server) -> None:
    """_persist_nonces only writes nonces within the replay window."""
    now = time.time()
    mobile_server.server.nonce_seen.clear()
    mobile_server.server.nonce_seen["fresh"] = now - 10
    mobile_server.server.nonce_seen["expired"] = now - mobile_api.REPLAY_WINDOW_SECONDS - 60

    mobile_server.server._persist_nonces()

    cache_path = mobile_server.server._nonce_cache_path
    lines = cache_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["nonce"] == "fresh"


def test_load_nonces_restores_valid_nonces(mobile_server) -> None:
    """_load_nonces restores nonces from disk that are within the replay window."""
    now = time.time()
    cache_path = mobile_server.server._nonce_cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    entries = [
        json.dumps({"nonce": "restored_1", "ts": now - 10}),
        json.dumps({"nonce": "restored_2", "ts": now - 50}),
        json.dumps({"nonce": "too_old", "ts": now - mobile_api.REPLAY_WINDOW_SECONDS - 100}),
    ]
    cache_path.write_text("\n".join(entries) + "\n", encoding="utf-8")

    mobile_server.server.nonce_seen.clear()
    mobile_server.server._load_nonces()

    assert "restored_1" in mobile_server.server.nonce_seen
    assert "restored_2" in mobile_server.server.nonce_seen
    assert "too_old" not in mobile_server.server.nonce_seen


def test_load_nonces_handles_missing_file(mobile_server) -> None:
    """_load_nonces silently handles missing cache file."""
    cache_path = mobile_server.server._nonce_cache_path
    if cache_path.exists():
        cache_path.unlink()

    mobile_server.server.nonce_seen.clear()
    mobile_server.server._load_nonces()
    assert len(mobile_server.server.nonce_seen) == 0


def test_cleanup_nonces_triggers_persist(mobile_server) -> None:
    """_cleanup_nonces calls _persist_nonces during periodic cleanup."""
    stub = _make_handler_stub(mobile_server.server)

    now = time.time()
    mobile_server.server.nonce_seen.clear()
    mobile_server.server.nonce_seen["persist_test"] = now - 5

    stub._cleanup_nonces(now, force=True)

    cache_path = mobile_server.server._nonce_cache_path
    assert cache_path.exists()
    lines = cache_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["nonce"] == "persist_test"


# ---------------------------------------------------------------------------
# TestNoncePersistence — nonce cache persistence across server restarts
# ---------------------------------------------------------------------------


class TestNoncePersistence:
    """Tests for _persist_nonces / _load_nonces round-trip and edge cases."""

    def _make_server(self, root: Path) -> MobileIngestServer:
        """Create a MobileIngestServer for unit tests (not started)."""
        store = MemoryStore(root)
        pipeline = IngestionPipeline(store)
        return MobileIngestServer(
            ("127.0.0.1", 0),
            MobileIngestHandler,
            auth_token="t",
            signing_key="k",
            pipeline=pipeline,
            repo_root=root,
        )

    def test_persist_nonces_creates_file(self, tmp_path: Path) -> None:
        """_persist_nonces creates a JSONL file with correct nonce entries."""
        from unittest.mock import patch

        root = tmp_path / "repo"
        root.mkdir(parents=True, exist_ok=True)
        fake_now = 1_700_000_000.0

        with patch("time.time", return_value=fake_now):
            server = self._make_server(root)

        server.nonce_seen["aaa"] = fake_now - 10
        server.nonce_seen["bbb"] = fake_now - 20

        with patch("time.time", return_value=fake_now):
            server._persist_nonces()

        cache_path = server._nonce_cache_path
        assert cache_path.exists(), "Nonce cache file should exist after persist"
        lines = cache_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        written = {}
        for line in lines:
            entry = json.loads(line)
            assert "nonce" in entry
            assert "ts" in entry
            assert isinstance(entry["ts"], float)
            written[entry["nonce"]] = entry["ts"]
        assert set(written.keys()) == {"aaa", "bbb"}
        assert written["aaa"] == fake_now - 10
        assert written["bbb"] == fake_now - 20

    def test_load_nonces_restores_valid(self, tmp_path: Path) -> None:
        """Persist nonces on one server, create a new server, verify they load."""
        from unittest.mock import patch

        root = tmp_path / "repo"
        root.mkdir(parents=True, exist_ok=True)
        fake_now = 1_700_000_000.0

        with patch("time.time", return_value=fake_now):
            server1 = self._make_server(root)

        server1.nonce_seen["nonce_x"] = fake_now - 30
        server1.nonce_seen["nonce_y"] = fake_now - 60

        with patch("time.time", return_value=fake_now):
            server1._persist_nonces()

        # Create a brand-new server (simulates restart) — _load_nonces runs in __init__
        with patch("time.time", return_value=fake_now):
            server2 = self._make_server(root)

        assert "nonce_x" in server2.nonce_seen
        assert "nonce_y" in server2.nonce_seen
        assert len(server2.nonce_seen) == 2

    def test_load_nonces_filters_expired(self, tmp_path: Path) -> None:
        """Load a cache with valid and expired nonces; only valid survive."""
        from unittest.mock import patch

        root = tmp_path / "repo"
        root.mkdir(parents=True, exist_ok=True)
        fake_now = 1_700_000_000.0

        # Write both valid and expired nonces directly to disk
        cache_path = root / ".planning" / "runtime" / "nonce_cache.jsonl"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            json.dumps({"nonce": "still_valid", "ts": fake_now - 50}),
            json.dumps({"nonce": "barely_valid", "ts": fake_now - 119}),
            json.dumps({"nonce": "just_expired", "ts": fake_now - 181}),
            json.dumps({"nonce": "way_too_old", "ts": fake_now - 999}),
        ]
        cache_path.write_text("\n".join(entries) + "\n", encoding="utf-8")

        with patch("time.time", return_value=fake_now):
            server = self._make_server(root)

        assert "still_valid" in server.nonce_seen
        assert "barely_valid" in server.nonce_seen
        assert "just_expired" not in server.nonce_seen
        assert "way_too_old" not in server.nonce_seen
        assert len(server.nonce_seen) == 2

    def test_persist_atomic_write(self, tmp_path: Path) -> None:
        """After successful persist, the .tmp file should not remain on disk."""
        from unittest.mock import patch

        root = tmp_path / "repo"
        root.mkdir(parents=True, exist_ok=True)
        fake_now = 1_700_000_000.0

        with patch("time.time", return_value=fake_now):
            server = self._make_server(root)

        server.nonce_seen["abc123"] = fake_now - 5

        with patch("time.time", return_value=fake_now):
            server._persist_nonces()

        assert server._nonce_cache_path.exists()
        tmp_file = server._nonce_cache_path.with_suffix(".jsonl.tmp")
        assert not tmp_file.exists(), "Temp file should be cleaned up after atomic rename"

    def test_load_nonces_handles_missing_file(self, tmp_path: Path) -> None:
        """_load_nonces does not crash when no cache file exists on disk."""
        root = tmp_path / "repo"
        root.mkdir(parents=True, exist_ok=True)

        cache_path = root / ".planning" / "runtime" / "nonce_cache.jsonl"
        assert not cache_path.exists()

        # __init__ calls _load_nonces — should not crash
        server = self._make_server(root)
        assert len(server.nonce_seen) == 0

    def test_load_nonces_handles_corrupt_file(self, tmp_path: Path) -> None:
        """_load_nonces gracefully skips malformed JSONL lines."""
        from unittest.mock import patch

        root = tmp_path / "repo"
        root.mkdir(parents=True, exist_ok=True)
        fake_now = 1_700_000_000.0

        runtime_dir = root / ".planning" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        cache_path = runtime_dir / "nonce_cache.jsonl"

        content = "\n".join([
            json.dumps({"nonce": "good_one", "ts": fake_now - 10}),
            "{this is not valid json",
            json.dumps({"nonce": "", "ts": fake_now - 5}),         # empty nonce
            json.dumps({"ts": fake_now - 5}),                      # missing nonce key
            json.dumps({"nonce": "also_good", "ts": fake_now - 20}),
            "",                                                      # blank line
            "null",                                                  # valid JSON but wrong type
        ]) + "\n"
        cache_path.write_text(content, encoding="utf-8")

        with patch("time.time", return_value=fake_now):
            server = self._make_server(root)

        assert "good_one" in server.nonce_seen
        assert "also_good" in server.nonce_seen
        assert len(server.nonce_seen) == 2


# ---------------------------------------------------------------------------
# _parse_bool utility
# ---------------------------------------------------------------------------


def test_parse_bool_true_values() -> None:
    """_parse_bool returns True for truthy inputs."""
    assert mobile_api._parse_bool(True) is True
    assert mobile_api._parse_bool("true") is True
    assert mobile_api._parse_bool("True") is True
    assert mobile_api._parse_bool("TRUE") is True
    assert mobile_api._parse_bool("1") is True
    assert mobile_api._parse_bool("yes") is True
    assert mobile_api._parse_bool("  YES  ") is True


def test_parse_bool_false_values() -> None:
    """_parse_bool returns False for falsy inputs."""
    assert mobile_api._parse_bool(False) is False
    assert mobile_api._parse_bool("false") is False
    assert mobile_api._parse_bool("False") is False
    assert mobile_api._parse_bool("0") is False
    assert mobile_api._parse_bool("no") is False
    assert mobile_api._parse_bool("") is False
    assert mobile_api._parse_bool("random") is False


def test_parse_bool_non_string_non_bool() -> None:
    """_parse_bool casts non-string/non-bool via bool()."""
    assert mobile_api._parse_bool(1) is True
    assert mobile_api._parse_bool(0) is False
    assert mobile_api._parse_bool(42) is True
    assert mobile_api._parse_bool(None) is False
    assert mobile_api._parse_bool([]) is False
    assert mobile_api._parse_bool([1]) is True


# ---------------------------------------------------------------------------
# Security Fix 2: /processes requires authentication
# ---------------------------------------------------------------------------


def test_processes_endpoint_requires_auth(mobile_server) -> None:
    """GET /processes without auth should return 401."""
    code, _ = http_request("GET", f"{mobile_server.base_url}/processes")
    assert code == 401


def test_processes_endpoint_rejects_invalid_bearer(mobile_server) -> None:
    """GET /processes with wrong bearer token should return 401."""
    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    headers["Authorization"] = "Bearer wrong-token"
    code, _ = http_request("GET", f"{mobile_server.base_url}/processes", headers=headers)
    assert code == 401


# ---------------------------------------------------------------------------
# Security Fix 4: master password via env var (not CLI arg)
# ---------------------------------------------------------------------------


def test_voice_command_subprocess_does_not_pass_master_password_as_cli_arg(mobile_server) -> None:
    """The subprocess fallback path should NOT put master_password in cmd args."""
    from unittest.mock import patch, MagicMock

    captured_cmds: list[list[str]] = []
    captured_envs: list[dict[str, str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        captured_envs.append(dict(kwargs.get("env", {})))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "intent=noop\nreason=test\nstatus_code=ok\n"
        result.stderr = ""
        return result

    # Force the subprocess path by making the in-process import fail
    with patch("jarvis_engine.mobile_api.subprocess.run", fake_run), \
         patch.dict("sys.modules", {"jarvis_engine.main": None}):
        handler = MobileIngestHandler.__new__(MobileIngestHandler)
        handler.server = mobile_server.server

        payload = {
            "text": "test command",
            "execute": False,
            "approve_privileged": False,
            "speak": False,
            "master_password": "SuperSecret123!",
        }
        result = handler._run_voice_command(payload)

    if captured_cmds:
        # The --master-password flag should NOT appear in the command
        cmd = captured_cmds[0]
        assert "--master-password" not in cmd
        assert "SuperSecret123!" not in cmd
        # But the env var SHOULD be set
        env = captured_envs[0]
        assert env.get("JARVIS_MASTER_PASSWORD") == "SuperSecret123!"


# ---------------------------------------------------------------------------
# Security Fix 5: master password rate limiting
# ---------------------------------------------------------------------------


def test_master_password_rate_limiter_blocks_after_max_attempts(mobile_server) -> None:
    """After 5 master password attempts, subsequent requests should be rate-limited."""
    import unittest.mock as _mock

    write_owner_guard(mobile_server.root, enabled=True, owner_user_id="conner")
    set_master_password(mobile_server.root, "CorrectPassword123!")

    # Clear any existing rate state
    mobile_server.server._master_pw_attempts.clear()

    # Temporarily lower the limit to 3 for testing
    with _mock.patch.object(mobile_api, "_MASTER_PW_RATE_LIMIT_MAX", 3):
        for i in range(5):
            payload = {
                "source": "user",
                "kind": "semantic",
                "task_id": f"rate-limit-{i}",
                "content": "rate limit test",
            }
            raw = json.dumps(payload).encode("utf-8")
            headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
            headers["X-Jarvis-Device-Id"] = f"untrusted_device_{i}"
            headers["X-Jarvis-Master-Password"] = "CorrectPassword123!"
            code, body = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)

            if code == 429:
                resp = json.loads(body.decode("utf-8"))
                assert "master password" in resp["error"].lower()
                break
        else:
            # If we didn't break, the rate limiter didn't fire
            pytest.fail("Rate limiter did not fire after max attempts")


def test_master_password_rate_limiter_allows_normal_usage(mobile_server) -> None:
    """A single master password attempt should not be rate-limited."""
    write_owner_guard(mobile_server.root, enabled=True, owner_user_id="conner")
    set_master_password(mobile_server.root, "CorrectPassword123!")

    # Clear any existing rate state
    mobile_server.server._master_pw_attempts.clear()

    payload = {
        "source": "user",
        "kind": "semantic",
        "task_id": "rate-limit-ok",
        "content": "should work",
    }
    raw = json.dumps(payload).encode("utf-8")
    headers = signed_headers(raw, mobile_server.auth_token, mobile_server.signing_key)
    headers["X-Jarvis-Device-Id"] = "new_device_rate_test"
    headers["X-Jarvis-Master-Password"] = "CorrectPassword123!"
    code, _ = http_request("POST", f"{mobile_server.base_url}/ingest", raw, headers)
    assert code == 201


# ---------------------------------------------------------------------------
# /intelligence/growth endpoint
# ---------------------------------------------------------------------------


def test_intelligence_growth_requires_auth(mobile_server) -> None:
    """GET /intelligence/growth without auth should return 401."""
    code, _ = http_request("GET", f"{mobile_server.base_url}/intelligence/growth")
    assert code == 401


def test_intelligence_growth_returns_metrics_structure(mobile_server) -> None:
    """GET /intelligence/growth with auth returns expected JSON structure."""
    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/intelligence/growth", headers=headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert "metrics" in resp
    m = resp["metrics"]
    # Verify all expected keys are present
    assert "facts_total" in m
    assert "facts_last_7d" in m
    assert "corrections_applied" in m
    assert "corrections_last_7d" in m
    assert "consolidations_run" in m
    assert "entities_merged" in m
    assert "kg_nodes" in m
    assert "kg_edges" in m
    assert "memory_records" in m
    assert "branches" in m
    assert "growth_trend" in m
    assert "last_self_test_score" in m
    # Verify types
    assert isinstance(m["facts_total"], int)
    assert isinstance(m["kg_nodes"], int)
    assert isinstance(m["kg_edges"], int)
    assert isinstance(m["memory_records"], int)
    assert isinstance(m["branches"], dict)
    assert m["growth_trend"] in ("increasing", "stable", "declining")
    assert isinstance(m["last_self_test_score"], (int, float))


def test_intelligence_growth_reads_self_test_score(mobile_server) -> None:
    """GET /intelligence/growth reads self-test score from history file."""
    runtime_dir = mobile_server.root / ".planning" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    history_path = runtime_dir / "self_test_history.jsonl"
    record = json.dumps({
        "average_score": 0.78,
        "timestamp": "2026-02-25T12:00:00Z",
        "below_threshold": False,
    })
    history_path.write_text(record + "\n", encoding="utf-8")

    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/intelligence/growth", headers=headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    assert resp["metrics"]["last_self_test_score"] == 0.78


def test_intelligence_growth_reads_kg_history(mobile_server) -> None:
    """GET /intelligence/growth reads KG metrics from kg_metrics.jsonl."""
    runtime_dir = mobile_server.root / ".planning" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    kg_path = runtime_dir / "kg_metrics.jsonl"
    record = json.dumps({
        "ts": "2026-02-25T12:00:00Z",
        "node_count": 142,
        "edge_count": 312,
        "branch_counts": {"health": 45, "finance": 32, "coding": 65},
        "cross_branch_edges": 12,
        "avg_confidence": 0.82,
        "locked_facts": 7,
    })
    kg_path.write_text(record + "\n", encoding="utf-8")

    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/intelligence/growth", headers=headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    m = resp["metrics"]
    assert m["kg_nodes"] == 142
    assert m["kg_edges"] == 312
    assert m["facts_total"] == 142
    assert m["branches"]["health"] == 45
    assert m["branches"]["finance"] == 32
    assert m["branches"]["coding"] == 65


def test_intelligence_growth_handles_missing_data_gracefully(mobile_server) -> None:
    """GET /intelligence/growth returns defaults when no data files exist."""
    # Ensure no data files exist
    runtime_dir = mobile_server.root / ".planning" / "runtime"
    for f in ["kg_metrics.jsonl", "self_test_history.jsonl"]:
        p = runtime_dir / f
        if p.exists():
            p.unlink()

    headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
    code, body = http_request("GET", f"{mobile_server.base_url}/intelligence/growth", headers=headers)
    assert code == 200
    resp = json.loads(body.decode("utf-8"))
    assert resp["ok"] is True
    m = resp["metrics"]
    # Should return defaults without errors
    assert m["facts_total"] == 0
    assert m["kg_nodes"] == 0
    assert m["kg_edges"] == 0
    assert m["memory_records"] == 0
    assert m["last_self_test_score"] == 0.0
    assert m["growth_trend"] in ("stable", "increasing", "declining")
