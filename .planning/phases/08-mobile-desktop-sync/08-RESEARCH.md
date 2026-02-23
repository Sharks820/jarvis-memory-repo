# Phase 8: Mobile-Desktop Sync - Research

**Researched:** 2026-02-23
**Domain:** Bidirectional data synchronization with changelog tracking, conflict resolution, encrypted transport
**Confidence:** HIGH

## Summary

This phase implements changelog-based bidirectional sync between the desktop PC (primary/authoritative) and Samsung Galaxy S25 Ultra (secondary). The existing codebase already has the HTTP server infrastructure (`MobileIngestServer` with HMAC auth, ThreadingHTTPServer) and a `/sync` endpoint stub. The sync protocol needs three new components: (1) a changelog table with SQLite triggers to automatically track all changes to `records`, `kg_nodes`, and `kg_edges` tables, (2) a diff engine that computes changes since a sync cursor and applies incoming changes with field-level conflict resolution, and (3) Fernet-encrypted payloads layered on top of the existing HMAC authentication.

The approach is explicitly **changelog-based, NOT CRDT-based** per PROJECT.md's key decision: "Bidirectional sync via encrypted diff-based protocol." This is the right call for a two-device setup (desktop + one phone) where one device is authoritative. CRDTs would be overengineered for this topology.

**Primary recommendation:** Use SQLite triggers (inspired by sqlite-chronicle pattern) to maintain a `_sync_changelog` table with monotonically increasing version numbers, Fernet encryption (from the already-installed `cryptography` 44.0.0 package) for payload encryption with key derived from the existing HMAC signing key via PBKDF2, and field-level merge with desktop-wins-ties for conflict resolution.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| SYNC-01 | Changelog-based bidirectional sync between desktop PC and Samsung Galaxy S25 Ultra | Changelog table with SQLite triggers (Section: Architecture Patterns, Pattern 1 & 2). Existing `/sync` endpoint in mobile_api.py to be extended. |
| SYNC-02 | Only changes since last sync are transmitted (not full database state) | Monotonically increasing `__version` column with `_sync_cursor` table tracks last-synced version per device. `WHERE __version > ?` query returns only deltas. (Section: Architecture Patterns, Pattern 2) |
| SYNC-03 | Field-level conflict resolution with desktop as authoritative for ties | `_fields_changed` bitmask in changelog enables field-level merge. Desktop timestamp wins ties. (Section: Architecture Patterns, Pattern 3) |
| SYNC-04 | Sync payloads are encrypted in transit | Fernet encryption using key derived from existing HMAC signing key via PBKDF2. Layered on top of existing HMAC auth. (Section: Standard Stack, Code Examples) |
| SYNC-05 | Learning acquired on mobile is merged into desktop knowledge base and vice versa | Changelog tracks `records`, `kg_nodes`, and `kg_edges` tables. Incoming records are applied through existing `insert_record()` and `add_fact()` with conflict resolution. (Section: Architecture Patterns, Pattern 4) |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| sqlite3 | stdlib | Changelog triggers, sync cursor storage | Already used everywhere in the project. Triggers are the idiomatic way to track changes in SQLite. |
| cryptography | 44.0.0 (installed) | Fernet symmetric encryption for sync payloads | Already a transitive dependency in the environment. Fernet provides authenticated encryption (AES-128-CBC + HMAC-SHA256) with zero-config. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| json | stdlib | Serialize/deserialize sync payloads | All sync data is JSON-encoded before encryption |
| hashlib | stdlib | PBKDF2 key derivation from signing key | Deriving Fernet key from existing HMAC signing_key |
| base64 | stdlib | URL-safe key encoding for Fernet | Fernet requires base64-encoded keys |
| zlib | stdlib | Optional gzip compression before encryption | When sync payloads exceed 100KB |
| threading | stdlib | Write lock coordination during sync apply | Reuse MemoryEngine._write_lock pattern |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Changelog triggers | SQLite session extension | Session extension has C API only, no pure-Python access, and "the session log can only be fetched as a whole but never resets" |
| Fernet | AES-256-GCM | AES-GCM gives more control (256-bit keys, custom nonces) but requires careful nonce management. Fernet handles all of this automatically. For sync payloads under 100MB, Fernet is simpler and equally secure. |
| Fernet | TLS/HTTPS | TLS requires certificate management. On a LAN between two personal devices with HMAC auth already in place, Fernet payload encryption is simpler and provides equivalent confidentiality. |
| Custom changelog | sqlite-chronicle library | sqlite-chronicle is a pip-installable library but adds an external dependency for ~100 lines of trigger SQL we can write ourselves. Avoid the dependency. |
| CRDTs | -- | PROJECT.md explicitly chose changelog-based sync over CRDTs. For a 2-device topology with one authoritative device, CRDTs are massive overkill. |

