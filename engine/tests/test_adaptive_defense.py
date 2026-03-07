"""Tests for security.adaptive_defense -- Wave 13 adaptive AI defense."""

from __future__ import annotations

import pytest

from jarvis_engine.security.adaptive_defense import AdaptiveDefenseEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> AdaptiveDefenseEngine:
    """Fresh AdaptiveDefenseEngine with no backing stores."""
    return AdaptiveDefenseEngine()


# ---------------------------------------------------------------------------
# record_detection
# ---------------------------------------------------------------------------


class TestRecordDetection:
    def test_stores_event(self, engine: AdaptiveDefenseEngine) -> None:
        engine.record_detection("injection", "hash1", "10.0.0.1")
        assert len(engine._events) == 1
        assert engine._events[0]["category"] == "injection"

    def test_increments_total_attacks(self, engine: AdaptiveDefenseEngine) -> None:
        engine.record_detection("injection", "h1", "10.0.0.1")
        engine.record_detection("traversal", "h2", "10.0.0.2")
        assert engine._total_attacks == 2

    def test_blocked_increments_total_blocked(
        self, engine: AdaptiveDefenseEngine
    ) -> None:
        engine.record_detection("injection", "h1", "10.0.0.1", blocked=True)
        engine.record_detection("traversal", "h2", "10.0.0.2", blocked=False)
        assert engine._total_blocked == 1

    def test_tracks_unique_ips(self, engine: AdaptiveDefenseEngine) -> None:
        engine.record_detection("a", "h1", "10.0.0.1")
        engine.record_detection("b", "h2", "10.0.0.1")
        engine.record_detection("c", "h3", "10.0.0.2")
        assert len(engine._unique_ips) == 2

    def test_empty_ip_not_tracked(self, engine: AdaptiveDefenseEngine) -> None:
        engine.record_detection("a", "h1", "")
        assert len(engine._unique_ips) == 0

    def test_category_counts_incremented(self, engine: AdaptiveDefenseEngine) -> None:
        engine.record_detection("injection", "h1", "1.1.1.1")
        engine.record_detection("injection", "h2", "1.1.1.2")
        assert engine._category_counts["injection"] == 2


# ---------------------------------------------------------------------------
# check_auto_rule
# ---------------------------------------------------------------------------


class TestCheckAutoRule:
    def test_generates_rule_at_threshold(self, engine: AdaptiveDefenseEngine) -> None:
        for i in range(3):
            engine.record_detection("injection", f"hash_{i}", f"10.0.0.{i}")
        rule = engine.check_auto_rule("injection")
        assert rule is not None
        assert rule["category"] == "injection"
        assert rule["detection_count"] == 3
        assert len(rule["triggered_by"]) == 3

    def test_no_rule_below_threshold(self, engine: AdaptiveDefenseEngine) -> None:
        engine.record_detection("injection", "h1", "10.0.0.1")
        engine.record_detection("injection", "h2", "10.0.0.2")
        rule = engine.check_auto_rule("injection")
        assert rule is None

    def test_no_duplicate_rule(self, engine: AdaptiveDefenseEngine) -> None:
        for i in range(5):
            engine.record_detection("injection", f"h{i}", f"10.0.0.{i}")
        rule1 = engine.check_auto_rule("injection")
        rule2 = engine.check_auto_rule("injection")
        assert rule1 is not None
        assert rule2 is None

    def test_unknown_category_returns_none(self, engine: AdaptiveDefenseEngine) -> None:
        assert engine.check_auto_rule("nonexistent") is None

    def test_multiple_categories_independent(
        self, engine: AdaptiveDefenseEngine
    ) -> None:
        for i in range(3):
            engine.record_detection("injection", f"inj{i}", f"10.0.0.{i}")
        for i in range(3):
            engine.record_detection("traversal", f"trav{i}", f"10.0.1.{i}")

        r1 = engine.check_auto_rule("injection")
        r2 = engine.check_auto_rule("traversal")
        assert r1 is not None
        assert r2 is not None
        assert r1["category"] == "injection"
        assert r2["category"] == "traversal"


# ---------------------------------------------------------------------------
# get_defense_dashboard
# ---------------------------------------------------------------------------


