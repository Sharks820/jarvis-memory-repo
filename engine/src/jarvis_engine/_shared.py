"""Shared utility functions used across multiple jarvis_engine modules.

Consolidates duplicated helpers to a single source of truth:
- atomic_write_json: safe JSON file writes with atomic replace
- env_int: bounded integer env-var reader
- safe_float / safe_int: type coercion with defaults
- check_path_within_root: path traversal guard
- win_hidden_subprocess_kwargs: Windows subprocess window suppression
- load_personal_vocab_lines: personal_vocab.txt reader (used by stt + stt_postprocess)
- sanitize_fts_query / FTS5_SPECIAL_RE / FTS5_KEYWORDS: FTS5 query sanitization
  (used by memory/engine.py and knowledge/graph.py)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_write_json(
    path: Path,
    payload: dict[str, Any] | list[Any],
    *,
    retries: int = 3,
    secure: bool = True,
) -> None:
    """Write JSON to *path* atomically via tmp-write-then-replace.

    Args:
        path: Destination file path.
        payload: JSON-serializable data.
        retries: Number of retry attempts on PermissionError (Windows lock contention).
        secure: If True, attempt ``chmod 0o600`` on the destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=True, indent=2)
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        tmp = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}.{attempt}")
        try:
            tmp.write_text(raw, encoding="utf-8")
            os.replace(str(tmp), str(path))
            if secure:
                try:
                    os.chmod(str(path), 0o600)
                except OSError as exc:
                    logger.debug("chmod 0o600 failed for %s: %s", path, exc)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.06 * (attempt + 1))
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError as exc:
                logger.debug("Failed to clean up temp file %s: %s", tmp, exc)
    if last_error is not None:
        raise last_error


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read a bounded integer from an environment variable.

    Returns *default* when the variable is unset or cannot be parsed as an
    integer.  The result is always clamped to [*minimum*, *maximum*].
    """
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Convert *value* to int, returning *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def check_path_within_root(path: Path, root: Path, label: str) -> None:
    """Resolve *path* and verify it stays within *root*.

    Raises ValueError if the resolved path escapes the root directory.
    """
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"{label} outside project root: {path}")


def win_hidden_subprocess_kwargs() -> dict[str, Any]:
    """Return subprocess kwargs to hide console windows on Windows.

    Returns an empty dict on non-Windows platforms.
    """
    if os.name != "nt":
        return {}
    import subprocess

    kwargs: dict[str, Any] = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if creationflags:
        kwargs["creationflags"] = creationflags
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    except Exception as exc:
        logger.debug("Failed to configure STARTUPINFO for hidden window: %s", exc)
    return kwargs


def sha256_hex(text: str) -> str:
    """Return the SHA-256 hex digest of *text* (UTF-8 encoded)."""
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def set_process_title(name: str) -> None:
    """Set the OS process title (requires ``setproctitle``; no-op if absent)."""
    try:
        import setproctitle
        setproctitle.setproctitle(name)
    except ImportError:
        logger.debug("setproctitle not available; process title unchanged")


# ---------------------------------------------------------------------------
# Personal vocabulary loader (shared by stt.py and stt_postprocess.py)
# ---------------------------------------------------------------------------

_personal_vocab_raw_cache: list[str] | None = None
_personal_vocab_stripped_cache: list[str] | None = None


def load_personal_vocab_lines(*, strip_parens: bool = False) -> list[str]:
    """Load personal vocabulary lines from ``data/personal_vocab.txt``.

    The file is located relative to the ``jarvis_engine`` package directory.
    Results are cached per variant (raw vs stripped) so the file is read at
    most once per process lifetime.

    Parameters
    ----------
    strip_parens:
        If ``True``, parenthetical annotations are removed from each line.
        For example ``"Conner (not Connor)"`` becomes ``"Conner"``.
        Used by the Deepgram keyterm loader in :mod:`jarvis_engine.stt`.
        If ``False``, lines are returned as-is (stripped of leading/trailing
        whitespace only).  Used by the LLM post-correction vocab loader in
        :mod:`jarvis_engine.stt_postprocess`.
    """
    global _personal_vocab_raw_cache, _personal_vocab_stripped_cache

    if strip_parens:
        if _personal_vocab_stripped_cache is not None:
            return _personal_vocab_stripped_cache
    else:
        if _personal_vocab_raw_cache is not None:
            return _personal_vocab_raw_cache

    # Locate the file relative to the jarvis_engine package
    vocab_path = Path(__file__).parent / "data" / "personal_vocab.txt"
    try:
        lines = vocab_path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        _personal_vocab_raw_cache = []
        _personal_vocab_stripped_cache = []
        return []

    # Build raw list (always needed)
    raw: list[str] = [line.strip() for line in lines if line.strip()]

    if _personal_vocab_raw_cache is None:
        _personal_vocab_raw_cache = raw

    # Build stripped list on demand
    if strip_parens:
        stripped: list[str] = []
        for entry in raw:
            paren_idx = entry.find("(")
            if paren_idx > 0:
                term = entry[:paren_idx].strip()
            else:
                term = entry
            if term:
                stripped.append(term)
        _personal_vocab_stripped_cache = stripped
        return _personal_vocab_stripped_cache

    return _personal_vocab_raw_cache


# ---------------------------------------------------------------------------
# FTS5 query sanitization (shared by memory/engine.py and knowledge/graph.py)
# ---------------------------------------------------------------------------

# FTS5 special characters that must be escaped in user queries.
# Includes: " * ( ) { } [ ] : ^ ~ + - ' (all FTS5 query syntax chars).
FTS5_SPECIAL_RE = re.compile(r"""["\*\(\)\{\}\[\]:^~+\-']""")
FTS5_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def load_jsonl_tail(path: Path, limit: int = 100) -> list[dict]:
    """Read the last *limit* JSON objects from a JSONL file.

    Blank lines and malformed JSON lines are silently skipped.
    Returns an empty list if the file does not exist.
    """
    if not path.exists():
        return []

    entries: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue

    return entries[-limit:]


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