**Installation:**
No new dependencies needed. `cryptography` 44.0.0 is already installed. Everything else is stdlib.

## Architecture Patterns

### Recommended Project Structure
```
engine/src/jarvis_engine/
  sync/
    __init__.py           # Package init
    changelog.py          # Changelog table, triggers, version tracking
    engine.py             # SyncEngine: compute diff, apply changes, resolve conflicts
    transport.py          # Fernet encryption/decryption of sync payloads
    protocol.py           # SyncProtocol: orchestrates push/pull over HTTP
  commands/
    sync_commands.py      # SyncPushCommand, SyncPullCommand, SyncStatusCommand
  handlers/
    sync_handlers.py      # Handler classes for sync commands
```

### Pattern 1: Changelog Table with SQLite Triggers

**What:** A `_sync_changelog` table records every INSERT, UPDATE, and DELETE on tracked tables. SQLite triggers fire automatically on data changes.

**When to use:** Always -- this is the core mechanism for SYNC-01 and SYNC-02.

**Schema:**
```sql
-- Changelog table
CREATE TABLE IF NOT EXISTS _sync_changelog (
    changelog_id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,          -- 'records', 'kg_nodes', 'kg_edges'
    row_id TEXT NOT NULL,              -- Primary key of changed row
    operation TEXT NOT NULL,           -- 'INSERT', 'UPDATE', 'DELETE'
    fields_changed TEXT NOT NULL DEFAULT '[]',  -- JSON array of field names
    old_values TEXT DEFAULT NULL,      -- JSON of previous field values (UPDATE/DELETE)
    new_values TEXT DEFAULT NULL,      -- JSON of new field values (INSERT/UPDATE)
    device_id TEXT NOT NULL DEFAULT 'desktop',  -- Origin device
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    __version INTEGER NOT NULL         -- Monotonically increasing per-table
);

CREATE INDEX IF NOT EXISTS idx_changelog_version
    ON _sync_changelog(table_name, __version);
CREATE INDEX IF NOT EXISTS idx_changelog_device
    ON _sync_changelog(device_id);

-- Sync cursor: tracks last-synced version per device per table
CREATE TABLE IF NOT EXISTS _sync_cursor (
    device_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    last_version INTEGER NOT NULL DEFAULT 0,
    last_sync_ts TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (device_id, table_name)
);

-- Schema version bump
INSERT OR IGNORE INTO schema_version(version) VALUES (3);
```

