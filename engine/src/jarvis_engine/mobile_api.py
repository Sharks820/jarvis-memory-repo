from __future__ import annotations

import gzip as _gzip_mod
import hashlib
import hmac
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict

import sqlite3 as _sqlite3

if TYPE_CHECKING:
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine
    from jarvis_engine.security.orchestrator import SecurityOrchestrator
    from jarvis_engine.security.owner_session import OwnerSessionManager
    from jarvis_engine.sync.auto_sync import AutoSyncConfig
    from jarvis_engine.sync.engine import SyncEngine
    from jarvis_engine.sync.transport import SyncTransport

from jarvis_engine._constants import (
    MAX_AUTH_BODY_SIZE,
    MAX_COMMAND_RESPONSE_CHARS,
    MAX_COMMAND_RESPONSE_CHUNK_CHARS,
    MAX_COMMAND_RESPONSE_CHUNKS,
    MAX_COMMAND_STDOUT_LINE_CHARS,
    MAX_COMMAND_STDOUT_TAIL_LINES,
    MAX_NONCES,
    REPLAY_WINDOW_SECONDS,
    SUBSYSTEM_ERRORS,
)
from jarvis_engine._shared import memory_db_path
from jarvis_engine._shared import runtime_dir
from jarvis_engine.ingest import IngestionPipeline
from jarvis_engine.owner_guard import read_owner_guard, trust_mobile_device, verify_master_password
from jarvis_engine.mobile_routes import (
    AuthRoutesMixin,
    CommandRoutesMixin,
    DataRoutesMixin,
    HealthRoutesMixin,
    IntelligenceRoutesMixin,
    ScamRoutesMixin,
    SecurityRoutesMixin,
    SyncRoutesMixin,
    VoiceCommandMixin,
)
logger = logging.getLogger(__name__)

# Alias for backward compatibility — prefer importing SUBSYSTEM_ERRORS from _constants.
_SUBSYSTEM_ERRORS = SUBSYSTEM_ERRORS


class CLIResult(TypedDict, total=False):
    """Typed return value for ``_run_main_cli``."""

    ok: bool
    error: str
    command_exit_code: int
    stdout_tail: list[str]
    stderr_tail: list[str]


# CORS whitelist: only allow localhost/loopback origins and file:// protocol.
# LAN IPs are added dynamically at server startup via _build_cors_whitelist().
_CORS_ALLOWED_ORIGIN_PATTERNS = [
    re.compile(r"^https?://localhost(:\d+)?$"),
    re.compile(r"^https?://127\.0\.0\.1(:\d+)?$"),
    re.compile(r"^https?://\[::1\](:\d+)?$"),
    re.compile(r"^file:///[A-Za-z]:/"),  # Only local file:// URIs with drive letter
]

# Rate-limit configuration
_RATE_BUCKET_PRUNE_THRESHOLD = 5000  # evict stale entries when bucket exceeds this size

class _RateLimitConfig:
    """Describes a sliding-window rate-limit bucket."""

    __slots__ = ("bucket_attr", "lock_attr", "max_attempts", "window_seconds")

    def __init__(
        self,
        bucket_attr: str,
        lock_attr: str,
        max_attempts: int,
        window_seconds: float,
    ) -> None:
        self.bucket_attr = bucket_attr
        self.lock_attr = lock_attr
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds


_BOOTSTRAP_RATE = _RateLimitConfig("_bootstrap_attempts", "_bootstrap_rate_lock", 5, 60.0)
_MASTER_PW_RATE = _RateLimitConfig("_master_pw_attempts", "_master_pw_rate_lock", 5, 60.0)
_API_RATE_NORMAL = _RateLimitConfig("_api_rate_normal", "_api_rate_lock", 120, 60.0)
_API_RATE_EXPENSIVE = _RateLimitConfig("_api_rate_expensive", "_api_rate_lock", 10, 60.0)

_EXPENSIVE_PATHS = {"/command", "/self-heal", "/auth/login", "/feedback"}

# Public endpoints with no body and no auth — skip the full security pipeline.
# Rate limiting already protects these from abuse.
_PUBLIC_SAFE_PATHS = frozenset({"/health", "/cert-fingerprint"})




