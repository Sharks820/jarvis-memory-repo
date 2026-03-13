"""Backward-compatibility shim — real implementation moved to jarvis_engine.mobile_routes.lifecycle."""
from jarvis_engine.mobile_routes.lifecycle import *  # noqa: F401, F403
from jarvis_engine.mobile_routes.lifecycle import (  # noqa: F401, E402
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