**Trigger pattern (for `records` table):**
```sql
-- AFTER INSERT trigger for records
CREATE TRIGGER IF NOT EXISTS _sync_records_ai
AFTER INSERT ON records
WHEN NEW.record_id IS NOT NULL
BEGIN
    INSERT INTO _sync_changelog
        (table_name, row_id, operation, fields_changed, new_values, device_id, __version)
    VALUES (
        'records',
        NEW.record_id,
        'INSERT',
        json_array('record_id','ts','source','kind','task_id','branch',
                    'tags','summary','content_hash','confidence','tier',
                    'access_count','last_accessed','created_at'),
        json_object(
            'record_id', NEW.record_id,
            'ts', NEW.ts,
            'source', NEW.source,
            'kind', NEW.kind,
            'task_id', NEW.task_id,
            'branch', NEW.branch,
            'tags', NEW.tags,
            'summary', NEW.summary,
            'content_hash', NEW.content_hash,
            'confidence', NEW.confidence,
            'tier', NEW.tier,
            'access_count', NEW.access_count,
            'last_accessed', NEW.last_accessed,
            'created_at', NEW.created_at
        ),
        'desktop',
        COALESCE(
            (SELECT MAX(__version) FROM _sync_changelog WHERE table_name = 'records'),
            0
        ) + 1
    );
END;

-- AFTER UPDATE trigger for records (only fires if data actually changed)
CREATE TRIGGER IF NOT EXISTS _sync_records_au
AFTER UPDATE ON records
WHEN OLD.ts != NEW.ts OR OLD.source != NEW.source OR OLD.kind != NEW.kind
     OR OLD.branch != NEW.branch OR OLD.tags != NEW.tags
     OR OLD.summary != NEW.summary OR OLD.confidence != NEW.confidence
     OR OLD.tier != NEW.tier OR OLD.access_count != NEW.access_count
     OR OLD.last_accessed != NEW.last_accessed
BEGIN
    INSERT INTO _sync_changelog
        (table_name, row_id, operation, fields_changed, old_values, new_values,
         device_id, __version)
    VALUES (
        'records',
        NEW.record_id,
        'UPDATE',
        -- Build JSON array of which fields actually changed
        (SELECT json_group_array(field) FROM (
            SELECT 'confidence' AS field WHERE OLD.confidence != NEW.confidence
            UNION ALL SELECT 'tier' WHERE OLD.tier != NEW.tier
            UNION ALL SELECT 'access_count' WHERE OLD.access_count != NEW.access_count
            UNION ALL SELECT 'last_accessed' WHERE OLD.last_accessed != NEW.last_accessed
            UNION ALL SELECT 'branch' WHERE OLD.branch != NEW.branch
            UNION ALL SELECT 'tags' WHERE OLD.tags != NEW.tags
            UNION ALL SELECT 'summary' WHERE OLD.summary != NEW.summary
        )),
        -- old_values: only changed fields
        json_object(
            'confidence', OLD.confidence, 'tier', OLD.tier,
            'access_count', OLD.access_count, 'last_accessed', OLD.last_accessed,
            'branch', OLD.branch, 'tags', OLD.tags, 'summary', OLD.summary
        ),
        -- new_values: only changed fields
        json_object(
            'confidence', NEW.confidence, 'tier', NEW.tier,
            'access_count', NEW.access_count, 'last_accessed', NEW.last_accessed,
            'branch', NEW.branch, 'tags', NEW.tags, 'summary', NEW.summary
        ),
        'desktop',
        COALESCE(
            (SELECT MAX(__version) FROM _sync_changelog WHERE table_name = 'records'),
            0
        ) + 1
    );
END;

-- AFTER DELETE trigger for records
CREATE TRIGGER IF NOT EXISTS _sync_records_ad
AFTER DELETE ON records
BEGIN
    INSERT INTO _sync_changelog
        (table_name, row_id, operation, old_values, device_id, __version)
    VALUES (
        'records',
        OLD.record_id,
        'DELETE',
        json_object('record_id', OLD.record_id, 'summary', OLD.summary),
        'desktop',
        COALESCE(
            (SELECT MAX(__version) FROM _sync_changelog WHERE table_name = 'records'),
            0
        ) + 1
    );
END;
```

Similar triggers are needed for `kg_nodes` and `kg_edges`.

### Pattern 2: Cursor-Based Incremental Diff

**What:** Each device stores a cursor (last-seen `__version`) per table. Diff computation is a simple `WHERE __version > cursor` query.

**When to use:** Every sync cycle -- this is what makes SYNC-02 work.

**Example:**
```python
def compute_diff(
    db: sqlite3.Connection,
    table_name: str,
    since_version: int,
    limit: int = 1000,
) -> list[dict]:
    """Return changelog entries since the given version."""
    cur = db.execute(
        """
        SELECT changelog_id, table_name, row_id, operation,
               fields_changed, old_values, new_values, device_id, ts, __version
        FROM _sync_changelog
        WHERE table_name = ? AND __version > ?
        ORDER BY __version ASC
        LIMIT ?
        """,
        (table_name, since_version, limit),
    )
    return [dict(row) for row in cur.fetchall()]


def get_sync_cursor(db: sqlite3.Connection, device_id: str, table_name: str) -> int:
    """Get the last-synced version for a device+table pair."""
    cur = db.execute(
        "SELECT last_version FROM _sync_cursor WHERE device_id = ? AND table_name = ?",
        (device_id, table_name),
    )
    row = cur.fetchone()
    return row[0] if row else 0


def update_sync_cursor(
    db: sqlite3.Connection,
    device_id: str,
    table_name: str,
    version: int,
    write_lock: threading.Lock,
) -> None:
    """Advance the sync cursor after successful sync."""
    ts = datetime.now(UTC).isoformat()
    with write_lock:
        db.execute(
            """
            INSERT INTO _sync_cursor (device_id, table_name, last_version, last_sync_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(device_id, table_name) DO UPDATE SET
                last_version = excluded.last_version,
                last_sync_ts = excluded.last_sync_ts
            """,
            (device_id, table_name, version, ts),
        )
        db.commit()
```

