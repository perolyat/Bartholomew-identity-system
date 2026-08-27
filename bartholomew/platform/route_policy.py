"""
Which capability each route requires, and which routes are unauthenticated.

The table is keyed by `(method, route_path_template)` -- the template
FastAPI matched, e.g. `("GET", "/api/memory/{kind}/{key}")`, taken from
`request.scope["route"].path`. Matching the template rather than the
concrete URL means no pattern-guessing, no regex, and no possibility of a
path-normalisation trick (`//api/..`, `%2e%2e`, a trailing slash) selecting a
different policy entry than the one whose handler actually runs: by the time
this is consulted, routing has already happened and the answer is exact.

**Default deny.** `capability_for` raises for anything not listed. A route
added by any stream is unreachable until classified, and
`tests/test_route_policy_coverage.py` fails until it is -- so the omission
surfaces in CI rather than in production as an open endpoint.
"""

from __future__ import annotations

from .capabilities import Capability


class UnclassifiedRouteError(Exception):
    """
    A route has no policy entry. Always refused (403), never admitted.

    Its own exception type so the default-deny branch is greppable and can
    never be mistaken for an ordinary authorisation failure when reading
    logs: this one means *we forgot*, and someone must classify the route.
    """


# Unauthenticated on purpose. Each entry is here because it holds no personal
# data and touches no governed state:
#
#   * the login endpoint, which cannot require a session to create one;
#   * health/liveness probes, which must answer during startup and shutdown
#     (the same reasoning as the existing admission-exemption list);
#   * API schema/docs and the static UI shell, which are code, not data.
#
# `/metrics` is deliberately NOT here -- see the table below.
PUBLIC_PATHS = frozenset(
    {
        "/",
        "/healthz",
        "/api/health",
        "/api/auth/login",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        "/api/onboarding/deployment-guide",
    },
)

# Static mounts, matched by prefix because a StaticFiles mount has no single
# route template.
PUBLIC_PREFIXES = ("/ui",)

_MEMORY = Capability.MEMORY_READ
_MEMW = Capability.MEMORY_WRITE

