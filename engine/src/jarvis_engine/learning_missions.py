from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from jarvis_engine._compat import UTC
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jarvis_engine.web_fetch import (
    fetch_page_text as _fetch_page_text,
    search_web as _search_web,
)

# File-level lock for missions.json to prevent TOCTOU race conditions
# between concurrent daemon auto-generation, mobile API creates, and mission runs.
_MISSIONS_LOCK = threading.Lock()

logger = logging.getLogger(__name__)

MISSION_DEFAULT_SOURCES = ["google", "reddit", "official_docs"]
_PAGE_CACHE: dict[tuple[str, int], tuple[float, str]] = {}
_PAGE_CACHE_LOCK = threading.Lock()
_PAGE_CACHE_TTL_SECONDS = 900.0
_PAGE_CACHE_MAX_BYTES = 50_000_000  # 50 MB soft cap
_page_cache_bytes = 0
from jarvis_engine.web_research import STOPWORDS


def _missions_path(root: Path) -> Path:
    return root / ".planning" / "missions.json"


def _reports_dir(root: Path) -> Path:
    return root / ".planning" / "missions"


def load_missions(root: Path) -> list[dict[str, Any]]:
    path = _missions_path(root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _save_missions(root: Path, missions: list[dict[str, Any]]) -> None:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    _atomic_write_json(_missions_path(root), missions, secure=False)


def create_learning_mission(
    root: Path,
    *,
    topic: str,
    objective: str,
    sources: list[str] | None = None,
    origin: str = "desktop-manual",
) -> dict[str, Any]:
    cleaned_topic = topic.strip()
    if not cleaned_topic:
        raise ValueError("topic is required")
    mission_id = f"m-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
    mission = {
        "mission_id": mission_id,
        "topic": cleaned_topic[:200],
        "objective": objective.strip()[:400],
        "sources": sources or list(MISSION_DEFAULT_SOURCES),
        "status": "pending",
        "origin": origin,
        "created_utc": datetime.now(UTC).isoformat(),
        "updated_utc": datetime.now(UTC).isoformat(),
        "last_report_path": "",
        "verified_findings": 0,
    }
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        missions.append(mission)
        _save_missions(root, missions)
    return mission


def _mission_queries(topic: str, sources: list[str]) -> list[str]:
    queries = [topic, f"{topic} tutorial", f"{topic} best practices"]
    lowered = {s.lower().strip() for s in sources}
    if "reddit" in lowered:
        queries.append(f"site:reddit.com {topic}")
    if "google" in lowered:
        queries.append(f"{topic} guide")
    if "official_docs" in lowered:
        queries.append(f"{topic} official documentation")
    if "wikipedia" in lowered:
        queries.append(f"site:en.wikipedia.org {topic}")
        queries.append(f"{topic} explained")
    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))


def _fetch_page_cached(url: str, *, max_bytes: int) -> str:
    global _page_cache_bytes
    key = (url.strip(), max(1, int(max_bytes)))
    now = time.time()
    with _PAGE_CACHE_LOCK:
        cached = _PAGE_CACHE.get(key)
        if cached is not None:
            ts, value = cached
            if now - ts <= _PAGE_CACHE_TTL_SECONDS:
                return value
            _page_cache_bytes -= len(value.encode("utf-8"))
            _PAGE_CACHE.pop(key, None)
    value = _fetch_page_text(url, max_bytes=max_bytes)
    with _PAGE_CACHE_LOCK:
        # Adjust byte counter if key already exists (concurrent fetch or refresh)
        if key in _PAGE_CACHE:
            _old_ts, old_val = _PAGE_CACHE[key]
            _page_cache_bytes -= len(old_val.encode("utf-8"))
        _PAGE_CACHE[key] = (now, value)
        _page_cache_bytes += len(value.encode("utf-8"))
        _page_cache_bytes = max(0, _page_cache_bytes)  # clamp against drift
        if len(_PAGE_CACHE) > 1200 or _page_cache_bytes > _PAGE_CACHE_MAX_BYTES:
            # Keep cache bounded for 24/7 operation.
            stale = sorted(_PAGE_CACHE.items(), key=lambda item: item[1][0])[:200]
            for stale_key, (_stale_ts, stale_val) in stale:
                _page_cache_bytes -= len(stale_val.encode("utf-8"))
                _PAGE_CACHE.pop(stale_key, None)
    return value


def _topic_keywords(topic: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]{4,}", topic.lower())
    return {w for w in words if w not in STOPWORDS}