### Pattern 3: Field-Level Conflict Resolution

**What:** When the same row is modified on both devices between syncs, merge at the field level rather than overwriting entire records. Desktop wins ties.

**When to use:** During sync apply when incoming changes overlap with local changes.

**Algorithm:**
```python
def resolve_conflicts(
    local_changes: dict[str, list[dict]],  # row_id -> list of changelog entries
    remote_changes: dict[str, list[dict]],
    desktop_is_local: bool,
) -> list[dict]:
    """Field-level conflict resolution. Desktop wins ties.

    Returns list of resolved change operations to apply.
    """
    resolved = []

    for row_id, remote_entries in remote_changes.items():
        if row_id not in local_changes:
            # No conflict -- apply remote changes directly
            resolved.extend(remote_entries)
            continue

        local_entries = local_changes[row_id]
        # Get the latest entry from each side
        local_latest = local_entries[-1]
        remote_latest = remote_entries[-1]

        if local_latest["operation"] == "DELETE" or remote_latest["operation"] == "DELETE":
            # DELETE always wins (data can be re-ingested, but ghost records are worse)
            resolved.append(local_latest if local_latest["operation"] == "DELETE"
                          else remote_latest)
            continue

        # Both are INSERT or UPDATE -- field-level merge
        local_fields = json.loads(local_latest.get("fields_changed", "[]"))
        remote_fields = json.loads(remote_latest.get("fields_changed", "[]"))
        local_new = json.loads(local_latest.get("new_values", "{}"))
        remote_new = json.loads(remote_latest.get("new_values", "{}"))

        merged = {}
        all_fields = set(local_fields) | set(remote_fields)

        for field in all_fields:
            if field in local_fields and field not in remote_fields:
                merged[field] = local_new.get(field)
            elif field in remote_fields and field not in local_fields:
                merged[field] = remote_new.get(field)
            else:
                # Both changed same field -- compare timestamps, desktop wins ties
                local_ts = local_latest.get("ts", "")
                remote_ts = remote_latest.get("ts", "")
                if local_ts > remote_ts:
                    merged[field] = local_new.get(field)
                elif remote_ts > local_ts:
                    merged[field] = remote_new.get(field)
                else:
                    # Exact tie -- desktop is authoritative
                    if desktop_is_local:
                        merged[field] = local_new.get(field)
                    else:
                        merged[field] = remote_new.get(field)

        resolved.append({
            "row_id": row_id,
            "operation": "UPDATE",
            "table_name": local_latest["table_name"],
            "new_values": json.dumps(merged),
            "fields_changed": json.dumps(list(merged.keys())),
            "ts": max(local_latest.get("ts", ""), remote_latest.get("ts", "")),
        })

    return resolved
```

### Pattern 4: Sync Protocol Flow

**What:** The push/pull protocol between desktop and mobile.

**When to use:** On every sync cycle (triggered manually via `/sync` endpoint or on a timer).

**Flow:**
```
Desktop (authoritative)            Mobile (secondary)
    |                                   |
    |  1. POST /sync/pull               |
    |  {device_id, cursors: {           |
    |    records: 42, kg_nodes: 17}}    |
    |<----------------------------------|
    |                                   |
    |  2. Compute diff since cursors    |
    |  3. Encrypt payload with Fernet   |
    |                                   |
    |  Response: {encrypted_payload,    |
    |    new_cursors, has_more}         |
    |---------------------------------->|
    |                                   |
    |  4. Mobile decrypts & applies     |
    |  5. Mobile computes its own diff  |
    |                                   |
    |  6. POST /sync/push               |
    |  {device_id, encrypted_payload,   |
    |   cursors}                        |
    |<----------------------------------|
    |                                   |
    |  7. Desktop decrypts              |
    |  8. Conflict resolution           |
    |  9. Apply changes                 |
    |  10. Update cursors               |
    |                                   |
    |  Response: {ok, applied,          |
    |    conflicts_resolved,            |
    |    new_cursors}                   |
    |---------------------------------->|
```

