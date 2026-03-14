"""Tests for OBS-01 through OBS-04 observability requirements."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_engine.memory.activity_feed import (
    ActivityCategory,
    ActivityFeed,
    redact_pii,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def feed(tmp_path: Path) -> ActivityFeed:
    f = ActivityFeed(db_path=tmp_path / "obs_test.db")
    yield f  # type: ignore[misc]
    f.close()


# ---------------------------------------------------------------------------
# OBS-01: Engine action logging with PII redaction
# ---------------------------------------------------------------------------


class TestRedactPii:
    """Unit tests for the redact_pii helper."""

    def test_redact_phone_number(self) -> None:
        assert "PHONE_REDACTED" in redact_pii("Call me at 555-867-5309 please")

    def test_redact_international_phone(self) -> None:
        assert "PHONE_REDACTED" in redact_pii("Number: +1 234 567 8901")

    def test_redact_email(self) -> None:
        result = redact_pii("Send to user@example.com")
        assert "EMAIL_REDACTED" in result
        assert "user@example.com" not in result

    def test_redact_api_key_sk_prefix(self) -> None:
        result = redact_pii("key=sk-abcdefghijklmnopqrstuvwxyz1234")
        assert "API_KEY_REDACTED" in result

    def test_redact_api_key_akia_prefix(self) -> None:
        result = redact_pii("aws AKIA1234567890ABCDEFGHIJ")
        assert "API_KEY_REDACTED" in result

    def test_no_redaction_for_clean_text(self) -> None:
        text = "Routing query to local Ollama model"
        assert redact_pii(text) == text


class TestLogEngineAction:
    """OBS-01: engine action thinking trace."""

    def test_basic_engine_action(self, feed: ActivityFeed) -> None:
        eid = feed.log_engine_action("route_query", "Chose Ollama for privacy query")
        events = feed.query(category=ActivityCategory.ENGINE_ACTION)
        assert len(events) == 1
        assert events[0].event_id == eid
        assert events[0].summary == "route_query"
        assert events[0].details["detail"] == "Chose Ollama for privacy query"

    def test_engine_action_redacts_email(self, feed: ActivityFeed) -> None:
        feed.log_engine_action("ingest", "Processing email from bob@secret.com")
        events = feed.query(category=ActivityCategory.ENGINE_ACTION)
        assert "bob@secret.com" not in events[0].details["detail"]
        assert "EMAIL_REDACTED" in events[0].details["detail"]

    def test_engine_action_redacts_phone(self, feed: ActivityFeed) -> None:
        feed.log_engine_action("Phone logged: 555-867-5309", "details")
        events = feed.query(category=ActivityCategory.ENGINE_ACTION)
        assert "555-867-5309" not in events[0].summary
        assert "PHONE_REDACTED" in events[0].summary

    def test_engine_action_no_redact_when_disabled(self, feed: ActivityFeed) -> None:
        feed.log_engine_action(
            "raw", "key=sk-abcdefghijklmnopqrstuvwxyz1234",
            secrets_redacted=False,
        )
        events = feed.query(category=ActivityCategory.ENGINE_ACTION)
        assert "sk-abcdefghijklmnopqrstuvwxyz1234" in events[0].details["detail"]


# ---------------------------------------------------------------------------
# OBS-02: Correlation ID generation and propagation
# ---------------------------------------------------------------------------


class TestCorrelationId:
    """OBS-02: correlation_id on every event."""

    def test_auto_generated_correlation_id(self, feed: ActivityFeed) -> None:
        feed.log("test_cat", "hello")
        events = feed.query(limit=1)
        assert len(events) == 1
        assert events[0].correlation_id != ""
        # Should be a 32-char hex UUID
        assert len(events[0].correlation_id) == 32

    def test_explicit_correlation_id(self, feed: ActivityFeed) -> None:
        cid = "my-custom-correlation-123"
        feed.log("test_cat", "linked event", correlation_id=cid)
        events = feed.query(limit=1)
        assert events[0].correlation_id == cid

    def test_correlation_id_propagated_through_engine_action(
        self, feed: ActivityFeed
    ) -> None:
        cid = "trace-abc"
        feed.log_engine_action("step1", "detail", correlation_id=cid)
        events = feed.query(correlation_id=cid)
        assert len(events) == 1
        assert events[0].correlation_id == cid

    def test_mission_id_stored(self, feed: ActivityFeed) -> None:
        feed.log("test_cat", "mission event", mission_id="mission-42")
        events = feed.query(limit=1)
        assert events[0].mission_id == "mission-42"

    def test_query_by_correlation_id(self, feed: ActivityFeed) -> None:
        cid = "shared-cid"
        feed.log("a", "first", correlation_id=cid)
        feed.log("b", "second", correlation_id=cid)
        feed.log("c", "other", correlation_id="different")
        events = feed.query(correlation_id=cid)
        assert len(events) == 2
        assert all(e.correlation_id == cid for e in events)


# ---------------------------------------------------------------------------
# OBS-03: Backend-truth progress
# ---------------------------------------------------------------------------


class TestLogProgress:
    """OBS-03: progress logging."""

    def test_basic_progress(self, feed: ActivityFeed) -> None:
        eid = feed.log_progress("task-1", 50.0, "Halfway done")
        events = feed.query(category=ActivityCategory.PROGRESS)
        assert len(events) == 1
        assert events[0].event_id == eid
        assert events[0].summary == "Halfway done"
        assert events[0].details["task_id"] == "task-1"
        assert events[0].details["progress_pct"] == 50.0

    def test_progress_clamp_min(self, feed: ActivityFeed) -> None:
        feed.log_progress("t", -10.0, "underflow")
        events = feed.query(category=ActivityCategory.PROGRESS)
        assert events[0].details["progress_pct"] == 0.0

    def test_progress_clamp_max(self, feed: ActivityFeed) -> None:
        feed.log_progress("t", 200.0, "overflow")
        events = feed.query(category=ActivityCategory.PROGRESS)
        assert events[0].details["progress_pct"] == 100.0

    def test_progress_with_correlation_id(self, feed: ActivityFeed) -> None:
        cid = "prog-cid"
        feed.log_progress("t", 75.0, "Almost there", correlation_id=cid)
        events = feed.query(correlation_id=cid)
        assert len(events) == 1
        assert events[0].details["progress_pct"] == 75.0


# ---------------------------------------------------------------------------
# OBS-04: Structured error surfaces
# ---------------------------------------------------------------------------


class TestLogError:
    """OBS-04: structured error with code + user message + diagnostic."""

    def test_basic_error(self, feed: ActivityFeed) -> None:
        eid = feed.log_error(
            error_code="STT_TIMEOUT",
            user_message="Voice recognition timed out. Try again or speak louder.",
            technical_detail="Timeout after 30s in stt.transcribe_smart()",
        )
        events = feed.query(category=ActivityCategory.ERROR)
        assert len(events) == 1
        assert events[0].event_id == eid
        assert events[0].summary == "Voice recognition timed out. Try again or speak louder."
        assert events[0].details["error_code"] == "STT_TIMEOUT"
        assert "30s" in events[0].details["technical_detail"]

    def test_error_with_correlation_id(self, feed: ActivityFeed) -> None:
        cid = "err-cid-1"
        feed.log_error(
            error_code="GATEWAY_FALLBACK_EXHAUSTED",
            user_message="All AI providers are currently unavailable.",
            technical_detail="Tried: ollama, claude, groq — all failed",
            correlation_id=cid,
        )
        events = feed.query(correlation_id=cid)
        assert len(events) == 1
        assert events[0].details["error_code"] == "GATEWAY_FALLBACK_EXHAUSTED"

    def test_error_with_mission_id(self, feed: ActivityFeed) -> None:
        feed.log_error(
            error_code="MISSION_STEP_FAILED",
            user_message="A mission step could not be completed.",
            technical_detail="Step 3 raised ValueError",
            mission_id="m-99",
        )
        events = feed.query(category=ActivityCategory.ERROR)
        assert events[0].mission_id == "m-99"

    def test_error_code_is_machine_readable(self, feed: ActivityFeed) -> None:
        feed.log_error("CODE_123", "msg", "detail")
        events = feed.query(category=ActivityCategory.ERROR)
        # error_code should be in details, not mangled
        assert events[0].details["error_code"] == "CODE_123"


# ---------------------------------------------------------------------------
# Integration: multiple OBS features together
# ---------------------------------------------------------------------------


class TestObservabilityIntegration:
    """Cross-cutting tests combining multiple OBS features."""

    def test_full_lifecycle_trace(self, feed: ActivityFeed) -> None:
        """Simulate a full request lifecycle with correlation."""
        cid = "lifecycle-001"
        mid = "mission-7"

        feed.log_engine_action(
            "start_query", "User asked about weather",
            correlation_id=cid, mission_id=mid,
        )
        feed.log_progress("weather-q", 30.0, "Routing to Ollama", correlation_id=cid)
        feed.log_progress("weather-q", 90.0, "Generating response", correlation_id=cid)
        feed.log_engine_action(
            "complete_query", "Response sent",
            correlation_id=cid, mission_id=mid,
        )

        all_events = feed.query(correlation_id=cid)
        assert len(all_events) == 4
        categories = {e.category for e in all_events}
        assert ActivityCategory.ENGINE_ACTION in categories
        assert ActivityCategory.PROGRESS in categories

    def test_error_in_lifecycle(self, feed: ActivityFeed) -> None:
        cid = "lifecycle-err"
        feed.log_engine_action("start", "begin", correlation_id=cid)
        feed.log_error(
            "INTERNAL_ERROR", "Something went wrong.", "traceback...",
            correlation_id=cid,
        )
        events = feed.query(correlation_id=cid)
        assert len(events) == 2
