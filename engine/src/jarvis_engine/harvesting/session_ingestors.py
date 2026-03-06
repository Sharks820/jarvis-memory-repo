"""Session ingestors for Claude Code and Codex JSONL files.

Parses local session JSONL files to extract assistant knowledge-bearing messages.
Handles missing directories, malformed JSON, and permission errors gracefully.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class ClaudeCodeIngestor:
    """Parse Claude Code session JSONL files for assistant knowledge.

    Discovers session files under ``~/.claude/projects/`` (or the directory
    specified by the ``CLAUDE_CONFIG_DIR`` environment variable).
    """

    def __init__(self) -> None:
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
        if config_dir:
            self.SESSION_BASE = Path(config_dir) / "projects"
        else:
            self.SESSION_BASE = Path.home() / ".claude" / "projects"

    def find_sessions(self, project_path: str | None = None) -> list[Path]:
        """Glob for ``*.jsonl`` files under SESSION_BASE.

        Args:
            project_path: Optional project subdirectory to scope the search.

        Returns:
            List of session file paths sorted by modification time (newest first).
        """
        try:
            base = self.SESSION_BASE
            if project_path:
                candidate = (base / project_path).resolve()
                # Prevent path traversal outside SESSION_BASE
                try:
                    candidate.relative_to(base.resolve())
                except ValueError:
                    logger.warning("Path traversal blocked: %s", project_path)
                    return []
                base = candidate
            if not base.exists():
                return []
            files = list(base.rglob("*.jsonl"))
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return files
        except PermissionError:
            logger.warning("Permission denied accessing %s", self.SESSION_BASE)
            return []
        except FileNotFoundError:
            return []

    def ingest_session(self, session_path: Path) -> list[str]:
        """Read a session JSONL file and extract assistant text content.

        Filters for ``type == "assistant"`` entries and extracts text blocks
        longer than 100 characters (short blocks are typically tool outputs).

        Args:
            session_path: Path to a ``.jsonl`` session file.

        Returns:
            List of extracted text strings.
        """
        return _parse_session_jsonl(session_path, entry_type="assistant")


class CodexIngestor:
    """Parse Codex session JSONL files for knowledge content.

    Discovers ``rollout-*.jsonl`` files under ``~/.codex/sessions/``
    (or the directory specified by the ``CODEX_HOME`` environment variable).
    """

    def __init__(self) -> None:
        codex_home = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
        self.SESSION_BASE = Path(codex_home) / "sessions"

    def find_sessions(self, days_back: int = 7) -> list[Path]:
        """Glob for ``rollout-*.jsonl`` files under SESSION_BASE.

        Args:
            days_back: Not currently used for filtering (all files returned,
                sorted by modification time). Reserved for future date-based
                pruning.

        Returns:
            List of session file paths sorted by modification time (newest first).
        """
        try:
            if not self.SESSION_BASE.exists():
                return []
            files = list(self.SESSION_BASE.glob("rollout-*.jsonl"))
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return files
        except PermissionError:
            logger.warning("Permission denied accessing %s", self.SESSION_BASE)
            return []
        except FileNotFoundError:
            return []

    def ingest_session(self, session_path: Path) -> list[str]:
        """Read a Codex session JSONL file and extract assistant text content.

        Args:
            session_path: Path to a ``rollout-*.jsonl`` session file.

        Returns:
            List of extracted text strings.
        """
        return _parse_session_jsonl(session_path, entry_type="assistant")


# ---------------------------------------------------------------------------
# Shared JSONL parsing
# ---------------------------------------------------------------------------


def _parse_session_jsonl(path: Path, entry_type: str = "assistant") -> list[str]:
    """Parse a JSONL session file and extract text content from matching entries.

    Handles both string and list-of-blocks content format.  Skips malformed
    JSON lines gracefully.  Returns empty list on ``FileNotFoundError`` or
    ``PermissionError``.

    Args:
        path: Path to the JSONL file.
        entry_type: Entry ``type`` value to filter for (default ``"assistant"``).

    Returns:
        List of extracted text strings (each >100 chars).
    """
    extracted: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed JSONL line in session history")
                    continue

                if not isinstance(entry, dict):
                    continue

                # Check entry type
                if entry.get("type") != entry_type:
                    continue

                # Extract text content from message.content
                message = entry.get("message", {})
                if not isinstance(message, dict):
                    continue
                content = message.get("content", "")

                texts = _extract_texts(content)
                for text in texts:
                    if len(text) > 100:
                        extracted.append(text)

    except FileNotFoundError:
        return []
    except PermissionError:
        logger.warning("Permission denied reading %s", path)
        return []

    return extracted


def _extract_texts(content) -> list[str]:
    """Extract text strings from content (string or list-of-blocks format).

    Args:
        content: Either a plain string or a list of content blocks
            (each with a ``type`` and ``text`` field).

    Returns:
        List of text strings.
    """
    if isinstance(content, str):
        return [content] if content.strip() else []

    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
            elif isinstance(block, str) and block.strip():
                texts.append(block)
        return texts

    return []
