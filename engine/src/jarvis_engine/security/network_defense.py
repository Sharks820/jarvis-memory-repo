"""Home network security — ARP monitoring, DNS analysis, connection tracking.

Provides:
- ``KnownDeviceRegistry``: persistent JSON registry of approved network devices
- ``HomeNetworkMonitor``: scans ARP table for rogue devices, analyses DNS cache
  for DGA/C2 indicators, and monitors active connections for suspicious activity

All subprocess calls are wrapped in try/except to degrade gracefully when
system commands are unavailable.
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Shannon entropy threshold for flagging potential DGA domains.
_DGA_ENTROPY_THRESHOLD = 3.5

# Ports commonly associated with reverse shells / C2 traffic.
_SUSPICIOUS_PORTS = frozenset({
    4444,   # Metasploit default
    5555,   # Android ADB / various RATs
    6666,   # IRC backdoors
    6667,   # IRC
    8443,   # Alt HTTPS (some C2)
    31337,  # Back Orifice
    12345,  # NetBus
    54321,  # Back Orifice 2000
    1337,   # waste
    9001,   # Tor default ORPort
    9050,   # Tor SOCKS
    9051,   # Tor control
})

# MAC for broadcast — always skip in "unknown device" alerts.
_BROADCAST_MACS = frozenset({
    "ff:ff:ff:ff:ff:ff",
    "00:00:00:00:00:00",
})

# Regex: IP address (v4)
_IP_RE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")

# Regex: MAC address (Windows uses dashes, Linux uses colons)
_MAC_RE = re.compile(r"([0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-]"
                      r"[0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_mac(mac: str) -> str:
    """Normalize a MAC to lowercase colon-separated format."""
    return mac.lower().replace("-", ":")


def _shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of string *s*."""
    if not s:
        return 0.0
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _run_command(args: list[str]) -> str:
    """Run a subprocess command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
        )
        return result.stdout or ""
    except Exception:
        logger.debug("Command %s failed", args, exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# KnownDeviceRegistry
# ---------------------------------------------------------------------------

class KnownDeviceRegistry:
    """Persistent JSON registry of approved network devices.

    Parameters
    ----------
    registry_path:
        Path to the JSON file storing the device registry.
    """

    def __init__(self, registry_path: Path) -> None:
        self._path = registry_path
        self._lock = threading.Lock()
        self._devices: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        """Load registry from disk (if exists)."""
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    self._devices = data
            except Exception:
                logger.warning("Failed to load device registry from %s", self._path, exc_info=True)

    def _save(self) -> None:
        """Persist registry to disk.  Must be called while holding ``_lock``."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._devices, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("Failed to save device registry to %s", self._path, exc_info=True)

    # -- public API ---------------------------------------------------------

    def register_device(
        self, mac: str, name: str, device_type: str = "unknown"
    ) -> None:
        """Add or update an approved device."""
        key = _normalize_mac(mac)
        with self._lock:
            self._devices[key] = {
                "mac": key,
                "name": name,
                "device_type": device_type,
                "registered_at": time.time(),
            }
            self._save()

    def is_known(self, mac: str) -> bool:
        """Return ``True`` if *mac* is in the registry."""
        key = _normalize_mac(mac)
        with self._lock:
            return key in self._devices

    def get_device(self, mac: str) -> dict[str, Any] | None:
        """Return device info dict or ``None``."""
        key = _normalize_mac(mac)
        with self._lock:
            entry = self._devices.get(key)
            return dict(entry) if entry else None

    def list_devices(self) -> list[dict[str, Any]]:
        """Return list of all registered devices."""
        with self._lock:
            return [dict(v) for v in self._devices.values()]

    def remove_device(self, mac: str) -> None:
        """Remove a device from the registry."""
        key = _normalize_mac(mac)
        with self._lock:
            self._devices.pop(key, None)
            self._save()


# ---------------------------------------------------------------------------
# HomeNetworkMonitor
# ---------------------------------------------------------------------------

