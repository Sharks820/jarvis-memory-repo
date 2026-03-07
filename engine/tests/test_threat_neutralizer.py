"""Tests for ThreatNeutralizer — legal offensive response capabilities.

CFAA-compliant: evidence preservation, automated abuse reporting,
permanent IP blackholing, and law enforcement report generation.
No hack-back.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from unittest.mock import MagicMock, patch

from jarvis_engine.security.alert_chain import AlertChain
from jarvis_engine.security.forensic_logger import ForensicLogger
from jarvis_engine.security.threat_intel import ThreatIntelFeed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_urlopen_response(data: bytes | str, status: int = 200) -> MagicMock:
    """Build a fake urllib response (context-manager compatible)."""
    if isinstance(data, str):
        data = data.encode()
    resp = MagicMock()
    resp.read.return_value = data
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_deps():
    """Create mock dependencies for ThreatNeutralizer."""
    db = sqlite3.connect(":memory:")
    lock = threading.Lock()

    forensic_logger = MagicMock(spec=ForensicLogger)
    forensic_logger.log_event = MagicMock()

    from jarvis_engine.security.ip_tracker import IPTracker
    ip_tracker = IPTracker(db, lock)

    from jarvis_engine.security.attack_memory import AttackPatternMemory
    attack_memory = AttackPatternMemory(db, lock)

    alert_chain = MagicMock(spec=AlertChain)
    alert_chain.send_alert = MagicMock(return_value={"level": 4, "deduped": False})

    threat_intel = MagicMock(spec=ThreatIntelFeed)
    threat_intel.enrich_ip = MagicMock(return_value={
        "ip": "1.2.3.4",
        "is_known_bad": True,
        "abuseipdb_score": 90,
    })

    return {
        "forensic_logger": forensic_logger,
        "ip_tracker": ip_tracker,
        "attack_memory": attack_memory,
        "alert_chain": alert_chain,
        "threat_intel": threat_intel,
    }


# ---------------------------------------------------------------------------
# 1. Full neutralization pipeline
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"ABUSEIPDB_API_KEY": "test-key-123"}, clear=True)
def test_neutralize_full_pipeline():
    """Full pipeline: evidence + block + memory + report + alert."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    deps = _make_deps()
    tn = ThreatNeutralizer(**deps)

    # Mock AbuseIPDB report and RDAP lookup
    abuseipdb_resp = _make_urlopen_response(json.dumps({
        "data": {"abuseConfidenceScore": 100},
    }))
    rdap_resp = _make_urlopen_response(json.dumps({
        "entities": [
            {
                "roles": ["abuse"],
                "vcardArray": [
                    "vcard",
                    [["fn", {}, "text", "Abuse Dept"],
                     ["email", {}, "text", "abuse@example-isp.com"]],
                ],
            }
        ]
    }))

    def _urlopen_router(req, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "abuseipdb" in url:
            return abuseipdb_resp
        if "rdap" in url:
            return rdap_resp
        return _make_urlopen_response("{}")

    with patch("urllib.request.urlopen", side_effect=_urlopen_router):
        result = tn.neutralize(
            ip="203.0.113.50",
            category="brute_force",
            evidence={"payload": "admin/admin attempt", "port": 22},
        )

    # Verify result structure
    assert result["ip"] == "203.0.113.50"
    assert isinstance(result["actions_taken"], list)
    assert len(result["actions_taken"]) >= 3  # evidence, block, memory at minimum
    assert result["blocked"] is True
    assert isinstance(result["reported_to"], list)
    assert isinstance(result["evidence_id"], str)

    # Verify forensic logger was called
    deps["forensic_logger"].log_event.assert_called()

    # Verify IP was blocked via ip_tracker
    assert deps["ip_tracker"].is_blocked("203.0.113.50")

    # Verify alert was sent
    deps["alert_chain"].send_alert.assert_called()


# ---------------------------------------------------------------------------
# 2. Neutralize with minimal (no) dependencies
# ---------------------------------------------------------------------------

def test_neutralize_minimal_deps():
    """With no optional deps, neutralize still returns a valid result."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()  # all deps = None

    result = tn.neutralize(
        ip="10.0.0.1",
        category="port_scan",
        evidence={"ports": [22, 80, 443]},
    )

    assert result["ip"] == "10.0.0.1"
    assert isinstance(result["actions_taken"], list)
    assert result["blocked"] is False  # no ip_tracker, can't block
    assert isinstance(result["reported_to"], list)
    assert isinstance(result["evidence_id"], str)


# ---------------------------------------------------------------------------
# 3. AbuseIPDB report submission
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"ABUSEIPDB_API_KEY": "test-key-abc"}, clear=True)
def test_report_to_abuseipdb():
    """AbuseIPDB report submission with correct format."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()

    mock_resp = _make_urlopen_response(json.dumps({
        "data": {"abuseConfidenceScore": 100},
    }))

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        success = tn.report_to_abuseipdb(
            ip="198.51.100.10",
            categories=[18, 15],
            comment="Brute force SSH attack detected",
        )

    assert success is True
    assert mock_urlopen.called

    # Verify the request was made with correct structure
    call_args = mock_urlopen.call_args
    req = call_args[0][0]
    assert hasattr(req, "full_url")
    assert "abuseipdb" in req.full_url
    assert req.get_header("Key") == "test-key-abc"

    # Verify POST data contains required fields
    body = req.data.decode() if isinstance(req.data, bytes) else req.data
    assert "198.51.100.10" in body
    assert "18" in body


# ---------------------------------------------------------------------------
# 4. AbuseIPDB rate limiting
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {"ABUSEIPDB_API_KEY": "test-key-rate"}, clear=True)
def test_report_rate_limited():
    """Second report for same IP within 1 hour is skipped."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()

    mock_resp = _make_urlopen_response(json.dumps({
        "data": {"abuseConfidenceScore": 100},
    }))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        first = tn.report_to_abuseipdb("198.51.100.20", [18], "First report")
        second = tn.report_to_abuseipdb("198.51.100.20", [18], "Second report")

    assert first is True
    assert second is False  # rate limited


# ---------------------------------------------------------------------------
# 5. RDAP ISP abuse contact lookup
# ---------------------------------------------------------------------------

def test_lookup_isp_abuse():
    """RDAP lookup extracts abuse contact email."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()

    rdap_data = json.dumps({
        "entities": [
            {
                "roles": ["registrant"],
                "vcardArray": [
                    "vcard",
                    [["fn", {}, "text", "Owner Corp"],
                     ["email", {}, "text", "admin@example.com"]],
                ],
            },
            {
                "roles": ["abuse"],
                "vcardArray": [
                    "vcard",
                    [["fn", {}, "text", "Abuse Team"],
                     ["email", {}, "text", "abuse@example-isp.net"]],
                ],
            },
        ]
    })
    mock_resp = _make_urlopen_response(rdap_data)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        email = tn.lookup_isp_abuse_contact("203.0.113.1")

    assert email == "abuse@example-isp.net"


