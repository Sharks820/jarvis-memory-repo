from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from jarvis_engine._compat import UTC
from jarvis_engine._shared import now_iso as _now_iso
from pathlib import Path
from typing import Any, TypedDict
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


class MissionStep(TypedDict, total=False):
    """A single step in a mission's execution plan."""

    name: str
    description: str
    weight: float
    status: str  # pending | running | completed | failed | skipped
    elapsed_ms: int
    artifacts_produced: int


class MissionRecord(TypedDict):
    """Shape returned by ``create_learning_mission``."""

    mission_id: str
    topic: str
    objective: str
    sources: list[str]
    status: str
    origin: str
    created_utc: str
    updated_utc: str
    last_report_path: str
    verified_findings: int
    progress_pct: int
    status_detail: str
    progress_bar: str


class VerifiedFinding(TypedDict):
    """Shape of a single verified finding in a mission report."""

    statement: str
    source_urls: list[str]
    source_domains: list[str]
    confidence: float


class MissionReport(TypedDict):
    """Shape returned by ``run_learning_mission``."""

    mission_id: str
    topic: str
    objective: str
    queries: list[str]
    scanned_urls: list[str]
    candidate_count: int
    verified_findings: list[VerifiedFinding]
    verified_count: int
    completed_utc: str


def _missions_path(root: Path) -> Path:
    return root / ".planning" / "missions.json"


def _reports_dir(root: Path) -> Path:
    return root / ".planning" / "missions"


def load_missions(root: Path) -> list[dict[str, Any]]:
    from jarvis_engine._shared import load_json_file

    path = _missions_path(root)
    raw = load_json_file(path, None, expected_type=list)
    if raw is None:
        return []
    return [item for item in raw if isinstance(item, dict)]


def _save_missions(root: Path, missions: list[dict[str, Any]]) -> None:
    from jarvis_engine._shared import atomic_write_json as _atomic_write_json

    _atomic_write_json(_missions_path(root), missions, secure=False)


def _progress_bar(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    filled = pct // 10
    return "[" + ("█" * filled) + ("░" * (10 - filled)) + f"] {pct}%"


def _update_mission_progress(
    root: Path,
    mission_id: str,
    *,
    status: str,
    progress_pct: int,
    status_detail: str,
) -> None:
    event_payload: dict[str, Any] | None = None
    cancelled = False
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        for mission in missions:
            if str(mission.get("mission_id", "")) != mission_id:
                continue
            if str(mission.get("status", "")).lower() == "cancelled":
                cancelled = True
                break
            mission["status"] = status
            mission["progress_pct"] = max(0, min(100, int(progress_pct)))
            mission["status_detail"] = status_detail[:180]
            mission["progress_bar"] = _progress_bar(mission["progress_pct"])
            mission["updated_utc"] = _now_iso()
            _save_missions(root, missions)
            event_payload = {
                "topic": str(mission.get("topic", "")),
                "status": status,
                "progress_pct": int(mission["progress_pct"]),
                "status_detail": str(mission["status_detail"]),
            }
            break
    if cancelled or event_payload is None:
        return
    if event_payload is not None:
        _log_mission_activity(
            mission_id=mission_id,
            topic=event_payload["topic"],
            status=event_payload["status"],
            progress_pct=event_payload["progress_pct"],
            step=event_payload["status_detail"],
        )


def _log_mission_activity(
    *,
    mission_id: str,
    topic: str,
    status: str,
    progress_pct: int,
    step: str,
) -> None:
    try:
        from jarvis_engine.activity_feed import ActivityCategory, log_activity

        log_activity(
            ActivityCategory.MISSION_STATE_CHANGE,
            f"Mission {status}: {topic}",
            {
                "mission_id": mission_id,
                "provider": "web_research",
                "step": step[:180],
                "progress_pct": max(0, min(100, int(progress_pct))),
                "correlation_id": f"mission-{mission_id}",
                "status": status,
            },
        )
    except (OSError, ValueError, RuntimeError):
        logger.debug("Mission activity logging failed for %s", mission_id)


def create_learning_mission(
    root: Path,
    *,
    topic: str,
    objective: str,
    sources: list[str] | None = None,
    origin: str = "desktop-manual",
) -> MissionRecord:
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
        "created_utc": _now_iso(),
        "updated_utc": _now_iso(),
        "last_report_path": "",
        "verified_findings": 0,
        "progress_pct": 0,
        "status_detail": "Queued",
        "progress_bar": _progress_bar(0),
    }
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        missions.append(mission)
        _save_missions(root, missions)
    _log_mission_activity(
        mission_id=mission_id,
        topic=str(mission.get("topic", "")),
        status="pending",
        progress_pct=0,
        step="Queued",
    )
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


