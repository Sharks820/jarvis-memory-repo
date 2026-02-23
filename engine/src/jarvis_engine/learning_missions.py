from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jarvis_engine.web_fetch import (
    fetch_page_text as _fetch_page_text,
    is_safe_public_url as _is_safe_public_url,
    search_duckduckgo as _search_duckduckgo,
)

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
        "created_utc": datetime.now(UTC).isoformat(),
        "updated_utc": datetime.now(UTC).isoformat(),
        "last_report_path": "",
        "verified_findings": 0,
    }
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
            _page_cache_bytes -= len(value)
            _PAGE_CACHE.pop(key, None)
    value = _fetch_page_text(url, max_bytes=max_bytes)
    with _PAGE_CACHE_LOCK:
        _PAGE_CACHE[key] = (now, value)
        _page_cache_bytes += len(value)
        if len(_PAGE_CACHE) > 1200 or _page_cache_bytes > _PAGE_CACHE_MAX_BYTES:
            # Keep cache bounded for 24/7 operation.
            stale = sorted(_PAGE_CACHE.items(), key=lambda item: item[1][0])[:200]
            for stale_key, (_stale_ts, stale_val) in stale:
                _page_cache_bytes -= len(stale_val)
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
    # Precompute keywords for all candidates to avoid redundant O(n) _keywords() calls
    candidate_keys = [_keywords(c.get("statement", "")) for c in candidates]

    verified: list[dict[str, Any]] = []
    for idx, item in enumerate(candidates):
        statement = item.get("statement", "").strip()
        if not statement:
            continue
        base_domain = item.get("domain", "")
        base_keys = candidate_keys[idx]
        support_urls = {item.get("url", "")}
        support_domains = {base_domain}
        for jdx, other in enumerate(candidates):
            if jdx == idx:
                continue
            if other.get("domain", "") == base_domain:
                continue
            overlap = len(base_keys.intersection(candidate_keys[jdx]))
            if overlap >= 4:
                support_urls.add(other.get("url", ""))
                support_domains.add(other.get("domain", ""))
        if len(support_domains) >= 2:
            verified.append(
                {
                    "statement": statement,
                    "source_urls": sorted(u for u in support_urls if u),
                    "source_domains": sorted(d for d in support_domains if d),
                    "confidence": round(min(1.0, 0.45 + 0.2 * len(support_domains)), 2),
                }
            )

    # Deduplicate by normalized statement.
    dedup: dict[str, dict[str, Any]] = {}
    for item in verified:
        key = re.sub(r"[^a-z0-9]+", " ", item["statement"].lower()).strip()
        if key not in dedup:
            dedup[key] = item
    return list(dedup.values())


def run_learning_mission(
    root: Path,
    *,
    mission_id: str,
    max_search_results: int = 8,
    max_pages: int = 12,
) -> dict[str, Any]:
    missions = load_missions(root)
    target: dict[str, Any] | None = None
    for item in missions:
        if str(item.get("mission_id", "")) == mission_id:
            target = item
            break
    if target is None:
        raise ValueError(f"mission not found: {mission_id}")

    topic = str(target.get("topic", "")).strip()
    sources = target.get("sources", MISSION_DEFAULT_SOURCES)
    if not isinstance(sources, list):
        sources = list(MISSION_DEFAULT_SOURCES)
    queries = _mission_queries(topic, [str(s) for s in sources])

    urls: list[str] = []
    for query in queries:
        urls.extend(_search_duckduckgo(query, limit=max_search_results))
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

    target["status"] = "completed"
    target["updated_utc"] = datetime.now(UTC).isoformat()
    target["last_report_path"] = str(report_path)
    target["verified_findings"] = len(verified)
    _save_missions(root, missions)
    return report
