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
Spam-call defense guide: `docs/SAMSUNG_SPAM_CALL_DEFENSE.md`

Mobile controls (pause/resume/safe mode/gaming mode):
- `GET /settings` (authenticated) returns runtime + gaming settings
- `GET /dashboard` (authenticated) returns intelligence ranking + ETA projections
- `POST /command` (authenticated) runs plain-language command routing (`voice-run`) without memorizing CLI syntax
- `GET /quick` opens the compact quick panel UI (phone + desktop)
- `POST /settings` (authenticated) updates:
  - `daemon_paused` (bool)
  - `safe_mode` (bool)
  - `gaming_enabled` (bool)
  - `gaming_auto_detect` (bool)
  - `reason` (string)
  - `reset` (bool, optional hard reset to defaults)
  - `X-Jarvis-Device-Id` header for trusted-device enforcement when owner guard is enabled
  - optional first-time bootstrap: include `X-Jarvis-Master-Password` with `X-Jarvis-Device-Id` to auto-trust that device

Open compact Jarvis chat/voice panel:
```powershell
.\scripts\open-jarvis-quick-panel.ps1 -BindHost 127.0.0.1 -Port 8787
# optional desktop hotkey installer (default Ctrl+Alt+J)
.\scripts\install-jarvis-quick-access.ps1 -BindHost 127.0.0.1 -Port 8787
```

