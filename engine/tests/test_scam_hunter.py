"""Tests for scam_hunter — scam campaign detection and intelligence."""
from __future__ import annotations

import json
import pytest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from jarvis_engine._compat import UTC
from jarvis_engine.scam_hunter import (
    CallIntelReport,
    CarrierIntel,
    ScamCampaign,
    build_prefix_block_actions,
    compute_enhanced_spam_score,
    create_call_intel_report,
    detect_campaigns,
    load_call_intel,
    load_campaigns,
    lookup_carrier_cached,
    save_call_intel,
    save_campaigns,
    save_carrier_intel,
    score_time_of_day,
    _generate_campaign_id,
)


# ---------- compute_enhanced_spam_score ----------


class TestComputeEnhancedSpamScore:
    def test_contact_override(self):
        """Contacts always score 0 regardless of other signals."""
        assert compute_enhanced_spam_score(0.9, stir_status="failed", is_in_contacts=True) == 0.0

    def test_stir_failed_boost(self):
        score = compute_enhanced_spam_score(0.0, stir_status="failed")
        assert score == pytest.approx(0.40, abs=0.01)

    def test_stir_not_verified_no_boost_when_clean(self):
        """not_verified only boosts when score is already > 0."""
        score = compute_enhanced_spam_score(0.0, stir_status="not_verified")
        assert score == pytest.approx(0.0, abs=0.01)

    def test_stir_not_verified_boosts_suspicious(self):
        """not_verified adds +0.05 when base score is already > 0."""
        score = compute_enhanced_spam_score(0.3, stir_status="not_verified")
        assert score == pytest.approx(0.35, abs=0.01)

    def test_stir_passed_no_boost(self):
        score = compute_enhanced_spam_score(0.0, stir_status="passed")
        assert score == pytest.approx(0.0, abs=0.01)

    def test_non_fixed_voip(self):
        score = compute_enhanced_spam_score(0.0, line_type="non_fixed_voip")
        assert score == pytest.approx(0.20, abs=0.01)

    def test_voip_line_type(self):
        score = compute_enhanced_spam_score(0.0, line_type="voip")
        assert score == pytest.approx(0.12, abs=0.01)

    def test_restricted_presentation(self):
        score = compute_enhanced_spam_score(0.0, presentation="restricted")
        assert score == pytest.approx(0.10, abs=0.01)

    def test_campaign_boost(self):
        score = compute_enhanced_spam_score(0.0, campaign_confidence=0.80)
        assert score == pytest.approx(0.20, abs=0.01)

    def test_combined_signals(self):
        score = compute_enhanced_spam_score(
            0.3, stir_status="failed", line_type="non_fixed_voip",
            presentation="restricted", campaign_confidence=0.9,
        )
        assert score > 0.90

    def test_cap_at_099(self):
        score = compute_enhanced_spam_score(
            0.99, stir_status="failed", line_type="non_fixed_voip",
            campaign_confidence=1.0, presentation="restricted",
        )
        assert score == pytest.approx(0.99, abs=0.001)


# ---------- detect_campaigns ----------


