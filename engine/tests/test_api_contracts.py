"""Tests for API contract validation and HTTPS/cert hardening.

Covers:
  - Contract dataclass definitions match server endpoint responses
  - Android compatibility checks pass
  - validate_contract() catches drift
  - get_contract_schema() produces valid schemas
  - TLS cert generation with SAN entries
  - Cert fingerprint endpoint
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis_engine.api_contracts import (
    BootstrapResponse,
    CertFingerprintResponse,
    CommandResponse,
    DashboardResponse,
    ErrorResponse,
    HealthResponse,
    SettingsResponse,
    check_android_compatibility,
    get_android_expected_fields,
    get_contract_schema,
    validate_contract,
)
from jarvis_engine.mobile_api import (
    _build_san_string,
    _detect_lan_ips,
    _ensure_tls_cert,
)
from jarvis_engine.mobile_routes._helpers import _get_cert_fingerprint


# ---------------------------------------------------------------------------
# Contract validation tests
# ---------------------------------------------------------------------------


class TestContractDefinitions:
    """Verify that contract dataclasses match server response shapes."""

    def test_health_response_fields(self) -> None:
        """HealthResponse must have ok, status, intelligence."""
        r = HealthResponse(ok=True, status="healthy", intelligence={"score": 0.85})
        assert r.ok is True
        assert r.status == "healthy"
        assert r.intelligence["score"] == 0.85

    def test_health_response_defaults(self) -> None:
        """HealthResponse defaults should match empty construction."""
        r = HealthResponse()
        assert r.ok is True
        assert r.status == ""
        assert r.intelligence is None

    def test_bootstrap_response_fields(self) -> None:
        """BootstrapResponse must have ok, session, message."""
        session = {
            "base_url": "https://192.168.1.10:8787",
            "token": "tok123",
            "signing_key": "key456",
            "device_id": "android-app",
            "trusted_device": True,
        }
        r = BootstrapResponse(ok=True, session=session, message="")
        assert r.ok is True
        assert r.session["token"] == "tok123"
        assert r.session["device_id"] == "android-app"

    def test_command_response_fields(self) -> None:
        """CommandResponse must include ok, intent, stdout_tail."""
        r = CommandResponse(ok=True, intent="general", stdout_tail=["response=Hello"])
        assert r.ok is True
        assert r.intent == "general"
        assert len(r.stdout_tail) == 1

    def test_settings_response_fields(self) -> None:
        """SettingsResponse must include ok, settings."""
        r = SettingsResponse(ok=True, settings={"runtime_control": {}, "gaming_mode": {}})
        assert r.ok is True
        assert "runtime_control" in r.settings
        assert "gaming_mode" in r.settings

    def test_dashboard_response_fields(self) -> None:
        """DashboardResponse must include ok, dashboard."""
        r = DashboardResponse(ok=True, dashboard={"jarvis": {}, "ranking": []})
        assert r.ok is True
        assert "jarvis" in r.dashboard

    def test_cert_fingerprint_response_fields(self) -> None:
        """CertFingerprintResponse must include ok, fingerprint, algorithm."""
        r = CertFingerprintResponse(ok=True, fingerprint="AA:BB:CC", algorithm="sha256")
        assert r.ok is True
        assert r.fingerprint == "AA:BB:CC"
        assert r.algorithm == "sha256"

    def test_error_response_fields(self) -> None:
        """ErrorResponse must include ok=False and error string."""
        r = ErrorResponse(ok=False, error="Something went wrong")
        assert r.ok is False
        assert r.error == "Something went wrong"


class TestValidateContract:
    """Test the validate_contract() function."""

    def test_valid_health_response(self) -> None:
        """A correct /health response should produce no errors."""
        response = {
            "ok": True,
            "status": "healthy",
            "intelligence": {"score": 0.85, "regression": False, "last_test": ""},
        }
        errors = validate_contract("GET /health", response)
        assert errors == []

    def test_valid_bootstrap_response(self) -> None:
        """A correct /bootstrap response should produce no errors."""
        response = {
            "ok": True,
            "session": {
                "base_url": "https://192.168.1.10:8787",
                "token": "tok",
                "signing_key": "key",
                "device_id": "dev",
                "trusted_device": True,
            },
        }
        errors = validate_contract("POST /bootstrap", response)
        assert errors == []

    def test_valid_command_response(self) -> None:
        """A correct /command response should produce no errors."""
        response = {
            "ok": True,
            "lifecycle_state": "completed",
            "intent": "general",
            "response": "hello",
            "response_chunks": ["hello"],
            "response_truncated": False,
            "stdout_tail": ["line1"],
            "stdout_truncated": False,
            "command_exit_code": 0,
            "status_code": "0",
            "reason": "",
            "stderr_tail": [],
            "correlation_id": "abc123",
            "diagnostic_id": "abc123",
            "error_code": "",
            "category": "",
            "retryable": False,
            "user_hint": "",
            "error": "",
        }
        errors = validate_contract("POST /command", response)
        assert errors == []

    def test_valid_settings_response(self) -> None:
        """A correct /settings response should produce no errors."""
        response = {
            "ok": True,
            "settings": {"runtime_control": {}, "gaming_mode": {}},
        }
        errors = validate_contract("GET /settings", response)
        assert errors == []

    def test_valid_cert_fingerprint_response(self) -> None:
        """A correct /cert-fingerprint response should produce no errors."""
        response = {
            "ok": True,
            "fingerprint": "AA:BB:CC:DD",
            "algorithm": "sha256",
        }
        errors = validate_contract("GET /cert-fingerprint", response)
        assert errors == []

    def test_unknown_endpoint(self) -> None:
        """Unknown endpoint name should return an error."""
        errors = validate_contract("GET /nonexistent", {})
        assert len(errors) == 1
        assert "Unknown endpoint" in errors[0]

    def test_type_mismatch_detected(self) -> None:
        """Type mismatches should be detected."""
        response = {
            "ok": "yes",  # Should be bool, not string
            "status": "healthy",
        }
        errors = validate_contract("GET /health", response)
        assert any("ok" in e and "bool" in e for e in errors)


class TestGetContractSchema:
    """Test JSON schema generation."""

    def test_schema_for_single_endpoint(self) -> None:
        """get_contract_schema with an endpoint returns a valid schema."""
        schema = get_contract_schema("GET /health")
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "ok" in schema["properties"]
        assert "status" in schema["properties"]

    def test_schema_for_all_endpoints(self) -> None:
        """get_contract_schema without args returns schemas for all endpoints."""
        schemas = get_contract_schema()
        assert isinstance(schemas, dict)
        assert "GET /health" in schemas
        assert "POST /bootstrap" in schemas
        assert "POST /command" in schemas
        assert "GET /settings" in schemas
        assert "GET /dashboard" in schemas
        assert "GET /cert-fingerprint" in schemas

    def test_schema_unknown_endpoint(self) -> None:
        """Unknown endpoint should return error dict."""
        schema = get_contract_schema("GET /nope")
        assert "error" in schema


class TestAndroidCompatibility:
    """Test that Android expected fields are present in server contracts."""

    def test_all_android_fields_present(self) -> None:
        """Every field Android expects must exist in the server contract."""
        errors = check_android_compatibility()
        assert errors == [], f"Android compatibility errors: {errors}"

    def test_android_expected_fields_coverage(self) -> None:
        """Verify the android expected fields map covers key endpoints."""
        fields = get_android_expected_fields()
        assert "GET /health" in fields
        assert "POST /bootstrap" in fields
        assert "POST /command" in fields
        assert "GET /settings" in fields
        assert "GET /dashboard" in fields
        assert "GET /cert-fingerprint" in fields

    def test_health_expects_status_not_ok(self) -> None:
        """Android's HealthResponse expects 'status' field (not just 'ok')."""
        fields = get_android_expected_fields()
        health_fields = fields["GET /health"]
        assert "status" in health_fields

    def test_bootstrap_session_fields(self) -> None:
        """Android expects all session credentials in bootstrap."""
        fields = get_android_expected_fields()
        session_fields = fields["POST /bootstrap.session"]
        assert "base_url" in session_fields
        assert "token" in session_fields
        assert "signing_key" in session_fields
        assert "device_id" in session_fields
        assert "trusted_device" in session_fields


