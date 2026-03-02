"""Security hardening — Waves 9-13.

Threat detection, forensic logging, IP tracking, prompt-injection firewall,
honeypot engine, attack pattern memory, identity monitoring, output scanning,
session management, autonomous containment, alert chain, adaptive defense,
memory provenance, and scope enforcement.
"""

from __future__ import annotations

from jarvis_engine.security.action_auditor import ActionAuditor
from jarvis_engine.security.adaptive_defense import AdaptiveDefenseEngine
from jarvis_engine.security.alert_chain import AlertChain
from jarvis_engine.security.attack_memory import AttackPatternMemory
from jarvis_engine.security.containment import ContainmentEngine, ContainmentLevel
from jarvis_engine.security.forensic_logger import ForensicLogger
from jarvis_engine.security.heartbeat import HeartbeatMonitor
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
from jarvis_engine.security.owner_session import OwnerSessionManager
from jarvis_engine.security.output_scanner import OutputScanResult, OutputScanner
from jarvis_engine.security.resource_monitor import ResourceMonitor
from jarvis_engine.security.scope_enforcer import ScopeEnforcer
from jarvis_engine.security.session_manager import Session, SessionManager
from jarvis_engine.security.threat_detector import (
    ThreatAssessment,
    ThreatDetector,
    ThreatSignal,
)

__all__ = [
    "ActionAuditor",
    "AdaptiveDefenseEngine",
    "AlertChain",
    "AttackPatternMemory",
    "ContainmentEngine",
    "ContainmentLevel",
    "ForensicLogger",
    "HeartbeatMonitor",
    "HoneypotEngine",
    "IdentityAlert",
    "IdentityMonitor",
    "InjectionResult",
    "InjectionVerdict",
    "IPTracker",
    "MemoryProvenance",
    "OutputScanResult",
    "OutputScanner",
    "OwnerSessionManager",
    "PromptInjectionFirewall",
    "ResourceMonitor",
    "ScopeEnforcer",
    "SecurityOrchestrator",
    "Session",
    "SessionManager",
    "ThreatAssessment",
    "ThreatDetector",
    "ThreatSignal",
]
