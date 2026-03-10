"""JSONL-to-SQLite migration: imports brain records, facts, and events into MemoryEngine.

Supports resumable migration via checkpoint files.
Includes count verification: inserted + skipped + errors == source_count.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from jarvis_engine._shared import now_iso as _now_iso, sha256_hex, sha256_short

if TYPE_CHECKING:
    from jarvis_engine.memory.classify import BranchClassifier
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)


class _MigrationResultBase(TypedDict):
    """Required keys shared by all migration result dicts."""

    status: str
    source_count: int
    inserted: int
    errors: int


class MigrationResult(_MigrationResultBase, total=False):
    """Result from :func:`migrate_brain_records` and :func:`migrate_events`.

    ``skipped`` is present for brain-record and event migrations.
    ``error_details`` is present for brain-record migrations and
    fact migrations that encounter read errors.
    """

    skipped: int
    error_details: list[str]


class ProcessLinesResult(TypedDict):
    """Result from :func:`_process_lines`."""

    inserted: int
    skipped: int
    errors: int
    error_details: list[str]


class _MigrationTotals(TypedDict):
    """Aggregate counts in :class:`FullMigrationResult`."""

    inserted: int
    skipped: int
    errors: int


class FullMigrationResult(TypedDict):
    """Result from :func:`run_full_migration`."""

    status: str
    brain: MigrationResult
    facts: MigrationResult
    events: MigrationResult
    totals: _MigrationTotals
    db_path: str


_CHECKPOINT_BATCH_SIZE = 50
_MAX_ERROR_DETAILS = 200


def _load_checkpoint(checkpoint_path: Path) -> dict | None:
    """Load migration checkpoint if it exists."""
    from jarvis_engine._shared import load_json_file

    return load_json_file(checkpoint_path, None)


def _save_checkpoint(checkpoint_path: Path, data: dict) -> None:
    """Save migration checkpoint."""
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")


def _delete_checkpoint(checkpoint_path: Path) -> None:
    """Delete checkpoint on completion."""
    try:
        checkpoint_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug(
            "Failed to delete migration checkpoint %s: %s", checkpoint_path, exc
        )


def _resolve_resume_offset(checkpoint: dict | None, jsonl_name: str) -> int:
    """Determine the line offset to resume from based on a checkpoint.

    Returns 0 when no valid checkpoint exists or the checkpoint belongs to a
    different file.
    """
    if not checkpoint or checkpoint.get("file") != jsonl_name:
        return 0
    saved_offset = checkpoint.get("line_offset", 0)
    if isinstance(saved_offset, int) and saved_offset >= 0:
        logger.info("Resuming migration from line %d", saved_offset)
        return saved_offset
    logger.warning("Invalid checkpoint offset %r, starting from 0", saved_offset)
    return 0


def _read_jsonl_lines(jsonl_path: Path) -> list[str] | MigrationResult:
    """Read non-empty lines from a JSONL file.

    Returns a list of stripped lines on success or a :class:`MigrationResult`
    error dict if the file cannot be read.
    """
    try:
        with jsonl_path.open(encoding="utf-8", errors="replace") as f:
            return [line for line in f if line.strip()]
    except OSError as exc:
        return {
            "status": "error",
            "source_count": 0,
            "inserted": 0,
            "skipped": 0,
            "errors": 1,
            "error_details": [f"Failed to read file: {exc}"],
        }


def _extract_summary(record_data: dict) -> str:
    """Extract a usable summary string from a brain record dict.

    Returns an empty string when neither ``summary`` nor ``content`` yields
    non-whitespace text.
    """
    summary = str(record_data.get("summary", ""))
    if not summary.strip():
        summary = str(record_data.get("content", ""))[:280]
    return summary


def _derive_record_id(record_data: dict, content_hash: str) -> str:
    """Derive a 32-hex-char record ID from the raw record data."""
    original_id = str(record_data.get("record_id", ""))
    if len(original_id) < 32:
        id_material = f"{original_id}|{content_hash}".encode("utf-8")
        return sha256_short(id_material)
    return original_id[:32]


def _transform_brain_record(
    record_data: dict,
    embed_service: "EmbeddingService",
    classifier: "BranchClassifier",
) -> dict | None:
    """Transform a parsed JSONL record into a MemoryEngine record dict.

    Returns ``None`` when the record has no usable summary/content and should
    be counted as an error.
    """
    summary = _extract_summary(record_data)
    if not summary.strip():
        return None

    embedding = embed_service.embed(summary, prefix="search_document")
    branch = classifier.classify(embedding)

    content_hash = str(record_data.get("content_hash", "")) or sha256_hex(summary)
    record_id = _derive_record_id(record_data, content_hash)

    ts = str(record_data.get("ts", _now_iso()))
    confidence = 0.72
    try:
        confidence = float(record_data.get("confidence", 0.72))
    except (TypeError, ValueError):
        logger.debug(
            "Invalid confidence value in record %s, using default",
            record_data.get("record_id", ""),
        )

    tags = record_data.get("tags", [])
    if isinstance(tags, list):
        tags = json.dumps(tags)
    elif not isinstance(tags, str):
        tags = "[]"

    record = {
        "record_id": record_id,
        "ts": ts,
        "source": str(record_data.get("source", "migration")),
        "kind": str(record_data.get("kind", "episodic")),
        "task_id": str(record_data.get("task_id", "")),
        "branch": branch,
        "tags": tags,
        "summary": summary[:2000],
        "content_hash": content_hash,
        "confidence": max(0.0, min(1.0, confidence)),
        "tier": "warm",
        "access_count": 0,
        "last_accessed": "",
        "_embedding": embedding,
    }
    return record


def _verify_migration_counts(
    inserted: int,
    skipped: int,
    errors: int,
    source_count: int,
    already_processed: int,
    error_details: list[str],
) -> None:
    """Append a count-mismatch error if processed != expected."""
    current_processed = inserted + skipped + errors
    expected_current = source_count - already_processed
    if current_processed != expected_current:
        msg = (
            f"Count mismatch: processed={current_processed} "
            f"(inserted={inserted} + skipped={skipped} + errors={errors}) "
            f"!= expected={expected_current} (source={source_count} - offset={already_processed})"
        )
        logger.error(msg)
        error_details.append(msg)


def _process_brain_lines(
    lines: list[str],
    start_offset: int,
    engine: "MemoryEngine",
    embed_service: "EmbeddingService",
    classifier: "BranchClassifier",
    checkpoint_path: Path,
    jsonl_name: str,
    source_count: int,
) -> ProcessLinesResult:
    """Process JSONL lines, transforming and inserting each brain record.

    Returns a dict with ``inserted``, ``skipped``, ``errors``, and
    ``error_details`` counts.
    """
    inserted = 0
    skipped = 0
    errors = 0
    error_details: list[str] = []

    for line_num, line in enumerate(lines):
        if line_num < start_offset:
            continue

        try:
            record_data = json.loads(line)
        except json.JSONDecodeError as exc:
            errors += 1
            if len(error_details) < _MAX_ERROR_DETAILS:
                error_details.append(f"Line {line_num + 1}: malformed JSON: {exc}")
            continue

        if not isinstance(record_data, dict):
            errors += 1
            if len(error_details) < _MAX_ERROR_DETAILS:
                error_details.append(f"Line {line_num + 1}: not a dict")
            continue

        try:
            record = _transform_brain_record(record_data, embed_service, classifier)
            if record is None:
                errors += 1
                if len(error_details) < _MAX_ERROR_DETAILS:
                    error_details.append(f"Line {line_num + 1}: empty summary/content")
                continue

            embedding = record.pop("_embedding")
            was_inserted = engine.insert_record(record, embedding=embedding)
            if was_inserted:
                inserted += 1
            else:
                skipped += 1

        except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            errors += 1
            if len(error_details) < _MAX_ERROR_DETAILS:
                error_details.append(
                    f"Line {line_num + 1}: {type(exc).__name__}: {exc}"
                )

        if (line_num - start_offset + 1) % _CHECKPOINT_BATCH_SIZE == 0:
            _save_checkpoint(
                checkpoint_path,
                {
                    "file": jsonl_name,
                    "line_offset": line_num + 1,
                    "records_hash": hashlib.sha256(line.encode()).hexdigest(),
                },
            )

        processed = line_num - start_offset + 1
        if processed % 100 == 0:
            logger.info("Migrating brain records: %d/%d...", line_num + 1, source_count)

    return {
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "error_details": error_details,
    }


def migrate_brain_records(
    jsonl_path: Path,
    engine: "MemoryEngine",
    embed_service: "EmbeddingService",
    classifier: "BranchClassifier",
) -> MigrationResult:
    """Migrate brain records from JSONL into SQLite MemoryEngine.

    Reads records.jsonl line by line, generates embeddings, classifies branches
    semantically, and inserts into SQLite. Supports resumable migration via
    checkpoint file.

    Args:
        jsonl_path: Path to records.jsonl file.
        engine: MemoryEngine instance.
        embed_service: EmbeddingService for generating embeddings.
        classifier: BranchClassifier for semantic branch assignment.

    Returns:
        Dict with status, source_count, inserted, skipped, errors, error_details.
    """
    if not jsonl_path.exists():
        return {
            "status": "ok",
            "source_count": 0,
            "inserted": 0,
            "skipped": 0,
            "errors": 0,
            "error_details": [],
        }

    checkpoint_path = Path(str(engine._db_path) + ".migration_checkpoint.json")
    checkpoint = _load_checkpoint(checkpoint_path)
    start_offset = _resolve_resume_offset(checkpoint, jsonl_path.name)

    lines_or_error = _read_jsonl_lines(jsonl_path)
    if isinstance(lines_or_error, dict):
        return lines_or_error  # type: ignore[return-value]
    lines = lines_or_error
    source_count = len(lines)

    counters = _process_brain_lines(
        lines,
        start_offset,
        engine,
        embed_service,
        classifier,
        checkpoint_path,
        jsonl_path.name,
        source_count,
    )

    _verify_migration_counts(
        counters["inserted"],
        counters["skipped"],
        counters["errors"],
        source_count,
        min(start_offset, source_count),
        counters["error_details"],
    )

    if counters["errors"] == 0:
        _delete_checkpoint(checkpoint_path)

    status = (
        "ok"
        if counters["errors"] == 0
        else ("partial" if counters["inserted"] > 0 else "error")
    )
    return {
        "status": status,
        "source_count": source_count,
        "inserted": counters["inserted"],
        "skipped": counters["skipped"],
        "errors": counters["errors"],
        "error_details": counters["error_details"],
    }


def migrate_facts(
    facts_path: Path,
    engine: "MemoryEngine",
) -> MigrationResult:
    """Migrate facts from facts.json into SQLite facts table.

    Args:
        facts_path: Path to facts.json file.
        engine: MemoryEngine instance.

    Returns:
        Dict with status, source_count, inserted, errors.
    """
    if not facts_path.exists():
        return {"status": "ok", "source_count": 0, "inserted": 0, "errors": 0}

    try:
        raw = json.loads(facts_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "status": "error",
            "source_count": 0,
            "inserted": 0,
            "errors": 1,
            "error_details": [str(exc)],
        }

    facts_data = raw.get("facts", {}) if isinstance(raw, dict) else {}
    if not isinstance(facts_data, dict):
        facts_data = {}

    source_count = len(facts_data)
    inserted = 0
    errors = 0

    with engine.write_lock:
        try:
            for key, value in facts_data.items():
                try:
                    if not isinstance(value, dict):
                        value = {
                            "value": str(value),
                            "confidence": 0.5,
                            "updated_utc": _now_iso(),
                        }
                    else:
                        value = dict(value)
                    value.setdefault("value", "")
                    value.setdefault("confidence", 0.0)
                    value.setdefault("locked", 0)
                    value.setdefault("updated_utc", _now_iso())
                    sources = value.get("sources", [])
                    history = value.get("history", [])
                    value["sources"] = sources if isinstance(sources, list) else []
                    value["history"] = history if isinstance(history, list) else []

                    engine.db.execute(
                        """
                        INSERT OR REPLACE INTO facts (key, value, confidence, locked, updated_utc, sources, history)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(key),
                            str(value.get("value", "")),
                            float(value.get("confidence", 0.0)),
                            int(value.get("locked", 0)),
                            str(value.get("updated_utc", _now_iso())),
                            json.dumps(value.get("sources", [])),
                            json.dumps(value.get("history", [])),
                        ),
                    )
                    inserted += 1
                except (sqlite3.Error, ValueError, TypeError) as exc:
                    errors += 1
                    logger.warning("Failed to migrate fact '%s': %s", key, exc)

            # Single commit for all facts (batch)
            engine.db.commit()
        except (sqlite3.Error, OSError) as exc:
            engine.db.rollback()
            logger.debug("migrate_facts transaction failed, rolled back: %s", exc)
            raise

    status = "ok" if errors == 0 else ("partial" if inserted > 0 else "error")
    return {
        "status": status,
        "source_count": source_count,
        "inserted": inserted,
        "errors": errors,
    }


