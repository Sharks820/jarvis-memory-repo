from __future__ import annotations

import gzip as _gzip_mod
import hashlib
import hmac
import io
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    import sqlite3

    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine
    from jarvis_engine.security.orchestrator import SecurityOrchestrator
    from jarvis_engine.security.owner_session import OwnerSessionManager
    from jarvis_engine.sync.auto_sync import AutoSyncConfig
    from jarvis_engine.sync.engine import SyncEngine
    from jarvis_engine.sync.transport import SyncTransport

from jarvis_engine._constants import ACTIONS_FILENAME as _ACTIONS_FILENAME
from jarvis_engine._constants import OPS_SNAPSHOT_FILENAME as _OPS_SNAPSHOT_FILENAME
from jarvis_engine._constants import memory_db_path as _memory_db_path
from jarvis_engine._constants import runtime_dir as _runtime_dir
from jarvis_engine._shared import make_thread_aware_repo_root as _make_thread_aware_repo_root
from jarvis_engine.ingest import IngestionPipeline
from jarvis_engine.memory_store import MemoryStore
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
)


logger = logging.getLogger(__name__)

REPLAY_WINDOW_SECONDS = 120.0
MAX_NONCES = 100_000
MAX_AUTH_BODY_SIZE = 2_000_000  # 2 MB (matches sync/push max_content_length)
MAX_COMMAND_TEXT_CHARS = 2000
MAX_COMMAND_STDOUT_TAIL_LINES = 30
MAX_COMMAND_STDOUT_LINE_CHARS = 1200
MAX_COMMAND_RESPONSE_CHARS = 12_000
MAX_COMMAND_RESPONSE_CHUNK_CHARS = 800
MAX_COMMAND_RESPONSE_CHUNKS = 24
THREAD_CAPTURE_MAX_CHARS = 200_000


class CLIResult(TypedDict, total=False):
    """Typed return value for ``_run_main_cli``."""

    ok: bool
    error: str
    command_exit_code: int
    stdout_tail: list[str]
    stderr_tail: list[str]


# Thread-local storage — shared single instance lives in mobile_routes._helpers
# so route mixin modules (e.g. command.py) and this file see the same object.
from jarvis_engine.mobile_routes._helpers import (
    _thread_local,
    _configure_db,
    _parse_bool,
)


class _ThreadCapturingStdout:
    """Wraps real stdout, routing writes to per-thread StringIO when active.

    Install once at server startup via ``_ThreadCapturingStdout.install()``.
    Each request thread calls ``start_capture()`` / ``stop_capture()`` to
    redirect its own prints to a thread-local buffer without affecting other
    threads.
    """

    _real_stdout = None  # set by install()

    def __init__(self, real_stdout: IO[str]) -> None:
        object.__setattr__(self, "_real", real_stdout)
        _ThreadCapturingStdout._real_stdout = real_stdout

    def write(self, s: str) -> int:
        buf = getattr(_thread_local, "capture_buf", None)
        if buf is not None:
            max_chars = int(getattr(_thread_local, "capture_max_chars", THREAD_CAPTURE_MAX_CHARS))
            used = int(getattr(_thread_local, "capture_chars", 0))
            remaining = max_chars - used
            if remaining <= 0:
                _thread_local.capture_truncated = True
                return len(s)
            if len(s) > remaining:
                buf.write(s[:remaining])
                _thread_local.capture_chars = max_chars
                _thread_local.capture_truncated = True
                return len(s)
            buf.write(s)
            _thread_local.capture_chars = used + len(s)
            return len(s)
        return object.__getattribute__(self, "_real").write(s)

    def flush(self) -> None:
        buf = getattr(_thread_local, "capture_buf", None)
        if buf is not None:
            buf.flush()
        object.__getattribute__(self, "_real").flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_real"), name)

    @staticmethod
    def start_capture(max_chars: int = THREAD_CAPTURE_MAX_CHARS) -> None:
        """Begin capturing stdout for the calling thread."""
        _thread_local.capture_buf = io.StringIO()
        _thread_local.capture_chars = 0
        _thread_local.capture_max_chars = max(10_000, int(max_chars))
        _thread_local.capture_truncated = False

    @staticmethod
    def stop_capture() -> tuple[str, bool]:
        """Stop capturing and return `(captured_text, truncated)`."""
        buf = getattr(_thread_local, "capture_buf", None)
        truncated = bool(getattr(_thread_local, "capture_truncated", False))
        _thread_local.capture_buf = None
        _thread_local.capture_chars = 0
        _thread_local.capture_max_chars = THREAD_CAPTURE_MAX_CHARS
        _thread_local.capture_truncated = False
        return (buf.getvalue() if buf is not None else "", truncated)

    @staticmethod
    def install() -> None:
        """Replace sys.stdout once at server startup."""
        if not isinstance(sys.stdout, _ThreadCapturingStdout):
            sys.stdout = _ThreadCapturingStdout(sys.stdout)  # type: ignore[assignment]


