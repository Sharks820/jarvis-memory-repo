"""Security hardening — Waves 9-13.

Threat detection, forensic logging, IP tracking, prompt-injection firewall,
honeypot engine, attack pattern memory, identity monitoring, output scanning,
session management, autonomous containment, alert chain, adaptive defense,
and memory provenance.
"""

from __future__ import annotations

from jarvis_engine.security.adaptive_defense import AdaptiveDefenseEngine
from jarvis_engine.security.alert_chain import AlertChain
from jarvis_engine.security.attack_memory import AttackPatternMemory
from jarvis_engine.security.containment import ContainmentEngine, ContainmentLevel
from jarvis_engine.security.forensic_logger import ForensicLogger
from jarvis_engine.security.honeypot import HoneypotEngine
from jarvis_engine.security.identity_monitor import IdentityAlert, IdentityMonitor
from jarvis_engine.security.injection_firewall import (
    InjectionResult,
    InjectionVerdict,
    PromptInjectionFirewall,
)
from jarvis_engine.security.ip_tracker import IPTracker
from jarvis_engine.security.memory_provenance import MemoryProvenance
from jarvis_engine.security.orchestrator import SecurityOrchestrator
from jarvis_engine.security.output_scanner import OutputScanResult, OutputScanner
from jarvis_engine.security.session_manager import Session, SessionManager
from jarvis_engine.security.threat_detector import (
    ThreatAssessment,
    ThreatDetector,
    ThreatSignal,
)

__all__ = [
    "AdaptiveDefenseEngine",
    "AlertChain",
    "AttackPatternMemory",
    "ContainmentEngine",
    "ContainmentLevel",
    "ForensicLogger",
    "HoneypotEngine",
    "IdentityAlert",
    "IdentityMonitor",
    "InjectionResult",
    "InjectionVerdict",
    "IPTracker",
    "MemoryProvenance",
    "OutputScanResult",
    "OutputScanner",
    "PromptInjectionFirewall",
    "SecurityOrchestrator",
    "Session",
    "SessionManager",
    "ThreatAssessment",
    "ThreatDetector",
    "ThreatSignal",
]