def _extract_candidates(text: str, *, topic: str, max_candidates: int) -> list[str]:
    keywords = _topic_keywords(topic)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for sentence in sentences:
        s = sentence.strip()
        if len(s) < 30 or len(s) > 320:
            continue
        lowered = s.lower()
        if keywords and not any(k in lowered for k in keywords):
            continue
        out.append(s)
        if len(out) >= max_candidates:
            break
    return out


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]{4,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _verify_candidates(candidates: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Cross-reference candidates across sources to verify factual claims.

    Verification tiers:
      - 2+ domains with 2+ keyword overlap → high confidence (0.55+)
      - Single source with 4+ keywords → low confidence (0.30) — still useful for niche topics
    """
    # Precompute keywords for all candidates to avoid redundant O(n) _keywords() calls
    candidate_keys = [_keywords(c.get("statement", "")) for c in candidates]

    verified: list[dict[str, Any]] = []
    seen_statements: set[str] = set()
    for idx, item in enumerate(candidates):
        statement = item.get("statement", "").strip()
        if not statement:
            continue
        base_domain = item.get("domain", "")
        base_keys = candidate_keys[idx]
        if len(base_keys) < 2:
            continue  # Too few keywords to be meaningful
        support_urls = {item.get("url", "")}
        support_domains = {base_domain}
        for jdx, other in enumerate(candidates):
            if jdx == idx:
                continue
            if other.get("domain", "") == base_domain:
                continue
            overlap = len(base_keys.intersection(candidate_keys[jdx]))
            if overlap >= 2:
                support_urls.add(other.get("url", ""))
                support_domains.add(other.get("domain", ""))
        norm_key = re.sub(r"[^a-z0-9]+", " ", statement.lower()).strip()
        if norm_key in seen_statements:
            continue
        if len(support_domains) >= 2:
            # Cross-source verified — higher confidence
            verified.append(
                {
                    "statement": statement,
                    "source_urls": sorted(u for u in support_urls if u),
                    "source_domains": sorted(d for d in support_domains if d),
                    "confidence": round(min(1.0, 0.55 + 0.15 * len(support_domains)), 2),
                }
            )
            seen_statements.add(norm_key)
        elif len(base_keys) >= 4:
            # Single-source but keyword-rich — accept at low confidence
            # This prevents niche topics from producing zero results
            verified.append(
                {
                    "statement": statement,
                    "source_urls": sorted(u for u in support_urls if u),
                    "source_domains": sorted(d for d in support_domains if d),
                    "confidence": 0.30,
                }
            )
            seen_statements.add(norm_key)

    return verified


def run_learning_mission(
    root: Path,
    *,
    mission_id: str,
    max_search_results: int = 8,
    max_pages: int = 12,
) -> dict[str, Any]:
    # Mark as "running" under lock before starting the long operation.
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        target: dict[str, Any] | None = None
        for item in missions:
            if str(item.get("mission_id", "")) == mission_id:
                target = item
                break
        if target is None:
            raise ValueError(f"mission not found: {mission_id}")
        target["status"] = "running"
        target["updated_utc"] = datetime.now(UTC).isoformat()
        _save_missions(root, missions)
        # Copy fields under lock to avoid referencing the shared dict outside.
        _topic = str(target.get("topic", "")).strip()
        _sources = target.get("sources", MISSION_DEFAULT_SOURCES)

    topic = _topic
    sources = _sources
    if not isinstance(sources, list):
        sources = list(MISSION_DEFAULT_SOURCES)
    queries = _mission_queries(topic, [str(s) for s in sources])

    urls: list[str] = []
    for query in queries:
        urls.extend(_search_web(query, limit=max_search_results))
    urls = list(dict.fromkeys(urls))
    candidate_rows: list[dict[str, str]] = []
    selected: list[tuple[str, str]] = []
    for url in urls[: max(1, max_pages)]:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain:
            continue
        selected.append((url, domain))
    scanned_urls = [url for url, _domain in selected]
    workers = max(1, min(4, len(selected)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_fetch_page_cached, url, max_bytes=220_000): (url, domain)
            for url, domain in selected
        }
        for future in as_completed(future_map):
            url, domain = future_map[future]
            try:
                text = future.result(timeout=30)
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", url, exc)
                text = ""
            if not text:
                continue
            candidates = _extract_candidates(text, topic=topic, max_candidates=8)
            for statement in candidates:
                candidate_rows.append({"statement": statement, "url": url, "domain": domain})

    verified = _verify_candidates(candidate_rows)
    report = {
        "mission_id": mission_id,
        "topic": topic,
        "objective": str(target.get("objective", "")),
        "queries": queries,
        "scanned_urls": scanned_urls,
        "candidate_count": len(candidate_rows),
        "verified_findings": verified,
        "verified_count": len(verified),
        "completed_utc": datetime.now(UTC).isoformat(),
    }
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", mission_id)[:80]
    report_path = _reports_dir(root) / f"{safe_id}.report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    # Re-read under lock to avoid TOCTOU overwrites from concurrent creates/runs.
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        target = None
        for item in missions:
            if str(item.get("mission_id", "")) == mission_id:
                target = item
                break
        if target is None:
            logger.warning("Mission %s disappeared during run — skipping status update", mission_id)
            return report
        # Respect user-initiated cancellation — do not overwrite.
        if target.get("status") == "cancelled":
            logger.info("Mission %s was cancelled during run — preserving cancelled status", mission_id)
            return report
        if verified:
            target["status"] = "completed"
            # Fire proactive alert so the phone gets notified
            try:
                from jarvis_engine.proactive.alert_queue import enqueue_alert
                enqueue_alert(root, {
                    "type": "mission_completed",
                    "title": f"Mission Complete: {target.get('topic', '')}",
                    "body": f"Found {len(verified)} verified findings for '{target.get('topic', '')}'",
                    "group_key": "jarvis_missions",
                    "priority": "important",
                })
            except Exception:
                pass
        else:
            # No verified findings — mark as failed for retry.
            # retries tracks how many times retry_failed_missions re-queued this.
            retries = int(target.get("retries", 0))
            target["status"] = "failed" if retries < 2 else "exhausted"
        target["updated_utc"] = datetime.now(UTC).isoformat()
        target["last_report_path"] = str(report_path)
        target["verified_findings"] = len(verified)
        _save_missions(root, missions)
    return report


# ---------------------------------------------------------------------------
# Cancel a mission
# ---------------------------------------------------------------------------


def cancel_mission(root: Path, *, mission_id: str) -> dict[str, Any]:
    """Cancel a mission by setting its status to 'cancelled'.

    Returns the updated mission dict, or raises ValueError if not found.
    """
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        target: dict[str, Any] | None = None
        for item in missions:
            if str(item.get("mission_id", "")) == mission_id:
                target = item
                break
        if target is None:
            raise ValueError(f"mission not found: {mission_id}")

        current_status = str(target.get("status", ""))
        _NON_CANCELLABLE = ("completed", "cancelled", "exhausted")
        if current_status in _NON_CANCELLABLE:
            raise ValueError(f"cannot cancel mission in '{current_status}' state: {mission_id}")

        target["status"] = "cancelled"
        target["updated_utc"] = datetime.now(UTC).isoformat()
        _save_missions(root, missions)

    # Log activity event for mission state change
    try:
        from jarvis_engine.activity_feed import ActivityCategory, log_activity

        log_activity(
            ActivityCategory.MISSION_STATE_CHANGE,
            f"Mission cancelled: {target.get('topic', '')}",
            {"mission_id": mission_id, "new_status": "cancelled"},
        )
    except Exception:
        pass

    return target


# ---------------------------------------------------------------------------
# Retry failed missions
# ---------------------------------------------------------------------------

def retry_failed_missions(root: Path) -> int:
    """Re-queue failed missions (up to 2 retries) by setting status back to pending.

    Broadens the query set on retry by appending alternate search terms.
    Returns the number of missions re-queued.
    """
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        re_queued = 0
        modified = False
        for mission in missions:
            if str(mission.get("status", "")).lower() != "failed":
                continue
            retries = int(mission.get("retries", 0))
            if retries >= 2:
                mission["status"] = "exhausted"
                mission["updated_utc"] = datetime.now(UTC).isoformat()
                modified = True
                continue
            # Broaden search: add alternative query phrasing
            sources = mission.get("sources", list(MISSION_DEFAULT_SOURCES))
            if not isinstance(sources, list):
                sources = list(MISSION_DEFAULT_SOURCES)
            # Add broadening sources on retry
            if "wikipedia" not in [s.lower() for s in sources]:
                sources.append("wikipedia")
            mission["sources"] = sources
            mission["retries"] = retries + 1
            mission["status"] = "pending"
            mission["updated_utc"] = datetime.now(UTC).isoformat()
            re_queued += 1
            modified = True
        if modified:
            _save_missions(root, missions)
    if re_queued > 0:
        logger.info("Re-queued %d failed mission(s) for retry", re_queued)
    return re_queued


# ---------------------------------------------------------------------------
# Auto-generate missions from knowledge gaps
# ---------------------------------------------------------------------------

_MISSION_OBJECTIVES = [
    "Discover verified facts and deepen knowledge graph coverage",
    "Find authoritative sources and cross-reference claims",
    "Expand understanding with practical examples and best practices",
]


def auto_generate_missions(
    root: Path,
    *,
    max_new: int = 3,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Auto-generate learning missions from conversation history and KG gaps.

    Creates up to `max_new` missions when the pending queue is empty.
    Uses the same 5-layer topic discovery as the auto-harvest system but
    produces structured missions with objectives and source strategies.

    Returns list of newly created mission dicts.
    """
    import sqlite3 as _sqlite3

    missions = load_missions(root)
    pending_count = sum(
        1 for m in missions
        if str(m.get("status", "")).lower() == "pending"
    )
    if pending_count > 0:
        logger.debug("auto_generate_missions: %d pending missions exist, skipping", pending_count)
        return []

    # Collect existing topics to avoid duplicates
    existing_topics = {
        str(m.get("topic", "")).lower().strip()
        for m in missions
        if str(m.get("status", "")).lower() in ("pending", "completed", "running")
    }

    # --- Discover candidate topics using multiple sources ---
    candidates: list[str] = []
    seen_lower: set[str] = set()

    def _add(topic: str) -> bool:
        topic = topic.strip()
        if not topic or len(topic) < 4:
            return False
        tl = topic.lower()
        if tl in seen_lower or tl in existing_topics:
            return False
        if len(topic.split()) < 2 or len(topic.split()) > 6:
            return False
        seen_lower.add(tl)
        candidates.append(topic)
        return len(candidates) >= max_new

    if db_path is None:
        db_path = root / ".planning" / "brain" / "jarvis_memory.db"

    conn = None
    try:
        if db_path.exists():
            conn = _sqlite3.connect(str(db_path), timeout=5)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = _sqlite3.Row

        # Source 1: Recent user conversations (last 14 days)
        if conn is not None:
            try:
                from datetime import timedelta
                cutoff = (datetime.now(UTC) - timedelta(days=14)).isoformat()
                rows = conn.execute(
                    """SELECT summary FROM records
                       WHERE ts >= ? AND source = 'user'
                       ORDER BY ts DESC
                       LIMIT 50""",
                    (cutoff,),
                ).fetchall()
                for row in rows:
                    summary = row["summary"] or ""
                    # Extract multi-word topic phrases
                    words = re.findall(r"[a-zA-Z0-9]{3,}", summary)
                    filtered = [w for w in words if w.lower() not in STOPWORDS]
                    # Build 2-4 word phrases from consecutive words
                    for i in range(len(filtered) - 1):
                        phrase = " ".join(filtered[i:i + min(3, len(filtered) - i)])
                        if _add(phrase):
                            break
                    if len(candidates) >= max_new:
                        break
            except Exception as exc:
                logger.debug("Topic extraction from recent queries failed: %s", exc)

        # Source 2: KG nodes with low edge count (knowledge gaps)
        if conn is not None and len(candidates) < max_new:
            try:
                sparse = conn.execute(
                    """SELECT n.label FROM kg_nodes n
                       LEFT JOIN kg_edges e ON n.node_id = e.source_id
                       WHERE n.confidence >= 0.3
                       GROUP BY n.node_id
                       HAVING COUNT(e.edge_id) BETWEEN 0 AND 1
                       ORDER BY n.updated_at DESC
                       LIMIT 20""",
                ).fetchall()
                for row in sparse:
                    label = row["label"] or ""
                    words = re.findall(r"[a-zA-Z0-9]{3,}", label)
                    filtered = [w for w in words if w.lower() not in STOPWORDS]
                    if len(filtered) >= 2:
                        phrase = " ".join(filtered[:4])
                        if _add(phrase):
                            break
            except Exception as exc:
                logger.debug("KG gap analysis for mission topics failed: %s", exc)

        # Source 3: Strong KG areas that could be deepened
        if conn is not None and len(candidates) < max_new:
            try:
                strong = conn.execute(
                    """SELECT n.label, COUNT(e.edge_id) AS edge_cnt
                       FROM kg_nodes n
                       JOIN kg_edges e ON n.node_id = e.source_id
                       WHERE n.confidence >= 0.5
                       GROUP BY n.node_id
                       HAVING edge_cnt >= 3
                       ORDER BY edge_cnt DESC
                       LIMIT 10""",
                ).fetchall()
                suffixes = ["advanced techniques", "real world applications", "recent developments"]
                for i, row in enumerate(strong):
                    label = row["label"] or ""
                    words = re.findall(r"[a-zA-Z0-9]{3,}", label)
                    filtered = [w for w in words if w.lower() not in STOPWORDS]
                    if filtered:
                        base = " ".join(filtered[:2])
                        phrase = f"{base} {suffixes[i % len(suffixes)]}"
                        if _add(phrase):
                            break
            except Exception as exc:
                logger.debug("KG strength analysis for mission topics failed: %s", exc)

    finally:
        if conn is not None:
            conn.close()

    # --- Create missions from discovered topics ---
    created: list[dict[str, Any]] = []
    for i, topic in enumerate(candidates[:max_new]):
        objective = _MISSION_OBJECTIVES[i % len(_MISSION_OBJECTIVES)]
        try:
            mission = create_learning_mission(
                root,
                topic=topic,
                objective=objective,
                origin="daemon",
            )
            created.append(mission)
            logger.info("Auto-created mission %s: %s", mission["mission_id"], topic)
        except Exception as exc:
            logger.warning("Failed to auto-create mission for topic '%s': %s", topic, exc)

    return created
