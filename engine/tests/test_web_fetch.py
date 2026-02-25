"""Tests for engine/src/jarvis_engine/web_fetch.py

Covers: is_safe_public_url, resolve_and_check_ip, SafeRedirectHandler,
        fetch_page_text, search_duckduckgo.

ALL network calls and DNS resolution are mocked — no real HTTP/DNS.
"""
from __future__ import annotations

import socket
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.web_fetch import (
    SafeRedirectHandler,
    fetch_page_text,
    is_safe_public_url,
    resolve_and_check_ip,
    search_duckduckgo,
)


# ── Helper to build fake getaddrinfo results ────────────────────────────


def _addrinfo(ip: str) -> list[tuple]:
    """Build a minimal socket.getaddrinfo-style result list for a single IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]


def _addrinfo_v6(ip: str) -> list[tuple]:
    return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 80, 0, 0))]


# ── is_safe_public_url ──────────────────────────────────────────────────


class TestIsSafePublicUrl:
    """Security-critical: SSRF prevention at the URL validation layer."""

    # ---- scheme checks ----

    def test_http_allowed(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            assert is_safe_public_url("http://example.com/page") is True

    def test_https_allowed(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            assert is_safe_public_url("https://example.com/page") is True

    def test_ftp_rejected(self):
        assert is_safe_public_url("ftp://example.com/file") is False

    def test_file_scheme_rejected(self):
        assert is_safe_public_url("file:///etc/passwd") is False

    def test_javascript_scheme_rejected(self):
        assert is_safe_public_url("javascript:alert(1)") is False

    def test_data_scheme_rejected(self):
        assert is_safe_public_url("data:text/html,<h1>Hi</h1>") is False

    def test_empty_string_rejected(self):
        assert is_safe_public_url("") is False

    def test_no_scheme_rejected(self):
        assert is_safe_public_url("example.com") is False

    # ---- localhost / empty host ----

    def test_localhost_rejected(self):
        assert is_safe_public_url("http://localhost/path") is False

    def test_localhost_uppercase_rejected(self):
        assert is_safe_public_url("http://LOCALHOST/path") is False

    def test_empty_host_rejected(self):
        assert is_safe_public_url("http:///path") is False

    # ---- private IP literals (IPv4) ----

    def test_loopback_127_0_0_1(self):
        assert is_safe_public_url("http://127.0.0.1/") is False

    def test_loopback_127_x(self):
        assert is_safe_public_url("http://127.255.0.1/") is False

    def test_private_10_x(self):
        assert is_safe_public_url("http://10.0.0.1/") is False

    def test_private_172_16_x(self):
        assert is_safe_public_url("http://172.16.0.1/") is False

    def test_private_172_31_x(self):
        assert is_safe_public_url("http://172.31.255.255/") is False

    def test_private_192_168_x(self):
        assert is_safe_public_url("http://192.168.1.1/") is False

    def test_unspecified_0_0_0_0(self):
        assert is_safe_public_url("http://0.0.0.0/") is False

    def test_link_local_169_254(self):
        assert is_safe_public_url("http://169.254.1.1/") is False

    def test_multicast_224_x(self):
        assert is_safe_public_url("http://224.0.0.1/") is False

    # ---- private IP literals (IPv6) ----

    def test_ipv6_loopback(self):
        assert is_safe_public_url("http://[::1]/") is False

    def test_ipv6_unspecified(self):
        assert is_safe_public_url("http://[::]/") is False

    def test_ipv6_link_local(self):
        assert is_safe_public_url("http://[fe80::1]/") is False

    # ---- public IP literal ----

    def test_public_ip_accepted(self):
        assert is_safe_public_url("http://93.184.216.34/") is True

    # ---- hostname resolves to private IP (DNS attack) ----

    def test_hostname_resolving_to_private_rejected(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("10.0.0.1")):
            assert is_safe_public_url("http://evil.example.com/") is False

    def test_hostname_resolving_to_loopback_rejected(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("127.0.0.1")):
            assert is_safe_public_url("http://evil.example.com/") is False

    def test_hostname_resolving_to_public_accepted(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            assert is_safe_public_url("http://example.com/") is True

    def test_hostname_dns_failure_rejected(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            assert is_safe_public_url("http://nonexistent.invalid/") is False

    def test_mixed_dns_results_one_private_rejected(self):
        """If any resolved IP is private, the entire URL is rejected."""
        results = _addrinfo("93.184.216.34") + _addrinfo("10.0.0.1")
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=results):
            assert is_safe_public_url("http://mixed.example.com/") is False

    def test_reserved_ip_240_rejected(self):
        assert is_safe_public_url("http://240.0.0.1/") is False


# ── resolve_and_check_ip ────────────────────────────────────────────────


class TestResolveAndCheckIp:
    """Security-critical: DNS rebinding prevention (TOCTOU mitigation)."""

    def test_public_ip_passes(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            assert resolve_and_check_ip("https://example.com/page") is True

    def test_private_ip_fails(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("192.168.1.1")):
            assert resolve_and_check_ip("https://evil.com/") is False

    def test_loopback_fails(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("127.0.0.1")):
            assert resolve_and_check_ip("https://evil.com/") is False

    def test_dns_failure_returns_false(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", side_effect=socket.gaierror):
            assert resolve_and_check_ip("https://fail.example.com/") is False

    def test_empty_host_returns_false(self):
        assert resolve_and_check_ip("https:///path") is False

    def test_uses_correct_default_port_https(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")) as m:
            resolve_and_check_ip("https://example.com/page")
            m.assert_called_once_with("example.com", 443, proto=socket.IPPROTO_TCP)

    def test_uses_correct_default_port_http(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")) as m:
            resolve_and_check_ip("http://example.com/page")
            m.assert_called_once_with("example.com", 80, proto=socket.IPPROTO_TCP)

    def test_explicit_port_used(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")) as m:
            resolve_and_check_ip("http://example.com:8080/page")
            m.assert_called_once_with("example.com", 8080, proto=socket.IPPROTO_TCP)

    def test_ipv6_loopback_rejected(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo_v6("::1")):
            assert resolve_and_check_ip("https://evil.com/") is False

    def test_link_local_rejected(self):
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("169.254.0.1")):
            assert resolve_and_check_ip("https://evil.com/") is False


# ── SafeRedirectHandler ─────────────────────────────────────────────────


class TestSafeRedirectHandler:
    def _make_handler(self):
        return SafeRedirectHandler()

    def test_redirect_to_public_url_allowed(self):
        handler = self._make_handler()
        req = MagicMock()
        req.get_method.return_value = "GET"
        req.full_url = "https://original.example.com/"
        req.data = None
        with patch("jarvis_engine.web_fetch.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            result = handler.redirect_request(
                req, None, 302, "Found", {}, "https://safe.example.com/page"
            )
        # Should return an actual request (not None)
        assert result is not None

    def test_redirect_to_private_ip_blocked(self):
        handler = self._make_handler()
        req = MagicMock()
        result = handler.redirect_request(
            req, None, 302, "Found", {}, "http://192.168.1.1/admin"
        )
        assert result is None

    def test_redirect_to_localhost_blocked(self):
        handler = self._make_handler()
        req = MagicMock()
        result = handler.redirect_request(
            req, None, 302, "Found", {}, "http://localhost/secrets"
        )
        assert result is None

    def test_redirect_to_file_scheme_blocked(self):
        handler = self._make_handler()
        req = MagicMock()
        result = handler.redirect_request(
            req, None, 302, "Found", {}, "file:///etc/passwd"
        )
        assert result is None

    def test_redirect_to_loopback_blocked(self):
        handler = self._make_handler()
        req = MagicMock()
        result = handler.redirect_request(
            req, None, 302, "Found", {}, "http://127.0.0.1/internal"
        )
        assert result is None


# ── fetch_page_text ─────────────────────────────────────────────────────


class TestFetchPageText:
    """Full HTTP fetch with SSRF checks, HTML stripping, DNS rebinding prevention."""

    @patch("jarvis_engine.web_fetch.resolve_and_check_ip", return_value=True)
    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.build_opener")
    def test_returns_stripped_text(self, mock_opener_fn, mock_safe, mock_resolve):
        html = b"<html><body><p>Hello World</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener_fn.return_value.open.return_value = mock_resp

        result = fetch_page_text("https://example.com/")
        assert "Hello World" in result

    @patch("jarvis_engine.web_fetch.resolve_and_check_ip", return_value=True)
    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.build_opener")
    def test_strips_script_tags(self, mock_opener_fn, mock_safe, mock_resolve):
        html = b"<html><script>alert('xss')</script><p>Clean</p></html>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener_fn.return_value.open.return_value = mock_resp

        result = fetch_page_text("https://example.com/")
        assert "alert" not in result
        assert "Clean" in result

    @patch("jarvis_engine.web_fetch.resolve_and_check_ip", return_value=True)
    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.build_opener")
    def test_strips_style_tags(self, mock_opener_fn, mock_safe, mock_resolve):
        html = b"<html><style>.hide{display:none}</style><p>Visible</p></html>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener_fn.return_value.open.return_value = mock_resp

        result = fetch_page_text("https://example.com/")
        assert "display:none" not in result
        assert "Visible" in result

    @patch("jarvis_engine.web_fetch.resolve_and_check_ip", return_value=True)
    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.build_opener")
    def test_unescapes_html_entities(self, mock_opener_fn, mock_safe, mock_resolve):
        html = b"<p>A &amp; B &lt; C</p>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener_fn.return_value.open.return_value = mock_resp

        result = fetch_page_text("https://example.com/")
        assert "A & B < C" in result

    def test_unsafe_url_returns_empty(self):
        # Private IP — no DNS mock needed, should fail at URL validation
        result = fetch_page_text("http://192.168.1.1/admin")
        assert result == ""

    def test_localhost_returns_empty(self):
        result = fetch_page_text("http://localhost/secrets")
        assert result == ""

    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.resolve_and_check_ip", return_value=False)
    def test_dns_rebinding_returns_empty(self, mock_resolve, mock_safe):
        result = fetch_page_text("https://rebind.example.com/")
        assert result == ""

    @patch("jarvis_engine.web_fetch.resolve_and_check_ip", return_value=True)
    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.build_opener")
    def test_network_error_returns_empty(self, mock_opener_fn, mock_safe, mock_resolve):
        mock_opener_fn.return_value.open.side_effect = OSError("connection refused")
        result = fetch_page_text("https://down.example.com/")
        assert result == ""

    @patch("jarvis_engine.web_fetch.resolve_and_check_ip", return_value=True)
    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.build_opener")
    def test_collapses_whitespace(self, mock_opener_fn, mock_safe, mock_resolve):
        html = b"<p>  lots   of    spaces  </p>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener_fn.return_value.open.return_value = mock_resp

        result = fetch_page_text("https://example.com/")
        assert "  " not in result  # no double spaces
        assert "lots of spaces" in result


# ── search_duckduckgo ───────────────────────────────────────────────────


class TestSearchDuckduckgo:
    def _mock_urlopen(self, html_body: bytes):
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.urlopen")
    def test_extracts_urls_from_results(self, mock_urlopen, mock_safe):
        html = b'''
        <div>
            <a href="https://example.com/result1">Result 1</a>
            <a href="https://example.org/result2">Result 2</a>
        </div>
        '''
        mock_urlopen.return_value = self._mock_urlopen(html).return_value
        # re-mock to use the context manager properly
        mock_urlopen.return_value = self._mock_urlopen(html)

        urls = search_duckduckgo("test query", limit=5)
        assert "https://example.com/result1" in urls
        assert "https://example.org/result2" in urls

    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.urlopen")
    def test_respects_limit(self, mock_urlopen, mock_safe):
        html = b'''
        <a href="https://a.com/1">1</a>
        <a href="https://b.com/2">2</a>
        <a href="https://c.com/3">3</a>
        <a href="https://d.com/4">4</a>
        '''
        mock_urlopen.return_value = self._mock_urlopen(html)
        urls = search_duckduckgo("test", limit=2)
        assert len(urls) <= 2

    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.urlopen")
    def test_filters_duckduckgo_urls(self, mock_urlopen, mock_safe):
        html = b'''
        <a href="https://duckduckgo.com/internal">DDG</a>
        <a href="https://example.com/real">Real</a>
        '''
        mock_urlopen.return_value = self._mock_urlopen(html)
        urls = search_duckduckgo("test", limit=5)
        for url in urls:
            assert "duckduckgo.com" not in url

    @patch("jarvis_engine.web_fetch.urlopen")
    def test_filters_unsafe_urls(self, mock_urlopen):
        html = b'''
        <a href="http://192.168.1.1/admin">Private</a>
        <a href="https://safe.example.com/page">Safe</a>
        '''
        mock_urlopen.return_value = self._mock_urlopen(html)

        # is_safe_public_url: private IP returns False, safe URL returns True
        def _safe_check(url):
            return "192.168" not in url

        with patch("jarvis_engine.web_fetch.is_safe_public_url", side_effect=_safe_check):
            urls = search_duckduckgo("test", limit=5)
        assert all("192.168" not in u for u in urls)

    @patch("jarvis_engine.web_fetch.urlopen", side_effect=OSError("network down"))
    def test_network_error_returns_empty_list(self, mock_urlopen):
        urls = search_duckduckgo("test query", limit=5)
        assert urls == []

    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.urlopen")
    def test_deduplicates_urls(self, mock_urlopen, mock_safe):
        html = b'''
        <a href="https://example.com/page">First</a>
        <a href="https://example.com/page">Duplicate</a>
        <a href="https://other.com/page">Other</a>
        '''
        mock_urlopen.return_value = self._mock_urlopen(html)
        urls = search_duckduckgo("test", limit=10)
        assert len(urls) == len(set(urls))  # no duplicates

    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.urlopen")
    def test_unescapes_html_entities_in_urls(self, mock_urlopen, mock_safe):
        html = b'<a href="https://example.com/page?a=1&amp;b=2">Link</a>'
        mock_urlopen.return_value = self._mock_urlopen(html)
        urls = search_duckduckgo("test", limit=5)
        # The & should be unescaped
        if urls:
            assert "&amp;" not in urls[0]
            assert "&" in urls[0]

    @patch("jarvis_engine.web_fetch.is_safe_public_url", return_value=True)
    @patch("jarvis_engine.web_fetch.urlopen")
    def test_no_results_returns_empty_list(self, mock_urlopen, mock_safe):
        html = b"<html><body><p>No results found</p></body></html>"
        mock_urlopen.return_value = self._mock_urlopen(html)
        urls = search_duckduckgo("xyzzy_nonexistent", limit=5)
        assert urls == []