class TestServerResponseMatchesContract:
    """Validate actual server responses against contracts.

    Uses the mobile_server fixture from conftest.py to make real HTTP requests
    and validate the responses match the contract definitions.
    """

    def test_health_matches_contract(self, mobile_server) -> None:
        """GET /health response must match HealthResponse contract."""
        from conftest import http_request
        code, body = http_request("GET", f"{mobile_server.base_url}/health")
        assert code == 200
        response = json.loads(body.decode("utf-8"))
        errors = validate_contract("GET /health", response)
        assert errors == [], f"Health contract violations: {errors}"
        # Also check Android-critical fields
        assert "status" in response, "Android expects 'status' field in health"
        assert response["status"] == "healthy"

    def test_settings_matches_contract(self, mobile_server) -> None:
        """GET /settings response must match SettingsResponse contract."""
        from conftest import http_request, signed_headers
        headers = signed_headers(b"", mobile_server.auth_token, mobile_server.signing_key)
        code, body = http_request("GET", f"{mobile_server.base_url}/settings", headers=headers)
        assert code == 200
        response = json.loads(body.decode("utf-8"))
        errors = validate_contract("GET /settings", response)
        assert errors == [], f"Settings contract violations: {errors}"
        assert "settings" in response

    def test_command_matches_contract(self, mobile_server) -> None:
        """POST /command response must match CommandResponse contract.

        Mocks cmd_voice_run to avoid full engine startup and timeout.
        """
        from conftest import http_request, signed_headers

        def _fake_voice_run(**kwargs):
            # Simulate the output format from cmd_voice_run
            import sys
            sys.stdout.write("intent=general\nreason=ok\nstatus_code=0\nresponse=Test response\n")
            return 0

        payload = json.dumps({"text": "what time is it"}).encode("utf-8")
        headers = signed_headers(payload, mobile_server.auth_token, mobile_server.signing_key)

        with patch("jarvis_engine.main.cmd_voice_run", side_effect=_fake_voice_run):
            code, body = http_request("POST", f"{mobile_server.base_url}/command", body=payload, headers=headers)
        assert code == 200
        response = json.loads(body.decode("utf-8"))
        errors = validate_contract("POST /command", response)
        assert errors == [], f"Command contract violations: {errors}"
        # Android expects these fields
        assert "ok" in response
        assert "intent" in response
        assert "stdout_tail" in response


