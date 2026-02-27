"""Tests for security.memory_provenance -- Wave 13 memory integrity."""

from __future__ import annotations

import pytest

from jarvis_engine.security.memory_provenance import (
    OWNER_INPUT,
    QUARANTINED,
    UNVERIFIED_EXTERNAL,
    VERIFIED_EXTERNAL,
    MemoryProvenance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def prov() -> MemoryProvenance:
    """Fresh MemoryProvenance instance."""
    return MemoryProvenance()


# ---------------------------------------------------------------------------
# Trust level constants
# ---------------------------------------------------------------------------


class TestTrustLevelConstants:
    def test_owner_input(self) -> None:
        assert OWNER_INPUT == "OWNER_INPUT"

    def test_verified_external(self) -> None:
        assert VERIFIED_EXTERNAL == "VERIFIED_EXTERNAL"

    def test_unverified_external(self) -> None:
        assert UNVERIFIED_EXTERNAL == "UNVERIFIED_EXTERNAL"

    def test_quarantined(self) -> None:
        assert QUARANTINED == "QUARANTINED"


# ---------------------------------------------------------------------------
# tag_record
# ---------------------------------------------------------------------------


class TestTagRecord:
    def test_creates_provenance_entry(self, prov: MemoryProvenance) -> None:
        result = prov.tag_record("abc123", "llm_interaction")
        assert result["record_hash"] == "abc123"
        assert result["source"] == "llm_interaction"
        assert result["trust_level"] == UNVERIFIED_EXTERNAL
        assert result["verification_status"] == "pending"
        assert result["ingestion_timestamp"]  # non-empty

    def test_with_owner_input_trust(self, prov: MemoryProvenance) -> None:
        result = prov.tag_record("abc123", "user_input", trust_level=OWNER_INPUT)
        assert result["trust_level"] == OWNER_INPUT
        assert result["verification_status"] == "verified"

    def test_with_verified_external_trust(self, prov: MemoryProvenance) -> None:
        result = prov.tag_record("abc123", "api", trust_level=VERIFIED_EXTERNAL)
        assert result["trust_level"] == VERIFIED_EXTERNAL
        assert result["verification_status"] == "verified"

    def test_invalid_trust_level_raises(self, prov: MemoryProvenance) -> None:
        with pytest.raises(ValueError, match="Invalid trust_level"):
            prov.tag_record("abc123", "source", trust_level="INVALID")

    def test_duplicate_tag_updates_existing(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "source_a")
        prov.tag_record("abc123", "source_b", trust_level=OWNER_INPUT)
        result = prov.get_provenance("abc123")
        assert result is not None
        assert result["source"] == "source_b"
        assert result["trust_level"] == OWNER_INPUT


# ---------------------------------------------------------------------------
# get_provenance
# ---------------------------------------------------------------------------


class TestGetProvenance:
    def test_returns_none_for_unknown(self, prov: MemoryProvenance) -> None:
        assert prov.get_provenance("nonexistent") is None

    def test_returns_tagged_record(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "source")
        result = prov.get_provenance("abc123")
        assert result is not None
        assert result["record_hash"] == "abc123"


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


class TestPromote:
    def test_promotes_unverified_to_verified(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "llm")
        assert prov.promote("abc123") is True
        p = prov.get_provenance("abc123")
        assert p is not None
        assert p["trust_level"] == VERIFIED_EXTERNAL
        assert p["verification_status"] == "verified"

    def test_returns_false_for_unknown(self, prov: MemoryProvenance) -> None:
        assert prov.promote("nonexistent") is False

    def test_returns_false_for_already_verified(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "user", trust_level=VERIFIED_EXTERNAL)
        assert prov.promote("abc123") is False

    def test_returns_false_for_owner_input(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "user", trust_level=OWNER_INPUT)
        assert prov.promote("abc123") is False

    def test_returns_false_for_quarantined(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "llm")
        prov.quarantine("abc123", "suspicious")
        assert prov.promote("abc123") is False


# ---------------------------------------------------------------------------
# quarantine
# ---------------------------------------------------------------------------


class TestQuarantine:
    def test_quarantines_record(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "llm")
        assert prov.quarantine("abc123", "contradiction detected") is True
        p = prov.get_provenance("abc123")
        assert p is not None
        assert p["trust_level"] == QUARANTINED
        assert p["quarantine_reason"] == "contradiction detected"
        assert p["verification_status"] == "pending"

    def test_returns_false_for_unknown(self, prov: MemoryProvenance) -> None:
        assert prov.quarantine("nonexistent", "reason") is False

    def test_can_quarantine_verified(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "user", trust_level=VERIFIED_EXTERNAL)
        assert prov.quarantine("abc123", "injection payload") is True
        p = prov.get_provenance("abc123")
        assert p is not None
        assert p["trust_level"] == QUARANTINED


# ---------------------------------------------------------------------------
# get_quarantined
# ---------------------------------------------------------------------------


class TestGetQuarantined:
    def test_returns_only_quarantined(self, prov: MemoryProvenance) -> None:
        prov.tag_record("h1", "s1")
        prov.tag_record("h2", "s2")
        prov.tag_record("h3", "s3")
        prov.quarantine("h2", "bad")
        result = prov.get_quarantined()
        assert len(result) == 1
        assert result[0]["record_hash"] == "h2"

    def test_empty_when_none_quarantined(self, prov: MemoryProvenance) -> None:
        prov.tag_record("h1", "s1")
        assert prov.get_quarantined() == []

    def test_limit_parameter(self, prov: MemoryProvenance) -> None:
        for i in range(10):
            prov.tag_record(f"h{i}", f"s{i}")
            prov.quarantine(f"h{i}", "bad")
        result = prov.get_quarantined(limit=3)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# purge_quarantined
# ---------------------------------------------------------------------------


class TestPurgeQuarantined:
    def test_removes_quarantined_record(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "llm")
        prov.quarantine("abc123", "bad")
        assert prov.purge_quarantined("abc123") is True
        assert prov.get_provenance("abc123") is None

    def test_returns_false_for_unknown(self, prov: MemoryProvenance) -> None:
        assert prov.purge_quarantined("nonexistent") is False

    def test_returns_false_for_non_quarantined(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "llm")
        assert prov.purge_quarantined("abc123") is False


# ---------------------------------------------------------------------------
# approve_quarantined
# ---------------------------------------------------------------------------


class TestApproveQuarantined:
    def test_moves_to_verified(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "llm")
        prov.quarantine("abc123", "suspicious")
        assert prov.approve_quarantined("abc123") is True
        p = prov.get_provenance("abc123")
        assert p is not None
        assert p["trust_level"] == VERIFIED_EXTERNAL
        assert p["verification_status"] == "verified"
        assert p["quarantine_reason"] == ""

    def test_returns_false_for_unknown(self, prov: MemoryProvenance) -> None:
        assert prov.approve_quarantined("nonexistent") is False

    def test_returns_false_for_non_quarantined(self, prov: MemoryProvenance) -> None:
        prov.tag_record("abc123", "llm")
        assert prov.approve_quarantined("abc123") is False
