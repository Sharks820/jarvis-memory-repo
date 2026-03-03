# Security Deep Hardening Design

**Date:** 2026-03-01
**Status:** Approved
**Scope:** Comprehensive security architecture for Jarvis — defense, governance, identity protection, home network security, and legal offensive response

## Problem Statement

Jarvis has 17 security modules (482 tests) that are fully implemented but **none are wired into the live request pipeline**. The mobile API implements its own parallel security (HMAC, rate limiting, CORS) but the advanced threat detection, containment, forensic logging, and prompt injection firewall sit unused. Additionally, the user requires:

1. Frictionless owner authentication (password session, not per-command auth)
2. Offensive retaliation capabilities (within legal bounds)
3. Full bot governance (see everything Jarvis does)
4. Identity and family protection
5. Home network defense

## Architecture: 7 Security Pillars

### Pillar 1: SecurityOrchestrator

Single integration point that wires all 17 existing modules + new modules into the live request pipeline.

**Request flow:**
```
Incoming Request
  -> HoneypotEngine.check()         [trap scanners/bots]
  -> IPTracker.check()              [block known-bad IPs]
  -> ThreatDetector.assess()        [8 detection rules]
  -> Auth (HMAC for mobile / Session for desktop)
  -> InjectionFirewall.scan()       [3-layer prompt injection check]
  -> [Process Request]
  -> OutputScanner.scan()           [credential/exfil check on LLM output]
  -> ForensicLogger.record()        [hash-chain audit trail]
  -> ActionAuditor.log()            [bot governance trail]
```

**Auto-escalation pipeline:**
```
Detection Event (ThreatDetector/InjectionFirewall/HoneypotEngine)
  -> AttackPatternMemory.record()   [learn attack patterns]
  -> AdaptiveDefenseEngine.process() [auto-generate rules at 3+ detections]
  -> ContainmentEngine.escalate()   [graduated: throttle -> block -> isolate -> lockdown -> kill]
  -> AlertChain.dispatch()          [notify: log -> widget -> mobile -> urgent -> alarm+email]
  -> ThreatIntelFeed.enrich()       [AbuseIPDB/OTX reputation lookup]
  -> AbuseReporter.report()         [auto-report at HIGH+ severity]
```

**New file:** `engine/src/jarvis_engine/security/orchestrator.py`

### Pillar 2: Owner Session Authentication

**Goal:** "Know it's me" — authenticate once, operate freely.

- **Argon2id** password hashing (memory_cost=65536, time_cost=3, parallelism=4)
- Fallback to PBKDF2-HMAC-SHA256 (600K iterations) if argon2-cffi unavailable
- 32-byte session token via `secrets.token_bytes(32)`
- Configurable idle timeout (default 30 minutes, env: `JARVIS_SESSION_TIMEOUT`)
- Exponential backoff on failed attempts (lockout after 5 failures: 2^n seconds)
- `secrets.compare_digest()` for constant-time token comparison
- Session stored in memory only, zeroed on timeout/lock
- Android mobile retains stateless HMAC auth (better for mobile reliability)

**Endpoints:**
- `POST /auth/login` — Password -> session token
- `POST /auth/logout` — Invalidate session
- `GET /auth/status` — Check session validity
- `POST /auth/lock` — Immediate lock (manual)

**New file:** `engine/src/jarvis_engine/security/owner_session.py`

### Pillar 3: Threat Intelligence & Active Defense

**ThreatIntelFeed** — Aggregates threat intelligence from free feeds:
- AbuseIPDB: IP reputation scores (1000 lookups/day free tier)
- AlienVault OTX: Indicators of compromise (IPs, domains, hashes)
- abuse.ch: Botnet C2 IPs (Feodo Tracker), malware URLs (URLhaus)
- Local cache with 1-hour TTL to minimize API calls
- Every incoming IP auto-enriched on first contact

**AbuseReporter** — Automated legal reporting:
- AbuseIPDB report API (automated at HIGH+ severity)
- RDAP lookup for ISP abuse contacts (replaces WHOIS)
- Evidence package generation from ForensicLogger (chain-verified ZIP)
- Law enforcement report templates (IC3/FBI format)
- Rate-limited to prevent report flooding

