"""Tests for ThreatIntelFeed — threat intelligence enrichment."""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch


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


# ---------------------------------------------------------------------------
# 1. No API keys configured — minimal enrichment
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
def test_enrich_ip_no_keys():
    """Without any API keys, enrichment still returns a valid dict."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    feed = ThreatIntelFeed(cache_ttl=3600)
    result = feed.enrich_ip("1.2.3.4")

    assert result["ip"] == "1.2.3.4"
    assert result["abuseipdb_score"] is None
    assert result["otx_pulses"] is None
    assert result["is_known_bad"] is False
    assert result["cache_hit"] is False
    # sources_checked should be a list (may include feodo even without keys)
    assert isinstance(result["sources_checked"], list)


# ---------------------------------------------------------------------------
# 2. Cached result
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
def test_enrich_ip_cached():
    """Second call for same IP returns cache_hit=True."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    feed = ThreatIntelFeed(cache_ttl=3600)
    first = feed.enrich_ip("10.0.0.1")
    second = feed.enrich_ip("10.0.0.1")

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["ip"] == "10.0.0.1"


# ---------------------------------------------------------------------------
# 3. Cache expiry
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
def test_cache_expiry():
    """After TTL expires, cache entry is stale and re-fetched."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    feed = ThreatIntelFeed(cache_ttl=60)

    with patch("time.time", return_value=1000.0):
        first = feed.enrich_ip("192.168.1.1")
    assert first["cache_hit"] is False

    # Still within TTL
    with patch("time.time", return_value=1050.0):
        second = feed.enrich_ip("192.168.1.1")
    assert second["cache_hit"] is True

    # After TTL
    with patch("time.time", return_value=1061.0):
        third = feed.enrich_ip("192.168.1.1")
    assert third["cache_hit"] is False


# ---------------------------------------------------------------------------
# 4. AbuseIPDB integration (mocked)
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"ABUSEIPDB_API_KEY": "test-key-123"}, clear=True)
def test_abuseipdb_integration():
    """AbuseIPDB query is issued with correct headers and parsed."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    abuseipdb_response = json.dumps(
        {
            "data": {
                "ipAddress": "8.8.8.8",
                "abuseConfidenceScore": 42,
                "totalReports": 7,
            }
        }
    )

    mock_resp = _make_urlopen_response(abuseipdb_response)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        feed = ThreatIntelFeed(cache_ttl=3600)
        result = feed.enrich_ip("8.8.8.8")

    # Verify the request was made (may have multiple calls: AbuseIPDB + Feodo)
    assert mock_urlopen.called
    # Find the AbuseIPDB call (not the Feodo blocklist call)
    abuseipdb_req = None
    for call in mock_urlopen.call_args_list:
        req = call[0][0]
        if hasattr(req, "full_url") and "abuseipdb" in req.full_url:
            abuseipdb_req = req
            break
    assert abuseipdb_req is not None, "AbuseIPDB request not found"
    assert abuseipdb_req.get_header("Key") == "test-key-123"
    assert "8.8.8.8" in abuseipdb_req.full_url

    assert result["abuseipdb_score"] == 42
    assert "abuseipdb" in result["sources_checked"]


# ---------------------------------------------------------------------------
# 5. OTX integration (mocked)
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"OTX_API_KEY": "otx-key-456"}, clear=True)
def test_otx_integration():
    """OTX query is issued with correct headers and parsed."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    otx_response = json.dumps(
        {
            "pulse_info": {
                "count": 3,
                "pulses": [
                    {"name": "Botnet C2"},
                    {"name": "Malware Distribution"},
                    {"name": "Phishing Campaign"},
                ],
            }
        }
    )

    mock_resp = _make_urlopen_response(otx_response)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        feed = ThreatIntelFeed(cache_ttl=3600)
        result = feed.enrich_ip("6.6.6.6")

    # Find OTX call among all urlopen calls
    otx_req = None
    for call in mock_urlopen.call_args_list:
        req = call[0][0]
        if hasattr(req, "full_url") and "otx.alienvault" in req.full_url:
            otx_req = req
            break
    assert otx_req is not None, "OTX request not found"

    assert result["otx_pulses"] == 3
    assert "otx" in result["sources_checked"]


# ---------------------------------------------------------------------------
# 6. Feodo Tracker blocklist
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
def test_feodo_blocklist():
    """IPs in the Feodo blocklist CSV are detected."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    blocklist_csv = (
        "# Feodo Tracker Blocklist\n# comment line\n1.2.3.4\n5.6.7.8\n9.10.11.12\n"
    )
    mock_resp = _make_urlopen_response(blocklist_csv)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        feed = ThreatIntelFeed(cache_ttl=3600)
        result = feed.enrich_ip("5.6.7.8")

    assert result["feodo_listed"] is True
    assert result["is_known_bad"] is True


