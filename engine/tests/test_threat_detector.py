"""Tests for jarvis_engine.security.threat_detector."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from jarvis_engine.security.threat_detector import (
    ThreatAssessment,
    ThreatDetector,
    ThreatSignal,
)


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _clean_ctx(**overrides: object) -> dict:
    """Return a request context with sensible defaults."""
    ctx: dict = {
        "ip": "192.168.1.100",
        "path": "/health",
        "body": "",
        "method": "GET",
        "user_agent": "JarvisAndroid/2.0",
        "nonce": None,
        "headers": {},
        "timestamp": time.time(),
    }
    ctx.update(overrides)
    return ctx


# ---------------------------------------------------------------
# Clean requests
# ---------------------------------------------------------------


class TestCleanRequests:
    def test_clean_get_returns_none(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx())
        assert result.threat_level == "NONE"
        assert result.recommended_action == "ALLOW"
        assert result.signals == []

    def test_clean_post_with_body(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(method="POST", body='{"query": "weather today"}'))
        assert result.threat_level == "NONE"

    def test_normal_user_agent(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(user_agent="Mozilla/5.0 JarvisAndroid/2.0"))
        assert result.threat_level == "NONE"

    def test_unique_nonce_is_fine(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(nonce="abc123"))
        assert result.threat_level == "NONE"


# ---------------------------------------------------------------
# SQL injection detection
# ---------------------------------------------------------------


class TestSQLInjection:
    def test_select_in_body(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="1; SELECT * FROM users"))
        assert any(s.category == "payload_injection" for s in result.signals)

    def test_union_select_in_path(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(path="/api?id=1 UNION SELECT password FROM users"))
        assert any(s.category == "payload_injection" for s in result.signals)

    def test_drop_table(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="'; DROP TABLE users;--"))
        assert any(s.category == "payload_injection" for s in result.signals)

    def test_or_equals_trick(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="' OR '1'='1'"))
        assert any(s.category == "payload_injection" for s in result.signals)

    def test_sleep_function(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="1; SLEEP(5)"))
        assert any(s.category == "payload_injection" for s in result.signals)

    def test_benchmark_function(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="1; BENCHMARK(1000000, SHA1('test'))"))
        assert any(s.category == "payload_injection" for s in result.signals)

    def test_sql_injection_severity_is_high(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="1; SELECT 1"))
        sig = [s for s in result.signals if s.category == "payload_injection"]
        assert sig and sig[0].severity == "HIGH"


# ---------------------------------------------------------------
# Path traversal detection
# ---------------------------------------------------------------


class TestPathTraversal:
    def test_dot_dot_slash(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(path="/files/../../../etc/passwd"))
        assert any(s.category == "path_traversal" for s in result.signals)

    def test_encoded_traversal(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(path="/files/%2e%2e%2fetc/passwd"))
        assert any(s.category == "path_traversal" for s in result.signals)

    def test_etc_passwd(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(path="/read?f=etc/passwd"))
        assert any(s.category == "path_traversal" for s in result.signals)

    def test_windows_system32(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(path="/read?f=windows/system32/config"))
        assert any(s.category == "path_traversal" for s in result.signals)

    def test_normal_path_no_trigger(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(path="/api/v1/memories/search"))
        assert not any(s.category == "path_traversal" for s in result.signals)


# ---------------------------------------------------------------
# Command injection detection
# ---------------------------------------------------------------


class TestCommandInjection:
    def test_semicolon_command(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="hello; rm -rf /"))
        assert any(s.category == "command_injection" for s in result.signals)

    def test_pipe_command(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="data | cat /etc/passwd"))
        assert any(s.category == "command_injection" for s in result.signals)

    def test_backtick_command(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="hello `whoami`"))
        assert any(s.category == "command_injection" for s in result.signals)

    def test_dollar_paren_command(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="hello $(id)"))
        assert any(s.category == "command_injection" for s in result.signals)

    def test_dollar_brace_variable(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="hello ${PATH}"))
        assert any(s.category == "command_injection" for s in result.signals)

    def test_clean_body_no_trigger(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(body="What is the weather today?"))
        assert not any(s.category == "command_injection" for s in result.signals)


# ---------------------------------------------------------------
# Suspicious user agent detection
# ---------------------------------------------------------------


class TestSuspiciousUserAgent:
    def test_empty_user_agent(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(user_agent=""))
        assert any(s.category == "suspicious_user_agent" for s in result.signals)

    def test_sqlmap_user_agent(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(user_agent="sqlmap/1.5.2"))
        assert any(s.category == "suspicious_user_agent" for s in result.signals)

    def test_nikto_user_agent(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(user_agent="Nikto/2.1.6"))
        assert any(s.category == "suspicious_user_agent" for s in result.signals)

    def test_nmap_user_agent(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(user_agent="Nmap Scripting Engine"))
        assert any(s.category == "suspicious_user_agent" for s in result.signals)

    def test_normal_browser_ok(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"))
        assert not any(s.category == "suspicious_user_agent" for s in result.signals)

    def test_empty_ua_severity_medium(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(user_agent=""))
        sig = [s for s in result.signals if s.category == "suspicious_user_agent"]
        assert sig and sig[0].severity == "MEDIUM"


# ---------------------------------------------------------------
# Replay attack detection
# ---------------------------------------------------------------


class TestReplayAttack:
    def test_first_nonce_is_fine(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(nonce="unique-nonce-1"))
        assert not any(s.category == "replay_attack" for s in result.signals)

    def test_duplicate_nonce_detected(self) -> None:
        d = ThreatDetector()
        d.assess(_clean_ctx(nonce="dup-nonce"))
        result = d.assess(_clean_ctx(nonce="dup-nonce"))
        assert any(s.category == "replay_attack" for s in result.signals)

    def test_different_nonces_ok(self) -> None:
        d = ThreatDetector()
        d.assess(_clean_ctx(nonce="nonce-a"))
        result = d.assess(_clean_ctx(nonce="nonce-b"))
        assert not any(s.category == "replay_attack" for s in result.signals)

    def test_no_nonce_skips_rule(self) -> None:
        d = ThreatDetector()
        result = d.assess(_clean_ctx(nonce=None))
        assert not any(s.category == "replay_attack" for s in result.signals)

    def test_replay_severity_is_high(self) -> None:
        d = ThreatDetector()
        d.assess(_clean_ctx(nonce="replay-test"))
        result = d.assess(_clean_ctx(nonce="replay-test"))
        sig = [s for s in result.signals if s.category == "replay_attack"]
        assert sig and sig[0].severity == "HIGH"


# ---------------------------------------------------------------
# Rate anomaly detection
# ---------------------------------------------------------------


class TestRateAnomaly:
    def test_under_threshold_ok(self) -> None:
        d = ThreatDetector()
        for _ in range(30):
            result = d.assess(_clean_ctx())
        assert not any(s.category == "rate_anomaly" for s in result.signals)

    def test_over_threshold_detected(self) -> None:
        d = ThreatDetector()
        for _ in range(61):
            result = d.assess(_clean_ctx())
        assert any(s.category == "rate_anomaly" for s in result.signals)

    def test_different_ips_independent(self) -> None:
        d = ThreatDetector()
        for i in range(61):
            d.assess(_clean_ctx(ip=f"10.0.0.{i % 2}"))
        # Each IP only got ~30 requests
        result = d.assess(_clean_ctx(ip="10.0.0.0"))
        # 10.0.0.0 got 31 requests, well under 60
        assert not any(s.category == "rate_anomaly" for s in result.signals)


# ---------------------------------------------------------------
# Auth brute force (with IPTracker mock)
# ---------------------------------------------------------------


class TestAuthBruteForce:
    def test_no_tracker_skips_rule(self) -> None:
        d = ThreatDetector(ip_tracker=None)
        result = d.assess(_clean_ctx())
        assert not any(s.category == "auth_brute_force" for s in result.signals)

    def test_low_attempts_no_signal(self) -> None:
        tracker = MagicMock()
        tracker.get_threat_report.return_value = {"total_attempts": 2}
        d = ThreatDetector(ip_tracker=tracker)
        result = d.assess(_clean_ctx())
        assert not any(s.category == "auth_brute_force" for s in result.signals)

    def test_5_attempts_medium(self) -> None:
        tracker = MagicMock()
        tracker.get_threat_report.return_value = {"total_attempts": 5}
        tracker.is_blocked.return_value = False
        d = ThreatDetector(ip_tracker=tracker)
        result = d.assess(_clean_ctx())
        sig = [s for s in result.signals if s.category == "auth_brute_force"]
        assert sig and sig[0].severity == "MEDIUM"

    def test_10_attempts_high(self) -> None:
        tracker = MagicMock()
        tracker.get_threat_report.return_value = {"total_attempts": 10}
        tracker.is_blocked.return_value = False
        d = ThreatDetector(ip_tracker=tracker)
        result = d.assess(_clean_ctx())
        sig = [s for s in result.signals if s.category == "auth_brute_force"]
        assert sig and sig[0].severity == "HIGH"

    def test_unknown_ip_no_signal(self) -> None:
        tracker = MagicMock()
        tracker.get_threat_report.return_value = None
        tracker.is_blocked.return_value = False
        d = ThreatDetector(ip_tracker=tracker)
        result = d.assess(_clean_ctx())
        assert not any(s.category == "auth_brute_force" for s in result.signals)


# ---------------------------------------------------------------
# Known bad IP (with IPTracker mock)
# ---------------------------------------------------------------


class TestKnownBadIP:
    def test_blocked_ip_critical(self) -> None:
        tracker = MagicMock()
        tracker.is_blocked.return_value = True
        tracker.get_threat_report.return_value = None
        d = ThreatDetector(ip_tracker=tracker)
        result = d.assess(_clean_ctx())
        sig = [s for s in result.signals if s.category == "known_bad_ip"]
        assert sig and sig[0].severity == "CRITICAL"

    def test_unblocked_ip_no_signal(self) -> None:
        tracker = MagicMock()
        tracker.is_blocked.return_value = False
        tracker.get_threat_report.return_value = None
        d = ThreatDetector(ip_tracker=tracker)
        result = d.assess(_clean_ctx())
        assert not any(s.category == "known_bad_ip" for s in result.signals)


# ---------------------------------------------------------------
# Aggregation logic
# ---------------------------------------------------------------


class TestAggregation:
    def test_no_signals_is_none(self) -> None:
        d = ThreatDetector()
        result = d._aggregate([])
        assert result.threat_level == "NONE"
        assert result.recommended_action == "ALLOW"

    def test_single_low_is_low(self) -> None:
        d = ThreatDetector()
        signals = [ThreatSignal(severity="LOW", category="test", confidence=0.5)]
        result = d._aggregate(signals)
        assert result.threat_level == "LOW"
        assert result.recommended_action == "ALLOW"

    def test_two_lows_is_medium(self) -> None:
        d = ThreatDetector()
        signals = [
            ThreatSignal(severity="LOW", category="a", confidence=0.5),
            ThreatSignal(severity="LOW", category="b", confidence=0.5),
        ]
        result = d._aggregate(signals)
        assert result.threat_level == "MEDIUM"
        assert result.recommended_action == "THROTTLE"

    def test_one_medium_is_medium(self) -> None:
        d = ThreatDetector()
        signals = [ThreatSignal(severity="MEDIUM", category="test", confidence=0.7)]
        result = d._aggregate(signals)
        assert result.threat_level == "MEDIUM"
        assert result.recommended_action == "THROTTLE"

    def test_two_mediums_is_high(self) -> None:
        d = ThreatDetector()
        signals = [
            ThreatSignal(severity="MEDIUM", category="a", confidence=0.7),
            ThreatSignal(severity="MEDIUM", category="b", confidence=0.7),
        ]
        result = d._aggregate(signals)
        assert result.threat_level == "HIGH"
        assert result.recommended_action == "BLOCK"

    def test_one_high_is_high(self) -> None:
        d = ThreatDetector()
        signals = [ThreatSignal(severity="HIGH", category="test", confidence=0.9)]
        result = d._aggregate(signals)
        assert result.threat_level == "HIGH"
        assert result.recommended_action == "BLOCK"

    def test_two_highs_is_critical(self) -> None:
        d = ThreatDetector()
        signals = [
            ThreatSignal(severity="HIGH", category="a", confidence=0.9),
            ThreatSignal(severity="HIGH", category="b", confidence=0.9),
        ]
        result = d._aggregate(signals)
        assert result.threat_level == "CRITICAL"
        assert result.recommended_action == "KILL"

    def test_one_critical_is_critical(self) -> None:
        d = ThreatDetector()
        signals = [ThreatSignal(severity="CRITICAL", category="test", confidence=1.0)]
        result = d._aggregate(signals)
        assert result.threat_level == "CRITICAL"
        assert result.recommended_action == "KILL"

    def test_mixed_signals_escalation(self) -> None:
        """LOW + MEDIUM -> HIGH because 1 medium present, but let's check."""
        d = ThreatDetector()
        signals = [
            ThreatSignal(severity="LOW", category="a", confidence=0.3),
            ThreatSignal(severity="MEDIUM", category="b", confidence=0.7),
        ]
        result = d._aggregate(signals)
        # 1 LOW + 1 MEDIUM: the MEDIUM alone gives MEDIUM level
        assert result.threat_level == "MEDIUM"


# ---------------------------------------------------------------
# Dataclass basics
# ---------------------------------------------------------------


class TestDataclasses:
    def test_threat_signal_frozen(self) -> None:
        sig = ThreatSignal(severity="LOW", category="test", confidence=0.5)
        with pytest.raises(AttributeError):
            sig.severity = "HIGH"  # type: ignore[misc]

    def test_threat_signal_default_evidence(self) -> None:
        sig = ThreatSignal(severity="LOW", category="test", confidence=0.5)
        assert sig.evidence == {}

    def test_threat_assessment_defaults(self) -> None:
        a = ThreatAssessment(threat_level="NONE")
        assert a.signals == []
        assert a.recommended_action == "ALLOW"
