"""Tamper-evident forensic log with hash-chain integrity.

Every entry includes a SHA-256 hash of the previous entry's JSON line,
forming a verifiable chain.  Supports rotation and law-enforcement export.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import zipfile
from pathlib import Path

from jarvis_engine._shared import now_iso as _now_iso, sha256_hex

logger = logging.getLogger(__name__)

_ZERO_HASH = "0" * 64  # prev_hash for the very first entry


class ForensicLogger:
    """Write tamper-evident JSONL entries with a SHA-256 hash chain.

    Parameters
    ----------
    log_dir:
        Directory where the forensic log lives.  The primary file is
        ``forensic_log.jsonl`` inside *log_dir*.
    """

    def __init__(self, log_dir: Path) -> None:
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "forensic_log.jsonl"
        self._lock = threading.Lock()
        self._prev_hash: str = self._recover_last_hash()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_event(self, event: dict) -> None:
        """Append *event* to the forensic log with timestamp and hash chain."""
        with self._lock:
            entry = dict(event)
            entry["timestamp_utc"] = _now_iso()
            entry["prev_hash"] = self._prev_hash

            line = json.dumps(entry, separators=(",", ":"), sort_keys=True)
            current_hash = sha256_hex(line)

            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                # Only advance hash chain after successful write
                self._prev_hash = current_hash
            except OSError as exc:
                logger.warning("Failed to write forensic log entry to %s: %s", self._path, exc)

    @staticmethod
    def verify_chain(path: Path) -> tuple[bool, int]:
        """Verify the hash chain of a forensic log file.

        Returns ``(valid, entries_checked)``.  An empty file is considered
        valid with 0 entries checked.
        """
        if not path.exists():
            return (True, 0)

        prev_hash = _ZERO_HASH
        count = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_no, raw_line in enumerate(f, 1):
                    raw_line = raw_line.rstrip("\n")
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        logger.warning("Corrupt JSON at line %d", line_no)
                        return (False, count)

                    if entry.get("prev_hash") != prev_hash:
                        logger.warning(
                            "Hash chain broken at line %d: expected %s, got %s",
                            line_no,
                            prev_hash,
                            entry.get("prev_hash"),
                        )
                        return (False, count)

                    prev_hash = sha256_hex(raw_line)
                    count += 1
        except OSError:
            return (False, count)

        return (True, count)

    def rotate_if_needed(self, max_bytes: int = 50_000_000) -> None:
        """Rotate the log file if it exceeds *max_bytes*.

        Keeps up to 10 rotated files (``.1`` through ``.10``).
        """
        with self._lock:
            try:
                size = os.path.getsize(self._path)
            except OSError:
                return
            if size < max_bytes:
                return
            self._do_rotate()

    def export_for_law_enforcement(
        self,
        start_date: str,
        end_date: str,
        output_path: Path,
    ) -> None:
        """Export filtered log entries to a ZIP archive.

        Parameters
        ----------
        start_date / end_date:
            ISO-8601 date strings (``YYYY-MM-DD``).  Entries whose
            ``timestamp_utc`` falls within ``[start_date, end_date]``
            (inclusive, prefix match) are included.
        output_path:
            Destination ``.zip`` file.
        """
        entries: list[str] = []
        categories: dict[str, int] = {}
        severities: dict[str, int] = {}

        # Read the entire log file under lock so the read is atomic with
        # respect to concurrent log_event() writes.
        with self._lock:
            log_path = self._path if self._path.exists() else None
            if log_path is not None:
                try:
                    with open(log_path, "r", encoding="utf-8") as f:
                        for raw_line in f:
                            raw_line = raw_line.rstrip("\n")
                            if not raw_line:
                                continue
                            try:
                                entry = json.loads(raw_line)
                            except json.JSONDecodeError:
                                continue
                            ts = entry.get("timestamp_utc", "")
                            # Compare date prefix (YYYY-MM-DD)
                            date_part = ts[:10]
                            if start_date <= date_part <= end_date:
                                entries.append(raw_line)
                                cat = entry.get("category", "unknown")
                                categories[cat] = categories.get(cat, 0) + 1
                                sev = entry.get("severity", "unknown")
                                severities[sev] = severities.get(sev, 0) + 1
                except OSError:
                    logger.warning("Failed to read forensic log for export", exc_info=True)

        summary_lines = [
            "Forensic Log Export Summary",
            "===========================",
            f"Date range: {start_date} to {end_date}",
            f"Total entries: {len(entries)}",
            "",
            "Categories:",
        ]
        for cat, cnt in sorted(categories.items()):
            summary_lines.append(f"  {cat}: {cnt}")
        summary_lines.append("")
        summary_lines.append("Severities:")
        for sev, cnt in sorted(severities.items()):
            summary_lines.append(f"  {sev}: {cnt}")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("forensic_log.jsonl", ("\n".join(entries) + "\n") if entries else "")
            zf.writestr("summary.txt", "\n".join(summary_lines) + "\n")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _recover_last_hash(self) -> str:
        """Read the last line of the log to restore the hash chain.

        Uses progressively larger reads to handle large JSON lines
        (up to 1 MB). Falls back to full-file scan if needed.
        """
        if not self._path.exists():
            return _ZERO_HASH
        try:
            with open(self._path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return _ZERO_HASH
                # Try progressively larger reads to find a complete last line
                for read_size in (8192, 65536, 524288, size):
                    actual_read = min(size, read_size)
                    f.seek(max(0, size - actual_read))
                    tail = f.read().decode("utf-8", errors="replace")
                    lines = tail.strip().splitlines()
                    if not lines:
                        continue
                    # Try lines from end backwards to handle truncated last line
                    for line in reversed(lines):
                        try:
                            json.loads(line)
                            return sha256_hex(line)
                        except (json.JSONDecodeError, ValueError):
                            continue
                    if actual_read >= size:
                        # We've read the entire file and no line parses
                        break
        except OSError:
            return _ZERO_HASH

        return _ZERO_HASH

    def _do_rotate(self) -> None:
        """Perform rotation — must be called with self._lock held."""
        # Shift existing rotated files
        for i in range(10, 0, -1):
            src = self._path.with_suffix(f".jsonl.{i}")
            if i == 10:
                # Delete the oldest
                if src.exists():
                    src.unlink()
            else:
                dst = self._path.with_suffix(f".jsonl.{i + 1}")
                if src.exists():
                    shutil.move(str(src), str(dst))

        # Move current to .1
        rotated = self._path.with_suffix(".jsonl.1")
        try:
            if rotated.exists():
                rotated.unlink()
            self._path.rename(rotated)
            logger.info("Rotated forensic log -> %s", rotated)
        except OSError as exc:
            logger.warning("Failed to rotate forensic log: %s", exc)

        # Reset hash chain for the fresh file
        self._prev_hash = _ZERO_HASH