class TestGetDefenseDashboard:
    def test_empty_state(self, engine: AdaptiveDefenseEngine) -> None:
        d = engine.get_defense_dashboard()
        assert d["total_attacks"] == 0
        assert d["total_blocked"] == 0
        assert d["rules_generated"] == 0
        assert d["unique_ips"] == 0
        assert d["effectiveness_pct"] == 100.0
        assert d["top_categories"] == []

    def test_correct_metrics_after_detections(
        self, engine: AdaptiveDefenseEngine
    ) -> None:
        engine.record_detection("injection", "h1", "10.0.0.1", blocked=True)
        engine.record_detection("injection", "h2", "10.0.0.2", blocked=True)
        engine.record_detection("traversal", "h3", "10.0.0.3", blocked=False)

        d = engine.get_defense_dashboard()
        assert d["total_attacks"] == 3
        assert d["total_blocked"] == 2
        assert d["unique_ips"] == 3
        assert d["effectiveness_pct"] == pytest.approx(66.67, abs=0.01)

    def test_effectiveness_100_when_all_blocked(
        self, engine: AdaptiveDefenseEngine
    ) -> None:
        engine.record_detection("a", "h1", "1.1.1.1", blocked=True)
        engine.record_detection("b", "h2", "2.2.2.2", blocked=True)
        d = engine.get_defense_dashboard()
        assert d["effectiveness_pct"] == 100.0

    def test_effectiveness_0_when_none_blocked(
        self, engine: AdaptiveDefenseEngine
    ) -> None:
        engine.record_detection("a", "h1", "1.1.1.1", blocked=False)
        engine.record_detection("b", "h2", "2.2.2.2", blocked=False)
        d = engine.get_defense_dashboard()
        assert d["effectiveness_pct"] == 0.0

    def test_rules_generated_counted(self, engine: AdaptiveDefenseEngine) -> None:
        for i in range(3):
            engine.record_detection("injection", f"h{i}", f"10.0.0.{i}")
        engine.check_auto_rule("injection")
        d = engine.get_defense_dashboard()
        assert d["rules_generated"] == 1

    def test_top_categories_sorted_descending(
        self, engine: AdaptiveDefenseEngine
    ) -> None:
        for i in range(5):
            engine.record_detection("injection", f"inj{i}", f"10.0.0.{i}")
        for i in range(2):
            engine.record_detection("traversal", f"trav{i}", f"10.0.1.{i}")

        d = engine.get_defense_dashboard()
        cats = d["top_categories"]
        assert len(cats) == 2
        assert cats[0]["category"] == "injection"
        assert cats[0]["count"] == 5
        assert cats[1]["category"] == "traversal"
        assert cats[1]["count"] == 2


# ---------------------------------------------------------------------------
# generate_briefing
# ---------------------------------------------------------------------------


class TestGenerateBriefing:
    def test_returns_nonempty_text(self, engine: AdaptiveDefenseEngine) -> None:
        text = engine.generate_briefing()
        assert isinstance(text, str)
        assert len(text) > 0

    def test_contains_nominal_when_no_attacks(
        self, engine: AdaptiveDefenseEngine
    ) -> None:
        text = engine.generate_briefing()
        assert "nominal" in text.lower()

    def test_contains_attack_count(self, engine: AdaptiveDefenseEngine) -> None:
        engine.record_detection("injection", "h1", "10.0.0.1")
        text = engine.generate_briefing()
        assert "1" in text

    def test_contains_category_info(self, engine: AdaptiveDefenseEngine) -> None:
        for i in range(3):
            engine.record_detection("injection", f"h{i}", f"10.0.0.{i}")
        text = engine.generate_briefing()
        assert "injection" in text


# ---------------------------------------------------------------------------
# get_rules
# ---------------------------------------------------------------------------


class TestGetRules:
    def test_empty_initially(self, engine: AdaptiveDefenseEngine) -> None:
        assert engine.get_rules() == []

    def test_returns_generated_rules(self, engine: AdaptiveDefenseEngine) -> None:
        for i in range(3):
            engine.record_detection("injection", f"h{i}", f"10.0.0.{i}")
        engine.check_auto_rule("injection")
        rules = engine.get_rules()
        assert len(rules) == 1
        assert rules[0]["category"] == "injection"

    def test_returns_copy(self, engine: AdaptiveDefenseEngine) -> None:
        """get_rules returns a copy, not the internal list."""
        for i in range(3):
            engine.record_detection("injection", f"h{i}", f"10.0.0.{i}")
        engine.check_auto_rule("injection")
        rules = engine.get_rules()
        rules.clear()
        assert len(engine.get_rules()) == 1


# ---------------------------------------------------------------------------
# Constructor with optional dependencies
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_default_no_deps(self) -> None:
        e = AdaptiveDefenseEngine()
        assert e._attack_memory is None
        assert e._ip_tracker is None

    def test_with_deps(self) -> None:
        sentinel_mem = object()
        sentinel_ip = object()
        e = AdaptiveDefenseEngine(attack_memory=sentinel_mem, ip_tracker=sentinel_ip)
        assert e._attack_memory is sentinel_mem
        assert e._ip_tracker is sentinel_ip
