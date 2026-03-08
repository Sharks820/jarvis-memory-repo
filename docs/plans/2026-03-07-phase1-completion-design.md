# Phase 1 Completion Design — v5.0 Reliability Core

**Date:** 2026-03-07
**Phase:** v5.0 Phase 1 (Reliability Core + Resource Control)
**Scope:** Complete all remaining 14-02 tasks to 100%
**Baseline:** 5077 tests passing, 0 failures, ruff clean

## Overview

Six task areas remain to fully close Phase 1. This design pushes each beyond
the minimum plan requirements toward world-class infrastructure. Every task
ships with contract tests, desloppify scan gate, and bug/error rescan.

## Task A: Cross-LLM Context Continuity — Full State Machine

### New Module: `conversation_state.py`

**ConversationStateManager** — persistent, cross-session, entity-aware state
machine that ensures no information is ever lost across provider switches,
daemon restarts, or session boundaries.

**Core state object (ConversationSnapshot)**:
- `session_id`: uuid4, generated on daemon startup, persists until explicit reset
- `checkpoint_id`: monotonic counter, incremented on each compaction/summary
- `rolling_summary`: LLM-generated summary of conversation history, updated
  every 10 turns or when context budget is 80% consumed
- `anchor_entities`: extracted named entities (people, places, orgs, dates,
  amounts) that MUST survive any provider switch
- `unresolved_goals`: tasks/questions the user started but hasn't finished,
  auto-extracted from conversation flow
- `prior_decisions`: key conclusions/choices made ("we decided to use X",
  "let's go with approach Y")
- `referenced_artifacts`: file paths, URLs, mission IDs, code snippets
  mentioned in conversation
- `active_model`: current provider identifier
- `model_history`: list of (model, turn_count, switch_reason) transitions
- `turn_count`: total turns in current session
- `created_at`, `updated_at`: timestamps

**Conversation replay buffer**:
- Rolling deque of last 30 turns (user+assistant pairs) serialized to disk
- On provider switch: inject `rolling_summary` + last 5 raw turns +
  `anchor_entities` + `unresolved_goals` into the new provider's context
- On daemon restart: reload full state, inject summary into first prompt

**Deep entity extraction pipeline** (runs on each assistant response):
- Named entities via regex patterns + personal_vocab.txt matching
- Decision/commitment detection ("I'll do X", "let's go with Y", "agreed")
- Artifact references (file paths, URLs, command outputs)
- Unresolved task detection ("we still need to", "todo:", "next step:")
- Goal completion detection (removes from unresolved_goals when addressed)

**Provider-normalized conversation timeline**:
- `ConversationTimeline` stores each turn as:
  `(timestamp, model, role, content_hash, entities_extracted, summary_snippet)`
- Queryable: "what did we discuss about X?" across model boundaries
- Searchable via FTS on summary_snippet field
- Timeline entries survive session boundaries (persistent SQLite table)

**Telemetry**:
- `continuity_reconstruction` events: entities_preserved, summary_length,
  replay_turns_injected, model_from, model_to, goals_carried, success
- `entity_extraction` events: entities_found, types, confidence
- `session_resume` events on daemon restart: state_age_seconds, entities_loaded

**Contract tests (8 scenarios)**:
1. cloud→local swap preserves entities + goals
2. local→cloud swap preserves entities + goals
3. fallback-after-failure preserves context
4. daemon restart resumes conversation seamlessly
5. Cross-session entity recall ("what was that person's name from earlier?")
6. Goal tracking persists unresolved tasks across 10+ turns
7. Decision recall survives 3 model switches
8. Rolling summary quality degrades gracefully under long conversations

**Integration points**:
- `voice_pipeline._prepare_history()` injects state into prompts
- `gateway/cli_providers.py` triggers checkpoint on compaction
- `daemon_loop.py` loads state on startup, saves on shutdown
- `mobile_api.py` exposes `GET /conversation/state` for mobile visibility

---

## Task C: Voice UX — Full Pipeline Telemetry + Widget Indicator

### Widget listening indicator

