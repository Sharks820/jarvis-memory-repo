"""Honeypot engine for detecting and wasting attacker time.

Deploys fake endpoints that mimic common scan targets.  When hit, they
return plausible-looking but fake responses and log the attacker activity
for forensic analysis.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Honeypot path registry
# ---------------------------------------------------------------------------

HONEYPOT_PATHS: list[str] = [
    "/admin",
    "/wp-admin",
    "/wp-login.php",
    "/api/v2/debug",
    "/config",
    "/env",
    "/.env",
    "/phpinfo.php",
    "/actuator",
    "/swagger.json",
    "/graphql",
    "/api/admin",
    "/debug/vars",
]

# Normalise to a set for O(1) lookup
_HONEYPOT_SET: set[str] = set(HONEYPOT_PATHS)

# ---------------------------------------------------------------------------
# Hit record
# ---------------------------------------------------------------------------


@dataclass
class _HitRecord:
    """Internal record of a single honeypot hit."""

    path: str
    source_ip: str
    headers: dict
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Fake response generators
# ---------------------------------------------------------------------------


def _fake_login_page() -> str:
    return (
        "<html><head><title>Log In</title></head><body>"
        '<form action="/wp-login.php" method="post">'
        '<label>Username</label><input type="text" name="log"/>'
        '<label>Password</label><input type="password" name="pwd"/>'
        '<input type="submit" value="Log In"/>'
        "</form></body></html>"
    )


def _fake_env() -> str:
    return (
        "APP_ENV=production\n"
        "APP_DEBUG=false\n"
        "DB_HOST=localhost\n"
        "DB_PORT=3306\n"
        "DB_DATABASE=app_prod\n"
        "DB_USERNAME=readonly\n"
        "DB_PASSWORD=changeme123\n"
        "SECRET_KEY=FAKE-NOT-REAL-KEY-0000000000\n"
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
    )


def _fake_config() -> str:
    return json.dumps(
        {
            "version": "2.1.0",
            "environment": "production",
            "database": {"host": "db.internal", "port": 5432, "name": "app"},
            "redis": {"host": "redis.internal", "port": 6379},
            "features": {"debug": False, "maintenance": False},
        },
        indent=2,
    )


def _fake_phpinfo() -> str:
    return (
        "<html><head><title>phpinfo()</title></head><body>"
        "<h1>PHP Version 8.2.13</h1>"
        "<table><tr><td>System</td><td>Linux app-server 5.15.0</td></tr>"
        "<tr><td>Server API</td><td>FPM/FastCGI</td></tr>"
        "<tr><td>Configuration File</td><td>/etc/php/8.2/fpm/php.ini</td></tr>"
        "</table></body></html>"
    )


def _fake_actuator() -> str:
    return json.dumps(
        {
            "_links": {
                "self": {"href": "/actuator"},
                "health": {"href": "/actuator/health"},
                "info": {"href": "/actuator/info"},
                "metrics": {"href": "/actuator/metrics"},
            }
        },
        indent=2,
    )


def _fake_swagger() -> str:
    return json.dumps(
        {
            "openapi": "3.0.1",
            "info": {"title": "Internal API", "version": "1.0.0"},
            "paths": {
                "/api/users": {"get": {"summary": "List users"}},
                "/api/admin/settings": {"get": {"summary": "Admin settings"}},
            },
        },
        indent=2,
    )


def _fake_graphql() -> str:
    return json.dumps(
        {
            "data": None,
            "errors": [
                {
                    "message": "Must provide query string.",
                    "locations": [],
                    "path": [],
                }
            ],
        },
        indent=2,
    )


def _fake_admin_panel() -> str:
    return (
        "<html><head><title>Admin Panel</title></head><body>"
        "<h1>Administration Dashboard</h1>"
        '<p>Please <a href="/admin/login">log in</a> to continue.</p>'
        "</body></html>"
    )


def _fake_debug() -> str:
    return json.dumps(
        {
            "cmdline": ["/usr/bin/app", "--config=/etc/app.conf"],
            "memstats": {"Alloc": 4194304, "TotalAlloc": 16777216},
            "goroutines": 42,
            "uptime_s": 86400,
        },
        indent=2,
    )


# Map path -> (status_code, content_type, body_generator)
_RESPONSE_MAP: dict[str, tuple[int, str, Callable[[], str]]] = {
    "/admin": (200, "text/html", _fake_admin_panel),
    "/wp-admin": (200, "text/html", _fake_admin_panel),
    "/wp-login.php": (200, "text/html", _fake_login_page),
    "/api/v2/debug": (200, "application/json", _fake_debug),
    "/config": (200, "application/json", _fake_config),
    "/env": (200, "text/plain", _fake_env),
    "/.env": (200, "text/plain", _fake_env),
    "/phpinfo.php": (200, "text/html", _fake_phpinfo),
    "/actuator": (200, "application/json", _fake_actuator),
    "/swagger.json": (200, "application/json", _fake_swagger),
    "/graphql": (200, "application/json", _fake_graphql),
    "/api/admin": (200, "text/html", _fake_admin_panel),
    "/debug/vars": (200, "application/json", _fake_debug),
}


# ---------------------------------------------------------------------------
# HoneypotEngine
# ---------------------------------------------------------------------------


class HoneypotEngine:
    """Deploy fake endpoints that look real to attackers.

    Parameters
    ----------
    forensic_logger:
        Optional logger object with a ``log(event_type, data)`` method.
        If provided, all hits are forwarded for forensic analysis.
    """

    def __init__(self, forensic_logger: object | None = None) -> None:
        self._forensic_logger = forensic_logger
        self._lock = threading.Lock()
        self._hits: dict[str, list[_HitRecord]] = defaultdict(list)
        self._unique_ips: set[str] = set()
        self._unique_ips_cap: int = 10000  # cap to prevent unbounded growth

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_honeypot_path(self, path: str) -> bool:
        """Return ``True`` if *path* is a known honeypot endpoint."""
        normalised = path.rstrip("/") if path != "/" else path
        return normalised in _HONEYPOT_SET

    def generate_response(self, path: str) -> tuple[int, dict, str]:
        """Return ``(status_code, headers, body)`` with a plausible fake response."""
        normalised = path.rstrip("/") if path != "/" else path
        entry = _RESPONSE_MAP.get(normalised)
        if entry is None:
            # Fallback: generic 403
            headers = {"Content-Type": "text/html", "Server": "nginx/1.24.0"}
            return (403, headers, "<html><body><h1>403 Forbidden</h1></body></html>")

        status_code, content_type, body_fn = entry
        body = body_fn()
        headers = {
            "Content-Type": content_type,
            "Server": "nginx/1.24.0",
            "X-Powered-By": "PHP/8.2.13",
        }
        return (status_code, headers, body)

    def record_hit(
        self, path: str, source_ip: str, headers: dict | None = None
    ) -> dict:
        """Record a honeypot hit and return summary stats for the path.

        If a *forensic_logger* was provided at construction time, the hit
        is also forwarded for forensic logging.

        Only records hits for known honeypot paths. Non-honeypot paths are
        silently ignored to prevent memory growth from arbitrary path strings.
        """
        # Guard: only record hits for known honeypot paths
        normalised = path.rstrip("/") if path != "/" else path
        if normalised not in _HONEYPOT_SET:
            return {"path": path, "total_hits": 0, "unique_ips": 0}

        record = _HitRecord(
            path=normalised,
            source_ip=source_ip,
            headers=headers or {},
        )
        with self._lock:
            self._hits[normalised].append(record)
            if len(self._hits[normalised]) > 1000:
                self._hits[normalised] = self._hits[normalised][-500:]
            if len(self._unique_ips) < self._unique_ips_cap:
                self._unique_ips.add(source_ip)
            total_path_hits = len(self._hits[normalised])
            unique_path_ips = len({h.source_ip for h in self._hits[normalised]})

        if self._forensic_logger is not None:
            try:
                self._forensic_logger.log_event({
                    "event_type": "honeypot_hit",
                    "path": normalised,
                    "source_ip": source_ip,
                    "headers": headers or {},
                    "timestamp": record.timestamp,
                })
            except Exception as exc:
                logger.debug("Failed to forward hit to forensic logger: %s", exc)

        return {
            "path": normalised,
            "total_hits": total_path_hits,
            "unique_ips": unique_path_ips,
        }

    def get_honeypot_stats(self) -> dict:
        """Return aggregate honeypot statistics."""
        with self._lock:
            total_hits = sum(len(recs) for recs in self._hits.values())
            hits_per_path: dict[str, int] = {
                path: len(recs) for path, recs in self._hits.items()
            }

            # Count hits per IP across all paths
            ip_counts: dict[str, int] = defaultdict(int)
            for recs in self._hits.values():
                for rec in recs:
                    ip_counts[rec.source_ip] += 1

            unique_count = len(self._unique_ips)

        # Top attackers — sorted descending by hit count
        top_attackers = sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[
            :10
        ]

        return {
            "total_hits": total_hits,
            "hits_per_path": hits_per_path,
            "unique_ips": unique_count,
            "top_attackers": [
                {"ip": ip, "hits": count} for ip, count in top_attackers
            ],
        }
