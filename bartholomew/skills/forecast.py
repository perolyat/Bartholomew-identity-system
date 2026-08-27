"""
Forecast Skill -- Bartholomew's first external capability provider
=================================================================

**This is not primarily a weather feature.** It is the first proven pattern
for Bartholomew *using* an external capability while remaining the governed
Executive above it, per `DECISIONS.md`'s "Bartholomew is the persistent
executive above an ecosystem of external intelligence and capability
providers" and the three external-provider boundary properties recorded in
`INTERFACES.md` §6. A forecast was chosen because it is the clearest case of
a capability Bartholomew must *obtain* rather than *recreate*: nothing in
this repository should ever attempt to model weather.

Built entirely on the existing seam -- `SkillBase` / manifest /
`PermissionChecker` / `SkillRegistry.execute_action()` -- exactly as
`DECISIONS.md` clause (f) says the first external capability should be. No
broker, no provider registry, no routing or selection mechanism, no second
provider, and no new architecture. `NotifySkill._deliver_notification()` is
the shipped precedent this follows for the outbound call itself: a bounded,
provider-agnostic HTTP request, run off the event loop, reporting *attempted*
separately from *succeeded* so a failure can never read as a success.

The four properties this module exists to hold
----------------------------------------------

1. **Governed chokepoint.** There is no way to reach the network from here
   except through `SkillRegistry.execute_action()`, which checks the parking
   brake (`skills` scope), the Identity policy on `skill_id="forecast"`, and
   resolves this manifest's `ask`-level `network.fetch` consent *before* this
   module's `execute()` is ever called. Brake engaged means zero packets, not
   a suppressed response.

2. **Governed egress.** Location leaving the deployment is a disclosure. The
   request is *constructed from typed fields* (`EGRESS_FIELDS`), never from
   free text, so there is no path by which memory, chat content, a user
   identifier or any other context could travel outward. Coordinates are
   rounded (`COORDINATE_PRECISION`) before they leave. A caller-supplied
   `place_label` is deliberately **local-only**: it is echoed back for the
   Executive's reply and never sent.

3. **Provenance on return.** Every successful result carries a `provenance`
   block naming the provider host, the exact endpoint, what was sent, and
   when. `evidence: True` records that the content is an external assertion,
   not an established fact -- clause (d). Nothing here writes to durable
   memory; promotion of external content into knowledge is not part of this
   slice and has no code path.

4. **Truthful degradation.** Unconfigured, refused-by-allowlist, timed out,
   HTTP error, and malformed-response are five *distinct*, truthfully
   reported outcomes. None of them can produce a number. There is no cache,
   so stale data cannot be presented as live, and there is no fallback
   estimate -- the `ModelBackendError` discipline, applied to a capability
   provider.

Replaceability
--------------

Provider-specific knowledge is confined to exactly two places: the
`daily=` variable names in `_build_egress_params()` and the response shape in
`_map_response()`. Everything else -- governance, egress control, provenance,
degradation -- is provider-neutral. The endpoint is configuration, not a
constant in a call site. Swapping providers is editing two functions; it is
deliberately *not* implementing an interface, because one provider does not
justify an abstraction (`docs/TILT.md`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from bartholomew.kernel.blocking_executor import run_off_loop
from bartholomew.kernel.skill_base import (
    SkillBase,
    SkillContext,
    SkillResult,
)

logger = logging.getLogger(__name__)

#: The provider endpoint. **Unset by default**, which is what makes the
#: capability *declared but unavailable* -- the distinction `INTERFACES.md`
#: §6 requires between declaring a capability and having it. Same shape as
#: `NotifySkill`'s `WEBHOOK_URL_ENV`: process configuration, never persisted,
#: so enabling outbound egress is always a deliberate operator act.
FORECAST_API_URL_ENV = "BARTHOLOMEW_FORECAST_API_URL"

#: The operator-configured location. Read from configuration and **never**
#: from Memory: this slice does not give an external provider a path to the
#: user's stored personal context, however convenient that would be.
LATITUDE_ENV = "BARTHOLOMEW_FORECAST_LATITUDE"
LONGITUDE_ENV = "BARTHOLOMEW_FORECAST_LONGITUDE"

#: The endpoint the manifest's `sandbox.network` allowlist is written for,
#: recorded here for documentation and for the local verification procedure.
#: Deliberately **not** used as a default value for `FORECAST_API_URL_ENV`:
#: a default endpoint would make outbound egress the unconfigured behaviour,
#: which is exactly backwards.
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: Bounded so a slow or black-holed provider cannot pin a worker thread.
#: The request already runs off the event loop (B2/B8 discipline).
REQUEST_TIMEOUT_SECONDS = 10

#: ~1.1 km. Enough for a forecast, coarse enough that the disclosure is a
#: locality rather than an address.
COORDINATE_PRECISION = 2

#: The maximum horizon this skill will ask for. A bound on the request, not
#: a provider limit.
MAX_FORECAST_DAYS = 7

#: **The complete set of field names permitted to leave Bartholomew.**
#: `_build_egress_params()` is asserted against this, so widening what is
#: disclosed requires editing this line -- it cannot happen by accident, and
#: a test fails if it does.
EGRESS_FIELDS = frozenset(
    {
        "latitude",
        "longitude",
        "start_date",
        "end_date",
        "daily",
        "timezone",
    },
)

#: The provider-specific daily variables requested. One of the two places
#: provider knowledge lives (see the module docstring).
_DAILY_VARIABLES = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
)

# Outcome labels. Distinct values, because "we never asked" and "we asked and
# it broke" are different claims and the Executive must be able to tell them
# apart when deciding what to do about the user's objective.
OUTCOME_OK = "ok"
OUTCOME_UNCONFIGURED = "unconfigured"
OUTCOME_HOST_NOT_ALLOWED = "host_not_allowed"
OUTCOME_BAD_REQUEST = "bad_request"
OUTCOME_PROVIDER_ERROR = "provider_error"
OUTCOME_MALFORMED = "malformed_response"


@dataclass
class ForecastEvidence:
    """
    One provider answer, as **evidence with provenance** -- never as fact.

    `DECISIONS.md` clause (d): an external provider's output enters as an
    observation carrying its source. The `provenance` block is not decoration;
    it is what lets the Executive say where a number came from, and what would
    let a later reader distinguish this from something Bartholomew knows.
    """

    days: list[dict[str, Any]]
    provenance: dict[str, Any]
    place_label: str | None = None
    #: Local-only, never sent. See the module docstring's egress note.
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "provenance": self.provenance,
            "place_label": self.place_label,
            "notes": self.notes,
        }


class ForecastLookupError(Exception):
    """
    A lookup that did not produce evidence, carrying *why*.

    Exists so the five failure shapes stay distinguishable all the way out to
    the caller instead of collapsing into a single "it didn't work" -- the
    `ModelBackendError` posture: a failure must be unmistakable for a result.
    """

    def __init__(self, outcome: str, message: str) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.message = message


class ForecastSkill(SkillBase):
    """
    Look up a bounded weather forecast from one external provider.

    One action (`lookup`), no persistence, no scheduled behaviour, no event
    subscriptions. The skill's entire job is: validate a typed request,
    enforce the declared network allowlist, make one bounded call, and return
    provenance-bearing evidence or a truthful failure.
    """

    def __init__(self) -> None:
        super().__init__()
        self._api_url: str = ""
        self._default_latitude: float | None = None
        self._default_longitude: float | None = None

    @property
    def skill_id(self) -> str:
        return "forecast"

    async def initialize(self, context: SkillContext) -> None:
        """
        Initialize the forecast skill.

        No database: this skill stores nothing. External content is evidence
        for the turn that asked for it, and promotion of external content into
        durable knowledge is explicitly outside this slice -- so the safest
        implementation of "don't contaminate governed memory" is to have no
        write path at all.
        """
        self._context = context

        self._api_url = _clean_env(FORECAST_API_URL_ENV)
        self._default_latitude = _clean_float_env(LATITUDE_ENV)
        self._default_longitude = _clean_float_env(LONGITUDE_ENV)

        if self._api_url:
            logger.info(
                "Forecast provider configured: %s",
                _host_of(self._api_url) or "<unparseable>",
            )
        else:
            logger.info("Forecast provider not configured; capability declared but unavailable")

        logger.info("Forecast skill initialized")

    async def shutdown(self) -> None:
        """Nothing to clean up -- no connections, subscriptions or state."""
        logger.info("Forecast skill shut down")

    @property
    def is_available(self) -> bool:
        """
        Whether the declared capability can actually be performed right now.

        `INTERFACES.md` §6: availability is reported **distinctly from
        declaration**. The skill being loaded says the capability exists; this
        says whether a provider is reachable to perform it.
        """
        return bool(self._api_url)

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        status["provider_configured"] = self.is_available
        status["provider_host"] = _host_of(self._api_url) if self._api_url else None
        status["has_configured_location"] = (
            self._default_latitude is not None and self._default_longitude is not None
        )
        return status

    async def execute(
        self,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> SkillResult:
        params = params or {}

        if action != "lookup":
            return SkillResult.fail(f"Unknown action: {action}")

        try:
            return await self._action_lookup(params)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Forecast action %s failed: %s", action, e)
            return SkillResult.fail(str(e))

    # -------------------------------------------------------------------------
    # The one action
    # -------------------------------------------------------------------------

    async def _action_lookup(self, params: dict[str, Any]) -> SkillResult:
        """
        Fetch a bounded forecast and return it as evidence with provenance.

        The permission self-check below is the skill's own; it is **not** the
        governance gate. `SkillRegistry.execute_action()` has already run the
        brake, the Identity policy check and the `ask`-level consent
        resolution for `network.fetch` before this method exists in the call
        stack -- see `SkillBase._require_permission()`'s docstring and the
        registry's `_resolve_permissions()`.
        """
        perm_error = self._require_permission("network.fetch")
        if perm_error:
            return perm_error

        if not self.is_available:
            # Declared, unavailable. Not an error the user did anything to
            # cause, and emphatically not an occasion to guess a forecast.
            return SkillResult.fail(
                "No forecast provider is configured, so no forecast was obtained.",
                data={"outcome": OUTCOME_UNCONFIGURED, "attempted": False},
            )

        try:
            request = self._build_request(params)
        except ForecastLookupError as e:
            return SkillResult.fail(e.message, data={"outcome": e.outcome, "attempted": False})

        # Enforce the manifest's declared network allowlist *before* any
        # socket is opened. `sandbox.network` has been a declaration with no
        # enforcement anywhere in this repository; enforcing it at this
        # skill's own egress point is the smallest change that makes the
        # declaration real, and deliberately not a framework-wide one.
        allow_error = self._check_host_allowed(request["url"])
        if allow_error is not None:
            return allow_error

        requested_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            payload = await run_off_loop(
                self._fetch,
                request["url"],
                request["params"],
                executor=getattr(self._context, "blocking_executor", None),
            )
        except ForecastLookupError as e:
            return self._failure_result(e, request, requested_at)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Forecast lookup dispatch failed")
            return self._failure_result(
                ForecastLookupError(OUTCOME_PROVIDER_ERROR, str(e)),
                request,
                requested_at,
            )

        try:
            days = _map_response(payload, request["params"])
        except ForecastLookupError as e:
            return self._failure_result(e, request, requested_at)

        evidence = ForecastEvidence(
            days=days,
            place_label=request["place_label"],
            provenance=self._provenance(request, requested_at, succeeded=True),
        )

        return SkillResult.ok(
            data=evidence.to_dict(),
            message="Forecast obtained from external provider",
            metadata=self._lookup_metadata(request, succeeded=True, outcome=OUTCOME_OK),
        )

    # -------------------------------------------------------------------------
    # Egress construction -- the disclosure boundary
    # -------------------------------------------------------------------------

    def _build_request(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Turn typed parameters into the exact outbound request.

        Everything that leaves is produced here, from validated numbers and
        dates. Note what is *absent*: no free-text field, no identifier, no
        memory read, no request body at all. `place_label` is captured for the
        reply and dropped from the egress params.
        """
        latitude = _coerce_coordinate(
            params.get("latitude", self._default_latitude),
            "latitude",
            limit=90.0,
        )
        longitude = _coerce_coordinate(
            params.get("longitude", self._default_longitude),
            "longitude",
            limit=180.0,
        )

        days = params.get("days", 1)
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise ForecastLookupError(OUTCOME_BAD_REQUEST, "days must be a whole number") from None
        if days < 1 or days > MAX_FORECAST_DAYS:
            raise ForecastLookupError(
                OUTCOME_BAD_REQUEST,
                f"days must be between 1 and {MAX_FORECAST_DAYS}",
            )

        start = params.get("start_date")
        start_date = _coerce_date(start) if start else date.today()
        end_date = start_date + timedelta(days=days - 1)

        tz = str(params.get("timezone") or "UTC").strip() or "UTC"

        egress = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": ",".join(_DAILY_VARIABLES),
            "timezone": tz,
        }

        # Structural, not cosmetic: this is the assertion that the disclosure
        # boundary is what it claims to be, checked on every single call
        # rather than only in a test.
        undeclared = set(egress) - EGRESS_FIELDS
        if undeclared:  # pragma: no cover - unreachable unless the dict above changes
            raise ForecastLookupError(
                OUTCOME_BAD_REQUEST,
                f"refusing to send undeclared fields: {sorted(undeclared)}",
            )

        place_label = params.get("place_label")
        return {
            "url": self._api_url,
            "params": egress,
            # Local-only from here on. Never merged into `params`.
            "place_label": str(place_label).strip() if place_label else None,
        }

    def _check_host_allowed(self, url: str) -> SkillResult | None:
        """
        Refuse, before opening a socket, any host the manifest did not declare.

        Fails closed twice over: an unparseable URL is refused, and an empty
        declared allowlist permits nothing rather than everything.
        """
        manifest = getattr(self._context, "manifest", None)
        allowed = []
        if manifest is not None:
            allowed = list(getattr(manifest.permissions.sandbox, "network", []) or [])

        host = _host_of(url)
        if host and host in allowed:
            return None

        logger.warning(
            "Forecast egress refused: host %r is not in the manifest network allowlist %r",
            host,
            allowed,
        )
        return SkillResult.fail(
            "The configured forecast provider is not in this skill's declared network "
            "allowlist, so no request was made.",
            data={
                "outcome": OUTCOME_HOST_NOT_ALLOWED,
                "attempted": False,
                "host": host,
                "allowed_hosts": allowed,
            },
        )

    # -------------------------------------------------------------------------
    # The outbound call
    # -------------------------------------------------------------------------

    @staticmethod
    def _fetch(url: str, params: dict[str, Any]) -> Any:
        """
        Perform one bounded GET.

        Synchronous by design and called through `run_off_loop()` -- the same
        reasoning as `NotifySkill._post_webhook()`: `requests` is already a
        declared dependency, and adding an async HTTP client to make one call
        would be new infrastructure this slice does not need.

        Raises `ForecastLookupError` for every failure shape. It never returns
        a substitute value, because a substitute value is indistinguishable
        from a forecast.
        """
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.Timeout as e:
            raise ForecastLookupError(
                OUTCOME_PROVIDER_ERROR,
                f"the forecast provider did not respond within {REQUEST_TIMEOUT_SECONDS}s",
            ) from e
        except Exception as e:
            raise ForecastLookupError(
                OUTCOME_PROVIDER_ERROR,
                f"the forecast provider could not be reached: {e}",
            ) from e

        if not (200 <= response.status_code < 300):
            raise ForecastLookupError(
                OUTCOME_PROVIDER_ERROR,
                f"the forecast provider returned HTTP {response.status_code}",
            )

        try:
            return response.json()
        except Exception as e:
            raise ForecastLookupError(
                OUTCOME_MALFORMED,
                "the forecast provider's response was not valid JSON",
            ) from e

    # -------------------------------------------------------------------------
    # Provenance and reporting
    # -------------------------------------------------------------------------

    def _provenance(
        self,
        request: dict[str, Any],
        requested_at: str,
        succeeded: bool,
    ) -> dict[str, Any]:
        """
        What the Executive needs in order to say where this came from.

        `disclosed` repeats the outbound values verbatim rather than
        describing them, so the record of what was sent is the thing that was
        sent -- an after-the-fact account could drift from the request.
        """
        return {
            "source_kind": "external_provider",
            "provider_host": _host_of(request["url"]),
            "endpoint": request["url"],
            "requested_at": requested_at,
            "disclosed": dict(request["params"]),
            "timezone": request["params"].get("timezone"),
            "succeeded": succeeded,
            # Clause (d): an external assertion, not an established fact.
            "evidence": True,
        }

    def _failure_result(
        self,
        error: ForecastLookupError,
        request: dict[str, Any],
        requested_at: str,
    ) -> SkillResult:
        """
        A truthful degraded result: what was attempted, what went wrong, and
        no forecast. The provenance block is retained precisely *because* the
        lookup failed -- "we asked this provider at this time and got nothing"
        is itself the evidence the Executive needs.
        """
        provenance = self._provenance(request, requested_at, succeeded=False)
        return SkillResult.fail(
            error.message,
            data={
                "outcome": error.outcome,
                "attempted": True,
                "days": [],
                "provenance": provenance,
            },
        )

    def _lookup_metadata(
        self,
        request: dict[str, Any] | None,
        succeeded: bool,
        outcome: str,
    ) -> dict[str, Any]:
        """
        Report *attempted* separately from *succeeded*, the shape
        `NotifySkill._delivery_metadata()` established for exactly this
        reason: collapsing them is how a failure comes to read as a success.
        """
        return {
            "lookup": {
                "attempted": request is not None,
                "succeeded": succeeded,
                "outcome": outcome,
                "provider_host": _host_of(request["url"]) if request else None,
            },
        }


