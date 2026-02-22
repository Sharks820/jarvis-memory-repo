# Next Session Checklist

1. Paste full hardware specs from target laptop (GPU/VRAM, RAM, CPU, storage, OS).
2. Choose operating mode:
   - Free-Only Local Mode
   - Teacher-Assisted Mode (uses paid subscriptions manually, not API)
3. Define security strictness:
   - Paranoid (max lockdown)
   - Balanced
4. Confirm mobile workflow split for Samsung S25:
   - Primary remote to laptop via secure tunnel
   - Optional on-device fallback model
5. Run bootstrap and verify local scaffold:
   - `.\scripts\bootstrap.ps1`
   - `.venv\Scripts\jarvis-engine.exe status`
6. Implement first three production controls:
   - Ollama provider adapter
   - Capability gate enforcement in execution path
   - Encrypted backup and restore automation
