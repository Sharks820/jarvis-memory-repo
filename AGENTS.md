# Jarvis Repo Agent Guide

This repository is the execution base for the local-first Jarvis engine.

## Source Of Truth
- `CLAUDE.md`
- `.planning/PROJECT.md`
- `.planning/REQUIREMENTS.md`
- `.planning/ROADMAP.md`
- `.planning/STATE.md`

## Collaboration Docs
- `CONTRIBUTING.md` — branch naming conventions, commit standards, PR process, code standards
- `WORKFLOW.md` — branch strategy, lifecycle, merge rules, parallel-bot coordination

## Working Rules
1. Read `.planning/STATE.md` before making changes.
2. Read `CONTRIBUTING.md` before opening any branch or PR.
3. Keep code in `engine/` and planning artifacts in `.planning/`.
4. Update `.planning/STATE.md` after meaningful changes.
5. Prefer small, verifiable commits by phase.
6. Do not relax security controls for convenience.
7. Branch naming: `feature/<botname>-<description>` — see `CONTRIBUTING.md` for full table.
8. Never push directly to `main`.

## Current Direction
- Primary runtime: desktop PC
- Secondary node: weaker laptop (future, non-primary)
- Architecture: local-first with optional cloud burst
- Security: default-deny + explicit allowlists