Wire `listening_state` activity events into the animated orb state machine:
- `arming` → amber pulse (2Hz), label "Arming..."
- `listening` → bright green breathing (1Hz), label "Listening..."
- `processing` → blue rotating arcs, label "Processing..."
- `executing` → cyan steady glow, label "Executing..."
- `speaking` → violet pulse, label "Speaking..."
- `error` → red flash (3Hz), label "Error"
- `idle` → default orb animation

Text label displayed below the orb, auto-sized, with fade transitions
between states. Accessibility: emit state changes to system accessibility API.

### Full pipeline latency telemetry

Instrument every stage of the voice pipeline with nanosecond timestamps:
- `vad_speech_onset_ts`: when Silero VAD first detects speech
- `vad_speech_end_ts`: when VAD detects end of utterance
- `wake_word_detected_ts`: when wake word is confirmed
- `transcription_start_ts`: when audio is sent to STT backend
- `transcription_end_ts`: when transcript text is received
- `intent_classification_ts`: when intent is determined
- `command_dispatch_ts`: when command handler is invoked
- `response_ready_ts`: when response text is generated
- `tts_start_ts`: when TTS begins speaking
- `tts_end_ts`: when TTS finishes speaking

**Derived metrics** (computed and stored):
- `capture_to_transcript_ms` = transcription_end - vad_speech_onset
- `transcript_to_response_ms` = response_ready - transcription_end
- `end_to_end_ms` = tts_end - vad_speech_onset (full user-perceived latency)
- `vad_duration_ms` = vad_speech_end - vad_speech_onset

**SLO enforcement**:
- Targets: capture_to_transcript p50 < 1.5s, p95 < 4s
- End-to-end p50 < 3s, p95 < 8s
- Store latency samples in rolling deque(500)
- Dashboard: `GET /voice/latency` returns p50, p95, p99, sample_count
- Alert if p95 exceeds target for 10 consecutive samples

### Voice pipeline health events

Emit structured `voice_pipeline_health` events every 100 utterances:
- Total utterances processed, success rate, average confidence
- Backend distribution (parakeet/deepgram/groq/local counts)
- Fallback trigger rate, average latency per backend

---

## Task D: Mission/Activity Transparency — Real Progress + Full Controls

### Step-driven progress

Refactor mission execution to use a step counter instead of hardcoded
percentages:

```
class MissionStep:
    name: str           # "search_provider_arxiv"
    description: str    # "Searching arXiv for quantum computing papers"
    weight: float       # 1.0 (default), higher for expensive steps
    status: str         # pending/running/completed/failed/skipped
    elapsed_ms: int
    artifacts_produced: int
```

Total progress = sum(completed_step_weights) / sum(all_step_weights) * 100

Each mission pre-declares its steps on creation. Progress bars are
truth-driven, not timer-driven.

### Expanded activity stream payload

Every mission activity event includes:
- `stage`: search | verify | ingest | complete | failed
- `substep`: human-readable ("Verifying fact: Earth orbits the Sun")
- `elapsed_ms`: time since mission started
- `progress_pct`: real step-driven percentage
- `artifact_count`: items ingested/produced so far
- `current_action`: what the mission is doing RIGHT NOW
- `correlation_id`: links this event to mission_id + session_id

### "Now working on" dashboard panel

`GET /widget-status` includes:
```json
{
  "now_working_on": {
    "mission_id": "abc123",
    "mission_topic": "quantum computing",
    "current_step": "Verifying arXiv paper claims",
    "progress_pct": 67,
    "elapsed_s": 45,
    "artifacts_so_far": 3
  }
}
```

Null when idle. Widget displays this as a live-updating card below the orb.

### Full mission lifecycle controls

- `POST /missions/create` (exists) — create new mission
- `POST /missions/stop` (exists via cancel) — immediate cancellation
- `POST /missions/restart` — restart a failed/cancelled mission,
  preserving prior findings + context
- `POST /missions/pause` — pause running mission, save checkpoint
- `POST /missions/resume` — resume paused mission from checkpoint
- `POST /missions/schedule` — schedule recurring mission
  (daily/weekly/cron expression)
- `GET /missions/{id}/steps` — get detailed step breakdown
- `GET /missions/{id}/artifacts` — list produced artifacts
- `GET /missions/active` — list all running/paused missions

### Learning dashboard enrichment

