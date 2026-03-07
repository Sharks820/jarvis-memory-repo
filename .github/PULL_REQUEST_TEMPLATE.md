## Summary
<!-- 2-3 sentence description of what this PR does and why. -->


## What changed
<!-- Bullet list of the key changes. Be specific enough that a reviewer can verify. -->

- 

## Test coverage
<!-- How was this tested? Which new tests were added or which existing tests cover this change? -->

- [ ] Added / updated unit tests
- [ ] Ran `python -m pytest engine/tests/ -x -q` — all pass
- [ ] Smoke test passes: `python -m pytest engine/tests/test_smoke.py -v`
- [ ] N/A (docs / config change only)

## Quality checklist
- [ ] `ruff check engine/src` passes
- [ ] No new `HIGH` severity bandit findings in changed code
- [ ] No security module changes without dedicated regression test
- [ ] Privacy / local-only routing guarantees preserved (no cloud leak)
- [ ] `.planning/STATE.md` updated if this closes a phase item

## Architecture notes (if applicable)
<!-- Fill in for non-trivial structural changes. Omit for small fixes. -->


## Breaking changes
<!-- List any breaking changes to public APIs, CLI commands, or DB schema. -->
None
