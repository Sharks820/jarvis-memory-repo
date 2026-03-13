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

from jarvis_engine._shared import now_iso as _now_iso
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

# Entity extraction patterns (compiled once)
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

# PII patterns — matches are masked before storage in anchor_entities
# These are used with fullmatch() on extracted entity strings, so anchors
# like \b are not needed (and would interfere with parenthesized phones).
_RE_PII_PHONE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_RE_PII_SSN = re.compile(r"\d{3}-\d{2}-\d{4}")
_RE_PII_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_PII_CC = re.compile(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}")

# Decision detection patterns
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

# Unresolved goal detection patterns
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
    active_model: str = ""
    model_history: list[list[Any]] = field(default_factory=list)
    turn_count: int = 0
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

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
        """Create the timeline table if it does not exist."""
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
        """Append a turn to the timeline.  Thread-safe."""
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
        """Return the total number of timeline entries."""
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
        """Run VACUUM on the timeline database to reclaim space (S4)."""
        with self._lock:
            if self._using_db and self._db is not None:
                try:
                    self._db.execute("VACUUM")
                    logger.info("Timeline database vacuumed")
                except sqlite3.Error as exc:
                    logger.warning("Timeline VACUUM failed: %s", exc)

    def close(self) -> None:
        """Close the database connection (idempotent)."""
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
    """Serialize *payload* to JSON and encrypt with Fernet."""
    from cryptography.fernet import Fernet

    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    f = Fernet(fernet_key)
    return _ENCRYPTED_HEADER + f.encrypt(raw)


def _decrypt_json(data: bytes, fernet_key: bytes) -> dict[str, Any]:
    """Decrypt Fernet-encrypted JSON data."""
    from cryptography.fernet import Fernet

    if data.startswith(_ENCRYPTED_HEADER):
        data = data[len(_ENCRYPTED_HEADER) :]
    f = Fernet(fernet_key)
    raw = f.decrypt(data)
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
    return f"***-**-{value[-4:]}"


def _mask_cc(value: str) -> str:
    """Mask credit card: 1234-5678-9012-3456 -> ****-****-****-3456"""
    digits = re.sub(r"[\s-]", "", value)
    return f"****-****-****-{digits[-4:]}"


def _mask_phone(value: str) -> str:
    """Mask phone: (555) 123-4567 -> ***-***-4567"""
    digits = re.sub(r"\D", "", value)
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
            self._snapshot.turn_count += 1
            self._snapshot.active_model = model
            self._snapshot.updated_at = _now_iso()

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
                timestamp=_now_iso(),
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
            self._snapshot.updated_at = _now_iso()

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
            from jarvis_engine.activity_feed import ActivityCategory, log_activity

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
            self._snapshot.updated_at = _now_iso()

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
            # Cap rolling summary at 2000 chars
            if len(merged) > 2000:
                merged = merged[-2000:]
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
        and prior decisions so a new LLM provider can seamlessly continue
        the conversation.

        Returns
        -------
        dict
            Keys: ``rolling_summary``, ``anchor_entities``,
            ``unresolved_goals``, ``prior_decisions``.
        """
        with self._lock:
            return {
                "rolling_summary": self._snapshot.rolling_summary,
                "anchor_entities": list(self._snapshot.anchor_entities),
                "unresolved_goals": list(self._snapshot.unresolved_goals),
                "prior_decisions": list(self._snapshot.prior_decisions),
            }

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
        now = time.monotonic()
        if now - self._last_save_time < self._SAVE_DEBOUNCE_SECONDS:
            self._save_pending = True
            return
        self.save()

    def flush_pending(self) -> None:
        """Force-save if a debounced save is pending (call on shutdown)."""
        if self._save_pending:
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
                        except Exception:  # noqa: BLE001 — last-resort decryption attempt
                            logger.debug(
                                "Fernet decryption failed for conversation state file"
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
        except Exception as exc:  # noqa: BLE001 — cryptography.fernet.InvalidToken and similar
            logger.warning("Could not decrypt conversation state: %s", exc)

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

        # Emit telemetry
        try:
            from jarvis_engine.activity_feed import ActivityCategory, log_activity

            log_activity(
                ActivityCategory.CONVERSATION_STATE,
                f"Session reset: new session {self._snapshot.session_id}",
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
        """Return the conversation timeline instance."""
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
        """Close the timeline database and persist final state."""
        self.save()
        self._timeline.close()

    # Telemetry helpers (private)

    def _emit_entity_telemetry(self, entities: set[str]) -> None:
        """Emit an entity_extraction telemetry event."""
        if not entities:
            return
        try:
            from jarvis_engine.activity_feed import ActivityCategory, log_activity

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

_conversation_state: ConversationStateManager | None = None
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
    global _conversation_state
    if _conversation_state is not None:
        return _conversation_state
    with _manager_lock:
        # Double-checked locking
        if _conversation_state is not None:
            return _conversation_state
        _conversation_state = ConversationStateManager(state_dir=state_dir)
        return _conversation_state


def _reset_manager() -> None:
    """Close and discard the module-level singleton.  Test-only."""
    global _conversation_state
    with _manager_lock:
        if _conversation_state is not None:
            try:
                _conversation_state.close()
            except (OSError, sqlite3.Error) as exc:
                logger.warning(
                    "Failed to close conversation state singleton during reset: %s",
                    exc,
                )
            _conversation_state = None