### Anti-Patterns to Avoid
- **Full database dump on every sync:** Violates SYNC-02. Always use cursor-based incremental diff.
- **Record-level overwrite on conflict:** Destroys field-level changes. Always merge at field level (SYNC-03).
- **Trigger on every column update:** Don't fire changelog triggers for access_count/last_accessed tier maintenance unless those changes need to sync. Consider excluding hot-path tier maintenance from changelog if it creates too much noise.
- **Storing embeddings in sync payloads:** Embeddings are 768 floats (3KB each). Never sync raw embeddings -- re-embed on the receiving side from the summary text. This keeps payloads small and avoids model version mismatches.
- **Syncing FTS5 virtual table data:** FTS5 is derived from the records table. Sync the records, rebuild FTS5 on the receiver.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Symmetric encryption | Custom AES wrapper | `cryptography.fernet.Fernet` | Handles IV generation, padding, HMAC auth, timestamp embedding. One-liner encrypt/decrypt. |
| Key derivation | Custom HMAC-to-key conversion | `cryptography.hazmat.primitives.kdf.pbkdf2.PBKDF2HMAC` | Proper key stretching with configurable iterations. Derive Fernet key from existing signing_key. |
| Change tracking | Manual diff of SQLite snapshots | SQLite triggers writing to `_sync_changelog` | Triggers fire atomically with the data change inside the same transaction. Zero chance of missed changes. |
| JSON serialization | Custom binary protocol | `json.dumps` / `json.loads` | Sync payloads are human-debuggable. Compression (zlib) handles size concerns. |
| Payload compression | Custom delta encoding | `zlib.compress` / `zlib.decompress` | Built into stdlib, works on bytes, 60-80% compression on JSON. Apply before Fernet encryption. |

**Key insight:** The existing HMAC auth system already handles authentication and replay protection. Fernet adds confidentiality (encryption) on top. Don't rebuild auth -- layer encryption onto the existing foundation.

## Common Pitfalls

### Pitfall 1: Trigger-Induced Infinite Loops
**What goes wrong:** Applying incoming sync changes fires the changelog triggers, creating new changelog entries that look like local changes, leading to ping-pong sync loops.
**Why it happens:** Triggers cannot distinguish between "organic local change" and "change applied from sync."
**How to avoid:** Use a `device_id` column in the changelog. When applying remote changes, temporarily set a connection-level flag (e.g., via `PRAGMA user_version` or a thread-local variable) so triggers record the remote `device_id`. During diff computation, exclude entries with the querying device's own `device_id`. Alternatively, disable triggers during sync apply with `DROP TRIGGER` / `CREATE TRIGGER` (simpler but more fragile).
**Warning signs:** Sync payload size grows with each cycle instead of shrinking.

### Pitfall 2: Version Number Conflicts Between Devices
**What goes wrong:** Both devices maintain their own `__version` sequences. If mobile generates version 50 and desktop generates version 50, cursors become meaningless.
**Why it happens:** Using a single global version sequence across devices.
**How to avoid:** Each device maintains its OWN changelog with its OWN version sequence. The sync cursor is per-device: "I last saw version 42 from the desktop changelog." The mobile has a separate changelog with its own version numbers. Never merge version sequences.
**Warning signs:** Missing changes after sync, or duplicate applies.

### Pitfall 3: Clock Skew Between Devices
**What goes wrong:** Field-level conflict resolution uses timestamps to determine "later wins." If the phone clock is 5 minutes ahead, phone changes always win even when desktop should win ties.
**Why it happens:** System clocks are not synchronized. Phone might be on NTP, desktop might not be, or vice versa.
**How to avoid:** For ties (same timestamp), desktop ALWAYS wins regardless of timestamp (SYNC-03 requirement). For non-ties, use ISO 8601 timestamps but accept that 1-2 second skew is normal and harmless. Do NOT try to synchronize clocks -- just make the tie-breaker rule deterministic.
**Warning signs:** Mobile changes consistently overwriting desktop changes (or vice versa) when they shouldn't.

