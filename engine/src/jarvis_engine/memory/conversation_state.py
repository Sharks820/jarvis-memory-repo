"""Cross-LLM Context Continuity — persistent conversation state machine.

Ensures no information is ever lost across provider switches, daemon restarts,
or session boundaries.  The state machine tracks entities, decisions,
unresolved goals, and a rolling summary so that any new LLM provider can
resume the conversation seamlessly.

Thread safety: all mutations go through ``threading.RLock`` instances.
Persistence: Fernet-encrypted JSON writes via ``os.replace`` for crash safety.
Timeline: SQLite-backed turn history, falls back to in-memory list.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jarvis_engine._shared import now_iso
from jarvis_engine.config import repo_root

logger = logging.getLogger(__name__)


# Constants

_DEFAULT_STATE_DIR = ".planning/runtime"
_STATE_FILENAME = "conversation_state.json"
_TIMELINE_DB_FILENAME = "conversation_timeline.db"
_SALT_FILENAME = "conversation_state_salt.bin"

# Encryption constants
_KDF_ITERATIONS = 480_000
_ENCRYPTION_ENV_KEY = "JARVIS_SIGNING_KEY"
_ENCRYPTED_HEADER = b"FERNET:"

# Timeline retention
_TIMELINE_MAX_AGE_DAYS = 30
_TIMELINE_PRUNE_INTERVAL = 100  # prune every Nth save
_TIMELINE_VACUUM_THRESHOLD = 1000

# Entity validation limits (S5)
_MAX_ENTITY_LENGTH = 200
_MAX_ENTITIES_PER_TURN = 50
_MAX_ROLLING_SUMMARY_CHARS = 2000
# Content sanitization patterns (used by _is_suspicious_entity)
_RE_CODE_BLOCK = re.compile(r"```")
_RE_URL_ENCODED = re.compile(r"(?:%[0-9A-Fa-f]{2}){3,}")
_RE_BASE64_BLOCK = re.compile(r"^(?=[A-Za-z0-9+/]*[+/=])[A-Za-z0-9+/]{16,}={0,2}$")
_RE_PROMPT_INJECTION = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions?"
    r"|system\s*:\s*you\s+are"
    r"|<\s*(?:script|img|iframe|object)\b"
    r"|javascript\s*:"
    r"|EXECUTE\s+|DROP\s+TABLE|SELECT\s+.*FROM"
    r"|\beval\s*\("
    r"|\b__import__\s*\()",
    re.IGNORECASE,
)

# Entity extraction patterns — findall/finditer on turn text
_RE_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_RE_UNIX_PATH = re.compile(r"(?<!\w)(?:/[\w._-]+){2,}", re.ASCII)
_RE_WIN_PATH = re.compile(r"[A-Z]:\\(?:[\w._-]+\\)*[\w._-]+", re.ASCII)
_RE_DATE_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b")
_RE_DATE_SLASH = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_RE_DATE_WRITTEN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}(?:,?\s+\d{4})?\b"
)
_RE_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\b")
_RE_AMOUNT = re.compile(
    r"(?:\$\d+(?:,\d{3})*(?:\.\d+)?)"
    r"|"
    r"(?:\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|USD|EUR|GBP|MB|GB|TB|KB|ms|seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b)",
    re.IGNORECASE,
)
_RE_NAME_PREFIX = re.compile(
    r"\b(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b"
)
_RE_CAPITALIZED_SEQ = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")

# PII patterns — fullmatch() on extracted entity strings for masking.
# No \b anchors needed (fullmatch provides implicit anchoring).
_RE_PII_PHONE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_RE_PII_SSN = re.compile(r"\d{3}-\d{2}-\d{4}")
_RE_PII_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_PII_CC = re.compile(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}")

# Provider name normalization (CTX-05): raw model strings → canonical provider name
_PROVIDER_NORMALIZATION: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"claude[-_]?\d", re.IGNORECASE), "claude"),
    (re.compile(r"^claude$", re.IGNORECASE), "claude"),
    (re.compile(r"gpt[-_]?\d", re.IGNORECASE), "openai"),
    (re.compile(r"^gpt", re.IGNORECASE), "openai"),
    (re.compile(r"^o\d", re.IGNORECASE), "openai"),
    (re.compile(r"qwen", re.IGNORECASE), "qwen"),
    (re.compile(r"gemini", re.IGNORECASE), "gemini"),
    (re.compile(r"gemma", re.IGNORECASE), "gemma"),
    (re.compile(r"llama", re.IGNORECASE), "llama"),
    (re.compile(r"mistral", re.IGNORECASE), "mistral"),
    (re.compile(r"mixtral", re.IGNORECASE), "mixtral"),
    (re.compile(r"deepseek", re.IGNORECASE), "deepseek"),
    (re.compile(r"codex", re.IGNORECASE), "codex"),
    (re.compile(r"groq", re.IGNORECASE), "groq"),
    (re.compile(r"phi[-_]?\d", re.IGNORECASE), "phi"),
    (re.compile(r"command[-_]?r", re.IGNORECASE), "cohere"),
    (re.compile(r"whisper", re.IGNORECASE), "whisper"),
]


def normalize_provider_name(raw_model: str) -> str:
    """Normalize a raw model identifier to a canonical provider name.

    Examples::

        "claude-3-opus-20240229" → "claude"
        "qwen3.5:latest"        → "qwen"
        "gpt-4-turbo"           → "openai"
        "unknown-model"         → "unknown-model"  (passthrough)

    Parameters
    ----------
    raw_model : str
        The raw model name as returned by the gateway.

    Returns
    -------
    str
        Canonical provider name, or the original string if no rule matched.
    """
    if not raw_model:
        return "unknown"
    stripped = raw_model.strip()
    for pattern, canonical in _PROVIDER_NORMALIZATION:
        if pattern.search(stripped):
            return canonical
    return stripped


# Semantic extraction patterns — decisions and unresolved goals
_RE_DECISIONS = re.compile(
    r"(?:^|\.\s+|\n)"
    r"("
    r"(?:I'll|I will|Let's|Let us|We decided|We've decided|Agreed to|Going with|"
    r"We're going with|I've decided|The plan is to|We chose|We'll|"
    r"Decision:|Decided to|I decided to|We agreed to|Going to)"
    r"\s+[^.!?\n]{5,80}"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_RE_UNRESOLVED = re.compile(
    r"(?:^|\.\s+|\n)"
    r"("
    r"(?:We still need|TODO:|Todo:|Next step:|Remaining:|We should|"
    r"Still need to|Haven't yet|Need to|We need to|Should also|"
    r"Action item:|Follow-up:|Follow up:)"
    r"\s+[^.!?\n]{5,120}"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Common English words to exclude from capitalized-sequence entity detection
_COMMON_WORDS = frozenset(
    {
        "The",
        "This",
        "That",
        "These",
        "Those",
        "What",
        "When",
        "Where",
        "Which",
        "While",
        "With",
        "About",
        "After",
        "Before",
        "Between",
        "Could",
        "Would",
        "Should",
        "Does",
        "Have",
        "Here",
        "There",
        "However",
        "Also",
        "Each",
        "Every",
        "Some",
        "Many",
        "Much",
        "Most",
        "Other",
        "Such",
        "Than",
        "Then",
        "Just",
        "Only",
        "Very",
        "Still",
        "Even",
        "Already",
        "Once",
        "Both",
        "Either",
        "Neither",
        "First",
        "Second",
        "Third",
        "Last",
        "Next",
        "Over",
        "Under",
        "Into",
        "From",
        "Through",
        "During",
        "Since",
        "Until",
        "Upon",
        "Within",
        "Without",
        "Along",
        "Among",
        "Across",
        "Around",
        "Beyond",
        "Inside",
        "Outside",
        "Above",
        "Below",
        "Near",
        "Far",
        "Well",
        "Good",
        "Great",
        "Better",
        "Best",
        "Sure",
        "Please",
        "Thanks",
        "Thank",
        "Sorry",
        "Yes",
        "No",
        "Not",
        "But",
        "And",
        "For",
        "Are",
        "Was",
        "Were",
        "Been",
        "Being",
        "Has",
        "Had",
        "May",
        "Can",
        "Will",
        "Shall",
        "Might",
    }
)


# Data classes


@dataclass
class ConversationSnapshot:
    """Full state of a cross-LLM conversation session.

    Persisted to disk so that daemon restarts and provider switches lose no
    context.  Every field that must survive a model switch is included.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    checkpoint_id: int = 0
    rolling_summary: str = ""
    anchor_entities: set[str] = field(default_factory=set)
    unresolved_goals: list[str] = field(default_factory=list)
    prior_decisions: list[str] = field(default_factory=list)
    referenced_artifacts: list[str] = field(default_factory=list)
    active_mission_ids: list[str] = field(default_factory=list)
    active_model: str = ""
    model_history: list[list[Any]] = field(default_factory=list)
    turn_count: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Converts ``anchor_entities`` from set to sorted list for JSON
        serialization.
        """
        d = asdict(self)
        # asdict converts set -> set; we need a list for JSON
        d["anchor_entities"] = sorted(d["anchor_entities"])
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationSnapshot:
        """Deserialize from a dictionary, tolerating missing/extra keys."""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        # Convert anchor_entities from list (JSON) back to set
        if "anchor_entities" in filtered and isinstance(
            filtered["anchor_entities"], list
        ):
            filtered["anchor_entities"] = set(filtered["anchor_entities"])
        return cls(**filtered)


# Timeline entry (named tuple-style dataclass)


@dataclass
class TimelineEntry:
    """A single turn in the conversation timeline."""

    timestamp: str
    model: str
    role: str
    content_hash: str
    entities_extracted: list[str]
    summary_snippet: str


# ConversationTimeline — SQLite-backed turn history


class ConversationTimeline:
    """Persistent timeline of conversation turns.

    Stores each turn as (timestamp, model, role, content_hash,
    entities_extracted, summary_snippet).  Uses a SQLite table when a
    database path is provided, otherwise falls back to an in-memory list.

    Parameters
    ----------
    db_path : Path | None
        Path to the SQLite database file.  When *None*, the timeline is
        held purely in memory and is not persisted.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        self._in_memory: list[TimelineEntry] = []
        self._using_db = False

        if db_path is not None:
            try:
                db_path.parent.mkdir(parents=True, exist_ok=True)
                from jarvis_engine._db_pragmas import connect_db

                self._db = connect_db(db_path, check_same_thread=False)
                self._init_schema()
                self._using_db = True
            except (OSError, sqlite3.Error) as exc:
                logger.warning(
                    "ConversationTimeline falling back to in-memory: %s", exc
                )
                self._db = None
                self._using_db = False

    def _init_schema(self) -> None:
        assert self._db is not None
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_timeline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                role TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                entities_extracted TEXT NOT NULL DEFAULT '[]',
                summary_snippet TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_timeline_timestamp
                ON conversation_timeline(timestamp);
            CREATE INDEX IF NOT EXISTS idx_timeline_model
                ON conversation_timeline(model);
            CREATE INDEX IF NOT EXISTS idx_timeline_role
                ON conversation_timeline(role);
            """
        )

    def add_turn(self, entry: TimelineEntry) -> None:
        with self._lock:
            if self._using_db and self._db is not None:
                try:
                    self._db.execute(
                        "INSERT INTO conversation_timeline "
                        "(timestamp, model, role, content_hash, entities_extracted, summary_snippet) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            entry.timestamp,
                            entry.model,
                            entry.role,
                            entry.content_hash,
                            json.dumps(entry.entities_extracted),
                            entry.summary_snippet,
                        ),
                    )
                    self._db.commit()
                except sqlite3.Error as exc:
                    logger.warning(
                        "Timeline DB write failed, storing in-memory: %s", exc
                    )
                    self._in_memory.append(entry)
            else:
                self._in_memory.append(entry)

    def recent(self, limit: int = 30) -> list[TimelineEntry]:
        """Return the most recent *limit* timeline entries, newest first."""
        with self._lock:
            if self._using_db and self._db is not None:
                try:
                    rows = self._db.execute(
                        "SELECT timestamp, model, role, content_hash, "
                        "entities_extracted, summary_snippet "
                        "FROM conversation_timeline "
                        "ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                    return [
                        TimelineEntry(
                            timestamp=row["timestamp"],
                            model=row["model"],
                            role=row["role"],
                            content_hash=row["content_hash"],
                            entities_extracted=json.loads(row["entities_extracted"]),
                            summary_snippet=row["summary_snippet"],
                        )
                        for row in rows
                    ]
                except sqlite3.Error as exc:
                    logger.warning("Timeline DB read failed: %s", exc)
                    return []
            else:
                return list(reversed(self._in_memory[-limit:]))

    def search(self, query: str, limit: int = 20) -> list[TimelineEntry]:
        """Search timeline entries by substring match on summary_snippet.

        Parameters
        ----------
        query : str
            Case-insensitive substring to search for.
        limit : int
            Maximum results to return.
        """
        with self._lock:
            if self._using_db and self._db is not None:
                try:
                    escaped = (
                        query.replace("\\", "\\\\")
                        .replace("%", "\\%")
                        .replace("_", "\\_")
                    )
                    rows = self._db.execute(
                        "SELECT timestamp, model, role, content_hash, "
                        "entities_extracted, summary_snippet "
                        "FROM conversation_timeline "
                        "WHERE summary_snippet LIKE ? ESCAPE '\\' "
                        "ORDER BY id DESC LIMIT ?",
                        (f"%{escaped}%", limit),
                    ).fetchall()
                    return [
                        TimelineEntry(
                            timestamp=row["timestamp"],
                            model=row["model"],
                            role=row["role"],
                            content_hash=row["content_hash"],
                            entities_extracted=json.loads(row["entities_extracted"]),
                            summary_snippet=row["summary_snippet"],
                        )
                        for row in rows
                    ]
                except sqlite3.Error as exc:
                    logger.warning("Timeline search failed: %s", exc)
                    return []
            else:
                q_lower = query.lower()
                matches = [
                    e
                    for e in reversed(self._in_memory)
                    if q_lower in e.summary_snippet.lower()
                ]
                return matches[:limit]

    def count(self) -> int:
        with self._lock:
            if self._using_db and self._db is not None:
                try:
                    row = self._db.execute(
                        "SELECT COUNT(*) AS cnt FROM conversation_timeline"
                    ).fetchone()
                    return row["cnt"]
                except sqlite3.Error:
                    return 0
            return len(self._in_memory)

    def prune(self, max_age_days: int = 30) -> int:
        """Delete timeline entries older than *max_age_days*.

        Returns the number of deleted rows.
        """
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

        with self._lock:
            if self._using_db and self._db is not None:
                try:
                    cur = self._db.execute(
                        "DELETE FROM conversation_timeline WHERE timestamp < ?",
                        (cutoff,),
                    )
                    self._db.commit()
                    deleted = cur.rowcount
                    if deleted:
                        logger.info(
                            "Pruned %d timeline entries older than %d days",
                            deleted,
                            max_age_days,
                        )
                    return deleted
                except sqlite3.Error as exc:
                    logger.warning("Timeline prune failed: %s", exc)
                    return 0
            else:
                before = len(self._in_memory)
                self._in_memory = [e for e in self._in_memory if e.timestamp >= cutoff]
                return before - len(self._in_memory)

    def vacuum(self) -> None:
        with self._lock:
            if self._using_db and self._db is not None:
                try:
                    self._db.execute("VACUUM")
                    logger.info("Timeline database vacuumed")
                except sqlite3.Error as exc:
                    logger.warning("Timeline VACUUM failed: %s", exc)

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                except (OSError, sqlite3.Error) as exc:
                    logger.warning("Failed to close timeline DB: %s", exc)
                self._db = None
                self._using_db = False


# Snippet redaction (S3)


def _redact_snippet(snippet: str, max_len: int = 50) -> str:
    """Truncate a summary snippet for API exposure.

    If the snippet is longer than *max_len*, show the first 20 chars,
    "...", and the last 20 chars.
    """
    if len(snippet) <= max_len:
        return snippet
    return f"{snippet[:20]}...{snippet[-20:]}"


# Encryption helpers (S1)


def _get_or_create_salt(salt_path: Path) -> bytes:
    """Return salt bytes from *salt_path*, creating the file if needed."""
    if salt_path.exists():
        return salt_path.read_bytes()
    salt = os.urandom(16)
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = salt_path.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_bytes(salt)
        os.replace(str(tmp), str(salt_path))
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                logger.debug("Failed to clean up temporary salt file")
        if salt_path.exists():
            return salt_path.read_bytes()
        raise
    return salt


def _derive_fernet_key(signing_key: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key using PBKDF2HMAC (same approach as sync module)."""
    try:
        from cryptography.hazmat.primitives import hashes as _hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as _imp_exc:
        raise ImportError(
            "cryptography package required for conversation state encryption"
        ) from _imp_exc

    kdf = PBKDF2HMAC(
        algorithm=_hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    raw_key = kdf.derive(signing_key.encode("utf-8"))
    return base64.urlsafe_b64encode(raw_key)


def _encrypt_json(payload: dict[str, Any], fernet_key: bytes) -> bytes:
    from cryptography.fernet import Fernet

    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    f = Fernet(fernet_key)
    return _ENCRYPTED_HEADER + f.encrypt(raw)


def _decrypt_json(data: bytes, fernet_key: bytes) -> dict[str, Any]:
    from cryptography.fernet import Fernet, InvalidToken

    if data.startswith(_ENCRYPTED_HEADER):
        data = data[len(_ENCRYPTED_HEADER) :]
    f = Fernet(fernet_key)
    try:
        raw = f.decrypt(data)
    except InvalidToken as exc:
        raise ValueError(f"Fernet decryption failed (invalid token): {exc}") from exc
    return json.loads(raw)


def _get_encryption_key(state_dir: Path) -> bytes | None:
    """Get Fernet key for conversation state encryption.

    Uses JARVIS_SIGNING_KEY env var.  Returns None if encryption is
    not available (no signing key set or cryptography not installed).
    """
    signing_key = os.environ.get(_ENCRYPTION_ENV_KEY, "")
    if not signing_key:
        return None
    try:
        salt_path = state_dir / _SALT_FILENAME
        salt = _get_or_create_salt(salt_path)
        return _derive_fernet_key(signing_key, salt)
    except (ImportError, OSError) as exc:
        logger.debug("Encryption unavailable for conversation state: %s", exc)
        return None


# PII masking helpers (S2)


def _mask_ssn(value: str) -> str:
    """Mask SSN: 123-45-6789 -> ***-**-6789"""
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "[REDACTED]"
    return f"***-**-{value[-4:]}"


def _mask_cc(value: str) -> str:
    """Mask credit card: 1234-5678-9012-3456 -> ****-****-****-3456"""
    digits = re.sub(r"[\s-]", "", value)
    return f"****-****-****-{digits[-4:]}"


def _mask_phone(value: str) -> str:
    """Mask phone: (555) 123-4567 -> ***-***-4567"""
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "[REDACTED]"
    return f"***-***-{digits[-4:]}"


def _mask_email(value: str) -> str:
    """Mask email: user@domain.com -> u***@domain.com"""
    parts = value.split("@")
    if len(parts) == 2 and parts[0]:
        return f"{parts[0][0]}***@{parts[1]}"
    return "***@***"


@dataclass
class PIIFilterResult:
    """Result of PII filtering on an entity."""

    value: str
    pii_detected: bool
    masked: bool


def filter_pii_entity(entity: str) -> PIIFilterResult:
    """Check entity for PII and return masked version if found.

    Returns a PIIFilterResult with the (possibly masked) value and a
    pii_detected flag for audit purposes.
    """
    # SSN — highest priority (most dangerous)
    if _RE_PII_SSN.fullmatch(entity):
        return PIIFilterResult(value=_mask_ssn(entity), pii_detected=True, masked=True)

    # Credit card
    if _RE_PII_CC.fullmatch(entity):
        return PIIFilterResult(value=_mask_cc(entity), pii_detected=True, masked=True)

    # Phone number
    if _RE_PII_PHONE.fullmatch(entity):
        return PIIFilterResult(
            value=_mask_phone(entity), pii_detected=True, masked=True
        )

    # Email address
    if _RE_PII_EMAIL.fullmatch(entity):
        return PIIFilterResult(
            value=_mask_email(entity), pii_detected=True, masked=True
        )

    return PIIFilterResult(value=entity, pii_detected=False, masked=False)


# Entity validation (S5)


def validate_entity(entity: str) -> bool:
    """Validate an entity string against poisoning attempts.

    Returns True if the entity is safe to store, False if it should be
    rejected.
    """
    # Max length check
    if len(entity) > _MAX_ENTITY_LENGTH:
        return False

    # Code block markers
    if _RE_CODE_BLOCK.search(entity):
        return False

    # URL-encoded content (3+ consecutive encoded bytes)
    if _RE_URL_ENCODED.search(entity):
        return False

    # Base64 blocks (long base64-looking strings)
    if _RE_BASE64_BLOCK.fullmatch(entity.strip()):
        return False

    # Prompt injection patterns
    if _RE_PROMPT_INJECTION.search(entity):
        return False

    return True


# Quality gate helpers


_RE_WORD_LIKE = re.compile(r"\b[a-zA-Z]{2,}\b")
_RE_CONSECUTIVE_REPEAT = re.compile(r"(.{2,4})\1{10,}")


def _is_garbled_text(text: str) -> bool:
    """Return True if *text* looks like garbled/nonsensical LLM output.

    Short strings (<=10 chars) are always accepted to avoid rejecting
    brief but legitimate responses like "ok" or "yes".

    Heuristics
    ----------
    1. Any single character (excluding spaces and common structural chars
       like quotes, braces, newlines) dominates >40 % of the text.
    2. Unique-char / total-char ratio < 0.05 for text longer than 50 chars.
    3. Fewer than 2 word-like tokens ([a-zA-Z]{2,}) in text longer than 20 chars
       — but exempts text that looks like URLs or JSON.
    4. A 2-4 char substring repeats more than 10 times consecutively.
    """
    if len(text) <= 10:
        return False

    # Strip spaces AND common structural characters that legitimately repeat
    # in JSON, code, and formatted text (quotes, braces, brackets, newlines).
    stripped = re.sub(r'[\s"\'{}()\[\]:,./\\]', "", text)
    if not stripped:
        return False

    from collections import Counter

    counts = Counter(stripped)
    max_freq = counts.most_common(1)[0][1]

    # 1. Repetitive single-character ratio
    if max_freq / len(stripped) > 0.40:
        return True

    # 2. Low unique character ratio (long text only)
    if len(stripped) > 50 and len(counts) / len(stripped) < 0.05:
        return True

    # 3. No word-like tokens (longer text only)
    # Exempt URLs (contain ://) and JSON (start with { or [)
    if len(text) > 20 and len(_RE_WORD_LIKE.findall(text)) < 2:
        if "://" not in text and not text.lstrip().startswith(("{", "[")):
            return True

    # 4. Consecutive repeated substrings (cap search length to avoid backtracking)
    search_text = text[:2000] if len(text) > 2000 else text
    if _RE_CONSECUTIVE_REPEAT.search(search_text):
        return True

    return False


# Entity extraction functions


def extract_entities(text: str) -> set[str]:
    """Extract named entities from text using regex patterns.

    Extracts:
    - People names (capitalized word sequences, Mr./Mrs./Dr. prefixes)
    - File paths (Unix and Windows patterns)
    - URLs (http/https patterns)
    - Dates and times (various formats)
    - Numbers/amounts with units

    Parameters
    ----------
    text : str
        The text to extract entities from.

    Returns
    -------
    set[str]
        Deduplicated set of extracted entity strings.
    """
    if not text:
        return set()

    entities: set[str] = set()

    _SIMPLE_PATTERNS = (
        _RE_URL, _RE_UNIX_PATH, _RE_WIN_PATH,
        _RE_DATE_ISO, _RE_DATE_SLASH, _RE_DATE_WRITTEN,
        _RE_TIME, _RE_AMOUNT, _RE_NAME_PREFIX,
    )
    for pat in _SIMPLE_PATTERNS:
        for m in pat.finditer(text):
            val = m.group(0).strip()
            if val:
                entities.add(val)

    for m in _RE_CAPITALIZED_SEQ.finditer(text):
        name = m.group(0)
        words = name.split()
        if all(w in _COMMON_WORDS for w in words) or len(words) > 4:
            continue
        entities.add(name)

    # S5: Validate entities against poisoning + S2: mask PII
    filtered: set[str] = set()
    pii_count = 0
    rejected_count = 0
    for entity in entities:
        # S5: reject entities that fail validation
        if not validate_entity(entity):
            rejected_count += 1
            continue
        # S2: mask PII instead of silently dropping
        result = filter_pii_entity(entity)
        if result.pii_detected:
            pii_count += 1
        filtered.add(result.value)

    # S5: enforce max entities per turn
    if len(filtered) > _MAX_ENTITIES_PER_TURN:
        # Keep the first N entities (sorted for determinism)
        filtered = set(sorted(filtered)[:_MAX_ENTITIES_PER_TURN])

    if pii_count:
        logger.info("PII masked in %d entities", pii_count)
    if rejected_count:
        logger.info("Rejected %d entities (validation failure)", rejected_count)

    return filtered


def extract_decisions(text: str) -> list[str]:
    """Extract decision/commitment statements from text.

    Looks for patterns like "I'll", "let's", "we decided", "agreed to",
    "going with", and similar commitment language.

    Parameters
    ----------
    text : str
        The text to scan for decisions.

    Returns
    -------
    list[str]
        List of extracted decision strings, deduplicated.
    """
    if not text:
        return []
    seen: set[str] = set()
    results: list[str] = []
    for m in _RE_DECISIONS.finditer(text):
        decision = m.group(1).strip()
        normalized = decision.lower()
        if normalized not in seen:
            seen.add(normalized)
            results.append(decision)
    return results


def extract_unresolved(text: str) -> list[str]:
    """Extract unresolved goals/tasks from text.

    Looks for patterns like "we still need", "todo:", "next step:",
    "remaining:", "we should", and similar incomplete-work language.

    Parameters
    ----------
    text : str
        The text to scan for unresolved items.

    Returns
    -------
    list[str]
        List of extracted unresolved goal strings, deduplicated.
    """
    if not text:
        return []
    seen: set[str] = set()
    results: list[str] = []
    for m in _RE_UNRESOLVED.finditer(text):
        goal = m.group(1).strip()
        normalized = goal.lower()
        if normalized not in seen:
            seen.add(normalized)
            results.append(goal)
    return results


def detect_goal_completion(text: str, goals: list[str]) -> list[str]:
    """Detect which unresolved goals appear to be addressed in the text.

    Uses keyword overlap to determine if a goal's key terms appear in
    the response text, suggesting the goal has been completed or addressed.

    Parameters
    ----------
    text : str
        The text to check for goal completion signals.
    goals : list[str]
        Current list of unresolved goals to check against.

    Returns
    -------
    list[str]
        Goals from the input list that appear to have been addressed.
    """
    if not text or not goals:
        return []

    text_lower = text.lower()
    # Completion signal words that strengthen a match
    completion_signals = {
        "done",
        "completed",
        "finished",
        "fixed",
        "resolved",
        "implemented",
        "added",
        "created",
        "updated",
        "removed",
        "deployed",
        "shipped",
    }
    has_signal = any(s in text_lower for s in completion_signals)

    completed: list[str] = []
    for goal in goals:
        # Extract significant words from the goal (skip short/common words)
        goal_words = {w.lower() for w in re.findall(r"\b\w{4,}\b", goal)}
        if not goal_words:
            continue
        # Check how many goal keywords appear in the text
        matches = sum(1 for w in goal_words if w in text_lower)
        ratio = matches / len(goal_words)
        # Require >50% keyword overlap, or >30% with a completion signal
        if ratio > 0.5 or (ratio > 0.3 and has_signal):
            completed.append(goal)

    return completed


# ConversationStateManager


class ConversationStateManager:
    """Persistent, thread-safe conversation state machine.

    Tracks entities, decisions, unresolved goals, model switches, and a
    rolling summary across LLM provider boundaries.  State is persisted to
    a JSON file with atomic writes so daemon restarts lose nothing.

    Parameters
    ----------
    state_dir : Path | None
        Directory for persistence files.  Defaults to
        ``<repo>/.planning/runtime/``.
    db_path : Path | None
        Path for the timeline SQLite database.  Defaults to
        ``<state_dir>/conversation_timeline.db``.
    """

    _SAVE_DEBOUNCE_SECONDS = 5.0

    def __init__(
        self,
        state_dir: Path | None = None,
        db_path: Path | None = None,
        *,
        encryption_key: bytes | None = ...,  # type: ignore[assignment]
    ) -> None:
        self._lock = threading.RLock()
        self._last_save_time: float = 0.0
        self._save_pending = False
        self._save_count = 0

        if state_dir is None:
            state_dir = repo_root() / _DEFAULT_STATE_DIR
        self._state_dir = state_dir
        self._state_file = self._state_dir / _STATE_FILENAME

        # S1: Fernet encryption key (lazy derive from env if not explicitly given)
        self._fernet_key: bytes | None
        if encryption_key is ...:
            self._fernet_key = _get_encryption_key(self._state_dir)
        else:
            self._fernet_key = encryption_key

        if db_path is None:
            db_path = self._state_dir / _TIMELINE_DB_FILENAME
        self._timeline = ConversationTimeline(db_path=db_path)

        self._snapshot = ConversationSnapshot()

        # Attempt to load persisted state
        self.load()

    # Turn tracking

    def update_turn(self, role: str, content: str, model: str) -> None:
        """Record a conversation turn and update extracted state.

        Updates turn_count, extracts entities from content, updates
        anchor_entities, extracts decisions and unresolved goals from
        assistant responses, detects goal completion, and records the
        turn in the timeline.

        Parameters
        ----------
        role : str
            Message role ("user" or "assistant").
        content : str
            The message content.
        model : str
            The model/provider that generated or received this turn.
        """
        with self._lock:
            # Quality gate: reject garbled/nonsensical assistant output.
            # User turns are always tracked (even if STT garbled them)
            # so the state machine doesn't lose turn counts.
            if role == "assistant" and _is_garbled_text(content):
                logger.warning(
                    "Rejected garbled content from assistant turn (len=%d)",
                    len(content),
                )
                return

            self._snapshot.turn_count += 1
            self._snapshot.active_model = model
            self._snapshot.updated_at = now_iso()

            # Extract entities
            entities = extract_entities(content)
            new_entities = entities - self._snapshot.anchor_entities
            if new_entities:
                self._snapshot.anchor_entities.update(new_entities)
                # Cap anchor entities at a reasonable size
                if len(self._snapshot.anchor_entities) > 200:
                    self._snapshot.anchor_entities = set(
                        sorted(self._snapshot.anchor_entities)[-200:]
                    )

            # Extract artifacts (URLs and file paths)
            urls = set(_RE_URL.findall(content))
            unix_paths = {m.group(0) for m in _RE_UNIX_PATH.finditer(content)}
            win_paths = {m.group(0) for m in _RE_WIN_PATH.finditer(content)}
            artifacts = urls | unix_paths | win_paths
            new_artifacts = artifacts - set(self._snapshot.referenced_artifacts)
            if new_artifacts:
                self._snapshot.referenced_artifacts.extend(sorted(new_artifacts))
                if len(self._snapshot.referenced_artifacts) > 200:
                    self._snapshot.referenced_artifacts = (
                        self._snapshot.referenced_artifacts[-200:]
                    )

            # Extract decisions (from any role, but primarily useful for assistant)
            decisions = extract_decisions(content)
            for d in decisions:
                if d not in self._snapshot.prior_decisions:
                    self._snapshot.prior_decisions.append(d)
            if len(self._snapshot.prior_decisions) > 50:
                self._snapshot.prior_decisions = self._snapshot.prior_decisions[-50:]

            # Detect goal completion on EXISTING goals before adding new
            # ones — prevents the same message from both creating and
            # immediately resolving a goal.
            completed = detect_goal_completion(content, self._snapshot.unresolved_goals)
            if completed:
                self._snapshot.unresolved_goals = [
                    g for g in self._snapshot.unresolved_goals if g not in completed
                ]

            # Extract new unresolved goals (after completion check)
            new_goals = extract_unresolved(content)
            for g in new_goals:
                if g not in self._snapshot.unresolved_goals:
                    self._snapshot.unresolved_goals.append(g)

            if len(self._snapshot.unresolved_goals) > 50:
                self._snapshot.unresolved_goals = self._snapshot.unresolved_goals[-50:]

            # Record in timeline
            content_hash = hashlib.sha256(
                content.encode("utf-8", errors="replace")
            ).hexdigest()[:16]
            snippet = content[:200].replace("\n", " ").strip()
            entry = TimelineEntry(
                timestamp=now_iso(),
                model=model,
                role=role,
                content_hash=content_hash,
                entities_extracted=sorted(entities),
                summary_snippet=snippet,
            )
            self._timeline.add_turn(entry)

            # Emit telemetry
            self._emit_entity_telemetry(entities)

        # Debounced save — avoid disk I/O on every single turn
        self._save_debounced()

    # Model switch tracking

    def mark_model_switch(
        self, from_model: str, to_model: str, reason: str = ""
    ) -> None:
        """Record a model/provider switch in the conversation.

        Appends to model_history and emits a continuity_reconstruction
        telemetry event.

        Parameters
        ----------
        from_model : str
            The model being switched away from.
        to_model : str
            The model being switched to.
        reason : str
            Human-readable reason for the switch (e.g. "fallback",
            "privacy_routing", "user_request").
        """
        with self._lock:
            self._snapshot.model_history.append(
                [to_model, self._snapshot.turn_count, reason]
            )
            self._snapshot.active_model = to_model
            self._snapshot.updated_at = now_iso()

            # Cap model history
            if len(self._snapshot.model_history) > 100:
                self._snapshot.model_history = self._snapshot.model_history[-100:]

            # Capture telemetry values under lock to avoid races
            _entities_count = len(self._snapshot.anchor_entities)
            _goals_count = len(self._snapshot.unresolved_goals)
            _summary_len = len(self._snapshot.rolling_summary)
            _turn_count = self._snapshot.turn_count

        # Emit telemetry (outside lock to avoid blocking)
        try:
            from jarvis_engine.memory.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.CONVERSATION_STATE,
                f"Model switch: {from_model} -> {to_model}",
                {
                    "event": "continuity_reconstruction",
                    "model_from": from_model,
                    "model_to": to_model,
                    "reason": reason,
                    "entities_preserved": _entities_count,
                    "goals_carried": _goals_count,
                    "summary_length": _summary_len,
                    "turn_count": _turn_count,
                },
            )
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Model switch telemetry failed: %s", exc)

        self.save()

    # Checkpointing

    def create_checkpoint(self, dropped_messages: list[dict[str, str]]) -> int:
        """Create a checkpoint from messages about to be dropped.

        Increments checkpoint_id, generates a rolling summary from the
        dropped messages by concatenating their content (truncated to fit
        a reasonable summary length), and persists the updated state.

        Parameters
        ----------
        dropped_messages : list[dict[str, str]]
            Messages being evicted from the context window.  Each dict
            should have ``role`` and ``content`` keys.

        Returns
        -------
        int
            The new checkpoint_id.
        """
        with self._lock:
            self._snapshot.checkpoint_id += 1
            self._snapshot.updated_at = now_iso()

            # Build summary from dropped messages.
            # Compute a per-message budget so the total summary is always
            # shorter than the raw dropped content.
            total_content_len = sum(
                len(msg.get("content", "")) for msg in dropped_messages
            )
            n_msgs = max(1, sum(1 for m in dropped_messages if m.get("content")))
            # Reserve room for separators and role tags, then divide remaining
            # budget across messages.  Minimum 20 chars per snippet.
            overhead_per_msg = 15  # "[role] " + " | "
            budget = max(
                total_content_len // 2,  # never exceed half the original
                n_msgs * 20,  # but at least 20 chars each
            )
            per_msg = max(20, (budget - n_msgs * overhead_per_msg) // n_msgs)

            parts: list[str] = []
            for msg in dropped_messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if content:
                    snippet = content[:per_msg].replace("\n", " ").strip()
                    parts.append(f"[{role}] {snippet}")

            combined = " | ".join(parts)
            # Merge with existing rolling summary
            if self._snapshot.rolling_summary:
                merged = f"{self._snapshot.rolling_summary} | {combined}"
            else:
                merged = combined
            # Cap rolling summary length
            if len(merged) > _MAX_ROLLING_SUMMARY_CHARS:
                merged = merged[-_MAX_ROLLING_SUMMARY_CHARS:]
            self._snapshot.rolling_summary = merged

            # Extract entities and decisions from dropped content
            full_text = " ".join(msg.get("content", "") for msg in dropped_messages)
            entities = extract_entities(full_text)
            new_entities = entities - self._snapshot.anchor_entities
            if new_entities:
                self._snapshot.anchor_entities.update(new_entities)

            decisions = extract_decisions(full_text)
            for d in decisions:
                if d not in self._snapshot.prior_decisions:
                    self._snapshot.prior_decisions.append(d)

            checkpoint_id = self._snapshot.checkpoint_id

        self.save()
        return checkpoint_id

    # Prompt injection helper

    def get_prompt_injection(self) -> dict[str, Any]:
        """Return state to inject into the system prompt for continuity.

        Provides the rolling summary, anchor entities, unresolved goals,
        prior decisions, active missions, and referenced artifacts so a
        new LLM provider can seamlessly continue the conversation.

        Returns
        -------
        dict
            Keys: ``rolling_summary``, ``anchor_entities``,
            ``unresolved_goals``, ``prior_decisions``,
            ``active_mission_ids``, ``referenced_artifacts``.
        """
        with self._lock:
            return {
                "rolling_summary": self._snapshot.rolling_summary,
                "anchor_entities": sorted(self._snapshot.anchor_entities),
                "unresolved_goals": list(self._snapshot.unresolved_goals),
                "prior_decisions": list(self._snapshot.prior_decisions),
                "active_mission_ids": list(self._snapshot.active_mission_ids),
                "referenced_artifacts": list(self._snapshot.referenced_artifacts),
            }

    # CTX-03: Mission and artifact tracking

    def track_mission(self, mission_id: str) -> None:
        """Track an active mission in the conversation state.

        Parameters
        ----------
        mission_id : str
            The mission identifier to track.
        """
        if not mission_id or not isinstance(mission_id, str):
            return
        mid = mission_id.strip()
        if not mid:
            return
        with self._lock:
            if mid not in self._snapshot.active_mission_ids:
                self._snapshot.active_mission_ids.append(mid)
                # Cap at 50 active missions
                if len(self._snapshot.active_mission_ids) > 50:
                    self._snapshot.active_mission_ids = (
                        self._snapshot.active_mission_ids[-50:]
                    )
                self._snapshot.updated_at = now_iso()
        self._save_debounced()

    def track_artifact(self, artifact_ref: str) -> None:
        """Track a referenced artifact in the conversation state.

        Parameters
        ----------
        artifact_ref : str
            The artifact reference (URL, file path, or identifier) to track.
        """
        if not artifact_ref or not isinstance(artifact_ref, str):
            return
        ref = artifact_ref.strip()
        if not ref:
            return
        with self._lock:
            if ref not in self._snapshot.referenced_artifacts:
                self._snapshot.referenced_artifacts.append(ref)
                if len(self._snapshot.referenced_artifacts) > 200:
                    self._snapshot.referenced_artifacts = (
                        self._snapshot.referenced_artifacts[-200:]
                    )
                self._snapshot.updated_at = now_iso()
        self._save_debounced()

    def get_active_missions(self) -> list[str]:
        """Return the list of active mission IDs.

        Returns
        -------
        list[str]
            Copy of the active mission IDs list.
        """
        with self._lock:
            return list(self._snapshot.active_mission_ids)

    # CTX-04: Transport-limit-aware compaction

    def compact_for_transport(self, max_chars: int = 8000) -> dict[str, Any]:
        """Return prompt injection data compacted to fit within *max_chars*.

        Prioritizes data by importance:
        1. anchor_entities (most important — identity/context anchors)
        2. unresolved_goals
        3. prior_decisions
        4. active_mission_ids
        5. referenced_artifacts
        6. rolling_summary (truncated last)

        Parameters
        ----------
        max_chars : int
            Maximum total character count for the serialized output.

        Returns
        -------
        dict[str, Any]
            Same keys as ``get_prompt_injection()`` but trimmed to fit.
        """
        with self._lock:
            data = self.get_prompt_injection()

        # Start with full data, iteratively trim to fit
        import json as _json

        def _measure(d: dict[str, Any]) -> int:
            return len(_json.dumps(d, ensure_ascii=False))

        # Phase 1: trim rolling_summary first (lowest priority text)
        if _measure(data) > max_chars and data.get("rolling_summary"):
            # Binary search for the right truncation point
            summary = data["rolling_summary"]
            lo, hi = 0, len(summary)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                data["rolling_summary"] = summary[:mid]
                if _measure(data) <= max_chars:
                    lo = mid
                else:
                    hi = mid - 1
            data["rolling_summary"] = summary[:lo]

        # Phase 2: trim referenced_artifacts
        if _measure(data) > max_chars:
            while data.get("referenced_artifacts") and _measure(data) > max_chars:
                data["referenced_artifacts"].pop()

        # Phase 3: trim active_mission_ids
        if _measure(data) > max_chars:
            while data.get("active_mission_ids") and _measure(data) > max_chars:
                data["active_mission_ids"].pop()

        # Phase 4: trim prior_decisions
        if _measure(data) > max_chars:
            while data.get("prior_decisions") and _measure(data) > max_chars:
                data["prior_decisions"].pop()

        # Phase 5: trim unresolved_goals
        if _measure(data) > max_chars:
            while data.get("unresolved_goals") and _measure(data) > max_chars:
                data["unresolved_goals"].pop()

        # Phase 6: trim entities as last resort
        if _measure(data) > max_chars:
            entities = data.get("anchor_entities", [])
            while entities and _measure(data) > max_chars:
                entities.pop()
            data["anchor_entities"] = entities

        return data

    # CTX-05: Unified cross-provider timeline

    def get_unified_timeline(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return timeline entries with normalized provider names.

        Each entry contains: timestamp, normalized_provider, role,
        content_hash, summary_snippet.

        Parameters
        ----------
        limit : int
            Maximum number of entries to return.

        Returns
        -------
        list[dict[str, Any]]
            Timeline entries with canonical provider names, newest first.
        """
        entries = self._timeline.recent(limit=limit)
        result: list[dict[str, Any]] = []
        for e in entries:
            result.append({
                "timestamp": e.timestamp,
                "normalized_provider": normalize_provider_name(e.model),
                "role": e.role,
                "content_hash": e.content_hash,
                "summary_snippet": e.summary_snippet,
            })
        return result

    # State snapshot (for mobile API / dashboard)

    def get_state_snapshot(self, *, full: bool = False) -> dict[str, Any]:
        """Return conversation state as a serializable dict.

        Parameters
        ----------
        full : bool
            When True, return complete unredacted data (for authenticated
            dashboard use only).  When False (default, used by the mobile
            API), redact sensitive fields per S3.

        Returns
        -------
        dict
            Snapshot with timeline count and recent timeline entries.
        """
        with self._lock:
            data = self._snapshot.to_dict()
            data["timeline_count"] = self._timeline.count()

            if full:
                # Full unredacted snapshot for authenticated dashboard
                data["recent_timeline"] = [
                    {
                        "timestamp": e.timestamp,
                        "model": e.model,
                        "role": e.role,
                        "content_hash": e.content_hash,
                        "summary_snippet": e.summary_snippet,
                    }
                    for e in self._timeline.recent(limit=10)
                ]
            else:
                # S3: Redacted snapshot — no full rolling_summary, truncated snippets
                data["summary_length"] = len(data.get("rolling_summary", ""))
                data["entity_count"] = len(data.get("anchor_entities", []))
                data.pop("rolling_summary", None)

                data["recent_timeline"] = [
                    {
                        "timestamp": e.timestamp,
                        "model": e.model,
                        "role": e.role,
                        "content_hash": e.content_hash,
                        "summary_snippet": _redact_snippet(e.summary_snippet),
                    }
                    for e in self._timeline.recent(limit=10)
                ]

            return data

    # Persistence

    def _save_debounced(self) -> None:
        """Save only if enough time has elapsed since the last save.

        Mirrors the debounce pattern from ``voice_pipeline.ConversationState``.
        Callers should use ``save()`` for immediate persistence (e.g. on
        shutdown).
        """
        with self._lock:
            now = time.monotonic()
            if now - self._last_save_time < self._SAVE_DEBOUNCE_SECONDS:
                self._save_pending = True
                return
        self.save()

    def flush_pending(self) -> None:
        """Force-save if a debounced save is pending (call on shutdown)."""
        with self._lock:
            pending = self._save_pending
        if pending:
            self.save()

    def save(self) -> None:
        """Persist conversation state to disk atomically.

        Uses Fernet encryption when a signing key is available (S1).
        Uses a temporary file + ``os.replace`` for crash safety.  Errors
        are logged but do not propagate.
        """
        with self._lock:
            payload = self._snapshot.to_dict()
            self._save_count += 1
            do_prune = (self._save_count % _TIMELINE_PRUNE_INTERVAL) == 0

        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(
                f".tmp.{os.getpid()}.{threading.get_ident()}"
            )
            if self._fernet_key is not None:
                encrypted = _encrypt_json(payload, self._fernet_key)
                tmp.write_bytes(encrypted)
            else:
                raw = json.dumps(payload, ensure_ascii=False, indent=2)
                tmp.write_text(raw, encoding="utf-8")
            os.replace(str(tmp), str(self._state_file))
            self._last_save_time = time.monotonic()
            self._save_pending = False
        except OSError as exc:
            logger.debug("Could not save conversation state: %s", exc)

        # S4: periodic timeline pruning
        if do_prune:
            try:
                deleted = self._timeline.prune(max_age_days=_TIMELINE_MAX_AGE_DAYS)
                if deleted >= _TIMELINE_VACUUM_THRESHOLD:
                    self._timeline.vacuum()
            except (OSError, sqlite3.Error) as exc:
                logger.debug("Timeline prune during save failed: %s", exc)

    def load(self) -> None:
        """Load conversation state from disk if the file exists.

        Handles both encrypted and plaintext files (S1). If an
        unencrypted file exists and a Fernet key is available, the file
        is automatically re-encrypted on first load (graceful migration).
        Tolerates missing files, corrupt JSON, and schema changes
        gracefully.  On any error, the current state is left unchanged.
        """
        if not self._state_file.exists():
            return

        try:
            raw_bytes = self._state_file.read_bytes()
            data: dict[str, Any] | None = None
            was_plaintext = False

            if raw_bytes.startswith(_ENCRYPTED_HEADER):
                # File is encrypted
                if self._fernet_key is not None:
                    data = _decrypt_json(raw_bytes, self._fernet_key)
                else:
                    logger.warning(
                        "Conversation state file is encrypted but no key is available"
                    )
                    return
            else:
                # File is plaintext JSON — try to parse
                try:
                    data = json.loads(raw_bytes.decode("utf-8"))
                    was_plaintext = True
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Might be encrypted without header (shouldn't happen, but be safe)
                    if self._fernet_key is not None:
                        try:
                            data = _decrypt_json(raw_bytes, self._fernet_key)
                        except (ValueError, TypeError, KeyError) as _fernet_exc:
                            logger.debug(
                                "Fernet decryption failed for conversation state file: %s",
                                _fernet_exc,
                            )
                    if data is None:
                        logger.warning("Could not parse conversation state file")
                        return

            if isinstance(data, dict):
                with self._lock:
                    self._snapshot = ConversationSnapshot.from_dict(data)
                logger.debug(
                    "Loaded conversation state: session=%s, turns=%d",
                    self._snapshot.session_id,
                    self._snapshot.turn_count,
                )
                # S1: Graceful migration — encrypt plaintext file on first load
                if was_plaintext and self._fernet_key is not None:
                    logger.info(
                        "Migrating plaintext conversation state to encrypted format"
                    )
                    self.save()
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Could not load conversation state: %s", exc)
        except (KeyError, AttributeError, RuntimeError) as exc:
            logger.warning("Could not decrypt conversation state: %s", exc)
        except ImportError as exc:
            logger.warning("Could not load conversation state (missing crypto lib): %s", exc)

    # Reset

    def reset(self) -> None:
        """Reset the conversation state, starting a new session.

        Generates a new session_id and clears transient state.  Preserves
        anchor_entities and prior_decisions across resets so long-term
        context is not lost.
        """
        with self._lock:
            preserved_entities = set(self._snapshot.anchor_entities)
            preserved_decisions = list(self._snapshot.prior_decisions)

            self._snapshot = ConversationSnapshot()
            self._snapshot.anchor_entities = preserved_entities
            self._snapshot.prior_decisions = preserved_decisions
            session_id = self._snapshot.session_id

        # Emit telemetry
        try:
            from jarvis_engine.memory.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.CONVERSATION_STATE,
                f"Session reset: new session {session_id}",
                {
                    "event": "session_resume",
                    "entities_loaded": len(preserved_entities),
                    "decisions_preserved": len(preserved_decisions),
                },
            )
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Session reset telemetry failed: %s", exc)

        self.save()

    # Timeline access

    @property
    def timeline(self) -> ConversationTimeline:
        return self._timeline

    @property
    def snapshot(self) -> ConversationSnapshot:
        """Return a defensive copy of the conversation snapshot.

        Thread-safe: acquires the lock and copies the snapshot so callers
        cannot mutate internal state or encounter races.
        """
        with self._lock:
            return ConversationSnapshot.from_dict(self._snapshot.to_dict())

    # Cleanup

    def close(self) -> None:
        self.save()
        self._timeline.close()

    # Telemetry helpers (private)

    def _emit_entity_telemetry(self, entities: set[str]) -> None:
        if not entities:
            return
        try:
            from jarvis_engine.memory.activity_feed import ActivityCategory, log_activity

            # Classify entity types for telemetry
            types: dict[str, int] = {}
            for e in entities:
                if _RE_URL.fullmatch(e):
                    types["url"] = types.get("url", 0) + 1
                elif _RE_WIN_PATH.fullmatch(e) or _RE_UNIX_PATH.fullmatch(e):
                    types["file_path"] = types.get("file_path", 0) + 1
                elif _RE_DATE_ISO.fullmatch(e) or _RE_DATE_SLASH.fullmatch(e):
                    types["date"] = types.get("date", 0) + 1
                elif _RE_AMOUNT.fullmatch(e):
                    types["amount"] = types.get("amount", 0) + 1
                else:
                    types["name_or_other"] = types.get("name_or_other", 0) + 1

            log_activity(
                ActivityCategory.CONVERSATION_STATE,
                f"Extracted {len(entities)} entities from turn",
                {
                    "event": "entity_extraction",
                    "entities_found": len(entities),
                    "types": types,
                },
            )
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("Entity telemetry failed: %s", exc)


# Module-level singleton

_state_holder: dict[str, ConversationStateManager | None] = {"instance": None}
_manager_lock = threading.Lock()


def get_conversation_state(
    state_dir: Path | None = None,
) -> ConversationStateManager:
    """Return (or create) the module-level ConversationStateManager singleton.

    Uses double-checked locking to avoid repeated initialization while
    minimizing contention on the lock.

    Parameters
    ----------
    state_dir : Path | None
        Directory for persistence files.  Only used on first call.
        Defaults to ``<repo>/.planning/runtime/``.
    """
    if _state_holder["instance"] is not None:
        return _state_holder["instance"]
    with _manager_lock:
        # Double-checked locking
        inst = _state_holder["instance"]
        if inst is not None:
            return inst
        mgr = ConversationStateManager(state_dir=state_dir)
        _state_holder["instance"] = mgr
        return mgr


def _reset_manager() -> None:
    with _manager_lock:
        if _state_holder["instance"] is not None:
            try:
                _state_holder["instance"].close()
            except (OSError, sqlite3.Error) as exc:
                logger.warning(
                    "Failed to close conversation state singleton during reset: %s",
                    exc,
                )
            _state_holder["instance"] = None