def _start_mission(
    root: Path, mission_id: str,
) -> tuple[str, str, list[str]]:
    """Mark mission as running and return its (topic, objective, sources) under lock.

    Raises ``ValueError`` if the mission is not found.
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
        target["status"] = "running"
        target["updated_utc"] = _now_iso()
        _save_missions(root, missions)
        topic = str(target.get("topic", "")).strip()
        objective = str(target.get("objective", ""))
        sources = target.get("sources", MISSION_DEFAULT_SOURCES)

    if not isinstance(sources, list):
        sources = list(MISSION_DEFAULT_SOURCES)
    return topic, objective, sources


def _fetch_mission_content(
    topic: str,
    queries: list[str],
    *,
    max_search_results: int,
    max_pages: int,
) -> tuple[list[str], list[tuple[str, str]], list[dict[str, str]]]:
    """Search the web, fetch pages, and extract candidate findings.

    Returns ``(scanned_urls, selected, candidate_rows)``.
    """
    urls: list[str] = []
    for query in queries:
        urls.extend(_search_web(query, limit=max_search_results))
    urls = list(dict.fromkeys(urls))

    selected: list[tuple[str, str]] = []
    for url in urls[: max(1, max_pages)]:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain:
            continue
        selected.append((url, domain))

    scanned_urls = [url for url, _domain in selected]

    candidate_rows: list[dict[str, str]] = []
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
            except (TimeoutError, OSError, ValueError, ConnectionError) as exc:
                logger.warning("Failed to fetch %s: %s", url, exc)
                text = ""
            if not text:
                continue
            candidates = _extract_candidates(text, topic=topic, max_candidates=8)
            for statement in candidates:
                candidate_rows.append({"statement": statement, "url": url, "domain": domain})

    return scanned_urls, selected, candidate_rows


def _finalize_mission(
    root: Path,
    mission_id: str,
    verified: list[dict],
    report: dict,
    report_path: Path,
) -> None:
    """Update mission status under lock and log the final activity event."""
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        target: dict[str, Any] | None = None
        for item in missions:
            if str(item.get("mission_id", "")) == mission_id:
                target = item
                break
        if target is None:
            logger.warning("Mission %s disappeared during run — skipping status update", mission_id)
            return
        # Respect user-initiated cancellation — do not overwrite.
        if target.get("status") == "cancelled":
            logger.info("Mission %s was cancelled during run — preserving cancelled status", mission_id)
            return
        if verified:
            target["status"] = "completed"
            target["progress_pct"] = 100
            target["status_detail"] = "Completed"
            target["progress_bar"] = _progress_bar(100)
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
            except (OSError, ImportError) as exc:
                logger.debug("Mission completion notification failed: %s", exc)
        else:
            retries = int(target.get("retries", 0))
            target["status"] = "failed" if retries < 2 else "exhausted"
            target["progress_pct"] = 100
            target["status_detail"] = "Completed with no verified findings"
            target["progress_bar"] = _progress_bar(100)
        target["updated_utc"] = _now_iso()
        target["last_report_path"] = str(report_path)
        target["verified_findings"] = len(verified)
        final_status = str(target.get("status", "completed"))
        final_progress = int(target.get("progress_pct", 100) or 100)
        final_detail = str(target.get("status_detail", "Completed"))
        final_topic = str(target.get("topic", ""))
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=final_topic,
        status=final_status,
        progress_pct=final_progress,
        step=final_detail,
    )


def run_learning_mission(
    root: Path,
    *,
    mission_id: str,
    max_search_results: int = 8,
    max_pages: int = 12,
) -> MissionReport:
    # Initialize step tracking for this mission
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        for m in missions:
            if str(m.get("mission_id", "")) == mission_id:
                m["steps"] = _init_mission_steps()
                _save_missions(root, missions)
                break

    _t0 = int(time.time() * 1000)
    _update_step(root, mission_id, "init", status="running")
    topic, objective, sources = _start_mission(root, mission_id)
    queries = _mission_queries(topic, [str(s) for s in sources])
    _update_step(root, mission_id, "init", status="completed", elapsed_ms=int(time.time() * 1000) - _t0)

    # Step: search web
    _t1 = int(time.time() * 1000)
    _update_step(root, mission_id, "search_web", status="running")
    _update_mission_progress(
        root, mission_id, status="running", progress_pct=_compute_step_progress(get_mission_steps(root, mission_id)),
        status_detail="Searching the web for sources",
    )

    # Step: fetch pages
    _update_step(root, mission_id, "search_web", status="completed", elapsed_ms=int(time.time() * 1000) - _t1)
    _t2 = int(time.time() * 1000)
    _update_step(root, mission_id, "fetch_pages", status="running")
    scanned_urls, _, candidate_rows = _fetch_mission_content(
        topic, queries,
        max_search_results=max_search_results,
        max_pages=max_pages,
    )
    _update_step(root, mission_id, "fetch_pages", status="completed",
                 elapsed_ms=int(time.time() * 1000) - _t2,
                 artifacts_produced=len(scanned_urls))

    # Step: extract candidates
    _t3 = int(time.time() * 1000)
    _update_step(root, mission_id, "extract_candidates", status="completed",
                 elapsed_ms=int(time.time() * 1000) - _t3,
                 artifacts_produced=len(candidate_rows))

    # Step: verify findings
    _t4 = int(time.time() * 1000)
    _update_step(root, mission_id, "verify_findings", status="running")
    _update_mission_progress(
        root, mission_id, status="running",
        progress_pct=_compute_step_progress(get_mission_steps(root, mission_id)),
        status_detail=f"Verifying {len(candidate_rows)} candidate findings",
    )
    verified = _verify_candidates(candidate_rows)
    _update_step(root, mission_id, "verify_findings", status="completed",
                 elapsed_ms=int(time.time() * 1000) - _t4,
                 artifacts_produced=len(verified))

    # Step: finalize
    _t5 = int(time.time() * 1000)
    _update_step(root, mission_id, "finalize", status="running")

    # Build and persist report
    report = {
        "mission_id": mission_id,
        "topic": topic,
        "objective": objective,
        "queries": queries,
        "scanned_urls": scanned_urls,
        "candidate_count": len(candidate_rows),
        "verified_findings": verified,
        "verified_count": len(verified),
        "completed_utc": _now_iso(),
    }
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", mission_id)[:80]
    report_path = _reports_dir(root) / f"{safe_id}.report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    _update_step(root, mission_id, "finalize", status="completed", elapsed_ms=int(time.time() * 1000) - _t5)
    _update_mission_progress(
        root, mission_id, status="running", progress_pct=100,
        status_detail="Finalizing mission report",
    )
    _finalize_mission(root, mission_id, verified, report, report_path)
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
        target["updated_utc"] = _now_iso()
        target["status_detail"] = "Cancelled"
        target["progress_bar"] = _progress_bar(int(target.get("progress_pct", 0) or 0))
        _save_missions(root, missions)

    # Log activity event for mission state change
    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="cancelled",
        progress_pct=int(target.get("progress_pct", 0) or 0),
        step="Cancelled",
    )

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
                mission["updated_utc"] = _now_iso()
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
            mission["updated_utc"] = _now_iso()
            mission["progress_pct"] = 0
            mission["status_detail"] = "Retry queued"
            mission["progress_bar"] = _progress_bar(0)
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


class _TopicCollector:
    """Accumulates candidate topics while enforcing uniqueness and limits."""

    def __init__(self, max_new: int, existing_topics: set[str]) -> None:
        self.max_new = max_new
        self._existing = existing_topics
        self._seen_lower: set[str] = set()
        self.candidates: list[str] = []

    @property
    def full(self) -> bool:
        return len(self.candidates) >= self.max_new

    def add(self, topic: str) -> bool:
        """Try to add *topic*. Returns ``True`` when the collector is full."""
        topic = topic.strip()
        if not topic or len(topic) < 4:
            return False
        tl = topic.lower()
        if tl in self._seen_lower or tl in self._existing:
            return False
        if len(topic.split()) < 2 or len(topic.split()) > 6:
            return False
        self._seen_lower.add(tl)
        self.candidates.append(topic)
        return self.full


def _gather_topics_from_conversations(
    conn: sqlite3.Connection, collector: _TopicCollector,
) -> None:
    """Source 1: Extract topics from recent user conversations (last 14 days)."""
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
            words = re.findall(r"[a-zA-Z0-9]{3,}", summary)
            filtered = [w for w in words if w.lower() not in STOPWORDS]
            for i in range(len(filtered) - 1):
                phrase = " ".join(filtered[i:i + min(3, len(filtered) - i)])
                if collector.add(phrase):
                    break
            if collector.full:
                break
    except (sqlite3.Error, OSError, ValueError) as exc:
        logger.debug("Topic extraction from recent queries failed: %s", exc)


def _gather_topics_from_kg_gaps(
    conn: sqlite3.Connection, collector: _TopicCollector,
) -> None:
    """Source 2: KG nodes with low edge count (knowledge gaps)."""
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
                if collector.add(phrase):
                    break
    except sqlite3.Error as exc:
        logger.debug("KG gap analysis for mission topics failed: %s", exc)


def _gather_topics_from_kg_strengths(
    conn: sqlite3.Connection, collector: _TopicCollector,
) -> None:
    """Source 3: Strong KG areas that could be deepened."""
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
                if collector.add(phrase):
                    break
    except sqlite3.Error as exc:
        logger.debug("KG strength analysis for mission topics failed: %s", exc)


def _create_missions_from_topics(
    root: Path, candidates: list[str], max_new: int,
) -> list[dict[str, Any]]:
    """Create mission records from a list of candidate topic strings."""
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
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.warning("Failed to auto-create mission for topic '%s': %s", topic, exc)
    return created


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
    missions = load_missions(root)
    pending_count = sum(
        1 for m in missions
        if str(m.get("status", "")).lower() == "pending"
    )
    if pending_count > 0:
        logger.debug("auto_generate_missions: %d pending missions exist, skipping", pending_count)
        return []

    existing_topics = {
        str(m.get("topic", "")).lower().strip()
        for m in missions
        if str(m.get("status", "")).lower() in ("pending", "completed", "running")
    }

    collector = _TopicCollector(max_new, existing_topics)

    if db_path is None:
        from jarvis_engine._constants import memory_db_path as _memory_db_path
        db_path = _memory_db_path(root)

    conn = None
    try:
        if db_path.exists():
            from jarvis_engine._db_pragmas import connect_db
            conn = connect_db(db_path)

        if conn is not None:
            _gather_topics_from_conversations(conn, collector)

        if conn is not None and not collector.full:
            _gather_topics_from_kg_gaps(conn, collector)

        if conn is not None and not collector.full:
            _gather_topics_from_kg_strengths(conn, collector)
    finally:
        if conn is not None:
            conn.close()

    return _create_missions_from_topics(root, collector.candidates, max_new)


# ---------------------------------------------------------------------------
# Step-driven progress model (Task D)
# ---------------------------------------------------------------------------

_MISSION_STEPS: list[MissionStep] = [
    {"name": "init", "description": "Initializing mission", "weight": 0.5, "status": "pending", "elapsed_ms": 0, "artifacts_produced": 0},
    {"name": "search_web", "description": "Searching the web for sources", "weight": 2.0, "status": "pending", "elapsed_ms": 0, "artifacts_produced": 0},
    {"name": "fetch_pages", "description": "Fetching and reading pages", "weight": 3.0, "status": "pending", "elapsed_ms": 0, "artifacts_produced": 0},
    {"name": "extract_candidates", "description": "Extracting candidate findings", "weight": 1.5, "status": "pending", "elapsed_ms": 0, "artifacts_produced": 0},
    {"name": "verify_findings", "description": "Cross-referencing and verifying claims", "weight": 2.0, "status": "pending", "elapsed_ms": 0, "artifacts_produced": 0},
    {"name": "finalize", "description": "Building mission report", "weight": 1.0, "status": "pending", "elapsed_ms": 0, "artifacts_produced": 0},
]


def _init_mission_steps() -> list[dict[str, Any]]:
    """Create a fresh copy of the step template for a mission."""
    return [dict(step) for step in _MISSION_STEPS]


def _compute_step_progress(steps: list[dict[str, Any]]) -> int:
    """Compute progress percentage from step weights."""
    total_weight = sum(float(s.get("weight", 1.0)) for s in steps)
    if total_weight <= 0:
        return 0
    completed_weight = sum(
        float(s.get("weight", 1.0))
        for s in steps
        if s.get("status") in ("completed", "skipped")
    )
    return max(0, min(100, int(completed_weight / total_weight * 100)))


def _update_step(
    root: Path,
    mission_id: str,
    step_name: str,
    *,
    status: str,
    elapsed_ms: int = 0,
    artifacts_produced: int = 0,
) -> None:
    """Update a specific step in a mission's step list and recompute progress."""
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        for mission in missions:
            if str(mission.get("mission_id", "")) != mission_id:
                continue
            if str(mission.get("status", "")).lower() == "cancelled":
                return
            steps = mission.get("steps", [])
            if not isinstance(steps, list):
                steps = _init_mission_steps()
            for step in steps:
                if step.get("name") == step_name:
                    step["status"] = status
                    if elapsed_ms > 0:
                        step["elapsed_ms"] = elapsed_ms
                    if artifacts_produced > 0:
                        step["artifacts_produced"] = artifacts_produced
                    break
            mission["steps"] = steps
            progress = _compute_step_progress(steps)
            mission["progress_pct"] = progress
            mission["progress_bar"] = _progress_bar(progress)
            # Find current running step for status_detail
            running_step = next((s for s in steps if s.get("status") == "running"), None)
            if running_step:
                mission["status_detail"] = str(running_step.get("description", ""))[:180]
            mission["updated_utc"] = _now_iso()
            _save_missions(root, missions)
            break