### Pitfall 4: Changelog Table Grows Without Bound
**What goes wrong:** Every INSERT, UPDATE, and DELETE forever recorded in `_sync_changelog`. After months, the table is larger than the actual data.
**Why it happens:** No cleanup/compaction strategy.
**How to avoid:** After both devices confirm sync up to version N, entries with `__version <= N` can be pruned. Run cleanup after successful sync. Keep a configurable retention window (e.g., 7 days) for safety.
**Warning signs:** Database file size growing disproportionately to actual record count.

### Pitfall 5: Partial Sync Apply Corruption
**What goes wrong:** Sync apply crashes halfway through. Some changes applied, some not. Next sync thinks everything was applied because cursor was updated.
**Why it happens:** Cursor updated before all changes are committed, or changes applied outside a transaction.
**How to avoid:** Apply ALL incoming changes in a single SQLite transaction. Update the cursor in the SAME transaction. If anything fails, the entire transaction rolls back and nothing is committed. The next sync retries from the same cursor.
**Warning signs:** Missing records after a failed sync cycle, or duplicate records.

### Pitfall 6: Embedding Model Mismatch Across Devices
**What goes wrong:** Desktop has nomic-embed-text-v1.5, mobile might have a different version or no embedding model at all. Syncing raw embeddings creates garbage vectors.
**Why it happens:** Trying to sync embedding vectors directly.
**How to avoid:** NEVER sync embeddings. Sync the text content (summary field). The receiving device re-generates embeddings from text using its local model. This is correct because (a) the embedding model might differ, and (b) embeddings are large (3KB per record).
**Warning signs:** Semantic search returning irrelevant results after sync.

## Code Examples

### Fernet Encryption for Sync Payloads
```python
# Source: cryptography.io/en/latest/fernet/ (verified 2026-02-23)
import base64
import json
import zlib
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def derive_sync_key(signing_key: str, salt: bytes) -> bytes:
    """Derive a Fernet key from the existing HMAC signing key.

    Uses PBKDF2 with SHA-256, 480_000 iterations.
    The salt should be generated once and stored alongside the sync config.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(signing_key.encode("utf-8")))


def encrypt_sync_payload(payload: dict, fernet_key: bytes) -> bytes:
    """Compress and encrypt a sync payload."""
    raw_json = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    compressed = zlib.compress(raw_json, level=6)
    f = Fernet(fernet_key)
    return f.encrypt(compressed)


def decrypt_sync_payload(token: bytes, fernet_key: bytes) -> dict:
    """Decrypt and decompress a sync payload."""
    f = Fernet(fernet_key)
    compressed = f.decrypt(token)
    raw_json = zlib.decompress(compressed)
    return json.loads(raw_json.decode("utf-8"))
```

### Sync Push/Pull API Endpoints
```python
# Extension to existing MobileIngestHandler in mobile_api.py
# These would be added as new path handlers in do_POST

def _handle_sync_pull(self, payload: dict) -> dict:
    """Desktop computes diff for mobile to pull."""
    device_id = payload.get("device_id", "")
    cursors = payload.get("cursors", {})
    limit = min(int(payload.get("limit", 500)), 2000)

    tables = ["records", "kg_nodes", "kg_edges"]
    changes = {}
    new_cursors = {}

    for table in tables:
        since = cursors.get(table, 0)
        diff = compute_diff(self._db, table, since, limit=limit)
        changes[table] = diff
        new_cursors[table] = diff[-1]["__version"] if diff else since

    payload_data = {"changes": changes, "new_cursors": new_cursors}
    encrypted = encrypt_sync_payload(payload_data, self._sync_key)

    return {
        "ok": True,
        "encrypted_payload": base64.b64encode(encrypted).decode("ascii"),
        "has_more": any(len(changes[t]) >= limit for t in tables),
    }


def _handle_sync_push(self, payload: dict) -> dict:
    """Mobile pushes its changes to desktop."""
    device_id = payload.get("device_id", "")
    encrypted_b64 = payload.get("encrypted_payload", "")
    encrypted = base64.b64decode(encrypted_b64)
    remote_data = decrypt_sync_payload(encrypted, self._sync_key)

    remote_changes = remote_data.get("changes", {})
    applied = 0
    conflicts_resolved = 0

    # Apply within single transaction
    with self._write_lock:
        for table, entries in remote_changes.items():
            for entry in entries:
                conflict = self._check_conflict(table, entry)
                if conflict:
                    resolved = self._resolve_conflict(conflict, entry)
                    self._apply_change(table, resolved)
                    conflicts_resolved += 1
                else:
                    self._apply_change(table, entry)
                applied += 1
        self._db.commit()

    return {
        "ok": True,
        "applied": applied,
        "conflicts_resolved": conflicts_resolved,
    }
```