**New files:**
- `engine/src/jarvis_engine/security/threat_intel.py`
- `engine/src/jarvis_engine/security/abuse_reporter.py`

### Pillar 4: Bot Governance & AI Alignment

**ActionAuditor** — Every Jarvis action logged to forensic chain:
- Action type (command, api_call, file_access, proactive, learning)
- Trigger source (user_command, proactive_engine, scheduled, internal)
- Input hash (SHA-256 of input for privacy-preserving audit)
- Output summary (truncated)
- Resource consumption (tokens, CPU time, memory)
- Decision rationale (why this action was chosen)
- Scope check result (pass/fail)

**ScopeEnforcer** — Formal operational boundary layer:
```
ALLOWED_SCOPES:
  memory:     read, write, search, delete_own
  knowledge:  read, add_fact, query, update_fact
  network:    http_get, http_post (no raw sockets)
  filesystem: read_data_dir, write_data_dir (sandboxed)
  system:     get_time, get_battery, get_network_status
  notification: send_routine, send_important

ESCALATION_REQUIRED (needs active owner session):
  notification.send_urgent
  security.modify_rules
  system.modify_settings
  filesystem.write_outside_sandbox
  security.containment_override
```

**HeartbeatMonitor** — Dead man's switch:
- 30-second heartbeat interval
- 3 missed heartbeats triggers safe shutdown
- Urgent mobile notification on failure
- Watchdog thread (daemon, minimal overhead)

**ResourceMonitor** — Usage caps + anomaly detection:
- Hard caps: tokens/day, API calls/hour, memory usage
- Z-score anomaly detection (alert at 3+ standard deviations)
- Rolling window of 100 data points per metric
- Daily reset for cumulative counters

**TransparencyDashboard** — `GET /dashboard`:
- HTML page: action log, resource usage, threat status, scope violations, health
- JSON API: same data for widget/mobile consumption
- Real-time threat map (blocked IPs with geolocation)

**New files:**
- `engine/src/jarvis_engine/security/action_auditor.py`
- `engine/src/jarvis_engine/security/scope_enforcer.py`
- `engine/src/jarvis_engine/security/heartbeat.py`
- `engine/src/jarvis_engine/security/resource_monitor.py`
- `engine/src/jarvis_engine/security/transparency.py`

### Pillar 5: Identity & Family Protection

**BreachMonitor** (proactive scheduled task):
- HaveIBeenPwned API integration (API key: $3.50/month)
- k-anonymity password checking (only sends first 5 chars of SHA-1)
- Family email registry in config
- Daily breach check, IMPORTANT-level alert on new findings
- Graceful fallback when no API key configured (skip silently)

**TyposquatMonitor** (proactive scheduled task):
- Generates variants: character omission, adjacent key, doubling, homoglyph, TLD swap
- DNS resolution check for variant registration
- Weekly scan, IMPORTANT-level alert on new registrations

**ImpersonationDetector** (proactive scheduled task):
- Username variant generation (underscore, "official", "real", digit, homoglyph)
- Platform profile existence check (Twitter/X, GitHub, Instagram)
- Weekly scan, uses public profile pages (no API keys needed)

**FamilyShield** (configuration):
- Family member registry: name, emails, usernames, domains to monitor
- Stored in encrypted config (Fernet, same key derivation as sync)
- All identity protection modules reference this registry

**New file:** `engine/src/jarvis_engine/security/identity_shield.py`

### Pillar 6: Home Network Security

**HomeNetworkMonitor** (background service):
- ARP table scan every 5 minutes (rogue device + ARP poisoning detection)
- DNS cache analysis (DGA detection via Shannon entropy > 3.5, C2 beaconing via interval regularity)
- Active connection monitoring (known-bad ports, suspicious high-port connections)
- Process-to-connection mapping via PID (identify which program is communicating)

**KnownDeviceRegistry**:
- JSON file of approved devices (MAC, friendly name, type)
- Auto-learn mode during setup (approve all current devices)
- New unknown device triggers IMPORTANT-level alert
- Device categorization: mobile, laptop, IoT, infrastructure

