"""Comprehensive tests for jarvis_engine.phone_guard module.

Covers spam detection, phone action building, call log loading, number
normalization, timestamp parsing, and report generation.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from jarvis_engine._compat import UTC

from jarvis_engine.phone_guard import (
    PhoneAction,
    SpamCandidate,
    area_key,
    normalize_number,
    _parse_ts,
    append_phone_actions,
    build_phone_action,
    build_spam_block_actions,
    detect_spam_candidates,
    load_call_log,
    write_spam_report,
)


# ---------------------------------------------------------------------------
# normalize_number tests
# ---------------------------------------------------------------------------

class TestNormalizeNumber:
    def test_us_10_digit(self):
        assert normalize_number("4155551234") == "+14155551234"

    def test_us_11_digit_with_leading_1(self):
        assert normalize_number("14155551234") == "+14155551234"

    def test_plus_prefix_preserved(self):
        assert normalize_number("+14155551234") == "+14155551234"

    def test_international_00_prefix(self):
        assert normalize_number("004412345678") == "+4412345678"

    def test_strips_non_digit_chars(self):
        assert normalize_number("(415) 555-1234") == "+14155551234"

    def test_short_number_returns_empty(self):
        assert normalize_number("12345") == ""

    def test_empty_string_returns_empty(self):
        assert normalize_number("") == ""

    def test_international_8_digits(self):
        result = normalize_number("12345678")
        assert result.startswith("+")
        assert len(result) >= 9

    def test_international_plus_long_number(self):
        result = normalize_number("+442012345678")
        assert result == "+442012345678"


# ---------------------------------------------------------------------------
# _parse_ts tests
# ---------------------------------------------------------------------------

class TestParseTs:
    def test_iso_format(self):
        result = _parse_ts("2026-02-20T10:30:00+00:00")
        assert result is not None
        assert result.year == 2026

    def test_z_suffix(self):
        result = _parse_ts("2026-02-20T10:30:00Z")
        assert result is not None

    def test_naive_datetime_gets_utc(self):
        result = _parse_ts("2026-02-20T10:30:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_empty_returns_none(self):
        assert _parse_ts("") is None

    def test_invalid_returns_none(self):
        assert _parse_ts("not a date") is None

    def test_none_value(self):
        assert _parse_ts(None) is None


# ---------------------------------------------------------------------------
# area_key tests
# ---------------------------------------------------------------------------

class TestAreaKey:
    def test_us_number(self):
        # NPA-NXX precision: +1 + NPA(415) + NXX(555) = 8 chars
        assert area_key("+14155551234") == "+1415555"

    def test_international_number(self):
        result = area_key("+442012345678")
        assert result == "+44201"

    def test_short_us_number_falls_through(self):
        # US numbers shorter than 8 chars fall through to international match
        assert area_key("+1415") == ""  # only 5 chars, < 6 required for intl
        assert area_key("+14155") == "+14155"  # 6 chars, meets intl threshold
        assert area_key("+141555") == "+14155"  # 7 chars, uses 6-char intl match

    def test_short_number_returns_empty(self):
        assert area_key("+123") == ""

    def test_no_plus_returns_empty(self):
        assert area_key("4155551234") == ""


# ---------------------------------------------------------------------------
# load_call_log tests
# ---------------------------------------------------------------------------

class TestLoadCallLog:
    def test_valid_json_list(self, tmp_path):
        log_file = tmp_path / "calls.json"
        log_file.write_text(json.dumps([
            {"number": "+14155551234", "type": "incoming"},
            {"number": "+14155555678", "type": "missed"},
        ]), encoding="utf-8")
        result = load_call_log(log_file)
        assert len(result) == 2

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_call_log(tmp_path / "nonexistent.json") == []

    def test_invalid_json_returns_empty(self, tmp_path):
        log_file = tmp_path / "bad.json"
        log_file.write_text("{not json}", encoding="utf-8")
        assert load_call_log(log_file) == []

    def test_non_list_returns_empty(self, tmp_path):
        log_file = tmp_path / "dict.json"
        log_file.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        assert load_call_log(log_file) == []

    def test_filters_non_dict_items(self, tmp_path):
        log_file = tmp_path / "mixed.json"
        log_file.write_text(json.dumps([
            {"number": "+14155551234"},
            "not a dict",
            42,
            {"number": "+14155555678"},
        ]), encoding="utf-8")
        result = load_call_log(log_file)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# detect_spam_candidates tests
# ---------------------------------------------------------------------------

class TestDetectSpamCandidates:
    def _make_call(self, number, call_type="missed", duration=0, contact="", ts_offset_min=0, label=""):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        return {
            "number": number,
            "type": call_type,
            "duration_sec": duration,
            "contact_name": contact,
            "caller_label": label,
            "ts_utc": (now - timedelta(minutes=ts_offset_min)).isoformat(),
        }

    def test_no_calls_returns_empty(self):
        assert detect_spam_candidates([]) == []

    def test_single_call_no_spam(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        log = [self._make_call("+14155551234", "incoming", 120, "Mom")]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 0

    def test_high_repeat_calls_detected(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        log = [self._make_call("+14155551234", "missed", 0, "", i) for i in range(4)]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 1
        assert result[0].calls == 4
        assert "high_repeat_volume" in result[0].reasons

    def test_repeat_volume_three_calls(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        log = [self._make_call("+14155551234", "missed", 0, "", i) for i in range(3)]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 1
        assert "repeat_volume" in result[0].reasons

    def test_spam_label_detected(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        log = [self._make_call("+14155551234", "incoming", 5, "", 0, "spam caller")]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 1
        assert "spam_or_scam_label" in result[0].reasons

    def test_scam_label_detected(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        log = [self._make_call("+14155551234", "incoming", 5, "SCAM", 0)]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 1
        assert "spam_or_scam_label" in result[0].reasons

    def test_old_calls_filtered_out(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        # Calls older than 14 days
        old_ts = (now - timedelta(days=20)).isoformat()
        log = [
            {"number": "+14155551234", "type": "missed", "duration_sec": 0,
             "contact_name": "", "ts_utc": old_ts},
        ] * 5
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 0

    def test_missed_ratio_calculation(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        log = [
            self._make_call("+14155551234", "missed", 0, "", 0),
            self._make_call("+14155551234", "missed", 0, "", 1),
            self._make_call("+14155551234", "missed", 0, "", 2),
            self._make_call("+14155551234", "incoming", 120, "", 3),
        ]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 1
        assert result[0].missed_ratio == 0.75

    def test_burst_day_pattern(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        # 2 calls on the same day
        log = [
            self._make_call("+14155551234", "missed", 0, "", 0),
            self._make_call("+14155551234", "missed", 0, "", 30),
        ]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 1
        assert "burst_day_pattern" in result[0].reasons

    def test_candidates_sorted_by_score_descending(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        log = []
        # Number A: low spam (just repeat volume)
        for i in range(3):
            log.append(self._make_call("+14155550001", "missed", 0, "", i))
        # Number B: high spam (repeat + spam label)
        for i in range(4):
            log.append(self._make_call("+14155550002", "missed", 0, "", i, "telemarketer"))
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) >= 2
        assert result[0].score >= result[1].score

    def test_score_capped_at_099(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        # Trigger all scoring factors
        log = [self._make_call("+14155551234", "missed", 0, "", i, "spam") for i in range(5)]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 1
        assert result[0].score <= 0.99

    def test_unknown_inbound_pattern(self):
        now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
        log = [self._make_call("+14155551234", "incoming", 3, "", i) for i in range(4)]
        result = detect_spam_candidates(log, now_utc=now)
        assert len(result) == 1
        assert "unknown_inbound_pattern" in result[0].reasons


# ---------------------------------------------------------------------------
# build_spam_block_actions tests
# ---------------------------------------------------------------------------

class TestBuildSpamBlockActions:
    def test_blocks_above_threshold(self):
        candidates = [
            SpamCandidate("+14155551234", 0.80, 4, 1.0, 2.0, ["high_repeat_volume"]),
            SpamCandidate("+14155555678", 0.50, 3, 0.5, 10.0, ["repeat_volume"]),
        ]
        actions = build_spam_block_actions(candidates, threshold=0.65)
        assert len(actions) == 1
        assert actions[0].action == "block_number"
        assert actions[0].number == "+14155551234"

    def test_global_silence_rule_when_5_plus(self):
        candidates = [
            SpamCandidate(f"+141555500{i:02d}", 0.90, 4, 1.0, 0.0, ["high_repeat_volume"])
            for i in range(6)
        ]
        actions = build_spam_block_actions(candidates, threshold=0.65, add_global_silence_rule=True)
        silence = [a for a in actions if a.action == "silence_unknown_callers"]
        assert len(silence) == 1

    def test_no_silence_rule_below_5(self):
        candidates = [
            SpamCandidate(f"+141555500{i:02d}", 0.90, 4, 1.0, 0.0, ["high_repeat_volume"])
            for i in range(3)
        ]
        actions = build_spam_block_actions(candidates, threshold=0.65, add_global_silence_rule=True)
        silence = [a for a in actions if a.action == "silence_unknown_callers"]
        assert len(silence) == 0

    def test_no_silence_rule_when_disabled(self):
        candidates = [
            SpamCandidate(f"+141555500{i:02d}", 0.90, 4, 1.0, 0.0, ["high_repeat_volume"])
            for i in range(6)
        ]
        actions = build_spam_block_actions(candidates, threshold=0.65, add_global_silence_rule=False)
        silence = [a for a in actions if a.action == "silence_unknown_callers"]
        assert len(silence) == 0

    def test_empty_candidates_returns_empty(self):
        assert build_spam_block_actions([], threshold=0.65) == []


# ---------------------------------------------------------------------------
# build_phone_action tests
# ---------------------------------------------------------------------------

class TestBuildPhoneAction:
    def test_send_sms_valid(self):
        action = build_phone_action("send_sms", "(415) 555-1234", "hello")
        assert action.action == "send_sms"
        assert action.number == "+14155551234"
        assert action.message == "hello"

    def test_send_sms_requires_message(self):
        with pytest.raises(ValueError, match="SMS action requires message"):
            build_phone_action("send_sms", "+14155551234", "")

    def test_send_sms_whitespace_only_message_fails(self):
        with pytest.raises(ValueError, match="SMS action requires message"):
            build_phone_action("send_sms", "+14155551234", "   ")

    def test_place_call_valid(self):
        action = build_phone_action("place_call", "+14155551234")
        assert action.action == "place_call"
        assert action.number == "+14155551234"

    def test_block_number_valid(self):
        action = build_phone_action("block_number", "+14155551234")
        assert action.action == "block_number"

    def test_ignore_call_valid(self):
        action = build_phone_action("ignore_call", "+14155551234")
        assert action.action == "ignore_call"

    def test_silence_unknown_callers_no_number_needed(self):
        action = build_phone_action("silence_unknown_callers", "")
        assert action.action == "silence_unknown_callers"
        assert action.number == ""

    def test_unsupported_action_raises(self):
        with pytest.raises(ValueError, match="Unsupported action"):
            build_phone_action("delete_number", "+14155551234")

    def test_invalid_number_raises(self):
        with pytest.raises(ValueError, match="Invalid phone number"):
            build_phone_action("place_call", "123")

    def test_created_utc_is_set(self):
        action = build_phone_action("place_call", "+14155551234")
        assert action.created_utc
        # Should be valid ISO format
        datetime.fromisoformat(action.created_utc)

    def test_custom_reason(self):
        action = build_phone_action("block_number", "+14155551234", reason="manual_block")
        assert action.reason == "manual_block"


# ---------------------------------------------------------------------------
# append_phone_actions tests
# ---------------------------------------------------------------------------

class TestAppendPhoneActions:
    def test_appends_to_file(self, tmp_path):
        actions_file = tmp_path / "sub" / "actions.jsonl"
        actions = [
            PhoneAction("block_number", "+14155551234", "", "2026-02-20T00:00:00", "spam"),
            PhoneAction("send_sms", "+14155555678", "hi", "2026-02-20T00:00:00", "user"),
        ]
        append_phone_actions(actions_file, actions)
        lines = actions_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        parsed = json.loads(lines[0])
        assert parsed["action"] == "block_number"

    def test_appends_incrementally(self, tmp_path):
        actions_file = tmp_path / "actions.jsonl"
        a1 = [PhoneAction("block_number", "+14155551234", "", "2026-02-20T00:00:00", "spam")]
        a2 = [PhoneAction("send_sms", "+14155555678", "hi", "2026-02-20T00:00:00", "user")]
        append_phone_actions(actions_file, a1)
        append_phone_actions(actions_file, a2)
        lines = actions_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# write_spam_report tests
# ---------------------------------------------------------------------------

class TestWriteSpamReport:
    def test_writes_valid_json(self, tmp_path):
        report_path = tmp_path / "report.json"
        candidates = [SpamCandidate("+14155551234", 0.80, 4, 1.0, 2.0, ["high_repeat_volume"])]
        actions = [PhoneAction("block_number", "+14155551234", "", "2026-02-20T00:00:00", "spam_guard")]
        write_spam_report(report_path, candidates, actions, threshold=0.65)
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["threshold"] == 0.65
        assert len(data["candidates"]) == 1
        assert len(data["actions"]) == 1
        assert "generated_utc" in data
        assert "prompt_options" in data

    def test_creates_parent_dirs(self, tmp_path):
        report_path = tmp_path / "deep" / "nested" / "report.json"
        write_spam_report(report_path, [], [], threshold=0.5)
        assert report_path.exists()
