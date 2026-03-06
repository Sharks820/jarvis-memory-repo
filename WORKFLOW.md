# Jarvis Workflow Guide

This document defines the **branch organization strategy**, **lifecycle**, **merge conventions**, and **parallel-work rules** for the Jarvis repository. It is the authoritative reference for how work is organized in this repo.

> **Quick rule**: `main` is always stable. All work happens on feature branches. PRs are the only path to `main`.

---

## Table of Contents

1. [Branch Map](#1-branch-map)
2. [Branch Lifecycle](#2-branch-lifecycle)
3. [Parallel Work by Multiple Bots/Agents](#3-parallel-work-by-multiple-botsagents)
4. [Merge Strategy](#4-merge-strategy)
5. [Release Process](#5-release-process)
6. [Hotfix Process](#6-hotfix-process)
7. [Conflict Resolution](#7-conflict-resolution)
8. [CI and Automated Checks](#8-ci-and-automated-checks)
9. [Bot Identity and Authorship](#9-bot-identity-and-authorship)

---

## 1. Branch Map

```
main                          ← stable, protected, production-ready always
 │
 ├── feature/<bot>-<desc>     ← new capabilities (bots and humans)
 ├── fix/<bot>-<desc>         ← bug fixes
 ├── refactor/<bot>-<desc>    ← code health, restructuring
 ├── desloppify/<desc>        ← automated code-quality improvements
 ├── docs/<author>-<desc>     ← documentation-only changes
 ├── experiment/<bot>-<desc>  ← exploratory, may never merge
 ├── hotfix/<desc>            ← urgent production fixes (maintainer only)
 └── release/<version>        ← release preparation (maintainer only)
```

### Branch Ownership

| Branch prefix | Owner | Notes |
|---|---|---|
| `feature/` | Bot or human | Scoped to one task |
| `fix/` | Bot or human | One bug per branch |
| `refactor/` | Bot or human | No behavior change |
| `desloppify/` | desloppify agent | Automated quality pass |
| `docs/` | Bot or human | Docs-only |
| `experiment/` | Bot or human | May never be merged; label clearly |
| `hotfix/` | Maintainer | Bypasses normal queue in emergencies |
| `release/` | Maintainer | Final prep before tagging |

---

## 2. Branch Lifecycle

```
[Create branch from main]
       │
       ▼
[Develop & commit on branch]
       │
       ▼
[Run lint + tests locally]
       │
       ▼
[Open PR → CI runs]
       │
       ├── CI fails → fix on branch, push again
       │
       └── CI passes → request review
                  │
                  ├── Changes requested → update branch
                  │
                  └── Approved → Squash-merge into main
                             │
                             └── Delete branch
```

### Freshness Rule
- A branch should not live longer than **7 days** without a PR.
- Stale branches (no commits in 14 days with no open PR) may be deleted by the maintainer.

---

## 3. Parallel Work by Multiple Bots/Agents

This repository is designed to support **multiple bots running simultaneously**. The following rules prevent conflicts:

### Assignment Before Starting
Before a bot creates a branch, it should:
1. Check open PRs and branches: `git fetch && git branch -r`
2. Confirm no other branch is already touching the same files.
3. If there is overlap, coordinate with the other bot/agent or wait for the in-flight PR to merge first.

### Non-Overlapping File Ownership (Recommended Split)
Bots should prefer to work in their domain:

| Domain | Primary files |
|---|---|
| Memory / Knowledge | `engine/src/jarvis_engine/memory/`, `engine/src/jarvis_engine/knowledge/` |
| Gateway / LLM routing | `engine/src/jarvis_engine/gateway/`, `engine/src/jarvis_engine/adapters.py` |
| Voice / TTS | `engine/src/jarvis_engine/voice.py`, `engine/src/jarvis_engine/tts*.py` |
| Mobile API | `engine/src/jarvis_engine/mobile_api.py` |
| Security | `engine/src/jarvis_engine/security/` |
| Android app | `android/` |
| Planning / Docs | `.planning/`, `docs/`, `*.md` |

### Isolation Contract
- Each bot branch is isolated: commits on `feature/copilot-X` have zero effect on `feature/codex-Y`.
- Merging order is determined by PR approval time.
- After one bot's PR merges, the next bot must **rebase or merge main** into their branch before their PR is reviewed:
  ```bash
  git fetch origin
  git rebase origin/main   # preferred
  # or: git merge origin/main
  ```

### Signaling
Bots should label their PRs clearly:
- `bot-pr` — indicates this is an automated contribution
- `needs-rebase` — maintainer signals a rebase is required before merge
- `blocked` — signals this PR is waiting on another to merge first

---

## 4. Merge Strategy

| Branch type | Merge method | Rationale |
|---|---|---|
| `feature/*` (bot) | **Squash-merge** | Keep `main` history clean, collapse noisy bot commits |
| `feature/*` (human) | Squash-merge or merge commit | Maintainer's choice |
| `fix/*` | **Squash-merge** | Single atomic fix in history |
| `refactor/*` | Squash-merge | Clean history |
| `desloppify/*` | Squash-merge | Batch quality changes as one commit |
| `docs/*` | Squash-merge | Single docs update |
| `hotfix/*` | **Merge commit** | Preserve emergency context |
| `release/*` | **Merge commit** | Release marker must be visible in history |

### Commit message on squash-merge
When squash-merging, the maintainer should use a clean commit message in Conventional Commits format:
```
feat(gateway): add Gemini Flash fallback (#42)
```

---

## 5. Release Process

1. All planned features for the release are merged into `main`.
2. Maintainer creates `release/<version>` from `main`.
3. Final integration tests run on the release branch.
4. Tag is created: `git tag -a v5.1.0 -m "v5.1.0 — reliability hardening"`
5. Release branch is merged back to `main` with a merge commit.
6. Release branch is deleted after tag.

---

## 6. Hotfix Process

For urgent production issues only:

```bash
# Branch from main
git checkout main
git pull origin main
git checkout -b hotfix/fix-description

# Make the fix
# ...

# Open PR against main with label: hotfix
# Maintainer fast-track approves and merge-commits
# Tag the patch version: v5.0.1
```

Hotfixes skip the normal queue but still require:
- CI to pass
- At least one approving review
- A test (even if minimal) covering the fix

---

## 7. Conflict Resolution

When a PR has merge conflicts with `main`:

1. **Rebase is preferred** over merge commits for feature branches:
   ```bash
   git fetch origin
   git rebase origin/main
   # Resolve conflicts, then:
   git rebase --continue
   git push --force-with-lease origin feature/botname-desc
   ```

2. If rebase produces more than 5 conflict markers, consider a merge commit instead:
   ```bash
   git merge origin/main
   ```

3. For bot branches: the bot should resolve conflicts automatically when possible. If it cannot, it should leave a comment on the PR explaining the conflict and tag the maintainer.

### Avoiding Conflicts Proactively
- Keep branches short-lived (< 7 days).
- Work in narrow file scopes.
- Pull from `main` at the start of each work session.
- Communicate between bots via PR comments if you see overlap.

---

## 8. CI and Automated Checks

Every PR against `main` triggers the CI workflow (`.github/workflows/ci.yml`), which runs:

1. **Lint** — `ruff check engine/src`
2. **Tests** — `python -m pytest engine/tests/ -x -q`

**PRs that fail CI will not be merged.** Fix the branch and push again.

### Local pre-push checks (recommended)
```bash
# Run before every push
ruff check engine/src --fix
python -m pytest engine/tests/ -x -q
```

---

## 9. Bot Identity and Authorship

Bots should configure their git identity explicitly:

```bash
git config user.name "Copilot Bot"
git config user.email "copilot-bot@users.noreply.github.com"
```

All bot commits are traceable. The branch name prefix (e.g., `feature/copilot-`) serves as the primary identity signal. PRs from bots must include the `bot-pr` label.

### Recognized Bot Identities

| Bot / Agent | Branch prefix | Role |
|---|---|---|
| GitHub Copilot | `feature/copilot-`, `fix/copilot-`, `refactor/copilot-` | General coding tasks |
| OpenAI Codex | `feature/codex-`, `fix/codex-` | Code generation |
| Claude / Anthropic | `feature/claude-`, `docs/claude-` | Design, documentation, analysis |
| Desloppify agent | `desloppify/` | Automated code quality |
| Jarvis self-improvement | `feature/jarvis-` | Engine self-modification (requires human review) |

Add new bot identities to this table via a `docs/` PR.
