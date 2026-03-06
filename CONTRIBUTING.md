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