def _parse_event_summary(event: dict) -> str | None:
    """Extract a summary string from an event dict.

    Returns ``None`` if the event has no usable content.
    """
    message = str(event.get("message", ""))
    event_type = str(event.get("event_type", ""))
    summary = f"{event_type}: {message}" if event_type else message
    if not summary.strip():
        return None
    return summary[:2000]


def _build_event_record(
    event: dict,
    summary: str,
    embed_service: "EmbeddingService",
    classifier: "BranchClassifier",
) -> tuple[dict, list[float]]:
    """Build a memory record and embedding for a single event.

    Returns ``(record_dict, embedding_vector)``.
    """
    embedding = embed_service.embed(summary, prefix="search_document")
    branch = classifier.classify(embedding)

    content_hash = sha256_hex(summary)
    ts = str(event.get("ts", _now_iso()))
    id_material = f"event_log|episodic|{ts}|{content_hash}".encode("utf-8")
    record_id = sha256_short(id_material)

    record = {
        "record_id": record_id,
        "ts": ts,
        "source": "event_log",
        "kind": "episodic",
        "task_id": str(event.get("task_id", "")),
        "branch": branch,
        "tags": "[]",
        "summary": summary,
        "content_hash": content_hash,
        "confidence": 0.6,
        "tier": "warm",
        "access_count": 0,
        "last_accessed": "",
    }
    return record, embedding


