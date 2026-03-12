"""Auto-sync orchestrator: relay URL management, sync scheduling, connectivity.

Enables the phone to reach the desktop from ANY network (not just same WiFi)
via a relay URL (Cloudflare Tunnel, Tailscale, ngrok, or custom relay).
The desktop advertises both its LAN URL and relay URL. The phone tries LAN
first (fast, low-latency) and falls back to relay (works anywhere).

Also provides sync scheduling hints so the phone knows when to sync
aggressively vs. conserve battery.
"""

from __future__ import annotations

import json
import logging
import os
import time
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default sync configuration
DEFAULT_SYNC_CONFIG = {
    # Relay URL for remote access (phone can reach desktop from anywhere)
    # Set this to your Cloudflare Tunnel URL, Tailscale IP, ngrok URL, etc.
    "relay_url": "",
    # LAN URL (auto-detected at startup, phone uses when on same network)
    "lan_url": "",
    # Whether auto-sync is enabled
    "enabled": True,
    # Sync intervals (seconds) — phone uses these as hints
    "sync_interval_connected": 60,  # When desktop is reachable: sync every 60s
    "sync_interval_disconnected": 300,  # When disconnected: try every 5 min
    "sync_interval_background": 900,  # App in background: every 15 min
    # Conflict resolution strategy
    # "most_recent" = timestamp-based (fairest), "desktop_wins" = legacy behavior
    "conflict_strategy": "most_recent",
    # Command queue behavior
    "max_offline_queue_age_hours": 168,  # Keep commands for up to 7 days
    "retry_backoff_base_seconds": 30,  # Exponential backoff base
    "retry_backoff_max_seconds": 1800,  # Max 30 min between retries
    # Data sync behavior
    "sync_on_reconnect": True,  # Immediate full sync when connection restored
    "sync_knowledge_graph": True,  # Sync KG nodes/edges
    "sync_preferences": True,  # Sync user preferences
    "sync_feedback": True,  # Sync response feedback
    "sync_patterns": True,  # Sync usage patterns
    # Phone autonomy settings
    "phone_cache_responses": True,  # Cache command responses for offline use
    "phone_cache_max_entries": 500,  # Max cached responses
    "phone_cache_ttl_hours": 72,  # Cache entries expire after 72 hours
}


class AutoSyncConfig:
    """Manages auto-sync configuration with persistent storage.

    Config is stored in ``{repo_root}/.planning/sync/auto_sync_config.json``.
    Thread-safe via a lock on read/write operations.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._config: dict[str, Any] = dict(DEFAULT_SYNC_CONFIG)
        self._config_path = config_path
        self._lock = (
            threading.RLock()
        )  # Reentrant: get_all_device_statuses calls get_device_status
        self._last_heartbeat: dict[str, float] = {}  # device_id -> timestamp
        if config_path and config_path.exists():
            self._load()

    def _load(self) -> None:
        """Load config from disk, merging with defaults for new keys."""
        config_path = self._config_path
        if config_path is None:
            return
        try:
            with open(config_path, "r") as f:
                saved = json.load(f)
            # Merge: saved values override defaults, new defaults are added
            merged = dict(DEFAULT_SYNC_CONFIG)
            merged.update(saved)
            self._config = merged
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Failed to load auto-sync config: %s", exc)

    def _save(self) -> None:
        """Persist config to disk."""
        if not self._config_path:
            return
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._config_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(self._config, f, indent=2)
            os.replace(str(tmp), str(self._config_path))
        except OSError as exc:
            logger.warning("Failed to save auto-sync config: %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value."""
        with self._lock:
            return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a config value and persist."""
        with self._lock:
            self._config[key] = value
            self._save()

    def update(self, updates: dict[str, Any]) -> None:
        """Bulk update config values and persist."""
        with self._lock:
            self._config.update(updates)
            self._save()

    def get_all(self) -> dict[str, Any]:
        """Return a copy of the full config."""
        with self._lock:
            return dict(self._config)

    def get_sync_config_for_device(self, device_id: str) -> dict[str, Any]:
        """Return the config payload that gets sent to a device.

        This is what the phone receives from ``/sync/config`` so it knows
        how to behave: which URLs to use, how often to sync, etc.
        """
        with self._lock:
            return {
                "relay_url": self._config.get("relay_url", ""),
                "lan_url": self._config.get("lan_url", ""),
                "enabled": self._config.get("enabled", True),
                "sync_interval_connected": self._config.get(
                    "sync_interval_connected", 60
                ),
                "sync_interval_disconnected": self._config.get(
                    "sync_interval_disconnected", 300
                ),
                "sync_interval_background": self._config.get(
                    "sync_interval_background", 900
                ),
                "conflict_strategy": self._config.get(
                    "conflict_strategy", "most_recent"
                ),
                "max_offline_queue_age_hours": self._config.get(
                    "max_offline_queue_age_hours", 168
                ),
                "retry_backoff_base_seconds": self._config.get(
                    "retry_backoff_base_seconds", 30
                ),
                "retry_backoff_max_seconds": self._config.get(
                    "retry_backoff_max_seconds", 1800
                ),
                "sync_on_reconnect": self._config.get("sync_on_reconnect", True),
                "phone_cache_responses": self._config.get(
                    "phone_cache_responses", True
                ),
                "phone_cache_max_entries": self._config.get(
                    "phone_cache_max_entries", 500
                ),
                "phone_cache_ttl_hours": self._config.get("phone_cache_ttl_hours", 72),
                "server_time": int(time.time()),
            }

    def record_heartbeat(self, device_id: str) -> None:
        """Record that a device has checked in."""
        with self._lock:
            self._last_heartbeat[device_id] = time.time()

    def get_device_status(self, device_id: str) -> dict[str, Any]:
        """Return the last-seen status for a device."""
        with self._lock:
            last_seen = self._last_heartbeat.get(device_id)
            if last_seen is None:
                return {"device_id": device_id, "online": False, "last_seen": None}
            age = time.time() - last_seen
            return {
                "device_id": device_id,
                "online": age < 120,  # Consider online if heartbeat within 2 min
                "last_seen": int(last_seen),
                "seconds_ago": int(age),
            }

    def get_all_device_statuses(self) -> list[dict[str, Any]]:
        """Return status for all known devices."""
        with self._lock:
            return [self.get_device_status(did) for did in self._last_heartbeat]
