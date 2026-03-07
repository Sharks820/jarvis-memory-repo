"""Shared utility functions used across multiple jarvis_engine modules.

Consolidates duplicated helpers to a single source of truth:
- load_json_file: safe JSON file reads with default-on-failure
- atomic_write_json: safe JSON file writes with atomic replace
- env_int: bounded integer env-var reader
- safe_float / safe_int: type coercion with defaults
- check_path_within_root: path traversal guard
- win_hidden_subprocess_kwargs: Windows subprocess window suppression
- load_personal_vocab_lines: personal_vocab.txt reader (used by stt + stt_postprocess)
- sanitize_fts_query / FTS5_SPECIAL_RE / FTS5_KEYWORDS: FTS5 query sanitization
  (used by memory/engine.py and knowledge/graph.py)
- now_iso: UTC ISO-8601 timestamp (used by security modules)
- make_thread_aware_repo_root: thread-local repo_root factory (used by mobile_api)
"""

from __future__ import annotations

__all__ = [
    "now_iso",
    "make_thread_aware_repo_root",
    "atomic_write_json",
    "load_json_file",
    "env_int",
    "safe_float",
    "safe_int",
    "check_path_within_root",
    "win_hidden_subprocess_kwargs",
    "sha256_hex",
    "sha256_short",
    "call_ollama_generate",
    "set_process_title",
    "load_personal_vocab_lines",
    "FTS5_SPECIAL_RE",
    "FTS5_KEYWORDS",
    "load_jsonl_tail",
    "sanitize_fts_query",
]

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.request import urlopen

T = TypeVar("T")

logger = logging.getLogger(__name__)


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


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
    except (AttributeError, OSError, TypeError) as exc:
        logger.debug("Failed to configure STARTUPINFO for hidden window: %s", exc)
    return kwargs


def sha256_hex(text: str) -> str:
    """Return the SHA-256 hex digest of *text* (UTF-8 encoded)."""
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_short(data: bytes, length: int = 32) -> str:
    """Return a truncated SHA-256 hex digest of *data*.

    Args:
        data: Raw bytes to hash.
        length: Number of hex characters to return (default 32).

    Returns:
        The first *length* characters of the full SHA-256 hex digest.
    """
    import hashlib
    return hashlib.sha256(data).hexdigest()[:length]


def call_ollama_generate(
    endpoint: str,
    model: str,
    prompt: str,
    options: dict[str, Any],
    *,
    timeout_s: int = 120,
) -> dict[str, Any]:
    """Send a non-streaming generate request to Ollama's ``/api/generate``.

    Args:
        endpoint: Ollama base URL (e.g. ``http://localhost:11434``).
        model: Model name (e.g. ``qwen3:14b``).
        prompt: The text prompt to send.
        options: Ollama options dict (num_ctx, num_predict, temperature, etc.).
        timeout_s: HTTP timeout in seconds.

    Returns:
        The parsed JSON response dict from Ollama.

    Raises:
        ValueError: If the endpoint fails the safety check or the response
            is not a JSON object.
        urllib.error.URLError: On network errors.
        TimeoutError: On request timeout.
    """
    from jarvis_engine.security.net_policy import is_safe_ollama_endpoint
    from urllib.request import Request

    if not is_safe_ollama_endpoint(endpoint):
        raise ValueError(f"Unsafe Ollama endpoint: {endpoint}")

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    req = Request(
        url=f"{endpoint.rstrip('/')}/api/generate",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"),
    )
    with urlopen(req, timeout=timeout_s) as resp:  # nosec B310
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from Ollama")
    return data


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
        :mod:`jarvis_engine.stt_postprocess`.
    """
    global _personal_vocab_raw_cache, _personal_vocab_stripped_cache

    # Fast path: check cache without lock
    if strip_parens:
        if _personal_vocab_stripped_cache is not None:
            return _personal_vocab_stripped_cache
    else:
        if _personal_vocab_raw_cache is not None:
            return _personal_vocab_raw_cache

    with _personal_vocab_lock:
        # Re-check under lock (double-checked locking)
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
