# CLAUDE.md

## Intelligent Reasoning Strategy

**Use the right level of thinking for the task:**

| Task Type | Thinking Level | When to Use |
|-----------|---------------|-------------|
| Architecture decisions | ultrathink | New systems, major refactors, security design |
| Complex debugging | think harder | Root cause analysis, multi-module issues |
| Feature implementation | think hard | New features following existing patterns |
| Bug fixes, simple changes | (default) | Clear scope, known solution pattern |
| Exploration, search | (minimal) | Finding files, understanding code |

**Opus 4.6 Adaptive Thinking**: Automatically adjusts thinking depth. Keywords override:
- ultrathink = max effort, full extended thinking
- think harder = high effort 
- think hard = moderate effort
- (no keyword) = adaptive based on complexity

**Token Efficiency Rules:**
- Read existing code BEFORE proposing changes (avoids rewrites)
- Use Explore agent for codebase searches (saves main context)
- Check episodic memory for prior decisions (avoids re-solving)
- Run tests incrementally, not full suite for small changes

## Code Quality Requirements

- Run python -m pytest engine/tests/ -x -q before ANY commit
- Use pyright type checking: no untyped definitions in new code
- Follow existing patterns: CQRS handlers, lazy imports, dataclass commands
- Security-first: validate inputs, mask PII, use existing security module patterns

## Workflow Enforcement (via Superpowers 5.0)

- /superpowers:brainstorming before NEW features (skip for bug fixes)
- /superpowers:systematic-debugging for complex bugs only
- /superpowers:verification-before-completion before claiming done

## Project Overview

Jarvis is a local-first personal AI assistant for Conner:
- Desktop Engine (Python, engine/): Memory, intelligence routing, knowledge graph, proactive engine
- Android App (Kotlin, android/): Voice, calls, notifications, location, camera

Both milestones complete: v1.0 Desktop Engine (phases 1-9), v2.0 Android App (phases 10-13).

## Quick Start

  .venv/Scripts/activate
  python -m pytest engine/tests/ -x -q    # ~4400 tests
  jarvis-engine daemon-run                 # Primary mode

## Architecture

- CQRS command bus (engine/src/jarvis_engine/app.py): 70+ commands
- Memory: SQLite + FTS5 + sqlite-vec
- Knowledge graph: NetworkX + SQLite with fact locks
- Intelligence gateway: Ollama (local) + Anthropic + Groq/Kimi + Gemini
- Mobile API: HMAC-SHA256 signed HTTP on port 8787
- Android: Jetpack Compose + Room/SQLCipher + Hilt DI + Retrofit2

## Key Patterns

- All 35 engine modules are actively used (verified Feb 2026)
- Handler pattern: Lazy imports of domain modules
- Hilt DI in services: @EntryPoint + EntryPointAccessors (NOT @AndroidEntryPoint)
- Room DB version 11: 16 entities, 10 explicit Migration objects
- HMAC timestamps: integers only (Math.floor, not floats)
- Phone numbers masked in logs (last 4 digits only)

## Critical Decisions

- Phone = sensor/interface, desktop = brain (all LLM on desktop)
- Offline-first: Room command queue, auto-flush on reconnect
- Privacy keywords force local Ollama routing
- Accelerometer driving detection (no Google Play Services)

## Testing

  python -m pytest engine/tests/ -x -q     # All tests
  python -m pytest engine/tests/test_main.py -x -q   # CLI only
  python -m pytest engine/tests/ -k "memory" -q     # Subset

7 tests skipped (live Ollama + optional deps).

## Security

- .planning/security/ and .planning/brain/ are gitignored
- HMAC-SHA256 with nonce replay protection (120s window)
- Fernet + PBKDF2HMAC for sync payloads
- Security module: 17 modules, 482 tests, 3-layer prompt injection firewall

## Planning and GSD

- Source of truth: .planning/STATE.md, ROADMAP.md, REQUIREMENTS.md
- Read STATE.md before changes, update after
- Prefer small, verifiable commits by phase
