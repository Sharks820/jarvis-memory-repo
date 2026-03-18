from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from jarvis_engine._compat import UTC
from jarvis_engine._shared import atomic_write_json, now_iso
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

from jarvis_engine.web.fetch import (
    fetch_page_text as _fetch_page_text,  # noqa: F401  (used by test monkeypatch)
    fetch_page_text_with_fallbacks as _fetch_page_text_with_fallbacks,
    search_web as _search_web,
)
from jarvis_engine.web.research import STOPWORDS

# File-level lock for missions.json to prevent TOCTOU race conditions
# between concurrent daemon auto-generation, mobile API creates, and mission runs.
_MISSIONS_LOCK = threading.Lock()

logger = logging.getLogger(__name__)


# LM-01: Finite state machine — valid transitions between mission states.
VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "cancelled"},
    "running": {"completed", "failed", "cancelled", "paused", "blocked"},
    "blocked": {"running", "cancelled", "failed"},
    "paused": {"pending", "cancelled"},
    "failed": {"pending", "exhausted"},
    "completed": set(),           # terminal
    "cancelled": {"pending"},     # restart allows cancelled → pending
    "exhausted": {"pending"},     # restart allows exhausted → pending
}


class InvalidTransitionError(ValueError):
    """Raised when a mission state transition is not allowed."""

    def __init__(self, mission_id: str, from_state: str, to_state: str) -> None:
        self.mission_id = mission_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"invalid transition for mission {mission_id}: "
            f"'{from_state}' -> '{to_state}'"
        )


def _check_transition(mission_id: str, from_state: str, to_state: str) -> None:
    """Validate a state transition, raising InvalidTransitionError if disallowed."""
    allowed = VALID_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise InvalidTransitionError(mission_id, from_state, to_state)


def _now_ms() -> int:
    """Current time in milliseconds (monotonic-friendly wall clock)."""
    return int(time.time() * 1000)

MISSION_DEFAULT_SOURCES = ["google", "reddit", "official_docs"]
_PAGE_CACHE: dict[tuple[str, int], tuple[float, str]] = {}
_PAGE_CACHE_LOCK = threading.Lock()
_PAGE_CACHE_TTL_SECONDS = 900.0
_PAGE_CACHE_MAX_BYTES = 50_000_000  # 50 MB soft cap
_PAGE_CACHE_MAX_ENTRIES = 1200  # max entries before eviction
_PAGE_CACHE_EVICT_BATCH = 200  # number of stale entries to evict per sweep
_page_cache_bytes = [0]  # mutable container to avoid 'global' keyword


class MissionStep(TypedDict, total=False):
    """A single step in a mission's execution plan."""

    name: str
    description: str
    weight: float
    status: str  # pending | running | completed | failed | skipped
    elapsed_ms: int
    artifacts_produced: int


class MissionRecord(TypedDict, total=False):
    """Shape returned by ``create_learning_mission``.

    Status is one of: pending, running, blocked, paused, completed, failed,
    cancelled, exhausted.  Transitions are enforced by ``VALID_TRANSITIONS``.
    """

    mission_id: str
    topic: str
    objective: str
    sources: list[str]
    status: str          # pending|running|blocked|paused|completed|failed|cancelled|exhausted
    origin: str
    created_utc: str
    updated_utc: str
    last_report_path: str
    verified_findings: int
    progress_pct: int
    status_detail: str
    progress_bar: str
    prior_results: list[dict[str, Any]]  # LM-06: preserved results from prior attempts
    delivery_method: str  # MOB-08: notification|file|none
    checkpoint: dict[str, Any]


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
    final_status: str


def _missions_path(root: Path) -> Path:
    return root / ".planning" / "missions.json"


def _reports_dir(root: Path) -> Path:
    return root / ".planning" / "missions"


def load_missions(root: Path) -> list[dict[str, Any]]:
    from jarvis_engine._shared import load_json_file

    path = _missions_path(root)
    try:
        raw = load_json_file(path, None, expected_type=list)
    except FileNotFoundError:
        # On Windows, os.replace() during concurrent _save_missions can briefly
        # remove the file.  Return empty rather than crashing.
        return []
    if raw is None:
        return []
    return [item for item in raw if isinstance(item, dict)]


def _save_missions(root: Path, missions: list[dict[str, Any]]) -> None:
    from jarvis_engine._shared import atomic_write_json

    atomic_write_json(_missions_path(root), missions, secure=False)


def _get_mission_checkpoint(root: Path, mission_id: str) -> dict[str, Any]:
    missions = load_missions(root)
    for mission in missions:
        if str(mission.get("mission_id", "")) != mission_id:
            continue
        checkpoint = mission.get("checkpoint", {})
        return checkpoint if isinstance(checkpoint, dict) else {}
    return {}


