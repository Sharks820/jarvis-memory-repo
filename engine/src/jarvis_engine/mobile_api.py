from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jarvis_engine.ingest import IngestionPipeline
from jarvis_engine.memory_store import MemoryStore


ALLOWED_SOURCES = {"user", "claude", "opus", "gemini", "task_outcome"}
ALLOWED_KINDS = {"episodic", "semantic", "procedural"}
REPLAY_WINDOW_SECONDS = 300.0
MAX_NONCES = 100_000


class MobileIngestServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        *,
        auth_token: str,
        signing_key: str,
        pipeline: IngestionPipeline,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.auth_token = auth_token
        self.signing_key = signing_key
        self.pipeline = pipeline
        self.nonce_seen: dict[str, float] = {}
        self.nonce_lock = threading.Lock()
        self.next_nonce_cleanup_ts = 0.0
        self.nonce_cleanup_interval_s = 30.0


class MobileIngestHandler(BaseHTTPRequestHandler):
    server_version = "JarvisMobileAPI/0.1"

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _unauthorized(self, message: str) -> None:
        self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": message})

    def _cleanup_nonces(self, now: float, *, force: bool = False) -> None:
        interval = float(getattr(self.server, "nonce_cleanup_interval_s", 30.0))  # type: ignore[attr-defined]
        next_cleanup = float(getattr(self.server, "next_nonce_cleanup_ts", 0.0))  # type: ignore[attr-defined]
        if not force and now < next_cleanup:
            return
        nonce_seen: dict[str, float] = self.server.nonce_seen  # type: ignore[attr-defined]
        cutoff = now - REPLAY_WINDOW_SECONDS
        stale = [key for key, seen_ts in nonce_seen.items() if seen_ts < cutoff]
        for key in stale:
            nonce_seen.pop(key, None)
        self.server.next_nonce_cleanup_ts = now + interval  # type: ignore[attr-defined]

    def _validate_auth(self, body: bytes) -> bool:
        auth = self.headers.get("Authorization", "")
        expected_auth = f"Bearer {self.server.auth_token}"  # type: ignore[attr-defined]
        if not hmac.compare_digest(auth, expected_auth):
            self._unauthorized("Invalid bearer token.")
            return False

        ts_raw = self.headers.get("X-Jarvis-Timestamp", "").strip()
        nonce = self.headers.get("X-Jarvis-Nonce", "").strip()
        if not ts_raw or not nonce:
            self._unauthorized("Missing replay-protection headers.")
            return False
        if len(nonce) < 8 or len(nonce) > 128 or (not nonce.isascii()):
            self._unauthorized("Invalid nonce.")
            return False
        try:
            ts = float(ts_raw)
        except ValueError:
            self._unauthorized("Invalid timestamp.")
            return False
        now = time.time()
        if abs(now - ts) > REPLAY_WINDOW_SECONDS:
            self._unauthorized("Expired timestamp.")
            return False

        signature = self.headers.get("X-Jarvis-Signature", "").strip().lower()
        signing_material = ts_raw.encode("utf-8") + b"\n" + nonce.encode("utf-8") + b"\n" + body
        expected_sig = hmac.new(
            self.server.signing_key.encode("utf-8"),  # type: ignore[attr-defined]
            signing_material,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            self._unauthorized("Invalid request signature.")
            return False

        with self.server.nonce_lock:  # type: ignore[attr-defined]
            nonce_seen: dict[str, float] = self.server.nonce_seen  # type: ignore[attr-defined]
            self._cleanup_nonces(now)
            if len(nonce_seen) >= MAX_NONCES:
                # Last-resort cleanup pass if we are at capacity.
                self._cleanup_nonces(now, force=True)
            if len(nonce_seen) >= MAX_NONCES:
                self._unauthorized("Replay cache saturated.")
                return False
            if nonce in nonce_seen:
                self._unauthorized("Replay detected.")
                return False
            nonce_seen[nonce] = now

        return True

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "status": "healthy"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/ingest":
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return

        raw_content_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_content_length)
        except (TypeError, ValueError):
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Invalid content length."},
            )
            return

        if content_length <= 0 or content_length > 50_000:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Invalid content length."},
            )
            return

        self.connection.settimeout(15.0)
        body = self.rfile.read(content_length)
        if not self._validate_auth(body):
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid UTF-8 body."})
            return
        except json.JSONDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON."})
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

        rec = self.server.pipeline.ingest(  # type: ignore[attr-defined]
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

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep mobile ingestion logs out of stdout unless explicitly logged via memory store.
        return


def run_mobile_server(host: str, port: int, auth_token: str, signing_key: str, repo_root: Path) -> None:
    store = MemoryStore(repo_root)
    pipeline = IngestionPipeline(store)
    server = MobileIngestServer(
        (host, port),
        MobileIngestHandler,
        auth_token=auth_token,
        signing_key=signing_key,
        pipeline=pipeline,
    )
    print(f"mobile_api_listening=http://{host}:{port}")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("warning=mobile_api_non_loopback_without_tls")
    print("endpoints: GET /health, POST /ingest")
    server.serve_forever()
