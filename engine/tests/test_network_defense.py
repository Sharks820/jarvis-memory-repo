"""Tests for HomeNetworkMonitor and KnownDeviceRegistry."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis_engine.security.network_defense import (
    HomeNetworkMonitor,
    KnownDeviceRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_registry(tmp_path: Path) -> KnownDeviceRegistry:
    """Return a KnownDeviceRegistry backed by a temp file."""
    return KnownDeviceRegistry(tmp_path / "devices.json")


@pytest.fixture
def monitor(tmp_path: Path) -> HomeNetworkMonitor:
    """Return a HomeNetworkMonitor with a fresh device registry."""
    reg = KnownDeviceRegistry(tmp_path / "devices.json")
    reg.register_device("aa:bb:cc:dd:ee:ff", "Router", "router")
    reg.register_device("11:22:33:44:55:66", "Desktop", "pc")
    return HomeNetworkMonitor(device_registry=reg)


# ---------------------------------------------------------------------------
# Sample command outputs
# ---------------------------------------------------------------------------

WINDOWS_ARP_OUTPUT = """\
Interface: 192.168.1.100 --- 0x5
  Internet Address      Physical Address      Type
  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic
  192.168.1.50          11-22-33-44-55-66     dynamic
  192.168.1.99          de-ad-be-ef-00-01     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
"""

LINUX_ARP_OUTPUT = """\
192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
192.168.1.50 dev eth0 lladdr 11:22:33:44:55:66 STALE
192.168.1.99 dev eth0 lladdr de:ad:be:ef:00:01 REACHABLE
"""

WINDOWS_DNS_OUTPUT = """\
Windows IP Configuration

    Record Name . . . . . : google.com
    Record Type . . . . . : 1
    Time To Live  . . . . : 200
    Data Length . . . . . : 4
    Section . . . . . . . : Answer
    A (Host) Record . . . : 142.250.80.46

    Record Name . . . . . : xj3k9q2m7zlpwvnt.biz
    Record Type . . . . . : 1
    Time To Live  . . . . : 30
    Data Length . . . . . : 4
    Section . . . . . . . : Answer
    A (Host) Record . . . : 10.99.1.5

    Record Name . . . . . : a8f3x2q9z7m1b4k6.xyz
    Record Type . . . . . : 1
    Time To Live  . . . . : 30
    Data Length . . . . . : 4
    Section . . . . . . . : Answer
    A (Host) Record . . . : 10.99.1.6

    Record Name . . . . . : github.com
    Record Type . . . . . : 1
    Time To Live  . . . . : 60
    Data Length . . . . . : 4
    Section . . . . . . . : Answer
    A (Host) Record . . . : 140.82.121.4
