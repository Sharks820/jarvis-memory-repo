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
| `WORKFLOW.md` | Branch lifecycle, merge rules, parallel-bot coordination |
| `CHANGELOG.md` | Full version history |

---

## Collaboration Docs

- `CONTRIBUTING.md` — branch naming conventions, bot/human workflow, commit standards, PR process, code standards, test quality requirements, performance benchmark guidelines
- `WORKFLOW.md` — branch lifecycle diagram, parallel-bot isolation rules, merge strategy, release/hotfix process

---

## Working Rules

1. **Read `.planning/STATE.md` before making any changes.** Do not assume continuity from a prior session.
2. **Read `CONTRIBUTING.md` before opening any branch or PR.**
3. **Keep code in `engine/` and planning artifacts in `.planning/`.**
4. **Update `.planning/STATE.md` after meaningful changes.**
5. **Prefer small, verifiable commits by phase.** One logical change per commit.
6. **Do not relax security controls for convenience.** Security module changes require focused regression tests.
7. **Branch naming:** `feature/<botname>-<description>` — see `CONTRIBUTING.md` for full table.
8. **Never push directly to `main`.**

---

## Current Direction

- **Primary runtime:** desktop PC
- **Secondary node:** weaker laptop (future, non-primary)
- **Architecture:** local-first with optional cloud burst
- **Security:** default-deny + explicit allowlists
- **Privacy:** all queries with privacy keywords route to local Ollama; no sensitive data leaves the device

---

## Quality Gates — Every Commit Must Pass

Before pushing any commit, verify:

```bash
# Lint (fast — run this after every file change)
ruff check engine/src
ruff format --check engine/src

# Tests (must all pass)
python -m pytest engine/tests/ -x -q

# Security check on your changed files
bandit engine/src/jarvis_engine/<your_file>.py -ll
```

**Smoke test validation (after any structural change):**
```bash
python -m pytest engine/tests/test_smoke.py -v
```

**Type check (informational; track count, never regress above baseline 105):**
```bash
cd engine && mypy src/jarvis_engine --ignore-missing-imports --no-error-summary 2>&1 | grep -c ": error:"
```

---

## Module Ownership & Dependency Rules

### Security modules (`engine/src/jarvis_engine/security/`)
- **Never** remove or weaken security checks without explicit owner approval
- **Always** add regression tests for any security module change
- New threat patterns must be added to the appropriate pattern list (injection_firewall, output_scanner, threat_detector)
- HMAC verification in `mobile_api.py` must remain integer timestamps

### Memory modules (`engine/src/jarvis_engine/memory/`)
- **Never** use `fallbackToDestructiveMigration` — always write explicit migrations
- Room DB currently at version 11 with 10 explicit migrations (1→2 through 10→11)
- Write-lock serialization via `_write_lock`; read-lock via `_db_lock` — always respect both
- WAL mode must remain enabled (PRAGMA journal_mode=WAL)

### Knowledge graph (`engine/src/jarvis_engine/knowledge/`)
- Fact locks are permanent — once locked, a fact cannot be overwritten without explicit unlock
- Contradictions go to quarantine, never silently overwrite
- All KG writes must go through `MemoryEngine._write_lock`

### Gateway (`engine/src/jarvis_engine/gateway/`)
- Privacy keyword list in `_constants.py` is the single source of truth
- Routing changes must not create paths that send privacy-sensitive queries to cloud
- Cost tracking must be maintained for all provider calls

### Learning (`engine/src/jarvis_engine/learning/`)
- `ConversationLearningEngine` is the single entry point for interaction learning
- Feedback signals must remain in the 3-category schema: `positive`, `negative`, `neutral`
- Preference changes must be backward-compatible with existing `user_preferences` table schema

### Voice/STT (`engine/src/jarvis_engine/stt.py`, `voice_pipeline.py`)
- STT fallback chain order must be maintained: Parakeet → Deepgram → Groq → faster-whisper
- Phone numbers must be masked in all logs (show only last 4 digits)
- `shorten_urls_for_speech` must strip `www.` and not emit raw `https://` URLs

---

## Prohibited Patterns

These patterns are **never acceptable** regardless of context:

