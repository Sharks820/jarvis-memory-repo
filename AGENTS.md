# Jarvis Repo Agent Guide

This repository is the execution base for the local-first Jarvis engine.

---

## Source Of Truth (read these first, every session)

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Architecture, quick-start, gotchas |
| `.planning/STATE.md` | Current phase, open blockers, last session notes |
| `.planning/PROJECT.md` | Long-term project vision |
| `.planning/REQUIREMENTS.md` | Functional and non-functional requirements |
| `.planning/ROADMAP.md` | Phase sequence and completion status |
| `CONTRIBUTING.md` | Branch naming, commit standards, PR process, test standards |

---

## Working Rules

1. **Read `.planning/STATE.md` before any changes.** Do not assume continuity from a prior session.
2. **Read `CONTRIBUTING.md` before opening any branch or PR.**
3. **Keep code in `engine/` and planning artifacts in `.planning/`.**
4. **Update `.planning/STATE.md` after meaningful changes.**
5. **Prefer small, verifiable commits by phase.** One logical change per commit.
6. **Do not relax security controls for convenience.** Security module changes require focused regression tests.

---

## Current Direction
- Primary runtime: desktop PC
- Secondary node: weaker laptop (future, non-primary)
- Architecture: local-first with optional cloud burst
- Security: default-deny + explicit allowlists

---

## Quality Gates (all must pass before merge)

```bash
# Lint
ruff check engine/src && ruff format --check engine/src

# Security scan (no HIGH findings allowed)
bandit -r engine/src -ll -x engine/src/jarvis_engine/security/honeypot.py

# Tests with coverage (≥50% required)
cd engine && PYTHONPATH=src python -m pytest tests/ -x -q --cov=jarvis_engine --cov-fail-under=50

# Smoke tests (all 254+ must pass)
cd engine && PYTHONPATH=src python -m pytest tests/test_smoke.py -v
```

---

## Agent-specific Protocols

### Copilot / Codex / Claude agents
- Branch prefix: `copilot/`, `codex/`, `feature/<agent>-`
- Always run `pytest tests/test_smoke.py` before submitting a PR
- Never disable or skip security tests
- Privacy keywords routing: any query containing words from `IntentClassifier.PRIVACY_KEYWORDS` **must** route locally — test this invariant whenever touching `gateway/`

### Desloppify agent
- Branch prefix: `desloppify/`
- Scope: code health, dead code removal, type annotation improvements
- Do not change behavior — tests must pass identically before and after
- Do not touch `security/` without explicit instruction

---

## Module Ownership

| Path | Owner | Notes |
|------|-------|-------|
| `engine/src/jarvis_engine/security/` | @Sharks820 | Changes require security regression tests |
| `engine/src/jarvis_engine/gateway/` | @Sharks820 | Privacy routing invariant must be preserved |
| `engine/src/jarvis_engine/memory/` | @Sharks820 | DB schema changes need migration |
| `engine/src/jarvis_engine/knowledge/` | @Sharks820 | Fact lock / contradiction logic is fragile |
| `android/` | @Sharks820 | Room DB at v11 — never use fallbackToDestructiveMigration |

---

## Anti-Patterns (never do these)

```python
# NEVER weaken the privacy check
if self._check_privacy(query):
    return ("cloud", cloud_model, 1.0)  # ← WRONG

# NEVER store user data in cloud without explicit user opt-in
# NEVER use fallbackToDestructiveMigration in Android Room DB
# NEVER commit .planning/security/ contents (signing keys, tokens)
# NEVER skip the injection firewall for "test" queries
```


