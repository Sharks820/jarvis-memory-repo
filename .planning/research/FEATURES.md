# Feature Research: AI Personal Assistant

**Domain:** Local-first AI personal assistant (JARVIS-class)
**Researched:** 2026-02-22
**Confidence:** HIGH (broad competitive landscape well-documented; specific implementation patterns verified across multiple sources)

## Competitive Landscape

Analyzed: Google Gemini (Personal Intelligence), OpenAI ChatGPT (Pulse, Operator, Agent Mode), Amazon Alexa+, Apple Siri/Apple Intelligence, OpenClaw, isair/jarvis, Stevens (SQLite-based assistant), Mem0, MemoryOS, Hume AI, and dozens of open-source JARVIS-inspired projects on GitHub.

The AI assistant space in 2026 is splitting into three tiers:
1. **Platform assistants** (Gemini, Siri, Alexa) -- tied to ecosystems, broad but shallow
2. **AI chatbots with memory** (ChatGPT, Claude) -- deep reasoning but generic, cloud-dependent
3. **Personal AI agents** (OpenClaw, custom builds) -- deep personalization, autonomous action, owner-controlled

This project targets tier 3 with the depth of tier 2. The goal is not to compete with Google's reach or Amazon's smart home dominance, but to build something no corporate product can: a private, self-improving, deeply personalized assistant that knows its owner better than any cloud service ever could.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features that any serious AI personal assistant must have. Missing these and the product feels like a toy.

| Feature | Why Expected | Complexity | Existing? | Notes |
|---------|--------------|------------|-----------|-------|
| **Natural language conversation** | Every AI chatbot does this; users assume fluent dialogue | MEDIUM | Partial (CLI commands) | Currently command-based. Need conversational layer on top of existing command structure. |
| **Persistent memory across sessions** | ChatGPT, Claude, Gemini all have cross-session memory now | HIGH | Partial (JSONL event log, brain records) | MemoryStore is append-only JSONL. Brain memory uses keyword matching. No semantic search, no embeddings, no database. This is the single biggest gap. |
| **Daily briefing / morning summary** | Stevens, ChatGPT Pulse, Poke, and every serious assistant deliver proactive morning briefs | MEDIUM | Partial (build_daily_brief exists) | OpsSnapshot + daily brief builder exists but connectors are stubs. Need real data flowing in. |
| **Calendar awareness** | Cannot manage someone's day without knowing their schedule | MEDIUM | Stub (connector defined, not implemented) | ConnectorDefinition for calendar exists but requires env vars. Need actual ICS/Google Calendar integration. |
| **Email triage** | Proactive email awareness is standard in Gemini, ChatGPT, Reclaim | MEDIUM | Stub (connector defined) | Same as calendar -- definition exists, implementation needed. |
| **Task management** | Basic task tracking is table stakes for any assistant | LOW | Partial (tasks.json fallback) | Tasks connector has local file fallback. Needs integration with actual task sources. |
| **Voice output (TTS)** | Siri, Alexa, Google all speak; a JARVIS that can't speak is not JARVIS | LOW | YES (Edge-TTS + Windows Speech) | Working well. en-GB-ThomasNeural voice. Solid foundation. |
| **Mobile access** | Must be reachable from phone, not just desktop | MEDIUM | YES (HMAC-signed mobile API) | HTTP API with replay protection exists. Quick access HTML panel exists. |
| **Security / owner verification** | Personal assistant with life data must verify identity | MEDIUM | YES (owner_guard, master password, trusted devices) | Solid implementation with tiered capability authorization. |
| **Bill / subscription tracking** | Managing finances is core life management | LOW | Stub (connector defined) | Definitions exist, needs real data sources. |
| **Medication reminders** | Health management is expected for a life assistant | LOW | Partial (medications in OpsSnapshot) | Data structure exists in life_ops. Needs proactive reminder system. |

### Differentiators (Competitive Advantage)

Features that go beyond what commercial assistants offer. These make Jarvis better than Siri/Alexa/Google.