def migrate_events(
    events_path: Path,
    engine: "MemoryEngine",
    embed_service: "EmbeddingService",
    classifier: "BranchClassifier",
) -> MigrationResult:
    """Migrate events from events.jsonl into SQLite as records.

    Events are stored as records with source='event_log' and kind='episodic'.

    Args:
        events_path: Path to events.jsonl file.
        engine: MemoryEngine instance.
        embed_service: EmbeddingService for generating embeddings.
        classifier: BranchClassifier for semantic branch assignment.

    Returns:
        Dict with status, source_count, inserted, skipped, errors.
    """
    if not events_path.exists():
        return {
            "status": "ok",
            "source_count": 0,
            "inserted": 0,
            "skipped": 0,
            "errors": 0,
        }

    try:
        with events_path.open(encoding="utf-8", errors="replace") as f:
            lines = [line for line in f if line.strip()]
    except OSError as exc:
        return {
            "status": "error",
            "source_count": 0,
            "inserted": 0,
            "skipped": 0,
            "errors": 1,
            "error_details": [str(exc)],
        }

    source_count = len(lines)
    inserted = 0
    skipped = 0
    errors = 0

    for line_num, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors += 1
            continue

        if not isinstance(event, dict):
            errors += 1
            continue

        try:
            summary = _parse_event_summary(event)
            if summary is None:
                errors += 1
                continue

            record, embedding = _build_event_record(
                event, summary, embed_service, classifier
            )
            was_inserted = engine.insert_record(record, embedding=embedding)
            if was_inserted:
                inserted += 1
            else:
                skipped += 1

        except (sqlite3.Error, ValueError, TypeError) as exc:
            errors += 1
            logger.warning("Failed to migrate event at line %d: %s", line_num + 1, exc)

    status = "ok" if errors == 0 else ("partial" if inserted > 0 else "error")
    return {
        "status": status,
        "source_count": source_count,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    }


