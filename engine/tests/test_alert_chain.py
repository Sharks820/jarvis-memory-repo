"""Tests for jarvis_engine.security.alert_chain."""

from __future__ import annotations

from unittest.mock import MagicMock

from jarvis_engine.security.alert_chain import AlertChain, _DEDUP_WINDOW_S


# ---------------------------------------------------------------
# Alert level channels
# ---------------------------------------------------------------


class TestAlertLevels:
    def test_level_1_background_channel(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(1, "Minor scan detected")
        assert alert["level"] == 1
        assert alert["channel"] == "BACKGROUND"
        assert alert["deduped"] is False

    def test_level_2_routine_channel(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(2, "Suspicious activity")
        assert alert["level"] == 2
        assert alert["channel"] == "ROUTINE"

    def test_level_3_important_channel(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(3, "Repeated attack attempts")
        assert alert["level"] == 3
        assert alert["channel"] == "IMPORTANT"

    def test_level_4_urgent_channel(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(4, "Breach detected", evidence="injection payload")
        assert alert["level"] == 4
        assert alert["channel"] == "URGENT"

    def test_level_5_urgent_channel(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(
            5,
            "Active exploit in progress",
            evidence="payload dump",
            containment_action="FULL_KILL executed",
        )
        assert alert["level"] == 5
        assert alert["channel"] == "URGENT"
        assert alert["evidence"] == "payload dump"
        assert alert["containment_action"] == "FULL_KILL executed"


# ---------------------------------------------------------------
# Dedup window
# ---------------------------------------------------------------


class TestDedup:
    def test_same_ip_level_within_window_deduped(self) -> None:
        chain = AlertChain()
        a1 = chain.send_alert(2, "first", source_ip="10.0.0.1")
        a2 = chain.send_alert(2, "second", source_ip="10.0.0.1")
        assert a1["deduped"] is False
        assert a2["deduped"] is True

    def test_different_ips_not_deduped(self) -> None:
        chain = AlertChain()
        a1 = chain.send_alert(2, "first", source_ip="10.0.0.1")
        a2 = chain.send_alert(2, "second", source_ip="10.0.0.2")
        assert a1["deduped"] is False
        assert a2["deduped"] is False

    def test_different_levels_same_ip_not_deduped(self) -> None:
        chain = AlertChain()
        a1 = chain.send_alert(2, "level 2", source_ip="10.0.0.1")
        a2 = chain.send_alert(3, "level 3", source_ip="10.0.0.1")
        assert a1["deduped"] is False
        assert a2["deduped"] is False

    def test_dedup_expires_after_window(self) -> None:
        chain = AlertChain()
        a1 = chain.send_alert(2, "first", source_ip="10.0.0.1")
        assert a1["deduped"] is False

        # Simulate time passing beyond the dedup window
        cache_key = ("10.0.0.1", 2)
        chain._dedup_cache[cache_key] = chain._dedup_cache[cache_key] - _DEDUP_WINDOW_S - 1

        a2 = chain.send_alert(2, "after window", source_ip="10.0.0.1")
        assert a2["deduped"] is False

    def test_none_source_ip_deduped_together(self) -> None:
        chain = AlertChain()
        a1 = chain.send_alert(1, "no ip alert")
        a2 = chain.send_alert(1, "another no ip alert")
        assert a1["deduped"] is False
        assert a2["deduped"] is True


# ---------------------------------------------------------------
# Alert history
# ---------------------------------------------------------------


class TestAlertHistory:
    def test_history_returns_recent_alerts(self) -> None:
        chain = AlertChain()
        chain.send_alert(1, "alpha", source_ip="10.0.0.1")
        chain.send_alert(2, "beta", source_ip="10.0.0.2")
        chain.send_alert(3, "gamma", source_ip="10.0.0.3")
        history = chain.get_alert_history()
        assert len(history) == 3
        # Newest first
        assert history[0]["summary"] == "gamma"
        assert history[2]["summary"] == "alpha"

    def test_history_respects_limit(self) -> None:
        chain = AlertChain()
        for i in range(10):
            chain.send_alert(1, f"alert {i}", source_ip=f"10.0.0.{i}")
        history = chain.get_alert_history(limit=3)
        assert len(history) == 3
        assert history[0]["summary"] == "alert 9"

    def test_history_empty(self) -> None:
        chain = AlertChain()
        history = chain.get_alert_history()
        assert history == []

    def test_history_includes_deduped_alerts(self) -> None:
        chain = AlertChain()
        chain.send_alert(2, "first", source_ip="10.0.0.1")
        chain.send_alert(2, "deduped", source_ip="10.0.0.1")
        history = chain.get_alert_history()
        assert len(history) == 2
        assert history[0]["deduped"] is True
        assert history[1]["deduped"] is False

    def test_history_contains_expected_fields(self) -> None:
        chain = AlertChain()
        chain.send_alert(
            3,
            "test summary",
            evidence="test evidence",
            containment_action="BLOCK",
            source_ip="192.168.1.1",
        )
        alert = chain.get_alert_history()[0]
        assert "timestamp" in alert
        assert alert["level"] == 3
        assert alert["channel"] == "IMPORTANT"
        assert alert["summary"] == "test summary"
        assert alert["evidence"] == "test evidence"
        assert alert["containment_action"] == "BLOCK"
        assert alert["source_ip"] == "192.168.1.1"
        assert alert["deduped"] is False


# ---------------------------------------------------------------
# Forensic logger integration
# ---------------------------------------------------------------


class TestForensicLoggerIntegration:
    def test_alert_logs_to_forensic_logger(self) -> None:
        mock_logger = MagicMock()
        chain = AlertChain(forensic_logger=mock_logger)
        chain.send_alert(2, "test alert", source_ip="10.0.0.1")
        mock_logger.log_event.assert_called_once()
        event = mock_logger.log_event.call_args[0][0]
        assert event["event_type"] == "alert_dispatched"
        assert event["level"] == 2
        assert event["channel"] == "ROUTINE"
        assert event["source_ip"] == "10.0.0.1"

    def test_deduped_alert_does_not_log(self) -> None:
        mock_logger = MagicMock()
        chain = AlertChain(forensic_logger=mock_logger)
        chain.send_alert(2, "first", source_ip="10.0.0.1")
        mock_logger.reset_mock()
        chain.send_alert(2, "second", source_ip="10.0.0.1")
        mock_logger.log_event.assert_not_called()

    def test_no_logger_no_crash(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(3, "no logger attached")
        assert alert["deduped"] is False


# ---------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------


class TestEdgeCases:
    def test_empty_summary(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(1, "")
        assert alert["summary"] == ""
        assert alert["deduped"] is False

    def test_no_evidence(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(2, "test")
        assert alert["evidence"] is None

    def test_level_clamped_below(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(0, "too low")
        assert alert["level"] == 1
        assert alert["channel"] == "BACKGROUND"

    def test_level_clamped_above(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(99, "too high")
        assert alert["level"] == 5
        assert alert["channel"] == "URGENT"

    def test_no_source_ip(self) -> None:
        chain = AlertChain()
        alert = chain.send_alert(3, "no ip")
        assert alert["source_ip"] is None