Desktop-native local widget (not browser):
```powershell
.\scripts\start-jarvis-widget.ps1
.\scripts\install-jarvis-widget-shortcut.ps1  # default hotkey Ctrl+Alt+K
```

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
python -m jarvis_engine.main ops-autopilot
python -m jarvis_engine.main ops-brief --snapshot-path ..\.planning\ops_snapshot.json
python -m jarvis_engine.main ops-export-actions --snapshot-path ..\.planning\ops_snapshot.json
python -m jarvis_engine.main automation-run --actions-path ..\.planning\actions.generated.json  # dry-run
```

Always-on daemon (24/7 while your user session is active):
```powershell
.\scripts\install-jarvis-startup.ps1 -IntervalSeconds 120 -IdleIntervalSeconds 900 -IdleAfterSeconds 300 -StartNow
# Run now in current terminal:
.\scripts\start-jarvis-daemon.ps1 -IntervalSeconds 120 -IdleIntervalSeconds 900 -IdleAfterSeconds 300
# Remove startup task if needed:
.\scripts\uninstall-jarvis-startup.ps1
```

Gaming mode (pause daemon autopilot workload while you play):
```powershell
python -m jarvis_engine.main gaming-mode --enable --reason "gaming session"
python -m jarvis_engine.main gaming-mode
python -m jarvis_engine.main gaming-mode --disable
python -m jarvis_engine.main gaming-mode --auto-detect on --reason "auto pause on game launch"
```
Optional process watchlist (used by auto-detect): `.planning/gaming_processes.json`

Hardline runtime controls (fast disable/re-enable/failsafe):
```powershell
python -m jarvis_engine.main runtime-control --pause --reason "maintenance"
python -m jarvis_engine.main runtime-control --resume
python -m jarvis_engine.main runtime-control --safe-on --reason "no execution"
python -m jarvis_engine.main runtime-control --safe-off
python -m jarvis_engine.main runtime-control --reset
```

Owner guard (lock Jarvis to your identity + trusted phone):
```powershell
python -m jarvis_engine.main owner-guard --enable --owner-user conner
python -m jarvis_engine.main owner-guard --set-master-password "<your_master_password>"
python -m jarvis_engine.main owner-guard --trust-device galaxy_s25_primary
python -m jarvis_engine.main owner-guard
```

Learning missions (auto-research + verified ingestion):
```powershell
python -m jarvis_engine.main mission-create --topic "Unity 6.3 game architecture" --objective "Learn production-ready systems" --source google --source reddit --source official_docs
python -m jarvis_engine.main mission-status --last 5
python -m jarvis_engine.main mission-run --id m-YYYYMMDDHHMMSS
```
Daemon mode now auto-runs pending missions unless `--skip-missions` is set.

Brain memory indexing (anti-context-regression filing system):
```powershell
python -m jarvis_engine.main brain-status
python -m jarvis_engine.main brain-context --query "gaming pause and resume behavior"
python -m jarvis_engine.main brain-regression
python -m jarvis_engine.main brain-compact --keep-recent 1800
python -m jarvis_engine.main memory-snapshot --create --note "manual checkpoint"
python -m jarvis_engine.main memory-snapshot --verify-path .\.planning\brain\snapshots\brain-snapshot-YYYYMMDD-HHMMSS.zip
python -m jarvis_engine.main memory-maintenance --keep-recent 1800 --snapshot-note nightly
python -m jarvis_engine.main web-research --query "best Samsung spam call blocking setup"
python -m jarvis_engine.main mobile-desktop-sync
python -m jarvis_engine.main self-heal --force-maintenance
python -m jarvis_engine.main intelligence-dashboard
```
Notes:
- Auto-ingest now dedupes, redacts sensitive tokens, and writes both event memory + branch-indexed long-term brain memory.
- `brain-context` retrieves compact, relevance-ranked packets + canonical facts to avoid context clutter.
- `brain-regression` reports duplicate ratio, unresolved memory conflicts, and branch entropy.

Nightly maintenance automation:
```powershell
.\scripts\install-jarvis-nightly-maintenance.ps1 -Time "02:30"
.\scripts\run-jarvis-nightly-maintenance.ps1
```

Persona tuning:
```powershell
python -m jarvis_engine.main persona-config --enable --mode jarvis_british --style brilliant_secret_agent --humor-level 2
python -m jarvis_engine.main persona-config
```

Voice commands also support:
- "Jarvis, enable gaming mode"
- "Jarvis, disable gaming mode"
- "Jarvis, gaming mode status"
- "Jarvis, enable auto gaming mode"
- "Jarvis, disable auto gaming mode"
- "Jarvis, what is the weather in Dallas"
- "Jarvis, search the web for Unity 6.3 performance best practices"
- "Jarvis, sync mobile desktop"
- "Jarvis, self heal" (requires voice auth/master password)

Voice interaction (local Windows TTS):
```powershell
python -m jarvis_engine.main voice-list
python -m jarvis_engine.main voice-say --text "Good morning. Your top priorities are ready." --profile jarvis_like
python -m jarvis_engine.main voice-enroll --user-id conner --wav .\samples\conner_enroll.wav --replace
python -m jarvis_engine.main voice-verify --user-id conner --wav .\samples\conner_check.wav --threshold 0.82
python -m jarvis_engine.main voice-run --text "Jarvis, sync my calendar and inbox" --execute --speak --voice-user conner --voice-auth-wav .\samples\conner_live.wav
python -m jarvis_engine.main voice-run --text "Jarvis, block spam calls now" --execute
```

Security note: executable or privileged `voice-run` requests now require `--voice-auth-wav`.

Note: exact MCU/actor voice cloning is not guaranteed in this local stack; `jarvis_like` selects the closest available local voice profile.

Generation quality optimization defaults:
- `run-task --quality-profile max_quality` now uses multi-pass code refinement and syntax repair.
- Code generation falls back automatically if your first model is unavailable.
- Override fallback chain with `JARVIS_CODE_MODEL_FALLBACKS` (comma-separated model ids).

Phone action queue + spam guard:
```powershell
python -m jarvis_engine.main phone-action --action send_sms --number +14155551234 --message "Running late, call you soon."
python -m jarvis_engine.main phone-action --action place_call --number +14155551234
python -m jarvis_engine.main phone-spam-guard --call-log-path ..\.planning\phone_call_log.json
```

## Learning Progress Visibility
Use golden-task evaluation history so improvement is measurable:
- `docs/CAPABILITY_GROWTH_TRACKING.md`
- `docs/MEMORY_CORE_V2.md`
- `docs/MEMORY_RESEARCH_SYNTHESIS_2026-02-22.md`
- `docs/LIMITLESS_ACCELERATION_ROADMAP.md`
- history file: `.planning/capability_history.jsonl`

## Fiction Target Mapping
- `docs/JARVIS_FICTION_RESEARCH.md` maps MCU J.A.R.V.I.S. behaviors to buildable modules in this repo.
- `docs/THREE_TRACK_EXECUTION_PLAN.md` is the detailed plan for multimodal execution + live ops + S25 voice flow.
- `docs/AUTOPILOT_AGENT_RESEARCH_2026-02-22.md` summarizes OpenClaw/OpenHands/AutoGen/LangGraph patterns and Jarvis upgrades.

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
