Runtime workspace for local execution artifacts.

This directory is intentionally excluded from source control except for this file.

Examples of generated local-only data:
- logs and forensic trails
- audit snapshots and reports
- nonce caches and lock files
- SQLite runtime databases

If you need a reproducible baseline, commit fixtures under `.planning/reports/` or
`.planning/phases/`, not runtime state files.
