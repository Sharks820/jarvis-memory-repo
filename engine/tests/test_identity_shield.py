"""Tests for identity_shield — breach monitoring, typosquat detection,
impersonation detection, and family member registry."""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.security.identity_shield import (
    BreachMonitor,
    FamilyShield,
    ImpersonationDetector,
    TyposquatMonitor,
)


# ---------------------------------------------------------------------------
# FamilyShield
# ---------------------------------------------------------------------------


class TestFamilyShield:
    """FamilyShield registry CRUD and persistence."""

    def test_add_member(self, tmp_path: Path) -> None:
        """Adding a member stores name, emails, usernames, domains."""
        fs = FamilyShield(config_path=tmp_path / "family.json")
        fs.add_member(
            "Alice",
            emails=["alice@example.com"],
            usernames=["alice123"],
            domains=["alice.com"],
        )
        members = fs.get_members()
        assert len(members) == 1
        assert members[0]["name"] == "Alice"
        assert "alice@example.com" in members[0]["emails"]
        assert "alice123" in members[0]["usernames"]
        assert "alice.com" in members[0]["domains"]

    def test_persist(self, tmp_path: Path) -> None:
        """Members survive save/reload from JSON."""
        cfg = tmp_path / "family.json"
        fs1 = FamilyShield(config_path=cfg)
        fs1.add_member("Bob", emails=["bob@test.org"])

        # Reload from disk
        fs2 = FamilyShield(config_path=cfg)
        members = fs2.get_members()
        assert len(members) == 1
        assert members[0]["name"] == "Bob"
        assert "bob@test.org" in members[0]["emails"]

    def test_get_all_emails(self, tmp_path: Path) -> None:
        """get_all_emails returns emails from every member."""
        fs = FamilyShield(config_path=tmp_path / "family.json")
        fs.add_member("Alice", emails=["a@x.com", "a2@x.com"])
        fs.add_member("Bob", emails=["b@x.com"])
        all_emails = fs.get_all_emails()
        assert set(all_emails) == {"a@x.com", "a2@x.com", "b@x.com"}

    def test_get_all_usernames(self, tmp_path: Path) -> None:
        """get_all_usernames aggregates across members."""
        fs = FamilyShield(config_path=tmp_path / "family.json")
        fs.add_member("Alice", usernames=["alice_gh"])
        fs.add_member("Bob", usernames=["bob_tw", "bob_ig"])
        assert set(fs.get_all_usernames()) == {"alice_gh", "bob_tw", "bob_ig"}

    def test_get_all_domains(self, tmp_path: Path) -> None:
        """get_all_domains aggregates across members."""
        fs = FamilyShield(config_path=tmp_path / "family.json")
        fs.add_member("Alice", domains=["alice.com"])
        fs.add_member("Bob", domains=["bob.dev", "bob.io"])
        assert set(fs.get_all_domains()) == {"alice.com", "bob.dev", "bob.io"}

    def test_no_config_path_memory_only(self) -> None:
        """When config_path is None, operates in-memory without error."""
        fs = FamilyShield(config_path=None)
        fs.add_member("Eve", emails=["eve@x.com"])
        assert len(fs.get_members()) == 1


# ---------------------------------------------------------------------------
# BreachMonitor
# ---------------------------------------------------------------------------


class TestBreachMonitor:
    """BreachMonitor — HIBP k-anonymity and breach lookup."""

    def test_password_check_compromised(self) -> None:
        """Password found in HIBP k-anonymity range returns compromised=True."""
        password = "password123"
        sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        prefix, suffix = sha1[:5], sha1[5:]

        # Simulate HIBP response: suffix:count lines
        fake_body = f"{suffix}:42\nAABBCCDDEEFF00112233445566778:1\n"
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_body.encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        bm = BreachMonitor()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = bm.check_password(password)

        assert result["compromised"] is True
        assert result["count"] == 42

    def test_password_check_safe(self) -> None:
        """Password NOT in HIBP response returns compromised=False."""
        password = "s3cure_rand0m_p@ss!"
        # Response that will NOT contain the suffix
        fake_body = "AABBCCDDEE0011223344556677889:5\n1122334455667788990011223344A:2\n"
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_body.encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        bm = BreachMonitor()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = bm.check_password(password)

        assert result["compromised"] is False
        assert result["count"] == 0

    def test_email_check_with_api_key(self) -> None:
        """Email breach check with API key returns parsed breaches."""
        breaches_json = json.dumps([
            {
                "Name": "ExampleBreach",
                "BreachDate": "2023-01-15",
                "DataClasses": ["Email addresses", "Passwords"],
            }
        ]).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = breaches_json
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        bm = BreachMonitor(api_key="test-hibp-key")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = bm.check_email("victim@example.com")

        assert len(result) == 1
        assert result[0]["name"] == "ExampleBreach"
        assert result[0]["breach_date"] == "2023-01-15"
        assert "Passwords" in result[0]["data_classes"]

    def test_email_check_no_api_key(self) -> None:
        """Without API key, check_email returns empty list gracefully."""
        bm = BreachMonitor(api_key=None)
        result = bm.check_email("user@example.com")
        assert result == []

    def test_check_all(self, tmp_path: Path) -> None:
        """check_all iterates family emails, returns per-email results."""
        family = FamilyShield(config_path=tmp_path / "f.json")
        family.add_member("Alice", emails=["a@x.com"])
        family.add_member("Bob", emails=["b@x.com"])

        bm = BreachMonitor(api_key="key")

        breaches_json = json.dumps([
            {"Name": "Leak", "BreachDate": "2024-06-01", "DataClasses": ["Emails"]}
        ]).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = breaches_json
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            results = bm.check_all(family)

        assert "a@x.com" in results
        assert "b@x.com" in results
        assert len(results["a@x.com"]) == 1