def _update_mission_checkpoint(root: Path, mission_id: str, **fields: Any) -> None:
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        for mission in missions:
            if str(mission.get("mission_id", "")) != mission_id:
                continue
            checkpoint = mission.get("checkpoint", {})
            if not isinstance(checkpoint, dict):
                checkpoint = {}
            checkpoint.update(fields)
            mission["checkpoint"] = checkpoint
            mission["updated_utc"] = now_iso()
            _save_missions(root, missions)
            return


def _mission_runtime_status(root: Path, mission_id: str) -> str:
    mission = get_mission_by_id(root, mission_id)
    return str(mission.get("status", "") if mission else "").lower()


def _halted_mission_status(root: Path, mission_id: str) -> str:
    status = _mission_runtime_status(root, mission_id)
    return status if status in {"paused", "cancelled", "blocked"} else ""


def _ensure_mission_steps(root: Path, mission_id: str) -> None:
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        for mission in missions:
            if str(mission.get("mission_id", "")) != mission_id:
                continue
            steps = mission.get("steps")
            if not isinstance(steps, list) or not steps:
                mission["steps"] = _init_mission_steps()
                mission["updated_utc"] = now_iso()
                _save_missions(root, missions)
            return


def _step_status(root: Path, mission_id: str, step_name: str) -> str:
    steps = get_mission_steps(root, mission_id)
    for step in steps:
        if str(step.get("name", "")) == step_name:
            return str(step.get("status", "pending"))
    return "pending"


def _step_is_complete(root: Path, mission_id: str, step_name: str) -> bool:
    return _step_status(root, mission_id, step_name) in {"completed", "skipped"}


def _serialize_selected_urls(selected: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"url": url, "domain": domain} for url, domain in selected]


def _deserialize_selected_urls(raw: Any) -> list[tuple[str, str]]:
    if not isinstance(raw, list):
        return []
    selected: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        domain = str(item.get("domain", "")).strip().lower()
        if url and domain:
            selected.append((url, domain))
    return selected


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
    halted = False
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        for mission in missions:
            if str(mission.get("mission_id", "")) != mission_id:
                continue
            current_status = str(mission.get("status", "")).lower()
            if current_status in {"cancelled", "paused", "blocked"}:
                halted = True
                break
            mission["status"] = status
            mission["progress_pct"] = max(0, min(100, int(progress_pct)))
            mission["status_detail"] = status_detail[:180]
            mission["progress_bar"] = _progress_bar(mission["progress_pct"])
            mission["updated_utc"] = now_iso()
            _save_missions(root, missions)
            event_payload = {
                "topic": str(mission.get("topic", "")),
                "status": status,
                "progress_pct": int(mission["progress_pct"]),
                "status_detail": str(mission["status_detail"]),
            }
            break
    if halted or event_payload is None:
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
        from jarvis_engine.memory.activity_feed import ActivityCategory, log_activity

        log_activity(
            ActivityCategory.MISSION_STATE_CHANGE,
            f"Mission {status}: {topic}",
            {
                "provider": "web_research",
                "step": step[:180],
                "progress_pct": max(0, min(100, int(progress_pct))),
                "status": status,
            },
            correlation_id=f"mission-{mission_id}",
            mission_id=mission_id,
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
    delivery_method: str = "notification",
) -> MissionRecord:
    cleaned_topic = topic.strip()
    if not cleaned_topic:
        raise ValueError("topic is required")
    if delivery_method not in ("notification", "file", "none"):
        delivery_method = "notification"
    import secrets
    mission_id = f"m-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3)}"
    mission: MissionRecord = {
        "mission_id": mission_id,
        "topic": cleaned_topic[:200],
        "objective": objective.strip()[:400],
        "sources": sources or list(MISSION_DEFAULT_SOURCES),
        "status": "pending",
        "origin": origin,
        "created_utc": now_iso(),
        "updated_utc": now_iso(),
        "last_report_path": "",
        "verified_findings": 0,
        "progress_pct": 0,
        "status_detail": "Queued",
        "progress_bar": _progress_bar(0),
        "delivery_method": delivery_method,
    }
    with _MISSIONS_LOCK:
        missions = load_missions(root)
        missions.append(dict(mission))
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
    """Generate diverse search queries targeting reliably fetchable sources.

    Produces 8-12 queries that target server-rendered, high-quality sites
    (Wikipedia, StackOverflow, educational sites, official docs) rather than
    JS-rendered shells.
    """
    queries = [topic, f"{topic} tutorial", f"{topic} best practices"]
    lowered = {s.lower().strip() for s in sources}
    if "reddit" in lowered:
        queries.append(f"site:old.reddit.com {topic}")
    if "google" in lowered:
        queries.append(f"{topic} guide")
    if "official_docs" in lowered:
        queries.append(f"{topic} official documentation")
    if "wikipedia" in lowered:
        queries.append(f"site:en.wikipedia.org {topic}")
        queries.append(f"{topic} explained")
    # Always target high-quality fetchable sources for diversity
    queries.append(f"site:en.wikipedia.org {topic}")
    queries.append(f"{topic} site:stackoverflow.com")
    queries.append(f"{topic} overview")
    queries.append(f"{topic} introduction beginner")
    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))


