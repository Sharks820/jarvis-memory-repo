## Summary

<!-- One or two sentences describing what this PR does. -->

## Type of Change

<!-- Check all that apply -->

- [ ] `feat` — New feature or capability
- [ ] `fix` — Bug fix
- [ ] `refactor` — Code restructuring without behavior change
- [ ] `docs` — Documentation only
- [ ] `test` — Tests only
- [ ] `chore` — Build / tooling / config
- [ ] `security` — Security fix or hardening
- [ ] `perf` — Performance improvement

## Bot / Automated PR

- [ ] This PR was created by an automated bot or AI agent

If yes, which agent? <!-- e.g., GitHub Copilot, OpenAI Codex, Claude, desloppify -->

## Related Plan / Issue

<!-- Link to the relevant planning doc, issue, or phase plan -->
<!-- e.g., Addresses .planning/phases/14-world-class-assistant-reliability/14-02-PLAN.md -->
<!-- e.g., Closes #42 -->

## What Changed

<!-- Briefly list the files/modules changed and why -->

- 

## Testing

- [ ] Existing tests pass: `python -m pytest engine/tests/ -x -q`
- [ ] New tests added for new logic
- [ ] Lint passes: `ruff check engine/src`
- [ ] New modules added to `_PUBLIC_MODULES` in `test_smoke.py`

## Coverage

<!-- Paste the coverage output line for changed modules, or "N/A — docs only" -->
<!-- e.g.: jarvis_engine/memory/engine.py    87%  (was 84%) -->

- 

## Performance Impact

<!-- Does this PR change any hot-path code? If yes, paste benchmark output. -->
<!-- Hot paths: injection_firewall, output_scanner, policy, memory engine, KG -->

- [ ] No hot-path changes
- [ ] Hot-path changed — benchmark output below:

```
# Paste benchmark output here if applicable
```

## Breaking Changes

- [ ] No breaking changes
- [ ] Breaking change — describe migration path:

## Security Checklist

- [ ] No secrets, tokens, or credentials committed
- [ ] No new hardcoded values that belong in env vars
- [ ] Security module changes have focused regression tests
- [ ] Phone numbers and PII masked in any new log statements
- [ ] No new broad `except Exception: pass` patterns

## Architecture Notes

<!-- Optional: note any design decisions, alternatives considered, or tech debt accepted -->

## Notes for Reviewer

<!-- Anything the reviewer should pay special attention to -->

