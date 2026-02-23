"""JSONL-to-SQLite migration: imports brain records, facts, and events into MemoryEngine.

Supports resumable migration via checkpoint files.
Includes count verification: inserted + skipped + errors == source_count.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.memory.classify import BranchClassifier
    from jarvis_engine.memory.embeddings import EmbeddingService
    from jarvis_engine.memory.engine import MemoryEngine

logger = logging.getLogger(__name__)

_CHECKPOINT_BATCH_SIZE = 50


def _load_checkpoint(checkpoint_path: Path) -> dict | None:
    """Load migration checkpoint if it exists."""
    if not checkpoint_path.exists():
        return None
    try:
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_checkpoint(checkpoint_path: Path, data: dict) -> None:
    """Save migration checkpoint."""
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")


def _delete_checkpoint(checkpoint_path: Path) -> None:
    """Delete checkpoint on completion."""
    try:
        checkpoint_path.unlink(missing_ok=True)
    except OSError:
        pass


def migrate_brain_records(
    jsonl_path: Path,
    engine: "MemoryEngine",
    embed_service: "EmbeddingService",
    classifier: "BranchClassifier",
) -> dict:
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

    # Resumable migration checkpoint
    checkpoint_path = Path(str(engine._db_path) + ".migration_checkpoint.json")
    checkpoint = _load_checkpoint(checkpoint_path)
    start_offset = 0
    if checkpoint and checkpoint.get("file") == jsonl_path.name:
        start_offset = checkpoint.get("line_offset", 0)
        logger.info("Resuming migration from line %d", start_offset)

    # Read all lines
    try:
        raw_text = jsonl_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "status": "error",
            "source_count": 0,
            "inserted": 0,
            "skipped": 0,
            "errors": 1,
            "error_details": [f"Failed to read file: {exc}"],
        }

    lines = [line for line in raw_text.splitlines() if line.strip()]
    source_count = len(lines)

    inserted = 0
    skipped = 0
    errors = 0
    error_details: list[str] = []

    # If resuming, count lines before start_offset as already processed
    already_processed = min(start_offset, source_count)

    for line_num, line in enumerate(lines):
        if line_num < start_offset:
            # Already processed in previous run
            continue

        try:
            record_data = json.loads(line)
        except json.JSONDecodeError as exc:
            errors += 1
            error_details.append(f"Line {line_num + 1}: malformed JSON: {exc}")
            continue

        if not isinstance(record_data, dict):
            errors += 1
            error_details.append(f"Line {line_num + 1}: not a dict")
            continue

        try:
            summary = str(record_data.get("summary", ""))
            if not summary.strip():
                summary = str(record_data.get("content", ""))[:280]
            if not summary.strip():
                errors += 1
                error_details.append(f"Line {line_num + 1}: empty summary/content")
                continue

            # Generate embedding
            embedding = embed_service.embed(summary, prefix="search_document")

            # Classify branch semantically
            branch = classifier.classify(embedding)

            # Build record dict for MemoryEngine
            content_hash = str(record_data.get("content_hash", ""))
            if not content_hash:
                content_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()

            # Use 32 hex chars for record_id (Codex: >16 to avoid collisions)
            original_id = str(record_data.get("record_id", ""))
            if len(original_id) < 32:
                id_material = f"{original_id}|{content_hash}".encode("utf-8")
                record_id = hashlib.sha256(id_material).hexdigest()[:32]
            else:
                record_id = original_id[:32]

            ts = str(record_data.get("ts", datetime.now(UTC).isoformat()))
            confidence = 0.72
            try:
                confidence = float(record_data.get("confidence", 0.72))
            except (TypeError, ValueError):
                pass

            tags = record_data.get("tags", [])
            if isinstance(tags, list):
                tags = str(tags)
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
            }

            was_inserted = engine.insert_record(record, embedding=embedding)
            if was_inserted:
                inserted += 1
            else:
                skipped += 1

        except Exception as exc:
            errors += 1
            error_details.append(f"Line {line_num + 1}: {type(exc).__name__}: {exc}")

        # Save checkpoint every batch
        if (line_num - start_offset + 1) % _CHECKPOINT_BATCH_SIZE == 0:
            _save_checkpoint(checkpoint_path, {
                "file": jsonl_path.name,
                "line_offset": line_num + 1,
                "records_hash": hashlib.sha256(line.encode()).hexdigest(),
            })

        # Progress logging
        processed = line_num - start_offset + 1
        if processed % 100 == 0:
            logger.info("Migrating brain records: %d/%d...", line_num + 1, source_count)

    # Count verification: already_processed records (from resume) count as previously inserted/skipped
    # But since we don't know the breakdown, we only verify the current run
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

    # Clean up checkpoint on success
    _delete_checkpoint(checkpoint_path)

    return {
        "status": "ok",
        "source_count": source_count,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "error_details": error_details,
    }


def migrate_facts(
    facts_path: Path,
    engine: "MemoryEngine",
) -> dict:
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
        return {"status": "error", "source_count": 0, "inserted": 0, "errors": 1, "error_details": [str(exc)]}

    facts_data = raw.get("facts", {}) if isinstance(raw, dict) else {}
    if not isinstance(facts_data, dict):
        facts_data = {}

    source_count = len(facts_data)
    inserted = 0
    errors = 0

    for key, value in facts_data.items():
        try:
            if not isinstance(value, dict):
                value = {"value": str(value), "confidence": 0.5, "updated_utc": datetime.now(UTC).isoformat()}

            engine._db.execute(
                """
                INSERT OR REPLACE INTO facts (key, value, confidence, locked, updated_utc, sources, history)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(key),
                    str(value.get("value", "")),
                    float(value.get("confidence", 0.0)),
                    int(value.get("locked", 0)),
                    str(value.get("updated_utc", datetime.now(UTC).isoformat())),
                    json.dumps(value.get("sources", [])),
                    json.dumps(value.get("history", [])),
                ),
            )
            engine._db.commit()
            inserted += 1
        except Exception as exc:
            errors += 1
            logger.warning("Failed to migrate fact '%s': %s", key, exc)

    return {"status": "ok", "source_count": source_count, "inserted": inserted, "errors": errors}


