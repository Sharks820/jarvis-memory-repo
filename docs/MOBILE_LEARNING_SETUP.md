# Mobile Learning Setup

This flow lets your phone send structured learning records into Jarvis.

## 1) Start the Mobile API
From `engine/`:

```powershell
$env:JARVIS_MOBILE_TOKEN = "set-a-long-random-token"
$env:JARVIS_MOBILE_SIGNING_KEY = "set-a-different-long-random-key"
$env:PYTHONPATH = "src"
python -m jarvis_engine.main serve-mobile --host 127.0.0.1 --port 8787
```

To accept requests from your phone on LAN/VPN, explicitly bind:
```powershell
python -m jarvis_engine.main serve-mobile --host 0.0.0.0 --port 8787
```

API endpoints:
- `GET /health`
- `POST /ingest`

## 2) Use Network Access Safely
- Preferred: use Tailscale or private VPN and keep this API private.
- Avoid exposing port `8787` directly to the public internet.

## 3) Request Format (Phone/Shortcut App)
Body JSON:

```json
{
  "source": "user",
  "kind": "semantic",
  "task_id": "mobile-2026-02-22-01",
  "content": "Learned preference: summarize tasks in 5 bullets."
}
```

Headers:
- `Authorization: Bearer <token>`
- `X-Jarvis-Timestamp: <unix epoch seconds>`
- `X-Jarvis-Nonce: <unique random id per request>`
- `X-Jarvis-Signature: <hex hmac sha256 of "{timestamp}\n{nonce}\n{raw_body}" using signing key>`

## 4) Verify It Landed
Run:

```powershell
$env:PYTHONPATH = "src"
python -m jarvis_engine.main status
```

Or inspect:
- `.planning/events.jsonl`

## 5) Suggested Mobile Event Types
- `kind=episodic`: what happened in a task.
- `kind=semantic`: stable facts/preferences learned.
- `kind=procedural`: reusable step-by-step instructions.