Intelligence dashboard (`GET /intelligence/growth`) adds:
- `missions_completed_7d`, `missions_failed_7d`
- `facts_learned_7d`, `corrections_applied_7d`
- `top_topics_learned`: list of (topic, fact_count) from last 7 days
- `knowledge_graph_growth`: nodes/edges added in last 7 days
- `mission_success_rate_trend`: 4-week rolling average

---

## Task E: Autonomous Self-Diagnosis — Full Health Engine

### New module: `self_diagnosis.py`

**DiagnosticEngine** — comprehensive health checker with ranked findings:

**Health check categories**:

1. **Memory pressure**: current usage vs budgets, growth trend over last hour
2. **Database integrity**: PRAGMA integrity_check, FTS5 rebuild-needed detection,
   WAL file size, page count growth rate
3. **Gateway connectivity**: ping each configured provider (Groq, Anthropic,
   Ollama, etc.), measure response time, detect degraded performance
4. **Mission health**: stuck missions (running > 10min), orphaned missions
   (no activity events for > 5min), excessive failure rate (> 50% last 10)
5. **Activity feed health**: staleness detection, event rate anomalies,
   missing correlation IDs
6. **Security module health**: orchestrator status, blocklist size,
   forensic log integrity (hash chain validation)
7. **Embedding model health**: loadability, inference latency benchmark,
   cache hit rate
8. **Voice pipeline health**: STT backend availability, VAD state,
   microphone accessibility
9. **Sync health**: last successful sync age, sync queue depth,
   changelog trigger integrity
10. **Knowledge graph health**: orphan nodes, contradiction count,
    fact lock staleness, integrity metrics

**Ranked issue output**:
```python
@dataclass
class DiagnosticIssue:
    severity: str          # critical / high / medium / low / info
    component: str         # "memory", "database", "gateway", etc.
    description: str       # human-readable
    suggested_fix: str     # what to do
    auto_fixable: bool     # can DiagnosticEngine fix this automatically
    fix_action: str | None # "vacuum_db", "restart_embedding", etc.
    evidence: dict         # supporting metrics
```

**Auto-fix actions** (approval-gated):
- `vacuum_db`: Run VACUUM + ANALYZE on memory database
- `rebuild_fts`: Rebuild FTS5 index
- `restart_embedding`: Reload embedding model
- `clear_stuck_missions`: Cancel missions stuck > 30 minutes
- `prune_wal`: Checkpoint WAL file to reduce size
- `rotate_forensic_log`: Rotate oversized forensic log
- `clear_stale_nonces`: Prune expired nonce cache entries

**Approval pipeline**:
1. DiagnosticEngine runs checks → produces ranked issues list
2. Auto-fixable issues presented to owner via activity feed + widget notification
3. Widget shows "Health Alert: [N] issues found. [M] auto-fixable."
4. Owner approves/denies via widget button or `POST /diagnostics/approve/{id}`
5. On approval: execute fix action, verify success, log result
6. On denial: log decision, suppress for 24 hours

**CQRS integration**:
- `DiagnosticRunCommand(full_scan: bool, categories: list[str])` →
  `DiagnosticRunResult(issues: list[DiagnosticIssue], healthy: bool, score: int)`
- `DiagnosticApproveCommand(issue_id: str)` →
  `DiagnosticApproveResult(applied: bool, verification: str)`
- Health score: 0-100 based on weighted issue severity

**Scheduled diagnostics**:
- Full scan runs every 6 hours automatically in daemon loop
- Quick scan (memory + database + gateway only) runs every 30 minutes
- Results persisted to `diagnostics_history.jsonl` with timestamps
- Dashboard: `GET /diagnostics/status` returns latest health score +
  issues + trend

**Audit trail**: All diagnosis runs, findings, approvals, denials, and
fix executions logged to `.planning/runtime/diagnostics_audit.jsonl`
with SHA-256 chain integrity.

---

## Task F: Memory Hygiene — Intelligent Signal Extraction

### Quality classification system

Add `signal_quality` column to memory records with 4 tiers:
- `high_signal`: personal facts, commitments, important decisions, learning
  outcomes, user preferences, calendar events, financial data
- `contextual`: conversation context, task details, session-specific info,
  intermediate reasoning steps