def _fetch_page_cached(url: str, *, max_bytes: int) -> str:
    key = (url.strip(), max(1, int(max_bytes)))
    now = time.time()
    with _PAGE_CACHE_LOCK:
        cached = _PAGE_CACHE.get(key)
        if cached is not None:
            ts, value = cached
            if now - ts <= _PAGE_CACHE_TTL_SECONDS:
                return value
            old = _PAGE_CACHE.pop(key, None)
            if old is not None:
                _page_cache_bytes[0] -= len(old[1].encode("utf-8"))
    value = _fetch_page_text_with_fallbacks(url, max_bytes=max_bytes)
    if not value:
        # Do not memoize transient fetch failures across retries.
        return ""
    with _PAGE_CACHE_LOCK:
        # Adjust byte counter if key already exists (concurrent fetch or refresh)
        if key in _PAGE_CACHE:
            _old_ts, old_val = _PAGE_CACHE[key]
            _page_cache_bytes[0] -= len(old_val.encode("utf-8"))
        _PAGE_CACHE[key] = (now, value)
        _page_cache_bytes[0] += len(value.encode("utf-8"))
        if len(_PAGE_CACHE) > _PAGE_CACHE_MAX_ENTRIES or _page_cache_bytes[0] > _PAGE_CACHE_MAX_BYTES:
            # Keep cache bounded for 24/7 operation.
            stale = sorted(_PAGE_CACHE.items(), key=lambda item: item[1][0])[:_PAGE_CACHE_EVICT_BATCH]
            for stale_key, (_stale_ts, stale_val) in stale:
                _page_cache_bytes[0] -= len(stale_val.encode("utf-8"))
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


def _verify_candidates(candidates: list[dict[str, str]]) -> list[VerifiedFinding]:
    """Cross-reference candidates across sources to verify factual claims.

    Verification tiers:
      - 2+ domains with 2+ keyword overlap → high confidence (0.55+)
      - Single source with 4+ keywords → low confidence (0.30) — still useful for niche topics
    """
    # Precompute keywords for all candidates to avoid redundant O(n) _keywords() calls
    candidate_keys = [_keywords(c.get("statement", "")) for c in candidates]

    verified: list[VerifiedFinding] = []
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
        current = str(target.get("status", "pending")).lower()
        _check_transition(mission_id, current, "running")
        target["status"] = "running"
        target["updated_utc"] = now_iso()
        _save_missions(root, missions)
        topic = str(target.get("topic", "")).strip()
        objective = str(target.get("objective", ""))
        sources = target.get("sources", MISSION_DEFAULT_SOURCES)

    if not isinstance(sources, list):
        sources = list(MISSION_DEFAULT_SOURCES)
    return topic, objective, sources


