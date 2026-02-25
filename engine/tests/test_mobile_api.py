from __future__ import annotations

import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from conftest import http_request, signed_headers
from jarvis_engine import mobile_api
from jarvis_engine.owner_guard import set_master_password, trust_mobile_device, write_owner_guard


def test_health_endpoint(mobile_server) -> None:
    code, body = http_request("GET", f"{mobile_server.base_url}/health")
    assert code == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["status"] == "healthy"


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
    mobile_server.server._api_rate_attempts.clear()

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
    with patch("jarvis_engine.gateway.models.ModelGateway.complete", _mock_complete):
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

    # Temporarily lower the limit for testing
    with _mock.patch.object(mobile_api, "_API_RATE_LIMIT_EXPENSIVE", 2):
        # Clear any existing rate state for our IP
        mobile_server.server._api_rate_attempts.clear()

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
    """GET /processes returns service statuses without auth."""
    code, body = http_request("GET", f"{mobile_server.base_url}/processes")
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