| Feature | Value Proposition | Complexity | Existing? | Notes |
|---------|-------------------|------------|-----------|-------|
| **Semantic memory with embeddings** | Unlike keyword-matching memory, semantic search finds what you meant, not just what you said. Mem0 shows 26% accuracy improvement over OpenAI's memory and 91% faster retrieval. | HIGH | NO (brain_memory uses keyword matching) | Use sentence-transformers for local embeddings + SQLite FTS5. The brain_memory module needs to be rebuilt around vector similarity search. |
| **Three-tier memory hierarchy (STM/MTM/LTM)** | MemoryOS (EMNLP 2025 Oral) showed 49% F1 improvement using hierarchical memory over flat storage. Short-term for active context, mid-term for recurring patterns, long-term for permanent knowledge. | HIGH | NO (flat JSONL currently) | Brain records are flat. Need tiered architecture with heat-driven promotion/eviction between tiers, matching the MemoryOS pattern. |
| **Knowledge graph with fact interconnection** | Knowledge graphs prevent contradictory information and enable reasoning across facts. KGoT achieved 29% improvement in task success. Goes far beyond what ChatGPT or Gemini offer. | HIGH | NO | Build a local knowledge graph (NetworkX or SQLite-backed) that links facts with typed relationships. Enable contradiction detection when new facts conflict with existing ones. |
| **Anti-regression locks** | No commercial assistant guarantees it won't forget what it learned. Jarvis should cryptographically protect learned knowledge with signed snapshots that prove nothing was lost. | MEDIUM | Partial (memory_snapshots.py has signed snapshots) | create_signed_snapshot and verify_signed_snapshot exist. Need to extend into regression detection: compare knowledge counts, verify no facts disappeared between snapshots. |
| **Multi-model intelligent routing** | Use Opus for complex reasoning, Sonnet for routine, local Ollama for privacy-sensitive tasks. 30-70% cost reduction per industry benchmarks while maintaining quality. | MEDIUM | Partial (basic router exists) | ModelRouter exists but only routes on risk/complexity to local vs cloud. Need model-specific routing: Opus for hard reasoning, Sonnet for summarization, local for embedding/classification. |
| **Proactive assistance (cron-driven)** | OpenClaw's heartbeat system and ChatGPT Pulse show the industry moving from reactive to proactive. Jarvis should act without being asked: "You have a meeting in 30 minutes and haven't eaten." | HIGH | Partial (daemon mode exists) | Runtime control has idle detection and gaming pause. Needs scheduled cron-style proactive checks: morning brief, bill due alerts, health reminders, calendar prep. |
| **Personality layer with contextual humor** | Stevens' creator noted personality made the tool "significantly more enjoyable." Jarvis's British butler persona with mild humor is a genuine differentiator over sterile corporate assistants. | MEDIUM | Partial (persona.py exists) | PersonaConfig with mode/humor_level/style exists. compose_persona_reply function exists. Need deeper personality integration: situational humor, tone adaptation based on context (serious for health, light for gaming). |
| **Learning missions (self-directed research)** | No commercial assistant autonomously researches topics to expand its own knowledge. Jarvis can create and run learning missions to deepen expertise in areas the owner cares about. | MEDIUM | YES (learning_missions.py + web_research.py) | Create/load/run missions with web research already implemented. This is a genuine differentiator already built. Needs integration with the memory system to permanently retain findings. |
| **Intelligence dashboard with growth tracking** | Quantified self-improvement with milestones and score tracking. No other assistant shows you how much smarter it's getting. | MEDIUM | YES (intelligence_dashboard.py + growth_tracker.py) | Dashboard with targets, milestones, and eval runs exists. Good foundation for proving the assistant is improving. |
| **Phone guard / spam defense** | Active protection against spam calls goes beyond what Siri/Google offer. Jarvis analyzes call logs, detects spam patterns, and generates block actions. | LOW | YES (phone_guard.py) | Full implementation with spam detection, block actions, and reporting. Unique feature for a personal assistant. |
| **Desktop widget for ambient presence** | Always-visible widget makes Jarvis feel present, not buried in a terminal. | LOW | YES (desktop_widget.py) | Widget implementation exists. |
| **Bidirectional mobile-desktop sync** | Unlike cloud assistants where you're locked to one ecosystem, Jarvis syncs knowledge bidirectionally between devices with conflict resolution. | HIGH | Partial (resilience.py has sync skeleton) | run_mobile_desktop_sync exists in resilience module. Needs encrypted diff-based protocol with proper conflict resolution. |

