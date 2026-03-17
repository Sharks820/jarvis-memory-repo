"""Unity 6.3 knowledge graph seeder.

Loads three JSON seed files (api, breaking changes, error patterns) and
inserts each entry into the KnowledgeGraph via kg.add_fact().  The seeder
is idempotent: re-running it on an already-seeded graph is safe because
add_fact() uses INSERT OR REPLACE logic internally.

Usage:
    from jarvis_engine.agent.kg_seeder import seed_unity_kg, is_unity_kg_seeded

    if not is_unity_kg_seeded(kg):
        count = seed_unity_kg(kg)
        logger.info("Seeded %d Unity 6.3 facts", count)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data" / "unity_kg_seed"
_SEED_FILES = [
    ("unity63_api.json", "unity_api"),
    ("unity63_breaking.json", "unity_breaking"),
    ("unity63_errors.json", "unity_error"),
]
_SOURCE_RECORD = "unity63_kg_seed_v1"

# Sentinel query: at least one node with our source_record exists
_SEEDED_QUERY = (
    "SELECT 1 FROM kg_nodes WHERE sources LIKE ? LIMIT 1"
)


def seed_unity_kg(kg: "KnowledgeGraph") -> int:
    """Load all Unity 6.3 seed files and add facts to *kg*.

    Returns the total number of add_fact calls made.
    """
    total = 0
    for filename, expected_node_type in _SEED_FILES:
        path = _DATA_DIR / filename
        entries: list[dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
        for entry in entries:
            kg.add_fact(
                node_id=str(entry["node_id"]),
                label=str(entry["label"]),
                confidence=float(entry["confidence"]),  # type: ignore[arg-type]
                source_record=str(entry.get("source_record", _SOURCE_RECORD)),
                node_type=str(entry.get("node_type", expected_node_type)),
            )
            total += 1

    logger.info("Unity 6.3 KG seeder: seeded %d facts", total)
    return total


def is_unity_kg_seeded(kg: "KnowledgeGraph") -> bool:
    """Return True if the KG already contains Unity 6.3 seed data."""
    try:
        row = kg._db.execute(
            _SEEDED_QUERY, (f"%{_SOURCE_RECORD}%",)
        ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False