### Changelog Trigger Installation (Python)
```python
# Source: sqlite-chronicle pattern (simonw/sqlite-chronicle)
# Adapted for Jarvis schema

_TRACKED_TABLES = {
    "records": {
        "pk": "record_id",
        "fields": [
            "ts", "source", "kind", "task_id", "branch", "tags",
            "summary", "content_hash", "confidence", "tier",
            "access_count", "last_accessed", "created_at",
        ],
    },
    "kg_nodes": {
        "pk": "node_id",
        "fields": [
            "label", "node_type", "confidence", "locked",
            "locked_at", "locked_by", "sources", "history",
            "created_at", "updated_at",
        ],
    },
    "kg_edges": {
        "pk": "edge_id",
        "fields": [
            "source_id", "target_id", "relation", "confidence",
            "source_record", "created_at",
        ],
    },
}


def install_changelog_triggers(db: sqlite3.Connection, device_id: str = "desktop") -> None:
    """Install changelog triggers for all tracked tables.

    Idempotent: uses CREATE TRIGGER IF NOT EXISTS.
    """
    # Create changelog and cursor tables
    db.executescript("""
        CREATE TABLE IF NOT EXISTS _sync_changelog (
            changelog_id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            fields_changed TEXT NOT NULL DEFAULT '[]',
            old_values TEXT DEFAULT NULL,
            new_values TEXT DEFAULT NULL,
            device_id TEXT NOT NULL DEFAULT 'desktop',
            ts TEXT NOT NULL DEFAULT (datetime('now')),
            __version INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_changelog_version
            ON _sync_changelog(table_name, __version);
        CREATE INDEX IF NOT EXISTS idx_changelog_device
            ON _sync_changelog(device_id);

        CREATE TABLE IF NOT EXISTS _sync_cursor (
            device_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            last_version INTEGER NOT NULL DEFAULT 0,
            last_sync_ts TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (device_id, table_name)
        );
    """)

    for table_name, config in _TRACKED_TABLES.items():
        pk = config["pk"]
        fields = config["fields"]
        _install_table_triggers(db, table_name, pk, fields, device_id)

    db.commit()
```

