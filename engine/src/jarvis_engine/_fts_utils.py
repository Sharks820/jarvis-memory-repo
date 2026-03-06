"""Shared FTS5 query sanitization utilities.

Used by both MemoryEngine (memory/engine.py) and KnowledgeGraph (knowledge/graph.py)
to sanitize user queries before FTS5 MATCH operations.
"""

from __future__ import annotations

import re

# FTS5 special characters that must be escaped in user queries.
# Includes: " * ( ) { } [ ] : ^ ~ + - ' (all FTS5 query syntax chars).
FTS5_SPECIAL_RE = re.compile(r"""["\*\(\)\{\}\[\]:^~+\-']""")
FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH to prevent injection.

    Strips FTS5 special characters that could alter query semantics
    and removes FTS5 boolean operators.
    """
    sanitized = FTS5_SPECIAL_RE.sub(" ", query)
    # Remove FTS5 boolean operators to prevent query injection
    tokens = sanitized.split()
    tokens = [t for t in tokens if t.upper() not in FTS5_KEYWORDS]
    return " ".join(tokens).strip()
