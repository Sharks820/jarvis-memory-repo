"""Tests for the interest learning engine."""

import json
from unittest.mock import patch

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

    def test_load_corrupt_json(self, tmp_path):
        """_load returns {} when the JSON file is corrupt."""
        learner = InterestLearner(tmp_path)
        learner._path.write_text("NOT VALID JSON {{{", encoding="utf-8")
        data = learner._load()
        assert data == {}

    def test_save_write_failure(self, tmp_path):
        """_save logs warning instead of crashing on write failure."""
        learner = InterestLearner(tmp_path)
        with patch(
            "jarvis_engine.news.interests.atomic_write_json",
            side_effect=OSError("disk full"),
        ):
            # Should not raise
            learner._save({"topic": {"score": 1.0}})

    def test_decay_all_malformed_timestamp(self, tmp_path):
        """_decay_all skips entries with unparseable timestamps."""
        learner = InterestLearner(tmp_path)
        learner.record_interest("good_topic", weight=1.0)
        # Inject a malformed timestamp directly
        data = json.loads(learner._path.read_text(encoding="utf-8"))
        data["bad_topic"] = {
            "score": 1.0,
            "count": 1,
            "last_seen": "not-a-date",
        }
        learner._path.write_text(json.dumps(data), encoding="utf-8")
        # Should not raise; good_topic still decayed normally
        learner._decay_all(days=10)
        updated = json.loads(learner._path.read_text(encoding="utf-8"))
        # bad_topic timestamp unchanged (skipped)
        assert updated["bad_topic"]["last_seen"] == "not-a-date"
        # good_topic timestamp was shifted back
        assert updated["good_topic"]["last_seen"] != data["good_topic"]["last_seen"]
