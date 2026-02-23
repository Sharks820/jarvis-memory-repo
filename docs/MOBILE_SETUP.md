# Jarvis Mobile Setup Guide

Complete guide for connecting your Samsung Galaxy S25 (or any phone) to your Jarvis desktop engine.

---

## Quick Start (One Command)

The fastest way to get everything running:

```powershell
.\scripts\start-jarvis-services.ps1 -BindHost 0.0.0.0
```

This automatically:
- Generates cryptographic tokens (first run only, stored in `.planning/security/mobile_api.json`)
- Starts the background daemon
- Starts the mobile API on port 8787
- Prints the token/signing key location for phone setup

After running, open `http://<your-pc-ip>:8787` on your phone browser.

---

## Step-by-Step Setup

### 1. Prerequisites

- Python 3.12+ with the Jarvis venv activated
- Your PC and phone on the same network (WiFi/LAN) or connected via Tailscale VPN

### 2. Start the Mobile API

**Option A: Automated (recommended)**

```powershell
cd C:\Users\Conner\jarvis-memory-repo
.\scripts\start-jarvis-services.ps1 -BindHost 0.0.0.0
```

**Option B: Manual**

```powershell
cd C:\Users\Conner\jarvis-memory-repo\engine
$env:JARVIS_MOBILE_TOKEN = "your-secret-token-here"
$env:JARVIS_MOBILE_SIGNING_KEY = "your-secret-signing-key-here"
$env:JARVIS_ALLOW_INSECURE_MOBILE_BIND = "true"
$env:PYTHONPATH = "src"
python -m jarvis_engine.main serve-mobile --host 0.0.0.0 --port 8787
```

### 3. Find Your PC's IP Address

```powershell
(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike "*Loopback*" -and $_.PrefixOrigin -eq "Dhcp" }).IPAddress
```

Example output: `192.168.1.42`

### 4. Connect Your Phone

Open Chrome on your Samsung Galaxy S25 and navigate to:

```
http://192.168.1.42:8787
```

You'll see the **Jarvis Quick Panel** -- a mobile-optimized web app with voice dictation, command execution, and dashboard access.

### 5. Enter Credentials (One-Time)

In the Quick Panel, tap **Secure Session** to expand the credential form:

| Field | Where to find it |
|-------|-----------------|
| **Base URL** | Auto-filled from the page URL (e.g. `http://192.168.1.42:8787`) |
| **Bearer token** | From `.planning/security/mobile_api.json` -- the `token` field |
| **Signing key** | From `.planning/security/mobile_api.json` -- the `signing_key` field |
| **Device ID** | Any name for your phone, e.g. `galaxy_s25_primary` |
| **Master password** | Only needed for bootstrap/owner-guard setup |

Tap **Save on Device** to store credentials in your phone's browser localStorage.

**To view your credentials on the PC:**

```powershell
Get-Content .planning\security\mobile_api.json | ConvertFrom-Json | Format-List
```

### 6. Test the Connection

Tap the **Refresh Settings** button in the Quick Panel. If you see settings data appear in the output area, you're connected.

You can also test from your phone terminal or Tasker:

```bash
curl http://192.168.1.42:8787/health
# Should return: {"ok": true, "status": "healthy"}
```

---

## Quick Panel Features

The web-based Quick Panel (`/` or `/quick`) gives you:

- **Voice Dictation** -- Tap "Voice Dictate" to speak commands (uses browser Speech Recognition API)
- **Command Execution** -- Send natural language commands like "Jarvis, runtime status"
- **Quick Controls** -- One-tap Pause/Resume/Safe Mode buttons
- **Settings View** -- Check daemon state, gaming mode, owner guard status
- **Intelligence Dashboard** -- View Jarvis's score, ranking, and ETA projections
- **Keyboard Shortcuts** -- Ctrl+Space toggle panel, Ctrl+Enter send command

---

## Add to Home Screen (Samsung Galaxy S25)

For instant access without opening Chrome first:

1. Open `http://192.168.1.42:8787` in Samsung Internet or Chrome
2. Tap the **three-dot menu** (top right)
3. Tap **"Add to Home screen"**
4. Name it "Jarvis" and tap Add
5. You now have a home screen icon that opens the Quick Panel directly

---

## API Reference

All endpoints except `/health`, `/`, `/quick`, and `/bootstrap` require signed authentication headers.

### Authentication Headers

Every authenticated request needs these headers:

```
Authorization: Bearer <token>
X-Jarvis-Timestamp: <unix_epoch_seconds>
X-Jarvis-Nonce: <unique_random_string>
X-Jarvis-Signature: <hex_hmac_sha256>
```

The signature is computed as:
```
HMAC-SHA256(signing_key, "{timestamp}\n{nonce}\n{raw_body}")
```

For GET requests, the body is empty (`""`).

### Public Endpoints (No Auth)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Jarvis Quick Panel (web UI) |
| GET | `/quick` | Same as `/` |
| GET | `/health` | Health check -- returns `{"ok": true, "status": "healthy"}` |
| POST | `/bootstrap` | Get credentials from localhost (master password required) |

### Authenticated Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/settings` | Current runtime/gaming/owner-guard settings |
| POST | `/settings` | Update daemon pause, safe mode, gaming mode |
| GET | `/dashboard` | Intelligence dashboard (score, ranking, ETAs) |
| POST | `/ingest` | Send a memory record (episodic/semantic/procedural) |
| POST | `/command` | Execute a voice/text command |
| GET | `/sync/status` | Sync engine status |
| POST | `/sync/pull` | Pull encrypted changes for a device |
| POST | `/sync/push` | Push encrypted changes from a device |
| POST | `/self-heal` | Trigger memory maintenance/self-heal |

### POST /ingest -- Send Memory Records

```json
{
  "source": "user",
  "kind": "semantic",
  "task_id": "mobile-2026-02-23-01",
  "content": "Learned: always summarize in 5 bullets."
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `source` | `user`, `claude`, `opus`, `gemini`, `task_outcome` | Who created the record |
| `kind` | `episodic`, `semantic`, `procedural` | Memory tier |
| `task_id` | Any string up to 128 chars | Unique ID for deduplication |
| `content` | Text up to 20,000 chars | The memory content |

**Memory kinds:**
- `episodic` -- What happened (events, outcomes, task results)
- `semantic` -- Stable facts and preferences
- `procedural` -- Reusable step-by-step instructions

### POST /command -- Voice/Text Commands

```json
{
  "text": "Jarvis, give me my morning briefing",
  "execute": true,
  "approve_privileged": false,
  "speak": false
}
```

Available command intents (from `mobile/voice_command_schema.json`):
- `ops_sync` / `ops_brief` -- Sync data or get a briefing
- `runtime_pause` / `runtime_resume` / `runtime_status` -- Daemon control
- `runtime_safe_on` / `runtime_safe_off` -- Safe mode toggle
- `gaming_mode_enable` / `gaming_mode_disable` / `gaming_mode_status` -- Gaming mode
- `phone_spam_guard` / `phone_send_sms` / `phone_place_call` / `phone_ignore_call` -- Phone actions
- `generate_code` / `generate_image` / `generate_video` / `generate_model3d` -- Content generation

### POST /settings -- Update Settings

```json
{
  "daemon_paused": true,
  "safe_mode": false,
  "gaming_enabled": true,
  "gaming_auto_detect": true,
  "reason": "gaming session"
}
```

Set `"reset": true` to restore all settings to defaults.

### POST /bootstrap -- First-Time Device Setup

Called from localhost only. Provides the token and signing key to the phone:

```json
{
  "master_password": "your-master-password",
  "device_id": "galaxy_s25_primary"
}
```

Returns the `token`, `signing_key`, and `device_id` needed for authenticated requests.

---

## Owner Guard (Optional Security)

Owner Guard restricts API access to trusted devices only. When enabled, every request must include `X-Jarvis-Device-Id` matching a pre-trusted device.

### Enable Owner Guard

```powershell
cd engine
$env:PYTHONPATH = "src"
python -m jarvis_engine.main owner-guard --enable --owner conner
python -m jarvis_engine.main owner-guard --set-master-password
```

### Trust Your Phone

**Option A: Via bootstrap endpoint** (recommended for first device)
1. Set master password as above
2. From your phone's Quick Panel, enter master password in the Secure Session section
3. Enter a device ID (e.g. `galaxy_s25_primary`)
4. Make your first authenticated request -- the device is auto-trusted

**Option B: Via CLI**
```powershell
python -m jarvis_engine.main owner-guard --trust-device galaxy_s25_primary
```

---

## Network Security

### Recommended: Tailscale VPN

For the safest setup, use [Tailscale](https://tailscale.com/) (free for personal use):

1. Install Tailscale on your PC and phone
2. Use the Tailscale IP (e.g. `100.x.y.z`) instead of your LAN IP
3. Traffic is end-to-end encrypted
4. Works from anywhere, not just your home network

### LAN-Only Setup

If not using Tailscale:
- Only access the API from your home WiFi
- The API uses HMAC-SHA256 signed requests (not just a simple token)
- Every request has replay protection (nonces + timestamp window)
- Never expose port 8787 to the public internet

### Windows Firewall

If your phone can't reach the API, allow the port through Windows Firewall:

```powershell
New-NetFirewallRule -DisplayName "Jarvis Mobile API" -Direction Inbound -Protocol TCP -LocalPort 8787 -Action Allow
```

---

## Samsung Galaxy S25 Specific Tips

### Samsung Internet vs Chrome
Both work. Chrome has better Speech Recognition API support for the voice dictation button.

### Bixby Routines Integration
Create a Bixby Routine to auto-open Jarvis:
1. Open **Settings > Modes and Routines > Routines**
2. Add trigger: "When connected to home WiFi"
3. Add action: "Open app > Chrome" with URL `http://your-pc-ip:8787`

