"""
Cloud spend ledger.

`Identity.yaml`'s budget policy has been declarative-only since it was
written: `select_model()` accepts a `budget_exhausted` flag and applies
`low_balance_behavior: force-local` when it is set, but nothing in the
repository ever computed that flag -- every caller passed the default
`False`. The monthly cap was therefore unenforced.

This module is the missing half: a small, separate SQLite-backed ledger of
what cloud generation has actually cost this month, so `budget_exhausted`
can be answered from recorded spend rather than assumed.

Deliberately narrow, following the pattern the rest of the repository uses
for concern-owned schema (see `bartholomew/orchestrator/safety/
governance_store.py`): it owns its own table, creates it with
CREATE TABLE IF NOT EXISTS, and knows nothing about memory, governance or
the kernel. It is not wired into MemoryStore's schema.

Synchronous by design, and called only from the model-routing path, which
already runs off the event loop via `asyncio.to_thread`/`run_off_loop` at
its callers (see `bartholomew_api_bridge_v0_1/services/api/app.py`).

**Costs recorded here are estimates, not billing truth.** They are computed
from a local price table against token counts the provider reports. The
provider's own invoice is authoritative; this ledger exists to stop a
runaway loop from quietly spending a month's budget in an afternoon, not to
reconcile accounts.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

# USD per million tokens, (input, output).
#
# Local prices for enforcing Identity.yaml's own cap -- NOT authoritative
# billing data, and they will drift. Verify against the provider's current
# pricing page before relying on the absolute numbers; an unknown model
# falls back to the most expensive entry so an unpriced model can never be
# treated as free.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_FALLBACK_PRICE = max(PRICE_PER_MTOK.values(), key=lambda p: p[1])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    period TEXT NOT NULL,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_usd REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_cloud_spend_period ON cloud_spend(period);
"""


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the USD cost of one generation. See the module docstring's
    caveat -- this is a local estimate, not billing truth."""
    price_in, price_out = PRICE_PER_MTOK.get(model, _FALLBACK_PRICE)
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def current_period(now: datetime | None = None) -> str:
    """The billing period key. Calendar month in UTC -- deliberately simple;
    Identity.yaml's cap is `monthly_cloud_spend_usd`, with no stated billing
    anniversary to align to."""
    moment = now or datetime.now(timezone.utc)
    return moment.strftime("%Y-%m")


@dataclass(frozen=True)
class SpendSnapshot:
    """What has been spent this period, and whether that exhausts the cap."""

    period: str
    spent_usd: float
    cap_usd: float | None
    exhausted: bool

    @property
    def remaining_usd(self) -> float | None:
        if self.cap_usd is None:
            return None
        return max(0.0, self.cap_usd - self.spent_usd)


class BudgetLedger:
    """Records cloud generation cost and answers whether the cap is spent."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        # Same WAL/busy_timeout posture the rest of the repository uses for
        # SQLite opened outside MemoryStore -- see the notify skill fix in
        # 79042b1, which was a bug precisely because it omitted these.
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def record(
        self,
        *,
        backend: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Record one generation's usage. Returns the estimated cost."""
        cost = estimate_usd(model, input_tokens, output_tokens)
        self.ensure_schema()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cloud_spend "
                "(ts, period, backend, model, input_tokens, output_tokens, estimated_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    current_period(),
                    backend,
                    model,
                    int(input_tokens),
                    int(output_tokens),
                    cost,
                ),
            )
        return cost

    def spent_this_period(self) -> float:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(estimated_usd), 0.0) FROM cloud_spend WHERE period = ?",
                (current_period(),),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def snapshot(self, cap_usd: float | None) -> SpendSnapshot:
        """Current spend against the cap.

        Fails **closed** on any ledger error: if spend cannot be read, the
        budget is reported exhausted rather than assumed available, so a
        broken ledger degrades to local-only rather than to uncapped cloud
        spend. That direction is deliberate -- the failure that costs the
        user money is the one worth defaulting against.
        """
        period = current_period()
        if cap_usd is None:
            return SpendSnapshot(period=period, spent_usd=0.0, cap_usd=None, exhausted=False)
        try:
            spent = self.spent_this_period()
        except Exception:
            return SpendSnapshot(
                period=period,
                spent_usd=float(cap_usd),
                cap_usd=float(cap_usd),
                exhausted=True,
            )
        return SpendSnapshot(
            period=period,
            spent_usd=spent,
            cap_usd=float(cap_usd),
            exhausted=spent >= float(cap_usd),
        )


def resolve_ledger_path() -> str:
    """The ledger lives beside the rest of the runtime's state."""
    return os.getenv("BARTH_DB_PATH", os.path.join("data", "barth.db"))