### Revolutionary (Never-Before-Done)

Features that would make this the greatest personal assistant ever built. These don't exist in any commercial or open-source product today.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Continuous learning engine with knowledge extraction** | Every interaction, every email, every calendar event should automatically extract and permanently retain useful knowledge. Not just "memory" -- active learning that builds understanding over time. No assistant today does this. ChatGPT's memory is shallow preferences; this is deep knowledge accumulation. | VERY HIGH | The ingest pipeline exists but is thin (no chunking, no enrichment per PROJECT.md). Need: automatic entity extraction, relationship mapping, knowledge categorization, confidence scoring, and permanent storage with provenance tracking. |
| **Self-improving capability with auditable verification** | The assistant should measurably get better at its job over time, and prove it. Track task success rates, identify failure patterns, adjust strategies. Golden task eval system (growth_tracker) is the seed. No commercial assistant offers verifiable, auditable self-improvement. | HIGH | Growth tracker with golden tasks and eval runs exists. Extend with: failure pattern analysis, strategy adjustment based on outcomes, measurable capability scores that only go up. |
| **Owner behavioral model** | Build a deep model of the owner's preferences, patterns, and routines. Not just "prefers dark mode" but "gets stressed before deadlines, needs proactive task breakdown by Wednesday, tends to forget medications on gaming nights." Pattern recognition across all life domains. | VERY HIGH | No existing implementation. Requires: behavioral pattern extraction from interaction history, routine detection, preference graph, contextual prediction. The habit tracking research shows 25% better goal success and 40% better retention with AI pattern recognition. |
| **Temporal knowledge with decay awareness** | Knowledge should have temporal metadata. "The pharmacy closes at 9pm" is permanent. "The milk expires Thursday" decays. "Your meeting with Dr. Smith is at 3pm" is ephemeral. No assistant distinguishes knowledge temporality. | HIGH | Brain records have timestamps but no temporal classification. Need: temporal tagging on all facts, automatic decay/archive for expired information, proactive alerts before time-sensitive knowledge expires. |
| **Cross-domain reasoning** | Connect insights across life domains: "You have a doctor's appointment tomorrow but your medication list in the health branch hasn't been updated since October. Want me to compile your current medications for the visit?" No assistant crosses domain boundaries like this. | HIGH | Branch-based filing (9 branches) exists but branches are isolated silos. Need: cross-branch query capability, relationship detection between domains, proactive cross-domain insights. |
| **Adversarial self-testing** | The assistant periodically tests itself: "Can I still recall the owner's medication schedule? Can I still produce accurate daily briefs?" Self-testing with automatic alerting if capability degrades. | MEDIUM | Golden task evaluation exists in growth_tracker. Extend to continuous self-testing on real knowledge: periodic quizzes on retained facts, alerts when recall accuracy drops. |
| **Emotional context awareness** | Detect the owner's emotional state from interaction patterns (short responses = frustrated, lots of questions = confused, late-night activity = stressed) and adapt behavior accordingly. Not sentiment analysis of text -- behavioral pattern inference. | HIGH | No existing implementation. Hume AI and emotional AI market ($37B by 2026) validate demand. For a local-first assistant, this means: interaction pattern analysis, response tone adaptation, proactive support during detected stress. |
| **Voice-activated ambient mode** | Always-listening mode where saying "Jarvis" triggers attention, like the isair/jarvis project. Combined with the existing Edge-TTS output, this creates true hands-free interaction. "Jarvis, what's my day look like?" from across the room. | MEDIUM | Edge-TTS output works. Need: continuous audio input monitoring, wake word detection (possibly Porcupine or similar), speech-to-text pipeline (Whisper), then routing to existing command system. |