# CORS whitelist: only allow localhost/loopback origins and file:// protocol.
# LAN IPs are added dynamically at server startup via _build_cors_whitelist().
_CORS_ALLOWED_ORIGIN_PATTERNS = [
    re.compile(r"^https?://localhost(:\d+)?$"),
    re.compile(r"^https?://127\.0\.0\.1(:\d+)?$"),
    re.compile(r"^https?://\[::1\](:\d+)?$"),
    re.compile(r"^file:///[A-Za-z]:/"),  # Only local file:// URIs with drive letter
]

# ---------------------------------------------------------------------------
# Rate-limit configuration
# ---------------------------------------------------------------------------

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
            except OSError as cleanup_exc:
                logger.debug("Failed to clean up partial TLS file %s: %s", p, cleanup_exc)
        return None, None
    finally:
        # Clean up the temporary extension file
        try:
            if ext_file.exists():
                ext_file.unlink()
        except OSError as cleanup_exc:
            logger.debug("Failed to clean up TLS extension file: %s", cleanup_exc)

    if cert_path.exists() and key_path.exists():
        logger.info("Generated self-signed TLS certificate with SAN=%s: %s", san_string, cert_path)
        return str(cert_path), str(key_path)

    return None, None


def _unescape_response(text: str) -> str:
    """Reverse the ``response=`` line escaping applied by the CLI."""
    return text.replace("\\n", "\n").replace("\\r", "\r").replace("\\\\", "\\")




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
        self._sync_engine: SyncEngine | None = None
        self._sync_transport: SyncTransport | None = None
        self._sync_init_attempted = False
        self._sync_init_lock = threading.Lock()
        # Auto-sync config for relay URLs, sync scheduling, phone autonomy
        self._auto_sync_config: AutoSyncConfig | None = None
        self._memory_engine: MemoryEngine | None = None
        self._memory_engine_init_lock = threading.Lock()
        self._embed_service: EmbeddingService | None = None
        self._embed_init_lock = threading.Lock()
        self.nonce_seen: dict[str, float] = {}
        self.nonce_lock = threading.RLock()
        self.next_nonce_cleanup_ts = 0.0
        self.nonce_cleanup_interval_s = 30.0
        self._nonce_cache_path = _runtime_dir(repo_root) / "nonce_cache.jsonl"
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
        self.security: SecurityOrchestrator | None = None
        self._security_db: sqlite3.Connection | None = None
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
            forensic_dir = _runtime_dir(self.repo_root) / "forensic"
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
        except Exception as exc:  # boundary: catch-all justified
            logger.error("SecurityOrchestrator init FAILED — server will reject non-essential requests: %s", exc)
            self.security = None
            self._security_degraded = True

        # Owner session manager — FAIL CLOSED: if init fails, reject
        # session-dependent requests.
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
        except Exception as exc:  # boundary: catch-all justified
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
                pass
            finally:
                try:
                    self.shutdown_request(request)
                except OSError:
                    pass
            return
        try:
            super().process_request(request, client_address)
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
            if len(bucket) > 5000:
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
            db_path = _memory_db_path(self.repo_root)
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
            except Exception as exc:  # boundary: catch-all justified
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
            db_path = _memory_db_path(self.repo_root)
            if not db_path.exists():
                return None
            try:
                from jarvis_engine.memory.engine import MemoryEngine
                self._memory_engine = MemoryEngine(db_path)
                logger.info("MemoryEngine lazy-initialized for mobile API metrics")
            except Exception as exc:  # boundary: catch-all justified
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
    BaseHTTPRequestHandler,
):
    server: MobileIngestServer  # type: ignore[assignment]  # narrow from HTTPServer
    server_version = "JarvisMobileAPI/0.1"

    # Credential endpoints contain passwords/tokens that look like base64
    # — skip body injection scan for them (they have their own auth checks).
    _BODY_SCAN_EXEMPT_PATHS = frozenset(
        {"/bootstrap", "/auth/login", "/auth/logout", "/auth/lock", "/sync/push"}
    )

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
        except Exception as exc:  # boundary: catch-all justified
            # Activity feed must never break command execution.
            logger.debug("Activity feed logging failed: %s", exc)

    # ------------------------------------------------------------------
    # Voice command helpers (decomposed from _run_voice_command)
    # ------------------------------------------------------------------

    def _validate_voice_text(
        self,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> str | dict[str, Any]:
        """Validate the ``text`` field in a voice command payload.

        Returns the cleaned text string on success, or an error-result dict
        that the caller should return immediately.
        """
        if "text" not in payload:
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Missing required field: text.",
                error_code="missing_text",
                category="validation",
                user_hint="Provide a non-empty 'text' field in the request payload.",
                retryable=False,
                command_exit_code=1,
                intent="validation_error",
                reason="missing required field",
                status_code="400",
            )
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > MAX_COMMAND_TEXT_CHARS:
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Invalid text command.",
                error_code="invalid_text",
                category="validation",
                user_hint=f"Command text must be 1..{MAX_COMMAND_TEXT_CHARS} characters.",
                retryable=False,
                command_exit_code=1,
                intent="validation_error",
                reason="invalid text command",
                status_code="400",
            )
        return text

    def _validate_voice_payload(
        self,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any] | None:
        """Validate and parse the voice command payload.

        Returns ``None`` on success (fields stored on ``self._voice_params``)
        or an error-result dict that the caller should return immediately.
        """
        text_or_err = self._validate_voice_text(payload, correlation_id)
        if isinstance(text_or_err, dict):
            return text_or_err
        text: str = text_or_err
        root = self._root

        voice_user = str(payload.get("voice_user", "conner")).strip() or "conner"
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", voice_user):
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Invalid voice_user.",
                error_code="invalid_voice_user",
                category="validation",
                user_hint="voice_user must match [a-zA-Z0-9._-]{1,64}.",
                retryable=False,
                command_exit_code=1,
                intent="validation_error",
                reason="invalid voice_user",
                status_code="400",
            )
        voice_auth_wav = str(payload.get("voice_auth_wav", "")).strip()
        if voice_auth_wav:
            try:
                wav_resolved = Path(voice_auth_wav).resolve()
                wav_resolved.relative_to(root.resolve())
            except (ValueError, OSError):
                return self._command_failure_result(
                    correlation_id=correlation_id,
                    error="voice_auth_wav path outside project root.",
                    error_code="invalid_voice_auth_path",
                    category="validation",
                    user_hint="Use a project-local path for voice_auth_wav.",
                    retryable=False,
                    command_exit_code=1,
                    intent="validation_error",
                    reason="voice_auth_wav path outside project root",
                    status_code="400",
                )

        voice_threshold_raw = payload.get("voice_threshold", 0.82)
        try:
            voice_threshold = float(voice_threshold_raw)
        except (TypeError, ValueError):
            voice_threshold = 0.82
        voice_threshold = min(0.99, max(0.1, voice_threshold))

        # Stash parsed params so the caller can access them without re-parsing.
        self._voice_params: dict[str, Any] = {  # type: ignore[attr-defined]
            "text": text,
            "execute": _parse_bool(payload.get("execute", False)),
            "approve_privileged": _parse_bool(payload.get("approve_privileged", False)),
            "speak": _parse_bool(payload.get("speak", False)),
            "voice_user": voice_user,
            "voice_auth_wav": voice_auth_wav,
            "master_password": str(payload.get("master_password", "")).strip(),
            "model_override": str(payload.get("model_override", "")).strip(),
            "voice_threshold": voice_threshold,
        }
        return None  # validation passed

    @staticmethod
    def _parse_voice_stdout(
        stdout_lines: list[str],
        default_status_code: str = "",
    ) -> dict[str, str]:
        """Extract intent/reason/status_code/response from voice command stdout."""
        intent = ""
        reason = ""
        response_text = ""
        status_code = default_status_code
        for line in stdout_lines:
            if line.startswith("intent="):
                intent = line.split("=", 1)[1]
            elif line.startswith("reason="):
                reason = line.split("=", 1)[1]
            elif line.startswith("status_code="):
                status_code = line.split("=", 1)[1]
            elif line.startswith("response="):
                raw = line.split("=", 1)[1]
                response_text = _unescape_response(raw)
        return {
            "intent": intent,
            "reason": reason,
            "status_code": status_code,
            "response_text": response_text,
        }

    def _build_voice_result(
        self,
        *,
        rc: int,
        correlation_id: str,
        parsed: dict[str, str],
        stdout_lines: list[str],
        stderr_lines: list[str] | None = None,
        stdout_truncated: bool = False,
    ) -> dict[str, Any]:
        """Build the final voice command result dict from parsed output."""
        normalized = self._normalize_command_output(
            response_text=parsed["response_text"],
            stdout_lines=stdout_lines[-MAX_COMMAND_STDOUT_TAIL_LINES:],
        )
        if stdout_truncated:
            normalized["stdout_truncated"] = True
        reason = parsed["reason"]
        return {
            "ok": rc == 0,
            "lifecycle_state": "completed" if rc == 0 else "failed",
            "correlation_id": correlation_id,
            "diagnostic_id": correlation_id[:12],
            "command_exit_code": rc,
            "intent": parsed["intent"],
            "response": normalized["response"],
            "response_chunks": normalized["response_chunks"],
            "response_truncated": normalized["response_truncated"],
            "status_code": parsed["status_code"],
            "reason": reason,
            "stdout_tail": normalized["stdout_tail"],
            "stdout_truncated": normalized["stdout_truncated"],
            "stderr_tail": (stderr_lines or [])[-20:],
            "error": "" if rc == 0 else (reason or "Command execution failed."),
            "error_code": "" if rc == 0 else "command_failed",
            "category": "" if rc == 0 else "execution",
            "retryable": rc != 0,
            "user_hint": "" if rc == 0 else "Retry or rephrase the request. Check diagnostic_id if it keeps failing.",
        }

    def _run_voice_in_process(
        self,
        params: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        """Execute voice command in-process via ``cmd_voice_run``."""
        import sqlite3 as _voice_sqlite3

        root = self._root
        try:
            import jarvis_engine.main as main_mod

            # Thread-local repo_root override — no global lock needed.
            _thread_local.repo_root_override = root
            original_repo_root = main_mod.repo_root
            if not hasattr(main_mod, "_original_repo_root"):
                main_mod._original_repo_root = original_repo_root  # type: ignore[attr-defined]

            # Install thread-aware repo_root if not already done
            if not getattr(main_mod, "_repo_root_patched", False):
                _orig = main_mod._original_repo_root  # type: ignore[attr-defined]
                main_mod.repo_root = _make_thread_aware_repo_root(_orig, _thread_local)  # type: ignore[assignment]
                main_mod._repo_root_patched = True  # type: ignore[attr-defined]

            # Per-thread stdout capture — concurrent requests run in parallel.
            _ThreadCapturingStdout.install()
            _ThreadCapturingStdout.start_capture()
            try:
                rc = main_mod.cmd_voice_run(
                    text=params["text"],
                    execute=params["execute"],
                    approve_privileged=params["approve_privileged"],
                    speak=params["speak"],
                    snapshot_path=root / ".planning" / _OPS_SNAPSHOT_FILENAME,
                    actions_path=root / ".planning" / _ACTIONS_FILENAME,
                    voice_user=params["voice_user"],
                    voice_auth_wav=params["voice_auth_wav"],
                    voice_threshold=params["voice_threshold"],
                    master_password=params["master_password"],
                    model_override=params["model_override"],
                    skip_voice_auth_guard=True,
                )
            finally:
                _thread_local.repo_root_override = None
        except (RuntimeError, OSError, ValueError, TimeoutError, KeyError, TypeError, AttributeError, ImportError, _voice_sqlite3.Error) as exc:
            logger.error("Voice command execution failed: %s", exc)
            _ThreadCapturingStdout.stop_capture()  # discard
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Command execution failed.",
                error_code="execution_exception",
                category="execution",
                user_hint="Retry once. If this keeps failing, inspect diagnostic_id in server logs.",
                retryable=True,
                intent="execution_error",
                reason="internal error",
                status_code="500",
            )
        except BaseException:
            _ThreadCapturingStdout.stop_capture()  # ensure cleanup on unexpected exceptions
            raise

        stdout_text, capture_truncated = _ThreadCapturingStdout.stop_capture()
        stdout_lines = stdout_text.splitlines()
        parsed = self._parse_voice_stdout(stdout_lines, default_status_code=str(rc))
        return self._build_voice_result(
            rc=rc,
            correlation_id=correlation_id,
            parsed=parsed,
            stdout_lines=stdout_lines,
            stderr_lines=[],
            stdout_truncated=capture_truncated,
        )

    def _run_voice_subprocess(
        self,
        params: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        """Execute voice command via subprocess (fallback when in-process import fails)."""
        root = self._root
        cmd = [
            sys.executable,
            "-m",
            "jarvis_engine.main",
            "voice-run",
            "--text",
            params["text"],
            "--voice-user",
            params["voice_user"],
            "--voice-threshold",
            str(params["voice_threshold"]),
        ]
        if params["execute"]:
            cmd.append("--execute")
        if params["approve_privileged"]:
            cmd.append("--approve-privileged")
        if params["speak"]:
            cmd.append("--speak")
        if params["voice_auth_wav"]:
            cmd.extend(["--voice-auth-wav", params["voice_auth_wav"]])
        if params["model_override"]:
            cmd.extend(["--model-override", params["model_override"]])
        cmd.append("--skip-voice-auth-guard")

        engine_dir = root / "engine"
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        # NOTE: master_password is intentionally NOT passed via env var
        # (visible to any local process via /proc/*/environ).  The in-process
        # path above is the primary execution method; this subprocess fallback
        # is deprecated and does not support master_password.
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
            return self._command_failure_result(
                correlation_id=correlation_id,
                error="Command execution failed.",
                error_code="subprocess_failure",
                category="execution",
                user_hint="Retry once. If persistent, check engine process and logs.",
                retryable=True,
                intent="execution_error",
                reason="subprocess failed",
                status_code="500",
            )

        stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        stderr_lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        parsed = self._parse_voice_stdout(stdout_lines)
        return self._build_voice_result(
            rc=result.returncode,
            correlation_id=correlation_id,
            parsed=parsed,
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
        )

    # ------------------------------------------------------------------
    # Main voice command orchestrator
    # ------------------------------------------------------------------

    def _run_voice_command(
        self,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if not correlation_id:
            correlation_id = uuid.uuid4().hex

        # 1. Validate and parse the payload.
        validation_error = self._validate_voice_payload(payload, correlation_id)
        if validation_error is not None:
            return validation_error
        params = self._voice_params  # type: ignore[attr-defined]

        # 2. Prefer in-process execution; fall back to subprocess.
        _can_import_in_process = True
        try:
            import jarvis_engine.main  # noqa: F401 — probe: is the module importable?
        except ImportError:
            _can_import_in_process = False

        if _can_import_in_process:
            return self._run_voice_in_process(params, correlation_id)
        return self._run_voice_subprocess(params, correlation_id)

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
        self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": message})

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
                self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Connection reset during read."})
                return None, None

        min_length = 1 if auth else 0
        if content_length < min_length or content_length > max_content_length:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid content length."})
            return None, None

        if auth and not self._validate_auth(body):
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
            self._write_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "error": "Too many master password attempts. Try again later."},
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

    # ------------------------------------------------------------------
    # GET handler methods (extracted for O(1) dispatch-dict routing)
    # ------------------------------------------------------------------

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
        "/conversation/state": "_handle_get_conversation_state",
        "/missions/status": "_handle_get_missions_status",
        "/alerts/pending": "_handle_get_alerts_pending",
        "/digest": "_handle_get_digest",
        "/meeting-prep": "_handle_get_meeting_prep",
        "/scam/campaigns": "_handle_get_scam_campaigns",
        "/scam/stats": "_handle_get_scam_stats",
        "/voice/latency": "_handle_get_voice_latency",
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
                self._write_json(HTTPStatus.FORBIDDEN, {
                    "ok": False,
                    "error": "Request blocked by security policy",
                })
                return False
        elif getattr(self.server, "_security_degraded", False) and path not in ("/health", "/auth/login"):
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ok": False,
                "error": "Service unavailable: security subsystem failed to initialize",
            })
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
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
        return

    def _check_rate_limit(self, path: str) -> bool:
        """Check global API rate limit. Returns True if request should proceed."""
        client_ip = str(self.client_address[0]).strip()
        server = self.server
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

    # ── Mission endpoints ────────────────────────────────────────────────

    # ── Alert queue endpoint (phone polls this) ───────────────────────────

    # ── Digest endpoint (summarize what you missed) ──────────────────────

    # ── Meeting prep endpoint (KG-powered) ───────────────────────────────

    # ── Smart reply endpoint ─────────────────────────────────────────────

    # ── Scam Campaign Hunter endpoints ──────────────────────────────────

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

    # Endpoint-specific POST body size limits (bytes).
    # /sync/push is allowed 2 MB; all others default to 1 MB.
    _POST_BODY_LIMITS: dict[str, int] = {
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
        self._cached_post_body: bytes | None = None
        raw_cl = self.headers.get("Content-Length", "0")
        try:
            cl = int(raw_cl)
        except (TypeError, ValueError):
            cl = 0
        if cl > max_body:
            self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
                "ok": False,
                "error": f"Request body too large (limit {max_body} bytes).",
            })
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
                self._write_json(HTTPStatus.BAD_REQUEST, {
                    "ok": False,
                    "error": "Failed to read request body.",
                })
                return
        # Security orchestrator pipeline check (with actual body)
        _body_text = ""
        if self._cached_post_body:
            try:
                _body_text = self._cached_post_body.decode("utf-8", errors="replace")
            except Exception as exc:  # boundary: catch-all justified
                logger.debug("POST body decode failed: %s", exc)
                _body_text = ""
        if not self._run_security_check(path, body=_body_text):
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