# ---------------------------------------------------------------------------
# 6. RDAP lookup failure — graceful fallback
# ---------------------------------------------------------------------------

def test_lookup_isp_abuse_failure():
    """RDAP lookup failure returns None, no crash."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()

    with patch("urllib.request.urlopen", side_effect=OSError("Timeout")):
        email = tn.lookup_isp_abuse_contact("203.0.113.2")

    assert email is None


# ---------------------------------------------------------------------------
# 7. Law enforcement package generation
# ---------------------------------------------------------------------------

def test_generate_law_enforcement_package():
    """Law enforcement package has all required IC3/FBI fields."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()

    evidence = {
        "payload": "SQL injection attempt",
        "method": "POST",
        "path": "/api/login",
        "timestamp": "2026-03-01T12:00:00Z",
        "user_agent": "Mozilla/5.0",
    }

    package = tn.generate_law_enforcement_package(
        ip="198.51.100.50",
        evidence=evidence,
    )

    assert package["ip"] == "198.51.100.50"
    assert isinstance(package["summary"], str)
    assert len(package["summary"]) > 0
    assert isinstance(package["attack_timeline"], list)
    assert isinstance(package["evidence_hashes"], dict)
    assert isinstance(package["recommended_charges"], list)
    assert isinstance(package["report_template"], str)
    assert "198.51.100.50" in package["report_template"]


