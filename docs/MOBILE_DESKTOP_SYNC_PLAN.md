# Jarvis Unlimited Mobile/Desktop Sync Plan

## Goal
Guarantee that learning, memory, and controls stay aligned between desktop and phone with zero manual data copy.

## Step 1: Always-on local services
1. Run `scripts/start-jarvis-services.ps1 -StartWidget`.
2. Keep daemon + mobile API running continuously.
3. Enable startup task so services launch automatically after reboot.

## Step 2: Secure session bootstrap once per device
1. Open `http://127.0.0.1:8787/quick`.
2. Save bearer token + signing key + trusted `device_id`.
3. If owner guard is enabled, trust the phone with master password one time.

## Step 3: Continuous sync checks
1. Run `python -m jarvis_engine.main mobile-desktop-sync` to generate sync report.
2. Review `.planning/runtime/mobile_desktop_sync.json`.
3. Fix any failed check before enabling full automation.

## Step 4: Continuous self-heal loop
1. Run `python -m jarvis_engine.main self-heal`.
2. Review `.planning/runtime/self_heal_report.json`.
3. Let daemon run periodic auto-sync and self-heal cycles.

## Step 5: Learning durability and anti-regression
1. Keep nightly maintenance task enabled (`JarvisNightlyMaintenance`).
2. Run `brain-regression` and `memory-maintenance` after major upgrades.
3. Use signed snapshots for rollback-safe memory recovery.

## Step 6: Verify parity from both endpoints
1. From desktop widget: run `Jarvis, runtime status`, `Jarvis, sync mobile desktop`.
2. From phone quick panel: run the same commands.
3. Confirm results match for runtime state, memory counts, and owner-guard state.