def get_mission_steps(root: Path, mission_id: str) -> list[dict[str, Any]]:
    """Return the step breakdown for a mission."""
    missions = load_missions(root)
    for mission in missions:
        if str(mission.get("mission_id", "")) == mission_id:
            steps = mission.get("steps", [])
            return steps if isinstance(steps, list) else []
    return []


def get_active_missions(root: Path) -> list[dict[str, Any]]:
    """Return all running and paused missions."""
    missions = load_missions(root)
    return [
        m for m in missions
        if str(m.get("status", "")).lower() in ("running", "paused", "pending")
    ]


def get_now_working_on(root: Path) -> dict[str, Any] | None:
    """Return the currently running mission for the 'now working on' panel, or None."""
    missions = load_missions(root)
    for m in missions:
        if str(m.get("status", "")).lower() == "running":
            steps = m.get("steps", [])
            current_step = ""
            if isinstance(steps, list):
                running = next((s for s in steps if s.get("status") == "running"), None)
                if running:
                    current_step = str(running.get("description", ""))
            created = m.get("created_utc", "")
            elapsed_s = 0
            if created:
                try:
                    created_dt = datetime.fromisoformat(created)
                    elapsed_s = int((datetime.now(UTC) - created_dt).total_seconds())
                except (ValueError, TypeError):
                    pass
            artifacts = 0
            if isinstance(steps, list):
                artifacts = sum(int(s.get("artifacts_produced", 0)) for s in steps)
            return {
                "mission_id": m.get("mission_id", ""),
                "mission_topic": m.get("topic", ""),
                "current_step": current_step,
                "progress_pct": int(m.get("progress_pct", 0)),
                "elapsed_s": elapsed_s,
                "artifacts_so_far": artifacts,
            }
    return None


