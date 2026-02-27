"""Tests for HoneypotEngine — fake endpoint deployment and attacker tracking."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from jarvis_engine.security.honeypot import HONEYPOT_PATHS, HoneypotEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> HoneypotEngine:
    return HoneypotEngine()


@pytest.fixture()
def mock_logger() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def engine_with_logger(mock_logger: MagicMock) -> HoneypotEngine:
    return HoneypotEngine(forensic_logger=mock_logger)


# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------


class TestPathMatching:
    def test_all_known_paths_match(self, engine: HoneypotEngine) -> None:
        for path in HONEYPOT_PATHS:
            assert engine.is_honeypot_path(path) is True, f"{path} should match"

    def test_trailing_slash_matches(self, engine: HoneypotEngine) -> None:
        assert engine.is_honeypot_path("/admin/") is True
        assert engine.is_honeypot_path("/wp-admin/") is True

    def test_normal_paths_do_not_match(self, engine: HoneypotEngine) -> None:
        assert engine.is_honeypot_path("/") is False
        assert engine.is_honeypot_path("/api/v1/memory") is False
        assert engine.is_honeypot_path("/health") is False
        assert engine.is_honeypot_path("/command") is False

    def test_partial_match_does_not_trigger(self, engine: HoneypotEngine) -> None:
        assert engine.is_honeypot_path("/admin-panel") is False
        assert engine.is_honeypot_path("/api/admin-extra") is False

    def test_case_sensitive(self, engine: HoneypotEngine) -> None:
        # Paths are case-sensitive — /Admin should NOT match /admin
        assert engine.is_honeypot_path("/Admin") is False
        assert engine.is_honeypot_path("/WP-ADMIN") is False


# ---------------------------------------------------------------------------
# Response generation
# ---------------------------------------------------------------------------


class TestResponseGeneration:
    def test_wp_login_returns_login_page(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/wp-login.php")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert "<form" in body
        assert "password" in body.lower()

    def test_env_returns_fake_secrets(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/.env")
        assert status == 200
        assert "DB_PASSWORD" in body
        assert "SECRET_KEY" in body
        assert "FAKE" in body  # ensure it's clearly fake

    def test_config_returns_valid_json(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/config")
        assert status == 200
        parsed = json.loads(body)
        assert "version" in parsed
        assert "database" in parsed

    def test_actuator_returns_links(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/actuator")
        assert status == 200
        parsed = json.loads(body)
        assert "_links" in parsed

    def test_swagger_returns_openapi(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/swagger.json")
        assert status == 200
        parsed = json.loads(body)
        assert "openapi" in parsed
        assert "paths" in parsed

    def test_graphql_returns_error(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/graphql")
        assert status == 200
        parsed = json.loads(body)
        assert "errors" in parsed

    def test_admin_returns_html(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/admin")
        assert status == 200
        assert "Admin" in body or "admin" in body

    def test_phpinfo_returns_php_info(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/phpinfo.php")
        assert status == 200
        assert "PHP Version" in body

    def test_debug_vars_returns_json(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/debug/vars")
        assert status == 200
        parsed = json.loads(body)
        assert "memstats" in parsed

    def test_unknown_path_returns_403(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/totally-unknown")
        assert status == 403
        assert "Forbidden" in body

    def test_response_has_server_header(self, engine: HoneypotEngine) -> None:
        status, headers, body = engine.generate_response("/config")
        assert "Server" in headers
        assert "nginx" in headers["Server"]


# ---------------------------------------------------------------------------
# Hit recording and stats
# ---------------------------------------------------------------------------


class TestHitRecording:
    def test_record_hit_returns_stats(self, engine: HoneypotEngine) -> None:
        result = engine.record_hit("/admin", "10.0.0.1")
        assert result["path"] == "/admin"
        assert result["total_hits"] == 1
        assert result["unique_ips"] == 1

    def test_multiple_hits_same_ip(self, engine: HoneypotEngine) -> None:
        engine.record_hit("/admin", "10.0.0.1")
        engine.record_hit("/admin", "10.0.0.1")
        result = engine.record_hit("/admin", "10.0.0.1")
        assert result["total_hits"] == 3
        assert result["unique_ips"] == 1

    def test_multiple_hits_different_ips(self, engine: HoneypotEngine) -> None:
        engine.record_hit("/admin", "10.0.0.1")
        engine.record_hit("/admin", "10.0.0.2")
        result = engine.record_hit("/admin", "10.0.0.3")
        assert result["total_hits"] == 3
        assert result["unique_ips"] == 3

    def test_hits_across_paths(self, engine: HoneypotEngine) -> None:
        engine.record_hit("/admin", "10.0.0.1")
        engine.record_hit("/.env", "10.0.0.1")
        engine.record_hit("/config", "10.0.0.2")

        stats = engine.get_honeypot_stats()
        assert stats["total_hits"] == 3
        assert stats["unique_ips"] == 2
        assert stats["hits_per_path"]["/admin"] == 1
        assert stats["hits_per_path"]["/.env"] == 1
        assert stats["hits_per_path"]["/config"] == 1

    def test_top_attackers_sorted(self, engine: HoneypotEngine) -> None:
        for _ in range(5):
            engine.record_hit("/admin", "10.0.0.1")
        for _ in range(3):
            engine.record_hit("/config", "10.0.0.2")
        engine.record_hit("/.env", "10.0.0.3")

        stats = engine.get_honeypot_stats()
        attackers = stats["top_attackers"]
        assert len(attackers) == 3
        assert attackers[0]["ip"] == "10.0.0.1"
        assert attackers[0]["hits"] == 5
        assert attackers[1]["ip"] == "10.0.0.2"
        assert attackers[1]["hits"] == 3

    def test_empty_stats(self, engine: HoneypotEngine) -> None:
        stats = engine.get_honeypot_stats()
        assert stats["total_hits"] == 0
        assert stats["unique_ips"] == 0
        assert stats["hits_per_path"] == {}
        assert stats["top_attackers"] == []

    def test_record_hit_with_headers(self, engine: HoneypotEngine) -> None:
        headers = {"User-Agent": "sqlmap/1.0", "Accept": "*/*"}
        result = engine.record_hit("/admin", "10.0.0.1", headers=headers)
        assert result["total_hits"] == 1


# ---------------------------------------------------------------------------
# Forensic logger integration
# ---------------------------------------------------------------------------


class TestForensicLogger:
    def test_logger_called_on_hit(
        self, engine_with_logger: HoneypotEngine, mock_logger: MagicMock
    ) -> None:
        engine_with_logger.record_hit("/admin", "10.0.0.1", {"X-Test": "1"})
        mock_logger.log.assert_called_once()
        call_args = mock_logger.log.call_args
        assert call_args[0][0] == "honeypot_hit"
        assert call_args[0][1]["path"] == "/admin"
        assert call_args[0][1]["source_ip"] == "10.0.0.1"

    def test_logger_exception_does_not_crash(
        self, engine_with_logger: HoneypotEngine, mock_logger: MagicMock
    ) -> None:
        mock_logger.log.side_effect = RuntimeError("logger broken")
        # Should not raise
        result = engine_with_logger.record_hit("/admin", "10.0.0.1")
        assert result["total_hits"] == 1

    def test_no_logger_works_fine(self, engine: HoneypotEngine) -> None:
        # No forensic logger, should not crash
        result = engine.record_hit("/.env", "192.168.1.1")
        assert result["total_hits"] == 1