"""

WINDOWS_NETSTAT_OUTPUT = """\
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    192.168.1.100:55432    93.184.216.34:443      ESTABLISHED     1234
  TCP    192.168.1.100:55433    10.0.0.5:8080          ESTABLISHED     5678
  TCP    192.168.1.100:55434    198.51.100.1:4444      ESTABLISHED     9999
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       900
"""

LINUX_SS_OUTPUT = """\
Netid State  Recv-Q Send-Q  Local Address:Port   Peer Address:Port  Process
tcp   ESTAB  0      0       192.168.1.100:55432  93.184.216.34:443  users:(("firefox",pid=1234,fd=50))
tcp   ESTAB  0      0       192.168.1.100:55433  10.0.0.5:8080      users:(("python",pid=5678,fd=12))
tcp   ESTAB  0      0       192.168.1.100:55434  198.51.100.1:4444  users:(("unknown",pid=9999,fd=3))
"""


# ---------------------------------------------------------------------------
# KnownDeviceRegistry tests
# ---------------------------------------------------------------------------

class TestKnownDeviceRegistry:
    def test_register_and_check(self, tmp_registry: KnownDeviceRegistry) -> None:
        """Register device, is_known returns True."""
        tmp_registry.register_device("aa:bb:cc:dd:ee:ff", "Router", "router")
        assert tmp_registry.is_known("aa:bb:cc:dd:ee:ff")
        assert tmp_registry.is_known("AA:BB:CC:DD:EE:FF")  # case-insensitive

    def test_persist(self, tmp_path: Path) -> None:
        """Save and reload registry from JSON."""
        path = tmp_path / "devices.json"
        reg = KnownDeviceRegistry(path)
        reg.register_device("aa:bb:cc:dd:ee:ff", "Laptop", "laptop")
        reg.register_device("11:22:33:44:55:66", "Phone", "phone")

        # Create a new instance from the same path
        reg2 = KnownDeviceRegistry(path)
        assert reg2.is_known("aa:bb:cc:dd:ee:ff")
        assert reg2.is_known("11:22:33:44:55:66")
        assert len(reg2.list_devices()) == 2

    def test_unknown_device(self, tmp_registry: KnownDeviceRegistry) -> None:
        """Unregistered MAC returns False."""
        assert not tmp_registry.is_known("de:ad:be:ef:00:01")
        assert tmp_registry.get_device("de:ad:be:ef:00:01") is None

    def test_remove_device(self, tmp_registry: KnownDeviceRegistry) -> None:
        """Remove device from registry."""
        tmp_registry.register_device("aa:bb:cc:dd:ee:ff", "Router", "router")
        assert tmp_registry.is_known("aa:bb:cc:dd:ee:ff")
        tmp_registry.remove_device("aa:bb:cc:dd:ee:ff")
        assert not tmp_registry.is_known("aa:bb:cc:dd:ee:ff")

    def test_get_device(self, tmp_registry: KnownDeviceRegistry) -> None:
        """get_device returns full info dict."""
        tmp_registry.register_device("aa:bb:cc:dd:ee:ff", "Router", "router")
        info = tmp_registry.get_device("aa:bb:cc:dd:ee:ff")
        assert info is not None
        assert info["name"] == "Router"
        assert info["device_type"] == "router"
        assert "registered_at" in info

    def test_list_devices(self, tmp_registry: KnownDeviceRegistry) -> None:
        """list_devices returns all registered devices."""
        tmp_registry.register_device("aa:bb:cc:dd:ee:ff", "Router", "router")
        tmp_registry.register_device("11:22:33:44:55:66", "Phone", "phone")
        devices = tmp_registry.list_devices()
        assert len(devices) == 2
        macs = {d["mac"] for d in devices}
        assert "aa:bb:cc:dd:ee:ff" in macs
        assert "11:22:33:44:55:66" in macs

    def test_thread_safety(self, tmp_path: Path) -> None:
        """Concurrent register/read should not corrupt state."""
        reg = KnownDeviceRegistry(tmp_path / "devices.json")
        errors: list[str] = []

        def register_batch(start: int) -> None:
            try:
                for i in range(start, start + 20):
                    mac = f"00:00:00:00:{i:02x}:{i:02x}"
                    reg.register_device(mac, f"Device-{i}", "test")
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=register_batch, args=(n * 20,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(reg.list_devices()) == 80


# ---------------------------------------------------------------------------
# HomeNetworkMonitor — ARP scanning
# ---------------------------------------------------------------------------

class TestARPScanning:
    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_scan_arp_table_windows(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """Parse Windows arp -a output correctly."""
        mock_sys.platform = "win32"
        mock_subprocess.run.return_value = MagicMock(
            stdout=WINDOWS_ARP_OUTPUT, returncode=0
        )
        results = monitor.scan_arp_table()
        # Should find 4 entries (3 dynamic + 1 static)
        ips = {r["ip"] for r in results}
        assert "192.168.1.1" in ips
        assert "192.168.1.50" in ips
        assert "192.168.1.99" in ips

    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_scan_arp_table_linux(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """Parse Linux ip neigh output correctly."""
        mock_sys.platform = "linux"
        mock_subprocess.run.return_value = MagicMock(
            stdout=LINUX_ARP_OUTPUT, returncode=0
        )
        results = monitor.scan_arp_table()
        ips = {r["ip"] for r in results}
        assert "192.168.1.1" in ips
        assert "192.168.1.50" in ips
        assert "192.168.1.99" in ips

    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_arp_unknown_device_detected(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """Unknown MAC triggers alert."""
        mock_sys.platform = "win32"
        mock_subprocess.run.return_value = MagicMock(
            stdout=WINDOWS_ARP_OUTPUT, returncode=0
        )
        alerts: list[dict] = []
        monitor._alert_callback = lambda alert: alerts.append(alert)

        results = monitor.scan_arp_table()

        # de:ad:be:ef:00:01 is NOT in the registry -> should be flagged
        unknown = [r for r in results if r.get("unknown")]
        assert len(unknown) >= 1
        unknown_macs = {r["mac"] for r in unknown}
        assert "de:ad:be:ef:00:01" in unknown_macs

        # Alert callback should have fired
        assert len(alerts) >= 1
        assert any("unknown" in a.get("type", "").lower() for a in alerts)

    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_arp_poisoning_detection(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """Detect ARP poisoning: multiple MACs for same IP."""
        poisoned_output = """\
