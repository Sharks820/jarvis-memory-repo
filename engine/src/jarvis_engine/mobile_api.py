from __future__ import annotations

import contextlib
import gzip as _gzip_mod
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime
from jarvis_engine._compat import UTC
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any

from jarvis_engine._shared import atomic_write_json as _atomic_write_json
from jarvis_engine.ingest import IngestionPipeline
from jarvis_engine.intelligence_dashboard import build_intelligence_dashboard
from jarvis_engine.memory_store import MemoryStore
from jarvis_engine.owner_guard import read_owner_guard, trust_mobile_device, verify_master_password
from jarvis_engine.runtime_control import read_control_state, reset_control_state, write_control_state


logger = logging.getLogger(__name__)

ALLOWED_SOURCES = {"user", "claude", "opus", "gemini", "task_outcome"}
ALLOWED_KINDS = {"episodic", "semantic", "procedural"}
REPLAY_WINDOW_SECONDS = 120.0
MAX_NONCES = 100_000
MAX_AUTH_BODY_SIZE = 2_000_000  # 2 MB (matches sync/push max_content_length)

# Lock for serializing repo_root monkeypatch in multi-threaded HTTP server
_repo_root_lock = threading.Lock()

# CORS whitelist: only allow localhost/loopback origins and file:// protocol.
# LAN IPs are added dynamically at server startup via _build_cors_whitelist().
_CORS_ALLOWED_ORIGIN_PATTERNS = [
    re.compile(r"^https?://localhost(:\d+)?$"),
    re.compile(r"^https?://127\.0\.0\.1(:\d+)?$"),
    re.compile(r"^https?://\[::1\](:\d+)?$"),
    re.compile(r"^file:///[A-Za-z]:/"),  # Only local file:// URIs with drive letter
]

# Bootstrap rate-limiter: max 5 failed attempts per IP within 60s window.
_BOOTSTRAP_RATE_LIMIT_WINDOW = 60.0
_BOOTSTRAP_RATE_LIMIT_MAX = 5

# Master password rate-limiter: max 5 attempts per IP within 60s window.
_MASTER_PW_RATE_LIMIT_WINDOW = 60.0
_MASTER_PW_RATE_LIMIT_MAX = 5

# Global API rate-limiter: per-IP sliding window.
_API_RATE_LIMIT_WINDOW = 60.0        # 60-second window
_API_RATE_LIMIT_NORMAL = 120          # 120 req/min for standard endpoints
_API_RATE_LIMIT_EXPENSIVE = 10        # 10 req/min for /command, /self-heal
_EXPENSIVE_PATHS = {"/command", "/self-heal"}


def _detect_lan_ips() -> list[str]:
    """Detect local LAN IP addresses for SAN entries."""
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            if lan_ip and lan_ip not in ips:
                ips.append(lan_ip)
    except OSError:
        pass
    # Always include loopback
    if "127.0.0.1" not in ips:
        ips.append("127.0.0.1")
    return ips


def _build_san_string(extra_ips: list[str] | None = None) -> str:
    """Build a subjectAltName string with DNS and IP entries.

    Includes localhost, 127.0.0.1, and any detected LAN IPs plus
    any extra IPs passed explicitly.
    """
    entries: list[str] = ["DNS:localhost", "IP:127.0.0.1"]
    seen_ips = {"127.0.0.1"}
    lan_ips = _detect_lan_ips()
    for ip in lan_ips:
        if ip not in seen_ips:
            entries.append(f"IP:{ip}")
            seen_ips.add(ip)
    if extra_ips:
        for ip in extra_ips:
            if ip not in seen_ips:
                entries.append(f"IP:{ip}")
                seen_ips.add(ip)
    return ",".join(entries)


def _get_cert_fingerprint(cert_path: str) -> str | None:
    """Return the SHA-256 fingerprint of a PEM certificate file.

    Returns the fingerprint as a colon-separated hex string (e.g.
    ``AA:BB:CC:...``) or ``None`` on failure.
    """
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-fingerprint", "-sha256"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output: "sha256 Fingerprint=AA:BB:CC:..."
            for line in result.stdout.strip().splitlines():
                if "=" in line:
                    return line.split("=", 1)[1].strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass
    # Fallback: compute from PEM bytes directly
    try:
        import base64 as _b64
        pem_text = Path(cert_path).read_text(encoding="utf-8")
        # Extract DER bytes from PEM
        der_lines: list[str] = []
        in_cert = False
        for line in pem_text.splitlines():
            if "BEGIN CERTIFICATE" in line:
                in_cert = True
                continue
            if "END CERTIFICATE" in line:
                break
            if in_cert:
                der_lines.append(line.strip())
        if der_lines:
            der_bytes = _b64.b64decode("".join(der_lines))
            digest = hashlib.sha256(der_bytes).hexdigest().upper()
            return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
    except Exception:
        pass
    return None


