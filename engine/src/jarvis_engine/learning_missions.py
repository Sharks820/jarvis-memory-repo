"""Backward-compatibility shim — real implementation lives in learning.missions."""
from jarvis_engine.learning.missions import *  # noqa: F401,F403
from jarvis_engine.learning import missions as _mod

# Re-export private names referenced by tests (monkeypatch / patch targets).
_fetch_page_text = _mod._fetch_page_text
_search_web = _mod._search_web
_fetch_page_cached = _mod._fetch_page_cached
_save_missions = _mod._save_missions
_MISSIONS_LOCK = _mod._MISSIONS_LOCK
_PAGE_CACHE = _mod._PAGE_CACHE
_PAGE_CACHE_LOCK = _mod._PAGE_CACHE_LOCK
_page_cache_bytes = _mod._page_cache_bytes
logger = _mod.logger
