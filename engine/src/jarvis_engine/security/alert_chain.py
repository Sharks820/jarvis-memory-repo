"""Alert chain with escalating notification levels and dedup.

Sends graduated alerts from background-log-only (level 1) through
URGENT-to-all-devices with audible alarm (level 5).  Deduplicates
repeat alerts from the same source IP within a 5-minute window.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis_engine._protocols import ForensicLoggerProtocol

logger = logging.getLogger(__name__)

# Channel mapping per alert level
_LEVEL_CHANNELS: dict[int, str] = {
    1: "BACKGROUND",
    2: "ROUTINE",
    3: "IMPORTANT",
    4: "URGENT",
    5: "URGENT",
}

# Dedup window in seconds
_DEDUP_WINDOW_S = 300  # 5 minutes


class AlertChain:
    """Escalating alert notifications with dedup.

    Parameters
    ----------
    forensic_logger:
        Optional object with a ``log_event(dict)`` method for logging
        all alert dispatches.
    """

    def __init__(self, forensic_logger: ForensicLoggerProtocol | None = None) -> None:
        self._forensic_logger = forensic_logger
        self._lock = threading.Lock()
        self._alerts: deque[dict] = deque(maxlen=10000)
        # Dedup tracking: (source_ip, level) -> last_alert_timestamp
        self._dedup_cache: dict[tuple[str | None, int], float] = {}
        self._dispatch_callbacks: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_alert(
        self,
        level: int,
        summary: str,
        evidence: str | None = None,
        containment_action: str | None = None,
        source_ip: str | None = None,
    ) -> dict:
        """Dispatch an alert at the given *level*.

        Returns a dict describing the alert sent (or dedup status).
        """
        level = max(1, min(5, int(level)))
        channel = _LEVEL_CHANNELS[level]
        now = time.time()

        with self._lock:
            # Check dedup (pass pre-computed timestamp for consistency)
            deduped = self._should_dedup(source_ip, level, now)

            alert_record = {
                "timestamp": now,
                "level": level,
                "channel": channel,
                "summary": summary,
                "evidence": evidence,
                "containment_action": containment_action,
                "source_ip": source_ip,
                "deduped": deduped,
            }

            self._alerts.append(alert_record)

            if not deduped:
                # Update dedup cache
                self._dedup_cache[(source_ip, level)] = now

            # Periodic cleanup: evict stale entries when cache grows large
            if len(self._dedup_cache) > 1000:
                stale = [k for k, v in self._dedup_cache.items() if now - v > _DEDUP_WINDOW_S]
                for k in stale:
                    del self._dedup_cache[k]

        if deduped:
            logger.debug(
                "Alert deduped (level=%d, ip=%s): %s", level, source_ip, summary
            )
            return alert_record

        # Dispatch based on level
        self._dispatch(level, channel, summary, evidence, containment_action)

        # Log to forensic logger
        if self._forensic_logger is not None:
            try:
                self._forensic_logger.log_event({
                    "event_type": "alert_dispatched",
                    "level": level,
                    "channel": channel,
                    "summary": summary,
                    "source_ip": source_ip,
                    "deduped": False,
                })
            except (OSError, ValueError, RuntimeError) as exc:
                logger.warning("Failed to write forensic log for alert dispatch: %s", exc)

        # Invoke registered dispatch callbacks (defensive copy for thread safety)
        with self._lock:
            callbacks = list(self._dispatch_callbacks)
        for cb in callbacks:
            try:
                cb(level, summary, evidence)
            except (OSError, ValueError, RuntimeError) as exc:
                logger.warning("Alert dispatch callback failed: %s", exc)

        logger.info(
            "Alert dispatched (level=%d, channel=%s): %s", level, channel, summary
        )

        return alert_record

    def get_alert_history(self, limit: int = 50) -> list[dict]:
        """Return the most recent *limit* alerts (newest first)."""
        with self._lock:
            items = list(self._alerts)
            return list(reversed(items[-limit:]))

    # ------------------------------------------------------------------
    # Dedup logic
    # ------------------------------------------------------------------

    def _should_dedup(self, source_ip: str | None, level: int, now: float | None = None) -> bool:
        """Check if an alert from *source_ip* at *level* should be deduped.

        Returns True if the same (source_ip, level) was alerted within
        the last 5 minutes.
        """
        if now is None:
            now = time.time()
        key = (source_ip, level)
        last_time = self._dedup_cache.get(key)
        if last_time is None:
            return False
        return (now - last_time) < _DEDUP_WINDOW_S

    # ------------------------------------------------------------------
    # Dispatch internals
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        level: int,
        channel: str,
        summary: str,
        evidence: str | None,
        containment_action: str | None,
    ) -> None:
        """Route alert to appropriate notification channels.

        In production this would call the mobile API, desktop widget,
        TTS engine, and email gateway.  Here we log the intent.
        """
        if level == 1:
            logger.info("[BACKGROUND] %s", summary)

        elif level == 2:
            logger.info("[ROUTINE notification] %s", summary)

        elif level == 3:
            logger.info("[IMPORTANT notification] %s", summary)

        elif level == 4:
            logger.warning(
                "[URGENT notification + widget flash] %s (evidence: %s, action: %s)",
                summary,
                evidence,
                containment_action,
            )

        elif level == 5:
            logger.critical(
                "[URGENT ALL DEVICES + ALARM + EMAIL] %s "
                "(evidence: %s, action: %s)",
                summary,
                evidence,
                containment_action,
            )