class HomeNetworkMonitor:
    """Monitor home network for rogue devices, DGA domains, and suspicious connections.

    Parameters
    ----------
    device_registry:
        Optional ``KnownDeviceRegistry`` for identifying trusted devices.
    alert_callback:
        Optional callable invoked with an alert dict when suspicious activity
        is detected.
    """

    def __init__(
        self,
        device_registry: KnownDeviceRegistry | None = None,
        alert_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._registry = device_registry
        self._alert_callback = alert_callback
        self._lock = threading.Lock()

        # Counters updated by scans.
        self._last_scan_time: float | None = None
        self._unknown_devices: int = 0
        self._suspicious_connections: int = 0
        self._dga_candidates: int = 0

    # ------------------------------------------------------------------
    # Alert helper
    # ------------------------------------------------------------------

    def _fire_alert(self, alert: dict[str, Any]) -> None:
        if self._alert_callback:
            try:
                self._alert_callback(alert)
            except Exception:
                logger.debug("Alert callback failed", exc_info=True)

    # ------------------------------------------------------------------
    # ARP scanning
    # ------------------------------------------------------------------

    def scan_arp_table(self) -> list[dict[str, Any]]:
        """Parse the system ARP table and return a list of entries.

        Each entry: ``{ip, mac, interface, unknown: bool, arp_poisoning: bool}``.
        """
        try:
            if sys.platform == "win32":
                raw = _run_command(["arp", "-a"])
            else:
                raw = _run_command(["ip", "neigh"])
        except Exception:
            logger.debug("ARP scan failed", exc_info=True)
            return []

        if not raw:
            return []

        entries = self._parse_arp_output(raw)
        self._detect_arp_anomalies(entries)

        # Update status counter.
        with self._lock:
            self._unknown_devices = sum(1 for e in entries if e.get("unknown"))

        return entries

    def _parse_arp_output(self, raw: str) -> list[dict[str, Any]]:
        """Extract (ip, mac, interface) tuples from ARP command output."""
        entries: list[dict[str, Any]] = []
        current_interface = ""

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue

            # Windows interface header: "Interface: 192.168.1.100 --- 0x5"
            if line.lower().startswith("interface:"):
                m = _IP_RE.search(line)
                if m:
                    current_interface = m.group(1)
                continue

            # Skip header lines
            if "Internet Address" in line or "Physical Address" in line:
                continue

            ip_match = _IP_RE.search(line)
            mac_match = _MAC_RE.search(line)
            if ip_match and mac_match:
                mac = _normalize_mac(mac_match.group(1))

                # Linux: interface after "dev"
                interface = current_interface
                dev_match = re.search(r"\bdev\s+(\S+)", line)
                if dev_match:
                    interface = dev_match.group(1)

                entries.append({
                    "ip": ip_match.group(1),
                    "mac": mac,
                    "interface": interface,
                    "unknown": False,
                    "arp_poisoning": False,
                })

        return entries

    def _detect_arp_anomalies(self, entries: list[dict[str, Any]]) -> None:
        """Flag unknown devices and ARP poisoning in entry list (mutates)."""
        # Single-pass: build maps and detect anomalies together.
        ip_to_macs: dict[str, set[str]] = {}
        mac_to_ips: dict[str, set[str]] = {}

        for entry in entries:
            ip = entry["ip"]
            mac = entry["mac"]
            ip_to_macs.setdefault(ip, set()).add(mac)
            mac_to_ips.setdefault(mac, set()).add(ip)

        # Track already-alerted pairs to prevent duplicate alert firing.
        alerted_ip_macs: set[str] = set()
        alerted_mac_ips: set[str] = set()

        for entry in entries:
            mac = entry["mac"]
            ip = entry["ip"]

            # --- Unknown device detection ---
            if mac not in _BROADCAST_MACS:
                if self._registry and not self._registry.is_known(mac):
                    entry["unknown"] = True
                    self._fire_alert({
                        "type": "unknown_device",
                        "mac": mac,
                        "ip": ip,
                        "message": f"Unknown device {mac} at {ip}",
                        "timestamp": time.time(),
                    })

            # --- ARP poisoning: multiple MACs for same IP ---
            if len(ip_to_macs.get(ip, set())) > 1:
                entry["arp_poisoning"] = True
                if ip not in alerted_ip_macs:
                    alerted_ip_macs.add(ip)
                    self._fire_alert({
                        "type": "arp_poisoning",
                        "ip": ip,
                        "macs": sorted(ip_to_macs[ip]),
                        "message": f"ARP poisoning suspected: {ip} resolves to multiple MACs",
                        "timestamp": time.time(),
                    })

            # --- ARP poisoning: multiple IPs for same MAC (not broadcast) ---
            if mac not in _BROADCAST_MACS and len(mac_to_ips.get(mac, set())) > 1:
                entry["arp_poisoning"] = True
                if mac not in alerted_mac_ips:
                    alerted_mac_ips.add(mac)
                    self._fire_alert({
                        "type": "arp_poisoning",
                        "mac": mac,
                        "ips": sorted(mac_to_ips[mac]),
                        "message": f"ARP anomaly: {mac} claims multiple IPs",
                        "timestamp": time.time(),
                    })

    # ------------------------------------------------------------------
    # DNS cache analysis
    # ------------------------------------------------------------------

    def analyze_dns_cache(self) -> dict[str, Any]:
        """Analyze the DNS cache for DGA / C2 indicators.

        Only works on Windows (``ipconfig /displaydns``).  Returns a dict:
        ``{total_entries, suspicious_domains, dga_candidates, entropy_alerts}``.
        """
        result: dict[str, Any] = {
            "total_entries": 0,
            "suspicious_domains": [],
            "dga_candidates": [],
            "entropy_alerts": [],
        }

        if sys.platform != "win32":
            return result

        try:
            raw = _run_command(["ipconfig", "/displaydns"])
        except Exception:
            logger.debug("DNS cache scan failed", exc_info=True)
            return result

        if not raw:
            return result

        # Parse "Record Name" lines.
        domains: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if line.lower().startswith("record name"):
                # "Record Name . . . . . : example.com"
                parts = line.split(":", 1)
                if len(parts) == 2:
                    domain = parts[1].strip()
                    if domain:
                        domains.append(domain)

        result["total_entries"] = len(domains)

        # Entropy analysis.
        for domain in domains:
            # Use only the first label (before first dot) for entropy calc.
            label = domain.split(".")[0]
            entropy = _shannon_entropy(label)

            if entropy > _DGA_ENTROPY_THRESHOLD:
                entry = {"domain": domain, "entropy": round(entropy, 3)}
                result["dga_candidates"].append(entry)
                result["entropy_alerts"].append(entry)
                result["suspicious_domains"].append(domain)

        with self._lock:
            self._dga_candidates = len(result["dga_candidates"])

        return result

    # ------------------------------------------------------------------
    # Connection monitoring
    # ------------------------------------------------------------------

    def check_connections(self) -> list[dict[str, Any]]:
        """List active network connections and flag suspicious ones.

        Returns list of ``{local_addr, remote_addr, state, pid, process,
        suspicious: bool, reason}``.
        """
        try:
            if sys.platform == "win32":
                return self._check_connections_windows()
            else:
                return self._check_connections_linux()
        except Exception:
            logger.debug("Connection check failed", exc_info=True)
            return []

    def _check_connections_windows(self) -> list[dict[str, Any]]:
        """Parse ``netstat -ano`` on Windows."""
        raw = _run_command(["netstat", "-ano"])
        if not raw:
            return []

        # Build PID -> process name map via tasklist.
        pid_map = self._get_pid_map_windows()

        conns: list[dict[str, Any]] = []
        for line in raw.splitlines():
            line = line.strip()
            # Example: TCP    192.168.1.100:55432    93.184.216.34:443      ESTABLISHED     1234
            parts = line.split()
            if len(parts) < 5:
                continue
            proto = parts[0].upper()
            if proto not in ("TCP", "UDP"):
                continue

            local_addr = parts[1]
            remote_addr = parts[2]
            state = parts[3] if len(parts) >= 5 and not parts[3].isdigit() else ""
            pid_str = parts[-1]

            try:
                pid = int(pid_str)
            except ValueError:
                pid = 0

            process = pid_map.get(pid, "unknown")
            suspicious, reason = self._assess_connection(remote_addr)

            conns.append({
                "local_addr": local_addr,
                "remote_addr": remote_addr,
                "state": state,
                "pid": pid,
                "process": process,
                "suspicious": suspicious,
                "reason": reason,
            })

        with self._lock:
            self._suspicious_connections = sum(1 for c in conns if c["suspicious"])

        return conns

    def _check_connections_linux(self) -> list[dict[str, Any]]:
        """Parse ``ss -tupn`` on Linux."""
        raw = _run_command(["ss", "-tupn"])
        if not raw:
            return []

        conns: list[dict[str, Any]] = []
        for line in raw.splitlines():
            line = line.strip()
            # Skip header
            if line.startswith("Netid") or not line:
                continue

            parts = line.split()
            if len(parts) < 6:
                continue

            state = parts[1]
            local_addr = parts[4]
            remote_addr = parts[5]

            # Extract PID from process column: users:(("name",pid=1234,fd=50))
            pid = 0
            process = "unknown"
            pid_match = re.search(r'pid=(\d+)', line)
            name_match = re.search(r'"([^"]+)"', line)
            if pid_match:
                pid = int(pid_match.group(1))
            if name_match:
                process = name_match.group(1)

            suspicious, reason = self._assess_connection(remote_addr)

            conns.append({
                "local_addr": local_addr,
                "remote_addr": remote_addr,
                "state": state,
                "pid": pid,
                "process": process,
                "suspicious": suspicious,
                "reason": reason,
            })

        with self._lock:
            self._suspicious_connections = sum(1 for c in conns if c["suspicious"])

        return conns

    def _get_pid_map_windows(self) -> dict[int, str]:
        """Build a PID -> process name map from ``tasklist``."""
        raw = _run_command(["tasklist"])
        if not raw:
            return {}

        pid_map: dict[int, str] = {}
        for line in raw.splitlines():
            # Tasklist format:  Image Name ... PID ...
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pid = int(parts[1])
                    pid_map[pid] = parts[0]
                except ValueError:
                    continue
        return pid_map

    def _assess_connection(self, remote_addr: str) -> tuple[bool, str]:
        """Assess whether a remote address is suspicious.

        Returns ``(is_suspicious, reason)`` tuple.
        """
        # Extract port from address (format "ip:port" or "[ipv6]:port").
        port = 0
        if ":" in remote_addr:
            port_str = remote_addr.rsplit(":", 1)[-1]
            try:
                port = int(port_str)
            except ValueError:
                pass

        # Check for known-bad ports.
        if port in _SUSPICIOUS_PORTS:
            return True, f"Known suspicious port {port}"

        return False, ""

    # ------------------------------------------------------------------
    # Full scan & status
    # ------------------------------------------------------------------

    def full_scan(self) -> dict[str, Any]:
        """Run ARP, DNS, and connection scans. Return aggregated results."""
        arp_results = self.scan_arp_table()
        dns_results = self.analyze_dns_cache()
        conn_results = self.check_connections()

        with self._lock:
            self._last_scan_time = time.time()

        return {
            "arp": arp_results,
            "dns": dns_results,
            "connections": conn_results,
            "scan_time": self._last_scan_time,
        }

    def status(self) -> dict[str, Any]:
        """Return current monitor status."""
        with self._lock:
            return {
                "last_scan_time": self._last_scan_time,
                "unknown_devices": self._unknown_devices,
                "suspicious_connections": self._suspicious_connections,
                "dga_candidates": self._dga_candidates,
            }
