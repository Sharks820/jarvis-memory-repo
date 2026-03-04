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
_EXPENSIVE_PATHS = {"/command", "/self-heal", "/auth/login", "/feedback"}

# Public endpoints with no body and no auth — skip the full security pipeline.
# Rate limiting already protects these from abuse.
_PUBLIC_SAFE_PATHS = frozenset({"/health", "/cert-fingerprint"})


def _configure_db(conn: "sqlite3.Connection") -> None:
    """Apply consistent SQLite PRAGMAs for performance and reliability.

    WAL mode, relaxed synchronous, 5s busy timeout, 64MB cache, 256MB mmap,
    and foreign key enforcement.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA cache_size=-65536")   # 64MB
    conn.execute("PRAGMA mmap_size=268435456")  # 256MB
    conn.execute("PRAGMA foreign_keys=ON")


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
    allow_reuse_address = True

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
        # Auto-sync config for relay URLs, sync scheduling, phone autonomy
        self._auto_sync_config: Any = None
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
        # Security orchestrator — FAIL CLOSED: if init fails, reject all
        # non-essential requests (security subsystem must be operational).
        self.security: Any = None
        self._security_db: Any = None
        self._security_write_lock: threading.Lock | None = None
        self._security_degraded: bool = False
        try:
            import sqlite3 as _sec_sqlite3
            from jarvis_engine.security.orchestrator import SecurityOrchestrator
            security_db_path = self.repo_root / ".planning" / "brain" / "security.db"
            security_db_path.parent.mkdir(parents=True, exist_ok=True)
            self._security_db = _sec_sqlite3.connect(str(security_db_path), check_same_thread=False)
            _configure_db(self._security_db)
            self._security_write_lock = threading.Lock()
            forensic_dir = self.repo_root / ".planning" / "runtime" / "forensic"
            forensic_dir.mkdir(parents=True, exist_ok=True)
            self.security = SecurityOrchestrator(
                db=self._security_db,
                write_lock=self._security_write_lock,
                log_dir=forensic_dir,
            )
            logger.info("SecurityOrchestrator initialized for mobile API")
        except Exception as exc:
            logger.error("SecurityOrchestrator init FAILED — server will reject non-essential requests: %s", exc)
            self.security = None
            self._security_degraded = True

        # Owner session manager — FAIL CLOSED: if init fails, reject
        # session-dependent requests.
        self.owner_session: Any = None
        self._session_degraded: bool = False
        try:
            from jarvis_engine.security.owner_session import OwnerSessionManager
            self.owner_session = OwnerSessionManager(
                session_timeout=int(os.environ.get("JARVIS_SESSION_TIMEOUT", "1800")),
            )
            logger.info("OwnerSessionManager initialized for mobile API")
        except Exception as exc:
            logger.error("OwnerSessionManager init FAILED — session auth will be unavailable: %s", exc)
            self.owner_session = None
            self._session_degraded = True

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
            if len(self._bootstrap_attempts) > 5000:
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
            if len(self._master_pw_attempts) > 5000:
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
            if len(bucket) > 5000:
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
                    _configure_db(sync_db)
                    sync_lock = threading.Lock()
                    install_changelog_triggers(sync_db, device_id="desktop")
                    # Load conflict strategy from auto-sync config
                    conflict_strategy = "most_recent"
                    if self._auto_sync_config is not None:
                        conflict_strategy = self._auto_sync_config.get(
                            "conflict_strategy", "most_recent",
                        )
                    self._sync_engine = SyncEngine(
                        sync_db, sync_lock, device_id="desktop",
                        conflict_strategy=conflict_strategy,
                    )
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
            "X-Jarvis-Signature, X-Jarvis-Device-Id, X-Jarvis-Master-Password, "
            "X-Jarvis-Session",
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
        model_override = str(payload.get("model_override", "")).strip()
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
                                model_override=model_override,
                            )
                    finally:
                        main_mod.repo_root = original_repo_root  # type: ignore[assignment]
            except Exception as exc:
                logger.error("Voice command execution failed: %s", exc)
                return {
                    "ok": False,
                    "error": "Command execution failed.",
                    "intent": "execution_error",
                    "reason": "internal error",
                    "stdout_tail": [],
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
            return {"ok": False, "error": "Command execution failed.", "command_exit_code": 2, "stdout_tail": [], "stderr_tail": []}
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
        # Use cached body from do_POST if available (already read for security scan)
        cached = getattr(self, "_cached_post_body", None)
        if cached is not None:
            body = cached
            self._cached_post_body = None  # consume once
            content_length = len(body)
        else:
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
            try:
                body = self.rfile.read(content_length)
            except (OSError, ConnectionError):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "Connection reset during read."},
                )
                return None, None

        if content_length <= 0 or content_length > max_content_length:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Invalid content length."},
            )
            return None, None

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
        # Use cached body from do_POST if available (already read for security scan)
        cached = getattr(self, "_cached_post_body", None)
        if cached is not None:
            body = cached
            self._cached_post_body = None  # consume once
            content_length = len(body)
        else:
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
            try:
                body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            except (OSError, ConnectionError):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "Connection reset during read."},
                )
                return None, None
        if content_length < 0 or content_length > max_content_length:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid content length."})
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

    def _validate_auth_flexible(self, body: bytes) -> bool:
        """Accept either a valid session token OR standard HMAC auth.

        Checks ``X-Jarvis-Session`` header first.  If a valid session token
        is present **and** the device is trusted, the request is authenticated
        without requiring HMAC headers.  Otherwise falls back to the full
        HMAC validation path.

        Session-based auth also enforces device trust to prevent stolen
        session tokens from being used on untrusted devices.
        """
        # If the session subsystem failed to initialize, reject session-based
        # auth attempts (fail closed) but still allow HMAC fallback.
        if getattr(self.server, "_session_degraded", False):
            session_token = self.headers.get("X-Jarvis-Session", "").strip()
            if session_token:
                # Session was explicitly provided but subsystem is down
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                    "ok": False,
                    "error": "Service unavailable: session subsystem failed to initialize",
                })
                return False
            # No session token — fall through to HMAC auth
            return self._validate_auth(body)

        session_token = self.headers.get("X-Jarvis-Session", "").strip()
        owner_session = getattr(self.server, "owner_session", None)
        if session_token and owner_session and owner_session.validate_session(session_token):
            # Session token is valid — now enforce device trust
            owner_guard = read_owner_guard(self.server.repo_root)  # type: ignore[attr-defined]
            if bool(owner_guard.get("enabled", False)):
                trusted = {
                    str(did).strip()
                    for did in owner_guard.get("trusted_mobile_devices", [])
                    if str(did).strip()
                }
                device_id = self.headers.get("X-Jarvis-Device-Id", "").strip()
                if not device_id or device_id not in trusted:
                    logger.warning(
                        "Session auth rejected: device %s not trusted",
                        device_id[-4:] if device_id else "(missing)",
                    )
                    self._unauthorized("Session requires trusted device.")
                    return False
            return True
        return self._validate_auth(body)

    # ------------------------------------------------------------------
    # GET handler methods (extracted for O(1) dispatch-dict routing)
    # ------------------------------------------------------------------

    def _handle_get_health(self) -> None:
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

    def _handle_get_cert_fingerprint(self) -> None:
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

    def _handle_get_quick_panel(self) -> None:
        self._write_text(HTTPStatus.OK, "text/html; charset=utf-8", self._quick_panel_html())

    def _handle_get_auth_status(self) -> None:
        # Public endpoint (no auth) — reports whether a session is active
        owner_session = getattr(self.server, "owner_session", None)
        if owner_session is None:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Session auth not available.",
            })
            return
        status = owner_session.session_status()
        status["ok"] = True
        self._write_json(HTTPStatus.OK, status)

    def _handle_get_settings(self) -> None:
        if not self._validate_auth(b""):
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "settings": self._settings_payload()})

    def _handle_get_dashboard(self) -> None:
        if not self._validate_auth(b""):
            return
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        self._write_json(
            HTTPStatus.OK,
            {"ok": True, "dashboard": build_intelligence_dashboard(root)},
        )

    def _handle_get_security_status(self) -> None:
        if not self._validate_auth(b""):
            return
        _sec_orch = getattr(self.server, "security", None)
        if _sec_orch is None:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Security orchestrator not available.",
            })
            return
        self._write_json(HTTPStatus.OK, {
            "ok": True,
            "security": _sec_orch.status(),
        })

    def _handle_get_security_dashboard(self) -> None:
        if not self._validate_auth_flexible(b""):
            return
        server_obj = self.server
        sec = getattr(server_obj, "security", None)
        if sec is None:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False, "error": "Security orchestrator not available"
            })
            return
        dashboard = {
            "security_status": sec.status(),
            "recent_actions": sec.action_auditor.recent_actions(20) if hasattr(sec, "action_auditor") and sec.action_auditor else [],
            "scope_violations": sec.scope_enforcer.recent_violations(10) if hasattr(sec, "scope_enforcer") and sec.scope_enforcer else [],
            "resource_usage": sec.resource_monitor.summary() if hasattr(sec, "resource_monitor") and sec.resource_monitor else {},
            "heartbeat": sec.heartbeat.status() if hasattr(sec, "heartbeat") and sec.heartbeat else {},
            "threat_intel": sec.threat_intel.status() if hasattr(sec, "threat_intel") and sec.threat_intel else {},
        }
        self._write_json(HTTPStatus.OK, {"ok": True, "dashboard": dashboard})

    def _handle_get_audit(self) -> None:
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

    def _handle_get_processes(self) -> None:
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

    def _handle_get_sync_status(self) -> None:
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

    def _handle_get_sync_config(self) -> None:
        """Return auto-sync configuration for the requesting device.

        The phone calls this to learn: relay URL, sync intervals, conflict
        strategy, offline cache settings, etc. This is what enables the phone
        to work from anywhere — not just the same WiFi network.
        """
        if not self._validate_auth(b""):
            return
        try:
            auto_sync = self.server._auto_sync_config
            if auto_sync is None:
                from jarvis_engine.sync.auto_sync import AutoSyncConfig
                config_path = self.server.repo_root / ".planning" / "sync" / "auto_sync_config.json"
                auto_sync = AutoSyncConfig(config_path)
                self.server._auto_sync_config = auto_sync
            device_id = self.headers.get("X-Jarvis-Device-Id", "unknown")
            config = auto_sync.get_sync_config_for_device(device_id)
            self._write_json(HTTPStatus.OK, {"ok": True, "config": config})
        except Exception as exc:
            logger.error("sync/config GET failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Failed to get sync config."})

    def _handle_get_sync_heartbeat(self) -> None:
        """Lightweight heartbeat — phone calls this to confirm connectivity.

        Also records the device's last-seen time for device status tracking.
        Returns minimal payload for speed (used for connectivity checks).
        """
        if not self._validate_auth(b""):
            return
        try:
            device_id = self.headers.get("X-Jarvis-Device-Id", "unknown")
            auto_sync = self.server._auto_sync_config
            if auto_sync is None:
                from jarvis_engine.sync.auto_sync import AutoSyncConfig
                config_path = self.server.repo_root / ".planning" / "sync" / "auto_sync_config.json"
                auto_sync = AutoSyncConfig(config_path)
                self.server._auto_sync_config = auto_sync
            auto_sync.record_heartbeat(device_id)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "server_time": int(time.time()),
                "device_id": device_id,
            })
        except Exception as exc:
            logger.error("sync/heartbeat failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Heartbeat failed."})

    def _handle_get_activity(self) -> None:
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

    def _handle_get_widget_status(self) -> None:
        # Combined endpoint: health + growth + dashboard alerts + recent events in ONE request.
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
        # Recent activity events for UI live updates (UI-03/04)
        try:
            from jarvis_engine.activity_feed import ActivityCategory, get_activity_feed
            feed = get_activity_feed()
            events = feed.query(limit=10)
            combined["recent_events"] = [
                {
                    "event_id": e.event_id,
                    "timestamp": e.timestamp,
                    "category": e.category,
                    "summary": e.summary,
                }
                for e in events
                if e.category != ActivityCategory.DAEMON_CYCLE
            ][:10]
        except Exception:
            combined["recent_events"] = []
        self._write_json(HTTPStatus.OK, combined)

    def _handle_get_intelligence_growth(self) -> None:
        if not self._validate_auth(b""):
            return
        self._write_json(HTTPStatus.OK, self._gather_intelligence_growth())

    def _handle_get_learning_summary(self) -> None:
        if not self._validate_auth(b""):
            return
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        db_path = root / ".planning" / "brain" / "jarvis_memory.db"
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
            except Exception as exc:
                logger.debug("Learning summary: preferences unavailable: %s", exc)
            try:
                from jarvis_engine.learning.feedback import ResponseFeedbackTracker
                ft = ResponseFeedbackTracker(lrn_db)
                summary["route_quality"] = ft.get_all_route_quality()
            except Exception as exc:
                logger.debug("Learning summary: route quality unavailable: %s", exc)
            try:
                from jarvis_engine.learning.usage_patterns import UsagePatternTracker
                ut = UsagePatternTracker(lrn_db)
                summary["peak_hours"] = ut.get_peak_hours()
                summary["hourly_distribution"] = ut.get_hourly_distribution()
                from datetime import datetime as _dt
                now = _dt.now(UTC)
                summary["current_context"] = ut.predict_context(now.hour, now.weekday())
            except Exception as exc:
                logger.debug("Learning summary: usage patterns unavailable: %s", exc)
        except Exception as exc:
            logger.debug("Learning summary: DB unavailable: %s", exc)
        finally:
            if lrn_db is not None:
                lrn_db.close()
        self._write_json(HTTPStatus.OK, summary)

    def _handle_get_favicon(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    # Dispatch dict for GET routes — built once per class, O(1) lookup.
    _GET_DISPATCH: dict[str, str] = {
        "/": "_handle_get_quick_panel",
        "/quick": "_handle_get_quick_panel",
        "/health": "_handle_get_health",
        "/cert-fingerprint": "_handle_get_cert_fingerprint",
        "/auth/status": "_handle_get_auth_status",
        "/settings": "_handle_get_settings",
        "/dashboard": "_handle_get_dashboard",
        "/security/status": "_handle_get_security_status",
        "/security/dashboard": "_handle_get_security_dashboard",
        "/audit": "_handle_get_audit",
        "/processes": "_handle_get_processes",
        "/sync/status": "_handle_get_sync_status",
        "/sync/config": "_handle_get_sync_config",
        "/sync/heartbeat": "_handle_get_sync_heartbeat",
        "/activity": "_handle_get_activity",
        "/widget-status": "_handle_get_widget_status",
        "/intelligence/growth": "_handle_get_intelligence_growth",
        "/learning/summary": "_handle_get_learning_summary",
        "/missions/status": "_handle_get_missions_status",
        "/alerts/pending": "_handle_get_alerts_pending",
        "/digest": "_handle_get_digest",
        "/meeting-prep": "_handle_get_meeting_prep",
        "/scam/campaigns": "_handle_get_scam_campaigns",
        "/scam/stats": "_handle_get_scam_stats",
        "/favicon.ico": "_handle_get_favicon",
    }

    # Paths exempt from rate limiting (public/unauthenticated GET endpoints)
    _GET_RATE_LIMIT_EXEMPT = frozenset({"/", "/quick", "/health", "/cert-fingerprint", "/auth/status", "/favicon.ico"})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        # Rate limit authenticated GET endpoints
        if path not in self._GET_RATE_LIMIT_EXEMPT:
            if not self._check_rate_limit(path):
                return
        # Fast-path: public, unauthenticated endpoints polled frequently.
        # Return early BEFORE the security pipeline for maximum performance.
        if path == "/health":
            self._handle_get_health()
            return
        if path == "/cert-fingerprint":
            self._handle_get_cert_fingerprint()
            return
        # Security orchestrator pipeline check (skipped for public safe paths)
        if path not in _PUBLIC_SAFE_PATHS:
            _security = getattr(self.server, "security", None)
            if _security is not None:
                _client_ip = str(self.client_address[0])
                _sec_check = _security.check_request(
                    path=path,
                    source_ip=_client_ip,
                    headers=dict(self.headers),
                    body="",
                    user_agent=self.headers.get("User-Agent", ""),
                )
                if not _sec_check["allowed"]:
                    logger.warning("Security pipeline blocked GET %s: %s", path, _sec_check.get("reason", "unknown"))
                    self._write_json(HTTPStatus.FORBIDDEN, {
                        "ok": False,
                        "error": "Request blocked by security policy",
                    })
                    return
            elif getattr(self.server, "_security_degraded", False):
                # Fail closed: security subsystem failed to init
                self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                    "ok": False,
                    "error": "Service unavailable: security subsystem failed to initialize",
                })
                return
        # O(1) dispatch for remaining GET routes
        handler_name = self._GET_DISPATCH.get(path)
        if handler_name:
            getattr(self, handler_name)()
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
            from jarvis_engine.commands.ops_commands import MissionStatusCommand
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

    # ------------------------------------------------------------------
    # POST handler methods (extracted for O(1) dispatch-dict routing)
    # ------------------------------------------------------------------

    def _handle_post_bootstrap(self) -> None:
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

    def _handle_post_auth_login(self) -> None:
        # No HMAC auth required — this IS the authentication step.
        # Rate limiting is enforced by _check_rate_limit (/auth/login
        # is in _EXPENSIVE_PATHS: 10 req/min).
        owner_session = getattr(self.server, "owner_session", None)
        if owner_session is None:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Session auth not available.",
            })
            return
        payload, _ = self._read_json_body_noauth(max_content_length=2_000)
        if payload is None:
            return
        password = str(payload.get("password", "")).strip()
        if not password:
            self._write_json(HTTPStatus.BAD_REQUEST, {
                "ok": False,
                "error": "Missing required field: password.",
            })
            return
        # Try OwnerSessionManager first (password set via set_password)
        token = owner_session.authenticate(password)
        if token is None:
            # Fall back to owner_guard master password verification.
            # If it passes, create a session token manually.
            root_auth: Path = self.server.repo_root  # type: ignore[attr-defined]
            if verify_master_password(root_auth, password):
                import secrets as _auth_secrets
                token = _auth_secrets.token_hex(32)
                with owner_session._lock:
                    owner_session._sessions[token] = time.time() + owner_session._session_timeout
                logger.info("Owner authenticated via master password, session %s... created", token[:8])
        if token is None:
            self._write_json(HTTPStatus.UNAUTHORIZED, {
                "ok": False,
                "error": "Invalid password.",
            })
            return
        self._write_json(HTTPStatus.OK, {
            "ok": True,
            "session_token": token,
        })

    def _handle_post_auth_logout(self) -> None:
        owner_session = getattr(self.server, "owner_session", None)
        if owner_session is None:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Session auth not available.",
            })
            return
        session_token = self.headers.get("X-Jarvis-Session", "").strip()
        if not session_token:
            self._write_json(HTTPStatus.BAD_REQUEST, {
                "ok": False,
                "error": "Missing X-Jarvis-Session header.",
            })
            return
        # Body already consumed by do_POST pre-read; no drain needed
        owner_session.logout(session_token)
        self._write_json(HTTPStatus.OK, {"ok": True})

    def _handle_post_auth_lock(self) -> None:
        # Requires session auth — invalidates ALL sessions
        owner_session = getattr(self.server, "owner_session", None)
        if owner_session is None:
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Session auth not available.",
            })
            return
        session_token = self.headers.get("X-Jarvis-Session", "").strip()
        if not session_token or not owner_session.validate_session(session_token):
            self._write_json(HTTPStatus.UNAUTHORIZED, {
                "ok": False,
                "error": "Valid session required for lock.",
            })
            return
        # Body already consumed by do_POST pre-read; no drain needed
        owner_session.logout_all()
        self._write_json(HTTPStatus.OK, {"ok": True})

    def _handle_post_processes_kill(self) -> None:
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

    def _handle_post_settings(self) -> None:
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
        muted = payload.get("muted")
        mute_until_utc = payload.get("mute_until_utc")
        gaming_enabled = payload.get("gaming_enabled")
        gaming_auto_detect = payload.get("gaming_auto_detect")

        for key, value in (
            ("daemon_paused", daemon_paused),
            ("safe_mode", safe_mode),
            ("muted", muted),
            ("gaming_enabled", gaming_enabled),
            ("gaming_auto_detect", gaming_auto_detect),
        ):
            if value is not None and not isinstance(value, bool):
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"Invalid {key}."})
                return
        if mute_until_utc is not None and not isinstance(mute_until_utc, str):
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid mute_until_utc."})
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
            if any(v is not None for v in (daemon_paused, safe_mode, muted, mute_until_utc)) or reason:
                write_control_state(
                    root_path,
                    daemon_paused=daemon_paused if isinstance(daemon_paused, bool) else None,
                    safe_mode=safe_mode if isinstance(safe_mode, bool) else None,
                    muted=muted if isinstance(muted, bool) else None,
                    mute_until_utc=mute_until_utc if isinstance(mute_until_utc, str) else None,
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

    def _handle_post_conversation_clear(self) -> None:
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
            logger.error("Conversation history clear failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "message": "Best-effort clear completed."})

    def _handle_post_command(self) -> None:
        payload, _ = self._read_json_body(max_content_length=25_000)
        if payload is None:
            return
        result = self._run_voice_command(payload)
        # Scan LLM output for security issues (credential leaks, exfiltration, etc.)
        _sec_orch = getattr(self.server, "security", None)
        if _sec_orch is not None and result.get("ok"):
            # Build a combined text from the LLM response fields
            _response_parts = []
            if result.get("reason"):
                _response_parts.append(str(result["reason"]))
            for _line in result.get("stdout_tail", []):
                _response_parts.append(str(_line))
            _response_text = "\n".join(_response_parts)
            if _response_text.strip():
                _output_check = _sec_orch.scan_output(_response_text)
                if not _output_check["safe"]:
                    result["reason"] = _output_check["filtered_text"]
                    result["stdout_tail"] = [_output_check["filtered_text"]]
                    result["security_filtered"] = True
                    logger.warning("Output filtered: %s", _output_check["findings"][:3])
        self._write_json(HTTPStatus.OK, result)

    def _handle_post_sync_deprecated(self) -> None:
        # Deprecated endpoint — tell clients to use the new endpoints
        self._write_json(
            HTTPStatus.GONE,
            {"ok": False, "error": "Deprecated. Use /sync/pull or /sync/push", "endpoints": ["/sync/pull", "/sync/push", "/sync/status"]},
        )

    def _handle_post_sync_pull(self) -> None:
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

    def _handle_post_sync_push(self) -> None:
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

    def _handle_post_sync_config(self) -> None:
        """Update auto-sync configuration (relay URL, intervals, etc).

        Called from desktop CLI or admin to configure how phones connect.
        Example: setting the relay_url to a Cloudflare Tunnel so the phone
        can reach the desktop from anywhere.
        """
        payload, _ = self._read_json_body(max_content_length=10_000)
        if payload is None:
            return
        try:
            auto_sync = self.server._auto_sync_config
            if auto_sync is None:
                from jarvis_engine.sync.auto_sync import AutoSyncConfig
                config_path = self.server.repo_root / ".planning" / "sync" / "auto_sync_config.json"
                auto_sync = AutoSyncConfig(config_path)
                self.server._auto_sync_config = auto_sync
            updates = payload.get("config", payload)
            # Only allow known keys to be updated
            from jarvis_engine.sync.auto_sync import DEFAULT_SYNC_CONFIG
            safe_updates = {k: v for k, v in updates.items() if k in DEFAULT_SYNC_CONFIG}
            if safe_updates:
                auto_sync.update(safe_updates)
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "config": auto_sync.get_all(),
            })
        except Exception as exc:
            logger.error("sync/config POST failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Failed to update sync config."})

    def _handle_post_intelligence_merge(self) -> None:
        """Accept intelligence from the phone and merge into desktop knowledge.

        The phone sends locally-learned facts, context observations, habit
        patterns, and interaction data. The desktop integrates these into
        its knowledge graph, making both systems smarter together.
        """
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
            store = MemoryStore(self.server.repo_root)

            for item in items[:200]:  # Cap at 200 items per merge
                content = item.get("content", "")
                category = item.get("category", "general")
                confidence = float(item.get("confidence", 0.7))
                source = item.get("source", "phone")

                if not content or len(content) > 5000:
                    continue

                try:
                    # Store as a memory record tagged with phone origin
                    store.add(
                        content=f"[phone-intelligence:{category}] {content}",
                        source=source,
                        kind="intelligence",
                        tags=f"phone,{category},auto-merged",
                        branch="phone-intelligence",
                    )
                    merged += 1
                except Exception:
                    pass

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "merged": merged,
                "total_received": len(items),
            })
            logger.info("Intelligence merge: %d items from phone", merged)
        except Exception as exc:
            logger.error("intelligence/merge failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False, "error": "Intelligence merge failed.",
            })

    def _handle_post_intelligence_export(self) -> None:
        """Export desktop knowledge for the phone's local intelligence.

        Returns structured knowledge facts from the desktop's knowledge graph
        and memory store for the phone to import into its local knowledge store.
        This is what makes the phone as smart as the desktop — it gets real
        knowledge, not just cached responses.
        """
        payload, _ = self._read_json_body(max_content_length=10_000)
        if payload is None:
            return
        try:
            limit = min(int(payload.get("limit", 200)), 500)
            items = []

            # Export from knowledge graph
            try:
                from jarvis_engine.knowledge.graph import KnowledgeGraph
                from jarvis_engine.memory.store import MemoryStore

                store = MemoryStore(self.server.repo_root)
                db_path = self.server.repo_root / ".planning" / "brain" / "jarvis_memory.db"

                if db_path.exists():
                    import sqlite3

                    db = sqlite3.connect(str(db_path))
                    db.row_factory = sqlite3.Row
                    try:
                        # Export high-confidence KG facts
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

                        # Export recent high-quality memories
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
                    finally:
                        db.close()
            except Exception as exc:
                logger.warning("KG export partial failure: %s", exc)

            # Export user preferences
            try:
                db_path = self.server.repo_root / ".planning" / "brain" / "jarvis_memory.db"
                if db_path.exists():
                    import sqlite3

                    db = sqlite3.connect(str(db_path))
                    db.row_factory = sqlite3.Row
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
                    finally:
                        db.close()
            except Exception as exc:
                logger.warning("Preferences export failure: %s", exc)

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "items": items[:limit],
                "total": len(items),
            })
            logger.info("Intelligence export: %d items for phone", len(items))
        except Exception as exc:
            logger.error("intelligence/export failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False, "error": "Intelligence export failed.",
            })

    def _handle_post_self_heal(self) -> None:
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
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        db_path = root / ".planning" / "brain" / "jarvis_memory.db"
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

    # ── Mission endpoints ────────────────────────────────────────────────

    def _handle_post_missions_create(self) -> None:
        """Create a learning mission from the phone.

        Payload: {"topic": str, "objective": str?, "sources": list[str]?}
        """
        payload, _ = self._read_json_body(max_content_length=5_000)
        if payload is None:
            return
        topic = str(payload.get("topic", "")).strip()
        if not topic:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "topic is required"})
            return
        objective = str(payload.get("objective", "")).strip()[:400]
        sources = payload.get("sources")
        if sources is not None:
            if not isinstance(sources, list):
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "sources must be a list"})
                return
            sources = [str(s).strip() for s in sources if str(s).strip()][:6]
        try:
            import jarvis_engine.main as _main_mod
            from jarvis_engine.commands.ops_commands import MissionCreateCommand
            bus = _main_mod._get_bus()
            cmd = MissionCreateCommand(topic=topic, objective=objective, sources=sources or [], origin="phone")
            result = bus.dispatch(cmd)
            if result.return_code != 0:
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Mission creation failed — invalid parameters."})
                return
            mission = result.mission if hasattr(result, "mission") else {}
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "mission_id": mission.get("mission_id", ""),
                "topic": mission.get("topic", ""),
                "status": mission.get("status", "pending"),
                "origin": mission.get("origin", "phone"),
                "sources": mission.get("sources", []),
            })
        except ValueError as exc:
            logger.warning("Mission create validation failed: %s", exc)
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            logger.error("Mission create failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Mission creation failed."})

    def _handle_get_missions_status(self) -> None:
        """Get learning mission status — richer than /intelligence/growth snippet."""
        if not self._validate_auth(b""):
            return
        try:
            import jarvis_engine.main as _main_mod
            from jarvis_engine.commands.ops_commands import MissionStatusCommand
            bus = _main_mod._get_bus()
            last = 15
            qs = self.path.split("?", 1)
            if len(qs) > 1:
                from urllib.parse import parse_qs
                params = parse_qs(qs[1])
                try:
                    last = min(int(params.get("last", ["15"])[0]), 50)
                except (TypeError, ValueError):
                    last = 15
            result = bus.dispatch(MissionStatusCommand(last=last))
            missions = result.missions if hasattr(result, "missions") else []
            total = result.total_count if hasattr(result, "total_count") else 0
            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "total": total,
                "missions": [
                    {
                        "mission_id": m.get("mission_id", ""),
                        "topic": m.get("topic", ""),
                        "objective": m.get("objective", ""),
                        "status": m.get("status", ""),
                        "origin": m.get("origin", "desktop-manual"),
                        "sources": m.get("sources", []),
                        "verified_findings": m.get("verified_findings", 0),
                        "created_utc": m.get("created_utc", ""),
                        "updated_utc": m.get("updated_utc", ""),
                    }
                    for m in missions
                    if isinstance(m, dict)
                ],
            })
        except Exception as exc:
            logger.error("Mission status failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Mission status unavailable."})

    # ── Alert queue endpoint (phone polls this) ───────────────────────────

    def _handle_get_alerts_pending(self) -> None:
        """Return and drain all pending proactive alerts for the phone."""
        if not self._validate_auth(b""):
            return
        try:
            from jarvis_engine.proactive.alert_queue import drain_alerts
            root: Path = self.server.repo_root  # type: ignore[attr-defined]
            alerts = drain_alerts(root, limit=50)
            self._write_json(HTTPStatus.OK, {"ok": True, "alerts": alerts})
        except Exception as exc:
            logger.error("Alert queue drain failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "alerts": []})

    # ── Digest endpoint (summarize what you missed) ──────────────────────

    def _handle_get_digest(self) -> None:
        """Return a context-aware digest of what happened while user was busy.

        Query params: ?since=<unix_ts>&context=<meeting|driving|sleeping>
        """
        if not self._validate_auth(b""):
            return
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        qs_parts = self.path.split("?", 1)
        since_ts = 0
        context_label = ""
        if len(qs_parts) > 1:
            from urllib.parse import parse_qs
            params = parse_qs(qs_parts[1])
            try:
                since_ts = int(params.get("since", ["0"])[0])
            except (TypeError, ValueError):
                since_ts = 0
            context_label = str(params.get("context", [""])[0]).strip()

        digest: dict[str, Any] = {
            "context": context_label,
            "since_ts": since_ts,
            "missed_calls": [],
            "notifications_summary": "",
            "calendar_upcoming": [],
            "proactive_alerts": [],
            "tasks_changed": [],
        }

        # Pull pending alerts (peek, don't drain — phone will drain via /alerts/pending)
        try:
            from jarvis_engine.proactive.alert_queue import peek_alerts
            digest["proactive_alerts"] = peek_alerts(root, limit=10)
        except Exception:
            pass

        # Get upcoming calendar events for next 2 hours
        try:
            snapshot_path = root / ".planning" / "ops_snapshot.live.json"
            if snapshot_path.exists():
                import json as _json
                snap = _json.loads(snapshot_path.read_text(encoding="utf-8"))
                events = snap.get("calendar_events", [])
                from datetime import datetime as _dt
                now = _dt.now().astimezone()
                upcoming = []
                for ev in events:
                    start_str = ev.get("start_time", "")
                    if not start_str:
                        continue
                    try:
                        start = _dt.fromisoformat(start_str)
                        if start.tzinfo is None:
                            start = start.astimezone()
                        diff_hours = (start - now).total_seconds() / 3600.0
                        if 0 <= diff_hours <= 2:
                            upcoming.append({
                                "title": ev.get("title", ""),
                                "start_time": start_str,
                                "minutes_until": int(diff_hours * 60),
                            })
                    except (ValueError, TypeError):
                        continue
                digest["calendar_upcoming"] = upcoming[:5]
        except Exception:
            pass

        # Generate a human-readable summary using the voice command system
        if context_label:
            try:
                import jarvis_engine.main as _main_mod
                bus = _main_mod._get_bus()
                from jarvis_engine.commands.ops_commands import OpsBriefCommand
                result = bus.dispatch(OpsBriefCommand())
                if hasattr(result, "brief") and result.brief:
                    digest["notifications_summary"] = result.brief[:1000]
            except Exception:
                pass

        self._write_json(HTTPStatus.OK, {"ok": True, "digest": digest})

    # ── Meeting prep endpoint (KG-powered) ───────────────────────────────

    def _handle_get_meeting_prep(self) -> None:
        """Return KG-powered intelligence briefing for an upcoming meeting.

        Query params: ?title=<meeting_title>&attendees=<comma_separated>
        """
        if not self._validate_auth(b""):
            return
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        qs_parts = self.path.split("?", 1)
        title = ""
        attendees: list[str] = []
        if len(qs_parts) > 1:
            from urllib.parse import parse_qs, unquote
            params = parse_qs(qs_parts[1])
            title = unquote(str(params.get("title", [""])[0]).strip())
            att_raw = str(params.get("attendees", [""])[0]).strip()
            if att_raw:
                attendees = [a.strip() for a in att_raw.split(",") if a.strip()]

        if not title and not attendees:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "title or attendees required"})
            return

        briefing: dict[str, Any] = {
            "title": title,
            "attendees": attendees,
            "context_facts": [],
            "recent_memories": [],
            "suggested_topics": [],
        }

        # Query KG for facts about attendees and meeting topic
        try:
            server_obj: MobileIngestServer = self.server  # type: ignore[assignment]
            mem_engine = server_obj.ensure_memory_engine()
            if mem_engine is not None:
                kg = getattr(mem_engine, "_kg", None) or getattr(mem_engine, "kg", None)
                if kg is not None:
                    # Look up each attendee in the KG
                    for person in attendees[:5]:
                        try:
                            facts = kg.query_relevant_facts([person], limit=5)
                            for fact in facts:
                                briefing["context_facts"].append({
                                    "about": person,
                                    "fact": fact.get("label", ""),
                                    "confidence": round(float(fact.get("confidence", 0)), 2),
                                })
                        except Exception:
                            pass
                    # Look up meeting topic
                    if title:
                        try:
                            topic_facts = kg.query_relevant_facts(title.split()[:4], limit=5)
                            for fact in topic_facts:
                                briefing["context_facts"].append({
                                    "about": title,
                                    "fact": fact.get("label", ""),
                                    "confidence": round(float(fact.get("confidence", 0)), 2),
                                })
                        except Exception:
                            pass

                # Search recent memories about attendees/topic
                keywords = attendees + ([title] if title else [])
                for keyword in keywords[:3]:
                    try:
                        results = mem_engine.search_fts(keyword, limit=3)
                        for record_id, _score in results:
                            rec = mem_engine.get_record(record_id)
                            if rec:
                                briefing["recent_memories"].append({
                                    "about": keyword,
                                    "summary": str(rec.get("summary", ""))[:200],
                                    "date": str(rec.get("ts", "")),
                                })
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("Meeting prep KG query failed: %s", exc)

        # Generate suggested discussion topics from the context
        if briefing["context_facts"] or briefing["recent_memories"]:
            topics = set()
            for fact in briefing["context_facts"]:
                label = fact.get("fact", "")
                if label and len(label) > 10:
                    topics.add(label[:80])
            for mem in briefing["recent_memories"]:
                summary = mem.get("summary", "")
                if summary and len(summary) > 10:
                    topics.add(summary[:80])
            briefing["suggested_topics"] = list(topics)[:5]

        self._write_json(HTTPStatus.OK, {"ok": True, "briefing": briefing})

    # ── Smart reply endpoint ─────────────────────────────────────────────

    def _handle_post_smart_reply(self) -> None:
        """Generate a contextual auto-reply SMS for a missed call.

        Payload: {
            "contact_name": str,
            "phone_number": str,
            "context": "meeting"|"driving"|"sleeping",
            "meeting_end_time": str?,  // ISO format, optional
            "eta_minutes": int?,       // for driving context
        }
        """
        payload, _ = self._read_json_body(max_content_length=5_000)
        if payload is None:
            return
        contact_name = str(payload.get("contact_name", "")).strip()[:50]
        context = str(payload.get("context", "")).strip().lower()
        meeting_end = str(payload.get("meeting_end_time", "")).strip()
        eta_minutes = payload.get("eta_minutes")

        if not contact_name:
            contact_name = "there"

        # Build a contextual reply
        if context == "meeting":
            reply = f"Hey {contact_name}, I'm in a meeting right now"
            if meeting_end:
                try:
                    from datetime import datetime as _dt
                    end_dt = _dt.fromisoformat(meeting_end)
                    reply += f" until {end_dt.strftime('%I:%M %p')}"
                except (ValueError, TypeError):
                    pass
            reply += ". I'll call you back as soon as I'm free."
        elif context == "driving":
            reply = f"Hey {contact_name}, I'm driving right now"
            if eta_minutes and isinstance(eta_minutes, (int, float)):
                reply += f" — about {int(eta_minutes)} min until I arrive"
            reply += ". I'll call you back when I get there."
        elif context == "sleeping":
            reply = f"Hey {contact_name}, I'm currently unavailable. I'll get back to you in the morning."
        else:
            reply = f"Hey {contact_name}, I missed your call. I'll call you back soon."

        reply += " — Sent by Jarvis"

        # Try to get additional context from KG about this contact
        contact_context = ""
        try:
            root: Path = self.server.repo_root  # type: ignore[attr-defined]
            server_obj: MobileIngestServer = self.server  # type: ignore[assignment]
            mem_engine = server_obj.ensure_memory_engine()
            if mem_engine is not None:
                results = mem_engine.search_fts(contact_name, limit=2)
                for record_id, _score in results:
                    rec = mem_engine.get_record(record_id)
                    if rec:
                        contact_context = str(rec.get("summary", ""))[:200]
                        break
        except Exception:
            pass

        self._write_json(HTTPStatus.OK, {
            "ok": True,
            "reply": reply,
            "contact_context": contact_context,
        })

    # ── Scam Campaign Hunter endpoints ──────────────────────────────────

    def _handle_post_scam_report_call(self) -> None:
        """Report a screened call with STIR/SHAKEN status for campaign analysis.

        Accepts: {number, stir_status, presentation, duration_sec, answered, contact_name}
        Returns: {ok, campaign_id?, enhanced_score, recommended_action}
        """
        body, _ = self._read_json_body(max_content_length=5_000)
        if body is None:
            return
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        try:
            from jarvis_engine.scam_hunter import (
                create_call_intel_report,
                save_call_intel,
                load_call_intel,
                detect_campaigns,
                save_campaigns,
                compute_enhanced_spam_score,
                lookup_carrier_cached,
            )
            from jarvis_engine.phone_guard import _normalize_number
            from jarvis_engine._shared import safe_float as _safe_float

            number = str(body.get("number", ""))
            stir_status = str(body.get("stir_status", ""))
            presentation = str(body.get("presentation", ""))
            duration_sec = _safe_float(body.get("duration_sec", 0))
            answered = bool(body.get("answered", False))
            contact_name = str(body.get("contact_name", ""))
            caller_display_name = str(body.get("caller_display_name", ""))
            gateway_domain = str(body.get("gateway_domain", ""))
            setup_latency_ms = int(_safe_float(body.get("setup_latency_ms", 0)))

            # Create and save intel report
            report = create_call_intel_report(
                number=number,
                stir_status=stir_status,
                presentation=presentation,
                duration_sec=duration_sec,
                answered=answered,
                contact_name=contact_name,
            )
            intel_path = root / ".planning" / "runtime" / "call_intel.jsonl"
            save_call_intel(intel_path, report)

            # Check carrier cache
            carrier_cache_path = root / ".planning" / "runtime" / "carrier_cache.json"
            carrier = lookup_carrier_cached(carrier_cache_path, report.normalized)
            carrier_risk = 0.0
            line_type = ""
            if carrier:
                line_type = carrier.line_type
                carrier_risk = carrier.risk_score

            # Run campaign detection on recent data
            all_reports = load_call_intel(intel_path, limit=200)
            campaigns = detect_campaigns(all_reports)
            campaign_path = root / ".planning" / "runtime" / "scam_campaigns.json"
            save_campaigns(campaign_path, campaigns)

            # Check if this number belongs to a campaign
            campaign_id = ""
            campaign_confidence = 0.0
            campaign_signals: list[str] = []
            normalized = _normalize_number(number)
            for campaign in campaigns:
                if normalized in campaign.numbers:
                    campaign_id = campaign.campaign_id
                    campaign_confidence = campaign.confidence
                    campaign_signals = campaign.signals
                    break

            # Build base score: phone_guard pattern score + time-of-day
            from jarvis_engine.scam_hunter import score_time_of_day
            from jarvis_engine.phone_guard import detect_spam_candidates
            tod_score = score_time_of_day(normalized)
            base_score = tod_score
            # Check if phone_guard has a pattern-based score for this number
            pg_candidates = detect_spam_candidates(all_reports)
            for c in pg_candidates:
                if c.number == normalized:
                    base_score = max(base_score, c.score)
                    break

            # Compute enhanced score with ALL signals
            enhanced_score = compute_enhanced_spam_score(
                base_score=base_score,
                stir_status=stir_status,
                line_type=line_type,
                carrier_risk=carrier_risk,
                campaign_confidence=campaign_confidence,
                presentation=presentation,
                is_in_contacts=bool(contact_name),
                caller_display_name=caller_display_name,
                gateway_domain=gateway_domain,
                setup_latency_ms=setup_latency_ms,
            )

            # Determine action
            if enhanced_score >= 0.80:
                action = "block"
            elif enhanced_score >= 0.60:
                action = "silence"
            elif enhanced_score >= 0.40:
                action = "voicemail"
            else:
                action = "allow"

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "enhanced_score": round(enhanced_score, 4),
                "recommended_action": action,
                "campaign_id": campaign_id,
                "campaign_confidence": round(campaign_confidence, 4),
                "line_type": line_type,
                "stir_status": stir_status,
                "signals": campaign_signals,
            })
        except Exception as exc:
            logger.warning("Scam report-call failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "enhanced_score": 0.0, "recommended_action": "voicemail", "error": str(exc)})

    def _handle_post_scam_lookup(self) -> None:
        """Lookup carrier and VoIP status for a phone number.

        Accepts: {number}
        Returns: {ok, carrier, line_type, is_voip, campaign_id?, risk_score}
        """
        body, _ = self._read_json_body(max_content_length=5_000)
        if body is None:
            return
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        try:
            from jarvis_engine.scam_hunter import (
                lookup_carrier_cached,
                load_campaigns,
            )
            from jarvis_engine.phone_guard import _normalize_number

            number = str(body.get("number", ""))
            normalized = _normalize_number(number)

            # Check carrier cache
            carrier_cache_path = root / ".planning" / "runtime" / "carrier_cache.json"
            carrier = lookup_carrier_cached(carrier_cache_path, normalized)

            # Check campaigns
            campaign_path = root / ".planning" / "runtime" / "scam_campaigns.json"
            campaigns = load_campaigns(campaign_path)
            campaign_id = ""
            campaign_confidence = 0.0
            campaign_signals: list[str] = []
            for c in campaigns:
                if normalized in c.numbers:
                    campaign_id = c.campaign_id
                    campaign_confidence = c.confidence
                    campaign_signals = c.signals
                    break

            result: dict[str, Any] = {
                "ok": True,
                "number": normalized,
                "carrier": carrier.carrier if carrier else "",
                "line_type": carrier.line_type if carrier else "",
                "is_voip": carrier.is_voip if carrier else False,
                "risk_score": carrier.risk_score if carrier else 0.0,
                "campaign_id": campaign_id,
                "campaign_confidence": round(campaign_confidence, 4),
                "campaign_signals": campaign_signals,
            }
            self._write_json(HTTPStatus.OK, result)
        except Exception as exc:
            logger.warning("Scam lookup failed: %s", exc)
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False, "number": str(body.get("number", "")),
                "carrier": "", "line_type": "", "is_voip": False, "error": str(exc),
            })

    def _handle_get_scam_campaigns(self) -> None:
        """Return detected scam campaigns."""
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        try:
            from jarvis_engine.scam_hunter import load_campaigns, build_prefix_block_actions
            from dataclasses import asdict

            campaign_path = root / ".planning" / "runtime" / "scam_campaigns.json"
            campaigns = load_campaigns(campaign_path)
            block_actions = build_prefix_block_actions(campaigns)

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "campaigns": [asdict(c) for c in campaigns],
                "block_actions": block_actions,
                "total_campaigns": len(campaigns),
                "total_scam_numbers": sum(len(c.numbers) for c in campaigns),
            })
        except Exception as exc:
            logger.warning("Scam campaigns fetch failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "campaigns": [], "block_actions": []})

    def _handle_get_scam_stats(self) -> None:
        """Return scam detection statistics."""
        root: Path = self.server.repo_root  # type: ignore[attr-defined]
        try:
            from jarvis_engine.scam_hunter import load_campaigns, load_call_intel

            campaign_path = root / ".planning" / "runtime" / "scam_campaigns.json"
            intel_path = root / ".planning" / "runtime" / "call_intel.jsonl"
            campaigns = load_campaigns(campaign_path)
            all_intel = load_call_intel(intel_path, limit=500)

            # Stats
            total_screened = len(all_intel)
            stir_failed = sum(1 for r in all_intel if r.get("stir_status") == "failed")
            stir_passed = sum(1 for r in all_intel if r.get("stir_status") == "passed")
            voip_calls = sum(1 for r in all_intel if r.get("line_type", "").endswith("voip"))
            blocked_numbers = set()
            for c in campaigns:
                if c.confidence >= 0.60:
                    blocked_numbers.update(c.numbers)

            # Top prefixes by campaign activity
            prefix_counts: dict[str, int] = {}
            for c in campaigns:
                prefix_counts[c.prefix] = prefix_counts.get(c.prefix, 0) + len(c.numbers)
            top_prefixes = sorted(prefix_counts.items(), key=lambda x: x[1], reverse=True)[:5]

            # Top carriers
            carrier_counts: dict[str, int] = {}
            for c in campaigns:
                if c.carrier:
                    carrier_counts[c.carrier] = carrier_counts.get(c.carrier, 0) + len(c.numbers)
            top_carriers = sorted(carrier_counts.items(), key=lambda x: x[1], reverse=True)[:5]

            self._write_json(HTTPStatus.OK, {
                "ok": True,
                "total_screened": total_screened,
                "stir_failed": stir_failed,
                "stir_passed": stir_passed,
                "voip_calls": voip_calls,
                "active_campaigns": len(campaigns),
                "total_scam_numbers": sum(len(c.numbers) for c in campaigns),
                "numbers_blocked": len(blocked_numbers),
                "top_scam_prefixes": [{"prefix": p, "numbers": n} for p, n in top_prefixes],
                "top_scam_carriers": [{"carrier": c, "numbers": n} for c, n in top_carriers],
            })
        except Exception as exc:
            logger.warning("Scam stats fetch failed: %s", exc)
            self._write_json(HTTPStatus.OK, {"ok": True, "total_screened": 0, "active_campaigns": 0})

    # Dispatch dict for POST routes — built once per class, O(1) lookup.
    _POST_DISPATCH: dict[str, str] = {
        "/bootstrap": "_handle_post_bootstrap",
        "/auth/login": "_handle_post_auth_login",
        "/auth/logout": "_handle_post_auth_logout",
        "/auth/lock": "_handle_post_auth_lock",
        "/processes/kill": "_handle_post_processes_kill",
        "/ingest": "_handle_post_ingest",
        "/settings": "_handle_post_settings",
        "/conversation/clear": "_handle_post_conversation_clear",
        "/command": "_handle_post_command",
        "/intelligence/merge": "_handle_post_intelligence_merge",
        "/intelligence/export": "_handle_post_intelligence_export",
        "/sync": "_handle_post_sync_deprecated",
        "/sync/pull": "_handle_post_sync_pull",
        "/sync/push": "_handle_post_sync_push",
        "/sync/config": "_handle_post_sync_config",
        "/smart-reply": "_handle_post_smart_reply",
        "/scam/report-call": "_handle_post_scam_report_call",
        "/scam/lookup": "_handle_post_scam_lookup",
        "/self-heal": "_handle_post_self_heal",
        "/feedback": "_handle_post_feedback",
        "/missions/create": "_handle_post_missions_create",
    }

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not self._check_rate_limit(path):
            return
        # Pre-read POST body so security pipeline can inspect it
        self._cached_post_body: bytes | None = None
        raw_cl = self.headers.get("Content-Length", "0")
        try:
            cl = int(raw_cl)
        except (TypeError, ValueError):
            cl = 0
        if cl > 0:
            try:
                self.connection.settimeout(15.0)
            except OSError:
                pass
            try:
                self._cached_post_body = self.rfile.read(cl)
            except (OSError, ConnectionError):
                self._cached_post_body = None
        # Security orchestrator pipeline check (with actual body)
        _security = getattr(self.server, "security", None)
        if _security is not None:
            _body_text = ""
            if self._cached_post_body:
                try:
                    _body_text = self._cached_post_body.decode("utf-8", errors="replace")
                except Exception:
                    _body_text = ""
            _client_ip = str(self.client_address[0])
            _sec_check = _security.check_request(
                path=path,
                source_ip=_client_ip,
                headers=dict(self.headers),
                body=_body_text,
                user_agent=self.headers.get("User-Agent", ""),
            )
            if not _sec_check["allowed"]:
                logger.warning("Security pipeline blocked POST %s: %s", path, _sec_check.get("reason", "unknown"))
                self._write_json(HTTPStatus.FORBIDDEN, {
                    "ok": False,
                    "error": "Request blocked by security policy",
                })
                return
        elif getattr(self.server, "_security_degraded", False) and path not in ("/health", "/auth/login"):
            # Fail closed: security subsystem failed to init — reject all
            # non-essential requests.
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Service unavailable: security subsystem failed to initialize",
            })
            return
        # O(1) dispatch for POST routes
        handler_name = self._POST_DISPATCH.get(path)
        if handler_name:
            getattr(self, handler_name)()
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

    # Initialize auto-sync config (relay URLs, sync scheduling, phone autonomy)
    try:
        from jarvis_engine.sync.auto_sync import AutoSyncConfig
        config_path = repo_root / ".planning" / "sync" / "auto_sync_config.json"
        server._auto_sync_config = AutoSyncConfig(config_path)
        # Auto-detect and store LAN URL
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
            proto = "https" if tls_active else "http"
            server._auto_sync_config.set("lan_url", f"{proto}://{lan_ip}:{port}")
        except OSError:
            pass
        logger.info("Auto-sync config initialized")
    except Exception as exc:
        logger.warning("Failed to initialize auto-sync config: %s", exc)

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
                _configure_db(sync_db)
                sync_lock = _threading.Lock()
                install_changelog_triggers(sync_db, device_id="desktop")
                # Use conflict strategy from auto-sync config
                conflict_strategy = "most_recent"
                if server._auto_sync_config is not None:
                    conflict_strategy = server._auto_sync_config.get(
                        "conflict_strategy", "most_recent",
                    )
                server._sync_engine = SyncEngine(
                    sync_db, sync_lock, device_id="desktop",
                    conflict_strategy=conflict_strategy,
                )
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
    logger.info("endpoints: GET /, GET /quick, GET /health, GET /cert-fingerprint, GET /auth/status, GET /settings, GET /dashboard, GET /audit, GET /security/status, GET /security/dashboard, GET /activity, GET /intelligence/growth, POST /bootstrap, POST /auth/login, POST /auth/logout, POST /auth/lock, POST /ingest, POST /settings, POST /command, POST /sync/pull, POST /sync/push, GET /sync/status, POST /self-heal")
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
        # Close security orchestrator DB connection
        if server._security_db is not None:
            try:
                server._security_db.close()
                logger.info("Security DB connection closed")
            except Exception as exc:
                logger.warning("Failed to close security DB: %s", exc)
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
