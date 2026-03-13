"""Shared utility functions used across multiple jarvis_engine modules.

Consolidates duplicated helpers to a single source of truth:
- load_json_file / atomic_write_json: safe JSON file I/O
- env_int / safe_float / safe_int: type coercion with defaults
- check_path_within_root: path traversal guard
- win_hidden_subprocess_kwargs: Windows subprocess window suppression
- load_personal_vocab_lines: personal_vocab.txt reader (stt + stt_postprocess)
- now_iso / parse_iso_timestamp: UTC timestamp helpers
- make_thread_aware_repo_root: thread-local repo_root factory (mobile_api)
- memory_db_path / runtime_dir: canonical project path helpers
- extract_keywords / is_privacy_sensitive: text analysis
- get_local_model / get_fast_local_model: LLM model name resolution
- make_task_id / recency_weight: task and scoring utilities

FTS5/DB helpers (sanitize_fts_query, FTS5_SPECIAL_RE, FTS5_KEYWORDS,
placeholder_csv) are re-exported from :mod:`jarvis_engine._db_pragmas`.
"""

from __future__ import annotations

__all__ = [
    "atomic_write_json",
    "check_path_within_root",
    "env_int",
    "FTS5_KEYWORDS",
    "FTS5_SPECIAL_RE",
    "load_json_file",
    "load_jsonl_tail",
    "load_personal_vocab_lines",
    "make_thread_aware_repo_root",
    "now_iso",
    "parse_iso_timestamp",
    "placeholder_csv",
    "safe_float",
    "safe_int",
    "sanitize_fts_query",
    "set_process_title",
    "sha256_hex",
    "sha256_short",
    "extract_keywords",
    "get_fast_local_model",
    "get_local_model",
    "is_privacy_sensitive",
    "make_task_id",
    "memory_db_path",
    "recency_weight",
    "runtime_dir",
    "win_hidden_subprocess_kwargs",
]

import hashlib
import json
import logging
import math
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from jarvis_engine._compat import UTC
from jarvis_engine._constants import (
    DEFAULT_LOCAL_MODEL,
    FAST_LOCAL_MODEL,
    PRIVACY_KEYWORDS,
    STOP_WORDS,
)

T = TypeVar("T")

logger = logging.getLogger(__name__)


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string, handling the ``Z`` UTC suffix.

    Returns a timezone-aware ``datetime`` in UTC, or ``None`` for empty
    or unparseable input.  Naive datetimes are assumed to be UTC.
    """
    raw = str(value).strip() if value else ""
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def make_thread_aware_repo_root(
    original_fn: Callable[[], Path],
    thread_local: threading.local,
) -> Callable[[], Path]:
    """Create a thread-aware wrapper around ``repo_root()``.

    Returns a function that checks ``thread_local.repo_root_override``
    before falling back to *original_fn*.  Used by the mobile API to let
    each request thread point at its own repo root without a global lock.
    """

    def _thread_aware_repo_root() -> Path:
        override = getattr(thread_local, "repo_root_override", None)
        if override is not None:
            return override
        return original_fn()

    return _thread_aware_repo_root


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
    _RETRY_BACKOFF_BASE_S = 0.06
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
            time.sleep(_RETRY_BACKOFF_BASE_S * (attempt + 1))
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError as exc:
                logger.debug("Failed to clean up temp file %s: %s", tmp, exc)
    if last_error is not None:
        raise last_error


def load_json_file(path: Path, default: T, *, expected_type: type | None = None) -> Any:
    """Load a JSON file, returning *default* on any failure.

    Parameters
    ----------
    path : Path
        File to read.
    default
        Value returned when the file is missing, unreadable, or has
        unexpected structure.
    expected_type : type, optional
        If given, the parsed JSON root must be an instance of this type
        (typically ``dict`` or ``list``); otherwise *default* is returned.
    """
    if not path.exists():
        return default
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    if expected_type is not None and not isinstance(raw, expected_type):
        return default
    return raw


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
    except ValueError as _val_exc:
        raise ValueError(f"{label} outside project root: {path}") from _val_exc


def win_hidden_subprocess_kwargs() -> dict[str, Any]:
    """Return subprocess kwargs to hide console windows on Windows.

    Returns an empty dict on non-Windows platforms.
    """
    if os.name != "nt":
        return {}
    kwargs: dict[str, Any] = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if creationflags:
        kwargs["creationflags"] = creationflags
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    except (AttributeError, OSError, TypeError) as exc:
        logger.debug("Failed to configure STARTUPINFO for hidden window: %s", exc)
    return kwargs


def sha256_hex(text: str) -> str:
    """Return the SHA-256 hex digest of *text* (UTF-8 encoded)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_short(data: bytes, length: int = 32) -> str:
    """Return a truncated SHA-256 hex digest of *data*.

    Args:
        data: Raw bytes to hash.
        length: Number of hex characters to return (default 32).

    Returns:
        The first *length* characters of the full SHA-256 hex digest.
    """
    return hashlib.sha256(data).hexdigest()[:length]