def _detect_lan_ips() -> list[str]:
    """Detect local LAN IP addresses for SAN entries."""
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            if lan_ip and lan_ip not in ips:
                ips.append(lan_ip)
    except OSError as exc:
        logger.debug("LAN IP detection failed: %s", exc)
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

    # Attempt to generate using openssl (atomic write for TLS config)
    ext_tmp = ext_file.with_suffix(".cnf.tmp")
    try:
        ext_tmp.write_text(ext_content, encoding="utf-8")
        os.replace(str(ext_tmp), str(ext_file))
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
        for p in (cert_path, key_path, ext_file, ext_tmp):
            try:
                if p.exists():
                    p.unlink()
            except OSError as cleanup_exc:
                logger.debug("Failed to clean up partial TLS file %s: %s", p, cleanup_exc)
        return None, None
    finally:
        # Clean up the temporary extension files
        for p in (ext_file, ext_tmp):
            try:
                if p.exists():
                    p.unlink()
            except OSError as cleanup_exc:
                logger.debug("Failed to clean up TLS extension file %s: %s", p, cleanup_exc)

    if cert_path.exists() and key_path.exists():
        logger.info("Generated self-signed TLS certificate with SAN=%s: %s", san_string, cert_path)
        return str(cert_path), str(key_path)

    return None, None