# ---------------------------------------------------------------------------
# 7. Feodo blocklist — IP not listed
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
def test_feodo_blocklist_not_listed():
    """IPs NOT in the Feodo blocklist CSV are correctly identified as clean."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    blocklist_csv = "# Feodo Tracker Blocklist\n1.2.3.4\n5.6.7.8\n"
    mock_resp = _make_urlopen_response(blocklist_csv)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        feed = ThreatIntelFeed(cache_ttl=3600)
        result = feed.enrich_ip("10.0.0.1")

    assert result["feodo_listed"] is False


# ---------------------------------------------------------------------------
# 8. is_known_bad convenience method
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
def test_is_known_bad():
    """is_known_bad returns True for IPs flagged in any source."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    blocklist_csv = "# header\n203.0.113.1\n"
    mock_resp = _make_urlopen_response(blocklist_csv)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        feed = ThreatIntelFeed(cache_ttl=3600)
        assert feed.is_known_bad("203.0.113.1") is True
        assert feed.is_known_bad("203.0.113.2") is False


# ---------------------------------------------------------------------------
# 9. status() report structure
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"ABUSEIPDB_API_KEY": "k1", "OTX_API_KEY": "k2"}, clear=True)
def test_status_report():
    """status() returns expected structure."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    feed = ThreatIntelFeed(cache_ttl=3600)
    st = feed.status()

    assert "cache_size" in st
    assert st["cache_size"] == 0
    assert "api_keys_configured" in st
    assert "abuseipdb" in st["api_keys_configured"]
    assert "otx" in st["api_keys_configured"]
    assert "last_feed_update" in st
    assert "requests_total" in st
    assert isinstance(st["requests_total"], int)


# ---------------------------------------------------------------------------
# 10. Network error handling — graceful degradation
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"ABUSEIPDB_API_KEY": "k1"}, clear=True)
def test_network_error_graceful():
    """Network failures don't crash enrichment; return partial data."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    with patch(
        "urllib.request.urlopen",
        side_effect=ConnectionRefusedError("Connection refused"),
    ):
        feed = ThreatIntelFeed(cache_ttl=3600)
        result = feed.enrich_ip("1.1.1.1")

    assert result["ip"] == "1.1.1.1"
    assert result["abuseipdb_score"] is None
    assert result["is_known_bad"] is False


# ---------------------------------------------------------------------------
# 11. Thread safety — concurrent enrichments
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {}, clear=True)
def test_thread_safety():
    """Concurrent enrichments from multiple threads don't crash."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    feed = ThreatIntelFeed(cache_ttl=3600)
    errors: list[Exception] = []

    def _enrich(ip: str) -> None:
        try:
            feed.enrich_ip(ip)
        except (RuntimeError, ValueError, OSError) as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_enrich, args=(f"10.0.0.{i}",)) for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(errors) == 0


# ---------------------------------------------------------------------------
# 12. AbuseIPDB high score marks is_known_bad
# ---------------------------------------------------------------------------


@patch.dict("os.environ", {"ABUSEIPDB_API_KEY": "test-key"}, clear=True)
def test_abuseipdb_high_score_marks_known_bad():
    """An AbuseIPDB confidence score >= 80 should flag is_known_bad."""
    from jarvis_engine.security.threat_intel import ThreatIntelFeed

    abuseipdb_response = json.dumps(
        {
            "data": {
                "ipAddress": "45.33.32.156",
                "abuseConfidenceScore": 95,
                "totalReports": 50,
            }
        }
    )
    mock_resp = _make_urlopen_response(abuseipdb_response)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        feed = ThreatIntelFeed(cache_ttl=3600)
        result = feed.enrich_ip("45.33.32.156")

    assert result["abuseipdb_score"] == 95
    assert result["is_known_bad"] is True