def set_process_title(name: str) -> None:
    """Set the OS process title (requires ``setproctitle``; no-op if absent)."""
    try:
        import setproctitle  # type: ignore[import-not-found]
        setproctitle.setproctitle(name)
    except ImportError:
        logger.debug("setproctitle not available; process title unchanged")


# Personal vocabulary loader (shared by stt.py and stt_postprocess.py)

_personal_vocab_cache: dict[str, list[str]] = {}
_personal_vocab_lock = threading.Lock()


def load_personal_vocab_lines(*, strip_parens: bool = False) -> list[str]:
    """Load personal vocabulary lines from ``data/personal_vocab.txt``.

    The file is located relative to the ``jarvis_engine`` package directory.
    Results are cached per variant (raw vs stripped) so the file is read at
    most once per process lifetime.  Thread-safe via double-checked locking.

    Parameters
    ----------
    strip_parens:
        If ``True``, parenthetical annotations are removed from each line.
        For example ``"Conner (not Connor)"`` becomes ``"Conner"``.
        Used by the Deepgram keyterm loader in :mod:`jarvis_engine.stt`.
        If ``False``, lines are returned as-is (stripped of leading/trailing
        whitespace only).  Used by the LLM post-correction vocab loader in
        :mod:`jarvis_engine.stt.postprocess`.
    """
    cache_key = "stripped" if strip_parens else "raw"

    # Fast path: check cache without lock
    if cache_key in _personal_vocab_cache:
        return _personal_vocab_cache[cache_key]

    with _personal_vocab_lock:
        # Re-check under lock (double-checked locking)
        if cache_key in _personal_vocab_cache:
            return _personal_vocab_cache[cache_key]

        # Locate the file relative to the jarvis_engine package
        vocab_path = Path(__file__).parent / "data" / "personal_vocab.txt"
        try:
            lines = vocab_path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            _personal_vocab_cache["raw"] = []
            _personal_vocab_cache["stripped"] = []
            return []

        # Build raw list (always needed)
        raw: list[str] = [line.strip() for line in lines if line.strip()]

        if "raw" not in _personal_vocab_cache:
            _personal_vocab_cache["raw"] = raw

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
            _personal_vocab_cache["stripped"] = stripped
            return _personal_vocab_cache["stripped"]

        return _personal_vocab_cache["raw"]


# FTS5 query sanitization — canonical home is _db_pragmas.py;
# re-exported here for backward compatibility.
from jarvis_engine._db_pragmas import (  # noqa: F401
    FTS5_KEYWORDS,
    FTS5_SPECIAL_RE,
    sanitize_fts_query,
    placeholder_csv,
)


