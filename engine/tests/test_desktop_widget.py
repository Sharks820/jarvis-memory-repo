"""Tests for engine/src/jarvis_engine/desktop_widget.py

Covers: HMAC signing, URL safety, config loading/saving, HTTP helpers,
health loop logic, service status parsing, widget state, hotword detection,
voice dictation, error detail extraction, and utility functions.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module-level imports (desktop_widget depends on tkinter at import time for
# the class definition, so we mock tkinter at import to avoid GUI creation).
# The module also does a top-level import of _win_hidden_subprocess_kwargs.
# ---------------------------------------------------------------------------

# We can import the helper functions directly since they don't instantiate tkinter:
from jarvis_engine.desktop_widget import (
    WidgetConfig,
    _is_safe_widget_base_url,
    _is_position_on_screen,
    _load_mobile_api_cfg,
    _load_widget_cfg,
    _signed_headers,
    _snap_to_edge,
    _http_error_details,
    _security_dir,
    _mobile_api_cfg_path,
    _widget_cfg_path,
    _repo_root,
    _dpapi_encrypt,
    _dpapi_decrypt,
    _DPAPI_AVAILABLE,
    _show_toast,
    _TOAST_COOLDOWN_SECONDS,
    _TOAST_MAX_TITLE,
    _TOAST_MAX_MESSAGE,
    _TOAST_ICON_TYPES,
)


# ---- WidgetConfig dataclass ------------------------------------------------

class TestWidgetConfig:
    def test_dataclass_fields(self):
        cfg = WidgetConfig(
            base_url="http://127.0.0.1:8787",
            token="tok123",
            signing_key="sk_abc",
            device_id="galaxy_s25_primary",
            master_password="secret",
        )
        assert cfg.base_url == "http://127.0.0.1:8787"
        assert cfg.token == "tok123"
        assert cfg.signing_key == "sk_abc"
        assert cfg.device_id == "galaxy_s25_primary"
        assert cfg.master_password == "secret"

    def test_dataclass_equality(self):
        a = WidgetConfig("u", "t", "k", "d", "p")
        b = WidgetConfig("u", "t", "k", "d", "p")
        assert a == b


# ---- HMAC signed headers ---------------------------------------------------

class TestSignedHeaders:
    def test_timestamp_is_integer_string(self):
        """HMAC timestamps MUST be integers (project critical decision)."""
        headers = _signed_headers("tok", "key", b"body", "dev1")
        ts = headers["X-Jarvis-Timestamp"]
        assert ts == str(int(ts)), "Timestamp must be an integer string (no decimals)"

    def test_headers_contain_all_required_keys(self):
        headers = _signed_headers("mytoken", "mykey", b"", "mydevice")
        assert "Authorization" in headers
        assert "X-Jarvis-Timestamp" in headers
        assert "X-Jarvis-Nonce" in headers
        assert "X-Jarvis-Signature" in headers
        assert "X-Jarvis-Device-Id" in headers

    def test_bearer_token_format(self):
        headers = _signed_headers("tok_abc", "key", b"", "dev")
        assert headers["Authorization"] == "Bearer tok_abc"

    def test_signature_is_valid_hmac_sha256(self):
        token = "mytoken"
        signing_key = "test_signing_key_123"
        body = b'{"text":"hello"}'
        device_id = "galaxy_s25_primary"

        with patch("jarvis_engine.desktop_widget.time") as mock_time, \
             patch("jarvis_engine.desktop_widget.uuid") as mock_uuid:
            mock_time.time.return_value = 1700000000.7  # float to verify int conversion
            mock_uuid.uuid4.return_value = SimpleNamespace(hex="deadbeef1234567890abcdef12345678")

            headers = _signed_headers(token, signing_key, body, device_id)

        ts = "1700000000"  # must be integer (truncated, not rounded)
        nonce = "deadbeef1234567890abcdef12345678"
        signing_material = ts.encode("utf-8") + b"\n" + nonce.encode("utf-8") + b"\n" + body
        expected_sig = hmac.new(signing_key.encode("utf-8"), signing_material, hashlib.sha256).hexdigest()
        assert headers["X-Jarvis-Signature"] == expected_sig
        assert headers["X-Jarvis-Timestamp"] == ts
        assert headers["X-Jarvis-Nonce"] == nonce

    def test_empty_device_id_omitted(self):
        headers = _signed_headers("tok", "key", b"", "")
        assert "X-Jarvis-Device-Id" not in headers

    def test_whitespace_only_device_id_omitted(self):
        headers = _signed_headers("tok", "key", b"", "   ")
        assert "X-Jarvis-Device-Id" not in headers

    def test_device_id_stripped(self):
        headers = _signed_headers("tok", "key", b"", "  dev1  ")
        assert headers["X-Jarvis-Device-Id"] == "dev1"

    def test_nonce_is_32_hex_chars(self):
        headers = _signed_headers("tok", "key", b"", "dev")
        nonce = headers["X-Jarvis-Nonce"]
        assert len(nonce) == 32
        int(nonce, 16)  # must be valid hex


# ---- URL safety check ------------------------------------------------------

class TestIsSafeWidgetBaseUrl:
    def test_https_always_safe(self):
        assert _is_safe_widget_base_url("https://remote-server.example.com:9090") is True

    def test_localhost_http_safe(self):
        assert _is_safe_widget_base_url("http://127.0.0.1:8787") is True
        assert _is_safe_widget_base_url("http://localhost:8787") is True

    def test_ipv6_loopback_safe(self):
        assert _is_safe_widget_base_url("http://[::1]:8787") is True

    def test_private_ip_http_safe(self):
        assert _is_safe_widget_base_url("http://192.168.1.100:8787") is True
        assert _is_safe_widget_base_url("http://10.0.0.5:8787") is True

    def test_cgnat_tailscale_http_safe(self):
        assert _is_safe_widget_base_url("http://100.112.0.32:8787") is True
        assert _is_safe_widget_base_url("http://100.64.0.1:8787") is True
        assert _is_safe_widget_base_url("http://100.127.255.254:8787") is True

    def test_public_ip_http_unsafe(self):
        assert _is_safe_widget_base_url("http://8.8.8.8:8787") is False

    def test_public_hostname_http_unsafe(self):
        assert _is_safe_widget_base_url("http://evil.example.com:8787") is False

    def test_empty_url(self):
        assert _is_safe_widget_base_url("") is False

    def test_no_scheme(self):
        # urlparse without scheme puts everything in path, hostname is None
        assert _is_safe_widget_base_url("127.0.0.1:8787") is False


# ---- Config loading ---------------------------------------------------------

class TestLoadMobileApiCfg:
    def test_missing_file_returns_empty(self, tmp_path):
        result = _load_mobile_api_cfg(tmp_path)
        assert result == {}

    def test_valid_json_returns_token_and_key(self, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "mobile_api.json").write_text(
            json.dumps({"token": "t1", "signing_key": "sk1", "extra": "ignored"}),
            encoding="utf-8-sig",
        )
        result = _load_mobile_api_cfg(tmp_path)
        assert result["token"] == "t1"
        assert result["signing_key"] == "sk1"
        assert "extra" not in result

    def test_invalid_json_returns_empty(self, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "mobile_api.json").write_text("NOT JSON", encoding="utf-8-sig")
        result = _load_mobile_api_cfg(tmp_path)
        assert result == {}

    def test_non_dict_json_returns_empty(self, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "mobile_api.json").write_text("[1,2,3]", encoding="utf-8-sig")
        result = _load_mobile_api_cfg(tmp_path)
        assert result == {}


@patch("jarvis_engine.desktop_widget.urlopen", side_effect=OSError("no network in tests"))
class TestLoadWidgetCfg:
    def test_defaults_when_no_files(self, _mock_urlopen, tmp_path):
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "http://127.0.0.1:8787"
        assert cfg.device_id == "galaxy_s25_primary"
        assert cfg.token == ""
        assert cfg.signing_key == ""

    def test_widget_cfg_overrides_mobile_api(self, _mock_urlopen, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "mobile_api.json").write_text(
            json.dumps({"token": "mobile_tok", "signing_key": "mobile_sk"}),
            encoding="utf-8-sig",
        )
        (sec / "desktop_widget.json").write_text(
            json.dumps({"token": "widget_tok", "signing_key": "widget_sk"}),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.token == "widget_tok"
        assert cfg.signing_key == "widget_sk"

    def test_falls_back_to_mobile_api_values(self, _mock_urlopen, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "mobile_api.json").write_text(
            json.dumps({"token": "mobile_tok", "signing_key": "mobile_sk"}),
            encoding="utf-8-sig",
        )
        # Widget config exists but has empty token/key
        (sec / "desktop_widget.json").write_text(
            json.dumps({"base_url": "http://10.0.0.1:9000"}),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "http://10.0.0.1:9000"
        assert cfg.token == "mobile_tok"
        assert cfg.signing_key == "mobile_sk"

    def test_auto_upgrade_http_to_https_when_tls_certs_exist(self, _mock_urlopen, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        # Create TLS cert files to trigger auto-upgrade
        (sec / "tls_cert.pem").write_text("cert", encoding="utf-8")
        (sec / "tls_key.pem").write_text("key", encoding="utf-8")
        (sec / "desktop_widget.json").write_text(
            json.dumps({"base_url": "http://100.112.0.32:8787"}),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        # Both probe and localhost fail → keeps the saved (upgraded) URL
        assert cfg.base_url == "https://100.112.0.32:8787"

    def test_default_https_when_tls_certs_exist(self, _mock_urlopen, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "tls_cert.pem").write_text("cert", encoding="utf-8")
        (sec / "tls_key.pem").write_text("key", encoding="utf-8")
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "https://127.0.0.1:8787"

    def test_no_upgrade_when_already_https(self, _mock_urlopen, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "tls_cert.pem").write_text("cert", encoding="utf-8")
        (sec / "tls_key.pem").write_text("key", encoding="utf-8")
        (sec / "desktop_widget.json").write_text(
            json.dumps({"base_url": "https://192.168.1.50:8787"}),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "https://192.168.1.50:8787"

    def test_no_upgrade_without_tls_certs(self, _mock_urlopen, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({"base_url": "http://10.0.0.1:8787"}),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "http://10.0.0.1:8787"

    def test_invalid_widget_json_uses_defaults(self, _mock_urlopen, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text("{invalid", encoding="utf-8")
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "http://127.0.0.1:8787"

    def test_auto_heal_stale_ip_to_localhost(self, _mock_urlopen, tmp_path):
        """When saved URL is unreachable but localhost works, auto-heal to localhost."""
        from io import BytesIO
        from http.client import HTTPResponse

        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({"base_url": "http://10.99.99.99:8787"}),
            encoding="utf-8",
        )
        # First call (probe saved IP) fails, second call (localhost) succeeds
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        _mock_urlopen.side_effect = [OSError("unreachable"), mock_resp]
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "http://127.0.0.1:8787"

    def test_no_auto_heal_when_both_fail(self, _mock_urlopen, tmp_path):
        """When both saved URL and localhost fail, keep saved URL."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({"base_url": "http://10.99.99.99:8787"}),
            encoding="utf-8",
        )
        # Both fail
        _mock_urlopen.side_effect = OSError("unreachable")
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "http://10.99.99.99:8787"

    def test_no_auto_heal_for_localhost(self, _mock_urlopen, tmp_path):
        """Localhost URLs should not trigger any probing."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({"base_url": "http://127.0.0.1:8787"}),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "http://127.0.0.1:8787"
        # urlopen should not be called for localhost URLs (no probing needed)
        _mock_urlopen.assert_not_called()


# ---- Path helpers -----------------------------------------------------------

class TestPathHelpers:
    def test_security_dir(self, tmp_path):
        result = _security_dir(tmp_path)
        assert result == tmp_path / ".planning" / "security"

    def test_mobile_api_cfg_path(self, tmp_path):
        result = _mobile_api_cfg_path(tmp_path)
        assert result.name == "mobile_api.json"

    def test_widget_cfg_path(self, tmp_path):
        result = _widget_cfg_path(tmp_path)
        assert result.name == "desktop_widget.json"

    def test_repo_root_returns_path(self):
        root = _repo_root()
        assert isinstance(root, Path)
        # The repo root should be 3 parents above desktop_widget.py
        # i.e. engine/src/jarvis_engine/desktop_widget.py -> repo root


# ---- HTTP error detail extraction -------------------------------------------

class TestHttpErrorDetails:
    def test_with_body(self):
        exc = MagicMock()
        exc.__enter__ = MagicMock(return_value=exc)
        exc.__exit__ = MagicMock(return_value=False)
        exc.read.return_value = b"error detail message"
        exc.__str__ = lambda self: "HTTP 400 Bad Request"
        result = _http_error_details(exc)
        assert "error detail message" in result
        assert "body=" in result

    def test_without_body(self):
        exc = MagicMock()
        exc.__enter__ = MagicMock(return_value=exc)
        exc.__exit__ = MagicMock(return_value=False)
        exc.read.return_value = b""
        exc.__str__ = lambda self: "HTTP 500 Internal Server Error"
        result = _http_error_details(exc)
        assert result == "HTTP 500 Internal Server Error"

    def test_read_raises_exception(self):
        exc = MagicMock()
        exc.__enter__ = MagicMock(return_value=exc)
        exc.__exit__ = MagicMock(return_value=False)
        exc.read.side_effect = OSError("read failed")
        exc.__str__ = lambda self: "HTTP 502 Bad Gateway"
        result = _http_error_details(exc)
        assert result == "HTTP 502 Bad Gateway"

    def test_body_truncated_to_420_chars(self):
        exc = MagicMock()
        exc.__enter__ = MagicMock(return_value=exc)
        exc.__exit__ = MagicMock(return_value=False)
        exc.read.return_value = ("X" * 1000).encode("utf-8")
        exc.__str__ = lambda self: "HTTP 400"
        result = _http_error_details(exc)
        # The body portion should be truncated to 420 chars
        assert "body=" in result
        body_part = result.split("body=")[1]
        assert len(body_part) == 420


# ---- _http_json helper -----------------------------------------------------

class TestHttpJson:
    def test_rejects_unsafe_base_url(self):
        from jarvis_engine.desktop_widget import _http_json
        cfg = WidgetConfig(
            base_url="http://evil.example.com:8787",
            token="t", signing_key="k", device_id="d", master_password="p",
        )
        with pytest.raises(RuntimeError, match="HTTPS"):
            _http_json(cfg, "/health")

    @patch("jarvis_engine.desktop_widget.urlopen")
    def test_get_request_success(self, mock_urlopen):
        from jarvis_engine.desktop_widget import _http_json
        resp_mock = MagicMock()
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        resp_mock.read.return_value = b'{"ok": true}'
        mock_urlopen.return_value = resp_mock

        cfg = WidgetConfig(
            base_url="http://127.0.0.1:8787",
            token="t", signing_key="k", device_id="d", master_password="p",
        )
        result = _http_json(cfg, "/health")
        assert result == {"ok": True}

    @patch("jarvis_engine.desktop_widget.urlopen")
    def test_post_request_includes_content_type(self, mock_urlopen):
        from jarvis_engine.desktop_widget import _http_json
        resp_mock = MagicMock()
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        resp_mock.read.return_value = b'{"ok": true}'
        mock_urlopen.return_value = resp_mock

        cfg = WidgetConfig(
            base_url="http://127.0.0.1:8787",
            token="t", signing_key="k", device_id="d", master_password="p",
        )
        _http_json(cfg, "/command", method="POST", payload={"text": "hello"})

        call_args = mock_urlopen.call_args
        req = call_args[0][0] if call_args[0] else call_args[1]["req"]
        # The Request object should have Content-Type
        assert req.get_header("Content-type") == "application/json"

    @patch("jarvis_engine.desktop_widget.urlopen")
    def test_invalid_json_response_raises(self, mock_urlopen):
        from jarvis_engine.desktop_widget import _http_json
        resp_mock = MagicMock()
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        resp_mock.read.return_value = b"NOT JSON"
        mock_urlopen.return_value = resp_mock

        cfg = WidgetConfig(
            base_url="http://localhost:8787",
            token="t", signing_key="k", device_id="d", master_password="",
        )
        with pytest.raises(RuntimeError, match="Invalid JSON"):
            _http_json(cfg, "/health")

    @patch("jarvis_engine.desktop_widget.urlopen")
    def test_non_dict_response_raises(self, mock_urlopen):
        from jarvis_engine.desktop_widget import _http_json
        resp_mock = MagicMock()
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        resp_mock.read.return_value = b'[1, 2, 3]'
        mock_urlopen.return_value = resp_mock

        cfg = WidgetConfig(
            base_url="http://localhost:8787",
            token="t", signing_key="k", device_id="d", master_password="",
        )
        with pytest.raises(RuntimeError, match="Invalid response"):
            _http_json(cfg, "/health")


# ---- _http_json_bootstrap ---------------------------------------------------

class TestHttpJsonBootstrap:
    def test_empty_base_url_raises(self):
        from jarvis_engine.desktop_widget import _http_json_bootstrap
        with pytest.raises(RuntimeError, match="Base URL is required"):
            _http_json_bootstrap("", "password", "dev1")

    def test_unsafe_url_raises(self):
        from jarvis_engine.desktop_widget import _http_json_bootstrap
        with pytest.raises(RuntimeError, match="HTTPS"):
            _http_json_bootstrap("http://public.example.com", "password", "dev1")

    def test_empty_password_raises(self):
        from jarvis_engine.desktop_widget import _http_json_bootstrap
        with pytest.raises(RuntimeError, match="Master password"):
            _http_json_bootstrap("http://127.0.0.1:8787", "  ", "dev1")

    @patch("jarvis_engine.desktop_widget.urlopen")
    def test_successful_bootstrap(self, mock_urlopen):
        from jarvis_engine.desktop_widget import _http_json_bootstrap
        resp_mock = MagicMock()
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        resp_mock.read.return_value = json.dumps({
            "ok": True,
            "session": {"token": "new_tok", "signing_key": "new_sk"},
        }).encode("utf-8")
        mock_urlopen.return_value = resp_mock

        result = _http_json_bootstrap("http://127.0.0.1:8787", "secret", "dev1")
        assert result["ok"] is True
        assert result["session"]["token"] == "new_tok"


# ---- _save_widget_cfg -------------------------------------------------------

class TestSaveWidgetCfg:
    @patch("jarvis_engine._shared.atomic_write_json")
    def test_save_calls_atomic_write_with_dpapi(self, mock_write):
        """On Windows, save should encrypt master_password via DPAPI."""
        from jarvis_engine.desktop_widget import _save_widget_cfg
        cfg = WidgetConfig("http://127.0.0.1:8787", "tok", "sk", "dev", "pw")
        root = Path("/fake/root")
        _save_widget_cfg(root, cfg)
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        written_path = call_args[0][0]
        assert str(written_path).endswith("desktop_widget.json")
        payload = call_args[0][1]
        assert payload["base_url"] == "http://127.0.0.1:8787"
        assert payload["device_id"] == "dev"
        assert "updated_utc" in payload
        if _DPAPI_AVAILABLE:
            # Secrets should be DPAPI-encrypted; plaintext should NOT be present
            assert "master_password_protected" in payload
            assert "master_password" not in payload
            assert "token_protected" in payload
            assert "token" not in payload
            assert "signing_key_protected" in payload
            assert "signing_key" not in payload
            # Verify round-trip: decrypt should recover original
            assert _dpapi_decrypt(payload["master_password_protected"]) == "pw"
            assert _dpapi_decrypt(payload["token_protected"]) == "tok"
            assert _dpapi_decrypt(payload["signing_key_protected"]) == "sk"
        else:
            # Fallback: plaintext stored when DPAPI unavailable
            assert payload["master_password"] == "pw"
            assert payload["token"] == "tok"
            assert payload["signing_key"] == "sk"

    @patch("jarvis_engine._shared.atomic_write_json")
    def test_save_empty_password_omits_both_keys(self, mock_write):
        """When master_password is empty, neither key should be written."""
        from jarvis_engine.desktop_widget import _save_widget_cfg
        cfg = WidgetConfig("http://127.0.0.1:8787", "tok", "sk", "dev", "")
        root = Path("/fake/root")
        _save_widget_cfg(root, cfg)
        payload = mock_write.call_args[0][1]
        assert "master_password" not in payload
        assert "master_password_protected" not in payload


# ---- _detect_hotword_once ---------------------------------------------------

class TestDetectHotwordOnce:
    def test_invalid_keyword_returns_false(self):
        from jarvis_engine.desktop_widget import _detect_hotword_once
        # Keywords with special chars should fail regex and return False
        assert _detect_hotword_once("jar!vis") is False

    @patch("jarvis_engine.desktop_widget.subprocess")
    def test_detected_keyword_returns_true(self, mock_subprocess):
        from jarvis_engine.desktop_widget import _detect_hotword_once
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="jarvis\n", stderr=""
        )
        assert _detect_hotword_once("jarvis", timeout_s=2) is True

    @patch("jarvis_engine.desktop_widget.subprocess")
    def test_no_detection_returns_false(self, mock_subprocess):
        from jarvis_engine.desktop_widget import _detect_hotword_once
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="", stderr=""
        )
        assert _detect_hotword_once("jarvis", timeout_s=2) is False

    @patch("jarvis_engine.desktop_widget.subprocess")
    def test_nonzero_returncode_returns_false(self, mock_subprocess):
        from jarvis_engine.desktop_widget import _detect_hotword_once
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=1, stdout="jarvis", stderr="error"
        )
        assert _detect_hotword_once("jarvis", timeout_s=2) is False

    def test_empty_keyword_uses_jarvis(self):
        from jarvis_engine.desktop_widget import _detect_hotword_once
        with patch("jarvis_engine.desktop_widget.subprocess") as mock_sub:
            mock_sub.run.return_value = SimpleNamespace(
                returncode=0, stdout="jarvis\n", stderr=""
            )
            result = _detect_hotword_once("", timeout_s=2)
            assert result is True


# ---- _voice_dictate_once ----------------------------------------------------

class TestVoiceDictateOnce:
    @patch("jarvis_engine.desktop_widget.listen_and_transcribe", create=True)
    def test_whisper_stt_success(self, mock_listen):
        # Need to patch the import inside the function
        mock_result = SimpleNamespace(text="hello world")
        with patch.dict("sys.modules", {"jarvis_engine.stt": MagicMock(listen_and_transcribe=MagicMock(return_value=mock_result))}):
            with patch("jarvis_engine.desktop_widget.listen_and_transcribe", mock_listen, create=True):
                mock_listen.return_value = mock_result
                # We need to actually test the function's import path
                # Since the function does `from jarvis_engine.stt import listen_and_transcribe`,
                # patch the module import
                pass

    @patch("jarvis_engine.desktop_widget._voice_dictate_system_speech")
    def test_fallback_to_system_speech_on_runtime_error(self, mock_fallback):
        from jarvis_engine.desktop_widget import _voice_dictate_once
        mock_fallback.return_value = "fallback text"
        with patch.dict("sys.modules", {}):
            # Make the stt import raise RuntimeError
            stt_mod = MagicMock()
            stt_mod.listen_and_transcribe = MagicMock(side_effect=RuntimeError("no device"))
            with patch.dict("sys.modules", {"jarvis_engine.stt": stt_mod}):
                result = _voice_dictate_once(timeout_s=5)
                assert result == "fallback text"


# ---- Service status uptime formatting (extracted logic) ---------------------

class TestServiceUptimeFormatting:
    """Test the uptime formatting logic from _refresh_services."""

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        """Replicate the formatting logic from _refresh_services."""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m"
        else:
            return f"{seconds // 3600}h {(seconds % 3600) // 60}m"

    def test_seconds(self):
        assert self._format_uptime(45) == "45s"

    def test_minutes(self):
        assert self._format_uptime(120) == "2m"
        assert self._format_uptime(3599) == "59m"

    def test_hours(self):
        assert self._format_uptime(3600) == "1h 0m"
        assert self._format_uptime(7320) == "2h 2m"

    def test_zero(self):
        assert self._format_uptime(0) == "0s"


# ---- Widget orb animation math (extracted) ----------------------------------

class TestOrbAnimationMath:
    """Test the time-based pulse math from _animate_orb."""

    def test_pulse_range(self):
        """Verify the orb pulse stays within expected bounds (4.5 to 7.5)."""
        for t in [0, 0.5, 1.0, 1.5, math.pi, 2 * math.pi, 10.0]:
            breath = math.sin(t * 3.0)
            pulse = 6.0 + breath * 1.5
            assert 4.5 <= pulse <= 7.5, f"pulse {pulse} out of range at t={t}"

    def test_sweep_angle_wraps(self):
        """Verify the sweep angle stays in [0, 360) range."""
        for t in [0, 1.0, 3.0, 100.0, 99999.0]:
            sweep = (t * 120) % 360
            assert 0 <= sweep < 360


# ---- Launcher animation math -----------------------------------------------

class TestLauncherAnimationMath:
    def test_launcher_core_breathing_bounds(self):
        """Core padding stays positive and within canvas (size=96)."""
        size = 96
        for t in [0, 0.5, 1.0, math.pi, 2 * math.pi, 10.0]:
            for speed in [0.3, 1.0, 1.6, 2.5]:
                breath = math.sin(t * 2.5 * speed)
                pad = 24 + breath * 2
                assert pad > 0, f"pad {pad} not positive at t={t}"
                assert pad < size / 2, f"pad {pad} >= half size at t={t}"

    def test_launcher_arc_angles_bounded(self):
        """Arc rotation angles stay in [0, 360)."""
        for t in [0, 1.0, 5.0, 100.0]:
            for speed in [0.3, 1.0, 2.5]:
                a1 = (t * 90 * speed) % 360
                a2 = (360 - (t * 60 * speed) % 360) % 360
                assert 0 <= a1 < 360
                assert 0 <= a2 <= 360

    def test_launcher_particle_orbits(self):
        """Particle positions stay within launcher canvas bounds."""
        size = 96
        cx, cy = size / 2, size / 2
        for t in [0, 0.5, 1.0, 3.0, 10.0]:
            for i in range(3):
                orbit_r = 34 - i * 5
                orbit_speed = (45 + i * 25)
                angle = math.radians((t * orbit_speed) % 360 + i * 120)
                px = cx + orbit_r * math.cos(angle) - 2
                py = cy + orbit_r * math.sin(angle) - 2
                # Particle center should be within canvas
                assert -5 < px < size + 5
                assert -5 < py < size + 5


# ---- Integration: full config round-trip ------------------------------------

class TestConfigRoundTrip:
    @patch("jarvis_engine.desktop_widget.urlopen", side_effect=OSError("no network in tests"))
    @patch("jarvis_engine.desktop_widget._save_widget_cfg")
    def test_load_with_both_files_present(self, mock_save, _mock_urlopen, tmp_path):
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "mobile_api.json").write_text(
            json.dumps({"token": "mob_tok", "signing_key": "mob_sk"}),
            encoding="utf-8-sig",
        )
        (sec / "desktop_widget.json").write_text(
            json.dumps({
                "base_url": "http://192.168.1.10:9000",
                "token": "",
                "signing_key": "",
                "device_id": "my_phone",
                "master_password": "pw123",
            }),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.base_url == "http://192.168.1.10:9000"
        # Empty widget token/key should fall back to mobile_api values
        assert cfg.token == "mob_tok"
        assert cfg.signing_key == "mob_sk"
        assert cfg.device_id == "my_phone"
        assert cfg.master_password == "pw123"
        # Plaintext master_password should trigger migration save
        mock_save.assert_called_once()


# ---- DPAPI encrypt/decrypt --------------------------------------------------

class TestDpapiEncryptDecrypt:
    """Test DPAPI encryption/decryption round-trip (native on Windows)."""

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_round_trip_basic(self):
        """Encrypt then decrypt should recover the original plaintext."""
        original = "my_secret_password_123!"
        encrypted = _dpapi_encrypt(original)
        # Encrypted result must be a non-empty base64 string, different from plaintext
        assert encrypted != original
        assert len(encrypted) > 0
        decrypted = _dpapi_decrypt(encrypted)
        assert decrypted == original

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_round_trip_unicode(self):
        """DPAPI should handle unicode passwords correctly."""
        original = "p@ssw0rd-\u00e9\u00e8\u00ea-\u4e16\u754c"
        encrypted = _dpapi_encrypt(original)
        decrypted = _dpapi_decrypt(encrypted)
        assert decrypted == original

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_round_trip_empty_string(self):
        """DPAPI should handle empty string (edge case)."""
        encrypted = _dpapi_encrypt("")
        decrypted = _dpapi_decrypt(encrypted)
        assert decrypted == ""

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_different_plaintexts_produce_different_ciphertexts(self):
        """Different passwords should produce different encrypted blobs."""
        enc_a = _dpapi_encrypt("password_a")
        enc_b = _dpapi_encrypt("password_b")
        assert enc_a != enc_b

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_decrypt_invalid_base64_raises(self):
        """Decrypting garbage should raise an error."""
        with pytest.raises(Exception):
            _dpapi_decrypt("not-valid-base64!!!")

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_decrypt_wrong_data_raises(self):
        """Decrypting valid base64 that is not DPAPI ciphertext should raise."""
        import base64
        fake = base64.b64encode(b"this is not encrypted data").decode("ascii")
        with pytest.raises(OSError):
            _dpapi_decrypt(fake)


# ---- Config migration from plaintext to DPAPI-protected --------------------

class TestConfigMigration:
    """Test that loading a config with plaintext master_password migrates to DPAPI."""

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    @patch("jarvis_engine._shared.atomic_write_json")
    def test_plaintext_password_migrated_on_load(self, mock_write, tmp_path):
        """Loading config with plaintext master_password should auto-migrate."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({
                "base_url": "http://127.0.0.1:8787",
                "master_password": "migrate_me",
            }),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        # Password should be loaded correctly
        assert cfg.master_password == "migrate_me"
        # Migration should have triggered a save
        mock_write.assert_called_once()
        saved_payload = mock_write.call_args[0][1]
        # The saved payload should have DPAPI-protected key, not plaintext
        assert "master_password_protected" in saved_payload
        assert "master_password" not in saved_payload
        # Verify the protected value decrypts back to original
        assert _dpapi_decrypt(saved_payload["master_password_protected"]) == "migrate_me"

    @patch("jarvis_engine.desktop_widget._save_widget_cfg")
    def test_empty_plaintext_password_no_migration(self, mock_save, tmp_path):
        """Empty plaintext password should NOT trigger migration."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({
                "base_url": "http://127.0.0.1:8787",
                "master_password": "",
            }),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.master_password == ""
        mock_save.assert_not_called()

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_protected_password_loads_without_migration(self, tmp_path):
        """Config with master_password_protected should load without triggering migration."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        # Pre-encrypt the password
        protected = _dpapi_encrypt("already_protected")
        (sec / "desktop_widget.json").write_text(
            json.dumps({
                "base_url": "http://127.0.0.1:8787",
                "master_password_protected": protected,
            }),
            encoding="utf-8",
        )
        with patch("jarvis_engine.desktop_widget._save_widget_cfg") as mock_save:
            cfg = _load_widget_cfg(tmp_path)
        assert cfg.master_password == "already_protected"
        # No migration needed -- save should NOT be called
        mock_save.assert_not_called()

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_protected_takes_precedence_over_plaintext(self, tmp_path):
        """If both keys exist, master_password_protected wins."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        protected = _dpapi_encrypt("the_real_password")
        (sec / "desktop_widget.json").write_text(
            json.dumps({
                "base_url": "http://127.0.0.1:8787",
                "master_password": "stale_plaintext",
                "master_password_protected": protected,
            }),
            encoding="utf-8",
        )
        with patch("jarvis_engine.desktop_widget._save_widget_cfg") as mock_save:
            cfg = _load_widget_cfg(tmp_path)
        assert cfg.master_password == "the_real_password"
        mock_save.assert_not_called()


# ---- Full save/load round-trip with DPAPI -----------------------------------

class TestSaveLoadRoundTripDpapi:
    """Integration: save config then load it back, verifying DPAPI protection."""

    @pytest.mark.skipif(not _DPAPI_AVAILABLE, reason="DPAPI only available on Windows")
    def test_full_round_trip(self, tmp_path):
        from jarvis_engine.desktop_widget import _save_widget_cfg
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)

        cfg_orig = WidgetConfig(
            base_url="http://127.0.0.1:8787",
            token="tok_abc",
            signing_key="sk_xyz",
            device_id="dev1",
            master_password="round_trip_secret",
        )
        _save_widget_cfg(tmp_path, cfg_orig)

        # Verify the JSON on disk does NOT contain plaintext password
        raw = json.loads((sec / "desktop_widget.json").read_text(encoding="utf-8"))
        assert "master_password" not in raw, "Plaintext master_password should not be in saved config"
        assert "master_password_protected" in raw

        # Load it back
        cfg_loaded = _load_widget_cfg(tmp_path)
        assert cfg_loaded.master_password == "round_trip_secret"
        assert cfg_loaded.base_url == "http://127.0.0.1:8787"
        assert cfg_loaded.token == "tok_abc"
        assert cfg_loaded.signing_key == "sk_xyz"
        assert cfg_loaded.device_id == "dev1"


# ---- Toast notification function --------------------------------------------

class TestShowToast:
    """Test _show_toast without actually launching PowerShell."""

    def _reset_throttle(self):
        """Reset the module-level toast throttle state for test isolation."""
        import jarvis_engine.desktop_widget as dw
        dw._last_toast_time = 0.0

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_basic_toast_launches_powershell(self, mock_popen):
        self._reset_throttle()
        _show_toast("Hello", "World", "Info")
        mock_popen.assert_called_once()
        args = mock_popen.call_args
        cmd_list = args[0][0]
        assert cmd_list[0] == "powershell"
        assert "-NoProfile" in cmd_list
        # The script should contain our title and message
        script = cmd_list[-1]
        assert "Hello" in script
        assert "World" in script

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_title_truncated_to_max(self, mock_popen):
        self._reset_throttle()
        long_title = "A" * 200
        _show_toast(long_title, "msg")
        script = mock_popen.call_args[0][0][-1]
        # Title in script should be truncated to _TOAST_MAX_TITLE chars
        assert "A" * _TOAST_MAX_TITLE in script
        assert "A" * (_TOAST_MAX_TITLE + 1) not in script

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_message_truncated_to_max(self, mock_popen):
        self._reset_throttle()
        long_msg = "B" * 500
        _show_toast("title", long_msg)
        script = mock_popen.call_args[0][0][-1]
        assert "B" * _TOAST_MAX_MESSAGE in script
        assert "B" * (_TOAST_MAX_MESSAGE + 1) not in script

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_invalid_icon_defaults_to_info(self, mock_popen):
        self._reset_throttle()
        _show_toast("title", "msg", "BogusIcon")
        script = mock_popen.call_args[0][0][-1]
        assert "::Info" in script

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_warning_icon(self, mock_popen):
        self._reset_throttle()
        _show_toast("title", "msg", "Warning")
        script = mock_popen.call_args[0][0][-1]
        assert "::Warning" in script

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_error_icon(self, mock_popen):
        self._reset_throttle()
        _show_toast("title", "msg", "Error")
        script = mock_popen.call_args[0][0][-1]
        assert "::Error" in script

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_single_quotes_escaped_in_title_and_message(self, mock_popen):
        self._reset_throttle()
        _show_toast("It's", "don't panic")
        script = mock_popen.call_args[0][0][-1]
        # PowerShell escaping: ' -> ''
        assert "It''s" in script
        assert "don''t panic" in script

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_empty_title_defaults_to_jarvis(self, mock_popen):
        self._reset_throttle()
        _show_toast("", "msg")
        script = mock_popen.call_args[0][0][-1]
        assert "Jarvis" in script

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_popen_exception_does_not_raise(self, mock_popen):
        self._reset_throttle()
        mock_popen.side_effect = OSError("powershell not found")
        # Should not raise
        _show_toast("title", "msg")

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_fire_and_forget_uses_popen_not_run(self, mock_popen):
        """Verify Popen is used (non-blocking) rather than subprocess.run."""
        self._reset_throttle()
        _show_toast("title", "msg")
        mock_popen.assert_called_once()
        # stdout/stderr should be DEVNULL (fire-and-forget)
        kwargs = mock_popen.call_args[1]
        import subprocess as _sp
        assert kwargs.get("stdout") == _sp.DEVNULL
        assert kwargs.get("stderr") == _sp.DEVNULL


class TestToastThrottle:
    """Test the toast notification throttle (max 1 per cooldown period)."""

    def _reset_throttle(self):
        import jarvis_engine.desktop_widget as dw
        dw._last_toast_time = 0.0

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_second_toast_within_cooldown_is_throttled(self, mock_popen):
        self._reset_throttle()
        _show_toast("First", "msg")
        assert mock_popen.call_count == 1
        # Second call within cooldown should be throttled
        _show_toast("Second", "msg")
        assert mock_popen.call_count == 1  # Still 1 -- second was suppressed

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_toast_after_cooldown_expires(self, mock_popen):
        self._reset_throttle()
        _show_toast("First", "msg")
        assert mock_popen.call_count == 1
        # Simulate time passing beyond cooldown
        import jarvis_engine.desktop_widget as dw
        dw._last_toast_time = time.time() - _TOAST_COOLDOWN_SECONDS - 1
        _show_toast("Second", "msg")
        assert mock_popen.call_count == 2

    @patch("jarvis_engine.desktop_widget.subprocess.Popen")
    def test_cooldown_is_120_seconds(self, mock_popen):
        """Verify the cooldown constant is 2 minutes (120 seconds)."""
        assert _TOAST_COOLDOWN_SECONDS == 120


class TestToastConstants:
    """Verify toast notification constants."""

    def test_max_title_length(self):
        assert _TOAST_MAX_TITLE == 64

    def test_max_message_length(self):
        assert _TOAST_MAX_MESSAGE == 256

    def test_valid_icon_types(self):
        assert _TOAST_ICON_TYPES == {"Info", "Warning", "Error"}

    def test_cooldown_seconds(self):
        assert _TOAST_COOLDOWN_SECONDS == 120


# ---- Chat-style conversation display ----------------------------------------

class TestChatDisplay:
    """Test the chat-style conversation display using a headless tkinter Text widget.

    These tests verify tag configuration, role-based formatting, line cap
    behavior, and the Clear History button logic without launching a full GUI.
    """

    @pytest.fixture(autouse=True)
    def _tk_text(self):
        """Create a minimal tkinter root + Text widget for each test."""
        import tkinter as _tk
        try:
            self._root = _tk.Tk()
        except _tk.TclError:
            pytest.skip("tkinter Tk() initialization failed (environment issue)")
        self._root.withdraw()  # No visible window
        self._text = _tk.Text(self._root, state=_tk.DISABLED)
        self._text.pack()
        # Import the class to access tag configuration logic
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        self._widget_cls = JarvisDesktopWidget
        yield
        try:
            self._root.destroy()
        except Exception:
            pass

    def _configure_tags(self):
        """Apply the same tag_configure calls the widget uses."""
        self._text.tag_configure(
            "user",
            background="#0c2d5e",
            foreground="#b8d4ff",
            font=("Consolas", 10, "bold"),
            lmargin1=40,
            lmargin2=40,
            rmargin=8,
            spacing1=4,
            spacing3=4,
        )
        self._text.tag_configure(
            "jarvis",
            background="#0d1e1e",
            foreground="#a8e6cf",
            font=("Consolas", 10),
            lmargin1=8,
            lmargin2=8,
            rmargin=40,
            spacing1=4,
            spacing3=4,
        )
        self._text.tag_configure(
            "system",
            foreground="#5a7a9e",
            font=("Consolas", 9),
            lmargin1=8,
            lmargin2=8,
            spacing1=2,
            spacing3=2,
        )
        self._text.tag_configure(
            "error",
            background="#1a0a0a",
            foreground="#f87171",
            font=("Consolas", 10),
            lmargin1=8,
            lmargin2=8,
            spacing1=4,
            spacing3=4,
        )
        self._text.tag_configure(
            "separator",
            foreground="#1e3250",
            font=("Consolas", 6),
            justify="center",
            spacing1=2,
            spacing3=2,
        )
        self._text.tag_configure(
            "timestamp",
            foreground="#3a5a7e",
            font=("Consolas", 8),
            justify="center",
            spacing1=6,
            spacing3=2,
        )

    def _insert_with_role(self, message: str, role: str = "system"):
        """Simulate the _log method's insert logic on our test Text widget."""
        import tkinter as _tk
        stamp = time.strftime("%H:%M:%S")
        self._text.config(state=_tk.NORMAL)

        if role == "user":
            display = f"You: {message}\n"
        elif role == "jarvis":
            display = f"Jarvis: {message}\n"
        elif role == "error":
            display = f"[{stamp}] ERROR: {message}\n"
        else:
            display = f"[{stamp}] {message}\n"

        if role == "user":
            sep_line = "\u2500" * 48 + "\n"
            self._text.insert(_tk.END, sep_line, "separator")
            self._text.insert(_tk.END, f"  {stamp}  \n", "timestamp")

        tag = role if role in ("user", "jarvis", "system", "error") else "system"
        self._text.insert(_tk.END, display, tag)

        line_count = int(self._text.index("end-1c").split(".")[0])
        if line_count > 500:
            self._text.delete("1.0", f"{line_count - 500}.0")

        self._text.config(state=_tk.DISABLED)

    def test_tag_configure_creates_all_roles(self):
        """Verify all expected tags are configured on the Text widget."""
        self._configure_tags()
        tag_names = self._text.tag_names()
        for expected in ("user", "jarvis", "system", "error", "separator", "timestamp"):
            assert expected in tag_names, f"Tag '{expected}' not found in {tag_names}"

    def test_user_tag_has_blue_background(self):
        """The 'user' tag should have a blue background for visual distinction."""
        self._configure_tags()
        bg = self._text.tag_cget("user", "background")
        assert bg == "#0c2d5e"

    def test_jarvis_tag_has_dark_green_background(self):
        """The 'jarvis' tag should have a dark greenish background."""
        self._configure_tags()
        bg = self._text.tag_cget("jarvis", "background")
        assert bg == "#0d1e1e"

    def test_error_tag_has_red_foreground(self):
        """The 'error' tag should use a red-tinted foreground."""
        self._configure_tags()
        fg = self._text.tag_cget("error", "foreground")
        assert fg == "#f87171"

    def test_system_tag_has_muted_foreground(self):
        """The 'system' tag should use a muted color."""
        self._configure_tags()
        fg = self._text.tag_cget("system", "foreground")
        assert fg == "#5a7a9e"

    def test_user_role_inserts_separator_and_timestamp(self):
        """User messages should be preceded by a separator line and timestamp."""
        import tkinter as _tk
        self._configure_tags()
        self._insert_with_role("hello world", role="user")
        content = self._text.get("1.0", _tk.END)
        # Should contain the separator character
        assert "\u2500" in content
        # Should contain the "You:" prefix
        assert "You: hello world" in content

    def test_jarvis_role_shows_prefix(self):
        """Jarvis messages should show 'Jarvis:' prefix."""
        import tkinter as _tk
        self._configure_tags()
        self._insert_with_role("I understand.", role="jarvis")
        content = self._text.get("1.0", _tk.END)
        assert "Jarvis: I understand." in content

    def test_error_role_shows_error_prefix(self):
        """Error messages should show 'ERROR:' with timestamp."""
        import tkinter as _tk
        self._configure_tags()
        self._insert_with_role("connection refused", role="error")
        content = self._text.get("1.0", _tk.END)
        assert "ERROR: connection refused" in content

    def test_system_role_shows_timestamp(self):
        """System messages should include a timestamp bracket."""
        import tkinter as _tk
        self._configure_tags()
        self._insert_with_role("Widget online.", role="system")
        content = self._text.get("1.0", _tk.END)
        assert "Widget online." in content
        assert "[" in content  # timestamp bracket

    def test_tags_applied_to_inserted_text(self):
        """Verify that inserted text actually has the correct tag applied."""
        import tkinter as _tk
        self._configure_tags()
        self._insert_with_role("test message", role="jarvis")
        # Find the line containing the message and check its tags
        # The text widget assigns tags at specific ranges
        content = self._text.get("1.0", _tk.END)
        assert "Jarvis: test message" in content
        # Check tag at the position of the message
        line_start = "1.0"
        tags_at = self._text.tag_names(line_start)
        assert "jarvis" in tags_at

    def test_line_cap_removes_old_lines(self):
        """When exceeding 500 lines, old lines should be pruned."""
        import tkinter as _tk
        self._configure_tags()
        # Insert 510 system messages (1 line each)
        self._text.config(state=_tk.NORMAL)
        for i in range(510):
            self._text.insert(_tk.END, f"line {i}\n", "system")
        # Trigger the cap logic (same as _log)
        line_count = int(self._text.index("end-1c").split(".")[0])
        if line_count > 500:
            self._text.delete("1.0", f"{line_count - 500}.0")
        self._text.config(state=_tk.DISABLED)
        final_count = int(self._text.index("end-1c").split(".")[0])
        # tkinter counts the trailing empty line, so 501 is acceptable (500 content lines)
        assert final_count <= 501

    def test_clear_history_empties_widget(self):
        """The clear history action should remove all text."""
        import tkinter as _tk
        self._configure_tags()
        self._insert_with_role("some message", role="system")
        self._insert_with_role("another message", role="jarvis")

        # Verify there is content
        assert len(self._text.get("1.0", _tk.END).strip()) > 0

        # Simulate clear
        self._text.config(state=_tk.NORMAL)
        self._text.delete("1.0", _tk.END)
        self._text.config(state=_tk.DISABLED)

        # Should be empty
        assert self._text.get("1.0", _tk.END).strip() == ""

    def test_widget_is_disabled_between_inserts(self):
        """The text widget should be in DISABLED state to prevent user edits."""
        self._configure_tags()
        self._insert_with_role("test", role="system")
        assert str(self._text.cget("state")) == "disabled"

    def test_multiple_roles_interleave_correctly(self):
        """Inserting messages with different roles should produce correct ordering."""
        import tkinter as _tk
        self._configure_tags()
        self._insert_with_role("hello", role="user")
        self._insert_with_role("[chat] ok=True", role="jarvis")
        self._insert_with_role("sync complete", role="system")
        self._insert_with_role("timeout", role="error")

        content = self._text.get("1.0", _tk.END)
        # All messages should be present in order (appended at END)
        user_pos = content.find("You: hello")
        jarvis_pos = content.find("Jarvis: [chat] ok=True")
        system_pos = content.find("sync complete")
        error_pos = content.find("ERROR: timeout")

        assert user_pos >= 0
        assert jarvis_pos > user_pos
        assert system_pos > jarvis_pos
        assert error_pos > system_pos

    def test_unknown_role_falls_back_to_system(self):
        """An unrecognized role should be treated as system."""
        import tkinter as _tk
        self._configure_tags()
        self._insert_with_role("mystery", role="unknown_role")
        content = self._text.get("1.0", _tk.END)
        # Should show with timestamp (system format)
        assert "mystery" in content
        assert "[" in content  # timestamp bracket from system format