# ---------------------------------------------------------------------------
# Pause / Resume / Restart lifecycle controls (Task D)
# ---------------------------------------------------------------------------


def pause_mission(root: Path, *, mission_id: str) -> dict[str, Any]:
    """Pause a running mission, saving its checkpoint."""
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        target: dict[str, Any] | None = None
        for item in missions:
            if str(item.get("mission_id", "")) == mission_id:
                target = item
                break
        if target is None:
            raise ValueError(f"mission not found: {mission_id}")
        if str(target.get("status", "")).lower() != "running":
            raise ValueError(f"can only pause a running mission, current status: {target.get('status')}")
        target["status"] = "paused"
        target["updated_utc"] = _now_iso()
        target["status_detail"] = "Paused"
        target["progress_bar"] = _progress_bar(int(target.get("progress_pct", 0)))
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="paused",
        progress_pct=int(target.get("progress_pct", 0)),
        step="Paused",
    )
    return target


def resume_mission(root: Path, *, mission_id: str) -> dict[str, Any]:
    """Resume a paused mission from its checkpoint."""
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        target: dict[str, Any] | None = None
        for item in missions:
            if str(item.get("mission_id", "")) == mission_id:
                target = item
                break
        if target is None:
            raise ValueError(f"mission not found: {mission_id}")
        if str(target.get("status", "")).lower() != "paused":
            raise ValueError(f"can only resume a paused mission, current status: {target.get('status')}")
        target["status"] = "pending"  # Will be picked up by daemon loop
        target["updated_utc"] = _now_iso()
        target["status_detail"] = "Resumed — queued for execution"
        target["progress_bar"] = _progress_bar(int(target.get("progress_pct", 0)))
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="resumed",
        progress_pct=int(target.get("progress_pct", 0)),
        step="Resumed",
    )
    return target


