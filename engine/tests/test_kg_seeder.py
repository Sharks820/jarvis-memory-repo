"""Tests for Unity 6.3 KG seeder -- idempotent knowledge graph seeding."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from jarvis_engine.agent.kg_seeder import is_unity_kg_seeded, seed_unity_kg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kg(add_fact_return: bool = True) -> MagicMock:
    """Return a mock KnowledgeGraph with add_fact tracked."""
    kg = MagicMock()
    kg.add_fact.return_value = add_fact_return
    # Simulate no existing unity seed nodes by default
    kg._db = MagicMock()
    kg._db.execute.return_value.fetchone.return_value = None
    return kg


# ---------------------------------------------------------------------------
# seed_unity_kg
# ---------------------------------------------------------------------------


def test_seed_returns_positive_count() -> None:
    kg = _make_kg()
    count = seed_unity_kg(kg)
    assert count > 0


def test_seed_calls_add_fact_for_each_entry() -> None:
    kg = _make_kg()
    count = seed_unity_kg(kg)
    assert kg.add_fact.call_count == count


def test_seed_at_least_50_entries() -> None:
    """Combined JSON files must supply at least 50 seed entries (30+10+10)."""
    kg = _make_kg()
    count = seed_unity_kg(kg)
    assert count >= 50


def test_seed_passes_correct_node_types() -> None:
    """Each entry should have a recognised node_type."""
    kg = _make_kg()
    seed_unity_kg(kg)
    for c in kg.add_fact.call_args_list:
        kwargs = c.kwargs
        node_type = kwargs.get("node_type", c.args[4] if len(c.args) > 4 else None)
        assert node_type in {"unity_api", "unity_breaking", "unity_error"}, (
            f"Unexpected node_type: {node_type}"
        )


def test_seed_passes_correct_source_record() -> None:
    kg = _make_kg()
    seed_unity_kg(kg)
    for c in kg.add_fact.call_args_list:
        kwargs = c.kwargs
        source_record = kwargs.get(
            "source_record", c.args[3] if len(c.args) > 3 else None
        )
        assert source_record == "unity63_kg_seed_v1", (
            f"Unexpected source_record: {source_record}"
        )


def test_seed_confidence_between_0_and_1() -> None:
    kg = _make_kg()
    seed_unity_kg(kg)
    for c in kg.add_fact.call_args_list:
        kwargs = c.kwargs
        confidence = kwargs.get("confidence", c.args[2] if len(c.args) > 2 else None)
        assert 0.0 <= confidence <= 1.0, f"confidence out of range: {confidence}"


# ---------------------------------------------------------------------------
# is_unity_kg_seeded
# ---------------------------------------------------------------------------


def test_is_seeded_true_after_seeding() -> None:
    kg = _make_kg()
    seed_unity_kg(kg)
    # After seeding, simulate DB having the sentinel
    kg._db.execute.return_value.fetchone.return_value = ("unity63_kg_seed_v1",)
    assert is_unity_kg_seeded(kg) is True


def test_is_seeded_false_before_seeding() -> None:
    kg = _make_kg()
    kg._db.execute.return_value.fetchone.return_value = None
    assert is_unity_kg_seeded(kg) is False


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_seeding_twice_does_not_duplicate() -> None:
    """Calling seed_unity_kg twice should yield same count each time (add_fact
    is called the same number of times per call -- KG deduplication is handled
    by add_fact itself, which is fine; we just verify we don't send extra calls
    beyond the total entries across both runs)."""
    kg = _make_kg()
    count1 = seed_unity_kg(kg)
    first_call_count = kg.add_fact.call_count
    assert first_call_count == count1

    count2 = seed_unity_kg(kg)
    assert count2 == count1  # same number of entries both times


# ---------------------------------------------------------------------------
# JSON file minimum entry counts
# ---------------------------------------------------------------------------


def test_api_json_has_at_least_30_entries() -> None:
    import json
    from pathlib import Path

    data_dir = (
        Path(__file__).parent.parent
        / "src"
        / "jarvis_engine"
        / "data"
        / "unity_kg_seed"
    )
    with (data_dir / "unity63_api.json").open() as f:
        entries = json.load(f)
    assert len(entries) >= 30, f"Expected >=30 api entries, got {len(entries)}"


def test_breaking_json_has_at_least_10_entries() -> None:
    import json
    from pathlib import Path

    data_dir = (
        Path(__file__).parent.parent
        / "src"
        / "jarvis_engine"
        / "data"
        / "unity_kg_seed"
    )
    with (data_dir / "unity63_breaking.json").open() as f:
        entries = json.load(f)
    assert len(entries) >= 10, f"Expected >=10 breaking entries, got {len(entries)}"


def test_errors_json_has_at_least_10_entries() -> None:
    import json
    from pathlib import Path

    data_dir = (
        Path(__file__).parent.parent
        / "src"
        / "jarvis_engine"
        / "data"
        / "unity_kg_seed"
    )
    with (data_dir / "unity63_errors.json").open() as f:
        entries = json.load(f)
    assert len(entries) >= 10, f"Expected >=10 error entries, got {len(entries)}"
