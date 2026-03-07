"""Tests for jarvis_engine.knowledge.facts -- FactExtractor and helpers.

Covers:
- _normalize: lowercasing, whitespace replacement, special char stripping
- FactTriple: named tuple fields
- FactExtractor.extract: health, schedule, preference, family, location, finance patterns
- Edge cases: short/long matches, cap at 10, empty text, no matches
"""

from __future__ import annotations


from jarvis_engine.knowledge.facts import FactExtractor, FactTriple, _normalize


# ---------------------------------------------------------------------------
# _normalize tests
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_lowercase(self):
        assert _normalize("HelloWorld") == "helloworld"

    def test_whitespace_to_underscore(self):
        assert _normalize("hello world") == "hello_world"

    def test_multiple_spaces(self):
        assert _normalize("hello   world") == "hello_world"

    def test_strips_special_chars(self):
        assert _normalize("hello-world!") == "helloworld"

    def test_keeps_digits_and_underscores(self):
        assert _normalize("test_123") == "test_123"

    def test_leading_trailing_whitespace(self):
        assert _normalize("  hello  ") == "hello"

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_all_special_chars(self):
        assert _normalize("!@#$%") == ""


# ---------------------------------------------------------------------------
# FactTriple
# ---------------------------------------------------------------------------


class TestFactTriple:
    def test_named_tuple_fields(self):
        ft = FactTriple(
            subject="health.aspirin",
            predicate="takes",
            object_val="aspirin",
            confidence=0.75,
        )
        assert ft.subject == "health.aspirin"
        assert ft.predicate == "takes"
        assert ft.object_val == "aspirin"
        assert ft.confidence == 0.75

    def test_tuple_unpacking(self):
        ft = FactTriple("a", "b", "c", 0.5)
        subj, pred, obj, conf = ft
        assert subj == "a"
        assert pred == "b"
        assert obj == "c"
        assert conf == 0.5


# ---------------------------------------------------------------------------
# FactExtractor - Health patterns
# ---------------------------------------------------------------------------


class TestFactExtractorHealth:
    def setup_method(self):
        self.extractor = FactExtractor()

    def test_takes_medication_daily(self):
        facts = self.extractor.extract("He takes aspirin for headaches daily")
        assert any(
            f.predicate == "takes" and "aspirin" in f.object_val.lower() for f in facts
        )

    def test_prescribed_medication(self):
        facts = self.extractor.extract(
            "She was prescribed lisinopril for blood pressure daily"
        )
        assert any(f.predicate == "takes" for f in facts)

    def test_on_medication_morning(self):
        facts = self.extractor.extract("He is on metformin morning and evening")
        assert any(f.predicate == "takes" for f in facts)

    def test_health_subject_prefix(self):
        facts = self.extractor.extract("She takes aspirin for pain daily")
        health_facts = [f for f in facts if f.subject.startswith("health.medication")]
        assert len(health_facts) > 0


# ---------------------------------------------------------------------------
# FactExtractor - Schedule patterns
# ---------------------------------------------------------------------------


class TestFactExtractorSchedule:
    def setup_method(self):
        self.extractor = FactExtractor()

    def test_meeting_at(self):
        facts = self.extractor.extract("I have a meeting at 3pm with the team.")
        schedule = [f for f in facts if f.predicate == "has_event"]
        assert len(schedule) > 0

    def test_appointment_on(self):
        facts = self.extractor.extract("Appointment on Monday with Dr. Smith.")
        assert any(f.predicate == "has_event" for f in facts)

    def test_schedule_subject_prefix(self):
        facts = self.extractor.extract("Meeting at noon in conference room.")
        events = [f for f in facts if f.subject.startswith("ops.schedule")]
        assert len(events) > 0


# ---------------------------------------------------------------------------
# FactExtractor - Preference patterns
# ---------------------------------------------------------------------------


