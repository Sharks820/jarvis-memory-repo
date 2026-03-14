"""Backward-compat shim -- moved to jarvis_engine.memory.auto_ingest."""
from jarvis_engine.config import repo_root  # noqa: F401 -- needed for test monkeypatching
from jarvis_engine.memory.auto_ingest import (  # noqa: F401
    VALID_KINDS,
    VALID_SOURCES,
    _auto_ingest_lock,
    _auto_ingest_state,
    _auto_ingest_store_lock,
    _get_auto_ingest_store,
    _auto_ingest_dedupe_path,
    _load_auto_ingest_hashes,
    _store_auto_ingest_hashes,
    auto_ingest_memory,
    auto_ingest_memory_sync,
    sanitize_memory_content,
)
