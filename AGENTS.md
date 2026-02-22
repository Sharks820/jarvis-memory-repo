# Jarvis Repo Agent Guide

This repository is the execution base for the local-first Jarvis engine.

## Source Of Truth
- `JARVIS_MASTERPLAN.md`
- `.planning/PROJECT.md`
- `.planning/REQUIREMENTS.md`
- `.planning/ROADMAP.md`
- `.planning/STATE.md`

## Working Rules
1. Read `.planning/STATE.md` before making changes.
2. Keep code in `engine/` and planning artifacts in `.planning/`.
3. Update `.planning/STATE.md` after meaningful changes.
4. Prefer small, verifiable commits by phase.
5. Do not relax security controls for convenience.

## Current Direction
- Primary runtime: desktop PC
- Secondary node: weaker laptop (future, non-primary)
- Architecture: local-first with optional cloud burst
- Security: default-deny + explicit allowlists