# ---------------------------------------------------------------------------
# 8. Permanent IP block
# ---------------------------------------------------------------------------

def test_permanent_block():
    """permanent_block adds IP to blocklist via ip_tracker."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    db = sqlite3.connect(":memory:")
    lock = threading.Lock()
    from jarvis_engine.security.ip_tracker import IPTracker
    ip_tracker = IPTracker(db, lock)

    tn = ThreatNeutralizer(ip_tracker=ip_tracker)

    tn.permanent_block("192.0.2.99", reason="Persistent brute force")

    assert ip_tracker.is_blocked("192.0.2.99")


# ---------------------------------------------------------------------------
# 9. Permanent block without ip_tracker — no crash
# ---------------------------------------------------------------------------

def test_permanent_block_no_tracker():
    """permanent_block without ip_tracker does not crash."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()  # no ip_tracker
    # Should not raise — gracefully handles missing tracker
    tn.permanent_block("10.0.0.1", reason="test")
    assert tn is not None  # completed without exception


# ---------------------------------------------------------------------------
# 10. Status report structure
# ---------------------------------------------------------------------------

def test_status_report():
    """status() returns expected structure with counters."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()

    st = tn.status()

    assert "total_neutralized" in st
    assert "total_reported" in st
    assert "total_blocked" in st
    assert "recent_actions" in st
    assert isinstance(st["total_neutralized"], int)
    assert isinstance(st["total_reported"], int)
    assert isinstance(st["total_blocked"], int)
    assert isinstance(st["recent_actions"], list)
    assert st["total_neutralized"] == 0


# ---------------------------------------------------------------------------
# 11. Status counters increment after neutralize
# ---------------------------------------------------------------------------

def test_status_increments():
    """Counters increase after neutralization actions."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    deps = _make_deps()
    tn = ThreatNeutralizer(**deps)

    tn.neutralize("10.0.0.1", "brute_force", {"payload": "test"})
    tn.neutralize("10.0.0.2", "port_scan", {"ports": [22]})

    st = tn.status()
    assert st["total_neutralized"] == 2
    assert st["total_blocked"] == 2  # ip_tracker present -> blocks
    assert len(st["recent_actions"]) == 2


# ---------------------------------------------------------------------------
# 12. AbuseIPDB report without API key — returns False
# ---------------------------------------------------------------------------

@patch.dict("os.environ", {}, clear=True)
def test_report_to_abuseipdb_no_key():
    """Without ABUSEIPDB_API_KEY, report returns False immediately."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    tn = ThreatNeutralizer()
    result = tn.report_to_abuseipdb("1.2.3.4", [18], "test")
    assert result is False


# ---------------------------------------------------------------------------
# 13. Thread safety — concurrent neutralize calls
# ---------------------------------------------------------------------------

def test_thread_safety():
    """Concurrent neutralize calls from multiple threads don't crash."""
    from jarvis_engine.security.threat_neutralizer import ThreatNeutralizer

    deps = _make_deps()
    tn = ThreatNeutralizer(**deps)
    errors: list[Exception] = []

    def _neutralize(i: int) -> None:
        try:
            tn.neutralize(f"10.0.0.{i}", "scan", {"port": i})
        except (RuntimeError, ValueError, OSError) as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_neutralize, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0
    st = tn.status()
    assert st["total_neutralized"] == 20
