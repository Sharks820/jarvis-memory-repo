"""Adaptive AI defense engine -- Wave 13 security hardening.

Ties all security components together: consumes threat signals, generates
auto-detection rules from recurring patterns, tracks defense effectiveness,
and produces dashboards and briefings.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from jarvis_engine.security.attack_memory import AttackPatternMemory
    from jarvis_engine.security.ip_tracker import IPTracker

from jarvis_engine._shared import now_iso as _now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------


class DefenseDashboard(TypedDict):
    total_attacks: int
    total_blocked: int
    rules_generated: int
    unique_ips: int
    effectiveness_pct: float
    top_categories: list[dict]


# ---------------------------------------------------------------------------
# Auto-rule generation threshold
# ---------------------------------------------------------------------------

_AUTO_RULE_THRESHOLD = 3  # similar attacks needed to generate a rule


class AdaptiveDefenseEngine:
    """Adaptive defense engine that learns from detected threats.

    Consumes ThreatDetector signals, ForensicLogger entries,
    AttackPatternMemory data, and IPTracker information to produce
    new detection rules, updated threat scores, and effectiveness
    metrics.

    Parameters
    ----------
    attack_memory:
        Optional ``AttackPatternMemory`` instance for pattern lookups.
    ip_tracker:
        Optional ``IPTracker`` instance for IP intelligence.
    """

    def __init__(
        self,
        attack_memory: AttackPatternMemory | None = None,
        ip_tracker: IPTracker | None = None,
    ) -> None:
        self._attack_memory = attack_memory
        self._ip_tracker = ip_tracker
        self._lock = threading.Lock()

        # Detection event log: bounded deque of dicts
        self._events: deque[dict[str, Any]] = deque(maxlen=10000)

        # Counts per category for auto-rule generation
        self._category_counts: dict[str, int] = defaultdict(int)

        # Auto-generated rules: list of rule dicts
        self._rules: list[dict[str, Any]] = []

        # Set of categories that already have an auto-generated rule
        self._ruled_categories: set[str] = set()

        # Tracking metrics
        self._total_attacks = 0
        self._total_blocked = 0
        self._unique_ips: set[str] = set()
        self._unique_ips_cap: int = 10000  # cap to prevent unbounded growth
        self._category_counts_cap: int = 1000  # max distinct categories tracked

    # ------------------------------------------------------------------
    # Record a detection event
    # ------------------------------------------------------------------

    def record_detection(
        self,
        category: str,
        payload_hash: str,
        source_ip: str,
        blocked: bool = True,
    ) -> None:
        """Record a detection event from the threat pipeline.

        Parameters
        ----------
        category:
            Attack category (e.g. ``"injection"``, ``"path_traversal"``).
        payload_hash:
            SHA-256 hash of the payload that triggered detection.
        source_ip:
            Source IP address of the attacker.
        blocked:
            Whether the attack was successfully blocked.
        """
        event = {
            "category": category,
            "payload_hash": payload_hash,
            "source_ip": source_ip,
            "blocked": blocked,
            "timestamp": _now_iso(),
        }
        with self._lock:
            self._events.append(event)
            self._total_attacks += 1
            if blocked:
                self._total_blocked += 1
            if source_ip and len(self._unique_ips) < self._unique_ips_cap:
                self._unique_ips.add(source_ip)
            if (
                len(self._category_counts) < self._category_counts_cap
                or category in self._category_counts
            ):
                self._category_counts[category] += 1

    # ------------------------------------------------------------------
    # Auto-rule generation
    # ------------------------------------------------------------------

    def check_auto_rule(self, category: str) -> dict[str, Any] | None:
        """Check if a category has enough detections to auto-generate a rule.

        When ``_AUTO_RULE_THRESHOLD`` or more similar attacks in *category*
        have been recorded and no rule exists yet, a new pattern rule is
        generated and stored.

        Returns the new rule dict, or ``None`` if threshold not met or
        rule already exists.
        """
        with self._lock:
            count = self._category_counts.get(category, 0)
            if count < _AUTO_RULE_THRESHOLD:
                return None
            if category in self._ruled_categories:
                return None

            # Gather payload hashes that triggered this category (limit scan to last 500 events)
            triggered_by: list[str] = []
            for e in reversed(self._events):
                if e["category"] == category:
                    triggered_by.append(e["payload_hash"])
                    if len(triggered_by) >= 50:
                        break

            rule: dict[str, Any] = {
                "pattern": f"auto_rule_{category}",
                "category": category,
                "created_at": _now_iso(),
                "triggered_by": triggered_by,
                "detection_count": count,
            }
            self._rules.append(rule)
            self._ruled_categories.add(category)
        logger.info(
            "Auto-generated defense rule for category %r (%d detections)",
            category,
            count,
        )
        return rule

    # ------------------------------------------------------------------
    # Dashboard & metrics
    # ------------------------------------------------------------------

    def get_defense_dashboard(self) -> DefenseDashboard:
        """Return a metrics dashboard dict.

        Keys: ``total_attacks``, ``total_blocked``, ``rules_generated``,
        ``unique_ips``, ``effectiveness_pct``, ``top_categories``.
        """
        with self._lock:
            total = self._total_attacks
            blocked = self._total_blocked
            rules_count = len(self._rules)
            unique_count = len(self._unique_ips)
            top = sorted(
                self._category_counts.items(),
                key=lambda kv: kv[1],
                reverse=True,
            )

        if total > 0:
            effectiveness = round((blocked / total) * 100, 2)
        else:
            effectiveness = 100.0

        return {
            "total_attacks": total,
            "total_blocked": blocked,
            "rules_generated": rules_count,
            "unique_ips": unique_count,
            "effectiveness_pct": effectiveness,
            "top_categories": [{"category": cat, "count": cnt} for cat, cnt in top],
        }

    # ------------------------------------------------------------------
    # Briefing
    # ------------------------------------------------------------------

    def generate_briefing(self) -> str:
        """Return a human-readable text briefing of defense status."""
        d = self.get_defense_dashboard()
        lines = [
            "=== Jarvis Adaptive Defense Briefing ===",
            "",
            f"Total attacks detected: {d['total_attacks']}",
            f"Attacks blocked: {d['total_blocked']}",
            f"Effectiveness: {d['effectiveness_pct']}%",
            f"Unique attacker IPs: {d['unique_ips']}",
            f"Auto-generated rules: {d['rules_generated']}",
        ]
        if d["top_categories"]:
            lines.append("")
            lines.append("Top attack categories:")
            for entry in d["top_categories"][:5]:
                lines.append(f"  - {entry['category']}: {entry['count']} detections")

        if not d["total_attacks"]:
            lines.append("")
            lines.append("No threats detected. All systems nominal.")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Rule access
    # ------------------------------------------------------------------

    def get_rules(self) -> list[dict[str, Any]]:
        """Return the list of auto-generated defense rules."""
        with self._lock:
            return list(self._rules)
