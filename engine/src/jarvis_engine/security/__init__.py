"""Security hardening — Wave 9.

Threat detection, forensic logging, IP tracking, prompt-injection firewall,
honeypot engine, attack pattern memory, identity monitoring, output scanning,
and session management.
"""

from __future__ import annotations

from jarvis_engine.security.attack_memory import AttackPatternMemory
from jarvis_engine.security.forensic_logger import ForensicLogger
from jarvis_engine.security.honeypot import HoneypotEngine
from jarvis_engine.security.identity_monitor import IdentityAlert, IdentityMonitor
from jarvis_engine.security.injection_firewall import (
    InjectionResult,
    InjectionVerdict,
    PromptInjectionFirewall,
)
from jarvis_engine.security.ip_tracker import IPTracker
from jarvis_engine.security.output_scanner import OutputScanResult, OutputScanner
from jarvis_engine.security.session_manager import Session, SessionManager
from jarvis_engine.security.threat_detector import (
    ThreatAssessment,
    ThreatDetector,
    ThreatSignal,
)

__all__ = [
    "AttackPatternMemory",
    "ForensicLogger",
    "HoneypotEngine",
    "IdentityAlert",
    "IdentityMonitor",
    "InjectionResult",
    "InjectionVerdict",
    "IPTracker",
    "OutputScanResult",
    "OutputScanner",
    "PromptInjectionFirewall",
    "Session",
    "SessionManager",
    "ThreatAssessment",
    "ThreatDetector",
    "ThreatSignal",
]
