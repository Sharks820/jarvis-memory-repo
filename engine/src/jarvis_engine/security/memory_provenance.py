"""Memory provenance tracking -- Wave 13 security hardening.

Ensures memory integrity by attaching provenance metadata to every
memory record: source, trust level, ingestion timestamp, and
verification status.
"""

from __future__ import annotations

import threading
from typing import Any

from jarvis_engine._shared import now_iso as _now_iso

# ---------------------------------------------------------------------------
# Trust level constants
# ---------------------------------------------------------------------------

OWNER_INPUT = "OWNER_INPUT"
VERIFIED_EXTERNAL = "VERIFIED_EXTERNAL"
UNVERIFIED_EXTERNAL = "UNVERIFIED_EXTERNAL"
QUARANTINED = "QUARANTINED"

_VALID_TRUST_LEVELS = {OWNER_INPUT, VERIFIED_EXTERNAL, UNVERIFIED_EXTERNAL, QUARANTINED}


class MemoryProvenance:
    """In-memory provenance tracker for memory records.

    Every memory record gets a provenance tag with:
    - ``source``: where the data came from
    - ``trust_level``: one of ``OWNER_INPUT``, ``VERIFIED_EXTERNAL``,
      ``UNVERIFIED_EXTERNAL``, or ``QUARANTINED``
    - ``ingestion_timestamp``: ISO-8601 UTC timestamp
    - ``verification_status``: ``"pending"`` or ``"verified"``
    - ``quarantine_reason``: reason string (only for quarantined records)

    New memories from LLM interactions start as ``UNVERIFIED_EXTERNAL``
    and are promoted to ``VERIFIED_EXTERNAL`` after owner confirmation.
    ``QUARANTINED`` records are those flagged for contradictions,
    injection payloads, or output scanner alerts.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # record_hash -> provenance dict
        self._records: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Tagging
    # ------------------------------------------------------------------

    def tag_record(
        self,
        record_hash: str,
        source: str,
        trust_level: str = UNVERIFIED_EXTERNAL,
    ) -> dict[str, Any]:
        """Add or update provenance for a memory record.

        Parameters
        ----------
        record_hash:
            Unique identifier (hash) of the memory record.
        source:
            Description of where the data came from.
        trust_level:
            One of the module-level trust constants.

        Returns
        -------
        The provenance dict stored for *record_hash*.
        """
        if trust_level not in _VALID_TRUST_LEVELS:
            raise ValueError(
                f"Invalid trust_level {trust_level!r}. "
                f"Must be one of {_VALID_TRUST_LEVELS}"
            )

        verification = (
            "verified" if trust_level in (OWNER_INPUT, VERIFIED_EXTERNAL) else "pending"
        )

        prov: dict[str, Any] = {
            "record_hash": record_hash,
            "source": source,
            "trust_level": trust_level,
            "ingestion_timestamp": _now_iso(),
            "verification_status": verification,
            "quarantine_reason": "",
        }
        with self._lock:
            self._records[record_hash] = prov
            # Evict oldest entries if capacity exceeded
            if len(self._records) > 50000:
                # Use heapq.nsmallest for O(n) eviction instead of O(n log n) full sort
                import heapq

                oldest_keys = heapq.nsmallest(
                    10000,
                    self._records,
                    key=lambda k: self._records[k].get("ingestion_timestamp", ""),
                )
                for k in oldest_keys:
                    del self._records[k]
        return prov

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_provenance(self, record_hash: str) -> dict[str, Any] | None:
        """Return a copy of the provenance dict for *record_hash*, or ``None``."""
        with self._lock:
            prov = self._records.get(record_hash)
            return dict(prov) if prov is not None else None

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def promote(self, record_hash: str) -> bool:
        """Promote a record from ``UNVERIFIED_EXTERNAL`` to ``VERIFIED_EXTERNAL``.

        Returns ``True`` if promotion succeeded, ``False`` if the record
        does not exist or is not in the promotable state.
        """
        with self._lock:
            prov = self._records.get(record_hash)
            if prov is None:
                return False
            if prov["trust_level"] != UNVERIFIED_EXTERNAL:
                return False
            prov["trust_level"] = VERIFIED_EXTERNAL
            prov["verification_status"] = "verified"
            return True

    def quarantine(self, record_hash: str, reason: str) -> bool:
        """Move a record to ``QUARANTINED`` with the given *reason*.

        Returns ``True`` if quarantine succeeded, ``False`` if the record
        does not exist.
        """
        with self._lock:
            prov = self._records.get(record_hash)
            if prov is None:
                return False
            prov["trust_level"] = QUARANTINED
            prov["verification_status"] = "pending"
            prov["quarantine_reason"] = reason
            return True

    # ------------------------------------------------------------------
    # Quarantine management
    # ------------------------------------------------------------------

    def get_quarantined(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to *limit* quarantined records (copies, not references)."""
        results: list[dict[str, Any]] = []
        with self._lock:
            for prov in self._records.values():
                if prov["trust_level"] == QUARANTINED:
                    results.append(dict(prov))
                    if len(results) >= limit:
                        break
        return results

    def purge_quarantined(self, record_hash: str) -> bool:
        """Remove a quarantined record entirely.

        Returns ``True`` if the record was quarantined and removed,
        ``False`` otherwise.
        """
        with self._lock:
            prov = self._records.get(record_hash)
            if prov is None:
                return False
            if prov["trust_level"] != QUARANTINED:
                return False
            del self._records[record_hash]
            return True

    def approve_quarantined(self, record_hash: str) -> bool:
        """Move a quarantined record to ``VERIFIED_EXTERNAL``.

        Returns ``True`` if approval succeeded, ``False`` if the record
        does not exist or is not quarantined.
        """
        with self._lock:
            prov = self._records.get(record_hash)
            if prov is None:
                return False
            if prov["trust_level"] != QUARANTINED:
                return False
            prov["trust_level"] = VERIFIED_EXTERNAL
            prov["verification_status"] = "verified"
            prov["quarantine_reason"] = ""
            return True
