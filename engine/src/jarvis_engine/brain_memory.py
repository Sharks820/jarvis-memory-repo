from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from jarvis_engine._compat import UTC
from jarvis_engine._shared import now_iso as _now_iso
from pathlib import Path
from typing import Any

from jarvis_engine._shared import atomic_write_json as _atomic_write_json
from jarvis_engine._shared import safe_float as _safe_float
from jarvis_engine._shared import sha256_hex, sha256_short
from jarvis_engine._constants import recency_weight as _recency_weight_core

logger = logging.getLogger(__name__)

# NOTE: This is a threading.RLock (reentrant), which only serializes access
# within a single process.  Cross-process safety (e.g. daemon + CLI both
# writing at the same time) is NOT covered here.  For the new MemoryEngine/
# SQLite path, WAL mode handles concurrent access.  This lock protects only
# the legacy JSONL path.  RLock is used because brain_status() calls
# brain_regression_report() while already holding the lock.
_brain_io_lock = threading.RLock()

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")

BRANCH_RULES: dict[str, tuple[str, ...]] = {
    "ops": ("calendar", "email", "bill", "subscription", "schedule", "meeting", "brief"),
    "coding": ("code", "python", "bug", "test", "refactor", "api", "deploy", "build"),
    "health": ("med", "prescription", "doctor", "health", "pharmacy", "dose"),
    "finance": ("budget", "bank", "invoice", "payment", "expense", "finance"),
    "security": ("auth", "owner", "password", "safe", "security", "guard", "trusted"),
    "learning": ("learn", "study", "research", "mission", "knowledge", "read"),
    "family": ("kid", "family", "school", "spouse", "home"),
    "communications": ("call", "sms", "message", "text", "spam"),
    "gaming": ("game", "gaming", "steam", "fps", "fortnite"),
}


@dataclass
class BrainRecord:
    record_id: str
    ts: str
    source: str
    kind: str
    task_id: str
    branch: str
    tags: list[str]
    summary: str
    confidence: float
    content_hash: str


_to_float = _safe_float


def _brain_dir(root: Path) -> Path:
    return root / ".planning" / "brain"


def _records_path(root: Path) -> Path:
    return _brain_dir(root) / "records.jsonl"


def _index_path(root: Path) -> Path:
    return _brain_dir(root) / "index.json"


def _facts_path(root: Path) -> Path:
    return _brain_dir(root) / "facts.json"


def _summaries_path(root: Path) -> Path:
    return _brain_dir(root) / "summaries.jsonl"


def _tokenize(value: str) -> list[str]:
    normalized = value.replace("_", " ")
    # Filter out pure-numeric tokens (e.g. "42") which are meaningless for search
    return [
        t
        for m in TOKEN_RE.finditer(normalized)
        if not (t := m.group(0).lower()).isdigit()
    ]


def _pick_branch(tokens: list[str]) -> str:
    if not tokens:
        return "general"
    scores: dict[str, int] = {k: 0 for k in BRANCH_RULES}
    for token in tokens:
        for branch, words in BRANCH_RULES.items():
            if token in words or any(token.startswith(word) for word in words):
                scores[branch] += 1
    winner = max(scores.items(), key=lambda item: item[1])
    return winner[0] if winner[1] > 0 else "general"


def _summarize(text: str, max_len: int = 280) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    if len(one_line) <= max_len:
        return one_line
    _SUFFIX = " ...(trimmed)"
    return one_line[: max_len - len(_SUFFIX)].rstrip() + _SUFFIX