### Anti-Features (Things to Deliberately NOT Build)

Features that seem appealing but would hurt the project.

| Anti-Feature | Why Tempting | Why Problematic | What to Do Instead |
|--------------|-------------|-----------------|-------------------|
| **Cloud-hosted deployment** | Easier access from anywhere, no local machine dependency | Destroys the core value proposition. Privacy is non-negotiable. Every cloud assistant already exists. The differentiator IS local-first. | Keep all core data local. Use cloud APIs only for inference (Opus/Sonnet). Encrypted sync for mobile access. |
| **Multi-user support** | Seems like a natural extension | Massive complexity increase. Owner model, personality calibration, and security all assume single user. Multi-user dilutes everything. | Stay single-owner. Family members get limited read access through mobile API if needed. |
| **Custom LLM training** | "Train a model on my data" sounds powerful | Impractical for single-user. Training requires massive data and compute. Fine-tuning is fragile. RAG + prompting + memory achieves 90% of the benefit at 1% of the cost. | Use RAG with local embeddings for personalization. Use prompt engineering with persona context. Let the memory system provide personalization, not model weights. |
| **Smart home control** | Every assistant does smart home (Alexa, Google, Siri) | Jarvis's strength is knowledge and life management, not hardware control. Smart home is a solved problem. Adding it fragments focus. | If needed later, use MCP integration to connect to Home Assistant. Don't build hardware control natively. |
| **Real-time streaming voice conversation** | Gemini Live, ChatGPT Voice Mode are impressive | Requires always-on audio pipeline, complex latency management, interruption handling. Huge engineering effort for marginal gain over TTS + keyboard. | Keep Edge-TTS for output. Add wake-word activation for input. Full duplex conversation is a v3+ feature. |
| **General-purpose web browsing agent** | OpenAI Operator can browse the web and fill forms | CUA achieves only 38% success on real tasks. Brittle, unpredictable, and dangerous with real accounts. The technology isn't reliable enough yet. | Focused web research (already implemented) for information gathering. No autonomous form-filling or account interaction. |
| **Plugin/extension marketplace** | Extensibility sounds good on paper | Single-user assistant doesn't need a marketplace. Custom integrations for one person's needs beat generic plugins. | Build specific connectors for the owner's actual services. MCP protocol support if extensibility is truly needed later. |
| **Mobile native app** | Seems more polished than a web panel | Massive development effort for marginal gain. HTTP API + quick-access web panel on Samsung Galaxy is sufficient. PWA if polish is needed. | Keep mobile HTTP API. Improve quick_access.html as a PWA with offline support. |

---

## Feature Dependencies

```
[SQLite + FTS5 Database]
    |
    +---> [Local Embeddings (sentence-transformers)]
    |         |
    |         +---> [Semantic Memory Search]
    |         |         |
    |         |         +---> [Three-Tier Memory Hierarchy]
    |         |         |         |
    |         |         |         +---> [Anti-Regression Locks] (enhanced)
    |         |         |         |
    |         |         |         +---> [Continuous Learning Engine]
    |         |         |                   |
    |         |         |                   +---> [Owner Behavioral Model]
    |         |         |                   |
    |         |         |                   +---> [Self-Improving Capability]
    |         |         |
    |         |         +---> [Knowledge Graph]
    |         |                   |
    |         |                   +---> [Contradiction Detection]
    |         |                   |
    |         |                   +---> [Cross-Domain Reasoning]
    |         |                   |
    |         |                   +---> [Temporal Knowledge]
    |         |
    |         +---> [Context Window Management]
    |                   |
    |                   +---> [Intelligent Summarization]
    |
    +---> [Real Connector Integrations]
              |
              +---> [Calendar (Google/ICS)] ---> [Daily Briefing] (enhanced)
              |
              +---> [Email (IMAP)] ---> [Email Triage]
              |
              +---> [Proactive Assistance] (requires calendar + tasks + memory)
              |
              +---> [Medication Reminders] (requires proactive system)

[Multi-Model Router] (enhanced)
    |
    +---> [Cost-Optimized Inference]
    |
    +---> [Opus for Reasoning, Sonnet for Routine, Local for Privacy]

[Persona Layer] (enhanced)
    |
    +---> [Emotional Context Awareness]
    |
    +---> [Contextual Humor Adaptation]

[Voice Input Pipeline]
    |
    +---> [Wake Word Detection (Porcupine/similar)]
    |
    +---> [Speech-to-Text (Whisper)]
    |
    +---> [Voice-Activated Ambient Mode]

[Mobile-Desktop Sync]
    |
    +---> [Encrypted Diff Protocol]
    |
    +---> [Conflict Resolution]
    |
    +---> [Bidirectional Learning Sync]
```

