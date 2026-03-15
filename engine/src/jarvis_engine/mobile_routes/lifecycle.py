"""Mobile API server lifecycle: startup, init, shutdown, and TLS helpers.

Extracted from mobile_api.py to reduce file size and improve cohesion.
Contains the functions that wire up the MobileIngestServer at startup
and tear it down on shutdown — but not the server/handler classes
themselves.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import ssl
from pathlib import Path

import sqlite3 as _sqlite3

from jarvis_engine._constants import SUBSYSTEM_ERRORS
from jarvis_engine._shared import make_thread_aware_repo_root, memory_db_path
from jarvis_engine.mobile_routes._helpers import _repo_root_patch_lock, _thread_local

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TLS helpers
# ---------------------------------------------------------------------------


def _resolve_tls(
    repo_root: Path,
    tls: bool | None,
) -> tuple[str | None, str | None, bool]:
    """Resolve TLS cert/key paths and determine whether TLS is active.

    Returns ``(tls_cert, tls_key, tls_active)``.
    """
    from jarvis_engine.mobile_routes.server import _ensure_tls_cert

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


def _wrap_tls_socket(
    server: object,
    tls_cert: str | None,
    tls_key: str | None,
    tls_active: bool,
) -> None:
    """Wrap the server socket with TLS if certs are available."""
    if not tls_active:
        return
    assert tls_cert is not None and tls_key is not None  # for type-checker
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(tls_cert, tls_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)  # type: ignore[attr-defined]
    server.tls_active = True  # type: ignore[attr-defined]
    logger.info("TLS enabled with cert=%s key=%s", tls_cert, tls_key)


# ---------------------------------------------------------------------------
# Subsystem init helpers
# ---------------------------------------------------------------------------


def _init_auto_sync_config(
    server: object,
    repo_root: Path,
    port: int,
    tls_active: bool,
) -> None:
    """Initialize auto-sync config (relay URLs, sync scheduling, phone autonomy)."""
    try:
        from jarvis_engine.sync.auto_sync import AutoSyncConfig

        config_path = repo_root / ".planning" / "sync" / "auto_sync_config.json"
        server._auto_sync_config = AutoSyncConfig(config_path)  # type: ignore[attr-defined]
        # Auto-detect and store LAN URL
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
            proto = "https" if tls_active else "http"
            server._auto_sync_config.set("lan_url", f"{proto}://{lan_ip}:{port}")  # type: ignore[attr-defined]
        except OSError as exc:
            logger.debug("LAN IP detection for auto-sync failed: %s", exc)
        logger.info("Auto-sync config initialized")
    except SUBSYSTEM_ERRORS as exc:
        logger.warning("Failed to initialize auto-sync config: %s", exc)


def _init_sync_engine(
    server: object,
    repo_root: Path,
    signing_key: str,
) -> None:
    """Initialize sync engine and transport if the memory DB exists."""
    db_path = memory_db_path(repo_root)
    if not db_path.exists():
        return

    try:
        from jarvis_engine.sync.changelog import install_changelog_triggers
        from jarvis_engine.sync.engine import SyncEngine
        from jarvis_engine.sync.transport import SyncTransport

        from jarvis_engine._db_pragmas import connect_db as _connect_db2
        import threading as _threading

        sync_db = _connect_db2(db_path, full=True, check_same_thread=False)
        try:
            sync_lock = _threading.Lock()
            install_changelog_triggers(sync_db, device_id="desktop")
            # Use conflict strategy from auto-sync config
            conflict_strategy = "most_recent"
            if server._auto_sync_config is not None:  # type: ignore[attr-defined]
                conflict_strategy = server._auto_sync_config.get(  # type: ignore[attr-defined]
                    "conflict_strategy", "most_recent",
                )
            server._sync_engine = SyncEngine(  # type: ignore[attr-defined]
                sync_db, sync_lock, device_id="desktop",
                conflict_strategy=conflict_strategy,
            )
        except (_sqlite3.Error, OSError) as exc:
            logger.debug("Sync engine init failed in create_app, closing DB: %s", exc)
            sync_db.close()
            raise

        if signing_key:
            salt_path = repo_root / ".planning" / "brain" / "sync_salt.bin"
            server._sync_transport = SyncTransport(signing_key, salt_path)  # type: ignore[attr-defined]
            logger.info("Sync engine and transport initialized for mobile API")
        else:
            logger.warning("No signing key; sync transport not initialized")
    except SUBSYSTEM_ERRORS as exc:
        logger.warning("Failed to initialize sync for mobile API: %s", exc)


def _add_cors_lan_origin(server: object, host: str) -> None:
    """Add the actual LAN IP to the CORS whitelist when binding to all interfaces."""
    if host not in ("0.0.0.0", "", "::"):
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
        server._extra_cors_origins.append(  # type: ignore[attr-defined]
            re.compile(rf"^https?://{re.escape(lan_ip)}(:\d+)?$")
        )
    except OSError as exc:
        logger.debug("LAN IP detection for CORS failed: %s", exc)


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

            from jarvis_engine.mobile_routes._helpers import _ThreadCapturingStdout

            # Use thread-local override for prewarm thread
            _thread_local.repo_root_override = repo_root
            # Install thread-aware repo_root once (lock prevents race with voice thread)
            with _repo_root_patch_lock:
                if not getattr(main_mod, "_repo_root_patched", False):
                    _orig = main_mod.repo_root
                    main_mod._original_repo_root = _orig  # type: ignore[attr-defined]
                    main_mod.repo_root = make_thread_aware_repo_root(_orig, _thread_local)  # type: ignore[assignment]
                    main_mod._repo_root_patched = True  # type: ignore[attr-defined]
            try:
                from jarvis_engine._bus import get_bus

                get_bus()
            finally:
                _thread_local.repo_root_override = None
            # Install thread-capturing stdout for concurrent request handling
            _ThreadCapturingStdout.install()
            logger.info("CommandBus pre-warmed successfully")
        except SUBSYSTEM_ERRORS as exc:
            logger.warning("CommandBus pre-warm failed (will warm on first request): %s", exc)

    import threading as _threading

    _threading.Thread(target=_prewarm, daemon=True, name="bus-prewarm").start()


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def _shutdown_server(server: object, store: object) -> None:
    """Shut down the server and close all open DB connections."""
    server.shutdown()  # type: ignore[attr-defined]
    # Close sync DB connections to prevent SQLite connection leaks
    sync_engine = getattr(server, "_sync_engine", None)
    if sync_engine is not None:
        try:
            sync_db = getattr(sync_engine, "_db", None)
            if sync_db is not None:
                sync_db.close()
                logger.info("Sync engine DB connection closed")
        except (_sqlite3.Error, OSError) as exc:
            logger.warning("Failed to close sync engine DB: %s", exc)
    # Close security orchestrator DB connection
    security_db = getattr(server, "_security_db", None)
    if security_db is not None:
        try:
            security_db.close()
            logger.info("Security DB connection closed")
        except (_sqlite3.Error, OSError) as exc:
            logger.warning("Failed to close security DB: %s", exc)
    # Close the MemoryEngine (lazy-initialized for metrics)
    memory_engine = getattr(server, "_memory_engine", None)
    if memory_engine is not None:
        try:
            memory_engine.close()
            logger.info("MemoryEngine connection closed")
        except (_sqlite3.Error, OSError) as exc:
            logger.warning("Failed to close MemoryEngine: %s", exc)
    # Close the MemoryStore (which holds its own SQLite connection)
    try:
        store.close()  # type: ignore[attr-defined]
        logger.info("MemoryStore connection closed")
    except (_sqlite3.Error, OSError) as exc:
        logger.warning("Failed to close MemoryStore: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


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
    from jarvis_engine.memory.basic_ingest import IngestionPipeline
    from jarvis_engine.memory.store import MemoryStore
    from jarvis_engine.mobile_routes.server import MobileIngestHandler, MobileIngestServer

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
