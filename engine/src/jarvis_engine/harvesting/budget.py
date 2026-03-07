"""Budget manager for per-provider harvest cost enforcement.

Uses the same SQLite database as CostTracker (WAL mode + threading.Lock)
to track and enforce daily/monthly spend limits per harvesting provider.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class ProviderSpend(TypedDict):
    """Shape of a single provider entry in spend summary."""

    provider: str
    total_cost_usd: float
    total_requests: int


class SpendSummary(TypedDict):
    """Return shape of ``BudgetManager.get_spend_summary``."""

    period_days: int
    providers: list[ProviderSpend]
    total_cost_usd: float


# Default budget limits (inserted on first schema init only)
_DEFAULT_BUDGETS: list[tuple[str, str, float, int]] = [
    # (provider, period, limit_usd, limit_requests)
    ("minimax", "daily", 1.00, 0),
    ("minimax", "monthly", 10.00, 0),
    ("kimi", "daily", 1.00, 0),
    ("kimi", "monthly", 10.00, 0),
    ("gemini", "daily", 0.00, 50),
    ("kimi_nvidia", "daily", 0.00, 100),
]


class BudgetManager:
    """SQLite-backed per-provider budget enforcement for knowledge harvesting.

    Shares the same database file as ``CostTracker`` and ``MemoryEngine``.
    Creates ``harvest_budgets`` and ``harvest_spend`` tables if they do not
    already exist.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._write_lock = threading.Lock()
        self._closed = False

        from jarvis_engine._db_pragmas import connect_db
        self._db = connect_db(db_path, check_same_thread=False)

        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables, indexes, and insert default budgets if table is empty."""
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS harvest_budgets (
                provider TEXT NOT NULL,
                period TEXT NOT NULL,
                limit_usd REAL NOT NULL,
                limit_requests INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (provider, period)
            );

            CREATE TABLE IF NOT EXISTS harvest_spend (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                cost_usd REAL NOT NULL DEFAULT 0.0,
                request_count INTEGER NOT NULL DEFAULT 1,
                topic TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_harvest_spend_provider_ts
                ON harvest_spend(provider, ts);
        """)
        self._db.commit()

        # Insert default budgets only if the table is empty
        row = self._db.execute("SELECT COUNT(*) AS cnt FROM harvest_budgets").fetchone()
        if row["cnt"] == 0:
            for provider, period, limit_usd, limit_requests in _DEFAULT_BUDGETS:
                self._db.execute(
                    "INSERT OR IGNORE INTO harvest_budgets (provider, period, limit_usd, limit_requests) "
                    "VALUES (?, ?, ?, ?)",
                    (provider, period, limit_usd, limit_requests),
                )
            self._db.commit()

    _VALID_PERIODS = {"daily", "monthly"}

    def set_budget(
        self,
        provider: str,
        period: str,
        limit_usd: float,
        limit_requests: int = 0,
    ) -> None:
        """Set or update budget limit for a provider/period combination."""
        if period not in self._VALID_PERIODS:
            raise ValueError(f"Invalid period {period!r}; must be one of {self._VALID_PERIODS}")
        if limit_usd < 0:
            raise ValueError(f"limit_usd must be >= 0, got {limit_usd}")
        if limit_requests < 0:
            raise ValueError(f"limit_requests must be >= 0, got {limit_requests}")
        with self._write_lock:
            self._db.execute(
                "INSERT OR REPLACE INTO harvest_budgets "
                "(provider, period, limit_usd, limit_requests) VALUES (?, ?, ?, ?)",
                (provider, period, limit_usd, limit_requests),
            )
            self._db.commit()

    def can_spend(self, provider: str) -> bool:
        """Check whether the provider is within both daily and monthly budget.

        Returns True if no budget is configured for the provider.
        Checks both USD cost limits and request count limits.
        Uses write lock to serialize with record_spend and prevent TOCTOU races.
        """
        if self._closed:
            return False
        with self._write_lock:
            return self._can_spend_locked(provider)

    def _can_spend_locked(self, provider: str) -> bool:
        """Inner can_spend check; must be called with _write_lock held."""
        budgets = self._db.execute(
            "SELECT period, limit_usd, limit_requests FROM harvest_budgets WHERE provider = ?",
            (provider,),
        ).fetchall()

        if not budgets:
            return True

        for budget in budgets:
            period = budget["period"]
            limit_usd = budget["limit_usd"]
            limit_requests = budget["limit_requests"]

            if period == "daily":
                where_clause = "provider = ? AND ts >= date('now')"
            elif period == "monthly":
                where_clause = "provider = ? AND ts >= date('now', 'start of month')"
            else:
                continue

            row = self._db.execute(
                f"SELECT COALESCE(SUM(cost_usd), 0.0) AS total_cost, "
                f"COALESCE(SUM(request_count), 0) AS total_requests "
                f"FROM harvest_spend WHERE {where_clause}",
                (provider,),
            ).fetchone()

            total_cost = row["total_cost"]
            total_requests = row["total_requests"]

            # Check USD limit (if limit_usd > 0)
            if limit_usd > 0 and total_cost >= limit_usd:
                return False

            # Check request count limit (if limit_requests > 0)
            if limit_requests > 0 and total_requests >= limit_requests:
                return False

        return True

    def record_spend(
        self,
        provider: str,
        cost_usd: float,
        topic: str = "",
    ) -> None:
        """Record a spend event for a provider."""
        if self._closed:
            return
        with self._write_lock:
            self._db.execute(
                "INSERT INTO harvest_spend (provider, cost_usd, topic) VALUES (?, ?, ?)",
                (provider, cost_usd, topic),
            )
            self._db.commit()

    def get_spend_summary(
        self,
        provider: str | None = None,
        days: int = 30,
    ) -> SpendSummary:
        """Return per-provider spend breakdown for the last N days.

        Args:
            provider: Optional provider name to filter. None = all providers.
            days: Number of days to look back (clamped to >= 0).

        Returns:
            Dict with providers list and total spend.
        """
        if self._closed:
            return {"period_days": days, "providers": [], "total_cost_usd": 0.0}
        days = max(0, days)
        with self._write_lock:
            if provider:
                cur = self._db.execute(
                    "SELECT provider, "
                    "COALESCE(SUM(cost_usd), 0.0) AS total_cost, "
                    "COALESCE(SUM(request_count), 0) AS total_requests "
                    "FROM harvest_spend "
                    "WHERE provider = ? AND ts >= datetime('now', ?) "
                    "GROUP BY provider",
                    (provider, f"-{days} days"),
                )
            else:
                cur = self._db.execute(
                    "SELECT provider, "
                    "COALESCE(SUM(cost_usd), 0.0) AS total_cost, "
                    "COALESCE(SUM(request_count), 0) AS total_requests "
                    "FROM harvest_spend "
                    "WHERE ts >= datetime('now', ?) "
                    "GROUP BY provider "
                    "ORDER BY total_cost DESC",
                    (f"-{days} days",),
                )

            providers = []
            total_cost = 0.0
            for row in cur.fetchall():
                row_cost = row["total_cost"] or 0.0
                providers.append({
                    "provider": row["provider"],
                    "total_cost_usd": row_cost,
                    "total_requests": row["total_requests"] or 0,
                })
                total_cost += row_cost

        return {
            "period_days": days,
            "providers": providers,
            "total_cost_usd": total_cost,
        }

    def close(self) -> None:
        """Close the database connection."""
        with self._write_lock:
            self._closed = True
            try:
                self._db.close()
            except (OSError, RuntimeError) as exc:
                logger.warning("Failed to close BudgetManager database connection: %s", exc)

    def __enter__(self) -> "BudgetManager":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception as exc:  # noqa: BLE001 -- __del__: interpreter may be shutting down
            logger.debug("__del__ cleanup failed: %s", exc)