def _resolve_tls(
    repo_root: Path,
    tls: bool | None,
) -> tuple[str | None, str | None, bool]:
    """Resolve TLS cert/key paths and determine whether TLS is active.

    Returns ``(tls_cert, tls_key, tls_active)``.
    """
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
    return tls_cert, tls_key, tls_active


def _check_non_loopback_bind(host: str, tls_active: bool) -> None:
    """Raise if binding to a non-loopback address without TLS (unless overridden)."""
    allow_insecure_non_loopback = os.getenv(
        "JARVIS_ALLOW_INSECURE_MOBILE_BIND", "",
    ).strip().lower() in {"1", "true", "yes"}
    if (
        host not in {"127.0.0.1", "localhost", "::1"}
        and not tls_active
        and not allow_insecure_non_loopback
    ):
        raise RuntimeError(
            "Refusing non-loopback mobile bind without TLS. "
            "Set JARVIS_ALLOW_INSECURE_MOBILE_BIND=true only for trusted local testing."
        )


def _init_auto_sync_config(
    server: MobileIngestServer,
    repo_root: Path,
    port: int,
    tls_active: bool,
) -> None:
    """Initialize auto-sync config (relay URLs, sync scheduling, phone autonomy)."""
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
        except OSError as exc:
            logger.debug("LAN IP detection for auto-sync failed: %s", exc)
        logger.info("Auto-sync config initialized")
    except Exception as exc:  # boundary: catch-all justified
        logger.warning("Failed to initialize auto-sync config: %s", exc)