def run_full_migration(
    root: Path,
    db_path: Path,
    embed_service: "EmbeddingService",
) -> FullMigrationResult:
    """Orchestrate full migration of JSONL/JSON data into SQLite.

    Creates MemoryEngine, BranchClassifier, and runs all three migrations:
    brain records, facts, and events.

    Args:
        root: Repository root path.
        db_path: Path for the SQLite database file.
        embed_service: EmbeddingService instance.

    Returns:
        Combined migration summary dict.
    """
    from jarvis_engine.memory.classify import BranchClassifier
    from jarvis_engine.memory.engine import MemoryEngine

    engine = MemoryEngine(db_path, embed_service=embed_service)
    classifier = BranchClassifier(embed_service)

    brain_path = root / ".planning" / "brain" / "records.jsonl"
    facts_path = root / ".planning" / "brain" / "facts.json"
    events_path = root / ".planning" / "events.jsonl"

    logger.info("Migrating brain records from %s...", brain_path)
    brain_result = migrate_brain_records(brain_path, engine, embed_service, classifier)
    logger.info(
        "  Brain records: %d inserted, %d skipped, %d errors",
        brain_result["inserted"],
        brain_result["skipped"],
        brain_result["errors"],
    )

    logger.info("Migrating facts from %s...", facts_path)
    facts_result = migrate_facts(facts_path, engine)
    logger.info(
        "  Facts: %d inserted, %d errors",
        facts_result["inserted"],
        facts_result["errors"],
    )

    logger.info("Migrating events from %s...", events_path)
    events_result = migrate_events(events_path, engine, embed_service, classifier)
    logger.info(
        "  Events: %d inserted, %d skipped, %d errors",
        events_result["inserted"],
        events_result["skipped"],
        events_result["errors"],
    )

    total_inserted = (
        brain_result["inserted"] + facts_result["inserted"] + events_result["inserted"]
    )
    total_skipped = brain_result.get("skipped", 0) + events_result.get("skipped", 0)
    total_errors = (
        brain_result["errors"] + facts_result["errors"] + events_result["errors"]
    )

    engine.close()

    # Derive overall status from sub-migration results
    status = (
        "ok" if total_errors == 0 else ("partial" if total_inserted > 0 else "error")
    )

    return {
        "status": status,
        "brain": brain_result,
        "facts": facts_result,
        "events": events_result,
        "totals": {
            "inserted": total_inserted,
            "skipped": total_skipped,
            "errors": total_errors,
        },
        "db_path": str(db_path),
    }