# -----------------------------------------------------------------------------
# Provider response mapping -- the second and last place provider knowledge
# lives (see the module docstring). Replacing the provider means replacing
# this function and `_DAILY_VARIABLES`; nothing else knows what Open-Meteo is.
# -----------------------------------------------------------------------------


def _map_response(payload: Any, egress: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Map the provider's parallel-arrays response into per-day records.

    Strict on purpose. A response that is the wrong shape, or whose arrays
    disagree in length, is a `malformed_response` failure -- never a partial
    forecast assembled from whatever happened to line up. Silently tolerating
    a broken response is how fabricated data enters a system.
    """
    if not isinstance(payload, dict):
        raise ForecastLookupError(OUTCOME_MALFORMED, "the provider's response was not an object")

    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise ForecastLookupError(
            OUTCOME_MALFORMED,
            "the provider's response contained no daily forecast",
        )

    dates = daily.get("time")
    if not isinstance(dates, list) or not dates:
        raise ForecastLookupError(
            OUTCOME_MALFORMED,
            "the provider's response contained no forecast dates",
        )

    columns: dict[str, list[Any]] = {}
    for name in _DAILY_VARIABLES:
        column = daily.get(name)
        if not isinstance(column, list) or len(column) != len(dates):
            raise ForecastLookupError(
                OUTCOME_MALFORMED,
                f"the provider's response was missing or inconsistent for '{name}'",
            )
        columns[name] = column

    days: list[dict[str, Any]] = []
    for index, day in enumerate(dates):
        days.append(
            {
                "date": str(day),
                "temperature_min_c": columns["temperature_2m_min"][index],
                "temperature_max_c": columns["temperature_2m_max"][index],
                "precipitation_mm": columns["precipitation_sum"][index],
                "precipitation_probability_pct": columns["precipitation_probability_max"][index],
            },
        )

    # The window the provider answered for may legitimately differ from the
    # window asked for; the Executive is told, rather than the difference
    # being hidden by trimming.
    requested_start = egress.get("start_date")
    if days and requested_start and days[0]["date"] != requested_start:
        logger.info(
            "Provider returned a window starting %s for a request starting %s",
            days[0]["date"],
            requested_start,
        )

    return days


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------


def _clean_env(name: str) -> str:
    import os

    return (os.getenv(name) or "").strip()


def _clean_float_env(name: str) -> float | None:
    raw = _clean_env(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring non-numeric %s=%r", name, raw)
        return None


def _host_of(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except Exception:
        return None


def _coerce_coordinate(value: Any, name: str, limit: float) -> float:
    if value is None:
        raise ForecastLookupError(
            OUTCOME_BAD_REQUEST,
            f"no {name} is configured, so no location could be sent",
        )
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ForecastLookupError(OUTCOME_BAD_REQUEST, f"{name} must be a number") from None
    if number != number or abs(number) > limit:  # NaN or out of range
        raise ForecastLookupError(
            OUTCOME_BAD_REQUEST,
            f"{name} must be between -{limit:g} and {limit:g}",
        )
    # Rounded before it leaves: the disclosure is a locality, not an address.
    return round(number, COORDINATE_PRECISION)


def _coerce_date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ForecastLookupError(
            OUTCOME_BAD_REQUEST,
            "start_date must be an ISO 8601 date (YYYY-MM-DD)",
        ) from None
