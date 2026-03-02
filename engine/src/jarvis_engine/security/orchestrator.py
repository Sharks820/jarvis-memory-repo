"""Unified security orchestrator — wires all 17 security modules into a single pipeline.

Provides ``check_request()`` for inbound request validation and
``scan_output()`` for outbound LLM response scanning.

Pipeline order:
  1. Honeypot check (instant trap for scanning tools)
  2. IP blocklist check (reject known-bad IPs)
  3. Threat detection (8 rule types)
  4. Prompt injection firewall (3 layers)
  5. Forensic logging of every decision
  6. Auto-escalation for HIGH/CRITICAL threats
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from jarvis_engine.security.adaptive_defense import AdaptiveDefenseEngine
from jarvis_engine.security.alert_chain import AlertChain
from jarvis_engine.security.attack_memory import AttackPatternMemory
from jarvis_engine.security.containment import ContainmentEngine
from jarvis_engine.security.forensic_logger import ForensicLogger
from jarvis_engine.security.honeypot import HoneypotEngine
from jarvis_engine.security.injection_firewall import (
    InjectionVerdict,
    PromptInjectionFirewall,
)
from jarvis_engine.security.ip_tracker import IPTracker
from jarvis_engine.security.output_scanner import OutputScanner
from jarvis_engine.security.threat_detector import ThreatDetector

logger = logging.getLogger(__name__)

# Threat levels that trigger automatic escalation
_ESCALATION_LEVELS = frozenset({"HIGH", "CRITICAL"})

# Map threat level -> containment severity (ContainmentEngine level)
_THREAT_TO_CONTAINMENT: dict[str, int] = {
    "HIGH": 2,       # BLOCK
    "CRITICAL": 3,   # ISOLATE
}

# Map threat level -> alert chain level
_THREAT_TO_ALERT: dict[str, int] = {
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 5,
}


class SecurityOrchestrator:
    """Unified security pipeline orchestrating all security modules.

    Parameters
    ----------
    db:
        Open ``sqlite3.Connection`` for attack memory and IP tracker.
    write_lock:
        Shared ``threading.Lock`` for serialising database writes.
    log_dir:
        Directory for forensic log files.
    owner_config:
        Optional dict with owner-specific configuration (reserved for
        future owner session integration).
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        write_lock: threading.Lock,
        log_dir: str | Path,
        owner_config: dict[str, Any] | None = None,
    ) -> None:
        log_dir = Path(log_dir)

        # --- Core infrastructure ---
        self._forensic_logger = ForensicLogger(log_dir)
        self._ip_tracker = IPTracker(db, write_lock)

        # --- Detection ---
        self._threat_detector = ThreatDetector(ip_tracker=self._ip_tracker)
        self._injection_firewall = PromptInjectionFirewall()
        self._output_scanner = OutputScanner()
        self._honeypot = HoneypotEngine(forensic_logger=self._forensic_logger)

        # --- Response ---
        self._containment = ContainmentEngine(
            forensic_logger=self._forensic_logger,
            ip_tracker=self._ip_tracker,
        )
        self._alert_chain = AlertChain(forensic_logger=self._forensic_logger)

        # --- Intelligence ---
        self._attack_memory = AttackPatternMemory(db, write_lock)
        self._adaptive_defense = AdaptiveDefenseEngine(
            attack_memory=self._attack_memory,
            ip_tracker=self._ip_tracker,
        )

        self._owner_config = owner_config or {}
        self._total_requests = 0
        self._total_blocked = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Inbound request pipeline
    # ------------------------------------------------------------------

    def check_request(
        self,
        path: str,
        source_ip: str,
        headers: dict,
        body: str,
        user_agent: str = "",
    ) -> dict[str, Any]:
        """Run the full security pipeline on an inbound request.

        Returns a dict with keys:
          - ``allowed`` (bool): whether the request should proceed
          - ``reason`` (str): human-readable explanation
          - ``threat_level`` (str): NONE / LOW / MEDIUM / HIGH / CRITICAL
          - ``injection_verdict`` (str): clean / suspicious / injection_detected / hostile
          - ``containment_actions`` (list): actions taken by containment engine
        """
        with self._lock:
            self._total_requests += 1

        containment_actions: list[str] = []

        # --- Step 1: Honeypot check ---
        if self._honeypot.is_honeypot_path(path):
            self._honeypot.record_hit(path, source_ip, headers)
            self._forensic_logger.log_event({
                "event_type": "honeypot_triggered",
                "path": path,
                "source_ip": source_ip,
            })
            # Record in IP tracker as an attack attempt
            self._ip_tracker.record_attempt(source_ip, "honeypot_probe")
            self._adaptive_defense.record_detection(
                category="honeypot_probe",
                payload_hash=path,
                source_ip=source_ip,
                blocked=True,
            )
            with self._lock:
                self._total_blocked += 1
            return {
                "allowed": False,
                "reason": "Honeypot path triggered",
                "threat_level": "HIGH",
                "injection_verdict": "clean",
                "containment_actions": [],
            }

        # --- Step 2: IP blocklist check ---
        if self._ip_tracker.is_blocked(source_ip):
            self._forensic_logger.log_event({
                "event_type": "blocked_ip_rejected",
                "source_ip": source_ip,
                "path": path,
            })
            with self._lock:
                self._total_blocked += 1
            return {
                "allowed": False,
                "reason": "IP is blocked",
                "threat_level": "CRITICAL",
                "injection_verdict": "clean",
                "containment_actions": [],
            }

        # --- Step 3: Threat detection ---
        request_context = {
            "ip": source_ip,
            "path": path,
            "body": body,
            "user_agent": user_agent,
            "headers": headers,
        }
        assessment = self._threat_detector.assess(request_context)
        threat_level = assessment.threat_level

        # --- Step 4: Injection firewall ---
        # Scan body (the main attack surface for prompt injection)
        scan_text = body or ""
        injection_result = self._injection_firewall.scan(scan_text)
        injection_verdict = injection_result.verdict.value

        # --- Step 5: Forensic log ---
        self._forensic_logger.log_event({
            "event_type": "request_assessed",
            "path": path,
            "source_ip": source_ip,
            "threat_level": threat_level,
            "injection_verdict": injection_verdict,
            "signal_count": len(assessment.signals),
        })

        # --- Step 6: Decision logic ---
        allowed = True
        reason = "Request allowed"

        # On HIGH/CRITICAL threat: auto-escalate, block
        if threat_level in _ESCALATION_LEVELS:
            allowed = False
            reason = f"Threat level {threat_level} detected"
            containment_level = _THREAT_TO_CONTAINMENT.get(threat_level, 2)
            try:
                result = self._containment.contain(
                    ip=source_ip,
                    level=containment_level,
                    reason=f"Auto-escalation: {threat_level} threat on {path}",
                )
                containment_actions = result.get("actions", [])
            except Exception as exc:
                logger.warning("Containment failed: %s", exc)

            # Send alert
            alert_level = _THREAT_TO_ALERT.get(threat_level, 3)
            categories = ", ".join(s.category for s in assessment.signals)
            self._alert_chain.send_alert(
                level=alert_level,
                summary=f"{threat_level} threat from {source_ip}: {categories}",
                evidence=f"path={path}",
                containment_action=f"level {containment_level} containment",
                source_ip=source_ip,
            )

            # Record in attack memory and adaptive defense
            for signal in assessment.signals:
                self._attack_memory.record_attack(
                    category=signal.category,
                    payload=body or path,
                    detection_method="threat_detector",
                    source_ip=source_ip,
                )
                self._adaptive_defense.record_detection(
                    category=signal.category,
                    payload_hash=signal.category,
                    source_ip=source_ip,
                    blocked=True,
                )
                self._adaptive_defense.check_auto_rule(signal.category)

            # Record IP attempt
            self._ip_tracker.record_attempt(source_ip, threat_level)

        # On injection detected: record attack, block
        if injection_verdict != "clean":
            if injection_verdict in ("injection_detected", "hostile"):
                allowed = False
                reason = f"Injection {injection_verdict}: {', '.join(injection_result.matched_patterns[:3])}"
                self._attack_memory.record_attack(
                    category="prompt_injection",
                    payload=body[:500] if body else "",
                    detection_method=f"firewall_{injection_verdict}",
                    source_ip=source_ip,
                )
                self._adaptive_defense.record_detection(
                    category="prompt_injection",
                    payload_hash=injection_verdict,
                    source_ip=source_ip,
                    blocked=True,
                )
                self._ip_tracker.record_attempt(source_ip, "prompt_injection")

                # Send alert for hostile injections
                if injection_verdict == "hostile":
                    self._alert_chain.send_alert(
                        level=4,
                        summary=f"Hostile injection from {source_ip}",
                        evidence=f"patterns={injection_result.matched_patterns[:5]}",
                        containment_action="request blocked",
                        source_ip=source_ip,
                    )

            elif injection_verdict == "suspicious":
                # Log but allow (may be a false positive)
                self._forensic_logger.log_event({
                    "event_type": "suspicious_injection",
                    "source_ip": source_ip,
                    "path": path,
                    "patterns": injection_result.matched_patterns[:5],
                })

        if not allowed:
            with self._lock:
                self._total_blocked += 1

        return {
            "allowed": allowed,
            "reason": reason,
            "threat_level": threat_level,
            "injection_verdict": injection_verdict,
            "containment_actions": containment_actions,
        }

    # ------------------------------------------------------------------
    # Outbound output scanning
    # ------------------------------------------------------------------

    def scan_output(
        self,
        response_text: str,
        system_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Scan an LLM response for credential leaks, exfiltration, etc.

        Returns a dict with keys:
          - ``safe`` (bool): True if no issues found
          - ``findings`` (list[str]): list of issue identifiers
          - ``filtered_text`` (str): the response text (unchanged if safe,
            redacted description if unsafe)
        """
        result = self._output_scanner.scan_output(response_text, system_context)

        if not result.safe:
            self._forensic_logger.log_event({
                "event_type": "output_scan_failed",
                "issues": result.issues,
                "confidence": result.confidence,
            })

        return {
            "safe": result.safe,
            "findings": result.issues,
            "filtered_text": response_text if result.safe else "[REDACTED: security issues detected]",
        }

    # ------------------------------------------------------------------
    # Status dashboard
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return aggregate security status across all modules.

        Keys: ``containment_level``, ``total_threats``, ``blocked_ips``,
        ``honeypot_stats``, ``adaptive_defense``, ``total_requests``,
        ``total_blocked``.
        """
        containment_status = self._containment.get_containment_status()
        honeypot_stats = self._honeypot.get_honeypot_stats()
        defense_dashboard = self._adaptive_defense.get_defense_dashboard()

        with self._lock:
            total_req = self._total_requests
            total_blk = self._total_blocked

        return {
            "containment_level": containment_status["current_level"],
            "containment_detail": containment_status,
            "total_threats": defense_dashboard["total_attacks"],
            "blocked_ips": containment_status["blocked_ips"],
            "honeypot_stats": honeypot_stats,
            "adaptive_defense": defense_dashboard,
            "total_requests": total_req,
            "total_blocked": total_blk,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_threat(
        self,
        source_ip: str,
        category: str,
        detail: str,
        level: int = 2,
    ) -> None:
        """Internal helper for manual threat escalation.

        Parameters
        ----------
        source_ip:
            Attacker IP address.
        category:
            Attack category string.
        detail:
            Payload or description of the threat.
        level:
            Containment level (1-5). Default is 2 (BLOCK).
        """
        # Record in attack memory
        self._attack_memory.record_attack(
            category=category,
            payload=detail,
            detection_method="orchestrator_escalation",
            source_ip=source_ip,
        )

        # Execute containment
        self._containment.contain(
            ip=source_ip,
            level=level,
            reason=f"Orchestrator escalation: {category}",
        )

        # Send alert
        alert_level = min(level + 1, 5)
        self._alert_chain.send_alert(
            level=alert_level,
            summary=f"Threat escalation: {category} from {source_ip}",
            evidence=detail[:200],
            containment_action=f"level {level} containment",
            source_ip=source_ip,
        )

        # Record in adaptive defense
        self._adaptive_defense.record_detection(
            category=category,
            payload_hash=category,
            source_ip=source_ip,
            blocked=True,
        )
        self._adaptive_defense.check_auto_rule(category)

        # Record IP attempt
        self._ip_tracker.record_attempt(source_ip, category)

        # Log to forensic log
        self._forensic_logger.log_event({
            "event_type": "threat_escalated",
            "source_ip": source_ip,
            "category": category,
            "containment_level": level,
        })

        logger.warning(
            "Threat escalated: %s from %s (containment level %d)",
            category, source_ip, level,
        )