### Dependency Notes

- **Everything depends on the memory system**: The SQLite + embeddings foundation is the critical path. Without semantic memory, none of the advanced features (knowledge graph, learning engine, behavioral model) can work.
- **Connectors enable proactive features**: Daily briefing, proactive assistance, and cross-domain reasoning all require real data flowing in from calendar, email, and tasks. Stubs must become real integrations.
- **Multi-model routing is independent**: Can be enhanced in parallel with memory work. Exists today in basic form.
- **Voice input is independent**: Wake word + STT pipeline can be built in parallel. Does not depend on memory system.
- **Persona enhancement is independent**: Can be deepened at any time. Existing persona module is a good foundation.
- **Mobile sync depends on memory**: The sync protocol needs to know what the memory format is before it can sync it. Build memory first, sync second.

---

## MVP Definition

### Launch With (v1) -- "Jarvis Remembers"

The minimum viable upgrade that transforms Jarvis from a command runner into an assistant that actually knows you.

- [x] **SQLite + FTS5 memory database** -- Replace JSONL with queryable storage. This is the foundation for everything.
- [x] **Local embeddings for semantic search** -- sentence-transformers for vector similarity. Makes memory actually useful.
- [x] **One real connector (Calendar)** -- Transform daily briefing from stub to real. Most impactful single integration.
- [x] **Enhanced daily briefing** -- Combine calendar + memory context into genuinely useful morning brief.
- [x] **Enhanced multi-model routing** -- Route to Opus for reasoning, Sonnet for routine, local for embeddings.
- [x] **Anti-regression verification** -- Extend signed snapshots to verify no knowledge loss between sessions.

### Add After Validation (v1.x) -- "Jarvis Learns"

Features to add once the memory foundation is proven stable.

- [ ] **Three-tier memory hierarchy** -- Add when flat SQLite proves too slow or noisy at scale
- [ ] **Knowledge graph layer** -- Add after memory has enough facts to interconnect (100+ facts)
- [ ] **Email connector (IMAP)** -- Add after calendar connector proves the integration pattern
- [ ] **Continuous learning engine** -- Add after memory + embeddings are stable and proven
- [ ] **Proactive assistance system** -- Add after calendar + tasks + memory are all flowing
- [ ] **Wake word + voice input** -- Add after core memory system is solid (voice is an input modality, not core intelligence)
- [ ] **Enhanced personality layer** -- Add after the assistant has enough context to be genuinely contextual with humor

### Future Consideration (v2+) -- "Jarvis Evolves"

Features to defer until the core intelligence is mature.