def _init_sync_engine(
    server: MobileIngestServer,
    repo_root: Path,
    signing_key: str,
) -> None:
    """Initialize sync engine and transport if the memory DB exists."""
    db_path = _memory_db_path(repo_root)
    if not db_path.exists():
        return

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
        except (_sqlite3.Error, OSError) as exc:
            logger.debug("Sync engine init failed in create_app, closing DB: %s", exc)
            sync_db.close()
            raise

        if signing_key:
            salt_path = repo_root / ".planning" / "brain" / "sync_salt.bin"
            server._sync_transport = SyncTransport(signing_key, salt_path)
            logger.info("Sync engine and transport initialized for mobile API")
        else:
            logger.warning("No signing key; sync transport not initialized")
    except Exception as exc:  # boundary: catch-all justified
        logger.warning("Failed to initialize sync for mobile API: %s", exc)


def _add_cors_lan_origin(server: MobileIngestServer, host: str) -> None:
    """Add the actual LAN IP to the CORS whitelist when binding to all interfaces."""
    if host not in ("0.0.0.0", "", "::"):
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
        server._extra_cors_origins.append(
            re.compile(rf"^https?://{re.escape(lan_ip)}(:\d+)?$")
        )
    except OSError as exc:
        logger.debug("LAN IP detection for CORS failed: %s", exc)


