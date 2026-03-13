"""Backward-compatibility shim — real implementation moved to jarvis_engine.mobile_routes.server."""
from jarvis_engine.mobile_routes.server import *  # noqa: F401, F403
from jarvis_engine.mobile_routes.server import (  # noqa: F401, E402
    _API_RATE_EXPENSIVE,
    _API_RATE_NORMAL,
    _MASTER_PW_RATE,
    _build_san_string,
    _detect_lan_ips,
    _ensure_tls_cert,
    _EXPENSIVE_PATHS,
    MobileIngestHandler,
    MobileIngestServer,
    run_mobile_server,
    _resolve_tls,
    _check_non_loopback_bind,
    _init_auto_sync_config,
    _init_sync_engine,
    _add_cors_lan_origin,
    _wrap_tls_socket,
    _log_startup_info,
    _start_bus_prewarm,
    _shutdown_server,
)