# ---------------------------------------------------------------------------
# TyposquatMonitor
# ---------------------------------------------------------------------------


class TestTyposquatMonitor:
    """TyposquatMonitor — domain variant generation and DNS check."""

    def test_generate_variants_count(self) -> None:
        """generate_variants produces at least 5 variants for a typical domain."""
        tm = TyposquatMonitor()
        variants = tm.generate_variants("example.com")
        assert len(variants) >= 5
        # All should be strings, none should equal the original
        for v in variants:
            assert isinstance(v, str)
            assert v != "example.com"

    def test_generate_variants_includes_tld_swap(self) -> None:
        """TLD swap should produce .net and .org variants."""
        tm = TyposquatMonitor()
        variants = tm.generate_variants("example.com")
        assert "example.net" in variants
        assert "example.org" in variants

    def test_generate_variants_includes_homoglyph(self) -> None:
        """Homoglyph substitution should appear (e.g. l->1, o->0)."""
        tm = TyposquatMonitor()
        variants = tm.generate_variants("google.com")
        # 'o' -> '0' or 'l' -> '1' should produce g00gle.com or goog1e.com
        assert any("0" in v.split(".")[0] for v in variants) or \
               any("1" in v.split(".")[0] for v in variants)

    def test_check_domain_registered(self) -> None:
        """check_domain detects registered typosquat variant via DNS."""
        tm = TyposquatMonitor()

        def fake_getaddrinfo(host, port, *a, **kw):
            if host == "exmple.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
            raise socket.gaierror("not found")

        with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
            results = tm.check_domain("example.com")

        registered = [r for r in results if r["registered"]]
        # exmple.com (char omission) should be detected
        exmple_hits = [r for r in registered if r["variant"] == "exmple.com"]
        assert len(exmple_hits) == 1
        assert "93.184.216.34" in exmple_hits[0]["ips"]

    def test_scan_all(self, tmp_path: Path) -> None:
        """scan_all checks all family domains."""
        family = FamilyShield(config_path=tmp_path / "f.json")
        family.add_member("Alice", domains=["alice.com"])

        tm = TyposquatMonitor()

        # All DNS lookups fail -> no registered variants
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            results = tm.scan_all(family)

        assert "alice.com" in results
        assert all(not r["registered"] for r in results["alice.com"])


# ---------------------------------------------------------------------------
# ImpersonationDetector
# ---------------------------------------------------------------------------


class TestImpersonationDetector:
    """ImpersonationDetector — username variant generation + profile check."""

    def test_generate_username_variants(self) -> None:
        """generate_username_variants produces multiple variants."""
        det = ImpersonationDetector()
        variants = det.generate_username_variants("john")
        assert len(variants) >= 5
        # Expect underscore variants
        assert "john_" in variants or "_john" in variants
        # Expect official/real variants
        assert any("official" in v for v in variants)
        assert any("real" in v for v in variants)
        # Expect digit appending
        assert any(v.endswith("1") or v.endswith("2") for v in variants)

    def test_generate_username_homoglyph(self) -> None:
        """Homoglyph substitution: o->0, l->1."""
        det = ImpersonationDetector()
        variants = det.generate_username_variants("john")
        assert "j0hn" in variants

    def test_check_platform_exists(self) -> None:
        """check_platform returns exists=True when profile returns 200."""
        det = ImpersonationDetector()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = det.check_platform("john_fake", "github")

        assert result is not None
        assert result["exists"] is True
        assert result["platform"] == "github"
        assert "john_fake" in result["url"]

    def test_check_platform_not_found(self) -> None:
        """check_platform returns exists=False on HTTP error."""
        det = ImpersonationDetector()

        from urllib.error import HTTPError
        err = HTTPError(
            url="https://github.com/john_fake",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = det.check_platform("john_fake", "github")

        assert result is not None
        assert result["exists"] is False

    def test_scan_all(self, tmp_path: Path) -> None:
        """scan_all checks all family usernames across platforms."""
        family = FamilyShield(config_path=tmp_path / "f.json")
        family.add_member("Alice", usernames=["alice_dev"])

        det = ImpersonationDetector()

        from urllib.error import HTTPError
        err = HTTPError(url="x", code=404, msg="Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]
        with patch("urllib.request.urlopen", side_effect=err):
            results = det.scan_all(family)

        assert "alice_dev" in results
        # Should have checked across platforms with variants
        assert len(results["alice_dev"]) > 0


# ---------------------------------------------------------------------------
# Status / summary report
# ---------------------------------------------------------------------------


class TestStatusReport:
    """Each module should expose a status or summary method."""

    def test_breach_monitor_status(self) -> None:
        bm = BreachMonitor()
        status = bm.status()
        assert "api_key_configured" in status
        assert status["api_key_configured"] is False

    def test_typosquat_monitor_status(self) -> None:
        tm = TyposquatMonitor()
        status = tm.status()
        assert "module" in status
        assert status["module"] == "TyposquatMonitor"

    def test_impersonation_detector_status(self) -> None:
        det = ImpersonationDetector()
        status = det.status()
        assert "module" in status
        assert status["module"] == "ImpersonationDetector"

    def test_family_shield_status(self, tmp_path: Path) -> None:
        fs = FamilyShield(config_path=tmp_path / "f.json")
        fs.add_member("Alice", emails=["a@x.com"])
        status = fs.status()
        assert status["member_count"] == 1
        assert status["total_emails"] == 1