def _wrap_tls_socket(
    server: MobileIngestServer,
    tls_cert: str | None,
    tls_key: str | None,
    tls_active: bool,
) -> None:
    """Wrap the server socket with TLS if certs are available."""
    if not tls_active:
        return
    assert tls_cert is not None and tls_key is not None  # for type-checker
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tls_cert, tls_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.tls_active = True
    logger.info("TLS enabled with cert=%s key=%s", tls_cert, tls_key)


def _log_startup_info(host: str, port: int, tls_active: bool) -> None:
    """Log server startup details and available endpoints."""
    scheme = "https" if tls_active else "http"
    logger.info("mobile_api_listening=%s://%s:%s", scheme, host, port)
    logger.info("tls=%s", "enabled" if tls_active else "disabled")
    if host not in {"127.0.0.1", "localhost", "::1"} and not tls_active:
        logger.warning("mobile_api_non_loopback_without_tls")
    logger.info(
        "endpoints: GET /, GET /quick, GET /health, GET /cert-fingerprint, "
        "GET /auth/status, GET /settings, GET /dashboard, GET /audit, "
        "GET /security/status, GET /security/dashboard, GET /activity, "
        "GET /intelligence/growth, POST /bootstrap, POST /auth/login, "
        "POST /auth/logout, POST /auth/lock, POST /ingest, POST /settings, "
        "POST /command, POST /sync/pull, POST /sync/push, GET /sync/status, "
        "POST /self-heal"
    )


