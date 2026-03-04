"""Tests for the interest learning engine."""
import pytest
from jarvis_engine.news.interests import InterestLearner


class TestInterestLearner:
    def test_record_interest_creates_entry(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("technology", weight=1.0)
        profile = learner.get_profile()
        assert "technology" in profile
        assert profile["technology"] > 0

    def test_decay_reduces_stale_interests(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("sports", weight=1.0)
        learner._decay_all(days=60)  # force 60-day decay
        profile = learner.get_profile()
        assert profile.get("sports", 0) < 0.5

    def test_negative_interest_reduces_score(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("sports", weight=1.0)
        learner.record_interest("sports", weight=-0.5)
        profile = learner.get_profile()
        assert profile["sports"] < 1.0

    def test_score_never_negative(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("niche", weight=0.1)
        learner.record_interest("niche", weight=-5.0)
        profile = learner.get_profile()
        # Score clamped at 0, so it either doesn't appear or is 0
        assert profile.get("niche", 0) >= 0

    def test_topic_normalization(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("  Technology  ", weight=1.0)
        learner.record_interest("TECHNOLOGY", weight=1.0)
        profile = learner.get_profile()
        assert "technology" in profile
        assert profile["technology"] > 1.5  # both contributions combined

    def test_get_profile_top_n(self, tmp_path):
        learner = InterestLearner(tmp_path)
        for i in range(30):
            learner.record_interest(f"topic_{i}", weight=float(i))
        profile = learner.get_profile(top_n=5)
        assert len(profile) == 5

    def test_empty_profile(self, tmp_path):
        learner = InterestLearner(tmp_path)
        profile = learner.get_profile()
        assert profile == {}

    def test_count_increments(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("ai", weight=0.5)
        learner.record_interest("ai", weight=0.5)
        learner.record_interest("ai", weight=0.5)
        # Internally count should be 3; profile score should be ~1.5
        profile = learner.get_profile()
        assert "ai" in profile
        assert profile["ai"] > 1.0

    def test_very_old_interest_decays_to_nothing(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("ancient", weight=0.1)
        learner._decay_all(days=365)
        profile = learner.get_profile()
        assert "ancient" not in profile  # decayed below 0.01 threshold
