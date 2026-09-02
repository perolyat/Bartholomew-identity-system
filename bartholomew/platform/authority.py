"""
The Platform/Admin Parking Brake tier.

Canonical semantics: `DECISIONS.md`, "Parking Brake authority tiers --
Personal/User and Platform/Admin". This module implements the Platform tier
only; the Personal/User tier is the existing per-runtime
`GovernanceStore`/`ParkingBrake` and is deliberately untouched.

Four properties from that decision are load-bearing here, and each is a
thing this module had to be shaped around rather than a comment on it:

1. **Not a scope.** The decision names adding `"platform"` to the existing
   `scopes` set as a category error, because scopes are cleared by the same
   ordinary `disengage()` any user may call -- a platform halt expressed as a
   scope would be user-overridable, which is the one property it exists to
   guarantee against. So the Platform tier lives in a **different table in a
   different database**, which no per-user runtime has a write path to.

2. **Restrictive composition.** Execution proceeds only if neither tier
   blocks it. `is_blocked()` below is an OR, and disengaging one tier never
   implies disengaging the other.

3. **A user cannot override it.** There is no capability in the user set that
   reaches this module -- see `capabilities._USER_CAPABILITIES`, which
   contains no `platform:admin`.

4. **A platform outage must never leave local execution unstoppable.** The
   Platform tier can only ever *add* a halt. If this store is unreachable,
   the Personal brake continues to work entirely on its own, in the user's
   own database, through the CLI, with no network involved. That asymmetry is
   the whole reason the tiers are separate stores rather than one.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from bartholomew.orchestrator.safety.governance_store import (
    register_additional_engaged_check,
    register_additional_halt_check,
)

from .exposure import platform_tier_active
from .store import platform_connection, record_platform_audit

# The same subsystem axis the Personal tier uses. The tiers are orthogonal to
# it: scopes answer *what* is halted, tiers answer *on whose authority*.
# "actuation" (Session B) is the scope that halts governed Windows actions and
# nothing else, so an administrator can stop a companion acting on somebody's
# computer without stopping Bartholomew thinking. It only ever *adds* a halt:
# the actuation seam also refuses on the brake being engaged at all, so every
# other scope already stops actuation and this one narrows the blast radius
# rather than widening what is possible.
VALID_SCOPES = frozenset(
    {"global", "skills", "sight", "voice", "scheduler", "training", "actuation"},
)


@dataclass(frozen=True)
class PlatformBrakeState:
    engaged: bool
    scopes: frozenset[str]
    revision: int


class StalePlatformWriteError(Exception):
    """The caller's view of the platform brake revision is out of date."""


class PlatformBrakeUnavailableError(Exception):
    """
    The platform brake state could not be read.

    Callers must treat this as **engaged** (fail closed): an unreadable
    safety halt is not evidence of the absence of a safety halt. See
    `is_blocked`, which does exactly that.
    """


def _read(conn) -> PlatformBrakeState:
    row = conn.execute(
        "SELECT engaged, scopes, revision FROM platform_brake_state WHERE id = 1",
    ).fetchone()
    if row is None:
        return PlatformBrakeState(engaged=False, scopes=frozenset(), revision=0)
    return PlatformBrakeState(
        engaged=bool(row["engaged"]),
        scopes=frozenset(json.loads(row["scopes"])),
        revision=int(row["revision"]),
    )


def get_state(*, db_path: str | None = None) -> PlatformBrakeState:
    with platform_connection(db_path) as conn:
        return _read(conn)


def engage(
    *scopes: str,
    reason: str | None = None,
    actor: str,
    db_path: str | None = None,
) -> PlatformBrakeState:
    """
    Engage (or tighten) the platform halt.

    Tightening is never refused on staleness grounds, matching the Personal
    tier's existing invariant: the brake may always become more restrictive
    without a confirmed loosening action. An emergency halt must not fail
    because someone else halted something a moment earlier.
    """
    requested = set(scopes) or {"global"}
    unknown = sorted(requested - VALID_SCOPES)
    if unknown:
        raise ValueError(f"unknown scope(s) {unknown}; valid: {sorted(VALID_SCOPES)}")

    now = int(time.time())
    with platform_connection(db_path) as conn:
        current = _read(conn)
        merged = sorted(set(current.scopes) | requested)
        revision = current.revision + 1
        conn.execute(
            "INSERT INTO platform_brake_state"
            "(id, engaged, scopes, revision, reason, actor, updated_at) "
            "VALUES (1, 1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET engaged=1, scopes=excluded.scopes, "
            "revision=excluded.revision, reason=excluded.reason, "
            "actor=excluded.actor, updated_at=excluded.updated_at",
            (json.dumps(merged), revision, reason, actor, now),
        )
        record_platform_audit(
            conn,
            "platform_brake.engaged",
            detail=f"actor={actor} scopes={','.join(merged)} revision={revision} reason={reason}",
            ts=now,
        )
        return PlatformBrakeState(True, frozenset(merged), revision)