def _load_records(root: Path, limit: int = 1500) -> list[dict[str, Any]]:
    path = _records_path(root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping corrupted record line: %s", exc)
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
    except OSError as exc:
        logger.warning("Failed to read records: %s", exc)
        return []
    return rows[-limit:]


def _load_index(root: Path) -> dict[str, Any]:
    from jarvis_engine._shared import load_json_file

    path = _index_path(root)
    default: dict[str, Any] = {"branches": {}, "hash_to_record_id": {}, "updated_utc": ""}
    raw = load_json_file(path, None, expected_type=dict)
    if raw is None:
        return default
    raw.setdefault("branches", {})
    raw.setdefault("hash_to_record_id", {})
    return raw


def _save_index(root: Path, payload: dict[str, Any]) -> None:
    payload["updated_utc"] = _now_iso()
    _atomic_write_json(_index_path(root), payload)


def _load_facts(root: Path) -> dict[str, Any]:
    from jarvis_engine._shared import load_json_file

    path = _facts_path(root)
    default: dict[str, Any] = {"facts": {}, "conflicts": []}
    raw = load_json_file(path, None, expected_type=dict)
    if raw is None:
        return default
    raw.setdefault("facts", {})
    raw.setdefault("conflicts", [])
    return raw


def _save_facts(root: Path, payload: dict[str, Any]) -> None:
    payload["updated_utc"] = _now_iso()
    _atomic_write_json(_facts_path(root), payload)


def _extract_fact_candidates(text: str, branch: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    out: list[dict[str, Any]] = []

    def add(key: str, value: str, conf: float) -> None:
        out.append({"key": key, "value": value, "confidence": max(0.0, min(1.0, conf))})

    if "safe mode" in lowered:
        if any(x in lowered for x in ["enable", "on", "start"]):
            add("runtime.safe_mode", "enabled", 0.84)
        if any(x in lowered for x in ["disable", "off", "stop"]):
            add("runtime.safe_mode", "disabled", 0.84)
    if "gaming mode" in lowered:
        if any(x in lowered for x in ["enable", "on", "start"]):
            add("runtime.gaming_mode", "enabled", 0.83)
        if any(x in lowered for x in ["disable", "off", "stop"]):
            add("runtime.gaming_mode", "disabled", 0.83)
        if "auto" in lowered:
            if any(x in lowered for x in ["enable", "on", "start"]):
                add("runtime.gaming_mode_auto", "enabled", 0.8)
            if any(x in lowered for x in ["disable", "off", "stop"]):
                add("runtime.gaming_mode_auto", "disabled", 0.8)
    if ("pause" in lowered and any(x in lowered for x in ["daemon", "autopilot", "jarvis"])):
        add("runtime.daemon_paused", "true", 0.82)
    if any(x in lowered for x in ["resume daemon", "resume autopilot", "resume jarvis"]):
        add("runtime.daemon_paused", "false", 0.82)
    if ("spam" in lowered and "call" in lowered and any(x in lowered for x in ["block", "guard", "stop"])):
        add("phone.spam_guard", "enabled", 0.77)
    if "owner guard" in lowered:
        if any(x in lowered for x in ["enable", "on"]):
            add("security.owner_guard", "enabled", 0.88)
        if any(x in lowered for x in ["disable", "off"]):
            add("security.owner_guard", "disabled", 0.88)
    if "organize" in lowered and any(x in lowered for x in ["day", "today", "schedule"]):
        add("ops.daily_autopilot", "preferred", 0.7)

    if not out and branch != "general":
        add(f"branch.last_focus.{branch}", "active", 0.55)

    return out[:8]


def _update_fact_store(
    root: Path,
    *,
    record_id: str,
    ts: str,
    branch: str,
    summary: str,
    base_confidence: float,
) -> None:
    state = _load_facts(root)
    facts_raw = state.get("facts", {})
    if not isinstance(facts_raw, dict):
        facts_raw = {}
    conflicts_raw = state.get("conflicts", [])
    if not isinstance(conflicts_raw, list):
        conflicts_raw = []

    candidates = _extract_fact_candidates(summary, branch)
    for cand in candidates:
        key = str(cand.get("key", "")).strip()
        value = str(cand.get("value", "")).strip()
        cand_conf = _to_float(cand.get("confidence", base_confidence), base_confidence)
        if not key or not value:
            continue

        current = facts_raw.get(key)
        if not isinstance(current, dict):
            facts_raw[key] = {
                "value": value,
                "confidence": cand_conf,
                "updated_utc": ts,
                "sources": [record_id],
                "history": [],
            }
            continue

        current_value = str(current.get("value", ""))
        current_conf = _to_float(current.get("confidence", 0.0), 0.0)
        sources = current.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        history = current.get("history", [])
        if not isinstance(history, list):
            history = []

        if current_value == value:
            current["confidence"] = max(current_conf, cand_conf)
            if record_id not in sources:
                sources.append(record_id)
            current["sources"] = sources[-50:]
            current["updated_utc"] = ts
            facts_raw[key] = current
            continue

        promote = cand_conf >= (current_conf + 0.05)
        conflicts_raw.append(
            {
                "key": key,
                "old_value": current_value,
                "new_value": value,
                "old_confidence": current_conf,
                "new_confidence": cand_conf,
                "record_id": record_id,
                "ts": ts,
                "resolved": promote,
            }
        )
        if promote:
            history.append(
                {
                    "value": current_value,
                    "confidence": current_conf,
                    "updated_utc": str(current.get("updated_utc", "")),
                }
            )
            current["value"] = value
            current["confidence"] = cand_conf
            if record_id not in sources:
                sources.append(record_id)
            current["sources"] = sources[-50:]
            current["history"] = history[-100:]
            current["updated_utc"] = ts
            facts_raw[key] = current

    state["facts"] = facts_raw
    state["conflicts"] = conflicts_raw[-400:]
    _save_facts(root, state)


def ingest_brain_record(
    root: Path,
    *,
    source: str,
    kind: str,
    task_id: str,
    content: str,
    tags: list[str] | None = None,
    confidence: float = 0.72,
) -> BrainRecord:
    cleaned = re.sub(r"\s+", " ", content).strip()[:4000]
    if not cleaned:
        raise ValueError("Empty content")
    content_hash = sha256_hex(cleaned.lower())

    with _brain_io_lock:
        index = _load_index(root)
        known = index.get("hash_to_record_id", {})
        if isinstance(known, dict) and content_hash in known:
            existing_id = str(known[content_hash])
            return BrainRecord(
                record_id=existing_id,
                ts=_now_iso(),
                source=source,
                kind=kind,
                task_id=task_id,
                branch="deduped",
                tags=[],
                summary="deduped",
                confidence=confidence,
                content_hash=content_hash,
            )

        tokens = _tokenize(cleaned)
        branch = _pick_branch(tokens)
        summary = _summarize(cleaned)
        unique_tags = sorted({t.lower() for t in (tags or []) if t.strip()})[:10]
        ts = _now_iso()
        # Exclude timestamp from hash material so identical content ingested at
        # different times produces the same record_id (cross-temporal dedup).
        material = f"{source}|{kind}|{task_id}|{content_hash}".encode("utf-8")
        record_id = sha256_short(material)

        record = BrainRecord(
            record_id=record_id,
            ts=ts,
            source=source,
            kind=kind,
            task_id=task_id[:128],
            branch=branch,
            tags=unique_tags,
            summary=summary,
            confidence=max(0.0, min(1.0, confidence)),
            content_hash=content_hash,
        )

        records_path = _records_path(root)
        records_path.parent.mkdir(parents=True, exist_ok=True)
        with records_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

        branches = index.get("branches", {})
        if not isinstance(branches, dict):
            branches = {}
        branch_state = branches.get(branch, {})
        if not isinstance(branch_state, dict):
            branch_state = {}
        ids = branch_state.get("record_ids", [])
        if not isinstance(ids, list):
            ids = []
        ids.append(record_id)
        branch_state["record_ids"] = ids[-500:]
        branch_state["count"] = int(branch_state.get("count", 0)) + 1
        branch_state["last_ts"] = record.ts
        branch_state["last_summary"] = summary
        branches[branch] = branch_state

        index["branches"] = branches
        hash_map = index.get("hash_to_record_id", {})
        if not isinstance(hash_map, dict):
            hash_map = {}
        hash_map[content_hash] = record_id
        if len(hash_map) > 6000:
            recent = _load_records(root, limit=1200)
            keep = {str(item.get("content_hash", "")): str(item.get("record_id", "")) for item in recent}
            hash_map = {k: v for k, v in keep.items() if k and v}
        index["hash_to_record_id"] = hash_map
        _save_index(root, index)

        _update_fact_store(
            root,
            record_id=record_id,
            ts=ts,
            branch=branch,
            summary=summary,
            base_confidence=record.confidence,
        )
    return record


def _recency_weight(ts_text: str) -> float:
    """Compute exponential recency decay (96-hour half-life).

    Returns 0.3 for empty or unparseable timestamps (so context packets
    always include some recency contribution even for undated records).
    """
    return _recency_weight_core(ts_text, default=0.3, decay_hours=96.0)


def build_context_packet(root: Path, *, query: str, max_items: int = 10, max_chars: int = 2400) -> dict[str, Any]:
    with _brain_io_lock:
        rows = _load_records(root, limit=200)
        facts_state = _load_facts(root)

    query_tokens = set(_tokenize(query))

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        summary = str(row.get("summary", ""))
        tokens = set(_tokenize(summary))
        overlap = len(query_tokens & tokens)
        conf = _to_float(row.get("confidence", 0.6), 0.6)
        recency = _recency_weight(str(row.get("ts", "")))
        source_bonus = 0.08 if str(row.get("source", "")) == "task_outcome" else 0.0
        score = (overlap * 1.2) + conf + recency + source_bonus
        if score > 0.45:
            scored.append((score, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    selected: list[dict[str, Any]] = []
    used_branches: dict[str, int] = {}
    total_chars = 0

    for score, row in scored:
        branch = str(row.get("branch", "general"))
        if used_branches.get(branch, 0) >= 3:
            continue
        summary = str(row.get("summary", "")).strip()
        if not summary:
            continue
        next_len = total_chars + len(summary)
        if next_len > max_chars:
            continue
        selected.append(
            {
                "record_id": str(row.get("record_id", "")),
                "branch": branch,
                "summary": summary,
                "source": str(row.get("source", "")),
                "kind": str(row.get("kind", "")),
                "ts": str(row.get("ts", "")),
                "score": round(score, 4),
            }
        )
        used_branches[branch] = used_branches.get(branch, 0) + 1
        total_chars = next_len
        if len(selected) >= max_items:
            break

    facts_raw = facts_state.get("facts", {})
    canonical_facts: list[dict[str, Any]] = []
    if isinstance(facts_raw, dict):
        for key, value in facts_raw.items():
            if not isinstance(value, dict):
                continue
            fact_tokens = set(_tokenize(key + " " + str(value.get("value", ""))))
            overlap = len(query_tokens & fact_tokens)
            if overlap <= 0:
                continue
            canonical_facts.append(
                {
                    "key": key,
                    "value": str(value.get("value", "")),
                    "confidence": _to_float(value.get("confidence", 0.0), 0.0),
                    "updated_utc": str(value.get("updated_utc", "")),
                    "overlap": overlap,
                }
            )
    canonical_facts.sort(key=lambda item: (_to_float(item.get("confidence", 0.0), 0.0), int(item.get("overlap", 0))), reverse=True)

    return {
        "query": query,
        "selected": selected,
        "selected_count": len(selected),
        "canonical_facts": canonical_facts[:8],
        "max_items": max_items,
        "max_chars": max_chars,
        "total_records_scanned": len(rows),
    }


def brain_compact(root: Path, *, keep_recent: int = 1800) -> dict[str, Any]:
    with _brain_io_lock:
        return _brain_compact_locked(root, keep_recent=keep_recent)


def _brain_compact_locked(root: Path, *, keep_recent: int = 1800) -> dict[str, Any]:
    records = _load_records(root, limit=200000)
    total = len(records)
    if total <= keep_recent:
        return {
            "compacted": False,
            "reason": "below_threshold",
            "total_records": total,
            "kept_records": total,
        }

    cut = max(0, total - keep_recent)
    old_records = records[:cut]
    recent_records = records[cut:]

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in old_records:
        branch = str(row.get("branch", "general"))
        ts = str(row.get("ts", ""))
        month = ts[:7] if len(ts) >= 7 else "unknown"
        grouped[(branch, month)].append(str(row.get("summary", "")))

    summaries_path = _summaries_path(root)
    summaries_path.parent.mkdir(parents=True, exist_ok=True)
    with summaries_path.open("a", encoding="utf-8") as f:
        for (branch, month), summaries in grouped.items():
            sample = [s for s in summaries if s][:3]
            payload = {
                "ts": _now_iso(),
                "branch": branch,
                "month": month,
                "count": len(summaries),
                "highlights": sample,
            }
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")

    records_path = _records_path(root)
    tmp = records_path.with_suffix(records_path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for row in recent_records:
                f.write(json.dumps(row, ensure_ascii=True) + "\n")
        os.replace(tmp, records_path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as exc:
            logger.debug("Failed to clean up temp file %s: %s", tmp, exc)

    branch_state: dict[str, dict[str, Any]] = {}
    hash_map: dict[str, str] = {}
    for row in recent_records:
        branch = str(row.get("branch", "general"))
        rec_id = str(row.get("record_id", ""))
        content_hash = str(row.get("content_hash", ""))
        if content_hash and rec_id:
            hash_map[content_hash] = rec_id

        state = branch_state.get(branch, {"record_ids": [], "count": 0, "last_ts": "", "last_summary": ""})
        ids = state.get("record_ids", [])
        if not isinstance(ids, list):
            ids = []
        ids.append(rec_id)
        state["record_ids"] = ids[-500:]
        state["count"] = int(state.get("count", 0)) + 1
        state["last_ts"] = str(row.get("ts", ""))
        state["last_summary"] = str(row.get("summary", ""))
        branch_state[branch] = state

    _save_index(
        root,
        {
            "branches": branch_state,
            "hash_to_record_id": hash_map,
        },
    )

    return {
        "compacted": True,
        "total_records": total,
        "compacted_records": len(old_records),
        "kept_records": len(recent_records),
        "summary_groups": len(grouped),
        "summaries_path": str(summaries_path),
    }


def brain_regression_report(root: Path) -> dict[str, Any]:
    with _brain_io_lock:
        records = _load_records(root, limit=200000)
        facts_state = _load_facts(root)

    total = len(records)
    unique_hashes = len({str(r.get("content_hash", "")) for r in records if str(r.get("content_hash", ""))})
    duplicate_ratio = 0.0
    if total > 0:
        duplicate_ratio = max(0.0, min(1.0, 1.0 - (unique_hashes / total)))

    branches = [str(r.get("branch", "general")) for r in records]
    counts = Counter(branches)
    entropy = 0.0
    if total > 0:
        for count in counts.values():
            p = count / total
            entropy -= p * math.log2(max(p, 1e-9))

    conflicts = facts_state.get("conflicts", [])
    if not isinstance(conflicts, list):
        conflicts = []
    unresolved = 0
    for item in conflicts:
        if isinstance(item, dict) and not bool(item.get("resolved", False)):
            unresolved += 1

    status = "pass"
    if unresolved > 20:
        status = "warn"
    if unresolved > 60 or duplicate_ratio > 0.85:
        status = "fail"

    return {
        "status": status,
        "total_records": total,
        "unique_hashes": unique_hashes,
        "duplicate_ratio": round(duplicate_ratio, 4),
        "branch_entropy": round(entropy, 4),
        "branch_count": len(counts),
        "unresolved_conflicts": unresolved,
        "conflict_total": len(conflicts),
        "generated_utc": _now_iso(),
    }


def brain_status(root: Path) -> dict[str, Any]:
    # Hold the lock for the entire operation so that the index/facts snapshot
    # and the regression report are computed from the same data.  The lock is
    # an RLock (reentrant), so brain_regression_report() can safely re-acquire
    # it within this same thread.
    with _brain_io_lock:
        index = _load_index(root)
        facts_state = _load_facts(root)

        branches_raw = index.get("branches", {})
        branches: list[dict[str, Any]] = []
        if isinstance(branches_raw, dict):
            for name, state in branches_raw.items():
                if not isinstance(state, dict):
                    continue
                branches.append(
                    {
                        "branch": str(name),
                        "count": int(state.get("count", 0)),
                        "last_ts": str(state.get("last_ts", "")),
                        "last_summary": str(state.get("last_summary", "")),
                    }
                )
        branches.sort(key=lambda item: item["count"], reverse=True)

        facts_raw = facts_state.get("facts", {})
        fact_count = len(facts_raw) if isinstance(facts_raw, dict) else 0

        report = brain_regression_report(root)

    return {
        "updated_utc": str(index.get("updated_utc", "")),
        "branch_count": len(branches),
        "fact_count": fact_count,
        "regression": report,
        "branches": branches,
    }