def _search_mission_urls(
    topic: str,
    queries: list[str],
    *,
    max_search_results: int,
    max_pages: int,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Search the web and select a domain-diverse set of URLs to fetch."""
    urls: list[str] = []
    for query in queries:
        hits = _search_web(query, limit=max_search_results)
        logger.info("Mission search '%s' → %d URLs", query, len(hits))
        urls.extend(hits)
    urls = list(dict.fromkeys(urls))

    if not urls:
        logger.warning("Mission for '%s': all %d search queries returned 0 URLs", topic, len(queries))

    # Domain diversity: max 3 pages per domain to spread sources for cross-referencing
    max_per_domain = 3
    domain_counts: dict[str, int] = {}
    diverse_urls: list[str] = []
    for url in urls:
        domain = urlparse(url).netloc.lower()
        if not domain:
            continue
        if domain_counts.get(domain, 0) < max_per_domain:
            diverse_urls.append(url)
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

    selected: list[tuple[str, str]] = []
    for url in diverse_urls[: max(1, max_pages)]:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain:
            continue
        selected.append((url, domain))

    scanned_urls = [url for url, _domain in selected]
    return scanned_urls, selected


def _fetch_selected_content(
    topic: str,
    selected: list[tuple[str, str]],
) -> list[dict[str, str]]:
    """Fetch selected pages and extract candidate findings."""

    candidate_rows: list[dict[str, str]] = []
    fetched_ok = 0
    fetched_empty = 0
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
                fetched_empty += 1
                continue
            fetched_ok += 1
            candidates = _extract_candidates(text, topic=topic, max_candidates=8)
            for statement in candidates:
                candidate_rows.append({"statement": statement, "url": url, "domain": domain})

    logger.info(
        "Mission '%s': %d/%d pages fetched OK, %d candidates extracted",
        topic, fetched_ok, len(selected), len(candidate_rows),
    )
    return candidate_rows


def _build_mission_report(
    *,
    mission_id: str,
    topic: str,
    objective: str,
    queries: list[str],
    scanned_urls: list[str],
    candidate_rows: list[dict[str, str]],
    verified: list[VerifiedFinding],
    final_status: str = "",
) -> MissionReport:
    report: MissionReport = {
        "mission_id": mission_id,
        "topic": topic,
        "objective": objective,
        "queries": queries,
        "scanned_urls": scanned_urls,
        "candidate_count": len(candidate_rows),
        "verified_findings": verified,
        "verified_count": len(verified),
        "completed_utc": now_iso(),
    }
    if final_status:
        report["final_status"] = final_status
    return report


def _finalize_mission(
    root: Path,
    mission_id: str,
    verified: list[VerifiedFinding],
    report: MissionReport,
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
        # Respect user-initiated cancellation or pause — do not overwrite.
        if target.get("status") in ("cancelled", "paused"):
            logger.info("Mission %s is %s — preserving status", mission_id, target["status"])
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
            target["status"] = "failed"  # retry_failed_missions handles failed→exhausted
            target["progress_pct"] = 100
            target["status_detail"] = "Completed with no verified findings"
            target["progress_bar"] = _progress_bar(100)
        target["updated_utc"] = now_iso()
        target["last_report_path"] = str(report_path)
        target["verified_findings"] = len(verified)
        delivery_method = str(target.get("delivery_method", "notification"))
        final_status = str(target.get("status", "completed"))
        final_progress = int(target.get("progress_pct", 100) or 100)
        final_detail = str(target.get("status_detail", "Completed"))
        final_topic = str(target.get("topic", ""))
        _save_missions(root, missions)

    # MOB-08: Trigger delivery action based on delivery_method
    if final_status == "completed":
        _execute_delivery(
            root,
            mission_id=mission_id,
            topic=final_topic,
            delivery_method=delivery_method,
            report_path=report_path,
            verified_count=len(verified),
        )

    _log_mission_activity(
        mission_id=mission_id,
        topic=final_topic,
        status=final_status,
        progress_pct=final_progress,
        step=final_detail,
    )


def _execute_delivery(
    root: Path,
    *,
    mission_id: str,
    topic: str,
    delivery_method: str,
    report_path: Path,
    verified_count: int,
) -> None:
    """MOB-08: Execute delivery action and log audit trail to activity feed."""
    delivery_detail = {"mission_id": mission_id, "method": delivery_method}
    try:
        if delivery_method == "notification":
            # Proactive alert already fired in _finalize_mission for notifications
            delivery_detail["action"] = "proactive_alert_sent"
        elif delivery_method == "file":
            # Report already persisted at report_path — record the export location
            delivery_detail["action"] = "file_exported"
            delivery_detail["report_path"] = str(report_path)
        else:
            delivery_detail["action"] = "none"

        # Audit trail: log delivery to activity feed
        from jarvis_engine.memory.activity_feed import ActivityCategory, log_activity

        log_activity(
            ActivityCategory.MISSION_STATE_CHANGE,
            f"Mission delivered ({delivery_method}): {topic}",
            {
                "mission_id": mission_id,
                "delivery_method": delivery_method,
                "report_path": str(report_path),
                "verified_count": verified_count,
                "audit": "delivery_completed",
            },
            mission_id=mission_id,
        )
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        logger.debug("Mission delivery action failed for %s: %s", mission_id, exc)


def get_mission_artifacts(root: Path, mission_id: str) -> list[dict[str, Any]]:
    """MOB-10: Return artifacts for a mission with metadata for mobile retrieval.

    Scans the mission report file and returns a list of artifact descriptors
    with type, size, created_at, and version-safe paths.
    """
    artifacts: list[dict[str, Any]] = []
    # Find the mission to get report path
    missions = load_missions(root)
    target: dict[str, Any] | None = None
    for m in missions:
        if str(m.get("mission_id", "")) == mission_id:
            target = m
            break
    if target is None:
        return artifacts

    report_path_str = str(target.get("last_report_path", ""))
    if report_path_str:
        report_path = Path(report_path_str)
        if report_path.exists():
            stat = report_path.stat()
            artifacts.append({
                "artifact_id": f"{mission_id}-report",
                "type": "report_json",
                "filename": report_path.name,
                "size_bytes": stat.st_size,
                "created_at": target.get("created_utc", ""),
                "updated_at": target.get("updated_utc", ""),
                "mission_id": mission_id,
                "version": 1,
            })

    # Also check for any additional files in the missions directory
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", mission_id)[:80]
    missions_dir = _reports_dir(root)
    if missions_dir.exists():
        for entry in sorted(missions_dir.iterdir()):
            if entry.name.startswith(safe_id) and entry.name != f"{safe_id}.report.json":
                stat = entry.stat()
                artifacts.append({
                    "artifact_id": f"{mission_id}-{entry.stem}",
                    "type": "supplementary",
                    "filename": entry.name,
                    "size_bytes": stat.st_size,
                    "created_at": target.get("created_utc", ""),
                    "updated_at": target.get("updated_utc", ""),
                    "mission_id": mission_id,
                    "version": 1,
                })

    return artifacts


def get_mission_by_id(root: Path, mission_id: str) -> dict[str, Any] | None:
    """Return a single mission by ID, or None if not found."""
    missions = load_missions(root)
    for m in missions:
        if str(m.get("mission_id", "")) == mission_id:
            return m
    return None


def run_learning_mission(
    root: Path,
    *,
    mission_id: str,
    max_search_results: int = 8,
    max_pages: int = 12,
) -> MissionReport:
    # Initialize step tracking only for fresh missions. Resumed missions keep checkpoint state.
    _ensure_mission_steps(root, mission_id)

    try:
        return _run_learning_mission_inner(
            root, mission_id=mission_id,
            max_search_results=max_search_results, max_pages=max_pages,
        )
    except Exception as exc:
        # CRITICAL: Never leave a mission stuck in "running" on unhandled errors.
        logger.error("Mission %s failed with unhandled error: %s", mission_id, exc)
        try:
            with _MISSIONS_LOCK:
                missions = load_missions(root)
                for m in missions:
                    if str(m.get("mission_id", "")) == mission_id:
                        if m.get("status") == "running":
                            m["status"] = "failed"
                            m["updated_utc"] = now_iso()
                            m["status_detail"] = f"Unhandled error: {type(exc).__name__}"
                            _save_missions(root, missions)
                        break
        except Exception as cleanup_exc:
            logger.error("Failed to mark mission %s as failed: %s", mission_id, cleanup_exc)
        raise


def _run_learning_mission_inner(
    root: Path,
    *,
    mission_id: str,
    max_search_results: int = 8,
    max_pages: int = 12,
) -> MissionReport:
    """Inner implementation of run_learning_mission, wrapped by error handler."""
    checkpoint = _get_mission_checkpoint(root, mission_id)
    _t0 = _now_ms()
    topic, objective, sources = _start_mission(root, mission_id)
    stored_queries = checkpoint.get("queries")
    queries = [str(item) for item in stored_queries] if isinstance(stored_queries, list) else []

    if not _step_is_complete(root, mission_id, "init"):
        _update_step(root, mission_id, "init", status="running")
        queries = _mission_queries(topic, [str(s) for s in sources])
        _update_step(root, mission_id, "init", status="completed", elapsed_ms=_now_ms() - _t0)
        _update_mission_checkpoint(root, mission_id, queries=queries)
    elif not queries:
        queries = _mission_queries(topic, [str(s) for s in sources])
        _update_mission_checkpoint(root, mission_id, queries=queries)

    halted = _halted_mission_status(root, mission_id)
    if halted:
        return _build_mission_report(
            mission_id=mission_id,
            topic=topic,
            objective=objective,
            queries=queries,
            scanned_urls=[str(item) for item in checkpoint.get("scanned_urls", []) if isinstance(item, str)],
            candidate_rows=checkpoint.get("candidate_rows", []) if isinstance(checkpoint.get("candidate_rows", []), list) else [],
            verified=checkpoint.get("verified_findings", []) if isinstance(checkpoint.get("verified_findings", []), list) else [],
            final_status=halted,
        )

    # Step: search web
    scanned_urls = [str(item) for item in checkpoint.get("scanned_urls", []) if isinstance(item, str)]
    selected = _deserialize_selected_urls(checkpoint.get("selected_urls", []))
    if not _step_is_complete(root, mission_id, "search_web"):
        _t1 = _now_ms()
        _update_step(root, mission_id, "search_web", status="running")
        _update_mission_progress(
            root, mission_id, status="running", progress_pct=_compute_step_progress(get_mission_steps(root, mission_id)),
            status_detail="Searching the web for sources",
        )
        scanned_urls, selected = _search_mission_urls(
            topic,
            queries,
            max_search_results=max_search_results,
            max_pages=max_pages,
        )
        _update_step(root, mission_id, "search_web", status="completed", elapsed_ms=_now_ms() - _t1, artifacts_produced=len(scanned_urls))
        _update_mission_checkpoint(
            root,
            mission_id,
            queries=queries,
            scanned_urls=scanned_urls,
            selected_urls=_serialize_selected_urls(selected),
        )

    halted = _halted_mission_status(root, mission_id)
    if halted:
        checkpoint = _get_mission_checkpoint(root, mission_id)
        return _build_mission_report(
            mission_id=mission_id,
            topic=topic,
            objective=objective,
            queries=queries,
            scanned_urls=[str(item) for item in checkpoint.get("scanned_urls", []) if isinstance(item, str)],
            candidate_rows=checkpoint.get("candidate_rows", []) if isinstance(checkpoint.get("candidate_rows", []), list) else [],
            verified=checkpoint.get("verified_findings", []) if isinstance(checkpoint.get("verified_findings", []), list) else [],
            final_status=halted,
        )

    # Step: fetch pages
    checkpoint = _get_mission_checkpoint(root, mission_id)
    candidate_rows = checkpoint.get("candidate_rows", [])
    if not isinstance(candidate_rows, list):
        candidate_rows = []
    if not _step_is_complete(root, mission_id, "fetch_pages"):
        _t2 = _now_ms()
        _update_step(root, mission_id, "fetch_pages", status="running")
        _update_mission_progress(
            root, mission_id, status="running", progress_pct=_compute_step_progress(get_mission_steps(root, mission_id)),
            status_detail=f"Fetching and reading {len(selected)} pages",
        )
        candidate_rows = _fetch_selected_content(topic, selected)
        _update_step(root, mission_id, "fetch_pages", status="completed",
                     elapsed_ms=_now_ms() - _t2,
                     artifacts_produced=len(scanned_urls))
        _update_mission_checkpoint(root, mission_id, candidate_rows=candidate_rows)

    halted = _halted_mission_status(root, mission_id)
    if halted:
        checkpoint = _get_mission_checkpoint(root, mission_id)
        return _build_mission_report(
            mission_id=mission_id,
            topic=topic,
            objective=objective,
            queries=queries,
            scanned_urls=[str(item) for item in checkpoint.get("scanned_urls", []) if isinstance(item, str)],
            candidate_rows=checkpoint.get("candidate_rows", []) if isinstance(checkpoint.get("candidate_rows", []), list) else [],
            verified=checkpoint.get("verified_findings", []) if isinstance(checkpoint.get("verified_findings", []), list) else [],
            final_status=halted,
        )

    # Step: extract candidates (done inline during fetch_pages)
    if not _step_is_complete(root, mission_id, "extract_candidates"):
        _t3 = _now_ms()
        _update_step(root, mission_id, "extract_candidates", status="running")
        _update_mission_progress(
            root, mission_id, status="running", progress_pct=_compute_step_progress(get_mission_steps(root, mission_id)),
            status_detail=f"Extracting candidate findings from {len(scanned_urls)} pages",
        )
        _update_step(root, mission_id, "extract_candidates", status="completed",
                     elapsed_ms=_now_ms() - _t3,
                     artifacts_produced=len(candidate_rows))

    halted = _halted_mission_status(root, mission_id)
    if halted:
        checkpoint = _get_mission_checkpoint(root, mission_id)
        return _build_mission_report(
            mission_id=mission_id,
            topic=topic,
            objective=objective,
            queries=queries,
            scanned_urls=[str(item) for item in checkpoint.get("scanned_urls", []) if isinstance(item, str)],
            candidate_rows=checkpoint.get("candidate_rows", []) if isinstance(checkpoint.get("candidate_rows", []), list) else [],
            verified=checkpoint.get("verified_findings", []) if isinstance(checkpoint.get("verified_findings", []), list) else [],
            final_status=halted,
        )

    # Step: verify findings
    checkpoint = _get_mission_checkpoint(root, mission_id)
    verified = checkpoint.get("verified_findings", [])
    if not isinstance(verified, list):
        verified = []
    if not _step_is_complete(root, mission_id, "verify_findings"):
        _t4 = _now_ms()
        _update_step(root, mission_id, "verify_findings", status="running")
        _update_mission_progress(
            root, mission_id, status="running",
            progress_pct=_compute_step_progress(get_mission_steps(root, mission_id)),
            status_detail=f"Verifying {len(candidate_rows)} candidate findings",
        )
        verified = _verify_candidates(candidate_rows)
        _update_step(root, mission_id, "verify_findings", status="completed",
                     elapsed_ms=_now_ms() - _t4,
                     artifacts_produced=len(verified))
        _update_mission_checkpoint(root, mission_id, verified_findings=verified)

    halted = _halted_mission_status(root, mission_id)
    if halted:
        checkpoint = _get_mission_checkpoint(root, mission_id)
        return _build_mission_report(
            mission_id=mission_id,
            topic=topic,
            objective=objective,
            queries=queries,
            scanned_urls=[str(item) for item in checkpoint.get("scanned_urls", []) if isinstance(item, str)],
            candidate_rows=checkpoint.get("candidate_rows", []) if isinstance(checkpoint.get("candidate_rows", []), list) else [],
            verified=checkpoint.get("verified_findings", []) if isinstance(checkpoint.get("verified_findings", []), list) else [],
            final_status=halted,
        )

    # Step: finalize
    _t5 = _now_ms()
    _update_step(root, mission_id, "finalize", status="running")
    _update_mission_progress(
        root, mission_id, status="running", progress_pct=_compute_step_progress(get_mission_steps(root, mission_id)),
        status_detail="Finalizing mission report",
    )

    halted = _halted_mission_status(root, mission_id)
    if halted:
        return _build_mission_report(
            mission_id=mission_id,
            topic=topic,
            objective=objective,
            queries=queries,
            scanned_urls=scanned_urls,
            candidate_rows=candidate_rows,
            verified=verified,
            final_status=halted,
        )

    # Build and persist report
    report = _build_mission_report(
        mission_id=mission_id,
        topic=topic,
        objective=objective,
        queries=queries,
        scanned_urls=scanned_urls,
        candidate_rows=candidate_rows,
        verified=verified,
    )
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", mission_id)[:80]
    report_path = _reports_dir(root) / f"{safe_id}.report.json"
    atomic_write_json(report_path, dict(report))  # type-safe dict conversion

    _update_step(root, mission_id, "finalize", status="completed", elapsed_ms=_now_ms() - _t5)
    _update_mission_progress(
        root, mission_id, status="running", progress_pct=100,
        status_detail="Finalizing mission report",
    )
    _finalize_mission(root, mission_id, verified, report, report_path)
    return report


# Cancel a mission


def cancel_mission(root: Path, *, mission_id: str) -> dict[str, Any]:
    """Cancel a mission immediately by setting its status to 'cancelled'.

    LM-07: Cancellation is immediate, safe, and visible across all clients.
    Uses VALID_TRANSITIONS state machine for enforcement.
    Returns the updated mission dict, or raises ValueError/InvalidTransitionError.
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

        current_status = str(target.get("status", "")).lower()
        _check_transition(mission_id, current_status, "cancelled")

        target["status"] = "cancelled"
        target["updated_utc"] = now_iso()
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


# Retry failed missions

def retry_failed_missions(root: Path) -> int:
    """Re-queue failed missions (up to 2 retries) by setting status back to pending.

    LM-06: Retries preserve prior context/results via ``prior_results`` field
    so intermediate work is never lost.
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
            retries = int(mission.get("retries", 0) or 0)
            if retries >= 2:
                # Use state machine: failed → exhausted
                mission["status"] = "exhausted"
                mission["updated_utc"] = now_iso()
                modified = True
                continue
            # LM-06: Preserve prior results before resetting progress
            _preserve_prior_results(mission)
            # Broaden search: add alternative query phrasing
            sources = mission.get("sources", list(MISSION_DEFAULT_SOURCES))
            if not isinstance(sources, list):
                sources = list(MISSION_DEFAULT_SOURCES)
            # Add broadening sources on retry
            if "wikipedia" not in [s.lower() for s in sources]:
                sources.append("wikipedia")
            mission["sources"] = sources
            mission["retries"] = retries + 1
            # Use state machine: failed → pending
            mission["status"] = "pending"
            mission["updated_utc"] = now_iso()
            mission["progress_pct"] = 0
            mission["status_detail"] = f"Retry #{retries + 1} queued (prior results preserved)"
            mission["progress_bar"] = _progress_bar(0)
            mission["steps"] = _init_mission_steps()
            mission.pop("checkpoint", None)
            re_queued += 1
            modified = True
        if modified:
            _save_missions(root, missions)
    if re_queued > 0:
        logger.info("Re-queued %d failed mission(s) for retry", re_queued)
    return re_queued


def _preserve_prior_results(mission: dict[str, Any]) -> None:
    """Snapshot the current mission state into ``prior_results`` (LM-06).

    Keeps verified findings, steps, progress, and report path from this attempt
    so that retry does not lose intermediate work.
    """
    prior = mission.get("prior_results")
    if not isinstance(prior, list):
        prior = []
    snapshot: dict[str, Any] = {
        "attempt": int(mission.get("retries", 0) or 0) + 1,
        "status": str(mission.get("status", "")),
        "progress_pct": int(mission.get("progress_pct", 0) or 0),
        "verified_findings": int(mission.get("verified_findings", 0) or 0),
        "last_report_path": str(mission.get("last_report_path", "")),
        "steps": mission.get("steps", []),
        "preserved_utc": now_iso(),
    }
    prior.append(snapshot)
    mission["prior_results"] = prior


# Auto-generate missions from knowledge gaps

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
            created.append(dict(mission))
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
    with _MISSIONS_LOCK:
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
        from jarvis_engine._shared import memory_db_path
        db_path = memory_db_path(root)

    conn = None
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        from jarvis_engine._db_pragmas import connect_db
        try:
            conn = connect_db(db_path)
        except (sqlite3.Error, OSError, ValueError) as exc:
            logger.debug("Mission topic DB open failed for %s: %s", db_path, exc)
            conn = None

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


# Step-driven progress model (Task D)

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
            if str(mission.get("status", "")).lower() in {"cancelled", "paused", "blocked"}:
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
            mission["updated_utc"] = now_iso()
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
    """Return all running, paused, blocked, and pending missions."""
    missions = load_missions(root)
    return [
        m for m in missions
        if str(m.get("status", "")).lower() in ("running", "paused", "pending", "blocked")
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
                    logger.debug("Failed to parse mission created_utc timestamp: %s", created)
            artifacts = 0
            if isinstance(steps, list):
                artifacts = sum(int(s.get("artifacts_produced", 0) or 0) for s in steps)
            return {
                "mission_id": m.get("mission_id", ""),
                "mission_topic": m.get("topic", ""),
                "current_step": current_step,
                "progress_pct": int(m.get("progress_pct", 0) or 0),
                "elapsed_s": elapsed_s,
                "artifacts_so_far": artifacts,
            }
    return None


# Pause / Resume / Restart lifecycle controls (Task D)


def block_mission(root: Path, *, mission_id: str, reason: str = "") -> dict[str, Any]:
    """Block a running mission (LM-01: running -> blocked only).

    Used when a mission cannot proceed due to external dependency (e.g.,
    rate limit, missing API key, network outage).
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
        current = str(target.get("status", "")).lower()
        _check_transition(mission_id, current, "blocked")
        target["status"] = "blocked"
        target["updated_utc"] = now_iso()
        target["status_detail"] = (reason or "Blocked")[:180]
        target["progress_bar"] = _progress_bar(int(target.get("progress_pct", 0) or 0))
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="blocked",
        progress_pct=int(target.get("progress_pct", 0) or 0),
        step=target["status_detail"],
    )
    return target