def _start_bus_prewarm(repo_root: Path) -> None:
    """Pre-warm the CommandBus in a background thread to avoid cold-start latency."""
    def _prewarm() -> None:
        try:
            import jarvis_engine.main as main_mod

            # Use thread-local override for prewarm thread
            _thread_local.repo_root_override = repo_root
            # Install thread-aware repo_root once
            if not getattr(main_mod, "_repo_root_patched", False):
                _orig = main_mod.repo_root
                main_mod._original_repo_root = _orig  # type: ignore[attr-defined]
                main_mod.repo_root = _make_thread_aware_repo_root(_orig, _thread_local)  # type: ignore[assignment]
                main_mod._repo_root_patched = True  # type: ignore[attr-defined]
            try:
                from jarvis_engine._bus import get_bus

                get_bus()
            finally:
                _thread_local.repo_root_override = None
            # Install thread-capturing stdout for concurrent request handling
            _ThreadCapturingStdout.install()
            logger.info("CommandBus pre-warmed successfully")
        except Exception as exc:  # boundary: catch-all justified
            logger.warning("CommandBus pre-warm failed (will warm on first request): %s", exc)

    import threading as _threading

    _threading.Thread(target=_prewarm, daemon=True, name="bus-prewarm").start()


def _shutdown_server(server: MobileIngestServer, store: MemoryStore) -> None:
    """Shut down the server and close all open DB connections."""
    server.shutdown()
    # Close sync DB connections to prevent SQLite connection leaks
    if server._sync_engine is not None:
        try:
            sync_db = getattr(server._sync_engine, "_db", None)
            if sync_db is not None:
                sync_db.close()
                logger.info("Sync engine DB connection closed")
        except Exception as exc:  # boundary: catch-all justified
            logger.warning("Failed to close sync engine DB: %s", exc)
    # Close security orchestrator DB connection
    if server._security_db is not None:
        try:
            server._security_db.close()
            logger.info("Security DB connection closed")
        except Exception as exc:  # boundary: catch-all justified
            logger.warning("Failed to close security DB: %s", exc)
    # Close the MemoryEngine (lazy-initialized for metrics)
    if server._memory_engine is not None:
        try:
            server._memory_engine.close()
            logger.info("MemoryEngine connection closed")
        except Exception as exc:  # boundary: catch-all justified
            logger.warning("Failed to close MemoryEngine: %s", exc)
    # Close the MemoryStore (which holds its own SQLite connection)
    try:
        store.close()
        logger.info("MemoryStore connection closed")
    except Exception as exc:  # boundary: catch-all justified
        logger.warning("Failed to close MemoryStore: %s", exc)


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
    tls_cert, tls_key, tls_active = _resolve_tls(repo_root, tls)
    _check_non_loopback_bind(host, tls_active)

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

    _init_auto_sync_config(server, repo_root, port, tls_active)
    _init_sync_engine(server, repo_root, signing_key)
    _add_cors_lan_origin(server, host)
    _wrap_tls_socket(server, tls_cert, tls_key, tls_active)
    _log_startup_info(host, port, tls_active)
    _start_bus_prewarm(repo_root)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Mobile API server shutting down (KeyboardInterrupt)")
    finally:
        _shutdown_server(server, store)
