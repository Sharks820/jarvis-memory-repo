# Samsung Galaxy S25 Setup

This setup gives your S25 secure control of Jarvis via text and voice-triggered commands.

## 1) Start Jarvis Mobile API on Desktop
```powershell
cd C:\Users\Conner\jarvis-memory-repo\engine
$env:JARVIS_MOBILE_TOKEN = "long-random-auth-token"
$env:JARVIS_MOBILE_SIGNING_KEY = "different-long-random-signing-key"
$env:PYTHONPATH = "src"
python -m jarvis_engine.main serve-mobile --host 0.0.0.0 --port 8787
```

Use private network access only (home LAN or Tailscale).

## 2) Install Android Client Runtime (S25)
1. Install `Termux` on the phone.
2. In Termux:
```bash
pkg update && pkg upgrade -y
pkg install -y python git
```
3. Copy repo (or just the `mobile/` folder) to phone.
4. Run:
```bash
export JARVIS_AUTH_TOKEN="YOUR_AUTH_TOKEN"
export JARVIS_SIGNING_KEY="YOUR_SIGNING_KEY"
python mobile/android_ingest_client.py \
  --base-url http://YOUR_PC_IP:8787 \
  --source user \
  --kind episodic \
  --task-id s25-test-001 \
  --content "Jarvis from S25: remember I prefer concise morning briefings."
```

## 3) Voice Trigger Flow on S25
Recommended pattern:
1. Use Samsung voice trigger (Bixby or Google Assistant) to launch a quick action.
2. Quick action sends text payload into Termux command (or HTTP automation app) using the client script.
3. Jarvis ingests, plans, and returns result to your main interface.

## 4) Verify It Landed
On desktop:
```powershell
cd C:\Users\Conner\jarvis-memory-repo\engine
$env:PYTHONPATH = "src"
python -m jarvis_engine.main status
```

You should see `ingest:user:*` events from S25.

## 5) Practical Usage Templates
1. Teach preference:
```bash
python mobile/android_ingest_client.py ... --kind semantic --task-id s25-pref-001 --content "Always include top 3 priorities first."
```
2. Log completed task:
```bash
python mobile/android_ingest_client.py ... --kind episodic --task-id s25-task-001 --content "Completed bill reconciliation and saved report."
```
3. Store reusable procedure:
```bash
python mobile/android_ingest_client.py ... --kind procedural --task-id s25-proc-001 --content "When a bill is overdue, alert me and generate payment checklist."
```