def migrate_events(
    events_path: Path,
    engine: "MemoryEngine",
    embed_service: "EmbeddingService",
    classifier: "BranchClassifier",
) -> dict:
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
        return {"status": "ok", "source_count": 0, "inserted": 0, "skipped": 0, "errors": 0}

    try:
        raw_text = events_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"status": "error", "source_count": 0, "inserted": 0, "skipped": 0, "errors": 1}

    lines = [line for line in raw_text.splitlines() if line.strip()]
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
            message = str(event.get("message", ""))
            event_type = str(event.get("event_type", ""))
            summary = f"{event_type}: {message}" if event_type else message
            if not summary.strip():
                errors += 1
                continue

            summary = summary[:2000]

            # Generate embedding and classify
            embedding = embed_service.embed(summary, prefix="search_document")
            branch = classifier.classify(embedding)

            content_hash = hashlib.sha256(summary.encode("utf-8")).hexdigest()
            ts = str(event.get("ts", datetime.now(UTC).isoformat()))
            id_material = f"event_log|episodic|{ts}|{content_hash}".encode("utf-8")
            record_id = hashlib.sha256(id_material).hexdigest()[:32]

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

            was_inserted = engine.insert_record(record, embedding=embedding)
            if was_inserted:
                inserted += 1
            else:
                skipped += 1

        except Exception as exc:
            errors += 1
            logger.warning("Failed to migrate event at line %d: %s", line_num + 1, exc)

    return {
        "status": "ok",
        "source_count": source_count,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    }


def run_full_migration(
    root: Path,
    db_path: Path,
    embed_service: "EmbeddingService",
) -> dict:
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

    print(f"Migrating brain records from {brain_path}...")
    brain_result = migrate_brain_records(brain_path, engine, embed_service, classifier)
    print(f"  Brain records: {brain_result['inserted']} inserted, {brain_result['skipped']} skipped, {brain_result['errors']} errors")

    print(f"Migrating facts from {facts_path}...")
    facts_result = migrate_facts(facts_path, engine)
    print(f"  Facts: {facts_result['inserted']} inserted, {facts_result['errors']} errors")

    print(f"Migrating events from {events_path}...")
    events_result = migrate_events(events_path, engine, embed_service, classifier)
    print(f"  Events: {events_result['inserted']} inserted, {events_result['skipped']} skipped, {events_result['errors']} errors")

    total_inserted = brain_result["inserted"] + facts_result["inserted"] + events_result["inserted"]
    total_skipped = brain_result.get("skipped", 0) + events_result.get("skipped", 0)
    total_errors = brain_result["errors"] + facts_result["errors"] + events_result["errors"]

    engine.close()

    return {
        "status": "ok",
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
