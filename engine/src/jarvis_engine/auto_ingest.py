"""Public auto-ingest API for fire-and-forget memory ingestion.

Extracted from ``jarvis_engine.main`` so that handler modules can import
``auto_ingest_memory`` without reaching into private ``main`` internals.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis_engine._shared import now_iso as _now_iso
from jarvis_engine._constants import runtime_dir
from jarvis_engine.config import repo_root

if TYPE_CHECKING:
    from jarvis_engine.memory_store import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_auto_ingest_lock = threading.Lock()
_auto_ingest_store: "MemoryStore | None" = None
_auto_ingest_store_lock = threading.Lock()

VALID_SOURCES = {"user", "claude", "opus", "gemini", "task_outcome", "conversation"}
VALID_KINDS = {"episodic", "semantic", "procedural"}


# ---------------------------------------------------------------------------
# Internal helpers (ported verbatim from main.py)
# ---------------------------------------------------------------------------


def _get_auto_ingest_store() -> "MemoryStore":
    """Return a cached MemoryStore for auto-ingest, creating once on first call."""
    global _auto_ingest_store
    if _auto_ingest_store is not None:
        return _auto_ingest_store
    with _auto_ingest_store_lock:
        if _auto_ingest_store is None:
            from jarvis_engine.memory_store import MemoryStore

            _auto_ingest_store = MemoryStore(repo_root())
        return _auto_ingest_store


def _auto_ingest_dedupe_path() -> Path:
    return runtime_dir(repo_root()) / "auto_ingest_dedupe.json"


def sanitize_memory_content(content: str) -> str:
    """Redact credentials from memory content before storage."""
    content = content[
        :100_000
    ]  # Truncate before regex to prevent catastrophic backtracking
    # Redact master password, tokens, API keys, secrets, signing keys, bearer tokens
    _CRED_KEYS = r"(?:master[\s_-]*)?password|passwd|pwd|token|api[_-]?key|secret|signing[_-]?key"
    # JSON-style: "key": "value"
    cleaned = re.sub(
        rf'(?i)"({_CRED_KEYS})"\s*:\s*"[^"]*"',
        r'"\1": "[redacted]"',
        content,
    )
    # Unquoted style: key=value or key: value
    cleaned = re.sub(
        rf"(?i)({_CRED_KEYS})\s*[:=]\s*\S+",
        r"\1=[redacted]",
        cleaned,
    )
    cleaned = re.sub(r"(?i)(bearer)\s+\S+", r"\1 [redacted]", cleaned)
    return cleaned.strip()[:2000]


def _load_auto_ingest_hashes(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, dict):
        return []
    values = raw.get("hashes", [])
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _store_auto_ingest_hashes(path: Path, hashes: list[str]) -> None:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    payload = {"hashes": hashes[-400:], "updated_utc": _now_iso()}
    _atomic_write_json(path, payload)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def auto_ingest_memory_sync(source: str, kind: str, task_id: str, content: str) -> str:
    """Synchronous core of auto-ingest (runs in background thread)."""
    safe_content = sanitize_memory_content(content)
    if not safe_content:
        return ""
    safe_task_id = task_id[:128]
    dedupe_path = _auto_ingest_dedupe_path()
    dedupe_material = f"{source}|{kind}|{safe_task_id}|{safe_content.lower()}".encode(
        "utf-8"
    )
    dedupe_hash = hashlib.sha256(dedupe_material).hexdigest()
    # Lock prevents race condition when daemon + CLI ingest concurrently.
    # Check dedup under lock, but only persist hash AFTER successful ingestion
    # to allow retries on failure.
    with _auto_ingest_lock:
        seen = _load_auto_ingest_hashes(dedupe_path)
        seen_set = set(seen)
        if dedupe_hash in seen_set:
            return ""

    from jarvis_engine.ingest import IngestionPipeline

    store = _get_auto_ingest_store()
    pipeline = IngestionPipeline(store)
    rec = pipeline.ingest(
        source=source,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        task_id=safe_task_id,
        content=safe_content,
    )
    try:
        from jarvis_engine.brain_memory import ingest_brain_record

        ingest_brain_record(
            repo_root(),
            source=source,
            kind=kind,
            task_id=safe_task_id,
            content=safe_content,
            tags=[source, kind],
            confidence=0.74 if source == "task_outcome" else 0.68,
        )
    except ValueError:
        logger.warning("brain ingest failed for task_id=%s", safe_task_id[:32])

    # Mark as seen only AFTER successful ingestion so failures can be retried
    with _auto_ingest_lock:
        seen = _load_auto_ingest_hashes(dedupe_path)
        seen.append(dedupe_hash)
        _store_auto_ingest_hashes(dedupe_path, seen)

    return rec.record_id


def auto_ingest_memory(source: str, kind: str, task_id: str, content: str) -> str:
    """Fire-and-forget auto-ingest -- runs in a background thread to avoid blocking responses."""
    if os.getenv("JARVIS_AUTO_INGEST_DISABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return ""
    if source not in VALID_SOURCES or kind not in VALID_KINDS:
        return ""

    def _bg() -> None:
        try:
            auto_ingest_memory_sync(source, kind, task_id, content)
        except Exception as exc:
            logger.debug("Background auto-ingest failed: %s", exc)

    t = threading.Thread(target=_bg, daemon=True)
    t.start()
    # Return empty -- the record ID is no longer available synchronously,
    # but the ingest still happens in the background.
    return ""
