"""Interest learning engine for news personalization.

Tracks user interests with exponential decay scoring. Topics are recorded
with a weight (positive = interest, negative = disinterest) and a half-life
of 30 days ensures stale interests fade naturally.

Thread-safe via a module-level lock on the JSON backing store.
"""

import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

from jarvis_engine._shared import atomic_write_json
from jarvis_engine._shared import now_iso as _now_iso

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_HALF_LIFE_DAYS = 30.0


class InterestEntry(TypedDict):
    """Single topic entry in the interests store."""

    score: float
    count: int
    last_seen: str


class InterestLearner:
    """Learns and decays user interest topics for news personalization."""

    def __init__(self, root: Path) -> None:
        from jarvis_engine._shared import runtime_dir

        self._path = runtime_dir(root) / "interests.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, InterestEntry]:
        from jarvis_engine._shared import load_json_file

        return load_json_file(self._path, {}, expected_type=dict)

    def _save(self, data: dict) -> None:
        try:
            atomic_write_json(self._path, data, secure=False)
        except OSError:
            logger.warning("Failed to save interests file %s", self._path)

    def record_interest(self, topic: str, *, weight: float = 0.3) -> None:
        """Record an interest signal for a topic.

        Args:
            topic: The interest topic (case-insensitive, trimmed).
            weight: Positive for interest, negative for disinterest.
        """
        topic = topic.lower().strip()
        with _LOCK:
            data = self._load()
            entry = data.get(topic, {"score": 0.0, "count": 0, "last_seen": ""})
            entry["score"] = max(0.0, entry["score"] + weight)
            entry["count"] = entry.get("count", 0) + 1
            entry["last_seen"] = _now_iso()
            data[topic] = entry
            self._save(data)

    def get_profile(self, *, top_n: int = 20) -> dict[str, float]:
        """Return the top-N interests with exponential decay applied.

        Interests that have decayed below 0.01 are excluded.
        """
        with _LOCK:
            data = self._load()
        now = datetime.now(timezone.utc)
        result: dict[str, float] = {}
        for topic, entry in data.items():
            last = entry.get("last_seen", "")
            if not last:
                continue
            try:
                dt = datetime.fromisoformat(last)
                days_ago = (now - dt).total_seconds() / 86400
            except (ValueError, TypeError):
                days_ago = 90.0
            decayed = entry["score"] * math.pow(0.5, days_ago / _HALF_LIFE_DAYS)
            if decayed > 0.01:
                result[topic] = round(decayed, 3)
        return dict(sorted(result.items(), key=lambda x: -x[1])[:top_n])

    def _decay_all(self, *, days: int) -> None:
        """Test helper: simulate passage of time by shifting last_seen back."""
        with _LOCK:
            data = self._load()
            for entry in data.values():
                if entry.get("last_seen"):
                    try:
                        dt = datetime.fromisoformat(entry["last_seen"])
                    except (ValueError, TypeError):
                        logger.debug(
                            "Failed to parse last_seen timestamp in _decay_all"
                        )
                        continue
                    entry["last_seen"] = (dt - timedelta(days=days)).isoformat()
            self._save(data)