def load_jsonl_tail(path: Path, limit: int = 100) -> list[dict]:
    """Read the last *limit* JSON objects from a JSONL file.

    Uses a seek-from-end strategy to avoid reading the entire file into
    memory for large JSONL files.  Falls back to a full read for small
    files or when the tail chunk doesn't contain enough lines.

    Blank lines and malformed JSON lines are silently skipped.
    Returns an empty list if the file does not exist.
    """
    if not path.exists():
        return []

    try:
        file_size = path.stat().st_size
    except OSError:
        return []

    if file_size == 0:
        return []

    # For small files (< 64KB) or when we need many lines, just read all.
    # Otherwise, seek from the end and read a chunk that should contain
    # enough lines.  Average JSONL line ~200-500 bytes, so 1KB per line
    # is a safe overestimate.
    _SMALL_FILE_THRESHOLD = 64 * 1024
    _BYTES_PER_LINE_ESTIMATE = 1024

    def _parse_lines(text: str) -> list[dict]:
        entries: list[dict] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSONL line in %s", path)
                continue
        return entries

    if file_size <= _SMALL_FILE_THRESHOLD:
        # Small file: read everything (avoids partial-line issues)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return _parse_lines(text)[-limit:]

    # Large file: seek from the end to read only the tail portion.
    # Request extra lines to account for blank/malformed lines being skipped.
    chunk_size = min(file_size, _BYTES_PER_LINE_ESTIMATE * (limit + 20))
    try:
        with path.open("rb") as f:
            f.seek(max(0, file_size - chunk_size))
            raw = f.read()
    except OSError:
        return []

    text = raw.decode("utf-8", errors="replace")

    # If we seeked into the middle of the file, discard the first
    # (potentially partial) line.
    if file_size > chunk_size:
        newline_pos = text.find("\n")
        if newline_pos >= 0:
            text = text[newline_pos + 1:]

    entries = _parse_lines(text)

    if len(entries) >= limit:
        return entries[-limit:]

    # Rare edge case: not enough valid lines in the tail chunk.
    # Fall back to reading the entire file.
    try:
        full_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entries[-limit:]
    return _parse_lines(full_text)[-limit:]


# Utilities moved from _constants.py (these are functions, not constants)

_lazy_cache: dict[str, object] = {}


def _get_privacy_re() -> re.Pattern[str]:
    """Lazily compile the privacy regex (avoids circular import at load time)."""
    if "privacy_re" not in _lazy_cache:
        _lazy_cache["privacy_re"] = re.compile(
            r"\b(?:" + "|".join(
                re.escape(kw) for kw in sorted(PRIVACY_KEYWORDS, key=len, reverse=True)
            ) + r")\b",
            re.IGNORECASE,
        )
    return _lazy_cache["privacy_re"]  # type: ignore[return-value]


def is_privacy_sensitive(text: str) -> bool:
    """Return *True* if *text* contains any privacy keyword (word-boundary match)."""
    return bool(_get_privacy_re().search(text))


def get_local_model() -> str:
    """Return the configured local Ollama model name."""
    return os.environ.get("JARVIS_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)


def get_fast_local_model() -> str:
    """Return the configured fast local Ollama model name."""
    return os.environ.get("JARVIS_FAST_LOCAL_MODEL", FAST_LOCAL_MODEL)


def memory_db_path(root: Path) -> Path:
    """Return the canonical path to the main Jarvis memory database."""
    return root / ".planning" / "brain" / "jarvis_memory.db"


def runtime_dir(root: Path) -> Path:
    """Return the canonical path to the runtime data directory."""
    return root / ".planning" / "runtime"


def extract_keywords(
    text: str,
    *,
    stop_words: frozenset[str] | None = None,
    min_length: int = 4,
    pattern: str = r"[a-zA-Z]+",
    deduplicate: bool = True,
) -> list[str]:
    """Extract meaningful keywords from *text*."""
    if not text:
        return []

    if stop_words is None:
        stop_words = STOP_WORDS

    words = re.findall(pattern, text.lower())
    keywords = [w for w in words if len(w) >= min_length and w not in stop_words]

    if deduplicate:
        seen: set[str] = set()
        unique: list[str] = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique.append(kw)
        return unique

    return keywords


def make_task_id(prefix: str) -> str:
    """Generate a timestamped task ID like ``prefix-20260305143000-a1b2``."""
    stamp = datetime.now(UTC).strftime('%Y%m%d%H%M%S')
    suffix = secrets.token_hex(2)
    return f"{prefix}-{stamp}-{suffix}"


_RECENCY_DECAY_HOURS = 168.0  # ~1 week half-life for recency scoring


def recency_weight(
    ts_text: str,
    *,
    default: float = 0.0,
    decay_hours: float = _RECENCY_DECAY_HOURS,
) -> float:
    """Compute exponential recency decay for a timestamp string.

    Returns a value between 0.0 and 1.0 for valid timestamps (1.0 = just
    created, decaying toward 0.0 with a half-life of approximately
    *decay_hours* hours).  Returns *default* for empty or unparseable input.
    """
    parsed = parse_iso_timestamp(ts_text)
    if parsed is None:
        return default
    delta_hours = max(0.0, (datetime.now(UTC) - parsed).total_seconds() / 3600.0)
    return math.exp(-delta_hours / decay_hours)


# placeholder_csv re-exported from _db_pragmas above