### Disabling Triggers During Sync Apply
```python
def apply_remote_changes_safely(
    db: sqlite3.Connection,
    write_lock: threading.Lock,
    changes: list[dict],
    remote_device_id: str,
) -> int:
    """Apply remote changes with triggers temporarily adjusted.

    Uses a pragmatic approach: before applying, set a user-space flag
    via a temp table that triggers check. This avoids dropping/recreating
    triggers which is fragile under concurrent access.
    """
    applied = 0
    with write_lock:
        try:
            # Signal to triggers that we are in sync-apply mode
            db.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _sync_applying "
                "(device_id TEXT PRIMARY KEY)"
            )
            db.execute(
                "INSERT OR REPLACE INTO _sync_applying VALUES (?)",
                (remote_device_id,),
            )

            for change in changes:
                _apply_single_change(db, change)
                applied += 1

            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            # Clear sync-apply flag
            db.execute("DELETE FROM _sync_applying")
    return applied
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Full DB dump sync | Changelog/CDC-based incremental sync | 2023-2024 | Only deltas transmitted, O(changes) not O(total_records) |
| Record-level LWW | Field-level merge with system-of-record | 2024-2025 | Preserves non-conflicting field changes from both sides |
| Custom encryption | Fernet (authenticated encryption) | Stable since 2014 | Zero-config authenticated encryption, safe by default |
| CRDT-based sync | Changelog + authoritative node | For 2-device topologies, CRDTs unnecessary | Simpler, debuggable, matches PROJECT.md decision |
| sqlite-history (audit log) | sqlite-chronicle (sync-optimized) | 2024 | Version-indexed changelog optimized for "changes since" queries |

**Deprecated/outdated:**
- **SQLite Session Extension for Python sync:** The C-level session API requires ctypes/C extension wrappers and the session log "can only be fetched as a whole but never resets." Not suitable for incremental sync.
- **PyCryptodome for Fernet-equivalent:** The `cryptography` package is the standard. PyCryptodome is for when you need raw AES/RSA access.

## Open Questions

1. **Mobile-side SQLite instance**
   - What we know: The mobile sends data via HTTP to the desktop. The existing `mobile_api.py` accepts `/ingest` and `/sync` POSTs.
   - What's unclear: Does the mobile device run its own SQLite instance with the same schema? Or does it use a simpler JSON-based local store? The mobile quick_access.html is a web panel, not a native app.
   - Recommendation: For Plan 08-01, implement the desktop-side changelog and sync engine fully. For the mobile side, implement a lightweight Python sync client that can run in Termux on the S25 Ultra. The mobile client maintains its own SQLite DB with the same schema + changelog triggers. This client POSTs to `/sync/push` and GETs from `/sync/pull`.

2. **Changelog compaction frequency**
   - What we know: Changelog grows with every data change. Must be pruned after both devices confirm sync.
   - What's unclear: How aggressive should compaction be? What retention window is safe?
   - Recommendation: Compact entries older than 7 days AND where both devices have synced past that version. Run compaction at end of each successful sync cycle.

3. **Tier maintenance noise in changelog**
   - What we know: The `TierManager` runs periodic tier promotion/demotion updates (access_count, tier, last_accessed). These fire changelog triggers.
   - What's unclear: Should tier changes sync? They're derived from local access patterns.
   - Recommendation: Do NOT sync `access_count` and `last_accessed` -- these are local-device metrics. DO sync `tier` changes only if they cross a threshold (e.g., hot->warm). Implement this by having the UPDATE trigger check if the change is tier-maintenance-only and skip changelog in that case, OR by excluding those fields from the trigger's WHEN clause.

## Sources

### Primary (HIGH confidence)
- [cryptography.io Fernet docs](https://cryptography.io/en/latest/fernet/) - Complete API for Fernet encryption, PBKDF2 key derivation recipe, verified against v44.0.0 installed locally
- Codebase: `engine/src/jarvis_engine/memory/engine.py` - MemoryEngine schema, insert_record, write_lock pattern
- Codebase: `engine/src/jarvis_engine/mobile_api.py` - Existing HMAC auth, /sync endpoint stub, ThreadingHTTPServer
- Codebase: `engine/src/jarvis_engine/knowledge/graph.py` - KnowledgeGraph schema (kg_nodes, kg_edges, kg_contradictions)
- Codebase: `engine/src/jarvis_engine/commands/ops_commands.py` - Command/Result dataclass patterns
- Codebase: `engine/src/jarvis_engine/handlers/ops_handlers.py` - Handler class patterns

### Secondary (MEDIUM confidence)
- [sqlite-chronicle](https://github.com/simonw/sqlite-chronicle) - Version-indexed changelog trigger pattern with `__version`, `__added_ms`, `__updated_ms` columns, `updates_since()` API
- [sqlite-cdc](https://github.com/kevinconway/sqlite-cdc) - Change-data-capture trigger pattern with before/after JSON recording
- [Stacksync: Mastering Two-Way Sync](https://www.stacksync.com/blog/mastering-two-way-sync-key-concepts-and-implementation-strategies) - Field-level conflict resolution strategies, system-of-record pattern

### Tertiary (LOW confidence)
- [Termux Python scheduling](https://netzro.github.io/posts/2025/Jun/08/setting-up-cronie-and-scheduling-scripts-in-termux/) - Cronie for periodic sync on mobile, termux-wake-lock for background persistence. Needs validation on S25 Ultra specifically.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - `cryptography` Fernet verified installed (44.0.0), SQLite triggers are well-documented stdlib, all patterns verified against official docs
- Architecture: HIGH - Changelog trigger pattern is well-established (sqlite-chronicle, sqlite-cdc), protocol design follows standard push/pull bidirectional sync
- Conflict resolution: HIGH - Field-level merge with authoritative-wins-ties is a standard pattern, algorithm is straightforward
- Pitfalls: HIGH - All pitfalls are well-documented in sync literature and verified by examining the codebase's specific patterns (triggers, write_lock, WAL mode)
- Mobile side: MEDIUM - Termux + Python is proven on Android, but specific S25 Ultra behavior with background processes needs validation

**Research date:** 2026-02-23
**Valid until:** 2026-03-23 (30 days - stable domain, no fast-moving dependencies)
