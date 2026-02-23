from __future__ import annotations

import html
import json
import logging
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MISSION_DEFAULT_SOURCES = ["google", "reddit", "official_docs"]
_PAGE_CACHE: dict[tuple[str, int], tuple[float, str]] = {}
_PAGE_CACHE_LOCK = threading.Lock()
_PAGE_CACHE_TTL_SECONDS = 900.0
STOPWORDS = {
    "about",
    "after",
    "also",
    "because",
    "between",
    "could",
    "does",
    "from",
    "have",
    "into",
    "just",
    "more",
    "other",
    "over",
    "some",
    "than",
    "that",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "with",
    "your",
    "what",
    "when",
    "where",
    "which",
    "while",
}


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
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _save_missions(root: Path, missions: list[dict[str, Any]]) -> None:
    path = _missions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(missions, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


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


def _search_duckduckgo(query: str, *, limit: int) -> list[str]:
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    req = Request(
        search_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    try:
        resp = urlopen(req, timeout=12)  # nosec B310
        payload = resp.read(400_000)
    except OSError:
        return []
    finally:
        try:
            if "resp" in locals():
                resp.close()  # type: ignore[attr-defined]
        except Exception:
            pass
    text = payload.decode("utf-8", errors="replace")
    urls: list[str] = []
    for match in re.findall(r'href="(https?://[^"]+)"', text):
        candidate = html.unescape(match).strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if not _is_safe_public_url(candidate):
            continue
        if "duckduckgo.com" in parsed.netloc.lower():
            continue
        urls.append(candidate)
    # Preserve order while deduplicating.
    unique = list(dict.fromkeys(urls))
    return unique[: max(1, limit)]


def _resolve_and_check_ip(url: str) -> bool:
    """Re-resolve hostname immediately before fetch to prevent DNS rebinding."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    try:
        resolved = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for item in resolved:
        raw_ip = item[4][0]
        try:
            ip = ip_address(raw_ip)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    return True


# TODO: deduplicate with web_research.py
def _fetch_page_text(url: str, *, max_bytes: int) -> str:
    if not _is_safe_public_url(url):
        return ""
    # Second DNS check immediately before fetch to prevent TOCTOU / DNS rebinding
    if not _resolve_and_check_ip(url):
        return ""
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    try:
        resp = urlopen(req, timeout=12)  # nosec B310
        data = resp.read(max_bytes)
    except OSError:
        return ""
    finally:
        try:
            if "resp" in locals():
                resp.close()  # type: ignore[attr-defined]
        except Exception:
            pass
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fetch_page_cached(url: str, *, max_bytes: int) -> str:
    key = (url.strip(), max(1, int(max_bytes)))
    now = time.time()
    with _PAGE_CACHE_LOCK:
        cached = _PAGE_CACHE.get(key)
        if cached is not None:
            ts, value = cached
            if now - ts <= _PAGE_CACHE_TTL_SECONDS:
                return value
            _PAGE_CACHE.pop(key, None)
    value = _fetch_page_text(url, max_bytes=max_bytes)
    with _PAGE_CACHE_LOCK:
        _PAGE_CACHE[key] = (now, value)
        if len(_PAGE_CACHE) > 1200:
            # Keep cache bounded for 24/7 operation.
            stale = sorted(_PAGE_CACHE.items(), key=lambda item: item[1][0])[:200]
            for stale_key, _stale_value in stale:
                _PAGE_CACHE.pop(stale_key, None)
    return value


def _topic_keywords(topic: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]{4,}", topic.lower())
    return {w for w in words if w not in STOPWORDS}


# TODO: deduplicate with web_research.py
def _is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost"}:
        return False
    try:
        ip = ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        pass
    try:
        resolved = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for item in resolved:
        raw_ip = item[4][0]
        try:
            ip = ip_address(raw_ip)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    return True


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
    verified: list[dict[str, Any]] = []
    for idx, item in enumerate(candidates):
        statement = item.get("statement", "").strip()
        if not statement:
            continue
        base_domain = item.get("domain", "")
        base_keys = _keywords(statement)
        support_urls = {item.get("url", "")}
        support_domains = {base_domain}
        for jdx, other in enumerate(candidates):
            if jdx == idx:
                continue
            if other.get("domain", "") == base_domain:
                continue
            other_stmt = other.get("statement", "")
            overlap = len(base_keys.intersection(_keywords(other_stmt)))
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
                text = future.result()
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
    report_path = _reports_dir(root) / f"{mission_id}.report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    target["status"] = "completed"
    target["updated_utc"] = datetime.now(UTC).isoformat()
    target["last_report_path"] = str(report_path)
    target["verified_findings"] = len(verified)
    _save_missions(root, missions)
    return report
