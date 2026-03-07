# Contributing to Jarvis

This guide covers how both **human developers** and **AI/automation bots** contribute to this repository. Read it before opening a branch or PR.

---

## Branch Naming

| Branch Type | Pattern | Example |
|---|---|---|
| Feature | `feature/<author>-<short-description>` | `feature/copilot-add-voice-commands` |
| Bug Fix | `fix/<author>-<short-description>` | `fix/codex-memory-leak` |
| Refactor | `refactor/<author>-<short-description>` | `refactor/copilot-dedup-handlers` |
| Code quality | `desloppify/<short-description>` | `desloppify/type-annotations` |
| Docs | `docs/<author>-<short-description>` | `docs/human-update-readme` |
| Experiment | `experiment/<author>-<short-description>` | `experiment/copilot-new-gateway` |
| Hotfix | `hotfix/<short-description>` | `hotfix/security-patch-hmac` |

- `<author>` is the bot name (`copilot`, `codex`, `claude`) or GitHub username for humans.
- `<short-description>` is hyphen-separated lowercase, max 5 words.

---

## Before Opening a PR

1. Read `.planning/STATE.md` — confirm your change aligns with the current phase.
2. Run all quality gates locally:

```bash
# From repo root
ruff check engine/src && ruff format --check engine/src
bandit -r engine/src -ll -x engine/src/jarvis_engine/security/honeypot.py
cd engine && PYTHONPATH=src python -m pytest tests/ -x -q --cov=jarvis_engine --cov-fail-under=50
cd engine && PYTHONPATH=src python -m pytest tests/test_smoke.py -v
```

3. Update `.planning/STATE.md` if this closes a phase item.

---

## Commit Standards

- Use the conventional commits format: `<type>: <short description>`
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `ci`, `security`, `perf`
- Keep the subject line under 72 characters
- Reference phase/issue in the body when relevant

Examples:
```
feat: add atomic send lock to Android chat composer
fix: restore draft on chat submit failure
test: add property-based tests for injection firewall
ci: add bandit HIGH-severity gate to CI pipeline
```

---

## Test Standards

### AAA pattern
Every test must follow Arrange → Act → Assert:
```python
def test_memory_insert_returns_true(tmp_path):
    # Arrange
    eng = MemoryEngine(db_path=tmp_path / "mem.db")
    rec = make_record("hello world")
    # Act
    result = eng.insert_record(rec)
    # Assert
    assert result is True
```

### Coverage requirements
- New behavior: add a focused unit test
- New security logic: add both a unit test AND a smoke test entry
- New Android UX: add a helper-function unit test (see `ChatViewModelHelpersTest`)

### Smoke test additions
When adding a new public module or changing a critical API, add an entry to `engine/tests/test_smoke.py`. Import tests live in `TestModuleImports`; behavioral tests get their own numbered section class.

---

## Security Policy

- **No HIGH-severity bandit findings** in new code.
- **Privacy routing invariant**: queries containing any word from `IntentClassifier.PRIVACY_KEYWORDS` must route to `simple_private` (local Ollama). Never bypass this.
- **No secrets in code**: tokens, signing keys, and passwords live in `.planning/security/` which is gitignored.
- **Security module changes** require a dedicated regression test in `engine/tests/test_security_hardening.py` or equivalent.
- **Android Room DB**: currently at version 11 with 16 entities. Every schema change needs an explicit `Migration` object. Never use `fallbackToDestructiveMigration`.

---

## Privacy / Local-First Guarantees

This is a private assistant. Privacy is non-negotiable:

- Health, financial, calendar, and contact data must **never** leave the device.
- The `gateway/classifier.py` privacy check is the enforcement point — do not weaken it.
- Test privacy routing whenever touching `gateway/` or `router.py`.
- HMAC-SHA256 is used on all mobile API calls — timestamps must be integers (`Math.floor(Date.now() / 1000)`).

---

## Onboarding a New Bot

1. Add the bot's GitHub app/account to CODEOWNERS if it owns a path.
2. Use the branch prefix convention above.
3. The bot must be able to run `pytest tests/test_smoke.py` successfully before its first PR.
4. Security and gateway changes require human review regardless of bot confidence level.