def unblock_mission(root: Path, *, mission_id: str) -> dict[str, Any]:
    """Unblock a blocked mission back to running (LM-01: blocked -> running)."""
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
        _check_transition(mission_id, current, "running")
        target["status"] = "running"
        target["updated_utc"] = now_iso()
        target["status_detail"] = "Unblocked — resumed execution"
        target["progress_bar"] = _progress_bar(int(target.get("progress_pct", 0) or 0))
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="running",
        progress_pct=int(target.get("progress_pct", 0) or 0),
        step="Unblocked",
    )
    return target


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
        current = str(target.get("status", "")).lower()
        _check_transition(mission_id, current, "paused")
        target["status"] = "paused"
        target["updated_utc"] = now_iso()
        target["status_detail"] = "Paused"
        target["progress_bar"] = _progress_bar(int(target.get("progress_pct", 0) or 0))
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="paused",
        progress_pct=int(target.get("progress_pct", 0) or 0),
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
        current = str(target.get("status", "")).lower()
        _check_transition(mission_id, current, "pending")
        target["status"] = "pending"  # Will be picked up by daemon loop
        target["updated_utc"] = now_iso()
        target["status_detail"] = "Resumed — continuing from checkpoint"
        target["progress_bar"] = _progress_bar(int(target.get("progress_pct", 0) or 0))
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="resumed",
        progress_pct=int(target.get("progress_pct", 0) or 0),
        step="Resumed",
    )
    return target


def restart_mission(root: Path, *, mission_id: str) -> dict[str, Any]:
    """Restart a failed/cancelled/exhausted mission, preserving prior context (LM-06)."""
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
        _check_transition(mission_id, current, "pending")
        # LM-06: Preserve prior results before resetting
        _preserve_prior_results(target)
        target["status"] = "pending"
        target["progress_pct"] = 0
        target["progress_bar"] = _progress_bar(0)
        target["status_detail"] = "Restarted — queued for execution"
        target["updated_utc"] = now_iso()
        target["steps"] = _init_mission_steps()
        target.pop("checkpoint", None)
        _save_missions(root, missions)

    _log_mission_activity(
        mission_id=mission_id,
        topic=str(target.get("topic", "")),
        status="restarted",
        progress_pct=0,
        step="Restarted",
    )
    return target


# Learning dashboard enrichment (Task D)


def mission_dashboard_metrics(root: Path) -> dict[str, Any]:
    """Compute mission-related dashboard metrics for the last 7 days."""
    missions = load_missions(root)
    now = datetime.now(UTC)
    from datetime import timedelta
    cutoff = (now - timedelta(days=7)).isoformat()

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

