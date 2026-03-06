"""Backwards-compatibility shim — FTS5 utilities now live in _shared.py.

This module re-exports the canonical implementations so that any remaining
references to ``jarvis_engine._fts_utils`` continue to work.
"""

from jarvis_engine._shared import FTS5_KEYWORDS, FTS5_SPECIAL_RE, sanitize_fts_query

__all__ = ["FTS5_SPECIAL_RE", "FTS5_KEYWORDS", "sanitize_fts_query"]