def _ensure_tls_cert(security_dir: Path, *, extra_ips: list[str] | None = None) -> tuple[str | None, str | None]:
    """Generate a self-signed TLS certificate + key if they don't exist.

    Uses ``openssl`` via subprocess.  Returns ``(cert_path, key_path)`` on
    success or ``(None, None)`` when ``openssl`` is unavailable or the
    generation fails.  Existing certs are reused without regeneration.

    The certificate includes Subject Alternative Name (SAN) entries for
    localhost, 127.0.0.1, and any detected LAN IP addresses.  This fixes
    hostname verification failures when Android connects by IP.

    The cert and key files are stored inside *security_dir* which is
    expected to be gitignored (e.g. ``.planning/security/``).
    """
    security_dir.mkdir(parents=True, exist_ok=True)
    cert_path = security_dir / "tls_cert.pem"
    key_path = security_dir / "tls_key.pem"

    if cert_path.exists() and key_path.exists():
        return str(cert_path), str(key_path)

    # Build SAN extension config
    san_string = _build_san_string(extra_ips)
    ext_file = security_dir / "tls_ext.cnf"
    ext_content = (
        "[req]\n"
        "distinguished_name = req_distinguished_name\n"
        "x509_extensions = v3_req\n"
        "prompt = no\n"
        "\n"
        "[req_distinguished_name]\n"
        "CN = jarvis-local\n"
        "\n"
        "[v3_req]\n"
        "keyUsage = digitalSignature, keyEncipherment\n"
        "extendedKeyUsage = serverAuth\n"
        f"subjectAltName = {san_string}\n"
    )

    # Attempt to generate using openssl
    try:
        ext_file.write_text(ext_content, encoding="utf-8")
        subprocess.run(
            [
                "openssl", "req",
                "-x509",
                "-newkey", "rsa:2048",
                "-keyout", str(key_path),
                "-out", str(cert_path),
                "-days", "365",
                "-nodes",
                "-config", str(ext_file),
                "-extensions", "v3_req",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("TLS cert generation failed (openssl not available?): %s", exc)
        # Clean up partial files
        for p in (cert_path, key_path, ext_file):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        return None, None
    finally:
        # Clean up the temporary extension file
        try:
            if ext_file.exists():
                ext_file.unlink()
        except OSError:
            pass

    if cert_path.exists() and key_path.exists():
        logger.info("Generated self-signed TLS certificate with SAN=%s: %s", san_string, cert_path)
        return str(cert_path), str(key_path)

    return None, None


def _parse_bool(value: Any) -> bool:
    """Safely parse a boolean from JSON payload (handles string "false"/"true")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


class MobileIngestServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        *,
        auth_token: str,
        signing_key: str,
        pipeline: IngestionPipeline,
        repo_root: Path,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.auth_token = auth_token
        self.signing_key = signing_key
        self.pipeline = pipeline
        self.repo_root = repo_root
        self.tls_active = False
        self._sync_engine: Any = None
        self._sync_transport: Any = None
        self._sync_init_attempted = False
        self._sync_init_lock = threading.Lock()
        self._memory_engine: Any = None
        self._memory_engine_init_lock = threading.Lock()
        self.nonce_seen: dict[str, float] = {}
        self.nonce_lock = threading.RLock()
        self.next_nonce_cleanup_ts = 0.0
        self.nonce_cleanup_interval_s = 30.0
        self._nonce_cache_path = repo_root / ".planning" / "runtime" / "nonce_cache.jsonl"
        self._load_nonces()
        # Bootstrap rate-limiter: {ip: [timestamp, ...]}
        self._bootstrap_attempts: dict[str, list[float]] = {}
        self._bootstrap_rate_lock = threading.Lock()
        # Dynamic CORS origins (populated at startup with LAN IP)
        self._extra_cors_origins: list[re.Pattern[str]] = []
        # Global API rate-limiter: separate counters per tier to prevent
        # widget polling (~33 req/min) from blocking /command (10 req/min limit).
        self._api_rate_normal: dict[str, list[float]] = {}
        self._api_rate_expensive: dict[str, list[float]] = {}
        self._api_rate_lock = threading.Lock()
        # Master password rate-limiter: {ip: [timestamp, ...]}
        self._master_pw_attempts: dict[str, list[float]] = {}
        self._master_pw_rate_lock = threading.Lock()

    @staticmethod
    def _prune_rate_dict(d: dict[str, list[float]], max_keys: int = 5000) -> None:
        """Remove the oldest half of entries when the dict exceeds max_keys.

        Prevents unbounded memory growth from unique IPs over time.
        Each value is a list of timestamps; the 'oldest' entry is determined
        by the maximum timestamp in each list (most recent activity).
        """
        if len(d) <= max_keys:
            return
        # Sort IPs by their most recent attempt timestamp, ascending
        by_recency = sorted(d.keys(), key=lambda ip: max(d[ip]) if d[ip] else 0.0)
        to_remove = len(d) // 2
        for ip in by_recency[:to_remove]:
            del d[ip]

    def check_bootstrap_rate(self, client_ip: str) -> bool:
        """Return True if this IP is rate-limited for bootstrap attempts."""
        now = time.time()
        with self._bootstrap_rate_lock:
            self._prune_rate_dict(self._bootstrap_attempts)
            attempts = self._bootstrap_attempts.get(client_ip, [])
            # Prune attempts outside the sliding window
            cutoff = now - _BOOTSTRAP_RATE_LIMIT_WINDOW
            attempts = [ts for ts in attempts if ts > cutoff]
            self._bootstrap_attempts[client_ip] = attempts
            return len(attempts) >= _BOOTSTRAP_RATE_LIMIT_MAX

    def record_bootstrap_attempt(self, client_ip: str) -> None:
        """Record a failed bootstrap attempt for rate limiting."""
        now = time.time()
        with self._bootstrap_rate_lock:
            attempts = self._bootstrap_attempts.get(client_ip, [])
            cutoff = now - _BOOTSTRAP_RATE_LIMIT_WINDOW
            attempts = [ts for ts in attempts if ts > cutoff]
            attempts.append(now)
            self._bootstrap_attempts[client_ip] = attempts

    def check_master_pw_rate(self, client_ip: str) -> bool:
        """Return True if this IP is rate-limited for master password attempts."""
        now = time.time()
        with self._master_pw_rate_lock:
            self._prune_rate_dict(self._master_pw_attempts)
            attempts = self._master_pw_attempts.get(client_ip, [])
            cutoff = now - _MASTER_PW_RATE_LIMIT_WINDOW
            attempts = [ts for ts in attempts if ts > cutoff]
            self._master_pw_attempts[client_ip] = attempts
            return len(attempts) >= _MASTER_PW_RATE_LIMIT_MAX

    def record_master_pw_attempt(self, client_ip: str) -> None:
        """Record a master password attempt for rate limiting."""
        now = time.time()
        with self._master_pw_rate_lock:
            attempts = self._master_pw_attempts.get(client_ip, [])
            cutoff = now - _MASTER_PW_RATE_LIMIT_WINDOW
            attempts = [ts for ts in attempts if ts > cutoff]
            attempts.append(now)
            self._master_pw_attempts[client_ip] = attempts

    def check_api_rate(self, client_ip: str, path: str) -> bool:
        """Return True if this IP exceeds the API rate limit for the given path.

        Uses **separate** counters for expensive paths (/command, /self-heal)
        vs normal paths so that widget polling doesn't consume the expensive
        tier's budget.

        Only records the request if it is NOT rate-limited, so rejected
        requests do not consume future budget (matches bootstrap/master_pw
        pattern).
        """
        is_expensive = path in _EXPENSIVE_PATHS
        limit = _API_RATE_LIMIT_EXPENSIVE if is_expensive else _API_RATE_LIMIT_NORMAL
        bucket = self._api_rate_expensive if is_expensive else self._api_rate_normal
        now = time.time()
        with self._api_rate_lock:
            self._prune_rate_dict(bucket)
            attempts = bucket.get(client_ip, [])
            cutoff = now - _API_RATE_LIMIT_WINDOW
            attempts = [ts for ts in attempts if ts > cutoff]
            if len(attempts) >= limit:
                bucket[client_ip] = attempts
                return True
            attempts.append(now)
            bucket[client_ip] = attempts
            return False

    def is_cors_origin_allowed(self, origin: str) -> bool:
        """Check if the given Origin is in the CORS whitelist."""
        if not origin:
            return False
        for pattern in _CORS_ALLOWED_ORIGIN_PATTERNS:
            if pattern.match(origin):
                return True
        for pattern in self._extra_cors_origins:
            if pattern.match(origin):
                return True
        return False

    def _load_nonces(self) -> None:
        """Restore nonces from disk for replay protection across restarts."""
        try:
            if not self._nonce_cache_path.exists():
                return
            now = time.time()
            cutoff = now - REPLAY_WINDOW_SECONDS
            with open(self._nonce_cache_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        nonce = str(entry.get("nonce", ""))
                        ts = float(entry.get("ts", 0.0))
                        if nonce and ts >= cutoff:
                            self.nonce_seen[nonce] = ts
                    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                        continue
        except OSError:
            logger.warning("Failed to load nonce cache from disk")

    def _persist_nonces(self) -> None:
        """Persist current valid nonces to disk using atomic write pattern."""
        # Take snapshot under lock to avoid iterating a dict mutated by other threads
        with self.nonce_lock:
            now = time.time()
            cutoff = now - REPLAY_WINDOW_SECONDS
            snapshot = {k: v for k, v in self.nonce_seen.items() if v >= cutoff}
        # Write snapshot to file (outside lock to avoid holding it during I/O)
        tmp = self._nonce_cache_path.with_suffix(".jsonl.tmp")
        try:
            self._nonce_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                for nonce, ts in snapshot.items():
                    f.write(json.dumps({"nonce": nonce, "ts": ts}, ensure_ascii=True) + "\n")
            os.replace(str(tmp), str(self._nonce_cache_path))
        except OSError:
            logger.warning("Failed to persist nonce cache to disk")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def ensure_sync_engine(self) -> Any:
        """Lazy-initialize sync engine when DB becomes available.

        The sync engine uses a dedicated SQLite connection with WAL mode
        and a threading.Lock to serialize all database operations, ensuring
        thread-safe access from the multi-threaded HTTP server.
        """
        if self._sync_engine is not None:
            return self._sync_engine
        if self._sync_init_attempted:
            return None
        with self._sync_init_lock:
            if self._sync_engine is not None:
                return self._sync_engine
            db_path = self.repo_root / ".planning" / "brain" / "jarvis_memory.db"
            if not db_path.exists():
                return None
            try:
                from jarvis_engine.sync.changelog import install_changelog_triggers
                from jarvis_engine.sync.engine import SyncEngine
                from jarvis_engine.sync.transport import SyncTransport

                import sqlite3 as _sqlite3

                sync_db = _sqlite3.connect(str(db_path), check_same_thread=False)
                try:
                    sync_db.execute("PRAGMA journal_mode=WAL")
                    sync_db.execute("PRAGMA busy_timeout=5000")
                    sync_lock = threading.Lock()
                    install_changelog_triggers(sync_db, device_id="desktop")
                    self._sync_engine = SyncEngine(sync_db, sync_lock, device_id="desktop")
                except Exception:
                    sync_db.close()
                    raise
                if self.signing_key:
                    salt_path = self.repo_root / ".planning" / "brain" / "sync_salt.bin"
                    self._sync_transport = SyncTransport(self.signing_key, salt_path)
                    logger.info("Sync engine lazy-initialized for mobile API")
            except Exception as exc:
                logger.warning("Failed to lazy-initialize sync: %s", exc)
                self._sync_init_attempted = True  # Only prevent retry on failure
            return self._sync_engine

    def ensure_memory_engine(self) -> Any:
        """Lazy-initialize a MemoryEngine for read-only metric queries.

        Returns the MemoryEngine instance, or None if the DB doesn't exist
        or initialization fails.
        """
        if self._memory_engine is not None:
            return self._memory_engine
        with self._memory_engine_init_lock:
            if self._memory_engine is not None:
                return self._memory_engine
            db_path = self.repo_root / ".planning" / "brain" / "jarvis_memory.db"
            if not db_path.exists():
                return None
            try:
                from jarvis_engine.memory.engine import MemoryEngine
                self._memory_engine = MemoryEngine(db_path)
                logger.info("MemoryEngine lazy-initialized for mobile API metrics")
            except Exception as exc:
                logger.warning("Failed to lazy-initialize MemoryEngine: %s", exc)
            return self._memory_engine

    def ensure_embed_service(self) -> Any:
        """Lazy-initialize an EmbeddingService for self-test queries.

        Returns the EmbeddingService instance, or None on failure.
        """
        _embed = getattr(self, "_embed_service", None)
        if _embed is not None:
            return _embed
        try:
            from jarvis_engine.memory.embeddings import EmbeddingService
            self._embed_service = EmbeddingService()
            logger.info("EmbeddingService lazy-initialized for mobile API self-test")
        except Exception as exc:
            logger.warning("Failed to lazy-initialize EmbeddingService: %s", exc)
            self._embed_service = None
        return self._embed_service


class MobileIngestHandler(BaseHTTPRequestHandler):
    server_version = "JarvisMobileAPI/0.1"

    def _cors_headers(self) -> None:
        """Add CORS headers to every response for browser-based clients.

        Only whitelisted origins (localhost, 127.0.0.1, ::1, file://, and
        any configured LAN IP) are reflected.  Unknown origins receive no
        Access-Control-Allow-Origin header, effectively blocking CORS.
        """
        origin = self.headers.get("Origin", "")
        server: MobileIngestServer = self.server  # type: ignore[assignment]
        if origin and server.is_cors_origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        # If origin is not whitelisted, omit Access-Control-Allow-Origin
        # so the browser will block the cross-origin request.
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Jarvis-Timestamp, X-Jarvis-Nonce, "
            "X-Jarvis-Signature, X-Jarvis-Device-Id, X-Jarvis-Master-Password",
        )
        self.send_header("Access-Control-Max-Age", "3600")

    def do_OPTIONS(self) -> None:  # noqa: N802
        """Handle CORS preflight requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def _security_headers(self) -> None:
        """Add security headers to every response."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        accept_enc = self.headers.get("Accept-Encoding", "") if hasattr(self, "headers") and self.headers else ""
        use_gzip = "gzip" in accept_enc and len(raw) > 256
        encoded = _gzip_mod.compress(raw, compresslevel=6) if use_gzip else raw
        self.send_response(status)
        self._cors_headers()
        self._security_headers()
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_text(self, status: int, content_type: str, payload: str) -> None:
        encoded = payload.encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _quick_panel_path(self) -> Path:
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        return root / "mobile" / "quick_access.html"

    def _quick_panel_html(self) -> str:
        path = self._quick_panel_path()
        if not path.exists():
            return "<h1>Jarvis Quick Panel not found.</h1>"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return "<h1>Jarvis Quick Panel unavailable.</h1>"

    def _run_voice_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Validate required field: text (string, non-empty, <= 2000 chars)
        if "text" not in payload:
            return {"ok": False, "error": "Missing required field: text."}
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 2000:
            return {"ok": False, "error": "Invalid text command."}

        root: Path = self.server.repo_root  # type: ignore[attr-defined]

        execute = _parse_bool(payload.get("execute", False))
        approve_privileged = _parse_bool(payload.get("approve_privileged", False))
        speak = _parse_bool(payload.get("speak", False))
        voice_user = str(payload.get("voice_user", "conner")).strip() or "conner"
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", voice_user):
            return {"ok": False, "error": "Invalid voice_user."}
        voice_auth_wav = str(payload.get("voice_auth_wav", "")).strip()
        if voice_auth_wav:
            try:
                wav_resolved = Path(voice_auth_wav).resolve()
                wav_resolved.relative_to(root.resolve())
            except (ValueError, OSError):
                return {"ok": False, "error": "voice_auth_wav path outside project root."}
        master_password = str(payload.get("master_password", "")).strip()
        voice_threshold_raw = payload.get("voice_threshold", 0.82)
        try:
            voice_threshold = float(voice_threshold_raw)
        except (TypeError, ValueError):
            voice_threshold = 0.82
        voice_threshold = min(0.99, max(0.1, voice_threshold))
        # Always prefer in-process execution for speed and stdout capture.
        # Fall back to subprocess only if the in-process import fails.
        _can_import_in_process = True
        try:
            import jarvis_engine.main as _test_mod  # noqa: F401
        except ImportError:
            _can_import_in_process = False
        if _can_import_in_process:
            captured_out = io.StringIO()
            try:
                import jarvis_engine.main as main_mod

                with _repo_root_lock:
                    original_repo_root = main_mod.repo_root
                    main_mod.repo_root = lambda: root  # type: ignore[assignment]
                    try:
                        with contextlib.redirect_stdout(captured_out):
                            rc = main_mod.cmd_voice_run(
                                text=text,
                                execute=execute,
                                approve_privileged=approve_privileged,
                                speak=speak,
                                snapshot_path=root / ".planning" / "ops_snapshot.live.json",
                                actions_path=root / ".planning" / "actions.generated.json",
                                voice_user=voice_user,
                                voice_auth_wav=voice_auth_wav,
                                voice_threshold=voice_threshold,
                                master_password=master_password,
                            )
                    finally:
                        main_mod.repo_root = original_repo_root  # type: ignore[assignment]
            except Exception as exc:
                logger.error("Voice command execution failed: %s", exc)
                # Include partial stdout and error details for widget debugging
                partial_out = captured_out.getvalue().splitlines()[-20:] if captured_out.getvalue() else []
                return {
                    "ok": False,
                    "error": f"Command execution failed: {exc}",
                    "intent": "execution_error",
                    "reason": str(exc),
                    "stdout_tail": partial_out + [f"error={exc}"],
                    "stderr_tail": [],
                }
            # Parse captured stdout for intent/reason/status (same as subprocess path)
            stdout_text = captured_out.getvalue()
            stdout_lines = stdout_text.splitlines()
            intent = ""
            reason = ""
            status_code = str(rc)
            for line in stdout_lines:
                if line.startswith("intent="):
                    intent = line.split("=", 1)[1]
                elif line.startswith("reason="):
                    reason = line.split("=", 1)[1]
                elif line.startswith("status_code="):
                    status_code = line.split("=", 1)[1]
            return {
                "ok": rc == 0,
                "command_exit_code": rc,
                "intent": intent,
                "status_code": status_code,
                "reason": reason,
                "stdout_tail": stdout_lines[-20:] if stdout_lines else [],
                "stderr_tail": [],
            }

        cmd = [
            sys.executable,
            "-m",
            "jarvis_engine.main",
            "voice-run",
            "--text",
            text,
            "--voice-user",
            voice_user,
            "--voice-threshold",
            str(voice_threshold),
        ]
        if execute:
            cmd.append("--execute")
        if approve_privileged:
            cmd.append("--approve-privileged")
        if speak:
            cmd.append("--speak")
        if voice_auth_wav:
            cmd.extend(["--voice-auth-wav", voice_auth_wav])

        engine_dir = root / "engine"
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        if master_password:
            env["JARVIS_MASTER_PASSWORD"] = master_password
        try:
            result = subprocess.run(
                cmd,
                cwd=str(engine_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.error("Voice subprocess failed: %s", exc)
            return {"ok": False, "error": "Command execution failed."}

        stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        stderr_lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        intent = ""
        reason = ""
        status_code = ""
        for line in stdout_lines:
            if line.startswith("intent="):
                intent = line.split("=", 1)[1].strip()
            elif line.startswith("reason="):
                reason = line.split("=", 1)[1].strip()
            elif line.startswith("status_code="):
                status_code = line.split("=", 1)[1].strip()

        return {
            "ok": result.returncode == 0,
            "command_exit_code": result.returncode,
            "intent": intent,
            "status_code": status_code,
            "reason": reason,
            "stdout_tail": stdout_lines[-20:],
            "stderr_tail": stderr_lines[-20:],
        }

    def _run_main_cli(self, args: list[str], *, timeout_s: int = 240) -> dict[str, Any]:
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        engine_dir = root / "engine"
        if not engine_dir.exists():
            return {"ok": False, "error": "Engine directory not found.", "command_exit_code": 2, "stdout_tail": [], "stderr_tail": []}
        cmd = [sys.executable, "-m", "jarvis_engine.main", *args]
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        try:
            result = subprocess.run(
                cmd,
                cwd=str(engine_dir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(30, timeout_s),
            )
        except subprocess.TimeoutExpired as exc:
            # TimeoutExpired may carry partial stdout/stderr captured before the timeout
            stderr_partial = ""
            stdout_partial = ""
            if exc.stderr:
                stderr_partial = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", errors="replace")
            if exc.stdout:
                stdout_partial = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", errors="replace")
            logger.error("CLI subprocess timed out after %ss: %s", timeout_s, exc)
            stderr_lines = [line.strip() for line in stderr_partial.splitlines() if line.strip()]
            stdout_lines = [line.strip() for line in stdout_partial.splitlines() if line.strip()]
            return {
                "ok": False,
                "error": f"Command timed out after {timeout_s}s.",
                "command_exit_code": 2,
                "stdout_tail": stdout_lines[-20:],
                "stderr_tail": stderr_lines[-20:],
            }
        except OSError as exc:
            logger.error("CLI subprocess failed: %s", exc)
            return {"ok": False, "error": "Command execution failed.", "command_exit_code": 2, "stdout_tail": [], "stderr_tail": [str(exc)]}
        stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        stderr_lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        return {
            "ok": result.returncode == 0,
            "command_exit_code": result.returncode,
            "stdout_tail": stdout_lines[-20:],
            "stderr_tail": stderr_lines[-20:],
        }

    def _unauthorized(self, message: str) -> None:
        self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": message})

    def _read_json_body(self, *, max_content_length: int) -> tuple[dict[str, Any] | None, bytes | None]:
        raw_content_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_content_length)
        except (TypeError, ValueError):
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Invalid content length."},
            )
            return None, None

        if content_length <= 0 or content_length > max_content_length:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Invalid content length."},
            )
            return None, None

        try:
            self.connection.settimeout(15.0)
        except OSError:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Connection closed."},
            )
            return None, None
        body = self.rfile.read(content_length)
        if not self._validate_auth(body):
            return None, None

        try:
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid UTF-8 body."})
            return None, None
        except json.JSONDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON."})
            return None, None
        if not isinstance(payload, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON payload."})
            return None, None
        return payload, body

    def _read_json_body_noauth(self, *, max_content_length: int) -> tuple[dict[str, Any] | None, bytes | None]:
        raw_content_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_content_length)
        except (TypeError, ValueError):
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid content length."})
            return None, None
        if content_length < 0 or content_length > max_content_length:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid content length."})
            return None, None
        try:
            self.connection.settimeout(15.0)
        except OSError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Connection closed."})
            return None, None
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid UTF-8 body."})
            return None, None
        except json.JSONDecodeError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON."})
            return None, None
        if not isinstance(payload, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON payload."})
            return None, None
        return payload, body

    def _client_is_private_or_loopback(self) -> bool:
        raw_ip = str(self.client_address[0]).strip()
        try:
            ip = ip_address(raw_ip)
            return bool(ip.is_loopback or ip.is_private or ip.is_link_local)
        except ValueError:
            return False

    def _gaming_state_path(self) -> Path:
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        root_resolved = root.resolve()
        path = root_resolved / ".planning" / "runtime" / "gaming_mode.json"
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise PermissionError("Unsafe gaming state path resolution.") from exc
        return path

    def _read_gaming_state(self) -> dict[str, Any]:
        try:
            path = self._gaming_state_path()
        except PermissionError:
            return {"enabled": False, "auto_detect": False, "reason": "", "updated_utc": ""}
        if not path.exists():
            return {"enabled": False, "auto_detect": False, "reason": "", "updated_utc": ""}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"enabled": False, "auto_detect": False, "reason": "", "updated_utc": ""}
        if not isinstance(raw, dict):
            return {"enabled": False, "auto_detect": False, "reason": "", "updated_utc": ""}
        return {
            "enabled": bool(raw.get("enabled", False)),
            "auto_detect": bool(raw.get("auto_detect", False)),
            "reason": str(raw.get("reason", "")).strip()[:200],
            "updated_utc": str(raw.get("updated_utc", "")),
        }

    def _write_gaming_state(
        self,
        *,
        enabled: bool | None = None,
        auto_detect: bool | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        state = self._read_gaming_state()
        if enabled is not None:
            state["enabled"] = enabled
        if auto_detect is not None:
            state["auto_detect"] = auto_detect
        if reason.strip():
            state["reason"] = reason.strip()[:200]
        state["updated_utc"] = datetime.now(UTC).isoformat()
        path = self._gaming_state_path()
        _atomic_write_json(path, state)
        return state

    def _settings_payload(self) -> dict[str, Any]:
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        control = read_control_state(root)
        gaming = self._read_gaming_state()
        owner_guard = read_owner_guard(root)
        return {
            "runtime_control": control,
            "gaming_mode": gaming,
            "owner_guard": {
                "enabled": bool(owner_guard.get("enabled", False)),
                "owner_user_id": str(owner_guard.get("owner_user_id", "")),
                "trusted_mobile_device_count": len(owner_guard.get("trusted_mobile_devices", [])),
            },
        }

    def _cleanup_nonces(self, now: float, *, force: bool = False) -> None:
        should_persist = False
        with self.server.nonce_lock:  # type: ignore[attr-defined]
            interval = float(getattr(self.server, "nonce_cleanup_interval_s", 30.0))  # type: ignore[attr-defined]
            next_cleanup = float(getattr(self.server, "next_nonce_cleanup_ts", 0.0))  # type: ignore[attr-defined]
            if not force and now < next_cleanup:
                return
            nonce_seen: dict[str, float] = self.server.nonce_seen  # type: ignore[attr-defined]
            cutoff = now - REPLAY_WINDOW_SECONDS
            valid_nonces = {k: v for k, v in nonce_seen.items() if v >= cutoff}
            nonce_seen.clear()
            nonce_seen.update(valid_nonces)
            self.server.next_nonce_cleanup_ts = now + interval  # type: ignore[attr-defined]
            should_persist = True
        if should_persist:
            self.server._persist_nonces()  # type: ignore[attr-defined]

    def _validate_auth(self, body: bytes) -> bool:
        if len(body) > MAX_AUTH_BODY_SIZE:
            self._unauthorized("Request body too large.")
            return False
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
        if not math.isfinite(ts):
            self._unauthorized("Invalid timestamp.")
            return False
        now = time.time()
        if abs(now - ts) > REPLAY_WINDOW_SECONDS:
            self._unauthorized("Expired timestamp.")
            return False

        signature = self.headers.get("X-Jarvis-Signature", "").strip().lower()
        # Signing material format: "<timestamp>\n<nonce>\n<body_bytes>"
        # All clients (mobile, desktop widget, tests) MUST produce the same
        # byte sequence: timestamp as UTF-8 string, newline, nonce as UTF-8,
        # newline, then the raw request body bytes (no trailing newline).
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
            # Temporarily record the nonce to block concurrent replays.
            # If a downstream check (owner_guard) fails, we remove it so
            # the client can retry with the same nonce or a fallback URL.
            nonce_seen[nonce] = now

        owner_guard = read_owner_guard(self.server.repo_root)  # type: ignore[attr-defined]
        if bool(owner_guard.get("enabled", False)):
            trusted = {
                str(device_id).strip()
                for device_id in owner_guard.get("trusted_mobile_devices", [])
                if str(device_id).strip()
            }
            device_id = self.headers.get("X-Jarvis-Device-Id", "").strip()
            if not device_id or len(device_id) > 128 or (not device_id.isascii()):
                # Remove nonce so client can retry (e.g. fallback URL)
                with self.server.nonce_lock:  # type: ignore[attr-defined]
                    self.server.nonce_seen.pop(nonce, None)  # type: ignore[attr-defined]
                self._unauthorized("Missing trusted mobile device id.")
                return False
            if device_id not in trusted:
                master_password = self.headers.get("X-Jarvis-Master-Password", "").strip()
                if master_password:
                    client_ip = str(self.client_address[0]).strip()
                    server: MobileIngestServer = self.server  # type: ignore[assignment]
                    if server.check_master_pw_rate(client_ip):
                        with self.server.nonce_lock:  # type: ignore[attr-defined]
                            self.server.nonce_seen.pop(nonce, None)  # type: ignore[attr-defined]
                        self._write_json(
                            HTTPStatus.TOO_MANY_REQUESTS,
                            {"ok": False, "error": "Too many master password attempts. Try again later."},
                        )
                        return False
                    server.record_master_pw_attempt(client_ip)
                    if verify_master_password(self.server.repo_root, master_password):  # type: ignore[attr-defined]
                        trust_mobile_device(self.server.repo_root, device_id)  # type: ignore[attr-defined]
                    else:
                        with self.server.nonce_lock:  # type: ignore[attr-defined]
                            self.server.nonce_seen.pop(nonce, None)  # type: ignore[attr-defined]
                        self._unauthorized("Untrusted mobile device.")
                        return False
                else:
                    with self.server.nonce_lock:  # type: ignore[attr-defined]
                        self.server.nonce_seen.pop(nonce, None)  # type: ignore[attr-defined]
                    self._unauthorized("Untrusted mobile device.")
                    return False

        return True

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        # Rate limit authenticated GET endpoints
        if path not in ("/", "/quick", "/health", "/cert-fingerprint", "/favicon.ico"):
            if not self._check_rate_limit(path):
                return
        if path == "/":
            self._write_text(HTTPStatus.OK, "text/html; charset=utf-8", self._quick_panel_html())
            return
        if path == "/quick":
            self._write_text(HTTPStatus.OK, "text/html; charset=utf-8", self._quick_panel_html())
            return
        if path == "/health":
            # Include intelligence regression status from self-test history
            self_test_history_path = self.server.repo_root / ".planning" / "runtime" / "self_test_history.jsonl"  # type: ignore[attr-defined]
            intelligence_status: dict[str, Any] = {"score": 0.0, "regression": False, "last_test": ""}
            if self_test_history_path.exists():
                try:
                    lines = self_test_history_path.read_text(encoding="utf-8").strip().split("\n")
                    if lines and lines[-1].strip():
                        latest = json.loads(lines[-1])
                        intelligence_status["score"] = latest.get("average_score", 0.0)
                        intelligence_status["last_test"] = latest.get("timestamp", "")
                        intelligence_status["regression"] = latest.get("below_threshold", False)
                except Exception as exc:
                    logger.debug("self-test history parse failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "status": "healthy", "intelligence": intelligence_status})
            return
        if path == "/cert-fingerprint":
            # Public endpoint (no auth) — returns TLS cert SHA-256 fingerprint
            # for trust-on-first-use (TOFU) cert pinning
            server_obj: MobileIngestServer = self.server  # type: ignore[assignment]
            security_dir = server_obj.repo_root / ".planning" / "security"
            cert_path_str = str(security_dir / "tls_cert.pem")
            if not (security_dir / "tls_cert.pem").exists():
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "No TLS certificate found."})
                return
            fingerprint = _get_cert_fingerprint(cert_path_str)
            if fingerprint is None:
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Failed to compute fingerprint."})
                return
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "fingerprint": fingerprint,
                "algorithm": "sha256",
            })
            return
        if path == "/settings":
            if not self._validate_auth(b""):
                return
            self._write_json(HTTPStatus.OK, {"ok": True, "settings": self._settings_payload()})
            return
        if path == "/dashboard":
            if not self._validate_auth(b""):
                return
            root: Path = self.server.repo_root  # type: ignore[attr-defined]
            self._write_json(
                HTTPStatus.OK,
                {"ok": True, "dashboard": build_intelligence_dashboard(root)},
            )
            return
        if path == "/audit":
            if not self._validate_auth(b""):
                return
            root_path: Path = self.server.repo_root  # type: ignore[attr-defined]
            audit_path = root_path / ".planning" / "runtime" / "gateway_audit.jsonl"
            records: list[dict[str, Any]] = []
            if audit_path.exists():
                try:
                    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
                    for line in lines[-50:]:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                except OSError:
                    pass
            self._write_json(HTTPStatus.OK, {"ok": True, "audit": records, "total": len(records)})
            return
        if path == "/processes":
            if not self._validate_auth(b""):
                return
            from jarvis_engine.process_manager import list_services
            root_p: Path = self.server.repo_root  # type: ignore[attr-defined]
            services = list_services(root_p)
            control = {}
            ctrl_path = root_p / ".planning" / "runtime" / "control.json"
            if ctrl_path.exists():
                try:
                    control = json.loads(ctrl_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "services": services,
                "control": control,
            })
            return
        if path == "/sync/status":
            if not self._validate_auth(b""):
                return
            sync_engine = self.server.ensure_sync_engine()
            if sync_engine is None:
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Sync not available."})
                return
            try:
                status = sync_engine.sync_status()
                self._write_json(HTTPStatus.OK, {"ok": True, "sync_status": status})
            except Exception as exc:
                logger.error("sync/status failed: %s", exc)
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Sync status query failed."})
            return
        if path == "/activity":
            if not self._validate_auth(b""):
                return
            try:
                from jarvis_engine.activity_feed import get_activity_feed
            except ImportError:
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Activity feed not available."})
                return
            # Parse query params
            import urllib.parse as _urlparse
            qs = _urlparse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            try:
                limit = int(qs.get("limit", ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(limit, 500))
            category = qs.get("category", [None])[0]
            since = qs.get("since", [None])[0]
            try:
                feed = get_activity_feed()
                events = feed.query(limit=limit, category=category, since=since)
                stats = feed.stats()
                self._write_json(HTTPStatus.OK, {
                    "ok": True,
                    "events": [
                        {
                            "event_id": e.event_id,
                            "timestamp": e.timestamp,
                            "category": e.category,
                            "summary": e.summary,
                            "details": e.details,
                        }
                        for e in events
                    ],
                    "stats": stats,
                })
            except Exception as exc:
                logger.error("activity feed query failed: %s", exc)
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Activity feed query failed."})
            return
        if path == "/widget-status":
            # Combined endpoint: health + growth + dashboard alerts in ONE request.
            # Replaces 3 separate calls per widget poll cycle.
            if not self._validate_auth(b""):
                return
            root_ws: Path = self.server.repo_root  # type: ignore[attr-defined]
            combined: dict[str, Any] = {"ok": True}
            try:
                combined["growth"] = self._gather_intelligence_growth()
            except Exception:
                combined["growth"] = {}
            try:
                dash = build_intelligence_dashboard(root_ws)
                combined["alerts"] = dash.get("proactive_alerts", [])
            except Exception:
                combined["alerts"] = []
            self._write_json(HTTPStatus.OK, combined)
            return
        if path == "/intelligence/growth":
            if not self._validate_auth(b""):
                return
            self._write_json(HTTPStatus.OK, self._gather_intelligence_growth())
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        return

    def _gather_intelligence_growth(self) -> dict[str, Any]:
        """Collect real intelligence growth metrics from all subsystems."""
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
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
        }

        # --- Knowledge graph metrics from KG history (same source as dashboard) ---
        try:
            from jarvis_engine.proactive.kg_metrics import load_kg_history, kg_growth_trend
            history_path = root / ".planning" / "runtime" / "kg_metrics.jsonl"
            history = load_kg_history(history_path, limit=50)
            if history:
                latest = history[-1]
                metrics["kg_nodes"] = int(latest.get("node_count", 0))
                metrics["kg_edges"] = int(latest.get("edge_count", 0))
                metrics["facts_total"] = metrics["kg_nodes"]
                branch_counts = latest.get("branch_counts", {})
                if isinstance(branch_counts, dict):
                    metrics["branches"] = {str(k): int(v) for k, v in branch_counts.items()}

                # Count facts from last 7 days by comparing history entries
                from datetime import timedelta
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

                # Growth trend from KG history
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
                except Exception as exc:
                    logger.debug("intelligence growth metric failed: %s", exc)
        except Exception as exc:
            logger.debug("Intelligence growth: KG metrics unavailable: %s", exc)

        # --- Activity feed: corrections and consolidations ---
        try:
            from jarvis_engine.activity_feed import get_activity_feed
            feed = get_activity_feed()
            stats = feed.stats()
            if isinstance(stats, dict):
                metrics["corrections_applied"] = int(stats.get("correction_applied", 0))
                metrics["consolidations_run"] = int(stats.get("consolidation", 0))

            # Count corrections in last 7 days from feed query
            from datetime import timedelta
            since_7d = (datetime.now(UTC) - timedelta(days=7)).isoformat()
            try:
                recent_events = feed.query(limit=500, category="correction_applied", since=since_7d)
                metrics["corrections_last_7d"] = len(recent_events)
            except Exception as exc:
                logger.debug("intelligence growth metric failed: %s", exc)
        except Exception as exc:
            logger.debug("Intelligence growth: activity feed unavailable: %s", exc)

        # --- Memory engine: record count ---
        try:
            server: MobileIngestServer = self.server  # type: ignore[assignment]
            mem_engine = server.ensure_memory_engine()
            if mem_engine is not None:
                metrics["memory_records"] = mem_engine.count_records()
        except Exception as exc:
            logger.debug("Intelligence growth: memory records unavailable: %s", exc)

        # --- Self-test score from growth tracker history ---
        self_test_path = root / ".planning" / "runtime" / "self_test_history.jsonl"
        try:
            if self_test_path.exists():
                lines = self_test_path.read_text(encoding="utf-8").strip().split("\n")
                if lines and lines[-1].strip():
                    latest_test = json.loads(lines[-1])
                    score = latest_test.get("average_score", 0.0)
                    metrics["last_self_test_score"] = round(float(score), 3)
        except Exception as exc:
            logger.debug("Intelligence growth: self-test history unavailable: %s", exc)

        # --- On-demand self-test if no history exists and memory engine is available ---
        if metrics["last_self_test_score"] == 0.0 and metrics["memory_records"] > 0:
            try:
                from jarvis_engine.proactive.self_test import AdversarialSelfTest
                server_obj: MobileIngestServer = self.server  # type: ignore[assignment]
                mem_engine = server_obj.ensure_memory_engine()
                embed_svc = server_obj.ensure_embed_service()
                if mem_engine is not None and embed_svc is not None:
                    tester = AdversarialSelfTest(mem_engine, embed_svc, score_threshold=0.5)
                    quiz_result = tester.run_memory_quiz()
                    self_test_path.parent.mkdir(parents=True, exist_ok=True)
                    tester.save_quiz_result(quiz_result, self_test_path)
                    score = quiz_result.get("average_score", 0.0)
                    metrics["last_self_test_score"] = round(float(score), 3)
                    logger.info("On-demand self-test completed: score=%.3f", score)
            except Exception as exc:
                logger.debug("On-demand self-test failed: %s", exc)

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
        except Exception as exc:
            logger.debug("Intelligence growth: capability history unavailable: %s", exc)

        # --- Active learning missions ---
        try:
            import jarvis_engine.main as main_mod
            from jarvis_engine.commands.intelligence_commands import MissionStatusCommand
            bus = main_mod._get_bus()
            mission_result = bus.dispatch(MissionStatusCommand(last=5))
            if mission_result.missions:
                metrics["mission_count"] = mission_result.total_count
                metrics["active_missions"] = [
                    {"topic": m.get("topic", ""), "status": m.get("status", ""), "findings": m.get("verified_findings", 0)}
                    for m in mission_result.missions[:5]
                    if isinstance(m, dict)
                ]
            else:
                metrics["mission_count"] = 0
                metrics["active_missions"] = []
        except Exception as exc:
            logger.debug("Intelligence growth: mission status unavailable: %s", exc)
            metrics["mission_count"] = 0
            metrics["active_missions"] = []

        return {"ok": True, "metrics": metrics}

    def _check_rate_limit(self, path: str) -> bool:
        """Check global API rate limit. Returns True if request should proceed."""
        client_ip = str(self.client_address[0]).strip()
        server: MobileIngestServer = self.server  # type: ignore[assignment]
        if server.check_api_rate(client_ip, path):
            self._write_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "error": "Rate limit exceeded. Try again later."},
            )
            return False
        return True

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not self._check_rate_limit(path):
            return
        if path == "/bootstrap":
            payload, _ = self._read_json_body_noauth(max_content_length=6_000)
            if payload is None:
                return
            # Bootstrap returns credentials so restrict to localhost first.
            # The only exception is if JARVIS_ALLOW_REMOTE_BOOTSTRAP is set.
            client_ip = str(self.client_address[0]).strip()
            allow_remote_bootstrap = os.getenv("JARVIS_ALLOW_REMOTE_BOOTSTRAP", "").strip().lower() in {
                "1",
                "true",
                "yes",
            }
            if client_ip not in ("127.0.0.1", "::1") and not allow_remote_bootstrap:
                self._write_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Bootstrap only allowed from localhost."})
                return
            # Rate-limit bootstrap attempts to prevent brute-force attacks
            server: MobileIngestServer = self.server  # type: ignore[assignment]
            if server.check_bootstrap_rate(client_ip):
                self._write_json(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {"ok": False, "error": "Too many bootstrap attempts. Try again later."},
                )
                return
            master_password = str(payload.get("master_password", "")).strip()
            if not master_password:
                master_password = self.headers.get("X-Jarvis-Master-Password", "").strip()
            if not master_password:
                self._unauthorized("Master password is required.")
                return
            root: Path = self.server.repo_root  # type: ignore[attr-defined]
            if not verify_master_password(root, master_password):
                server.record_bootstrap_attempt(client_ip)
                self._unauthorized("Invalid master password.")
                return
            device_id = str(payload.get("device_id", "")).strip()
            if not device_id:
                device_id = self.headers.get("X-Jarvis-Device-Id", "").strip()
            trusted = False
            if device_id and len(device_id) <= 128 and device_id.isascii():
                trust_mobile_device(root, device_id)
                trusted = True
            bind_addr = self.server.server_address[0]
            port = self.server.server_address[1]
            if bind_addr in ("0.0.0.0", "", "::"):
                # Determine the actual LAN IP so the mobile client can connect
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.connect(("8.8.8.8", 80))
                        bind_addr = s.getsockname()[0]
                except OSError:
                    bind_addr = "127.0.0.1"
            _scheme = "https" if getattr(self.server, "tls_active", False) else "http"
            base_url = f"{_scheme}://{bind_addr}:{port}"
            logger.warning("Bootstrap credentials sent — ensure connection is from localhost only")
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "session": {
                        "base_url": base_url,
                        "token": self.server.auth_token,  # type: ignore[attr-defined]
                        "signing_key": self.server.signing_key,  # type: ignore[attr-defined]
                        "device_id": device_id,
                        "trusted_device": trusted,
                    },
                    "owner_guard": {
                        k: v for k, v in read_owner_guard(root).items()
                        if k not in ("master_password_hash", "master_password_salt_b64", "master_password_iterations")
                    },
                },
            )
            return

        if path == "/processes/kill":
            payload, _ = self._read_json_body(max_content_length=1_000)
            if payload is None:
                return
            service_name = str(payload.get("service", "")).strip()
            from jarvis_engine.process_manager import SERVICES, kill_service
            if service_name not in SERVICES:
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Unknown service: {service_name}"})
                return
            root_p: Path = self.server.repo_root  # type: ignore[attr-defined]
            killed = kill_service(service_name, root_p)
            self._write_json(HTTPStatus.OK, {"ok": True, "service": service_name, "killed": killed})
            return

        if path == "/ingest":
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
            return

        if path == "/settings":
            payload, _ = self._read_json_body(max_content_length=10_000)
            if payload is None:
                return

            reason = str(payload.get("reason", "")).strip()[:200]
            reset_raw = payload.get("reset", False)
            if not isinstance(reset_raw, bool):
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid reset."})
                return
            reset = reset_raw
            daemon_paused = payload.get("daemon_paused")
            safe_mode = payload.get("safe_mode")
            gaming_enabled = payload.get("gaming_enabled")
            gaming_auto_detect = payload.get("gaming_auto_detect")

            for key, value in (
                ("daemon_paused", daemon_paused),
                ("safe_mode", safe_mode),
                ("gaming_enabled", gaming_enabled),
                ("gaming_auto_detect", gaming_auto_detect),
            ):
                if value is not None and not isinstance(value, bool):
                    self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Invalid {key}."})
                    return

            root_path: Path = self.server.repo_root  # type: ignore[attr-defined]
            if reset:
                reset_control_state(root_path)
                try:
                    self._write_gaming_state(enabled=False, auto_detect=False, reason=reason)
                except PermissionError:
                    self._write_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Unsafe gaming state path."})
                    return
            else:
                if daemon_paused is not None or safe_mode is not None or reason:
                    write_control_state(
                        root_path,
                        daemon_paused=daemon_paused if isinstance(daemon_paused, bool) else None,
                        safe_mode=safe_mode if isinstance(safe_mode, bool) else None,
                        reason=reason,
                    )
                if gaming_enabled is not None or gaming_auto_detect is not None or reason:
                    try:
                        self._write_gaming_state(
                            enabled=gaming_enabled if isinstance(gaming_enabled, bool) else None,
                            auto_detect=gaming_auto_detect if isinstance(gaming_auto_detect, bool) else None,
                            reason=reason,
                        )
                    except PermissionError:
                        self._write_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Unsafe gaming state path."})
                        return

            self._write_json(HTTPStatus.OK, {"ok": True, "settings": self._settings_payload()})
            return

        if path == "/conversation/clear":
            payload, _ = self._read_json_body(max_content_length=1_000)
            if payload is None:
                return
            # Clear server-side conversation history
            try:
                import jarvis_engine.main as _main_mod
                with _main_mod._conversation_history_lock:
                    _main_mod._conversation_history.clear()
                self._write_json(HTTPStatus.OK, {"ok": True, "message": "Conversation history cleared."})
            except Exception as exc:
                self._write_json(HTTPStatus.OK, {"ok": True, "message": f"Best-effort clear: {exc}"})
            return

        if path == "/command":
            payload, _ = self._read_json_body(max_content_length=25_000)
            if payload is None:
                return
            result = self._run_voice_command(payload)
            self._write_json(HTTPStatus.OK, result)
            return

        if path == "/sync":
            # Deprecated endpoint — tell clients to use the new endpoints
            self._write_json(
                HTTPStatus.GONE,
                {"ok": False, "error": "Deprecated. Use /sync/pull or /sync/push", "endpoints": ["/sync/pull", "/sync/push", "/sync/status"]},
            )
            return

        if path == "/sync/pull":
            payload, _ = self._read_json_body(max_content_length=10_000)
            if payload is None:
                return
            device_id = str(payload.get("device_id", "")).strip()
            if not device_id or len(device_id) > 128 or not device_id.isascii():
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid device_id."})
                return
            sync_engine = self.server.ensure_sync_engine()
            sync_transport = getattr(self.server, "_sync_transport", None)
            if sync_engine is None or sync_transport is None:
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Sync not available."})
                return
            try:
                import base64 as _b64
                outgoing = sync_engine.compute_outgoing(device_id)
                encrypted = sync_transport.encrypt(outgoing)
                encoded = _b64.b64encode(encrypted).decode("ascii")
                has_more = any(len(v) >= 500 for v in outgoing.get("changes", {}).values())
                self._write_json(HTTPStatus.OK, {
                    "ok": True,
                    "encrypted_payload": encoded,
                    "new_cursors": outgoing.get("cursors", {}),
                    "has_more": has_more,
                })
            except Exception as exc:
                logger.error("sync/pull failed: %s", exc)
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Sync pull failed."})
            return

        if path == "/sync/push":
            payload, _ = self._read_json_body(max_content_length=2_000_000)
            if payload is None:
                return
            device_id = str(payload.get("device_id", "")).strip()
            encrypted_payload = str(payload.get("encrypted_payload", "")).strip()
            if not device_id or len(device_id) > 128 or not device_id.isascii():
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid device_id."})
                return
            if not encrypted_payload:
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "encrypted_payload is required."})
                return
            sync_engine = self.server.ensure_sync_engine()
            sync_transport = getattr(self.server, "_sync_transport", None)
            if sync_engine is None or sync_transport is None:
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Sync not available."})
                return
            try:
                import base64 as _b64
                try:
                    raw_token = _b64.b64decode(encrypted_payload)
                except Exception:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid base64 payload."})
                    return
                changes = sync_transport.decrypt(raw_token)
                result = sync_engine.apply_incoming(changes, device_id)
                self._write_json(HTTPStatus.OK, {
                    "ok": True,
                    "applied": result.get("applied", 0),
                    "conflicts_resolved": result.get("conflicts_resolved", 0),
                    "errors": result.get("errors", []),
                })
            except Exception as exc:
                logger.error("sync/push failed: %s", exc)
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Sync push failed."})
            return

        if path == "/self-heal":
            payload, _ = self._read_json_body(max_content_length=10_000)
            if payload is None:
                return
            keep_recent_raw = payload.get("keep_recent", 1800)
            force_maintenance = _parse_bool(payload.get("force_maintenance", False))
            snapshot_note = str(payload.get("snapshot_note", "mobile-self-heal")).strip()[:160] or "mobile-self-heal"
            snapshot_note = snapshot_note.lstrip("-") or "mobile-self-heal"
            try:
                keep_recent = int(keep_recent_raw)
            except (TypeError, ValueError):
                keep_recent = 1800
            keep_recent = max(200, min(keep_recent, 50000))
            args = ["self-heal", "--keep-recent", str(keep_recent), "--snapshot-note", snapshot_note]
            if force_maintenance:
                args.append("--force-maintenance")
            result = self._run_main_cli(args, timeout_s=240)
            self._write_json(HTTPStatus.OK, result)
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        return

    def log_message(self, fmt: str, *args: object) -> None:
        # Keep mobile ingestion logs out of stdout unless explicitly logged via memory store.
        return


def run_mobile_server(
    host: str,
    port: int,
    auth_token: str,
    signing_key: str,
    repo_root: Path,
    *,
    tls: bool | None = None,
) -> None:
    """Start the mobile API HTTP(S) server.

    *tls* controls TLS behaviour:
    - ``None``  (default): auto-detect; enable TLS if certs exist or can be
      generated, fall back to HTTP otherwise.
    - ``True``:  require TLS; generate certs if needed, raise on failure.
    - ``False``: explicitly disable TLS (plain HTTP).
    """
    # --- Resolve TLS cert / key ---------------------------------------------------
    security_dir = repo_root / ".planning" / "security"
    tls_cert: str | None = None
    tls_key: str | None = None

    if tls is not False:
        tls_cert, tls_key = _ensure_tls_cert(security_dir)
        if tls is True and (tls_cert is None or tls_key is None):
            raise RuntimeError(
                "TLS was explicitly requested but certificate generation failed. "
                "Install openssl or provide certs manually in .planning/security/"
            )

    tls_active = tls_cert is not None and tls_key is not None

    # --- Non-loopback bind guard --------------------------------------------------
    allow_insecure_non_loopback = os.getenv("JARVIS_ALLOW_INSECURE_MOBILE_BIND", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if host not in {"127.0.0.1", "localhost", "::1"} and not tls_active and not allow_insecure_non_loopback:
        raise RuntimeError(
            "Refusing non-loopback mobile bind without TLS. "
            "Set JARVIS_ALLOW_INSECURE_MOBILE_BIND=true only for trusted local testing."
        )

    store = MemoryStore(repo_root)
    pipeline = IngestionPipeline(store)
    server = MobileIngestServer(
        (host, port),
        MobileIngestHandler,
        auth_token=auth_token,
        signing_key=signing_key,
        pipeline=pipeline,
        repo_root=repo_root,
    )

    # Initialize sync engine and transport if memory DB exists
    db_path = repo_root / ".planning" / "brain" / "jarvis_memory.db"
    if db_path.exists():
        try:
            from jarvis_engine.sync.changelog import install_changelog_triggers
            from jarvis_engine.sync.engine import SyncEngine
            from jarvis_engine.sync.transport import SyncTransport

            import sqlite3 as _sqlite3
            import threading as _threading

            sync_db = _sqlite3.connect(str(db_path), check_same_thread=False)
            try:
                sync_db.execute("PRAGMA journal_mode=WAL")
                sync_db.execute("PRAGMA busy_timeout=5000")
                sync_lock = _threading.Lock()
                install_changelog_triggers(sync_db, device_id="desktop")
                server._sync_engine = SyncEngine(sync_db, sync_lock, device_id="desktop")
            except Exception:
                sync_db.close()
                raise

            if signing_key:
                salt_path = repo_root / ".planning" / "brain" / "sync_salt.bin"
                server._sync_transport = SyncTransport(signing_key, salt_path)
                logger.info("Sync engine and transport initialized for mobile API")
            else:
                logger.warning("No signing key; sync transport not initialized")
        except Exception as exc:
            logger.warning("Failed to initialize sync for mobile API: %s", exc)

    # Build dynamic CORS whitelist: add the actual LAN IP if binding to 0.0.0.0
    if host in ("0.0.0.0", "", "::"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
            server._extra_cors_origins.append(
                re.compile(rf"^https?://{re.escape(lan_ip)}(:\d+)?$")
            )
        except OSError:
            pass

    # --- Wrap server socket with TLS if certs are available ----------------------
    if tls_active:
        assert tls_cert is not None and tls_key is not None  # for type-checker
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls_cert, tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.tls_active = True
        logger.info("TLS enabled with cert=%s key=%s", tls_cert, tls_key)

    scheme = "https" if tls_active else "http"
    logger.info("mobile_api_listening=%s://%s:%s", scheme, host, port)
    logger.info("tls=%s", "enabled" if tls_active else "disabled")
    if host not in {"127.0.0.1", "localhost", "::1"} and not tls_active:
        logger.warning("mobile_api_non_loopback_without_tls")
    logger.info("endpoints: GET /, GET /quick, GET /health, GET /cert-fingerprint, GET /settings, GET /dashboard, GET /activity, GET /intelligence/growth, POST /bootstrap, POST /ingest, POST /settings, POST /command, POST /sync/pull, POST /sync/push, GET /sync/status, POST /self-heal")
    # Pre-warm the CommandBus so the first user request doesn't pay cold start cost
    def _prewarm() -> None:
        try:
            import jarvis_engine.main as main_mod
            with _repo_root_lock:
                original = main_mod.repo_root
                main_mod.repo_root = lambda: repo_root  # type: ignore[assignment]
            try:
                main_mod._get_bus()
            finally:
                with _repo_root_lock:
                    main_mod.repo_root = original  # type: ignore[assignment]
            logger.info("CommandBus pre-warmed successfully")
        except Exception as exc:
            logger.warning("CommandBus pre-warm failed (will warm on first request): %s", exc)

    import threading as _threading
    _threading.Thread(target=_prewarm, daemon=True, name="bus-prewarm").start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Mobile API server shutting down (KeyboardInterrupt)")
    finally:
        server.shutdown()
        # Close sync DB connections to prevent SQLite connection leaks
        if server._sync_engine is not None:
            try:
                sync_db = getattr(server._sync_engine, "_db", None)
                if sync_db is not None:
                    sync_db.close()
                    logger.info("Sync engine DB connection closed")
            except Exception as exc:
                logger.warning("Failed to close sync engine DB: %s", exc)
        # Close the MemoryEngine (lazy-initialized for metrics)
        if server._memory_engine is not None:
            try:
                server._memory_engine.close()
                logger.info("MemoryEngine connection closed")
            except Exception as exc:
                logger.warning("Failed to close MemoryEngine: %s", exc)
        # Close the MemoryStore (which holds its own SQLite connection)
        try:
            store.close()
            logger.info("MemoryStore connection closed")
        except Exception as exc:
            logger.warning("Failed to close MemoryStore: %s", exc)
