"""
Stage 5, S5.1/S5.2: the generic Initiative Engine store.

Persistence for `docs/S5_1_INITIATIVE_ENGINE_ARCHITECTURE_DESIGN.md`'s
`Initiative` object -- the single shape every future proactive behaviour
(check-in, reminder, review, next-best-action, maintenance, wellness)
instantiates instead of each getting its own feature-specific store. See
that design doc for the full lifecycle/state-machine rationale, and
`docs/S5_2_TYPED_CADENCE_DESIGN.md` for the first real consumer of this
module (`initiative_sweep`, which only exercises the `expire` transition).

Deliberately synchronous, mirroring `awaiting_response_store.py`'s own
module docstring rationale: this class is not called from the event loop
directly anywhere -- every real call site routes it through
`bartholomew.kernel.blocking_executor.run_off_loop()`.

State machine (design doc Sec 5):
    proposed --(governance denies)--------------------------------> denied [terminal]
    proposed --(governance allows)---------------------------------> approved
    approved --(quiet hours or category muted at delivery-check)---> deferred
    deferred --(delivery window reopens, still before expires_at)--> approved
    approved --(delivery capability executes)-----------------------> delivered
    delivered --(user accepts)---------------------------------------> accepted [terminal]
    delivered --(user dismisses)-------------------------------------> dismissed [terminal]
    delivered --(user snoozes)----------------------------------------> snoozed
    snoozed --(snooze due_at reached)---------------------------------> approved
    {proposed, approved, deferred, delivered, snoozed} --(expires_at)-> expired [terminal]
    {any non-terminal} --(explicit cancel)-----------------------------> cancelled [terminal]
    {any non-terminal} --(superseded by a newer initiative, same kind)-> superseded [terminal]

`propose()` resolves governance synchronously and writes the *final*
status (`approved`/`denied`) in one atomic insert -- unlike
`awaiting_response_store.py`'s `open()`, a denied Initiative is still
persisted (design doc Sec 5: "denied ... Still audited and reflected --
a denial is not silently dropped"), so `proposed` is never actually
observed as a stored status by this implementation (the design doc's own
footnote treats that as a defensive allowance for a crash mid-transition,
not an expected state). The `deferred -> approved` and `snoozed ->
approved` re-entries named in the state diagram above have no dedicated
seam transition of their own -- `deliver()` simply accepts any of
`approved`/`deferred`/`snoozed` as its pre-state, so a future delivery-
timing check (S5.3/S5.4) can call `deliver` directly once conditions are
met, without a separate "un-defer" step.

Every transition writes its state change and an `initiative_audit` row in
one transaction -- the two are never observable independently, mirroring
`GovernanceStore`/`AwaitingResponseStore`'s own atomic state+audit write.

Reserved, not implemented here (design doc Sec 13): `initiative_dependencies`
and `parent_initiative_id` are schema-only -- `propose()` records them if
supplied, but nothing in this module reads, blocks, or cascades on either.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from bartholomew.kernel.db_ctx import connect, set_wal_pragmas

VALID_STATUSES = frozenset(
    {
        "proposed",
        "denied",
        "approved",
        "deferred",
        "delivered",
        "accepted",
        "dismissed",
        "snoozed",
        "expired",
        "cancelled",
        "superseded",
    },
)
TERMINAL_STATUSES = frozenset(
    {"denied", "accepted", "dismissed", "expired", "cancelled", "superseded"}
)
VALID_PRIORITIES = frozenset({"low", "normal", "high"})
VALID_RESOLUTIONS = frozenset({"accepted", "dismissed", "snoozed"})
# Registered taxonomy (design doc Sec 7) -- fixed, not free text, so
# Identity Policy and consent can be written against stable keys.
VALID_CATEGORIES = frozenset(
    {"check_in", "reminder", "review", "next_best_action", "maintenance", "wellness"},
)

# Pre-states each transition accepts. "propose" is not listed -- it always
# creates a new row rather than advancing an existing one.
_ALLOWED_PRE_STATES: dict[str, frozenset[str]] = {
    "defer": frozenset({"approved"}),
    "deliver": frozenset({"approved", "deferred", "snoozed"}),
    "resolve": frozenset({"delivered"}),
    "expire": VALID_STATUSES - TERMINAL_STATUSES,
    "cancel": VALID_STATUSES - TERMINAL_STATUSES,
    "supersede": VALID_STATUSES - TERMINAL_STATUSES,
}


class InitiativeNotFoundError(RuntimeError):
    """Raised when a transition targets an initiative_id that doesn't exist."""