# ---------------------------------------------------------------------------
# TLS / HTTPS hardening tests
# ---------------------------------------------------------------------------


class TestSanGeneration:
    """Test SAN string generation for TLS certificates."""

    def test_build_san_includes_localhost(self) -> None:
        """SAN string must always include DNS:localhost and IP:127.0.0.1."""
        san = _build_san_string()
        assert "DNS:localhost" in san
        assert "IP:127.0.0.1" in san

    def test_build_san_includes_extra_ips(self) -> None:
        """Extra IPs should be included in the SAN string."""
        san = _build_san_string(extra_ips=["10.0.0.50"])
        assert "IP:10.0.0.50" in san
        assert "DNS:localhost" in san
        assert "IP:127.0.0.1" in san

    def test_build_san_no_duplicate_loopback(self) -> None:
        """127.0.0.1 should not appear twice even if passed as extra."""
        san = _build_san_string(extra_ips=["127.0.0.1"])
        parts = san.split(",")
        loopback_count = sum(1 for p in parts if p.strip() == "IP:127.0.0.1")
        assert loopback_count == 1

    def test_detect_lan_ips_includes_loopback(self) -> None:
        """_detect_lan_ips should always include 127.0.0.1."""
        ips = _detect_lan_ips()
        assert "127.0.0.1" in ips