class MobileIngestServer(ThreadingHTTPServer):
    allow_reuse_address = True
    _MAX_CONCURRENT = 32

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
        self._thread_semaphore = threading.Semaphore(self._MAX_CONCURRENT)
        self.auth_token = auth_token
        self.signing_key = signing_key
        self.pipeline = pipeline
        self.repo_root = repo_root
        self.tls_active = False
        self._setup_sync_state()
        self._setup_nonce_tracking(repo_root)
        self._setup_rate_limiters()
        self._setup_security(repo_root)
        self._setup_owner_session()

    # -- Private setup helpers ------------------------------------------------

    def _setup_sync_state(self) -> None:
        """Initialise sync engine and memory engine lazy-init state."""
        self._sync_engine: SyncEngine | None = None
        self._sync_transport: SyncTransport | None = None
        self._sync_init_attempted = False
        self._sync_init_lock = threading.Lock()
        self._auto_sync_config: AutoSyncConfig | None = None
        self._memory_engine: MemoryEngine | None = None
        self._memory_engine_init_lock = threading.Lock()
        self._embed_service: EmbeddingService | None = None
        self._embed_init_lock = threading.Lock()

    def _setup_nonce_tracking(self, repo_root: Path) -> None:
        """Initialise HMAC nonce replay-protection state."""
        self.nonce_seen: dict[str, float] = {}
        self.nonce_lock = threading.RLock()
        self.next_nonce_cleanup_ts = 0.0
        self.nonce_cleanup_interval_s = 30.0
        self._nonce_cache_path = runtime_dir(repo_root) / "nonce_cache.jsonl"
        self._load_nonces()

    def _setup_rate_limiters(self) -> None:
        """Initialise all rate-limiter buckets and locks."""
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

    def _setup_security(self, repo_root: Path) -> None:
        """Initialise SecurityOrchestrator -- FAIL CLOSED on error."""
        self.security: SecurityOrchestrator | None = None
        self._security_db: _sqlite3.Connection | None = None
        self._security_write_lock: threading.Lock | None = None
        self._security_degraded: bool = False
        try:
            from jarvis_engine._db_pragmas import connect_db as _connect_db
            from jarvis_engine.security.orchestrator import SecurityOrchestrator
            security_db_path = repo_root / ".planning" / "brain" / "security.db"
            security_db_path.parent.mkdir(parents=True, exist_ok=True)
            self._security_db = _connect_db(security_db_path, full=True, check_same_thread=False)
            self._security_write_lock = threading.Lock()
            forensic_dir = runtime_dir(repo_root) / "forensic"
            forensic_dir.mkdir(parents=True, exist_ok=True)
            def _rotate_signing_key(new_key: str) -> None:
                self.signing_key = new_key
                logger.warning("Server signing key rotated by containment engine")

            self.security = SecurityOrchestrator(
                db=self._security_db,
                write_lock=self._security_write_lock,
                log_dir=forensic_dir,
                on_credential_rotate=_rotate_signing_key,
            )
            logger.info("SecurityOrchestrator initialized for mobile API")
        except _SUBSYSTEM_ERRORS as exc:
            logger.error("SecurityOrchestrator init FAILED — server will reject non-essential requests: %s", exc)
            self.security = None
            self._security_degraded = True

    def _setup_owner_session(self) -> None:
        """Initialise OwnerSessionManager -- FAIL CLOSED on error."""
        self.owner_session: OwnerSessionManager | None = None
        self._session_degraded: bool = False
        try:
            from jarvis_engine.security.owner_session import OwnerSessionManager
            self.owner_session = OwnerSessionManager(
                session_timeout=int(os.environ.get("JARVIS_SESSION_TIMEOUT", "1800")),
            )
            logger.info("OwnerSessionManager initialized for mobile API")
            # Share with SecurityOrchestrator to avoid duplicate instances
            if self.security is not None:
                self.security.owner_session = self.owner_session
        except _SUBSYSTEM_ERRORS as exc:
            logger.error("OwnerSessionManager init FAILED — session auth will be unavailable: %s", exc)
            self.owner_session = None
            self._session_degraded = True

    def process_request(self, request: Any, client_address: Any) -> None:
        """Override to cap concurrent request threads via semaphore.

        If the semaphore cannot be acquired within 5 seconds, the connection
        is rejected with a 503 Service Unavailable response.
        """
        if not self._thread_semaphore.acquire(timeout=5.0):
            # Reject: too many concurrent connections
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except OSError:
                logger.debug("Client already disconnected; 503 not sent")
            finally:
                try:
                    self.shutdown_request(request)
                except OSError:
                    logger.debug("Socket already closed during cleanup")
            return
        # NOTE: super().process_request() spawns a thread and returns immediately.
        # We must release the semaphore AFTER the thread finishes, not here.
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        """Override to release semaphore after the request thread completes."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._thread_semaphore.release()

    @staticmethod
    def _prune_rate_dict(rate_dict: dict[str, list[float]], max_keys: int = 5000) -> None:
        """Remove the oldest half of entries when the dict exceeds max_keys.

        Prevents unbounded memory growth from unique IPs over time.
        Each value is a list of timestamps; the 'oldest' entry is determined
        by the maximum timestamp in each list (most recent activity).
        """
        if len(rate_dict) <= max_keys:
            return
        # Sort IPs by their most recent attempt timestamp, ascending
        by_recency = sorted(rate_dict.keys(), key=lambda ip: max(rate_dict[ip]) if rate_dict[ip] else 0.0)
        to_remove = len(rate_dict) // 2
        for ip in by_recency[:to_remove]:
            del rate_dict[ip]

    def check_rate(
        self,
        cfg: "_RateLimitConfig",
        key: str,
        *,
        record_on_allow: bool = False,
    ) -> bool:
        """Generic sliding-window rate limiter.

        Returns True if *key* is rate-limited (i.e. has reached
        ``cfg.max_attempts`` within the last ``cfg.window_seconds``).

        When *record_on_allow* is True the current timestamp is appended to
        the window **only** when the request is allowed (not rate-limited).
        This is used by the API rate limiter so that rejected requests do not
        consume future budget.
        """
        bucket: dict[str, list[float]] = getattr(self, cfg.bucket_attr)
        lock: threading.Lock = getattr(self, cfg.lock_attr)
        now = time.time()
        with lock:
            if len(bucket) > _RATE_BUCKET_PRUNE_THRESHOLD:
                self._prune_rate_dict(bucket)
            attempts = bucket.get(key, [])
            cutoff = now - cfg.window_seconds
            attempts = [ts for ts in attempts if ts > cutoff]
            if len(attempts) >= cfg.max_attempts:
                bucket[key] = attempts
                return True
            if record_on_allow:
                attempts.append(now)
            bucket[key] = attempts
            return False

    def record_attempt(self, cfg: "_RateLimitConfig", key: str) -> None:
        """Record a rate-limit attempt (used after a failed auth event)."""
        bucket: dict[str, list[float]] = getattr(self, cfg.bucket_attr)
        lock: threading.Lock = getattr(self, cfg.lock_attr)
        now = time.time()
        with lock:
            attempts = bucket.get(key, [])
            cutoff = now - cfg.window_seconds
            attempts = [ts for ts in attempts if ts > cutoff]
            attempts.append(now)
            bucket[key] = attempts

    # -- Public convenience wrappers (preserve existing call-site API) ------

    def check_bootstrap_rate(self, client_ip: str) -> bool:
        """Return True if this IP is rate-limited for bootstrap attempts."""
        return self.check_rate(_BOOTSTRAP_RATE, client_ip)

    def record_bootstrap_attempt(self, client_ip: str) -> None:
        """Record a failed bootstrap attempt for rate limiting."""
        self.record_attempt(_BOOTSTRAP_RATE, client_ip)

    def check_master_pw_rate(self, client_ip: str) -> bool:
        """Return True if this IP is rate-limited for master password attempts."""
        return self.check_rate(_MASTER_PW_RATE, client_ip)

    def record_master_pw_attempt(self, client_ip: str) -> None:
        """Record a master password attempt for rate limiting."""
        self.record_attempt(_MASTER_PW_RATE, client_ip)

    def check_api_rate(self, client_ip: str, path: str) -> bool:
        """Return True if this IP exceeds the API rate limit for the given path.

        Uses **separate** counters for expensive paths (/command, /self-heal)
        vs normal paths so that widget polling doesn't consume the expensive
        tier's budget.

        Only records the request if it is NOT rate-limited, so rejected
        requests do not consume future budget.
        """
        cfg = _API_RATE_EXPENSIVE if path in _EXPENSIVE_PATHS else _API_RATE_NORMAL
        return self.check_rate(cfg, client_ip, record_on_allow=True)

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
                        logger.debug("Skipping malformed nonce cache entry")
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
            except OSError as cleanup_exc:
                logger.debug("Failed to remove nonce cache temp file: %s", cleanup_exc)

    def ensure_sync_engine(self) -> SyncEngine | None:
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
            db_path = memory_db_path(self.repo_root)
            if not db_path.exists():
                return None
            try:
                from jarvis_engine.sync.changelog import install_changelog_triggers
                from jarvis_engine.sync.engine import SyncEngine
                from jarvis_engine.sync.transport import SyncTransport

                from jarvis_engine._db_pragmas import connect_db as _connect_sync_db

                sync_db = _connect_sync_db(db_path, full=True, check_same_thread=False)
                try:
                    sync_lock = threading.Lock()
                    install_changelog_triggers(sync_db, device_id="desktop")
                    # Load conflict strategy from auto-sync config
                    conflict_strategy = "most_recent"
                    if self._auto_sync_config is not None:
                        conflict_strategy = self._auto_sync_config.get(
                            "conflict_strategy", "most_recent",
                        )
                    engine = SyncEngine(
                        sync_db, sync_lock, device_id="desktop",
                        conflict_strategy=conflict_strategy,
                    )
                    # Build transport BEFORE committing to self._sync_engine so
                    # a transport failure doesn't leave a half-initialized state.
                    transport = None
                    if self.signing_key:
                        salt_path = self.repo_root / ".planning" / "brain" / "sync_salt.bin"
                        transport = SyncTransport(self.signing_key, salt_path)
                    # Both succeeded — commit.
                    self._sync_engine = engine
                    if transport is not None:
                        self._sync_transport = transport
                    self._sync_init_attempted = True
                    logger.info("Sync engine lazy-initialized for mobile API")
                except (_sqlite3.Error, OSError) as exc:
                    logger.debug("Sync engine/transport init failed, closing DB: %s", exc)
                    sync_db.close()
                    raise
            except _SUBSYSTEM_ERRORS as exc:
                logger.warning("Failed to lazy-initialize sync: %s", exc)
                # Do NOT set _sync_init_attempted so future calls can retry.
            return self._sync_engine

    def ensure_memory_engine(self) -> MemoryEngine | None:
        """Lazy-initialize a MemoryEngine for read-only metric queries.

        Returns the MemoryEngine instance, or None if the DB doesn't exist
        or initialization fails.
        """
        if self._memory_engine is not None:
            return self._memory_engine
        with self._memory_engine_init_lock:
            if self._memory_engine is not None:
                return self._memory_engine
            db_path = memory_db_path(self.repo_root)
            if not db_path.exists():
                return None
            try:
                from jarvis_engine.memory.engine import MemoryEngine
                self._memory_engine = MemoryEngine(db_path)
                logger.info("MemoryEngine lazy-initialized for mobile API metrics")
            except _SUBSYSTEM_ERRORS as exc:
                logger.warning("Failed to lazy-initialize MemoryEngine: %s", exc)
            return self._memory_engine



class MobileIngestHandler(
    HealthRoutesMixin,
    AuthRoutesMixin,
    SyncRoutesMixin,
    IntelligenceRoutesMixin,
    CommandRoutesMixin,
    DataRoutesMixin,
    ScamRoutesMixin,
    SecurityRoutesMixin,
    VoiceCommandMixin,
    BaseHTTPRequestHandler,
):
    server: MobileIngestServer  # type: ignore[assignment]  # narrow from HTTPServer
    server_version = "JarvisMobileAPI/0.1"

    # Credential endpoints contain passwords/tokens that look like base64
    # — skip body injection scan for them (they have their own auth checks).
    _BODY_SCAN_EXEMPT_PATHS = frozenset(
        {"/bootstrap", "/auth/login", "/auth/logout", "/auth/lock", "/sync/push"}
    )
    _cached_post_body: bytes | None = None

    @property
    def _root(self) -> Path:
        """Shortcut for the server's repository root."""
        return self.server.repo_root

    def _cors_headers(self) -> None:
        """Add CORS headers to every response for browser-based clients.

        Only whitelisted origins (localhost, 127.0.0.1, ::1, file://, and
        any configured LAN IP) are reflected.  Unknown origins receive no
        Access-Control-Allow-Origin header, effectively blocking CORS.
        """
        origin = self.headers.get("Origin", "")
        server = self.server
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
        correlation_id = payload.get("correlation_id")
        if isinstance(correlation_id, str) and correlation_id:
            self.send_header("X-Jarvis-Correlation-Id", correlation_id[:64])
        diagnostic_id = payload.get("diagnostic_id")
        if isinstance(diagnostic_id, str) and diagnostic_id:
            self.send_header("X-Jarvis-Diagnostic-Id", diagnostic_id[:64])
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

    def _error_payload(
        self,
        message: str,
        *,
        ok: bool = False,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": ok, "error": message}
        payload.update(extra)
        return payload

    def _write_error(
        self,
        status: int,
        message: str,
        **extra: Any,
    ) -> None:
        self._write_json(status, self._error_payload(message, **extra))

    def _command_failure_result(
        self,
        *,
        correlation_id: str,
        error: str,
        error_code: str,
        category: str,
        user_hint: str,
        retryable: bool,
        command_exit_code: int = 2,
        intent: str = "execution_error",
        reason: str = "",
        status_code: str = "",
        stdout_tail: list[str] | None = None,
        stderr_tail: list[str] | None = None,
        response: str = "",
    ) -> dict[str, Any]:
        diagnostic_id = correlation_id[:12]
        return {
            "ok": False,
            "lifecycle_state": "failed",
            "correlation_id": correlation_id,
            "diagnostic_id": diagnostic_id,
            "error": error,
            "error_code": error_code,
            "category": category,
            "retryable": bool(retryable),
            "user_hint": user_hint,
            "command_exit_code": int(command_exit_code),
            "intent": intent,
            "status_code": str(status_code or command_exit_code),
            "reason": reason,
            "response": response,
            "stdout_tail": stdout_tail or [],
            "stderr_tail": stderr_tail or [],
        }

    def _normalize_command_output(
        self,
        *,
        response_text: str,
        stdout_lines: list[str],
    ) -> dict[str, Any]:
        response_text = response_text or ""
        stdout_lines = stdout_lines or []

        response_truncated = len(response_text) > MAX_COMMAND_RESPONSE_CHARS
        if response_truncated:
            response_text = response_text[:MAX_COMMAND_RESPONSE_CHARS]

        response_chunks = [
            response_text[i:i + MAX_COMMAND_RESPONSE_CHUNK_CHARS]
            for i in range(0, len(response_text), MAX_COMMAND_RESPONSE_CHUNK_CHARS)
        ][:MAX_COMMAND_RESPONSE_CHUNKS]

        normalized_stdout: list[str] = []
        stdout_truncated = False
        for line in stdout_lines:
            if len(normalized_stdout) >= MAX_COMMAND_STDOUT_TAIL_LINES:
                stdout_truncated = True
                break
            if len(line) > MAX_COMMAND_STDOUT_LINE_CHARS:
                normalized_stdout.append(line[:MAX_COMMAND_STDOUT_LINE_CHARS])
                stdout_truncated = True
            else:
                normalized_stdout.append(line)

        return {
            "response": response_text,
            "response_chunks": response_chunks,
            "response_truncated": response_truncated,
            "stdout_tail": normalized_stdout,
            "stdout_truncated": stdout_truncated,
        }

    def _log_command_lifecycle_event(
        self,
        *,
        lifecycle_state: str,
        correlation_id: str,
        payload: dict[str, Any],
        result: dict[str, Any] | None = None,
    ) -> None:
        try:
            from jarvis_engine.activity_feed import ActivityCategory, log_activity

            text = str(payload.get("text", "")).strip()
            details: dict[str, Any] = {
                "correlation_id": correlation_id,
                "lifecycle_state": lifecycle_state,
                "command_len": len(text),
                "command_preview": text[:120],
            }
            if result is not None:
                details.update(
                    {
                        "ok": bool(result.get("ok", False)),
                        "intent": str(result.get("intent", ""))[:80],
                        "status_code": str(result.get("status_code", ""))[:32],
                        "error_code": str(result.get("error_code", ""))[:80],
                        "retryable": bool(result.get("retryable", False)),
                        "diagnostic_id": str(result.get("diagnostic_id", ""))[:64],
                    }
                )
            log_activity(
                ActivityCategory.COMMAND_LIFECYCLE,
                f"Command {lifecycle_state}",
                details,
            )
        except _SUBSYSTEM_ERRORS as exc:
            # Activity feed must never break command execution.
            logger.debug("Activity feed logging failed: %s", exc)

    def _run_main_cli(self, args: list[str], *, timeout_s: int = 240) -> CLIResult:
        root: Path = getattr(self, "_root", None) or self.server.repo_root
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
        self._write_error(HTTPStatus.UNAUTHORIZED, message)

    def _read_json_body(
        self, *, max_content_length: int, auth: bool = True,
    ) -> tuple[dict[str, Any] | None, bytes | None]:
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
                self._write_error(HTTPStatus.BAD_REQUEST, "Invalid content length.")
                return None, None

            try:
                self.connection.settimeout(15.0)
            except OSError:
                self._write_error(HTTPStatus.BAD_REQUEST, "Connection closed.")
                return None, None
            try:
                body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            except (OSError, ConnectionError):
                self._write_error(HTTPStatus.BAD_REQUEST, "Connection reset during read.")
                return None, None

        min_length = 1 if auth else 0
        if content_length < min_length or content_length > max_content_length:
            self._write_error(HTTPStatus.BAD_REQUEST, "Invalid content length.")
            return None, None

        if auth and not self._validate_auth(body):
            return None, None

        try:
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "Invalid UTF-8 body.")
            return None, None
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "Invalid JSON.")
            return None, None
        if not isinstance(payload, dict):
            self._write_error(HTTPStatus.BAD_REQUEST, "Invalid JSON payload.")
            return None, None
        return payload, body

    def _cleanup_nonces_unlocked(self, now: float, *, force: bool = False) -> bool:
        """Purge expired nonces.  Caller MUST hold ``nonce_lock``.

        Returns *True* if nonces were actually cleaned (caller should persist).
        """
        interval = float(getattr(self.server, "nonce_cleanup_interval_s", 30.0))  # type: ignore[attr-defined]
        next_cleanup = float(getattr(self.server, "next_nonce_cleanup_ts", 0.0))  # type: ignore[attr-defined]
        if not force and now < next_cleanup:
            return False
        nonce_seen: dict[str, float] = self.server.nonce_seen
        cutoff = now - REPLAY_WINDOW_SECONDS
        valid_nonces = {k: v for k, v in nonce_seen.items() if v >= cutoff}
        nonce_seen.clear()
        nonce_seen.update(valid_nonces)
        self.server.next_nonce_cleanup_ts = now + interval
        return True

    def _cleanup_nonces(self, now: float, *, force: bool = False) -> None:
        with self.server.nonce_lock:
            should_persist = self._cleanup_nonces_unlocked(now, force=force)
        if should_persist:
            self.server._persist_nonces()

    def _validate_bearer_token(self, body: bytes) -> bool:
        """Check body size and bearer token validity."""
        if len(body) > MAX_AUTH_BODY_SIZE:
            self._unauthorized("Request body too large.")
            return False
        auth = self.headers.get("Authorization", "")
        expected_auth = f"Bearer {self.server.auth_token}"
        if not hmac.compare_digest(auth, expected_auth):
            self._unauthorized("Invalid bearer token.")
            return False
        return True

    def _validate_hmac_signature(self, body: bytes) -> tuple[bool, str, str, float]:
        """Parse and validate timestamp, nonce, and HMAC signature.

        Returns ``(ok, ts_raw, nonce, now)``.  On failure, sends the
        appropriate HTTP error and returns ``(False, "", "", 0.0)``.
        """
        _fail = (False, "", "", 0.0)
        ts_raw = self.headers.get("X-Jarvis-Timestamp", "").strip()
        nonce = self.headers.get("X-Jarvis-Nonce", "").strip()
        if not ts_raw or not nonce:
            self._unauthorized("Missing replay-protection headers.")
            return _fail
        if len(nonce) < 8 or len(nonce) > 128 or (not nonce.isascii()):
            self._unauthorized("Invalid nonce.")
            return _fail
        # Timestamps MUST be integers (no decimal point).  Float timestamps
        # leak sub-second precision and violate the HMAC signing contract.
        if "." in ts_raw:
            self._unauthorized("Timestamp must be an integer (no decimal point).")
            return _fail
        try:
            ts = int(ts_raw)
        except (ValueError, OverflowError):
            self._unauthorized("Invalid timestamp.")
            return _fail
        now = time.time()
        if abs(now - ts) > REPLAY_WINDOW_SECONDS:
            self._unauthorized("Expired timestamp.")
            return _fail

        signature = self.headers.get("X-Jarvis-Signature", "").strip().lower()
        # Signing material format: "<timestamp>\n<nonce>\n<body_bytes>"
        # All clients (mobile, desktop widget, tests) MUST produce the same
        # byte sequence: timestamp as UTF-8 string, newline, nonce as UTF-8,
        # newline, then the raw request body bytes (no trailing newline).
        signing_material = ts_raw.encode("utf-8") + b"\n" + nonce.encode("utf-8") + b"\n" + body
        expected_sig = hmac.new(
            self.server.signing_key.encode("utf-8"),
            signing_material,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            self._unauthorized("Invalid request signature.")
            return _fail
        return (True, ts_raw, nonce, now)

    def _check_and_commit_nonce(self, nonce: str, now: float) -> bool:
        """Atomically check nonce freshness and commit it in a single lock.

        The nonce is checked and recorded in one critical section to eliminate
        the TOCTOU race that existed when check and commit were separate lock
        acquisitions.  A consumed nonce on a subsequently-rejected request
        (e.g. owner-guard failure) is the correct security behavior -- it
        prevents attackers from replaying the same nonce to probe other checks.
        """
        with self.server.nonce_lock:
            nonce_seen: dict[str, float] = self.server.nonce_seen
            # Use _cleanup_nonces_unlocked to avoid nested lock acquisition (M2 fix).
            self._cleanup_nonces_unlocked(now)
            if len(nonce_seen) >= MAX_NONCES:
                self._cleanup_nonces_unlocked(now, force=True)
            if len(nonce_seen) >= MAX_NONCES:
                self._unauthorized("Replay cache saturated.")
                return False
            if nonce in nonce_seen:
                self._unauthorized("Replay detected.")
                return False
            # Commit the nonce atomically -- no window for replay.
            nonce_seen[nonce] = now
        return True

    def _check_owner_guard(self) -> bool:
        """Validate the requesting device against the owner guard config.

        Returns *True* if owner guard is disabled or the device is trusted
        (possibly after master-password based trust).
        """
        owner_guard = read_owner_guard(self._root)
        if not bool(owner_guard.get("enabled", False)):
            return True

        trusted = {
            str(device_id).strip()
            for device_id in owner_guard.get("trusted_mobile_devices", [])
            if str(device_id).strip()
        }
        device_id = self.headers.get("X-Jarvis-Device-Id", "").strip()
        if not device_id or len(device_id) > 128 or (not device_id.isascii()):
            self._unauthorized("Missing trusted mobile device id.")
            return False
        if device_id in trusted:
            return True

        master_password = self.headers.get("X-Jarvis-Master-Password", "").strip()
        if not master_password:
            self._unauthorized("Untrusted mobile device.")
            return False

        client_ip = str(self.client_address[0]).strip()
        server = self.server
        if server.check_master_pw_rate(client_ip):
            self._write_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "Too many master password attempts. Try again later.",
            )
            return False
        server.record_master_pw_attempt(client_ip)
        if verify_master_password(self._root, master_password):
            trust_mobile_device(self._root, device_id)
            return True
        self._unauthorized("Untrusted mobile device.")
        return False

    def _validate_auth(self, body: bytes) -> bool:
        if not self._validate_bearer_token(body):
            return False

        ok, ts_raw, nonce, now = self._validate_hmac_signature(body)
        if not ok:
            return False

        # Atomically check and commit the nonce in a single lock acquisition
        # to eliminate the TOCTOU replay window.
        if not self._check_and_commit_nonce(nonce, now):
            return False

        if not self._check_owner_guard():
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
                self._write_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "Service unavailable: session subsystem failed to initialize",
                )
                return False
            # No session token — fall through to HMAC auth
            return self._validate_auth(body)

        session_token = self.headers.get("X-Jarvis-Session", "").strip()
        owner_session = getattr(self.server, "owner_session", None)
        if session_token and owner_session and owner_session.validate_session(session_token):
            # Session token is valid — now enforce device trust
            owner_guard = read_owner_guard(self._root)
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

    # GET handler methods (extracted for O(1) dispatch-dict routing)

    # Dispatch dict for GET routes — built once per class, O(1) lookup.
    _GET_DISPATCH: ClassVar[dict[str, str]] = {
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
        "/conversation/state": "_handle_get_conversation_state",
        "/missions/status": "_handle_get_missions_status",
        "/missions/active": "_handle_get_missions_active",
        "/missions/steps": "_handle_get_missions_steps",
        "/alerts/pending": "_handle_get_alerts_pending",
        "/digest": "_handle_get_digest",
        "/meeting-prep": "_handle_get_meeting_prep",
        "/scam/campaigns": "_handle_get_scam_campaigns",
        "/scam/stats": "_handle_get_scam_stats",
        "/voice/latency": "_handle_get_voice_latency",
        "/gateway/health": "_handle_get_gateway_health",
        "/gateway/budget": "_handle_get_gateway_budget",
        "/memory/hygiene": "_handle_get_memory_hygiene",
        "/diagnostics/status": "_handle_get_diagnostics",
        "/favicon.ico": "_handle_get_favicon",
    }

    # Paths exempt from rate limiting (public/unauthenticated GET endpoints)
    _GET_RATE_LIMIT_EXEMPT = frozenset({"/", "/quick", "/health", "/cert-fingerprint", "/auth/status", "/favicon.ico"})

    def _run_security_check(self, path: str, body: str = "") -> bool:
        """Run the security orchestrator pipeline and write error responses.

        Returns *True* if the request is allowed, *False* if it was blocked
        (a 403/503 response has already been sent).
        """
        _security = getattr(self.server, "security", None)
        if _security is not None:
            _client_ip = str(self.client_address[0])
            _scan_body = "" if path in self._BODY_SCAN_EXEMPT_PATHS else body
            _sec_check = _security.check_request(
                path=path,
                source_ip=_client_ip,
                headers=dict(self.headers),
                body=_scan_body,
                user_agent=self.headers.get("User-Agent", ""),
            )
            if not _sec_check["allowed"]:
                logger.warning("Security pipeline blocked %s: %s", path, _sec_check.get("reason", "unknown"))
                self._write_error(HTTPStatus.FORBIDDEN, "Request blocked by security policy")
                return False
        elif getattr(self.server, "_security_degraded", False) and path not in ("/health", "/auth/login"):
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Service unavailable: security subsystem failed to initialize",
            )
            return False
        return True

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
            if not self._run_security_check(path):
                return
        # O(1) dispatch for remaining GET routes
        handler_name = self._GET_DISPATCH.get(path)
        if handler_name:
            getattr(self, handler_name)()
            return
        self._write_error(HTTPStatus.NOT_FOUND, "Not found")
        return

    def _check_rate_limit(self, path: str) -> bool:
        """Check global API rate limit. Returns True if request should proceed."""
        client_ip = str(self.client_address[0]).strip()
        server = self.server
        if server.check_api_rate(client_ip, path):
            self._write_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "Rate limit exceeded. Try again later.",
            )
            return False
        return True

    # POST handler methods (extracted for O(1) dispatch-dict routing)

    # ── Mission endpoints ────────────────────────────────────────────────

    # ── Alert queue endpoint (phone polls this) ───────────────────────────

    # ── Digest endpoint (summarize what you missed) ──────────────────────

    # ── Meeting prep endpoint (KG-powered) ───────────────────────────────

    # ── Smart reply endpoint ─────────────────────────────────────────────

    # ── Scam Campaign Hunter endpoints ──────────────────────────────────

    # Dispatch dict for POST routes — built once per class, O(1) lookup.
    _POST_DISPATCH: ClassVar[dict[str, str]] = {
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
        "/missions/pause": "_handle_post_missions_pause",
        "/missions/resume": "_handle_post_missions_resume",
        "/missions/restart": "_handle_post_missions_restart",
    }

    # Endpoint-specific POST body size limits (bytes).
    # /sync/push is allowed 2 MB; all others default to 1 MB.
    _POST_BODY_LIMITS: ClassVar[dict[str, int]] = {
        "/sync/push": 2_000_000,
    }
    _DEFAULT_POST_BODY_LIMIT: int = 1_000_000

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not self._check_rate_limit(path):
            return
        # Pre-read POST body so security pipeline can inspect it.
        # Enforce endpoint-specific Content-Length limits before reading
        # the full body to reject oversized payloads early.
        max_body = self._POST_BODY_LIMITS.get(path, self._DEFAULT_POST_BODY_LIMIT)
        self._cached_post_body = None
        raw_cl = self.headers.get("Content-Length", "0")
        try:
            cl = int(raw_cl)
        except (TypeError, ValueError):
            cl = 0
        if cl > max_body:
            self._write_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"Request body too large (limit {max_body} bytes).",
            )
            return
        if cl > 0:
            try:
                self.connection.settimeout(15.0)
            except OSError as exc:
                logger.debug("Failed to set connection timeout: %s", exc)
            try:
                self._cached_post_body = self.rfile.read(cl)
            except (OSError, ConnectionError) as exc:
                logger.warning("POST body read failed: %s", exc)
                self._write_error(HTTPStatus.BAD_REQUEST, "Failed to read request body.")
                return
        # Security orchestrator pipeline check (with actual body)
        _body_text = self._cached_post_body.decode("utf-8", errors="replace") if self._cached_post_body else ""
        if not self._run_security_check(path, body=_body_text):
            return
        # O(1) dispatch for POST routes
        handler_name = self._POST_DISPATCH.get(path)
        if handler_name:
            getattr(self, handler_name)()
            return
        self._write_error(HTTPStatus.NOT_FOUND, "Not found")
        return

    def log_message(self, fmt: str, *args: object) -> None:
        """Suppress BaseHTTPRequestHandler's default stderr logging.

        Mobile API traffic is high-volume; logging each request to stderr
        would overwhelm the console.  Meaningful events are logged via the
        module logger at appropriate levels instead.
        """


# ---------------------------------------------------------------------------
# Backward-compat re-exports — lifecycle functions moved to
# jarvis_engine.mobile_api_lifecycle for file-health / desloppify.
# ---------------------------------------------------------------------------
from jarvis_engine.mobile_api_lifecycle import (  # noqa: F401, E402
    _resolve_tls,
    _check_non_loopback_bind,
    _init_auto_sync_config,
    _init_sync_engine,
    _add_cors_lan_origin,
    _wrap_tls_socket,
    _log_startup_info,
    _start_bus_prewarm,
    _shutdown_server,
    run_mobile_server,
)
