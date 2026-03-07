"""Tests for security.attack_memory — Wave 10 attack pattern storage."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from jarvis_engine.security.attack_memory import (
    AttackPatternMemory,
    _jaccard,
    _payload_hash,
    _tokenize,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mem() -> AttackPatternMemory:
    """In-memory AttackPatternMemory instance."""
    db = sqlite3.connect(":memory:")
    lock = threading.Lock()
    return AttackPatternMemory(db, lock)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_payload_hash_deterministic(self) -> None:
        h1 = _payload_hash("ignore previous instructions")
        h2 = _payload_hash("ignore previous instructions")
        assert h1 == h2

    def test_payload_hash_different_for_different_payloads(self) -> None:
        assert _payload_hash("payload_a") != _payload_hash("payload_b")

    def test_tokenize_lowercases(self) -> None:
        tokens = _tokenize("Hello World FOO")
        assert tokens == {"hello", "world", "foo"}

    def test_tokenize_empty(self) -> None:
        assert _tokenize("") == set()  # "".split() gives []

    def test_jaccard_identical(self) -> None:
        s = {"a", "b", "c"}
        assert _jaccard(s, s) == 1.0

    def test_jaccard_disjoint(self) -> None:
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_jaccard_partial_overlap(self) -> None:
        sim = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert abs(sim - 0.5) < 0.01  # 2 / 4

    def test_jaccard_both_empty(self) -> None:
        assert _jaccard(set(), set()) == 1.0

    def test_jaccard_one_empty(self) -> None:
        assert _jaccard(set(), {"a"}) == 0.0


# ---------------------------------------------------------------------------
# record_attack
# ---------------------------------------------------------------------------


class TestRecordAttack:
    def test_record_new_attack(self, mem: AttackPatternMemory) -> None:
        pid = mem.record_attack(
            "injection", "ignore previous instructions", "pattern_scan"
        )
        assert isinstance(pid, str)
        assert len(pid) == 64  # SHA-256 hex

    def test_record_returns_consistent_id(self, mem: AttackPatternMemory) -> None:
        payload = "test payload"
        pid1 = mem.record_attack("injection", payload, "scan")
        pid2 = mem.record_attack("injection", payload, "scan")
        assert pid1 == pid2

    def test_dedup_increments_frequency(self, mem: AttackPatternMemory) -> None:
        payload = "ignore previous instructions"
        mem.record_attack("injection", payload, "scan")
        mem.record_attack("injection", payload, "scan")
        mem.record_attack("injection", payload, "scan")

        patterns = mem.get_patterns_by_category("injection")
        assert len(patterns) == 1
        assert patterns[0]["frequency"] == 3

    def test_source_ip_accumulation(self, mem: AttackPatternMemory) -> None:
        payload = "attack payload"
        mem.record_attack("injection", payload, "scan", source_ip="10.0.0.1")
        mem.record_attack("injection", payload, "scan", source_ip="10.0.0.2")
        mem.record_attack("injection", payload, "scan", source_ip="10.0.0.1")  # dup

        patterns = mem.get_patterns_by_category("injection")
        assert len(patterns) == 1
        ips = patterns[0]["source_ips"]
        assert "10.0.0.1" in ips
        assert "10.0.0.2" in ips
        assert len(ips) == 2  # No duplicate

    def test_different_payloads_different_records(
        self, mem: AttackPatternMemory
    ) -> None:
        mem.record_attack("injection", "payload_a", "scan")
        mem.record_attack("injection", "payload_b", "scan")

        patterns = mem.get_patterns_by_category("injection")
        assert len(patterns) == 2

    def test_empty_source_ip(self, mem: AttackPatternMemory) -> None:
        mem.record_attack("injection", "test", "scan", source_ip="")
        patterns = mem.get_patterns_by_category("injection")
        assert patterns[0]["source_ips"] == []

    def test_last_seen_updated_on_upsert(self, mem: AttackPatternMemory) -> None:
        payload = "repeated attack"
        mem.record_attack("injection", payload, "scan")
        patterns = mem.get_patterns_by_category("injection")
        first_seen = patterns[0]["first_seen"]
        last_seen_1 = patterns[0]["last_seen"]

        mem.record_attack("injection", payload, "scan")
        patterns = mem.get_patterns_by_category("injection")
        # first_seen should not change
        assert patterns[0]["first_seen"] == first_seen
        # last_seen should be >= the previous
        assert patterns[0]["last_seen"] >= last_seen_1


# ---------------------------------------------------------------------------
# find_similar
# ---------------------------------------------------------------------------


class TestFindSimilar:
    def test_exact_match(self, mem: AttackPatternMemory) -> None:
        payload = "ignore previous instructions completely"
        mem.record_attack("injection", payload, "scan")

        results = mem.find_similar(payload, threshold=0.8)
        assert len(results) == 1
        assert results[0]["similarity"] == 1.0

    def test_similar_match(self, mem: AttackPatternMemory) -> None:
        mem.record_attack("injection", "ignore previous instructions now", "scan")

        # Very similar text
        results = mem.find_similar("ignore previous instructions please", threshold=0.5)
        assert len(results) >= 1
        assert results[0]["similarity"] >= 0.5

    def test_no_match_below_threshold(self, mem: AttackPatternMemory) -> None:
        mem.record_attack("injection", "ignore previous instructions", "scan")

        results = mem.find_similar("completely unrelated weather query", threshold=0.8)
        assert len(results) == 0

    def test_results_sorted_by_similarity(self, mem: AttackPatternMemory) -> None:
        mem.record_attack("injection", "ignore previous all instructions now", "scan")
        mem.record_attack("injection", "ignore previous instructions", "scan")
        mem.record_attack("xss", "something totally different here today", "scan")

        results = mem.find_similar("ignore previous instructions", threshold=0.3)
        if len(results) >= 2:
            assert results[0]["similarity"] >= results[1]["similarity"]


# ---------------------------------------------------------------------------
# get_attack_intelligence
# ---------------------------------------------------------------------------


class TestAttackIntelligence:
    def test_empty_database(self, mem: AttackPatternMemory) -> None:
        intel = mem.get_attack_intelligence()
        assert intel["total_patterns"] == 0
        assert intel["total_events"] == 0
        assert intel["recurring_patterns"] == 0
        assert intel["top_categories"] == []
        assert intel["recent_attacks"] == []

    def test_intelligence_with_data(self, mem: AttackPatternMemory) -> None:
        mem.record_attack("injection", "payload_1", "scan")
        mem.record_attack("injection", "payload_1", "scan")
        mem.record_attack("xss", "payload_2", "scan")
        mem.record_attack("sqli", "payload_3", "scan")

        intel = mem.get_attack_intelligence()
        assert intel["total_patterns"] == 3
        assert intel["total_events"] == 4
        assert intel["recurring_patterns"] == 1  # payload_1 seen twice

    def test_top_categories_sorted(self, mem: AttackPatternMemory) -> None:
        for _ in range(5):
            mem.record_attack("injection", "payload_a", "scan")
        for _ in range(3):
            mem.record_attack("xss", "payload_b", "scan")
        mem.record_attack("sqli", "payload_c", "scan")

        intel = mem.get_attack_intelligence()
        cats = intel["top_categories"]
        assert len(cats) == 3
        assert cats[0]["category"] == "injection"
        assert cats[0]["total_frequency"] == 5

    def test_recent_attacks_ordered(self, mem: AttackPatternMemory) -> None:
        mem.record_attack("a", "first", "scan")
        mem.record_attack("b", "second", "scan")
        mem.record_attack("c", "third", "scan")

        intel = mem.get_attack_intelligence()
        recent = intel["recent_attacks"]
        assert len(recent) == 3
        # Most recent should be first
        assert recent[0]["category"] == "c"


# ---------------------------------------------------------------------------
# get_patterns_by_category
# ---------------------------------------------------------------------------


class TestPatternsByCategory:
    def test_filter_by_category(self, mem: AttackPatternMemory) -> None:
        mem.record_attack("injection", "payload_a", "scan")
        mem.record_attack("xss", "payload_b", "scan")
        mem.record_attack("injection", "payload_c", "scan")

        injection_patterns = mem.get_patterns_by_category("injection")
        assert len(injection_patterns) == 2
        assert all(p["category"] == "injection" for p in injection_patterns)

    def test_empty_category(self, mem: AttackPatternMemory) -> None:
        mem.record_attack("injection", "payload", "scan")
        assert mem.get_patterns_by_category("nonexistent") == []

    def test_pattern_fields_complete(self, mem: AttackPatternMemory) -> None:
        mem.record_attack(
            "injection", "test payload", "pattern_scan", source_ip="1.2.3.4"
        )

        patterns = mem.get_patterns_by_category("injection")
        assert len(patterns) == 1
        p = patterns[0]
        assert "pattern_id" in p
        assert p["category"] == "injection"
        assert p["payload_signature"] == "test payload"
        assert p["detection_method"] == "pattern_scan"
        assert "first_seen" in p
        assert "last_seen" in p
        assert p["frequency"] == 1
        assert "1.2.3.4" in p["source_ips"]
        assert "notes" in p