- [ ] **Owner behavioral model** -- Needs months of interaction data to build meaningful patterns
- [ ] **Cross-domain reasoning** -- Needs knowledge graph + multiple domain connectors populated
- [ ] **Emotional context awareness** -- Needs behavioral model as prerequisite
- [ ] **Temporal knowledge with decay** -- Nice refinement once knowledge graph exists
- [ ] **Adversarial self-testing** -- Needs established baseline of knowledge to test against
- [ ] **Bidirectional mobile sync** -- Needs finalized memory format before building sync protocol
- [ ] **Self-improving capability verification** -- Needs months of growth_tracker data to analyze

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority | Phase |
|---------|------------|---------------------|----------|-------|
| SQLite + FTS5 memory database | HIGH | MEDIUM | P1 | v1 |
| Local embeddings (semantic search) | HIGH | MEDIUM | P1 | v1 |
| Calendar connector (real) | HIGH | MEDIUM | P1 | v1 |
| Enhanced daily briefing | HIGH | LOW | P1 | v1 |
| Multi-model routing (enhanced) | HIGH | LOW | P1 | v1 |
| Anti-regression locks (enhanced) | HIGH | LOW | P1 | v1 |
| Three-tier memory hierarchy | HIGH | HIGH | P2 | v1.x |
| Knowledge graph | HIGH | HIGH | P2 | v1.x |
| Email connector (IMAP) | MEDIUM | MEDIUM | P2 | v1.x |
| Continuous learning engine | HIGH | HIGH | P2 | v1.x |
| Proactive assistance | HIGH | HIGH | P2 | v1.x |
| Wake word + voice input | MEDIUM | MEDIUM | P2 | v1.x |
| Persona enhancement | MEDIUM | LOW | P2 | v1.x |
| Owner behavioral model | HIGH | VERY HIGH | P3 | v2+ |
| Cross-domain reasoning | HIGH | HIGH | P3 | v2+ |
| Emotional context awareness | MEDIUM | HIGH | P3 | v2+ |
| Temporal knowledge | MEDIUM | MEDIUM | P3 | v2+ |
| Adversarial self-testing | MEDIUM | MEDIUM | P3 | v2+ |
| Bidirectional sync (full) | MEDIUM | HIGH | P3 | v2+ |
| Self-improving verification | MEDIUM | MEDIUM | P3 | v2+ |

**Priority key:**
- P1: Must have. Build first. The memory foundation without which nothing else works.
- P2: Should have. Build when foundation is stable. These create the differentiators.
- P3: Future vision. Build when the assistant has matured with months of real usage data.

---

## Competitor Feature Analysis

| Feature | Google Gemini | ChatGPT | Alexa+ | OpenClaw | isair/jarvis | **Our Jarvis** |
|---------|--------------|---------|--------|----------|-------------|----------------|
| Natural conversation | Excellent (Gemini Live) | Excellent | Good (voice-first) | Good (text chat) | Voice-only | Good CLI, needs conversation layer |
| Memory persistence | Cross-session (cloud) | Cross-session (cloud) | Limited | Local files | Local (SQLite) | Needs upgrade (JSONL to SQLite) |
| Semantic search | YES (cloud) | YES (cloud) | NO | Partial | Partial | NO (needs embeddings) |
| Knowledge graph | NO | NO | NO | NO | NO | **Planned -- differentiator** |
| Anti-regression | NO | NO | NO | NO | NO | **Planned -- unique** |
| Proactive briefing | Personal Intelligence | ChatGPT Pulse | Routines | Heartbeat/cron | NO | Partial (needs real data) |
| Calendar integration | Native Google | Partial | Amazon Calendar | Via MCP | NO | Stub (needs implementation) |
| Email triage | Native Gmail | Partial | NO | Via chat apps | NO | Stub (needs implementation) |
| Local-first privacy | NO (cloud) | NO (cloud) | NO (cloud) | YES (local) | YES (local) | **YES -- core principle** |
| Multi-model routing | Single model | Single model | Single model | YES | Single model | Partial (needs enhancement) |
| Self-improvement tracking | NO | NO | NO | NO | NO | **YES -- growth_tracker exists** |
| Owner behavioral model | Basic preferences | Basic memory | Routines | Basic | Basic | **Planned -- revolutionary** |
| Personality | Generic | Generic | Generic | Customizable | Basic | **British butler with humor** |
| Voice output | Excellent | Excellent | Excellent | NO | YES | **YES (Edge-TTS)** |
| Phone/spam guard | NO | NO | NO | NO | NO | **YES -- unique** |
| Learning missions | NO | NO | NO | NO | NO | **YES -- unique** |
| Code generation | Gemini Code Assist | GPT-4/o | NO | YES (Claude Code) | NO | YES (Ollama local) |

---

## What Would Make This "The Greatest Assistant Ever Made"