ROUTE_CAPABILITIES: dict[tuple[str, str], Capability] = {
    # --- authentication ------------------------------------------------
    # Logout requires a session: it is an action on a specific session, and
    # an unauthenticated logout endpoint is a way to revoke other people's.
    ("POST", "/api/auth/logout"): Capability.LIVENESS,
    ("GET", "/api/auth/whoami"): Capability.LIVENESS,
    # --- chat / conversation -------------------------------------------
    ("POST", "/api/chat"): Capability.CHAT,
    ("GET", "/api/conversation/recent"): Capability.CHAT,
    # --- self state -----------------------------------------------------
    ("GET", "/api/self"): Capability.SELF_READ,
    ("GET", "/api/self/affect"): Capability.SELF_READ,
    ("PUT", "/api/self/affect"): Capability.SELF_WRITE,
    ("GET", "/api/self/attention"): Capability.SELF_READ,
    ("PUT", "/api/self/attention"): Capability.SELF_WRITE,
    ("DELETE", "/api/self/attention"): Capability.SELF_WRITE,
    ("GET", "/api/self/drives"): Capability.SELF_READ,
    ("GET", "/api/self/drives/top"): Capability.SELF_READ,
    ("POST", "/api/self/drives/{drive_id}/activate"): Capability.SELF_WRITE,
    ("POST", "/api/self/drives/{drive_id}/satisfy"): Capability.SELF_WRITE,
    ("GET", "/api/self/goals"): Capability.SELF_READ,
    ("POST", "/api/self/goals"): Capability.SELF_WRITE,
    ("DELETE", "/api/self/goals/{goal}"): Capability.SELF_WRITE,
    ("GET", "/api/working_memory"): _MEMORY,
    ("GET", "/api/working_memory/context"): _MEMORY,
    ("DELETE", "/api/working_memory"): _MEMW,
    # --- episodes (personal history) -------------------------------------
    ("GET", "/api/episodes/recent"): _MEMORY,
    ("GET", "/api/episodes/search"): _MEMORY,
    ("GET", "/api/episodes/{episode_id}"): _MEMORY,
    ("GET", "/api/episodes/by-type/{episode_type}"): _MEMORY,
    ("GET", "/api/episodes/by-tag/{tag}"): _MEMORY,
    # --- persona ---------------------------------------------------------
    ("GET", "/api/persona/current"): Capability.SELF_READ,
    ("GET", "/api/persona/list"): Capability.SELF_READ,
    ("GET", "/api/persona/history"): Capability.SELF_READ,
    ("GET", "/api/persona/{pack_id}"): Capability.SELF_READ,
    ("POST", "/api/persona/switch"): Capability.SELF_WRITE,
    # --- inbound capture (Session D) --------------------------------------
    # An authenticated user's own runtime is the only place an inbound event
    # can land: which runtime is decided by the verified principal and this
    # process's runtime binding, never by the event or its sender (see
    # `inbound_auth.resolved_runtime_id`). Capture additionally requires a
    # verified *source*, which is provenance and not authorisation -- both
    # gates apply, and neither substitutes for the other.
    #
    # Not public, and not exempt: unlike health and the static UI, these
    # touch governed state.
    ("POST", "/api/inbound/events"): Capability.INBOUND_CAPTURE,
    ("GET", "/api/inbound/events"): Capability.INBOUND_READ,
    # --- memory ----------------------------------------------------------
    ("GET", "/api/memory"): _MEMORY,
    ("GET", "/api/memory/kinds"): _MEMORY,
    ("GET", "/api/memory/{kind}/{key}"): _MEMORY,
    ("PUT", "/api/memory/{kind}/{key}"): _MEMW,
    ("DELETE", "/api/memory/{kind}/{key}"): _MEMW,
    # Export is its own capability, not memory:read. Reading one key and
    # exfiltrating an entire personal memory are different powers, and a
    # future read-only role should be able to hold one without the other.
    ("GET", "/api/memory/export"): Capability.MEMORY_EXPORT,
    # --- consent ---------------------------------------------------------
    ("GET", "/api/consent/pending-writes"): Capability.CONSENT_DECIDE,
    ("POST", "/api/consent/pending-writes/{pending_id}/approve"): Capability.CONSENT_DECIDE,
    ("POST", "/api/consent/pending-writes/{pending_id}/deny"): Capability.CONSENT_DECIDE,
    # --- governance ------------------------------------------------------
    ("GET", "/api/governance/brake"): Capability.BRAKE_READ,
    ("POST", "/api/governance/brake/engage"): Capability.BRAKE_ENGAGE,
    ("POST", "/api/governance/brake/disengage"): Capability.BRAKE_DISENGAGE,
    ("GET", "/api/governance/audit"): Capability.GOVERNANCE_AUDIT,
    # --- awaiting response ------------------------------------------------
    ("GET", "/api/awaiting-response"): Capability.AWAITING_RESPONSE,
    ("POST", "/api/awaiting-response"): Capability.AWAITING_RESPONSE,
    ("POST", "/api/awaiting-response/{entry_id}/resolve"): Capability.AWAITING_RESPONSE,
    ("GET", "/api/awaiting-response/{entry_id}/audit"): Capability.AWAITING_RESPONSE,
    # --- notifications ----------------------------------------------------
    ("GET", "/api/notifications/settings"): Capability.NOTIFICATIONS,
    ("PUT", "/api/notifications/quiet-hours"): Capability.NOTIFICATIONS,
    ("POST", "/api/notifications/mute"): Capability.NOTIFICATIONS,
    ("POST", "/api/notifications/unmute"): Capability.NOTIFICATIONS,
    # --- nudges / reflection ----------------------------------------------
    ("GET", "/api/nudges/pending"): Capability.NOTIFICATIONS,
    ("POST", "/api/nudges/{nudge_id}/ack"): Capability.NOTIFICATIONS,
    ("POST", "/api/nudges/{nudge_id}/dismiss"): Capability.NOTIFICATIONS,
    ("GET", "/api/reflection/daily/latest"): Capability.REFLECTION,
    ("GET", "/api/reflection/weekly/latest"): Capability.REFLECTION,
    ("POST", "/api/reflection/run"): Capability.REFLECTION,
    # --- liveness ----------------------------------------------------------
    ("GET", "/api/liveness/self"): Capability.LIVENESS,
    ("GET", "/api/liveness/ticks"): Capability.LIVENESS,
    ("GET", "/api/liveness/nudges"): Capability.LIVENESS,
    ("GET", "/api/liveness/reflections"): Capability.LIVENESS,
    # --- training ----------------------------------------------------------
    ("GET", "/api/training/source-types"): Capability.TRAINING_SUBMIT,
    ("POST", "/api/training/submit"): Capability.TRAINING_SUBMIT,
    # --- kernel command -----------------------------------------------------
    ("POST", "/kernel/command/{cmd}"): Capability.KERNEL_COMMAND,
    # --- metrics -------------------------------------------------------------
    # Authenticated and admin-only. Process and kernel metrics describe how a
    # person's Bartholomew is behaving -- tick rates, queue depths, drive
    # activity -- which is a low-resolution view of their day, and under
    # per-user runtimes the label cardinality would leak more. The existing
    # METRICS_INTERNAL_ONLY prefix switch relocates it; it does not protect it.
    ("GET", "/metrics"): Capability.METRICS,
    ("GET", "/internal/metrics"): Capability.METRICS,
}


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def capability_for(method: str, route_path: str) -> Capability:
    """
    The capability a route requires. Raises `UnclassifiedRouteError` if the
    route has no entry -- the default-deny branch.
    """
    try:
        return ROUTE_CAPABILITIES[(method.upper(), route_path)]
    except KeyError as exc:
        raise UnclassifiedRouteError(
            f"{method.upper()} {route_path} has no entry in ROUTE_CAPABILITIES. "
            f"Routes are default-deny: classify it in "
            f"bartholomew/platform/route_policy.py before it can be reached.",
        ) from exc
