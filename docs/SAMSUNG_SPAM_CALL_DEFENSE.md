# Samsung S25 Spam Call Defense

This setup handles rotating scam numbers with layered defense:

1. Samsung/Google native spam filters.
2. Jarvis spam-pattern analysis from call logs.
3. Auto-queued phone actions (`block_number` + temporary `silence_unknown_callers`).

## Immediate device settings (do this first)
- Phone app -> Settings -> Caller ID and spam protection -> turn on.
- Enable blocking for high-risk spam/scam calls.
- Optional: block unknown/private numbers during work hours.

## Jarvis spam guard flow

1) Export call log JSON to:
- `.planning/phone_call_log.json`

Expected item example:
```json
{
  "number": "+14155551234",
  "type": "missed",
  "duration_sec": 0,
  "contact_name": "",
  "ts_utc": "2026-02-22T18:20:00+00:00"
}
```

2) Run spam analysis:
```powershell
cd C:\Users\Conner\jarvis-memory-repo\engine
$env:PYTHONPATH = "src"
python -m jarvis_engine.main phone-spam-guard
```

Outputs:
- `.planning/phone_spam_report.json`
- `.planning/phone_actions.jsonl`

3) Trigger by voice:
```powershell
python -m jarvis_engine.main voice-run --text "Jarvis, block spam calls now" --execute
```

## Two prompt options (always)
For missing phone setup or spam guard actions:
- Voice option: ask Jarvis to run the connection or spam action.
- Tap option: open setup URL directly.

## Why this works against rotating numbers
- It scores behavior patterns (repeat bursts, missed/short call ratios, unknown inbound patterns, rotating area-code clusters).
- It can apply a global temporary rule (`silence_unknown_callers`) when spam volume spikes.
- You are not limited to static number blocklists.