Based on all research, here is the hierarchy that matters:

1. **Memory that never forgets and always finds** -- The #1 gap in every assistant today. Even ChatGPT's memory is shallow. Build deep, semantic, persistent memory with anti-regression guarantees. This alone surpasses every commercial assistant.

2. **Proactive intelligence, not reactive answering** -- The industry is moving from "ask me anything" to "I'll tell you before you ask." Jarvis should know the owner's patterns well enough to surface the right information at the right time without being asked.

3. **Verifiable self-improvement** -- No assistant today can prove it's getting smarter. Growth tracking with golden tasks, capability scores, and regression detection creates an assistant that demonstrably improves every week.

4. **Cross-domain life awareness** -- Corporate assistants live in silos (Google for email, Alexa for home, separate apps for health). Jarvis sees the whole picture: health + schedule + finances + family + gaming + school. The cross-domain reasoning this enables is impossible for any single commercial product.

5. **Genuine personality** -- Not "I'm an AI assistant" but "Good morning, sir. I see you were up rather late with Fortnite. Your 9 AM meeting with Dr. Smith might benefit from an extra coffee. Shall I add your current medication list to your briefing notes?" This is what makes it JARVIS, not just another chatbot.

---

## Sources

- [Stevens AI assistant (SQLite + cron architecture)](https://www.geoffreylitt.com/2025/04/12/how-i-made-a-useful-ai-assistant-with-one-sqlite-table-and-a-handful-of-cron-jobs) -- MEDIUM confidence (single developer blog, but well-documented and viral in AI community)
- [MemoryOS (EMNLP 2025 Oral)](https://github.com/BAI-LAB/MemoryOS) -- HIGH confidence (peer-reviewed academic paper, published at top NLP venue)
- [Mem0 universal memory layer](https://github.com/mem0ai/mem0) -- HIGH confidence (open source, arxiv paper, production deployments)
- [Mem0 research paper](https://arxiv.org/abs/2504.19413) -- HIGH confidence (peer-reviewed)
- [OpenClaw personal AI assistant](https://github.com/openclaw/openclaw) -- MEDIUM confidence (active open-source project, multiple independent reviews)
- [isair/jarvis local-first assistant](https://github.com/isair/jarvis) -- MEDIUM confidence (active GitHub project)
- [Google Gemini Personal Intelligence](https://blog.google/innovation-and-ai/products/gemini-app/personal-intelligence/) -- HIGH confidence (official Google blog)
- [ChatGPT Pulse](https://winbuzzer.com/2025/09/26/openais-chatgpt-pulse-aims-to-own-your-morning-routine-with-proactive-ai-briefs-xcxwbn/) -- MEDIUM confidence (tech news, verified by multiple sources)
- [OpenAI Operator / CUA](https://openai.com/index/introducing-operator/) -- HIGH confidence (official OpenAI)
- [Knowledge Graph of Thoughts](https://arxiv.org/abs/2504.02670) -- HIGH confidence (arxiv paper)
- [AI agent proactive trends 2026](https://www.salesmate.io/blog/future-of-ai-agents/) -- LOW confidence (industry blog)
- [Multi-model routing cost optimization](https://www.swfte.com/blog/intelligent-llm-routing-multi-model-ai) -- MEDIUM confidence (multiple sources agree on 30-70% cost reduction)
- [Emotional AI market and Hume AI](https://www.hume.ai/) -- MEDIUM confidence (official product page + market research)
- [ElevenLabs voice agent trends](https://elevenlabs.io/blog/voice-agents-and-conversational-ai-new-developer-trends-2025) -- MEDIUM confidence (industry leader blog)
- [MemOS v2.0 Stardust](https://github.com/MemTensor/MemOS) -- MEDIUM confidence (open source, active development)
- [MIT Technology Review on secure AI assistants](https://www.technologyreview.com/2026/02/11/1132768/is-a-secure-ai-assistant-possible/) -- HIGH confidence (authoritative publication)

---
*Feature research for: Local-first AI Personal Assistant (JARVIS-class)*
*Researched: 2026-02-22*