### Tasker/HTTP Shortcuts Integration
For automated requests without opening a browser:

1. Install [HTTP Shortcuts](https://play.google.com/store/apps/details?id=ch.rmy.android.http_shortcuts) from the Play Store
2. Create a new shortcut with:
   - URL: `http://your-pc-ip:8787/command`
   - Method: POST
   - Body: `{"text": "Jarvis, morning briefing", "execute": true}`
   - Headers: Add the auth headers (token, timestamp, nonce, signature)

### Keep Screen On During Voice Dictation
Go to **Settings > Display > Screen timeout** and set to 5 minutes, or use the developer option "Stay awake while charging."

---

## Troubleshooting

### "Connection refused" on phone
- Verify PC IP: `ipconfig` in PowerShell
- Check the API is running: `curl http://localhost:8787/health` on the PC
- Check Windows Firewall (see above)
- Make sure both devices are on the same WiFi network

### "Invalid bearer token" (401)
- Re-check the token in `.planning/security/mobile_api.json`
- Make sure there are no extra spaces when copying
- Tap "Save on Device" again in the Quick Panel after re-entering

### "Invalid request signature" (401)
- Verify the signing key matches exactly
- Check that your phone's clock is accurate (within 5 minutes of your PC)
- The signature is HMAC-SHA256 of `"{timestamp}\n{nonce}\n{body}"`

### "Untrusted mobile device" (401)
- Owner Guard is enabled but your device isn't trusted
- Either disable Owner Guard or trust your device (see Owner Guard section)

### "Expired timestamp" (401)
- Your phone's clock is more than 5 minutes off from your PC
- Fix: **Settings > General management > Date and time > Automatic date and time**

### Voice dictation not working
- Chrome requires HTTPS for Speech Recognition on non-localhost origins
- Workaround: use HTTP Shortcuts app with manual text input
- Or access via `localhost` if running on same device

### API stops responding after PC sleep
- The service script handles this -- just run `.\scripts\start-jarvis-services.ps1` again
- For auto-restart, install the startup task (see below)

---

## Auto-Start on Boot

To start Jarvis services automatically when your PC boots:

```powershell
.\scripts\install-jarvis-startup.ps1
```

This creates a Windows Scheduled Task that runs `start-jarvis-services.ps1` at login.

---

## Stopping Services

```powershell
.\scripts\stop-jarvis-services.ps1
```

This cleanly stops the daemon and mobile API processes.

---

## Verifying Everything Works

Run through this checklist after setup:

1. **Health check**: Open `http://your-pc-ip:8787/health` on phone -- should see `{"ok": true}`
2. **Quick Panel**: Open `http://your-pc-ip:8787` -- should see the Jarvis UI
3. **Settings**: Enter credentials, tap "Refresh Settings" -- should show runtime state
4. **Dashboard**: Tap "Refresh Dashboard" -- should show score and ranking
5. **Command**: Type "Jarvis, runtime status" and tap Send -- should get a response
6. **Voice**: Tap "Voice Dictate", say a command -- should populate the text field
7. **Ingest**: Use the API to send a test memory record (via curl or HTTP Shortcuts)

All 27 mobile API tests pass in the test suite, covering authentication, replay protection, concurrent writes, settings management, dashboard, commands, owner guard, bootstrap, sync, and self-heal.
