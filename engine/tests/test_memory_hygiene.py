"""Tests for memory_hygiene module (Task F)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from jarvis_engine._compat import UTC
from jarvis_engine.memory_hygiene import (
    HygieneReport,
    MemoryHygieneEngine,
    classify_record,
    hygiene_dashboard_metrics,
    is_protected,
)


# ---------------------------------------------------------------------------
# classify_record tests
# ---------------------------------------------------------------------------


class TestClassifyRecord:
    """Tests for rule-based record classification."""

    def test_empty_content_is_junk(self):
        result = classify_record({"record_id": "r1", "summary": "", "confidence": 0.72})
        assert result["quality"] == "junk"
        assert result["confidence"] >= 0.90

    def test_whitespace_only_is_junk(self):
        result = classify_record({"record_id": "r2", "summary": "   \n\t  "})
        assert result["quality"] == "junk"

    def test_very_short_is_junk(self):
        result = classify_record({"record_id": "r3", "summary": "ab"})
        assert result["quality"] == "junk"
        assert "too short" in result["reason"]

    def test_repeated_chars_is_junk(self):
        result = classify_record({"record_id": "r4", "summary": "aaaaaaaaaaaaa"})
        assert result["quality"] == "junk"

    def test_test_data_is_junk(self):
        result = classify_record({"record_id": "r5", "summary": "test_function_output_check"})
        assert result["quality"] == "junk"

    def test_lorem_ipsum_is_junk(self):
        result = classify_record({"record_id": "r6", "summary": "Lorem ipsum dolor sit amet"})
        assert result["quality"] == "junk"

    def test_greeting_is_ephemeral(self):
        result = classify_record({"record_id": "r7", "summary": "Hello!"})
        assert result["quality"] == "ephemeral"

    def test_thanks_is_ephemeral(self):
        result = classify_record({"record_id": "r8", "summary": "Thank you"})
        assert result["quality"] == "ephemeral"

    def test_ok_is_ephemeral(self):
        result = classify_record({"record_id": "r9", "summary": "Ok"})
        assert result["quality"] == "ephemeral"

    def test_short_greeting_words(self):
        result = classify_record({"record_id": "r10", "summary": "Hi there"})
        assert result["quality"] == "ephemeral"

    def test_short_low_confidence_is_ephemeral(self):
        result = classify_record({"record_id": "r11", "summary": "short note", "confidence": 0.4})
        assert result["quality"] == "ephemeral"

    def test_medical_keyword_is_high_signal(self):
        result = classify_record({
            "record_id": "r12",
            "summary": "User takes medication for blood pressure daily",
        })
        assert result["quality"] == "high_signal"

    def test_financial_keyword_is_high_signal(self):
        result = classify_record({
            "record_id": "r13",
            "summary": "Monthly salary is deposited to checking account",
        })
        assert result["quality"] == "high_signal"

    def test_security_keyword_is_high_signal(self):
        result = classify_record({
            "record_id": "r14",
            "summary": "Updated the password for the main credential vault",
        })
        assert result["quality"] == "high_signal"

    def test_multiple_high_signal_keywords_boost_confidence(self):
        result = classify_record({
            "record_id": "r15",
            "summary": "Doctor prescribed new medication for the diagnosed condition",
        })
        assert result["quality"] == "high_signal"
        assert result["confidence"] >= 0.90

    def test_high_confidence_is_high_signal(self):
        result = classify_record({
            "record_id": "r16",
            "summary": "Some important context about the project",
            "confidence": 0.95,
        })
        assert result["quality"] == "high_signal"

    def test_user_pinned_is_high_signal(self):
        result = classify_record({
            "record_id": "r17",
            "summary": "Random note about something",
            "tags": '["user_pinned"]',
        })
        assert result["quality"] == "high_signal"

    def test_normal_content_is_contextual(self):
        result = classify_record({
            "record_id": "r18",
            "summary": "The project uses a microservices architecture with gRPC communication",
        })
        assert result["quality"] == "contextual"

    def test_missing_fields_handled(self):
        result = classify_record({"record_id": "r19"})
        # Empty summary -> junk
        assert result["quality"] == "junk"


# ---------------------------------------------------------------------------
# is_protected tests
# ---------------------------------------------------------------------------


class TestIsProtected:
    """Tests for anti-loss guardrails."""

    def test_high_signal_is_protected(self):
        protected, reason = is_protected({"signal_quality": "high_signal"})
        assert protected
        assert "high_signal" in reason

    def test_kg_referenced_is_protected(self):
        protected, reason = is_protected(
            {"record_id": "r1"},
            kg_fact_ids={"r1"},
        )
        assert protected
        assert "knowledge graph" in reason

    def test_active_mission_is_protected(self):
        protected, reason = is_protected(
            {"record_id": "r1", "task_id": "mission_abc"},
            active_mission_ids={"mission_abc"},
        )
        assert protected
        assert "mission" in reason

    def test_user_pinned_is_protected(self):
        protected, reason = is_protected(
            {"record_id": "r1", "tags": '["user_pinned"]'},
        )
        assert protected
        assert "pinned" in reason

    def test_high_access_count_is_protected(self):
        protected, reason = is_protected(
            {"record_id": "r1", "access_count": 5},
        )
        assert protected
        assert "access count" in reason

    def test_recent_record_is_protected(self):
        recent_ts = datetime.now(UTC).isoformat()
        protected, reason = is_protected(
            {"record_id": "r1", "ts": recent_ts},
        )
        assert protected
        assert "cooling period" in reason

    def test_old_unprotected_record(self):
        old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        protected, reason = is_protected(
            {"record_id": "r1", "ts": old_ts, "access_count": 0},
        )
        assert not protected

    def test_anchor_entity_protected(self):
        protected, reason = is_protected(
            {"record_id": "r1"},
            anchor_entity_ids={"r1"},
        )
        assert protected
        assert "anchor entity" in reason


# ---------------------------------------------------------------------------
# MemoryHygieneEngine tests
# ---------------------------------------------------------------------------


class TestMemoryHygieneEngine:
    """Tests for the hygiene engine."""

    def _make_engine_mock(self, records: list[dict]) -> MagicMock:
        engine = MagicMock()
        engine.get_all_records_for_tier_maintenance.return_value = [
            {"record_id": r["record_id"], "ts": r.get("ts", ""), "access_count": 0,
             "confidence": r.get("confidence", 0.72), "tier": "warm"}
            for r in records
        ]
        engine.get_record.side_effect = lambda rid: next(
            (r for r in records if r["record_id"] == rid), None
        )
        engine.get_records_batch.side_effect = lambda ids: [
            r for r in records if r["record_id"] in ids
        ]
        engine.delete_records_batch.return_value = 0
        return engine

    def test_scan_and_classify_returns_results(self, tmp_path):
        records = [
            {"record_id": "r1", "summary": "Hello!", "confidence": 0.72},
            {"record_id": "r2", "summary": "User takes medication daily", "confidence": 0.72},
        ]
        engine = self._make_engine_mock(records)
        hygiene = MemoryHygieneEngine(tmp_path)
        results = hygiene.scan_and_classify(engine)
        assert len(results) == 2
        qualities = {r["record_id"]: r["quality"] for r in results}
        assert qualities["r1"] == "ephemeral"
        assert qualities["r2"] == "high_signal"

    def test_identify_cleanup_candidates_respects_age(self, tmp_path):
        old_ts = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        recent_ts = datetime.now(UTC).isoformat()

        classifications = [
            {"record_id": "old_junk", "quality": "junk", "confidence": 0.9, "reason": "test"},
            {"record_id": "new_junk", "quality": "junk", "confidence": 0.9, "reason": "test"},
        ]
        records_by_id = {
            "old_junk": {"record_id": "old_junk", "ts": old_ts, "access_count": 0},
            "new_junk": {"record_id": "new_junk", "ts": recent_ts, "access_count": 0},
        }
        hygiene = MemoryHygieneEngine(tmp_path)
        candidates = hygiene.identify_cleanup_candidates(classifications, records_by_id)
        # Only old_junk should be a candidate (>3 days)
        assert len(candidates) == 1
        assert candidates[0]["record_id"] == "old_junk"

    def test_identify_cleanup_skips_protected(self, tmp_path):
        old_ts = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        classifications = [
            {"record_id": "r1", "quality": "junk", "confidence": 0.9, "reason": "test"},
        ]
        records_by_id = {
            "r1": {"record_id": "r1", "ts": old_ts, "access_count": 5},  # high access = protected
        }
        hygiene = MemoryHygieneEngine(tmp_path)
        candidates = hygiene.identify_cleanup_candidates(classifications, records_by_id)
        assert len(candidates) == 0

    def test_run_cleanup_dry_run(self, tmp_path):
        old_ts = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        records = [
            {"record_id": "junk1", "summary": "aaaaaaaaaaaaaaa", "confidence": 0.72,
             "ts": old_ts, "access_count": 0, "tags": "[]"},
            {"record_id": "good1", "summary": "User has a doctor appointment on Monday",
             "confidence": 0.72, "ts": old_ts, "access_count": 0, "tags": "[]"},
        ]
        engine = self._make_engine_mock(records)
        hygiene = MemoryHygieneEngine(tmp_path)
        report = hygiene.run_cleanup(engine, dry_run=True)
        assert report.scanned == 2
        assert report.archived == 0  # dry run = no deletes
        assert report.distribution["junk"] >= 1
        assert report.distribution["high_signal"] >= 1

    def test_run_cleanup_deletes_old_junk(self, tmp_path):
        old_ts = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        records = [
            {"record_id": "junk1", "summary": "zzzzzzzzzzzzzzz", "confidence": 0.72,
             "ts": old_ts, "access_count": 0, "tags": "[]"},
        ]
        engine = self._make_engine_mock(records)
        engine.delete_records_batch.return_value = 1
        hygiene = MemoryHygieneEngine(tmp_path)
        report = hygiene.run_cleanup(engine, dry_run=False)
        assert report.cleanup_candidates >= 1
        engine.delete_records_batch.assert_called_once()

    def test_run_cleanup_never_deletes_high_signal(self, tmp_path):
        old_ts = (datetime.now(UTC) - timedelta(days=90)).isoformat()
        records = [
            {"record_id": "important", "summary": "Remember to take medication every morning",
             "confidence": 0.72, "ts": old_ts, "access_count": 0, "tags": "[]"},
        ]
        engine = self._make_engine_mock(records)
        hygiene = MemoryHygieneEngine(tmp_path)
        report = hygiene.run_cleanup(engine, dry_run=False)
        assert report.archived == 0
        engine.delete_records_batch.assert_not_called()

    def test_ephemeral_requires_14_day_age(self, tmp_path):
        ts_10_days = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        ts_20_days = (datetime.now(UTC) - timedelta(days=20)).isoformat()

        classifications = [
            {"record_id": "young", "quality": "ephemeral", "confidence": 0.9, "reason": "test"},
            {"record_id": "old", "quality": "ephemeral", "confidence": 0.9, "reason": "test"},
        ]
        records_by_id = {
            "young": {"record_id": "young", "ts": ts_10_days, "access_count": 0},
            "old": {"record_id": "old", "ts": ts_20_days, "access_count": 0},
        }
        hygiene = MemoryHygieneEngine(tmp_path)
        candidates = hygiene.identify_cleanup_candidates(classifications, records_by_id)
        ids = [c["record_id"] for c in candidates]
        assert "old" in ids
        assert "young" not in ids


# ---------------------------------------------------------------------------
# HygieneReport tests
# ---------------------------------------------------------------------------


class TestHygieneReport:
    """Tests for the report dataclass."""

    def test_default_distribution(self):
        report = HygieneReport()
        assert report.distribution == {
            "high_signal": 0, "contextual": 0, "ephemeral": 0, "junk": 0,
        }
        assert report.scanned == 0
        assert report.archived == 0

    def test_report_has_timestamp(self):
        report = HygieneReport()
        assert report.timestamp  # non-empty


# ---------------------------------------------------------------------------
# hygiene_dashboard_metrics tests
# ---------------------------------------------------------------------------


class TestHygieneDashboardMetrics:
    """Tests for dashboard integration."""

    def test_returns_empty_when_no_history(self, tmp_path):
        result = hygiene_dashboard_metrics(tmp_path)
        assert result == {}

    def test_returns_metrics_from_history(self, tmp_path):
        import json
        runtime_dir = tmp_path / ".planning" / "runtime"
        runtime_dir.mkdir(parents=True)
        history = runtime_dir / "hygiene_history.jsonl"
        entry = {
            "timestamp": "2026-03-08T00:00:00Z",
            "distribution": {"high_signal": 10, "contextual": 20, "ephemeral": 5, "junk": 2},
            "cleanup_candidates": 3,
            "archived": 2,
            "protected": 8,
        }
        history.write_text(json.dumps(entry) + "\n")

        with patch("jarvis_engine._shared.runtime_dir", return_value=runtime_dir):
            result = hygiene_dashboard_metrics(tmp_path)
        assert isinstance(result, dict)
        assert result.get("last_scan_utc") == "2026-03-08T00:00:00Z"
        assert result.get("archived") == 2


# ---------------------------------------------------------------------------
# CQRS handler tests
# ---------------------------------------------------------------------------


class TestMemoryHygieneHandler:
    """Tests for the CQRS handler."""

    def test_handler_with_no_engine(self):
        from jarvis_engine.handlers.ops_handlers import MemoryHygieneHandler
        from jarvis_engine.commands.ops_commands import MemoryHygieneCommand

        handler = MemoryHygieneHandler(Path("/tmp"), engine=None)
        result = handler.handle(MemoryHygieneCommand(dry_run=True))
        assert result.return_code == 2
        assert "No memory engine" in result.message

    def test_handler_runs_scan(self, tmp_path):
        from jarvis_engine.handlers.ops_handlers import MemoryHygieneHandler
        from jarvis_engine.commands.ops_commands import MemoryHygieneCommand

        engine = MagicMock()
        engine.get_all_records_for_tier_maintenance.return_value = []

        handler = MemoryHygieneHandler(tmp_path, engine=engine)
        result = handler.handle(MemoryHygieneCommand(dry_run=True))
        assert result.return_code == 0
        assert result.scanned == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case handling."""

    def test_classify_with_none_tags(self):
        result = classify_record({
            "record_id": "r1",
            "summary": "Normal conversation about weather patterns",
            "tags": None,
        })
        assert result["quality"] in ("contextual", "high_signal", "ephemeral")

    def test_classify_with_list_tags(self):
        result = classify_record({
            "record_id": "r1",
            "summary": "Normal content here",
            "tags": ["some_tag"],
        })
        assert result["quality"] == "contextual"

    def test_protection_with_z_timestamp(self):
        recent_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        protected, _ = is_protected({"record_id": "r1", "ts": recent_ts})
        assert protected

    def test_protection_with_invalid_timestamp(self):
        protected, _ = is_protected(
            {"record_id": "r1", "ts": "not-a-date", "access_count": 0},
        )
        # Should not crash, returns unprotected
        assert not protected

    def test_classify_decision_keyword(self):
        result = classify_record({
            "record_id": "r1",
            "summary": "We decided to use the new architecture approach",
        })
        assert result["quality"] == "high_signal"

    def test_classify_appointment_keyword(self):
        result = classify_record({
            "record_id": "r1",
            "summary": "Set up a meeting with the team next Tuesday",
        })
        assert result["quality"] == "high_signal"