- `ephemeral`: greetings, small talk, acknowledgments, repeated content,
  status checks
- `junk`: empty/garbled entries, hallucination artifacts, test data,
  malformed content, abandoned drafts

### Multi-pass classification engine

**Pass 1 — Rule-based fast classifier**:
- Length analysis: < 10 chars → likely junk, > 500 chars → likely contextual+
- Keyword patterns: greeting words → ephemeral, "remember", "important" → high_signal
- Entity density: high entity count → likely high_signal
- Duplicate detection: near-duplicate content (Jaccard > 0.8) → ephemeral
- Freshness: > 90 days old + no cross-references → candidate for downgrade

**Pass 2 — Embedding-based similarity classifier**:
- Compare against centroid embeddings for each quality tier
- Records close to "junk" centroid but with high entity count get manual review
- Build centroids from manually labeled seed set (50 per tier)

**Pass 3 — LLM judge (optional, for ambiguous records)**:
- Send ambiguous records to local Ollama with classification prompt
- Only triggered for records where Pass 1 and Pass 2 disagree
- Results cached to avoid re-classification

### Cleanup pipeline

`MemoryHygieneCommand` (runs weekly or on-demand):
1. Scan all unclassified records → classify
2. Build cleanup candidates list (junk + old ephemeral)
3. Preview: show record count per tier, top 10 cleanup candidates
4. Auto-archive rules:
   - `junk` entries: archived after 3 days (soft delete, recoverable 30 days)
   - `ephemeral` entries: archived after 14 days
   - `contextual` entries: summarized + archived after 60 days
   - `high_signal`: NEVER auto-cleaned
5. Summary report: "Cleaned X junk, archived Y ephemeral, summarized Z contextual"

### Anti-loss guardrails (cannot be overridden)

Protected from cleanup regardless of classification:
- Facts in knowledge graph (cross-referenced via fact_id)
- Active mission context (mission_id referenced)
- User-explicit "remember X" entries (tagged `user_pinned`)
- Records with > 3 cross-references in other records
- Records referenced in conversation_state anchor_entities
- Records < 7 days old (cooling period before any cleanup)
- Records with `high_signal` classification at any confidence

### Dashboard

`GET /memory/hygiene` returns:
- Classification distribution: {high_signal: N, contextual: N, ephemeral: N, junk: N, unclassified: N}
- Cleanup candidates count
- Last cleanup run timestamp + results
- Protected records count
- Storage savings from last cleanup (bytes)

---

## Task H: Desloppify Score Tracking + Systematic Loop

### Automation

- `scripts/desloppify-cycle.sh`: scan → triage → report
- After each scan, append to `.planning/desloppify-trendline.csv`:
  `date,strict_score,findings_total,findings_open,findings_resolved`
- Every implementation task in this plan includes a desloppify gate:
  commit MUST NOT regress the score

### Integration with diagnostics

- DiagnosticEngine includes a "code quality" check that reads the latest
  desloppify score and flags if below threshold
- Score tracked as a health metric alongside test count and lint status

### Target

Every commit improves or maintains the desloppify score. Phase 1 exit
requires strict score > 50 (up from baseline 33.2).

---

## Execution Model

**GSD workflow** with strict phase gates:
1. Each task implemented as a separate GSD phase
2. After each phase: full test suite + bug/error scan + desloppify gate
3. No phase starts until previous phase is clean
4. All phases committed atomically with verification evidence

**Implementation order**:
1. Task A (Context Continuity) — foundation for everything else
2. Task C (Voice UX) — immediate user-visible improvement
3. Task D (Mission Transparency) — mission progress becomes truthful
4. Task E (Self-Diagnosis) — system monitors itself
5. Task F (Memory Hygiene) — system cleans itself
6. Task H (Desloppify) — runs continuously as quality gate throughout

**Exit criteria**:
- All 8 contract tests pass for context continuity
- Voice latency SLOs met in test harness
- Mission progress bars driven by real steps
- DiagnosticEngine runs full scan successfully
- Memory hygiene classifies records with > 80% accuracy on seed set
- Desloppify strict score > 50
- Full test suite: 0 failures
- Bug/error rescan: 0 CRITICAL or HIGH findings