class TestCertGeneration:
    """Test TLS certificate generation with SAN entries."""

    def _openssl_available(self) -> bool:
        try:
            result = subprocess.run(
                ["openssl", "version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, OSError):
            return False

    def test_ensure_tls_cert_generates_cert(self, tmp_path: Path) -> None:
        """_ensure_tls_cert should generate cert and key files."""
        if not self._openssl_available():
            pytest.skip("openssl not available")
        security_dir = tmp_path / "security"
        cert_path, key_path = _ensure_tls_cert(security_dir)
        assert cert_path is not None
        assert key_path is not None
        assert Path(cert_path).exists()
        assert Path(key_path).exists()

    def test_ensure_tls_cert_has_san(self, tmp_path: Path) -> None:
        """Generated cert should contain SAN entries."""
        if not self._openssl_available():
            pytest.skip("openssl not available")
        security_dir = tmp_path / "security"
        cert_path, _ = _ensure_tls_cert(security_dir, extra_ips=["10.0.0.99"])
        assert cert_path is not None
        # Inspect the cert for SAN
        result = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-text"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        cert_text = result.stdout
        assert "Subject Alternative Name" in cert_text
        assert "DNS:localhost" in cert_text
        assert "IP Address:127.0.0.1" in cert_text or "IP:127.0.0.1" in cert_text

    def test_ensure_tls_cert_reuses_existing(self, tmp_path: Path) -> None:
        """If cert already exists, _ensure_tls_cert should reuse it."""
        security_dir = tmp_path / "security"
        security_dir.mkdir(parents=True)
        cert_file = security_dir / "tls_cert.pem"
        key_file = security_dir / "tls_key.pem"
        cert_file.write_text("EXISTING_CERT", encoding="utf-8")
        key_file.write_text("EXISTING_KEY", encoding="utf-8")
        cert_path, key_path = _ensure_tls_cert(security_dir)
        assert cert_path == str(cert_file)
        assert key_path == str(key_file)
        # Content should be unchanged (not regenerated)
        assert cert_file.read_text(encoding="utf-8") == "EXISTING_CERT"


class TestCertFingerprint:
    """Test cert fingerprint computation."""

    def _openssl_available(self) -> bool:
        try:
            result = subprocess.run(
                ["openssl", "version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, OSError):
            return False

    def test_fingerprint_from_real_cert(self, tmp_path: Path) -> None:
        """Should compute SHA-256 fingerprint from a real cert."""
        if not self._openssl_available():
            pytest.skip("openssl not available")
        security_dir = tmp_path / "security"
        cert_path, _ = _ensure_tls_cert(security_dir)
        assert cert_path is not None
        fingerprint = _get_cert_fingerprint(cert_path)
        assert fingerprint is not None
        # Should be colon-separated hex
        assert ":" in fingerprint
        parts = fingerprint.split(":")
        assert len(parts) == 32  # SHA-256 = 32 bytes = 64 hex chars = 32 pairs
        for part in parts:
            assert len(part) == 2
            int(part, 16)  # Should be valid hex

    def test_fingerprint_missing_cert(self, tmp_path: Path) -> None:
        """Should return None for a non-existent cert file."""
        fingerprint = _get_cert_fingerprint(str(tmp_path / "nonexistent.pem"))
        assert fingerprint is None


class TestCertFingerprintEndpoint:
    """Test the /cert-fingerprint HTTP endpoint."""

    def test_cert_fingerprint_no_cert(self, mobile_server) -> None:
        """GET /cert-fingerprint returns 404 when no TLS cert exists."""
        from conftest import http_request
        code, body = http_request("GET", f"{mobile_server.base_url}/cert-fingerprint")
        assert code == 404
        response = json.loads(body.decode("utf-8"))
        assert response["ok"] is False

    def test_cert_fingerprint_with_cert(self, mobile_server) -> None:
        """GET /cert-fingerprint returns fingerprint when cert exists."""
        from conftest import http_request

        # Check if openssl is available
        try:
            result = subprocess.run(["openssl", "version"], capture_output=True, timeout=5)
            if result.returncode != 0:
                pytest.skip("openssl not available")
        except (FileNotFoundError, OSError):
            pytest.skip("openssl not available")

        # Generate a cert in the server's security dir
        security_dir = mobile_server.root / ".planning" / "security"
        cert_path, key_path = _ensure_tls_cert(security_dir)
        if cert_path is None:
            pytest.skip("cert generation failed")

        code, body = http_request("GET", f"{mobile_server.base_url}/cert-fingerprint")
        assert code == 200
        response = json.loads(body.decode("utf-8"))
        assert response["ok"] is True
        assert "fingerprint" in response
        assert response["algorithm"] == "sha256"
        assert ":" in response["fingerprint"]
        errors = validate_contract("GET /cert-fingerprint", response)
        assert errors == []


# ---------------------------------------------------------------------------
# Contract drift detection tests
# ---------------------------------------------------------------------------


class TestContractDriftDetection:
    """Detect drift between server responses and Android expectations."""

    def test_health_has_status_not_just_ok(self) -> None:
        """Server /health must return 'status' field, not just 'ok'.

        Android's HealthResponse data class expects `val status: String`.
        Previous bugs returned only 'ok' without 'status'.
        """
        schema = get_contract_schema("GET /health")
        assert "status" in schema["properties"]

    def test_bootstrap_has_session_nesting(self) -> None:
        """Bootstrap response must nest credentials under 'session'.

        Android expects: response.session.token, not response.token.
        """
        schema = get_contract_schema("POST /bootstrap")
        assert "session" in schema["properties"]

    def test_bootstrap_uses_device_id_not_device_name(self) -> None:
        """Bootstrap session must use 'device_id', not 'device_name'.

        Android's @SerializedName("device_id") maps to deviceId.
        """
        from dataclasses import fields as dc_fields
        from jarvis_engine.api_contracts import BootstrapSession
        field_names = {f.name for f in dc_fields(BootstrapSession)}
        assert "device_id" in field_names
        assert "device_name" not in field_names

    def test_command_response_has_stdout_tail(self) -> None:
        """Command response must include stdout_tail as a list.

        Android's CommandResponse: @SerializedName("stdout_tail") val stdoutTail: List<String>
        """
        schema = get_contract_schema("POST /command")
        assert "stdout_tail" in schema["properties"]
        assert schema["properties"]["stdout_tail"]["type"] == "array"

    def test_command_response_has_lifecycle_and_diagnostics(self) -> None:
        """Command response schema includes lifecycle and diagnostic metadata."""
        schema = get_contract_schema("POST /command")
        assert "lifecycle_state" in schema["properties"]
        assert "correlation_id" in schema["properties"]
        assert "diagnostic_id" in schema["properties"]
        assert "error_code" in schema["properties"]
        assert "retryable" in schema["properties"]

    def test_settings_has_nested_settings_object(self) -> None:
        """Settings response wraps data under 'settings' key.

        Android: data class SettingsResponse(val settings: SettingsData?)
        """
        schema = get_contract_schema("GET /settings")
        assert "settings" in schema["properties"]

    def test_dashboard_has_nested_dashboard_object(self) -> None:
        """Dashboard response wraps data under 'dashboard' key.

        Android: data class DashboardResponse(val dashboard: DashboardData?)
        """
        schema = get_contract_schema("GET /dashboard")
        assert "dashboard" in schema["properties"]

    def test_spam_candidates_has_ok_and_candidates(self) -> None:
        """SpamCandidatesResponse must have ok and candidates list."""
        schema = get_contract_schema("GET /spam/candidates")
        assert "ok" in schema["properties"]
        assert "candidates" in schema["properties"]

    def test_cert_fingerprint_has_required_fields(self) -> None:
        """CertFingerprintResponse needs ok, fingerprint, algorithm."""
        schema = get_contract_schema("GET /cert-fingerprint")
        assert "ok" in schema["properties"]
        assert "fingerprint" in schema["properties"]
        assert "algorithm" in schema["properties"]
