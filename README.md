# Jarvis Local-First Repo

This repo is now set up as a Codex-native workspace for planning and implementation.

## What Is Here
- `JARVIS_MASTERPLAN.md`: high-level architecture and security strategy.
- `.planning/`: active project state, requirements, roadmap, and config.
- `engine/`: runnable bootstrap code for the Jarvis engine.
- `AGENTS.md`: working rules for ongoing agent-driven execution.

## Quick Start (Codex Direct)
```powershell
cd C:\Users\Conner\jarvis-memory-repo
.\scripts\bootstrap.ps1
```

Then:
```powershell
.venv\Scripts\jarvis-engine.exe status
.venv\Scripts\jarvis-engine.exe route --risk high --complexity hard
.venv\Scripts\jarvis-engine.exe ingest --source user --kind semantic --task-id t1 --content "example memory"
.venv\Scripts\jarvis-engine.exe growth-eval --model qwen3:latest --think off
.venv\Scripts\jarvis-engine.exe growth-eval --model qwen3:latest --think off --accept-thinking
.venv\Scripts\jarvis-engine.exe growth-report --last 10
.venv\Scripts\jarvis-engine.exe growth-audit --run-index -1
```

## Mobile Learning (Phone -> Jarvis)
Run secure ingestion API:
```powershell
cd C:\Users\Conner\jarvis-memory-repo\engine
$env:JARVIS_MOBILE_TOKEN = "set-a-long-random-token"
$env:JARVIS_MOBILE_SIGNING_KEY = "set-a-different-long-random-key"
$env:PYTHONPATH = "src"
python -m jarvis_engine.main serve-mobile --host 127.0.0.1 --port 8787
```

See `docs/MOBILE_LEARNING_SETUP.md` for signed request format and safe network setup.
Samsung Galaxy S25 guide: `docs/SAMSUNG_GALAXY_S25_SETUP.md`
Voice security guide: `docs/VOICE_SECURITY_SETUP.md`

## Jarvis Ops Pack (New)
Multimodal task routing:
```powershell
cd C:\Users\Conner\jarvis-memory-repo\engine
$env:PYTHONPATH = "src"
python -m jarvis_engine.main run-task --type code --prompt "Write a robust Python retry decorator." --execute --quality-profile max_quality --output-path ..\generated\retry_decorator.py
python -m jarvis_engine.main run-task --type image --prompt "Concept art of a clean AI workstation" --execute --quality-profile max_quality
python -m jarvis_engine.main run-task --type video --prompt "30-second product teaser" --execute --approve-privileged --quality-profile max_quality
python -m jarvis_engine.main run-task --type model3d --prompt "Low poly desk drone" --execute --approve-privileged
```

Day planning / monitoring:
```powershell
copy .planning\ops_snapshot.example.json .planning\ops_snapshot.json
python -m jarvis_engine.main ops-sync
python -m jarvis_engine.main ops-brief --snapshot-path ..\.planning\ops_snapshot.json
python -m jarvis_engine.main ops-export-actions --snapshot-path ..\.planning\ops_snapshot.json
python -m jarvis_engine.main automation-run --actions-path ..\.planning\actions.generated.json  # dry-run
```

Voice interaction (local Windows TTS):
```powershell
python -m jarvis_engine.main voice-list
python -m jarvis_engine.main voice-say --text "Good morning. Your top priorities are ready." --profile jarvis_like
python -m jarvis_engine.main voice-enroll --user-id conner --wav .\samples\conner_enroll.wav --replace
python -m jarvis_engine.main voice-verify --user-id conner --wav .\samples\conner_check.wav --threshold 0.82
python -m jarvis_engine.main voice-run --text "Jarvis, sync my calendar and inbox" --execute --speak --voice-user conner --voice-auth-wav .\samples\conner_live.wav
```

Note: exact MCU/actor voice cloning is not guaranteed in this local stack; `jarvis_like` selects the closest available local voice profile.

Generation quality optimization defaults:
- `run-task --quality-profile max_quality` now uses multi-pass code refinement and syntax repair.
- Code generation falls back automatically if your first model is unavailable.
- Override fallback chain with `JARVIS_CODE_MODEL_FALLBACKS` (comma-separated model ids).

## Learning Progress Visibility
Use golden-task evaluation history so improvement is measurable:
- `docs/CAPABILITY_GROWTH_TRACKING.md`
- history file: `.planning/capability_history.jsonl`

## Fiction Target Mapping
- `docs/JARVIS_FICTION_RESEARCH.md` maps MCU J.A.R.V.I.S. behaviors to buildable modules in this repo.
- `docs/THREE_TRACK_EXECUTION_PLAN.md` is the detailed plan for multimodal execution + live ops + S25 voice flow.

## Tests
```powershell
cd C:\Users\Conner\jarvis-memory-repo\engine
pip install -e .[dev]
$env:PYTHONPATH = "src"
pytest -q
```

## Durability + Regression Commands
```powershell
.\scripts\backup-state.ps1
.\scripts\eval-gate.ps1
# restore when needed:
# .\scripts\restore-state.ps1 -ArchivePath C:\path\to\jarvis-state-YYYYMMDD-HHMMSS.zip
```

## Current Decision
- Primary runtime: this desktop PC.
- Weaker laptop: secondary node later, after benchmark gates are met.

## Resume Prompt
"Open this repo, read `.planning/STATE.md` and `JARVIS_MASTERPLAN.md`, then continue Phase 0 implementation in `engine/`."
