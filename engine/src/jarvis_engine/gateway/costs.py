"""Cost tracking for LLM completions via SQLite.

Logs every LLM completion with model, tokens, and calculated USD cost.
Provides per-model cost summaries over configurable time periods.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from jarvis_engine.gateway.pricing import calculate_cost

logger = logging.getLogger(__name__)


class CostTracker:
    """SQLite-backed cost tracker for LLM query costs.

    NOTE: This class uses a single connection for both reads and writes,
    protected by a threading lock.  External code that opens a second
    connection to the same DB file (e.g. for read-only dashboards) must
    also set ``PRAGMA busy_timeout=5000`` to avoid SQLITE_BUSY errors
    under concurrent access (dual-connection pattern).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._write_lock = threading.Lock()

        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")

        self._init_schema()

    def _init_schema(self) -> None:
        """Create query_costs table and indexes if they don't exist."""
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS query_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                route_reason TEXT NOT NULL DEFAULT '',
                fallback_used INTEGER NOT NULL DEFAULT 0,
                query_hash TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_query_costs_ts ON query_costs(ts);
            CREATE INDEX IF NOT EXISTS idx_query_costs_model ON query_costs(model);
        """)
        self._db.commit()

    def log(
        self,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None = None,
        route_reason: str = "",
        fallback_used: bool = False,
        query_hash: str = "",
    ) -> None:
        """Log a completion cost to the database.

        If cost_usd is None, automatically calculates from pricing table.
        """
        if cost_usd is None:
            cost_usd = calculate_cost(model, input_tokens, output_tokens)

        with self._write_lock:
            self._db.execute(
                """
                INSERT INTO query_costs
                    (model, provider, input_tokens, output_tokens, cost_usd,
                     route_reason, fallback_used, query_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model,
                    provider,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    route_reason,
                    1 if fallback_used else 0,
                    query_hash,
                ),
            )
            self._db.commit()

    def summary(self, days: int = 30) -> dict:
        """Return per-model cost breakdown for the last N days.

        Returns dict with:
        - period_days: int
        - models: list of dicts with model, count, input_tokens, output_tokens, cost_usd
        - total_cost_usd: float
        """
        cur = self._db.execute(
            """
            SELECT
                model,
                COUNT(*) as count,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(cost_usd) as total_cost
            FROM query_costs
            WHERE ts >= datetime('now', ?)
            GROUP BY model
            ORDER BY total_cost DESC
            """,
            (f"-{days} days",),
        )

        models = []
        total_cost = 0.0
        for row in cur.fetchall():
            row_cost = row["total_cost"] or 0.0
            models.append({
                "model": row["model"],
                "count": row["count"],
                "input_tokens": row["total_input_tokens"] or 0,
                "output_tokens": row["total_output_tokens"] or 0,
                "cost_usd": row_cost,
            })
            total_cost += row_cost

        return {
            "period_days": days,
            "models": models,
            "total_cost_usd": total_cost,
        }

    def local_vs_cloud_summary(self, days: int = 30) -> dict:
        """Return local (ollama) vs cloud query ratio for the last N days.

        Returns dict with:
        - period_days: int
        - local_count: int
        - cloud_count: int
        - total_count: int
        - local_pct: float (rounded to 1 decimal)
        - cloud_cost_usd: float
        """
        cur = self._db.execute(
            """
            SELECT
                CASE WHEN provider = 'ollama' THEN 'local' ELSE 'cloud' END AS category,
                COUNT(*) AS cnt,
                SUM(cost_usd) AS total_cost
            FROM query_costs
            WHERE ts >= datetime('now', ?)
            GROUP BY category
            """,
            (f"-{days} days",),
        )

        local_count = 0
        cloud_count = 0
        cloud_cost = 0.0
        for row in cur.fetchall():
            cat = row["category"]
            cnt = row["cnt"] or 0
            cost = row["total_cost"] or 0.0
            if cat == "local":
                local_count = cnt
            else:
                cloud_count = cnt
                cloud_cost += cost

        total = local_count + cloud_count
        local_pct = round((local_count / total * 100) if total > 0 else 0.0, 1)

        return {
            "period_days": days,
            "local_count": local_count,
            "cloud_count": cloud_count,
            "total_count": total,
            "local_pct": local_pct,
            "cloud_cost_usd": round(cloud_cost, 6),
        }

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._db.close()
        except Exception:
            pass

    def __del__(self) -> None:
        """Best-effort close on garbage collection."""
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "CostTracker":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
