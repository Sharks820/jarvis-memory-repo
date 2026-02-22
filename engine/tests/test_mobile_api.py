from __future__ import annotations

import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from conftest import http_request, signed_headers


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
    assert len(resp["record_id"]) == 16

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