**WiFiMonitor** (optional, requires scapy):
- Deauthentication attack detection (threshold: 10+ deauth frames from one source)
- Graceful skip if scapy not available (Windows limitation)
- URGENT-level alert on confirmed deauth attack

**New file:** `engine/src/jarvis_engine/security/network_defense.py`

### Pillar 7: Offensive Response & Legal Retaliation

**What we DO (legal):**

1. **Evidence preservation** — Forensic-grade hash-chain logs with chain-of-custody metadata, exportable as timestamped ZIP with verification summary
2. **Automated reporting** — AbuseIPDB API report, ISP abuse contacts via RDAP, law enforcement report templates (IC3/FBI format)
3. **Threat intelligence sharing** — Contribute confirmed attack patterns to community feeds
4. **Local process termination** — Kill malicious processes detected on system (via PID)
5. **Permanent IP blackholing** — Lifetime ban for confirmed attackers (persisted in SQLite)
6. **Honeypot intelligence gathering** — Catalog attacker TTPs, fingerprint tools, build behavioral profiles
7. **Network isolation** — Auto-quarantine compromised devices on the network (ARP-level if possible)

**What we DON'T do (CFAA boundary):**
- Access attacker systems (hack back)
- Deploy malware against attackers
- DDoS attacker infrastructure
- Intercept attacker communications

**ThreatNeutralizer** pipeline:
```
Confirmed Threat
  -> ForensicLogger.preserve_evidence()
  -> IPTracker.permanent_block()
  -> AttackPatternMemory.catalog()
  -> AbuseReporter.report_to_abuseipdb()
  -> AbuseReporter.report_to_isp()
  -> AbuseReporter.generate_law_enforcement_package()
  -> AlertChain.notify_owner()  [with full evidence summary]
```

If malicious local process detected:
```
  -> ProcessTerminator.kill_process(pid)
  -> ForensicLogger.record_termination()
  -> AlertChain.notify_owner()
```

**New file:** `engine/src/jarvis_engine/security/threat_neutralizer.py`

## Files Summary

### New files (13):
1. `security/orchestrator.py` — Pipeline integration
2. `security/owner_session.py` — Argon2 session auth
3. `security/threat_intel.py` — AbuseIPDB/OTX/abuse.ch feeds
4. `security/abuse_reporter.py` — Automated reporting
5. `security/action_auditor.py` — Bot governance audit trail
6. `security/scope_enforcer.py` — Operational boundary layer
7. `security/heartbeat.py` — Dead man's switch
8. `security/resource_monitor.py` — Usage caps + anomaly detection
9. `security/transparency.py` — Dashboard endpoints
10. `security/identity_shield.py` — Breach/typosquat/impersonation monitoring
11. `security/network_defense.py` — Home network + device registry
12. `security/threat_neutralizer.py` — Legal offensive response
13. Test files for each new module

### Modified files:
- `mobile_api.py` — Integrate SecurityOrchestrator, add auth/dashboard/audit endpoints
- `app.py` — Register defense command handlers, instantiate security modules
- `security/__init__.py` — Export new modules
- `security/alert_chain.py` — Wire actual notification dispatch (mobile API, widget, email)
- `security/containment.py` — Wire actual service stop in FULL_KILL
- `security/injection_firewall.py` — Implement Layer 3 semantic analysis
- `security/session_manager.py` — Use secrets.token_hex instead of uuid4

### Dependencies (graceful-degrade):
- `argon2-cffi` — Argon2id (falls back to PBKDF2)
- `httpx` — Async HTTP for threat intel (falls back to urllib/requests)
- `scapy` — WiFi monitoring (optional, skipped if missing)

## Design Principles

1. **Defense-in-depth** — Multiple independent layers, no single point of failure
2. **Frictionless for owner** — One password login, then everything works
3. **Aggressive for attackers** — Auto-escalation, permanent bans, authority reporting
4. **Full transparency** — Every action audited, every decision logged, dashboard visible
5. **Legal compliance** — No hack-back, evidence-grade logging, proper chain of custody
6. **Graceful degradation** — Missing API keys or optional deps reduce capability, never crash
7. **AI alignment** — Scope enforcement, resource caps, anomaly detection, heartbeat monitoring