```python
# ❌ NEVER — broad exception swallowing
except Exception:
    pass

# ❌ NEVER — hardcoded credentials
API_KEY = "sk-abc123"

# ❌ NEVER — raw SQL string construction
query = f"SELECT * FROM records WHERE id = '{user_input}'"

# ❌ NEVER — disabling security gates
if debug_mode:
    skip_injection_firewall = True

# ❌ NEVER — logging sensitive data
logger.info(f"Password: {user_password}")
logger.info(f"Token: {api_key}")

# ❌ NEVER — global mutable state without locking
_shared_cache = {}  # race condition

# ❌ NEVER — sleep in tests
time.sleep(2)  # use monkeypatch or mocks instead

# ❌ NEVER — network calls in tests
requests.get("https://api.example.com")  # use unittest.mock.patch
```

---

## Required Patterns

```python
# ✅ Always — narrow exceptions
except (ValueError, KeyError) as exc:
    logger.warning("Expected error: %s", exc)

# ✅ Always — parameterized queries
cursor.execute("SELECT * FROM records WHERE id = ?", (record_id,))

# ✅ Always — mask PII in logs
logger.info("Call from ...%s", phone_number[-4:])

# ✅ Always — type annotations on all public functions
def classify(self, query: str) -> tuple[str, str, float]:

# ✅ Always — docstring on every public class and function
def ingest(self, content: str, kind: str) -> dict:
    """Ingest a memory record through the enriched pipeline.
    
    Args:
        content: The text content to ingest.
        kind: Memory kind — 'episodic', 'semantic', or 'procedural'.
    
    Returns:
        Dict with 'record_id', 'duplicate', and 'tier' keys.
    """
```

---

## Test Standards

Every new feature must include tests that follow this pattern:

```python
# Arrange
store = MemoryStore(tmp_path)

# Act
store.append("event_type", "test content")
events = list(store.tail(limit=5))

# Assert
assert len(events) >= 1
assert any("test content" in e.message for e in events)
```

**Test quality checklist:**
- [ ] No `time.sleep()` — use mocks or fixed fixtures instead
- [ ] No network calls — mock all HTTP/socket I/O with `unittest.mock.patch`
- [ ] No LLM calls — mock gateway responses with `MagicMock`
- [ ] Uses `tmp_path` fixture for all file I/O
- [ ] Each test is independent — no shared mutable state between tests
- [ ] Negative cases tested (invalid input, empty state, error conditions)
- [ ] Edge cases tested (empty strings, zero, None, unicode)

---

## Per-Agent Protocols

### GitHub Copilot (copilot-swe-agent)
- Focus: collaboration infrastructure, documentation, test coverage, CI workflows
- Branch prefix: `copilot/`
- Must update `STATE.md` after every session
- Must run full test suite before marking work complete

### OpenAI Codex
- Focus: refactoring, code health (desloppify), performance optimization
- Branch prefix: `desloppify/` or `refactor/codex-`
- Must verify test count does not decrease after each batch
- No new features — refactor only

### Claude (Anthropic)
- Focus: complex reasoning, bug fixes, security review, architecture
- Branch prefix: `feature/claude-`
- All security-related changes require paired regression tests
- Must document reasoning in PR description

### Desloppify Bot
- Focus: automated code health improvements
- Branch prefix: `desloppify/`
- Max 25 files per PR (complexity limit)
- Must include before/after error count in PR description

---

## CI Status Interpretation

| Gate | Failure Meaning | Action Required |
|------|----------------|-----------------|
| Lint (ruff) | Style/formatting issue | Run `ruff check --fix` and `ruff format` |
| Type Check (mypy) | Type regression above baseline 105 | Fix or suppress with `# type: ignore[specific-code]` |
| Security Scan (bandit) | HIGH severity finding | Must fix before merge — no exceptions |
| Dependency Audit (pip-audit) | Known CVE in dependency | Update dependency version |
| Tests + Coverage | Test failure or coverage below 50% | Fix failing test or add coverage |
| Smoke Tests | Module import or behavioral failure | Critical — fix immediately |
| Benchmarks | 5x performance regression | Profile and optimize before merge |