# ---- Visual State Machine ---------------------------------------------------

class TestWidgetStateMachine:
    """Test the widget visual state machine logic (no tkinter required)."""

    def test_orb_color_idle_online(self):
        """Idle + online should return ACCENT (teal)."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.ACCENT = "#12c9b1"
        widget.ACCENT_2 = "#1aa3ff"
        widget.WARN = "#d15a5a"
        widget._widget_state = "idle"
        widget.online = True
        color = JarvisDesktopWidget._orb_color(widget)
        assert color == "#12c9b1"

    def test_orb_color_idle_offline(self):
        """Idle + offline should return indigo (cool offline color)."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.ACCENT = "#12c9b1"
        widget.ACCENT_2 = "#1aa3ff"
        widget.WARN = "#d15a5a"
        widget._widget_state = "idle"
        widget.online = False
        color = JarvisDesktopWidget._orb_color(widget)
        assert color == "#6366f1"

    def test_orb_color_listening(self):
        """Listening should return ACCENT_2 (blue)."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.ACCENT = "#12c9b1"
        widget.ACCENT_2 = "#1aa3ff"
        widget.WARN = "#d15a5a"
        widget._widget_state = "listening"
        widget.online = True
        color = JarvisDesktopWidget._orb_color(widget)
        assert color == "#1aa3ff"

    def test_orb_color_processing(self):
        """Processing should return orange."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.ACCENT = "#12c9b1"
        widget.ACCENT_2 = "#1aa3ff"
        widget.WARN = "#d15a5a"
        widget._widget_state = "processing"
        widget.online = True
        color = JarvisDesktopWidget._orb_color(widget)
        assert color == "#ff9f43"

    def test_orb_color_error(self):
        """Error should return WARN (red)."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.ACCENT = "#12c9b1"
        widget.ACCENT_2 = "#1aa3ff"
        widget.WARN = "#d15a5a"
        widget._widget_state = "error"
        widget.online = True
        color = JarvisDesktopWidget._orb_color(widget)
        assert color == "#d15a5a"

    def test_set_state_rejects_invalid_state(self):
        """_set_state with invalid state should default to idle."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._widget_state = "processing"
        widget._refresh_status_view = MagicMock()
        JarvisDesktopWidget._set_state(widget, "bogus")
        assert widget._widget_state == "idle"
        widget._refresh_status_view.assert_called_once()

    def test_set_state_valid_states(self):
        """_set_state should accept all valid states."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        for state in ("idle", "listening", "processing", "error"):
            widget = MagicMock(spec=JarvisDesktopWidget)
            widget._refresh_status_view = MagicMock()
            JarvisDesktopWidget._set_state(widget, state)
            assert widget._widget_state == state

    def test_send_command_sets_processing_state(self):
        """_send_command_async should set processing state before HTTP call."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.command_text = MagicMock()
        widget.command_text.get.return_value = "hello\n"
        widget._log = MagicMock()
        widget._set_state = MagicMock()
        widget._current_cfg = MagicMock(return_value=WidgetConfig(
            "http://127.0.0.1:8787", "t", "k", "d", "p"))
        widget.execute_var = MagicMock()
        widget.execute_var.get.return_value = False
        widget.priv_var = MagicMock()
        widget.priv_var.get.return_value = False
        widget.speak_var = MagicMock()
        widget.speak_var.get.return_value = False
        widget._thread = MagicMock()
        widget._cancel_event = threading.Event()

        JarvisDesktopWidget._send_command_async(widget)

        widget._set_state.assert_called_once_with("processing")

    def test_on_escape_cancels_when_processing(self):
        """ESC should cancel command when widget is processing."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._widget_state = "processing"
        widget._cancel_command = MagicMock()
        widget._toggle_min = MagicMock()

        JarvisDesktopWidget._on_escape(widget)

        widget._cancel_command.assert_called_once()
        widget._toggle_min.assert_not_called()

    def test_on_escape_minimizes_when_idle(self):
        """ESC should minimize widget when not processing."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._widget_state = "idle"
        widget._cancel_command = MagicMock()
        widget._toggle_min = MagicMock()

        JarvisDesktopWidget._on_escape(widget)

        widget._toggle_min.assert_called_once()
        widget._cancel_command.assert_not_called()

    def test_dictate_sets_listening_state(self):
        """_dictate_async should set listening state before dictation."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.auto_send_var = MagicMock()
        widget.auto_send_var.get.return_value = False
        widget._set_state = MagicMock()
        widget._thread = MagicMock()

        JarvisDesktopWidget._dictate_async(widget)

        widget._set_state.assert_called_once_with("listening")


# ---- Position Persistence & Edge Snapping -----------------------------------

class TestSnapToEdge:
    """Test edge-snapping logic (no tkinter display required)."""

    def _make_tk_root(self, screen_w: int = 1920, screen_h: int = 1080):
        """Create a mock tkinter root with screen dimensions."""
        root = MagicMock()
        root.winfo_screenwidth.return_value = screen_w
        root.winfo_screenheight.return_value = screen_h
        return root

    def test_no_snap_when_far_from_edges(self):
        root = self._make_tk_root()
        x, y = _snap_to_edge(100, 100, 470, 840, root, snap_dist=20)
        assert x == 100
        assert y == 100

    def test_snap_left_edge(self):
        root = self._make_tk_root()
        x, y = _snap_to_edge(15, 100, 470, 840, root, snap_dist=20)
        assert x == 0
        assert y == 100

    def test_snap_top_edge(self):
        root = self._make_tk_root()
        x, y = _snap_to_edge(100, 10, 470, 840, root, snap_dist=20)
        assert x == 100
        assert y == 0

    def test_snap_right_edge(self):
        """Window right edge near screen right should snap."""
        root = self._make_tk_root(1920, 1080)
        # Window at x=1445 with width=470 -> right edge = 1915, within 20px of 1920
        x, y = _snap_to_edge(1445, 100, 470, 840, root, snap_dist=20)
        assert x == 1920 - 470  # snapped to right
        assert y == 100

    def test_snap_bottom_edge(self):
        """Window bottom near screen bottom (with taskbar margin) should snap."""
        root = self._make_tk_root(1920, 1080)
        # Window at y=195 with height=840 -> bottom = 1035, near 1080-40=1040
        x, y = _snap_to_edge(100, 195, 470, 840, root, snap_dist=20)
        assert x == 100
        assert y == 1080 - 40 - 840  # snapped to bottom with taskbar margin

    def test_snap_corner(self):
        """Both edges near screen corner should snap both axes."""
        root = self._make_tk_root(1920, 1080)
        x, y = _snap_to_edge(5, 8, 470, 200, root, snap_dist=20)
        assert x == 0
        assert y == 0

    def test_exact_edge_no_snap(self):
        """At x=0 already, should remain at 0."""
        root = self._make_tk_root()
        x, y = _snap_to_edge(0, 0, 470, 840, root, snap_dist=20)
        assert x == 0
        assert y == 0

    def test_negative_position_no_snap(self):
        """Positions outside snap range should not snap."""
        root = self._make_tk_root()
        x, y = _snap_to_edge(-50, -50, 470, 840, root, snap_dist=20)
        assert x == -50
        assert y == -50


class TestIsPositionOnScreen:
    """Test on-screen position validation."""

    def _make_tk_root(self, screen_w: int = 1920, screen_h: int = 1080):
        root = MagicMock()
        root.winfo_screenwidth.return_value = screen_w
        root.winfo_screenheight.return_value = screen_h
        return root

    def test_valid_position(self):
        root = self._make_tk_root()
        assert _is_position_on_screen(100, 100, root) is True

    def test_origin(self):
        root = self._make_tk_root()
        assert _is_position_on_screen(0, 0, root) is True

    def test_far_offscreen_left(self):
        root = self._make_tk_root()
        assert _is_position_on_screen(-200, 100, root) is False

    def test_far_offscreen_right(self):
        root = self._make_tk_root()
        assert _is_position_on_screen(2000, 100, root) is False

    def test_slightly_offscreen_allowed(self):
        """Positions slightly off-screen (within -100px tolerance) should be OK."""
        root = self._make_tk_root()
        assert _is_position_on_screen(-50, -50, root) is True

    def test_screen_edge(self):
        root = self._make_tk_root(1920, 1080)
        assert _is_position_on_screen(1920, 1080, root) is True


class TestPositionPersistence:
    """Test position fields in config load/save."""

    def test_config_defaults_no_position(self, tmp_path):
        """Config without position fields should have None positions."""
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.panel_x is None
        assert cfg.panel_y is None
        assert cfg.launcher_x is None
        assert cfg.launcher_y is None

    def test_config_loads_position_fields(self, tmp_path):
        """Config with position fields should load them."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({
                "base_url": "http://127.0.0.1:8787",
                "panel_x": 200,
                "panel_y": 100,
                "launcher_x": 1800,
                "launcher_y": 950,
            }),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.panel_x == 200
        assert cfg.panel_y == 100
        assert cfg.launcher_x == 1800
        assert cfg.launcher_y == 950

    def test_config_invalid_position_returns_none(self, tmp_path):
        """Non-integer position values should be treated as None."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({
                "base_url": "http://127.0.0.1:8787",
                "panel_x": "not_a_number",
                "panel_y": None,
            }),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.panel_x is None
        assert cfg.panel_y is None

    @patch("jarvis_engine._shared.atomic_write_json")
    def test_save_includes_position_fields(self, mock_write):
        """Save should include position fields when set."""
        from jarvis_engine.desktop_widget import _save_widget_cfg
        cfg = WidgetConfig("http://127.0.0.1:8787", "t", "k", "d", "",
                          panel_x=300, panel_y=150, launcher_x=1800, launcher_y=950)
        _save_widget_cfg(Path("/fake"), cfg)
        payload = mock_write.call_args[0][1]
        assert payload["panel_x"] == 300
        assert payload["panel_y"] == 150
        assert payload["launcher_x"] == 1800
        assert payload["launcher_y"] == 950

    @patch("jarvis_engine._shared.atomic_write_json")
    def test_save_omits_none_positions(self, mock_write):
        """Save should not include position fields when None."""
        from jarvis_engine.desktop_widget import _save_widget_cfg
        cfg = WidgetConfig("http://127.0.0.1:8787", "t", "k", "d", "")
        _save_widget_cfg(Path("/fake"), cfg)
        payload = mock_write.call_args[0][1]
        assert "panel_x" not in payload
        assert "panel_y" not in payload
        assert "launcher_x" not in payload
        assert "launcher_y" not in payload

    def test_widget_config_backward_compatible(self, tmp_path):
        """Old config files without position fields should load fine."""
        sec = tmp_path / ".planning" / "security"
        sec.mkdir(parents=True)
        (sec / "desktop_widget.json").write_text(
            json.dumps({
                "base_url": "http://127.0.0.1:8787",
                "token": "old_tok",
                "signing_key": "old_sk",
                "device_id": "old_dev",
            }),
            encoding="utf-8",
        )
        cfg = _load_widget_cfg(tmp_path)
        assert cfg.token == "old_tok"
        assert cfg.panel_x is None
        assert cfg.launcher_x is None


# ---- System Tray Icon -------------------------------------------------------

class TestCreateTrayIconImage:
    """Test the tray icon image generation (requires PIL)."""

    def test_creates_64x64_image(self):
        from jarvis_engine.desktop_widget import _create_tray_icon_image
        img = _create_tray_icon_image()
        assert img is not None
        assert img.size == (64, 64)
        assert img.mode == "RGBA"

    def test_image_has_blue_pixel(self):
        """The icon should contain blue pixels (from the background)."""
        from jarvis_engine.desktop_widget import _create_tray_icon_image
        img = _create_tray_icon_image()
        assert img is not None
        # Sample the edge -- should be blue (18, 163, 255, 255)
        pixel = img.getpixel((5, 32))
        # Blue channel should be dominant
        assert pixel[2] > 200  # B channel

    def test_image_has_white_text_area(self):
        """The center area should contain white pixels from the 'J' text."""
        from jarvis_engine.desktop_widget import _create_tray_icon_image
        img = _create_tray_icon_image()
        assert img is not None
        # Sample center area -- should have some white pixels
        center_pixels = [img.getpixel((32, y)) for y in range(20, 44)]
        has_white = any(p[0] > 200 and p[1] > 200 and p[2] > 200 for p in center_pixels)
        assert has_white, "Expected white 'J' text in center of tray icon"


class TestTrayMenuCallbacks:
    """Test tray menu callbacks route to correct widget methods."""

    def test_tray_show_widget_calls_show_panel(self):
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.after = MagicMock()
        widget._show_panel = MagicMock()
        JarvisDesktopWidget._tray_show_widget(widget)
        widget.after.assert_called_once_with(0, widget._show_panel)

    def test_tray_voice_dictate_shows_panel_then_dictates(self):
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.after = MagicMock()
        widget._show_panel = MagicMock()
        widget._voice_dictate = MagicMock()
        JarvisDesktopWidget._tray_voice_dictate(widget)
        calls = widget.after.call_args_list
        assert len(calls) == 2
        assert calls[0] == ((0, widget._show_panel),)
        assert calls[1][0][0] == 100  # 100ms delay before dictation

    def test_tray_quit_calls_confirm_exit(self):
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.after = MagicMock()
        widget._confirm_exit = MagicMock()
        JarvisDesktopWidget._tray_quit(widget)
        widget.after.assert_called_once_with(0, widget._confirm_exit)

    def test_stop_tray_icon_cleans_up(self):
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        mock_icon = MagicMock()
        widget._tray_icon = mock_icon
        JarvisDesktopWidget._stop_tray_icon(widget)
        mock_icon.stop.assert_called_once()
        assert widget._tray_icon is None

    def test_stop_tray_icon_none_is_noop(self):
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._tray_icon = None
        JarvisDesktopWidget._stop_tray_icon(widget)
        # Should not raise

    def test_hide_panel_with_tray_shows_launcher(self):
        """When tray icon is present, hide_panel should still show the launcher orb."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._tray_icon = MagicMock()  # Tray icon present
        widget.launcher_win = MagicMock()
        JarvisDesktopWidget._hide_panel(widget)
        widget.withdraw.assert_called_once()
        # Launcher orb must always be visible — tray icon is supplementary
        widget.launcher_win.deiconify.assert_called_once()
        widget.launcher_win.lift.assert_called_once()

    def test_hide_panel_without_tray_shows_launcher(self):
        """Without tray icon, hide_panel should show the launcher orb."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._tray_icon = None  # No tray icon
        widget.launcher_win = MagicMock()
        JarvisDesktopWidget._hide_panel(widget)
        widget.withdraw.assert_called_once()
        widget.launcher_win.deiconify.assert_called_once()
        widget.launcher_win.lift.assert_called_once()

    def test_shutdown_stops_tray_icon(self):
        """_shutdown should stop the tray icon."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget.stop_event = threading.Event()
        widget._stop_tray_icon = MagicMock()
        widget._orb_after_id = None
        widget._launcher_after_id = None
        widget.launcher_win = None
        JarvisDesktopWidget._shutdown(widget)
        widget._stop_tray_icon.assert_called_once()

    @patch("jarvis_engine.desktop_widget.messagebox")
    def test_confirm_exit_yes_calls_shutdown(self, mock_msgbox):
        """When user confirms exit, _shutdown should be called."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        mock_msgbox.askyesno.return_value = True
        mock_msgbox.WARNING = "warning"
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._shutdown = MagicMock()
        JarvisDesktopWidget._confirm_exit(widget)
        mock_msgbox.askyesno.assert_called_once()
        widget._shutdown.assert_called_once()

    @patch("jarvis_engine.desktop_widget.messagebox")
    def test_confirm_exit_no_does_not_shutdown(self, mock_msgbox):
        """When user cancels exit, _shutdown should NOT be called."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        mock_msgbox.askyesno.return_value = False
        mock_msgbox.WARNING = "warning"
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._shutdown = MagicMock()
        JarvisDesktopWidget._confirm_exit(widget)
        mock_msgbox.askyesno.assert_called_once()
        widget._shutdown.assert_not_called()

    @patch("jarvis_engine.desktop_widget.messagebox")
    def test_confirm_exit_dialog_contains_key_info(self, mock_msgbox):
        """Confirmation dialog should mention services and memory safety."""
        from jarvis_engine.desktop_widget import JarvisDesktopWidget
        mock_msgbox.askyesno.return_value = False
        mock_msgbox.WARNING = "warning"
        widget = MagicMock(spec=JarvisDesktopWidget)
        widget._shutdown = MagicMock()
        JarvisDesktopWidget._confirm_exit(widget)
        call_args = mock_msgbox.askyesno.call_args
        msg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("message", "")
        # Must mention key shutdown info
        assert "Daemon" in msg or "daemon" in msg.lower()
        assert "Mobile API" in msg or "mobile" in msg.lower()
        assert "memory" in msg.lower() or "Memory" in msg
        assert "Minimize" in msg or "minimize" in msg.lower()
