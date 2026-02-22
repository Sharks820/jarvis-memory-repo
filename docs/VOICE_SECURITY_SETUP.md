# Voice Security Setup

Use this to gate high-value Jarvis commands behind your enrolled voiceprint.

## 1) Enroll your voice
Record a clean WAV sample (16 kHz mono preferred), then run:

```powershell
cd C:\Users\Conner\jarvis-memory-repo\engine
$env:PYTHONPATH = "src"
python -m jarvis_engine.main voice-enroll --user-id conner --wav ..\samples\conner_enroll.wav --replace
```

Profile is stored at:
- `.planning/security/voiceprints/conner.json`

## 2) Verify before command execution

```powershell
python -m jarvis_engine.main voice-verify --user-id conner --wav ..\samples\conner_live.wav --threshold 0.82
```

If `matched=False`, command execution should be blocked.

## 3) Require voice auth in `voice-run`

```powershell
python -m jarvis_engine.main voice-run `
  --text "Jarvis, run automation" `
  --execute `
  --approve-privileged `
  --voice-user conner `
  --voice-auth-wav ..\samples\conner_live.wav `
  --voice-threshold 0.82
```

## 4) Hardening recommendations
- Keep enroll audio private and offline.
- Re-enroll if your mic setup changes significantly.
- Use a dedicated mic profile (same gain/noise suppression each run).
- Pair voice auth with token-based mobile API auth for layered security.

## Notes
- This local voiceprint gate is a practical security layer, not forensic-grade biometrics.
- For stronger anti-spoofing, add liveness checks (challenge phrase) and periodic re-enrollment.