Interface: 192.168.1.100 --- 0x5
  Internet Address      Physical Address      Type
  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic
  192.168.1.1           de-ad-be-ef-00-01     dynamic
"""
        mock_sys.platform = "win32"
        mock_subprocess.run.return_value = MagicMock(
            stdout=poisoned_output, returncode=0
        )
        alerts: list[dict] = []
        monitor._alert_callback = lambda alert: alerts.append(alert)

        results = monitor.scan_arp_table()

        # Should detect ARP poisoning
        poisoned = [r for r in results if r.get("arp_poisoning")]
        assert len(poisoned) >= 1
        # Alert callback should fire for poisoning
        assert any("poison" in a.get("type", "").lower() for a in alerts)


# ---------------------------------------------------------------------------
# HomeNetworkMonitor — DNS analysis
# ---------------------------------------------------------------------------

class TestDNSAnalysis:
    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_dns_entropy_detection(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """High-entropy domains flagged as DGA candidates."""
        mock_sys.platform = "win32"
        mock_subprocess.run.return_value = MagicMock(
            stdout=WINDOWS_DNS_OUTPUT, returncode=0
        )
        result = monitor.analyze_dns_cache()

        assert "total_entries" in result
        assert result["total_entries"] >= 4

        # The random-looking domains should be flagged
        dga = result.get("dga_candidates", [])
        dga_names = {d["domain"] for d in dga}
        assert "xj3k9q2m7zlpwvnt.biz" in dga_names
        assert "a8f3x2q9z7m1b4k6.xyz" in dga_names

        # google.com and github.com should NOT be flagged
        assert "google.com" not in dga_names
        assert "github.com" not in dga_names

    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_dns_graceful_on_linux(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """DNS cache analysis returns empty result on non-Windows (graceful skip)."""
        mock_sys.platform = "linux"
        result = monitor.analyze_dns_cache()
        assert result["total_entries"] == 0
        # subprocess should not have been called for DNS on Linux
        mock_subprocess.run.assert_not_called()


# ---------------------------------------------------------------------------
# HomeNetworkMonitor — connection monitoring
# ---------------------------------------------------------------------------

class TestConnectionMonitoring:
    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_check_connections_windows(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """Parse netstat output and flag suspicious connections."""
        mock_sys.platform = "win32"

        # First call: netstat, second call: tasklist for PID lookup
        tasklist_output = """\