def restart_mission(root: Path, *, mission_id: str) -> dict[str, Any]:
    """Restart a failed/cancelled mission, preserving prior context."""
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        target: dict[str, Any] | None = None
        for item in missions:
            if str(item.get("mission_id", "")) == mission_id:
                target = item
                break
        if target is None:
            raise ValueError(f"mission not found: {mission_id}")
        current = str(target.get("status", "")).lower()
        if current not in ("failed", "cancelled", "exhausted"):
            raise ValueError(f"can only restart a failed/cancelled/exhausted mission, current status: {current}")
        target["status"] = "pending"
        target["progress_pct"] = 0
        target["progress_bar"] = _progress_bar(0)
        target["status_detail"] = "Restarted — queued for execution"
        target["updated_utc"] = _now_iso()
        target["steps"] = _init_mission_steps()
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="restarted",
        progress_pct=0,
        step="Restarted",
    )
    return target


# ---------------------------------------------------------------------------
# Learning dashboard enrichment (Task D)
# ---------------------------------------------------------------------------


def mission_dashboard_metrics(root: Path) -> dict[str, Any]:
    """Compute mission-related dashboard metrics for the last 7 days."""
    missions = load_missions(root)
    now = datetime.now(UTC)
    cutoff = (now - __import__("datetime").timedelta(days=7)).isoformat()

    completed_7d = 0
    failed_7d = 0
    topic_counts: dict[str, int] = {}

    for m in missions:
        updated = str(m.get("updated_utc", ""))
        if updated < cutoff:
            continue
        status = str(m.get("status", "")).lower()
        if status == "completed":
            completed_7d += 1
            topic = str(m.get("topic", "unknown"))
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        elif status in ("failed", "exhausted"):
            failed_7d += 1

    total_completed = sum(1 for m in missions if str(m.get("status", "")).lower() == "completed")
    total_run = sum(1 for m in missions if str(m.get("status", "")).lower() in ("completed", "failed", "exhausted"))
    success_rate = round(total_completed / total_run * 100, 1) if total_run > 0 else 0.0

    top_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "missions_completed_7d": completed_7d,
        "missions_failed_7d": failed_7d,
        "mission_success_rate": success_rate,
        "top_topics_learned": [{"topic": t, "count": c} for t, c in top_topics],
        "total_missions": len(missions),
        "active_count": sum(1 for m in missions if str(m.get("status", "")).lower() in ("running", "paused", "pending")),
    }