def disengage(
    *,
    reason: str | None = None,
    actor: str,
    expected_revision: int | None = None,
    db_path: str | None = None,
) -> PlatformBrakeState:
    """
    Release the platform halt. Revision-guarded, mirroring the Personal tier.

    Loosening requires an explicit, confirmed action against a known
    revision, so two administrators cannot each release a halt the other
    tightened without noticing.
    """
    now = int(time.time())
    with platform_connection(db_path) as conn:
        current = _read(conn)
        if expected_revision is not None and expected_revision != current.revision:
            raise StalePlatformWriteError(
                f"platform brake revision is {current.revision}, not {expected_revision}; "
                f"re-read the state and retry",
            )
        revision = current.revision + 1
        conn.execute(
            "INSERT INTO platform_brake_state"
            "(id, engaged, scopes, revision, reason, actor, updated_at) "
            "VALUES (1, 0, '[]', ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET engaged=0, scopes='[]', "
            "revision=excluded.revision, reason=excluded.reason, "
            "actor=excluded.actor, updated_at=excluded.updated_at",
            (revision, reason, actor, now),
        )
        record_platform_audit(
            conn,
            "platform_brake.disengaged",
            detail=f"actor={actor} revision={revision} reason={reason}",
            ts=now,
        )
        return PlatformBrakeState(False, frozenset(), revision)


def is_blocked(scope: str, *, personal_blocked: bool, db_path: str | None = None) -> bool:
    """
    Restrictive composition of the two tiers for one scope.

    `personal_blocked` is the Personal/User tier's existing answer, passed in
    rather than computed here: this module must not reach into a user's
    runtime, and inverting that dependency is what keeps the Personal tier
    working when the platform store is gone.

    Fails closed. If the platform store cannot be read, the answer is
    "blocked" -- an unreadable halt is treated as a halt, consistent with the
    Personal tier's own fail-closed contract on an unreadable brake.
    """
    if personal_blocked:
        return True
    try:
        state = get_state(db_path=db_path)
    except Exception:
        return True
    if not state.engaged:
        return False
    return "global" in state.scopes or scope in state.scopes


def platform_halt_check(scope: str) -> bool:
    """
    The Platform/Admin tier's answer for one scope, for Governance to compose.

    Inert in a deployment that has no platform tier (a single-user loopback
    development install): there is no platform to halt and no administrator
    distinct from the user, and treating an absent control-plane database as
    an unreadable safety halt would fail-close a purely local Bartholomew into
    uselessness for no safety gain.

    Where the tier *is* active, an unreadable platform state raises, and
    `is_blocked_fail_closed` turns that into a halt.
    """
    if not platform_tier_active():
        return False
    state = get_state()
    if not state.engaged:
        return False
    return "global" in state.scopes or scope in state.scopes


def platform_engaged_check() -> bool:
    """
    Whether the Platform/Admin tier is engaged **at all**, for Governance to
    compose into operations that belong to no subsystem scope.

    Inert when the deployment has no platform tier, for the same reason as
    `platform_halt_check`. Where the tier is active, an unreadable state
    raises and the caller treats that as engaged.
    """
    if not platform_tier_active():
        return False
    return get_state().engaged


def install_platform_halt_hook() -> None:
    """
    Wire the Platform tier into Governance's composition point.

    Called from API startup and from the platform-brake CLI, so that every
    downstream execution boundary already consulting Governance -- skill
    execution, the runtime contract's autonomous work and governed state
    mutations -- composes both tiers without any of those call sites
    changing. Registration is one-directional by design: Governance never
    imports this package, so the local Personal brake keeps working with the
    control plane destroyed.
    """
    register_additional_halt_check(platform_halt_check)
    register_additional_engaged_check(platform_engaged_check)
