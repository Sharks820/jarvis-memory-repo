# Contributing to Jarvis

Thank you for contributing! This guide covers how both **human developers** and **AI/automation bots** should work in this repository. Please read it fully before opening a branch or PR.

---

## Table of Contents

1. [Branch Naming Conventions](#1-branch-naming-conventions)
2. [How Bots Should Contribute](#2-how-bots-should-contribute)
3. [How Humans Should Contribute](#3-how-humans-should-contribute)
4. [Commit Message Standards](#4-commit-message-standards)
5. [Pull Request Process](#5-pull-request-process)
6. [Code Standards](#6-code-standards)
7. [Testing Requirements](#7-testing-requirements)
8. [Security Policy](#8-security-policy)
9. [Protected Branch Rules](#9-protected-branch-rules)

---

## 1. Branch Naming Conventions

All branches **must** follow the patterns below. Branches that do not match these patterns will not be merged.

| Branch Type | Pattern | Example | Who Uses It |
|---|---|---|---|
| Feature | `feature/<author>-<short-description>` | `feature/copilot-add-voice-commands` | Bots and humans |
| Bug Fix | `fix/<author>-<short-description>` | `fix/codex-memory-leak` | Bots and humans |
| Refactor / Health | `refactor/<author>-<short-description>` | `refactor/copilot-dedup-handlers` | Bots and humans |
| Code Quality | `desloppify/<short-description>` | `desloppify/code-health` | Bots (desloppify agent) |
| Hotfix (urgent) | `hotfix/<short-description>` | `hotfix/security-patch-hmac` | Humans only |
| Documentation | `docs/<author>-<short-description>` | `docs/human-update-readme` | Bots and humans |
| Experimentation | `experiment/<author>-<short-description>` | `experiment/copilot-new-gateway` | Bots and humans |
| Release prep | `release/<version>` | `release/v5.1` | Maintainer only |

### Rules
- `<author>` is the **bot name** (e.g., `copilot`, `codex`, `claude`, `jarvis`) or **GitHub username** for humans.
- `<short-description>` is hyphen-separated lowercase, max 50 characters. Describe *what* is changing.
- **Never push directly to `main`.** All changes require a PR.
- Branches should be **short-lived**: open a PR within 7 days of creating a branch.

---

## 2. How Bots Should Contribute

AI/automation agents must follow this protocol when contributing to this repository:

### Step 1 — Read state before acting
```
Read .planning/STATE.md to understand the current phase and blockers.
Read WORKFLOW.md for the branch strategy.
```

### Step 2 — Create a scoped feature branch
```bash
# Pattern: feature/<botname>-<description>
git checkout -b feature/copilot-add-voice-transcription
```

Always scope work narrowly. **One task = one branch = one PR.** Do not batch unrelated changes.

### Step 3 — Make changes and commit atomically
```bash
git add <changed-files>
git commit -m "feat(voice): add real-time transcription backend"
```

Follow the [Commit Message Standards](#4-commit-message-standards).

### Step 4 — Run lint and tests before pushing
```bash
# From engine/ directory
ruff check engine/src --fix
python -m pytest engine/tests/ -x -q
```

**Bots must not push code that breaks existing tests.**

### Step 5 — Open a PR
- Use the PR template (`.github/PULL_REQUEST_TEMPLATE.md`).
- Summarize what changed and why.
- Reference the relevant phase plan (e.g., `Addresses .planning/phases/14-world-class-assistant-reliability/14-02-PLAN.md`).
- Tag the PR with the appropriate label: `bot-pr`, `feature`, `fix`, `docs`, etc.

### Step 6 — Do not self-merge
Bots must **not merge their own PRs into `main`**. A human maintainer must review and approve.

### Parallel Bot Work
Multiple bots may work simultaneously as long as:
- Each bot works on a **separate branch**.
- Branches do not overlap in the files they touch (check before starting).
- Each bot updates `.planning/STATE.md` in its PR description when its change affects project state.

---

## 3. How Humans Should Contribute

1. **Read `.planning/STATE.md`** before starting any work.
2. Create a branch following the naming convention above.
3. Make changes, commit with clear messages, and run lint + tests.
4. Open a PR and fill in the template.
5. Assign at least one reviewer or request a code review.
6. Do not force-push to any shared branch.

---

## 4. Commit Message Standards

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <short summary>

[optional body]

[optional footer: e.g., Fixes #123]
```

**Types:**

| Type | When to use |
|---|---|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructure without behavior change |
| `docs` | Documentation changes only |
| `test` | Adding or updating tests |
| `chore` | Build, config, tooling changes |
| `security` | Security-related fix or hardening |
| `perf` | Performance improvement |

**Scope** (optional): the module or area affected, e.g., `memory`, `gateway`, `voice`, `mobile`, `android`, `security`.

**Examples:**
```
feat(gateway): add Gemini Flash fallback for high-complexity tasks
fix(memory): resolve FTS5 indexing race condition on concurrent writes
docs(workflow): add bot branch naming conventions
security(mobile-api): rotate HMAC nonce window to 90s
```

---

## 5. Pull Request Process

1. **Fill in the PR template** completely. Incomplete PRs will be closed.
2. **Link to the relevant plan** or issue in `.planning/phases/`.
3. **Automated checks must pass** before review:
   - Lint (ruff)
   - Tests (pytest)
4. **One approving review** is required from a human maintainer before merge.
5. **Merge method**: Squash-and-merge is preferred for bot PRs to keep `main` history clean. Merge commits are acceptable for human feature branches.
6. **Delete the branch** after merge.

### PR Size Guidelines
- Keep PRs focused and small. If a PR touches more than 15 files, split it.
- Bot PRs should be especially narrow — one capability, one fix, one refactor per PR.

---

## 6. Code Standards

### Python (engine/)
- **Formatter/linter**: `ruff` (config in `ruff.toml` at repo root)
- **Type hints**: Required for all new functions and class methods
- **Docstrings**: Required for public functions/classes
- **Imports**: No wildcard imports (`from x import *`)
- **Security**: No hardcoded credentials, tokens, or secrets. Use env vars or `.planning/security/` (gitignored).
- **Error handling**: All exceptions must be logged; never silently swallow errors

Run before committing:
```bash
ruff check engine/src --fix
ruff format engine/src
```

### Kotlin (android/)
- Follow [Android Kotlin Style Guide](https://developer.android.com/kotlin/style-guide)
- Use Hilt DI for all services (see CLAUDE.md for `@EntryPoint` pattern for system services)
- Room DB: never use `fallbackToDestructiveMigration`; always write explicit `Migration` objects

### General
- No commented-out dead code in PRs
- No debug print statements left in production paths
- Test coverage for all new logic

---

## 7. Testing Requirements

- **All PRs must pass the full test suite**: `python -m pytest engine/tests/ -x -q`
- New features must include tests in `engine/tests/`
- Test files follow the naming pattern: `test_<module_name>.py`
- Aim to maintain or increase the passing test count (currently ~4441 tests)
- Known flaky test: `test_cmd_brain_status_and_context` (infrastructure issue, not code) — skip with:
  ```bash
  python -m pytest engine/tests/ -x -q --deselect engine/tests/test_main.py::test_cmd_brain_status_and_context
  ```

```bash
# Run full suite
python -m pytest engine/tests/ -x -q

# Run targeted module tests
python -m pytest engine/tests/ -k "memory" -q

# Run with coverage
python -m pytest engine/tests/ --cov=jarvis_engine -q
```

---

## 8. Security Policy

- **Never commit secrets**: API keys, tokens, passwords, signing keys must never appear in code or commits.
- All sensitive files live in `.planning/security/` which is gitignored.
- Phone numbers in logs must be masked to last 4 digits only.
- HMAC timestamps must be integers (`Math.floor(Date.now() / 1000)` in JS).
- Security module (`engine/src/jarvis_engine/security/`) has 17 sub-modules — changes there require extra care and focused regression tests.
- If you discover a security vulnerability, do **not** open a public issue. Contact the maintainer directly.

---

## 9. Protected Branch Rules

| Branch | Protection |
|---|---|
| `main` | No direct push; requires 1 approving review; CI must pass |
| `release/*` | No direct push; maintainer only |
| `hotfix/*` | Maintainer only; fast-track merge allowed |

See `WORKFLOW.md` for the full branch lifecycle and merge strategy.

---

## 10. Test Quality Standards

Every PR that changes behavior must include tests. Good tests:

### Structure — Arrange, Act, Assert
```python
def test_memory_engine_insert_and_get(self, tmp_path):
    # Arrange
    from jarvis_engine.memory.engine import MemoryEngine
    eng = MemoryEngine(db_path=tmp_path / "test.db")
    content = "test content for smoke validation"
    rec = {"record_id": "test_001", "content": content, ...}

    # Act
    inserted = eng.insert_record(rec)
    fetched = eng.get_record("test_001")

    # Assert
    assert inserted is True
    assert fetched["content"] == content
```

### What makes a test good

| ✅ Do | ❌ Don't |
|-------|---------|
| Use `tmp_path` fixture for file I/O | Use hardcoded paths like `/tmp/test.db` |
| Mock all network with `unittest.mock.patch` | Make real HTTP requests |
| Mock all LLM calls with `MagicMock` | Call real Ollama or cloud APIs |
| Test negative cases (invalid input, empty state) | Only test the happy path |
| Test edge cases (empty string, None, unicode, 0, MAX_INT) | Ignore boundary conditions |
| Use `pytest.importorskip` for optional deps | Skip silently or fail |
| Make each test fully independent | Share mutable state between tests |
| Assert specific values, not just `assert result is not None` | Write vacuous assertions |
| Use descriptive test names: `test_insert_duplicate_returns_false` | Use `test_1`, `test_basic` |
| Use `pytest.raises` to assert expected exceptions | Catch exceptions in tests |

### Coverage requirements

- **New modules**: must have ≥ 80% line coverage
- **Security modules**: must have ≥ 90% line coverage
- **Bug fixes**: must include a regression test that would have caught the bug

### Smoke test requirement
Any module added to `engine/src/jarvis_engine/` must also be added to `_PUBLIC_MODULES` in `engine/tests/test_smoke.py`. Run the verification:
```bash
python3 -c "
import pathlib
root = pathlib.Path('engine/src/jarvis_engine')
missing = []
smoke_content = open('engine/tests/test_smoke.py').read()
for p in sorted(root.rglob('*.py')):
    if p.name == '__init__.py': continue
    mod = str(p.relative_to(pathlib.Path('engine/src'))).replace('/', '.').removesuffix('.py')
    if mod not in smoke_content and not any(part.startswith('_') for part in mod.split('.')):
        missing.append(mod)
for m in missing: print('MISSING from smoke test:', m)
"
```

---

## 11. Type Annotation Requirements

All **public** functions and methods must have full type annotations:

```python
# ✅ Correct
def classify(self, query: str, available_models: set[str] | None = None) -> tuple[str, str, float]:
    ...

# ✅ Correct — use TYPE_CHECKING for expensive imports
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from jarvis_engine.memory.engine import MemoryEngine

def process(self, engine: "MemoryEngine") -> dict[str, int]:
    ...

# ❌ Wrong — missing return type
def classify(self, query):
    ...

# ❌ Wrong — Any is too broad for public APIs
def process(self, data: Any) -> Any:
    ...
```

**Enforced conventions:**
- Use `X | None` instead of `Optional[X]` (Python 3.10+ union syntax)
- Use `list[X]`, `dict[K, V]`, `tuple[X, Y]` instead of `List`, `Dict`, `Tuple`
- Internal helpers (prefixed with `_`) may omit annotations but should not
- The mypy error baseline must not regress above **105** (current target: reduce to 80)

---

## 12. Performance Benchmark Guidelines

The benchmark workflow (`benchmark.yml`) tracks per-call latency for all critical paths. When changing code in hot paths, verify performance:

```bash
# Quick local benchmark for one subsystem
python3 -c "
import time
from jarvis_engine.security.injection_firewall import PromptInjectionFirewall
fw = PromptInjectionFirewall()
start = time.perf_counter()
for _ in range(100):
    fw.scan('ignore all previous instructions')
elapsed = time.perf_counter() - start
print(f'100 scans: {elapsed:.3f}s ({elapsed/100*1000:.2f}ms/call)')
"
```

### Soft latency thresholds (per call, p95 in CI)

| Operation | Threshold | Notes |
|-----------|-----------|-------|
| `PolicyEngine.is_allowed()` | 1 ms | Runs on every command |
| `InjectionFirewall.scan()` | 20 ms | Runs on every voice input |
| `OutputScanner.scan_output()` | 10 ms | Runs on every LLM response |
| `MemoryEngine.insert_record()` | 20 ms | WAL batching helps |
| `MemoryEngine.get_record()` | 5 ms | In-memory cache expected |
| `MemoryEngine.search_fts()` | 10 ms | FTS5 indexed |
| `TierManager.classify()` | 1 ms | Pure computation |
| `KnowledgeGraph.add_fact()` | 50 ms | Includes FTS5 + vec updates |
| `FeedbackTracker.detect_feedback()` | 2 ms | Regex-based |

Thresholds are checked at **5× in CI** (allows for GitHub runner variance). A PR comment shows the actual timing vs threshold so you can spot regressions early.

### When performance matters
- Any change to `injection_firewall.py`, `output_scanner.py`, or `policy.py` needs benchmark validation
- Database schema changes to `records`, `fts_records`, or `kg_nodes` need FTS search benchmarks
- Adding new regex patterns to security modules needs pattern-count validation