class TestFactExtractorPreference:
    def setup_method(self):
        self.extractor = FactExtractor()

    def test_prefers(self):
        facts = self.extractor.extract("He prefers dark mode.")
        assert any(f.predicate == "prefers" for f in facts)

    def test_likes(self):
        facts = self.extractor.extract("She likes running in the morning.")
        assert any(f.predicate == "prefers" for f in facts)

    def test_favorite(self):
        facts = self.extractor.extract("His favorite color is blue.")
        prefs = [f for f in facts if f.predicate == "prefers"]
        assert len(prefs) > 0


# ---------------------------------------------------------------------------
# FactExtractor - Family patterns
# ---------------------------------------------------------------------------


class TestFactExtractorFamily:
    def setup_method(self):
        self.extractor = FactExtractor()

    def test_son_named(self):
        facts = self.extractor.extract("He has a son named Oliver")
        family = [f for f in facts if f.predicate == "family_relation"]
        assert len(family) > 0
        assert any("oliver" in f.subject.lower() for f in family)

    def test_daughter(self):
        facts = self.extractor.extract("Their daughter Emily is in school")
        assert any(f.predicate == "family_relation" for f in facts)

    def test_family_subject_prefix(self):
        facts = self.extractor.extract("His wife Sarah works at the hospital")
        family = [f for f in facts if f.subject.startswith("family.member")]
        assert len(family) > 0


# ---------------------------------------------------------------------------
# FactExtractor - Location patterns
# ---------------------------------------------------------------------------


class TestFactExtractorLocation:
    def setup_method(self):
        self.extractor = FactExtractor()

    def test_lives_in(self):
        facts = self.extractor.extract("He lives in Portland, Oregon.")
        locs = [f for f in facts if f.predicate == "located_at"]
        assert len(locs) > 0

    def test_address_is(self):
        facts = self.extractor.extract("Her address is 123 Main Street.")
        assert any(f.predicate == "located_at" for f in facts)


# ---------------------------------------------------------------------------
# FactExtractor - Finance patterns
# ---------------------------------------------------------------------------


class TestFactExtractorFinance:
    def setup_method(self):
        self.extractor = FactExtractor()

    def test_salary_is(self):
        facts = self.extractor.extract("His salary is $75,000 per year")
        fin = [f for f in facts if f.predicate == "earns"]
        assert len(fin) > 0

    def test_income_of(self):
        facts = self.extractor.extract("She has income of $50,000 per month")
        assert any(f.predicate == "earns" for f in facts)


# ---------------------------------------------------------------------------
# FactExtractor - Edge cases
# ---------------------------------------------------------------------------


class TestFactExtractorEdgeCases:
    def setup_method(self):
        self.extractor = FactExtractor()

    def test_empty_text(self):
        facts = self.extractor.extract("")
        assert facts == []

    def test_no_matching_patterns(self):
        facts = self.extractor.extract("The weather is sunny today.")
        assert facts == []

    def test_cap_at_ten_facts(self):
        """Even with many matches, at most 10 facts are returned."""
        # Build text with many preference matches
        text = ". ".join([f"He prefers item{i} daily" for i in range(20)])
        facts = self.extractor.extract(text)
        assert len(facts) <= 10

    def test_short_match_skipped(self):
        """Object values shorter than 2 chars are skipped."""
        # "takes X for" where X is single char should be skipped
        facts = self.extractor.extract("takes X for something daily")
        health_facts = [f for f in facts if f.predicate == "takes"]
        # Single char "X" should be filtered out
        assert all(len(f.object_val) >= 2 for f in health_facts)

    def test_confidence_values_reasonable(self):
        """All extracted facts have confidence between 0 and 1."""
        facts = self.extractor.extract(
            "He takes aspirin for pain daily. Meeting at noon. Prefers tea."
        )
        for f in facts:
            assert 0.0 <= f.confidence <= 1.0

    def test_source_and_branch_params_accepted(self):
        """extract() accepts optional source and branch parameters."""
        facts = self.extractor.extract(
            "He takes aspirin for pain daily",
            source="test_source",
            branch="health",
        )
        # Just confirm it doesn't error -- source/branch aren't stored in FactTriple
        assert isinstance(facts, list)
