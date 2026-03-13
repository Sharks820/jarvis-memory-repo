"""Budget enforcement and cost governance for LLM gateway.

Prevents runaway costs by tracking cumulative spend in SQLite and enforcing
configurable daily and monthly caps.  Emits cost alerts at 50%/75%/90%
thresholds and supports cost-aware provider routing.

Thread-safe: all reads and writes go through a single connection protected
by a threading lock.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis_engine._compat import UTC
from jarvis_engine.gateway.pricing import calculate_cost

if TYPE_CHECKING:
    from jarvis_engine.gateway.audit import GatewayAudit

logger = logging.getLogger(__name__)


# Cost-per-1K-tokens for cost-aware routing

#: Estimated cost per 1K input+output tokens (blended) by provider.
#: Lower is cheaper. Used to prefer cheaper providers for routine queries.
PROVIDER_COST_PER_1K: dict[str, float] = {
    "ollama": 0.0,  # free (local)
    "groq": 0.002,  # free tier / very cheap
    "zai": 0.0004,  # GLM free tier
    "mistral": 0.001,  # cheap
    "anthropic": 0.009,  # most expensive
    # CLI providers use subscription, effectively free per-call
    "claude-cli": 0.0,
    "codex-cli": 0.0,
    "gemini-cli": 0.0,
    "kimi-cli": 0.0,
}


class BudgetExceededError(Exception):
    """Raised when a request would exceed the configured budget cap."""

    def __init__(self, message: str, *, period: str, spent: float, cap: float) -> None:
        super().__init__(message)
        self.period = period
        self.spent = spent
        self.cap = cap


# Alert thresholds

_ALERT_THRESHOLDS: tuple[float, ...] = (0.50, 0.75, 0.90)


@dataclass
class BudgetStatus:
    """Current budget utilisation snapshot."""

    daily_spent: float = 0.0
    daily_cap: float = 5.0
    daily_pct: float = 0.0
    monthly_spent: float = 0.0
    monthly_cap: float = 50.0
    monthly_pct: float = 0.0
    budget_ok: bool = True


# BudgetEnforcer


class BudgetEnforcer:
    """Enforces daily and monthly cost caps with SQLite-backed tracking.

    Uses a dedicated ``budget_tracking`` table to store cumulative costs
    per day.  Checks are fast (single SQL query per call).

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Can share the same file as
        :class:`CostTracker` -- schema is additive.
    daily_cap:
        Maximum daily spend in USD.  Override with env var
        ``JARVIS_BUDGET_DAILY_CAP``.
    monthly_cap:
        Maximum monthly spend in USD.  Override with env var
        ``JARVIS_BUDGET_MONTHLY_CAP``.
    audit:
        Optional :class:`GatewayAudit` instance for logging alerts.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        daily_cap: float = 5.0,
        monthly_cap: float = 50.0,
        audit: "GatewayAudit | None" = None,
    ) -> None:
        self._db_path = db_path
        self._daily_cap = _env_float("JARVIS_BUDGET_DAILY_CAP", daily_cap)
        self._monthly_cap = _env_float("JARVIS_BUDGET_MONTHLY_CAP", monthly_cap)
        self._audit = audit
        self._lock = threading.Lock()
        self._closed = False

        # Track which alert thresholds have already fired (period -> set of pcts)
        self._fired_alerts: dict[str, set[float]] = {
            "daily": set(),
            "monthly": set(),
        }
        # Track the current day/month so we can reset fired alerts on rollover
        self._last_day: str = ""
        self._last_month: str = ""

        # In-memory running totals to avoid full-table SUM() on every LLM call.
        # None means "not yet initialised" — first access triggers a DB query.
        self._cached_daily: float | None = None
        self._cached_monthly: float | None = None
        self._cached_day: str = ""
        self._cached_month: str = ""

        from jarvis_engine._db_pragmas import connect_db

        self._db = connect_db(db_path, check_same_thread=False)
        self._init_schema()

    # -- schema -------------------------------------------------------------

    def _init_schema(self) -> None:
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS budget_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                day_key TEXT NOT NULL,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                model TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_budget_day ON budget_tracking(day_key);
        """)
        self._db.commit()

    # -- internal helpers ---------------------------------------------------

    def _today(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _this_month(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def _daily_spend(self) -> float:
        """Sum of costs for the current UTC day.

        Returns the cached running total when the day has not rolled over,
        otherwise re-queries SQLite to get an accurate baseline.
        Caller MUST hold ``_lock``.
        """
        today = self._today()
        if today != self._cached_day or self._cached_daily is None:
            row = self._db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM budget_tracking WHERE day_key = ?",
                (today,),
            ).fetchone()
            self._cached_daily = float(row["total"])
            self._cached_day = today
        return self._cached_daily

    def _monthly_spend(self) -> float:
        """Sum of costs for the current UTC month.

        Returns the cached running total when the month has not rolled over,
        otherwise re-queries SQLite to get an accurate baseline.
        Caller MUST hold ``_lock``.
        """
        month = self._this_month()
        if month != self._cached_month or self._cached_monthly is None:
            prefix = month
            row = self._db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM budget_tracking WHERE day_key LIKE ?",
                (prefix + "%",),
            ).fetchone()
            self._cached_monthly = float(row["total"])
            self._cached_month = month
        return self._cached_monthly

    def _reset_alerts_if_needed(self) -> None:
        """Clear fired alerts when the day or month rolls over."""
        today = self._today()
        month = self._this_month()
        if today != self._last_day:
            self._fired_alerts["daily"] = set()
            self._last_day = today
        if month != self._last_month:
            self._fired_alerts["monthly"] = set()
            self._last_month = month

    def _emit_alerts(self, daily_spent: float, monthly_spent: float) -> None:
        """Emit cost_alert activity events at threshold crossings."""
        self._reset_alerts_if_needed()
        self._check_threshold("daily", daily_spent, self._daily_cap)
        self._check_threshold("monthly", monthly_spent, self._monthly_cap)

    def _check_threshold(self, period: str, spent: float, cap: float) -> None:
        if cap <= 0:
            return
        pct = spent / cap
        for threshold in _ALERT_THRESHOLDS:
            if pct >= threshold and threshold not in self._fired_alerts[period]:
                self._fired_alerts[period].add(threshold)
                threshold_pct = int(threshold * 100)
                msg = (
                    f"Cost alert: {period} budget at {threshold_pct}% "
                    f"(${spent:.2f} / ${cap:.2f})"
                )
                logger.warning(msg)
                # Log to audit trail
                if self._audit is not None:
                    self._audit.log_decision(
                        provider="budget_enforcer",
                        model="",
                        reason=f"cost_alert:{period}:{threshold_pct}pct",
                        latency_ms=0.0,
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=spent,
                        success=True,
                    )
                # Emit activity event
                try:
                    from jarvis_engine.activity_feed import log_activity

                    log_activity(
                        "cost_alert",
                        msg,
                        {
                            "period": period,
                            "threshold_pct": threshold_pct,
                            "current_spend": round(spent, 4),
                            "budget_limit": round(cap, 2),
                        },
                    )
                except (ImportError, OSError, ValueError, TypeError) as exc:
                    logger.debug("Activity feed cost alert failed: %s", exc)

    # -- public API ---------------------------------------------------------

    def record_cost(self, cost_usd: float, model: str = "", provider: str = "") -> None:
        """Record a completed request's cost and check alert thresholds.

        Called AFTER a successful LLM completion to update the budget ledger.
        """
        if self._closed or cost_usd <= 0.0:
            return
        with self._lock:
            if self._closed:
                return
            self._db.execute(
                "INSERT INTO budget_tracking (day_key, cost_usd, model, provider) VALUES (?, ?, ?, ?)",
                (self._today(), cost_usd, model, provider),
            )
            self._db.commit()
            # Update in-memory cache incrementally instead of re-querying.
            # _daily_spend()/_monthly_spend() will return the already-updated
            # cache value (no DB round-trip) or initialise from DB on first call
            # (the committed row is already visible to those queries).
            if self._cached_daily is not None and self._cached_day == self._today():
                self._cached_daily += cost_usd
            if self._cached_monthly is not None and self._cached_month == self._this_month():
                self._cached_monthly += cost_usd
            self._emit_alerts(self._daily_spend(), self._monthly_spend())

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        return calculate_cost(model, input_tokens, output_tokens)

    def check_budget(self, estimated_cost: float = 0.0) -> None:
        """Raise BudgetExceededError if adding estimated_cost would exceed caps.

        Args:
            estimated_cost: Estimated cost for the upcoming request.
        """
        if self._closed:
            return
        with self._lock:
            if self._closed:
                return
            daily = self._daily_spend()
            monthly = self._monthly_spend()

        if self._daily_cap > 0 and (daily + estimated_cost) > self._daily_cap:
            raise BudgetExceededError(
                f"Daily budget exceeded: ${daily:.2f} + ${estimated_cost:.4f} > ${self._daily_cap:.2f}",
                period="daily",
                spent=daily,
                cap=self._daily_cap,
            )
        if self._monthly_cap > 0 and (monthly + estimated_cost) > self._monthly_cap:
            raise BudgetExceededError(
                f"Monthly budget exceeded: ${monthly:.2f} + ${estimated_cost:.4f} > ${self._monthly_cap:.2f}",
                period="monthly",
                spent=monthly,
                cap=self._monthly_cap,
            )

    def status(self) -> BudgetStatus:
        """Return current budget utilisation snapshot."""
        if self._closed:
            return BudgetStatus()
        with self._lock:
            if self._closed:
                return BudgetStatus()
            daily = self._daily_spend()
            monthly = self._monthly_spend()
        daily_pct = (daily / self._daily_cap * 100) if self._daily_cap > 0 else 0.0
        monthly_pct = (
            (monthly / self._monthly_cap * 100) if self._monthly_cap > 0 else 0.0
        )
        return BudgetStatus(
            daily_spent=round(daily, 6),
            daily_cap=self._daily_cap,
            daily_pct=round(daily_pct, 1),
            monthly_spent=round(monthly, 6),
            monthly_cap=self._monthly_cap,
            monthly_pct=round(monthly_pct, 1),
            budget_ok=(daily_pct < 100 and monthly_pct < 100),
        )

    def rank_providers_by_cost(self, candidates: list[str]) -> list[str]:
        """Sort provider names by estimated cost (cheapest first).

        Args:
            candidates: List of provider names (e.g. ["groq", "anthropic", "ollama"]).

        Returns:
            Same list sorted by ascending cost.
        """
        return sorted(candidates, key=lambda p: PROVIDER_COST_PER_1K.get(p, 999.0))

    def close(self) -> None:
        """Close the database connection."""
        if self._closed:
            return
        self._closed = True
        try:
            with self._lock:
                self._db.close()
        except (OSError, RuntimeError) as exc:
            logger.debug("Failed to close BudgetEnforcer DB: %s", exc)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001
            logger.debug("BudgetEnforcer.__del__ cleanup failed")

    def __enter__(self) -> "BudgetEnforcer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# Helpers


def _env_float(key: str, default: float) -> float:
    """Read a float from environment, falling back to default."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default