Image Name                     PID Session Name        Session#    Mem Usage
========================= ======== ================ =========== ============
firefox.exe                   1234 Console                    1     150,000 K
python.exe                    5678 Console                    1      50,000 K
suspicious.exe                9999 Console                    1      10,000 K
svchost.exe                    900 Services                   0      20,000 K
"""
        mock_subprocess.run.side_effect = [
            MagicMock(stdout=WINDOWS_NETSTAT_OUTPUT, returncode=0),  # netstat
            MagicMock(stdout=tasklist_output, returncode=0),          # tasklist
        ]

        conns = monitor.check_connections()
        assert len(conns) >= 3  # at least 3 established connections

        # Port 4444 is a suspicious port (common reverse shell)
        suspicious = [c for c in conns if c.get("suspicious")]
        assert len(suspicious) >= 1
        suspicious_addrs = {c["remote_addr"] for c in suspicious}
        assert any("4444" in addr for addr in suspicious_addrs)

    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_check_connections_linux(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """Parse Linux ss output."""
        mock_sys.platform = "linux"
        mock_subprocess.run.return_value = MagicMock(
            stdout=LINUX_SS_OUTPUT, returncode=0
        )
        conns = monitor.check_connections()
        assert len(conns) >= 3
        pids = {c.get("pid") for c in conns}
        assert 1234 in pids or "1234" in pids


# ---------------------------------------------------------------------------
# HomeNetworkMonitor — full scan & status
# ---------------------------------------------------------------------------

class TestFullScanAndStatus:
    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_full_scan(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """full_scan aggregates ARP, DNS, and connection results."""
        mock_sys.platform = "win32"

        tasklist_output = "Image Name PID\nfirefox.exe 1234\n"

        # We need to handle multiple subprocess.run calls:
        # 1. arp -a, 2. ipconfig /displaydns, 3. netstat -ano, 4. tasklist
        mock_subprocess.run.side_effect = [
            MagicMock(stdout=WINDOWS_ARP_OUTPUT, returncode=0),
            MagicMock(stdout=WINDOWS_DNS_OUTPUT, returncode=0),
            MagicMock(stdout=WINDOWS_NETSTAT_OUTPUT, returncode=0),
            MagicMock(stdout=tasklist_output, returncode=0),
        ]

        result = monitor.full_scan()

        assert "arp" in result
        assert "dns" in result
        assert "connections" in result
        assert "scan_time" in result
        assert isinstance(result["arp"], list)
        assert isinstance(result["dns"], dict)
        assert isinstance(result["connections"], list)

    def test_status_report(self, monitor: HomeNetworkMonitor) -> None:
        """status() returns expected structure."""
        status = monitor.status()

        assert "last_scan_time" in status
        assert "unknown_devices" in status
        assert "suspicious_connections" in status
        assert "dga_candidates" in status
        assert isinstance(status["unknown_devices"], int)
        assert isinstance(status["suspicious_connections"], int)
        assert isinstance(status["dga_candidates"], int)

    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_status_updates_after_scan(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """Status counters update after a full scan."""
        mock_sys.platform = "win32"
        tasklist_output = "Image Name PID\nfirefox.exe 1234\n"
        mock_subprocess.run.side_effect = [
            MagicMock(stdout=WINDOWS_ARP_OUTPUT, returncode=0),
            MagicMock(stdout=WINDOWS_DNS_OUTPUT, returncode=0),
            MagicMock(stdout=WINDOWS_NETSTAT_OUTPUT, returncode=0),
            MagicMock(stdout=tasklist_output, returncode=0),
        ]

        monitor.full_scan()
        status = monitor.status()

        assert status["last_scan_time"] is not None
        # de:ad:be:ef:00:01 is unknown, ff:ff:ff:ff:ff:ff is broadcast (also unknown)
        assert status["unknown_devices"] >= 1
        assert status["dga_candidates"] >= 2  # two high-entropy domains


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_subprocess_failure_arp(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """ARP scan returns empty list on subprocess failure."""
        mock_sys.platform = "win32"
        mock_subprocess.run.side_effect = OSError("command not found")
        result = monitor.scan_arp_table()
        assert result == []

    @patch("jarvis_engine.security.network_defense.subprocess")
    @patch("jarvis_engine.security.network_defense.sys")
    def test_subprocess_failure_connections(self, mock_sys, mock_subprocess, monitor: HomeNetworkMonitor) -> None:
        """Connection check returns empty list on subprocess failure."""
        mock_sys.platform = "win32"
        mock_subprocess.run.side_effect = OSError("command not found")
        result = monitor.check_connections()
        assert result == []