class TestDetectCampaigns:
    def _make_report(self, number, ts_offset_h=0, stir="not_verified",
                     answered=False, contact_name="", now=None):
        now = now or datetime.now(UTC)
        ts = now - timedelta(hours=ts_offset_h)
        return {
            "number": number,
            "timestamp_utc": ts.isoformat(),
            "stir_status": stir,
            "presentation": "allowed",
            "duration_sec": 0,
            "answered": answered,
            "contact_name": contact_name,
        }

    def test_no_reports_no_campaigns(self):
        assert detect_campaigns([]) == []

    def test_single_number_no_campaign(self):
        now = datetime.now(UTC)
        reports = [self._make_report("+15551230001", now=now)]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 0

    def test_two_numbers_same_prefix_detected(self):
        now = datetime.now(UTC)
        reports = [
            self._make_report("+15551230001", now=now),
            self._make_report("+15551230002", now=now),
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 1
        assert len(campaigns[0].numbers) == 2
        assert campaigns[0].prefix == "+1555123"

    def test_sequential_numbers_boost_confidence(self):
        now = datetime.now(UTC)
        reports = [
            self._make_report("+15551230001", now=now),
            self._make_report("+15551230002", now=now),
            self._make_report("+15551230003", now=now),
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 1
        assert "sequential_numbers" in campaigns[0].signals

    def test_stir_failed_boost(self):
        now = datetime.now(UTC)
        reports = [
            self._make_report("+15551230001", stir="failed", now=now),
            self._make_report("+15551230002", stir="failed", now=now),
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 1
        assert campaigns[0].stir_failed_count == 2
        assert any("stir_failed" in s for s in campaigns[0].signals)

    def test_contacts_excluded(self):
        now = datetime.now(UTC)
        reports = [
            self._make_report("+15551230001", contact_name="Mom", now=now),
            self._make_report("+15551230002", contact_name="Dad", now=now),
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 0

    def test_old_reports_excluded(self):
        now = datetime.now(UTC)
        reports = [
            self._make_report("+15551230001", ts_offset_h=100, now=now),
            self._make_report("+15551230002", ts_offset_h=100, now=now),
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 0

    def test_five_numbers_high_confidence(self):
        now = datetime.now(UTC)
        reports = [
            self._make_report(f"+1555123{i:04d}", now=now)
            for i in range(5)
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 1
        assert campaigns[0].confidence >= 0.35

    def test_different_prefixes_separate_campaigns(self):
        now = datetime.now(UTC)
        reports = [
            self._make_report("+15551230001", now=now),
            self._make_report("+15551230002", now=now),
            self._make_report("+12125550001", now=now),
            self._make_report("+12125550002", now=now),
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 2

    def test_restricted_presentation_boost(self):
        now = datetime.now(UTC)
        reports = [
            {"number": "+15551230001", "timestamp_utc": now.isoformat(),
             "stir_status": "", "presentation": "restricted",
             "duration_sec": 0, "answered": False, "contact_name": ""},
            {"number": "+15551230002", "timestamp_utc": now.isoformat(),
             "stir_status": "", "presentation": "restricted",
             "duration_sec": 0, "answered": False, "contact_name": ""},
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 1
        assert "restricted_presentation" in campaigns[0].signals


# ---------- create_call_intel_report ----------


class TestCreateCallIntelReport:
    def test_basic_report(self):
        report = create_call_intel_report("+15551234567", stir_status="passed")
        assert report.normalized == "+15551234567"
        assert report.prefix == "+1555123"
        assert report.stir_status == "passed"
        assert report.timestamp_utc != ""

    def test_normalization(self):
        report = create_call_intel_report("5551234567")
        assert report.normalized == "+15551234567"


# ---------- Persistence ----------


class TestPersistence:
    def test_save_and_load_campaigns(self, tmp_path):
        path = tmp_path / "campaigns.json"
        campaigns = [
            ScamCampaign(
                campaign_id="abc123",
                prefix="+1555",
                numbers=["+15551230001", "+15551230002"],
                confidence=0.85,
                signals=["rotating_numbers_2"],
            )
        ]
        save_campaigns(path, campaigns)
        loaded = load_campaigns(path)
        assert len(loaded) == 1
        assert loaded[0].campaign_id == "abc123"
        assert loaded[0].confidence == 0.85

    def test_load_missing_file(self, tmp_path):
        assert load_campaigns(tmp_path / "nonexistent.json") == []

    def test_save_and_load_call_intel(self, tmp_path):
        path = tmp_path / "call_intel.jsonl"
        report = create_call_intel_report("+15551234567", stir_status="failed")
        save_call_intel(path, report)
        save_call_intel(path, report)
        loaded = load_call_intel(path)
        assert len(loaded) == 2

    def test_carrier_cache(self, tmp_path):
        path = tmp_path / "carrier_cache.json"
        intel = CarrierIntel(
            number="+15551234567",
            carrier="Vonage",
            line_type="non_fixed_voip",
            is_voip=True,
        )
        save_carrier_intel(path, intel)
        cached = lookup_carrier_cached(path, "+15551234567")
        assert cached is not None
        assert cached.carrier == "Vonage"
        assert cached.is_voip is True

    def test_carrier_cache_miss(self, tmp_path):
        path = tmp_path / "carrier_cache.json"
        assert lookup_carrier_cached(path, "+15559999999") is None


# ---------- build_prefix_block_actions ----------


class TestBuildPrefixBlockActions:
    def test_no_actions_below_threshold(self):
        campaigns = [ScamCampaign(campaign_id="a", prefix="+1555", confidence=0.4)]
        assert build_prefix_block_actions(campaigns) == []

    def test_block_numbers_above_threshold(self):
        campaigns = [ScamCampaign(
            campaign_id="a", prefix="+1555",
            numbers=["+15551230001", "+15551230002"],
            confidence=0.65,
        )]
        actions = build_prefix_block_actions(campaigns)
        block_actions = [a for a in actions if a["action"] == "block_number"]
        assert len(block_actions) == 2

    def test_prefix_silence_at_high_confidence(self):
        campaigns = [ScamCampaign(
            campaign_id="a", prefix="+1555",
            numbers=["+15551230001", "+15551230002", "+15551230003"],
            confidence=0.80,
        )]
        actions = build_prefix_block_actions(campaigns)
        silence_actions = [a for a in actions if a["action"] == "silence_prefix"]
        assert len(silence_actions) == 1


# ---------- _generate_campaign_id ----------


class TestGenerateCampaignId:
    def test_deterministic(self):
        id1 = _generate_campaign_id("+1555", ["+15551230001", "+15551230002"])
        id2 = _generate_campaign_id("+1555", ["+15551230001", "+15551230002"])
        assert id1 == id2

    def test_different_numbers_different_id(self):
        id1 = _generate_campaign_id("+1555", ["+15551230001"])
        id2 = _generate_campaign_id("+1555", ["+15551230002"])
        assert id1 != id2

    def test_length(self):
        cid = _generate_campaign_id("+1555", ["+15551230001"])
        assert len(cid) == 12


# ---------- compute_enhanced_spam_score — new signal coverage ----------


class TestEnhancedScoreNewSignals:
    def test_caller_display_name_scam_label(self):
        """Carrier SCAM LIKELY label adds +0.50."""
        score = compute_enhanced_spam_score(0.0, caller_display_name="SCAM LIKELY")
        assert score == pytest.approx(0.50, abs=0.01)

    def test_caller_display_name_spam_risk(self):
        score = compute_enhanced_spam_score(0.0, caller_display_name="SPAM RISK")
        assert score == pytest.approx(0.50, abs=0.01)

    def test_caller_display_name_normal_name_no_boost(self):
        score = compute_enhanced_spam_score(0.0, caller_display_name="John Smith")
        assert score == pytest.approx(0.0, abs=0.01)

    def test_gateway_domain_known_voip(self):
        """Known VoIP gateway domain adds +0.15."""
        score = compute_enhanced_spam_score(0.0, gateway_domain="sip.twilio.com")
        assert score == pytest.approx(0.15, abs=0.01)

    def test_gateway_domain_exact_match(self):
        score = compute_enhanced_spam_score(0.0, gateway_domain="bandwidth.com")
        assert score == pytest.approx(0.15, abs=0.01)

    def test_gateway_domain_spoofed_no_boost(self):
        """nottwilio.com should NOT match twilio.com."""
        score = compute_enhanced_spam_score(0.0, gateway_domain="nottwilio.com")
        assert score == pytest.approx(0.0, abs=0.01)

    def test_gateway_domain_unknown_no_boost(self):
        score = compute_enhanced_spam_score(0.0, gateway_domain="example.com")
        assert score == pytest.approx(0.0, abs=0.01)

    def test_setup_latency_high(self):
        """VoIP transcoding latency >1500ms adds +0.08."""
        score = compute_enhanced_spam_score(0.0, setup_latency_ms=2000)
        assert score == pytest.approx(0.08, abs=0.01)

    def test_setup_latency_normal_no_boost(self):
        score = compute_enhanced_spam_score(0.0, setup_latency_ms=500)
        assert score == pytest.approx(0.0, abs=0.01)

    def test_unknown_presentation(self):
        score = compute_enhanced_spam_score(0.0, presentation="unknown")
        assert score == pytest.approx(0.05, abs=0.01)

    def test_carrier_risk_factor(self):
        score = compute_enhanced_spam_score(0.0, carrier_risk=1.0)
        assert score == pytest.approx(0.15, abs=0.01)


# ---------- score_time_of_day ----------


class TestScoreTimeOfDay:
    def test_normal_business_hours(self):
        """2:00 PM ET (19:00 UTC) should score 0."""
        call_utc = datetime(2026, 3, 1, 19, 0, tzinfo=UTC)
        assert score_time_of_day("+12125551234", call_utc) == 0.0

    def test_late_night_caller_time(self):
        """3:00 AM ET (08:00 UTC) should score 0.15."""
        call_utc = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
        assert score_time_of_day("+12125551234", call_utc) == 0.15

    def test_evening_caller_time(self):
        """10:00 PM ET (03:00 UTC next day) should score 0.05."""
        call_utc = datetime(2026, 3, 2, 3, 0, tzinfo=UTC)
        assert score_time_of_day("+12125551234", call_utc) == 0.05

    def test_pacific_time(self):
        """2:00 AM PT (10:00 UTC) should score 0.15."""
        call_utc = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        assert score_time_of_day("+14155551234", call_utc) == 0.15

    def test_unknown_area_code_returns_zero(self):
        """Area code not in mapping should return 0."""
        call_utc = datetime(2026, 3, 1, 5, 0, tzinfo=UTC)
        assert score_time_of_day("+19995551234", call_utc) == 0.0

    def test_short_number_returns_zero(self):
        assert score_time_of_day("+1", None) == 0.0

    def test_non_us_number_returns_zero(self):
        assert score_time_of_day("+442012345678", None) == 0.0


# ---------- detect_campaigns — input mutation fix ----------


class TestDetectCampaignsNoMutation:
    def test_does_not_mutate_input(self):
        """detect_campaigns should not add internal keys to caller's dicts."""
        now = datetime.now(UTC)
        reports = [
            {"number": "+15551230001", "timestamp_utc": now.isoformat(),
             "stir_status": "", "presentation": "allowed",
             "duration_sec": 0, "answered": False, "contact_name": ""},
            {"number": "+15551230002", "timestamp_utc": now.isoformat(),
             "stir_status": "", "presentation": "allowed",
             "duration_sec": 0, "answered": False, "contact_name": ""},
        ]
        detect_campaigns(reports, now_utc=now)
        for r in reports:
            assert "_normalized" not in r
            assert "_prefix" not in r


# ---------- detect_campaigns — burst pattern with identical timestamps ----------


class TestBurstPatternInstant:
    def test_same_timestamp_burst(self):
        """3+ reports at the exact same second should trigger burst."""
        now = datetime.now(UTC)
        reports = [
            {"number": f"+1555123000{i}", "timestamp_utc": now.isoformat(),
             "stir_status": "", "presentation": "allowed",
             "duration_sec": 0, "answered": False, "contact_name": ""}
            for i in range(3)
        ]
        campaigns = detect_campaigns(reports, now_utc=now)
        assert len(campaigns) == 1
        assert any("burst_pattern" in s for s in campaigns[0].signals)