class InvalidTransitionError(RuntimeError):
    """Raised when a transition is attempted against an initiative not in
    one of that transition's allowed pre-states, or with an invalid
    parameter (bad category/priority/confidence/resolution)."""


@dataclass(frozen=True)
class Initiative:
    id: int
    kind: str
    category: str
    status: str
    priority: str
    confidence: float
    rationale: str
    payload: dict[str, Any] | None
    origin_drive: str
    parent_initiative_id: int | None
    created_at: str
    due_at: str | None
    expires_at: str | None
    governance_decision: str | None
    governance_reason: str | None
    deferred_reason: str | None
    delivered_at: str | None
    resolved_at: str | None
    resolution: str | None
    actor: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "category": self.category,
            "status": self.status,
            "priority": self.priority,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "payload": self.payload,
            "origin_drive": self.origin_drive,
            "parent_initiative_id": self.parent_initiative_id,
            "created_at": self.created_at,
            "due_at": self.due_at,
            "expires_at": self.expires_at,
            "governance_decision": self.governance_decision,
            "governance_reason": self.governance_reason,
            "deferred_reason": self.deferred_reason,
            "delivered_at": self.delivered_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
            "actor": self.actor,
        }


def ensure_schema(db_path: str) -> None:
    """Create initiatives/initiative_audit/initiative_dependencies/
    initiative_consent if missing. Safe to call every time an
    InitiativeStore is constructed."""
    conn = connect(db_path)
    try:
        set_wal_pragmas(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS initiatives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed',
                priority TEXT NOT NULL DEFAULT 'normal',
                confidence REAL NOT NULL,
                rationale TEXT NOT NULL,
                payload TEXT,
                origin_drive TEXT NOT NULL,
                parent_initiative_id INTEGER REFERENCES initiatives(id),
                created_at TEXT NOT NULL,
                due_at TEXT,
                expires_at TEXT,
                governance_decision TEXT,
                governance_reason TEXT,
                deferred_reason TEXT,
                delivered_at TEXT,
                resolved_at TEXT,
                resolution TEXT,
                actor TEXT
            )
            """,
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_initiatives_status ON initiatives(status, due_at)",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_initiatives_category ON initiatives(category, status)",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_initiatives_parent "
            "ON initiatives(parent_initiative_id)",
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS initiative_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                initiative_id INTEGER NOT NULL REFERENCES initiatives(id),
                ts TEXT NOT NULL,
                transition TEXT NOT NULL,
                actor TEXT,
                detail TEXT
            )
            """,
        )
        # Reserved (design doc Sec 13): write-only in S5.1/S5.2, nothing
        # reads or enforces this table yet.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS initiative_dependencies (
                initiative_id INTEGER NOT NULL REFERENCES initiatives(id),
                depends_on_id INTEGER NOT NULL REFERENCES initiatives(id),
                PRIMARY KEY (initiative_id, depends_on_id)
            )
            """,
        )
        # Reserved for S5.3: default-off (allowed=0) per-category consent.
        # Not consulted by this module or the S5.2 seam yet.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS initiative_consent (
                category TEXT PRIMARY KEY,
                allowed INTEGER NOT NULL DEFAULT 0,
                muted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                actor TEXT
            )
            """,
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_initiative(row: sqlite3.Row) -> Initiative:
    payload = json.loads(row["payload"]) if row["payload"] else None
    return Initiative(
        id=row["id"],
        kind=row["kind"],
        category=row["category"],
        status=row["status"],
        priority=row["priority"],
        confidence=row["confidence"],
        rationale=row["rationale"],
        payload=payload,
        origin_drive=row["origin_drive"],
        parent_initiative_id=row["parent_initiative_id"],
        created_at=row["created_at"],
        due_at=row["due_at"],
        expires_at=row["expires_at"],
        governance_decision=row["governance_decision"],
        governance_reason=row["governance_reason"],
        deferred_reason=row["deferred_reason"],
        delivered_at=row["delivered_at"],
        resolved_at=row["resolved_at"],
        resolution=row["resolution"],
        actor=row["actor"],
    )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class InitiativeStore:
    """Isolated, tested persistence for the Initiative Engine. Not itself
    governed -- see runtime_contract.py's
    run_initiative_through_runtime_contract(), the sole intended caller of
    every mutating method here (design doc's non-negotiable invariant: no
    direct store write from a route, skill, or drive bypasses the Runtime
    Contract seam)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        ensure_schema(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, initiative_id: int) -> Initiative | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM initiatives WHERE id = ?",
                (initiative_id,),
            ).fetchone()
        finally:
            conn.close()
        return _row_to_initiative(row) if row is not None else None

    def is_category_consented(self, category: str) -> bool:
        """Governance gate 3 (S5.1 design doc Sec 8): default-off per-category
        user consent. No row (the default) means not consented, same as an
        explicit `allowed=0` row -- the intended "default-OFF consent"
        behaviour. `set_category_consent()` (S5.3) is how a row comes to
        exist at all."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT allowed FROM initiative_consent WHERE category = ?",
                (category,),
            ).fetchone()
        finally:
            conn.close()
        return bool(row is not None and row["allowed"])

    def is_category_muted(self, category: str) -> bool:
        """S5.3 design doc Sec 3: functional per-category mute, the
        delivery-eligibility-check drive's gate. No row (default) means
        not muted -- distinct from consent's default-off, since a category
        with no mute preference set has simply never been muted, not
        implicitly silenced."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT muted FROM initiative_consent WHERE category = ?",
                (category,),
            ).fetchone()
        finally:
            conn.close()
        return bool(row is not None and row["muted"])

    def get_category_consent(self, category: str) -> dict[str, Any]:
        """S5.3 design doc Sec 3: the current consent/mute row for
        `category`, or the default-off baseline if none exists yet."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT category, allowed, muted, updated_at, actor "
                "FROM initiative_consent WHERE category = ?",
                (category,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {
                "category": category,
                "allowed": False,
                "muted": False,
                "updated_at": None,
                "actor": None,
            }
        return {
            "category": row["category"],
            "allowed": bool(row["allowed"]),
            "muted": bool(row["muted"]),
            "updated_at": row["updated_at"],
            "actor": row["actor"],
        }

    def list_category_consent(self) -> list[dict[str, Any]]:
        """S5.3 design doc Sec 3: every registered category's consent/mute
        state, defaulted for any category with no row yet -- the whole
        picture in one call, for a future settings UI (not built here)."""
        return [self.get_category_consent(category) for category in sorted(VALID_CATEGORIES)]

    def set_category_consent(
        self,
        category: str,
        *,
        allowed: bool | None = None,
        muted: bool | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """S5.3 design doc Sec 3/5: upsert `initiative_consent` for
        `category`. Only the field(s) explicitly passed change; the other
        stays at its current (or default-off/`False`) value. Direct,
        audited write -- not a Runtime Contract transition, per the design
        doc's Sec 5 reasoning (this isn't any single initiative's own
        lifecycle event, the same category `GovernanceStore.engage()`/
        `disengage()` fall into for Parking Brake state)."""
        if category not in VALID_CATEGORIES:
            raise InvalidTransitionError(
                f"category must be one of {sorted(VALID_CATEGORIES)}, got {category!r}",
            )
        now = _now_iso()

        # Read-then-write inside one BEGIN IMMEDIATE transaction -- a
        # partial update (e.g. only `muted` passed) must not clobber a
        # concurrent partial update to `allowed` computed from a
        # separately-read, now-stale "current" row.
        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT allowed, muted FROM initiative_consent WHERE category = ?",
                (category,),
            ).fetchone()
            current_allowed = bool(row["allowed"]) if row is not None else False
            current_muted = bool(row["muted"]) if row is not None else False
            new_allowed = current_allowed if allowed is None else allowed
            new_muted = current_muted if muted is None else muted

            conn.execute(
                "INSERT INTO initiative_consent (category, allowed, muted, updated_at, actor) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(category) DO UPDATE SET "
                "allowed = excluded.allowed, muted = excluded.muted, "
                "updated_at = excluded.updated_at, actor = excluded.actor",
                (category, int(new_allowed), int(new_muted), now, actor),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_category_consent(category)

    def list(
        self,
        status: str | None = None,
        category: str | None = None,
        limit: int = 50,
    ) -> list[Initiative]:
        conn = self._connect()
        try:
            clauses = []
            params: list[Any] = []
            if status is not None:
                clauses.append("status = ?")
                params.append(status)
            if category is not None:
                clauses.append("category = ?")
                params.append(category)
            where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM initiatives {where}ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_initiative(row) for row in rows]

    def list_expiring(self, now_ts: int) -> list[Initiative]:
        """Every non-terminal initiative whose expires_at has passed --
        initiative_sweep's (S5.2) sole query."""
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
            rows = conn.execute(
                f"SELECT * FROM initiatives "
                f"WHERE status NOT IN ({placeholders}) AND expires_at IS NOT NULL "
                f"ORDER BY expires_at ASC",
                tuple(TERMINAL_STATUSES),
            ).fetchall()
        finally:
            conn.close()
        entries = [_row_to_initiative(row) for row in rows]
        return [
            e
            for e in entries
            if _parse_iso(e.expires_at) is not None and _parse_iso(e.expires_at) <= now_ts
        ]

    def list_open_by_kind(self, kind: str) -> list[Initiative]:
        """Every non-terminal initiative of a given kind -- used by a
        future proposing drive to detect and supersede a duplicate
        (design doc Sec 12); not consumed by S5.2 itself."""
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
            rows = conn.execute(
                f"SELECT * FROM initiatives "
                f"WHERE kind = ? AND status NOT IN ({placeholders}) "
                f"ORDER BY id ASC",
                (kind, *TERMINAL_STATUSES),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_initiative(row) for row in rows]

    def list_due_for_delivery(self, now_ts: int) -> list[Initiative]:
        """S5.3 design doc Sec 4: every initiative in `approved`,
        `deferred`, or `snoozed` status (deliver()'s own allowed pre-states,
        `_ALLOWED_PRE_STATES["deliver"]`) whose `due_at` has passed --
        `initiative_delivery_check`'s sole query. A NULL `due_at` is
        treated as immediately due (design doc Sec 8 open question 4):
        `expires_at` is mandatory at `propose` time, but `due_at` was left
        optional, and "no due_at" reading as "eligible now" is the only
        interpretation consistent with `propose` not requiring one."""
        conn = self._connect()
        try:
            placeholders = ",".join("?" for _ in _ALLOWED_PRE_STATES["deliver"])
            rows = conn.execute(
                f"SELECT * FROM initiatives "
                f"WHERE status IN ({placeholders}) "
                f"ORDER BY COALESCE(due_at, created_at) ASC",
                tuple(_ALLOWED_PRE_STATES["deliver"]),
            ).fetchall()
        finally:
            conn.close()
        entries = [_row_to_initiative(row) for row in rows]
        due = []
        for e in entries:
            if e.due_at is None:
                due.append(e)
                continue
            due_ts = _parse_iso(e.due_at)
            if due_ts is not None and due_ts <= now_ts:
                due.append(e)
        return due

    def list_audit(self, initiative_id: int, limit: int = 50) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, initiative_id, ts, transition, actor, detail "
                "FROM initiative_audit WHERE initiative_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (initiative_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": row["id"],
                "initiative_id": row["initiative_id"],
                "ts": row["ts"],
                "transition": row["transition"],
                "actor": row["actor"],
                "detail": row["detail"],
            }
            for row in rows
        ]

    def propose(
        self,
        *,
        kind: str,
        category: str,
        priority: str = "normal",
        confidence: float,
        rationale: str,
        payload: dict[str, Any] | None = None,
        origin_drive: str,
        due_at: str | None = None,
        expires_at: str,
        parent_initiative_id: int | None = None,
        depends_on: list[int] | None = None,
        governance_decision: str,
        governance_reason: str | None = None,
        actor: str | None = None,
    ) -> Initiative:
        """Insert a new Initiative, already resolved to its final
        `governance_decision` ("allowed" -> status "approved", anything
        else -> status "denied") -- the caller (the Runtime Contract seam)
        must evaluate Governance *before* calling this, per this module's
        docstring. `expires_at` is required (design doc Sec 14 item 6: no
        silent default). `depends_on` (design doc Sec 13, reserved) is
        recorded into initiative_dependencies if supplied; nothing reads
        it yet."""
        if category not in VALID_CATEGORIES:
            raise InvalidTransitionError(
                f"category must be one of {sorted(VALID_CATEGORIES)}, got {category!r}",
            )
        if priority not in VALID_PRIORITIES:
            raise InvalidTransitionError(
                f"priority must be one of {sorted(VALID_PRIORITIES)}, got {priority!r}",
            )
        if not (0.0 <= confidence <= 1.0):
            raise InvalidTransitionError(f"confidence must be in [0.0, 1.0], got {confidence!r}")
        if not rationale or not rationale.strip():
            raise InvalidTransitionError("rationale must not be empty or whitespace-only")
        if not expires_at:
            raise InvalidTransitionError("expires_at is required (no silent default)")

        status = "approved" if governance_decision == "allowed" else "denied"
        now = _now_iso()
        payload_json = json.dumps(payload) if payload is not None else None

        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO initiatives "
                "(kind, category, status, priority, confidence, rationale, payload, "
                " origin_drive, parent_initiative_id, created_at, due_at, expires_at, "
                " governance_decision, governance_reason, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    category,
                    status,
                    priority,
                    confidence,
                    rationale,
                    payload_json,
                    origin_drive,
                    parent_initiative_id,
                    now,
                    due_at,
                    expires_at,
                    governance_decision,
                    governance_reason,
                    actor,
                ),
            )
            initiative_id = cur.lastrowid
            for dep_id in depends_on or []:
                conn.execute(
                    "INSERT OR IGNORE INTO initiative_dependencies "
                    "(initiative_id, depends_on_id) VALUES (?, ?)",
                    (initiative_id, dep_id),
                )
            conn.execute(
                "INSERT INTO initiative_audit (initiative_id, ts, transition, actor, detail) "
                "VALUES (?, ?, 'propose', ?, ?)",
                (initiative_id, now, actor, status),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(initiative_id)

    def defer(self, initiative_id: int, *, reason: str, actor: str | None = None) -> Initiative:
        return self._advance(
            initiative_id,
            "deferred",
            "defer",
            actor=actor,
            extra={"deferred_reason": reason},
        )

    def deliver(self, initiative_id: int, *, actor: str | None = None) -> Initiative:
        now = _now_iso()
        return self._advance(
            initiative_id,
            "delivered",
            "deliver",
            actor=actor,
            extra={"delivered_at": now},
        )

    def resolve(
        self,
        initiative_id: int,
        *,
        resolution: str,
        due_at: str | None = None,
        actor: str | None = None,
    ) -> Initiative:
        if resolution not in VALID_RESOLUTIONS:
            raise InvalidTransitionError(
                f"resolution must be one of {sorted(VALID_RESOLUTIONS)}, got {resolution!r}",
            )
        now = _now_iso()
        extra: dict[str, Any] = {"resolution": resolution}
        if resolution == "snoozed":
            # Re-entry: a snoozed initiative becomes eligible for delivery
            # again at the new due_at (design doc Sec 5), not resolved_at.
            extra["due_at"] = due_at
        else:
            extra["resolved_at"] = now
        return self._advance(initiative_id, resolution, "resolve", actor=actor, extra=extra)

    def expire(self, initiative_id: int, *, actor: str | None = None) -> Initiative:
        now = _now_iso()
        return self._advance(
            initiative_id,
            "expired",
            "expire",
            actor=actor,
            extra={"resolved_at": now, "resolution": "expired"},
        )

    def cancel(self, initiative_id: int, *, actor: str | None = None) -> Initiative:
        now = _now_iso()
        return self._advance(
            initiative_id,
            "cancelled",
            "cancel",
            actor=actor,
            extra={"resolved_at": now, "resolution": "cancelled"},
        )

    def supersede(self, initiative_id: int, *, actor: str | None = None) -> Initiative:
        now = _now_iso()
        return self._advance(
            initiative_id,
            "superseded",
            "supersede",
            actor=actor,
            extra={"resolved_at": now, "resolution": "superseded"},
        )

    def _advance(
        self,
        initiative_id: int,
        new_status: str,
        transition: str,
        *,
        actor: str | None,
        extra: dict[str, Any],
    ) -> Initiative:
        now = _now_iso()
        conn = self._connect()
        try:
            set_wal_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM initiatives WHERE id = ?",
                (initiative_id,),
            ).fetchone()
            if row is None:
                raise InitiativeNotFoundError(f"No initiative {initiative_id}")
            allowed_pre = _ALLOWED_PRE_STATES[transition]
            if row["status"] not in allowed_pre:
                raise InvalidTransitionError(
                    f"initiative {initiative_id} is in status {row['status']!r}; "
                    f"{transition!r} requires one of {sorted(allowed_pre)}",
                )

            set_clauses = ["status = ?", "actor = ?"]
            params: list[Any] = [new_status, actor]
            for column, value in extra.items():
                set_clauses.append(f"{column} = ?")
                params.append(value)
            params.append(initiative_id)
            conn.execute(
                f"UPDATE initiatives SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
            conn.execute(
                "INSERT INTO initiative_audit (initiative_id, ts, transition, actor, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (initiative_id, now, transition, actor, new_status),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get(initiative_id)


def _parse_iso(value: str | None) -> int | None:
    """Parse a 'YYYY-MM-DDTHH:MM:SSZ' timestamp (always UTC, matching
    _now_iso()'s own format) to a Unix epoch second count. Returns None for
    an unparseable/missing value."""
    if not value:
        return None
    try:
        return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None
