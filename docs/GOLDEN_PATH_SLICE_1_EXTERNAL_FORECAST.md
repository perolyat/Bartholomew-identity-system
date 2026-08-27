# Golden Path — Slice 1 Planning Note: External Forecast Lookup

**Status: APPROVED and IMPLEMENTED.**

> Approved 2026-08-27 by Taylor, under the User Approval Gate, in response to the first-slice
> proposal presented at the start of that session. The approval decided the three open points
> recorded in §7. This note is the one right-sized planning note `docs/TILT.md`'s vertical-slice
> discipline calls for — not a design document, and not a second authority on anything.
>
> Authority above this note: `DECISIONS.md`'s "Bartholomew is the persistent executive above an
> ecosystem of external intelligence and capability providers" (2026-08-27, PR #69), whose clause
> (f) says the correct first step is *one narrowly scoped external provider performing one bounded
> task through the existing governed seam*; `CONSTITUTION.md`'s "Bartholomew employs an ecosystem;
> it does not become it"; `INTERFACES.md` §6's external-capability-provider boundary properties;
> and `docs/TILT.md`, which remains the near-term sequencing authority.

## 0. The slice in one paragraph

**This is not primarily a weather feature.** It is Bartholomew's first proven pattern for using an
external capability provider while remaining the governed Executive above it. A user asks an
ordinary question Bartholomew genuinely cannot answer ("will it rain tomorrow? I've got the roof
guy coming"). The Executive recognises the explicit request, sends one bounded, typed question —
a rounded latitude/longitude, a date range and a fixed variable list, and *nothing else* — to one
external provider through `SkillRegistry.execute_action()`, the existing single governed
chokepoint. The provider answers. The answer comes back as **evidence carrying its provenance**,
never as fact, and Bartholomew decides what it means for the thing the user was actually trying
to decide. A forecast was chosen because it is the clearest case of a capability Bartholomew must
*obtain* rather than *recreate*: nothing in this repository should ever attempt to model weather.

## 1. What it adds

All of it through the existing seam. No new architecture, no broker, no provider registry, no
routing or selection mechanism, no second provider — all explicitly unauthorised by clause (f).

1. **`bartholomew/skills/forecast.py`** — an ordinary `SkillBase` skill with one action
   (`lookup`), no database and no event subscriptions. It validates a typed request, enforces its
   own manifest's network allowlist, makes one bounded call off the event loop, and returns
   provenance-bearing evidence or a truthful failure.
2. **`config/skills/forecast.yaml`** — the capability *declaration*, at permission level `ask`
   with `network.fetch` required and `api.open-meteo.com` as the only allowlisted host.
3. **`bartholomew/kernel/forecast_intents.py`** — a pure recogniser and renderer, the same
   discipline `task_intents.py` holds to: no I/O, no execution, no network. This is where the
   Executive decides that an external capability is needed and what the returned evidence means.
4. **`bartholomew/kernel/runtime_contract.py`** — one dispatch, structurally identical to the
   existing conversational task control, routing through `Planner.handle_skill_request()`.
5. **`Identity.yaml`** — one `tool_use.allowlist` entry (`forecast`), without which the Governance
   stage denies every lookup outright.

## 2. Exactly what leaves Bartholomew

Six fields, named in `forecast.EGRESS_FIELDS` and asserted against on **every call**, not only in
a test: `latitude`, `longitude`, `start_date`, `end_date`, `daily`, `timezone`.

The request is *constructed from validated typed values*, never copied from caller input, so there
is no path by which memory, chat content, a user identifier or any other context could travel
outward. There is no request body at all. Coordinates are rounded to two decimal places (~1 km)
before they leave, so the disclosure is a locality rather than an address. A caller-supplied
`place_label` is deliberately **local-only**: it is used in Bartholomew's reply and never sent.

Location comes from process configuration (`BARTHOLOMEW_FORECAST_LATITUDE` /
`_LONGITUDE`) or an explicit parameter. **It is never read from Memory.** Giving an external
provider a path into the user's stored personal context is not in this slice and has no code path.

## 3. Exactly what comes back

Per day: min/max temperature (°C), precipitation total (mm) and maximum precipitation probability
(%). Normalised into `ForecastEvidence`, whose `provenance` block carries `source_kind`,
`provider_host`, `endpoint`, `requested_at`, `disclosed` (the outbound values verbatim),
`timezone`, `succeeded`, and `evidence: True` — the last recording that the content is an external
assertion, not an established fact (`DECISIONS.md` clause (d)).

**Nothing is written to durable memory.** The skill has no database and no write path. Promotion
of external content into knowledge follows the existing learning rules and is explicitly outside
this slice.

## 4. How governance applies

Every gate is one that already existed; none was added, weakened or bypassed.

| Gate | Where | Effect |
|---|---|---|
| Parking Brake (`skills` scope) | `SkillRegistry._execute_action_inner()` | Engaged ⇒ **zero external requests**. Not a suppressed answer — the provider is never contacted. |
| Identity policy | same, on `candidate_action.kind == "forecast"` | Not allowlisted ⇒ denied before the skill is reached. |
| Consent (`ask` level) | `SkillRegistry._resolve_permissions()` | Resolved per action through the existing consent handler; session-scoped, never persisted; **fails closed with no handler registered**. |
| Manifest network allowlist | `ForecastSkill._check_host_allowed()` | Any host the manifest did not declare is refused **before a socket is opened**. Empty allowlist permits nothing. |
| Audit | `SkillRegistry._finish()` | Every attempt — success, failure, denial, brake block — writes a `skill_action_audit` row, with WP-A2 truthful degradation unchanged. |
| Reflection | same, plus the chat turn's own | The chat surface's Reflection records what was asked, which provider was consulted and **exactly what was disclosed**. |

`sandbox.network` had been a declaration with no enforcement anywhere in this repository.
Enforcing it at this skill's own egress point is the smallest change that makes the declaration
real; it is deliberately **not** a framework-wide change, which would be building for a second
provider that does not exist.

## 5. Failure semantics

Five distinct, truthfully reported outcomes, none of which can produce a number:
`unconfigured` (declared but no provider set — the default), `host_not_allowed`, `bad_request`,
`provider_error` (unreachable, timeout, non-2xx), `malformed_response` (including arrays that
disagree in length — a half-assembled forecast is a fabricated forecast). There is **no cache**,
so stale data cannot be presented as live, and **no fallback estimate**. This is
`ModelBackendError`'s discipline applied to a capability provider.

Critically, a failed or denied lookup never falls through to the model. A model asked "will it
rain tomorrow" will produce a fluent answer it has no way to know — which is precisely the failure
an external capability exists to remove.

## 6. Replaceability

Provider-specific knowledge lives in exactly two places: `_DAILY_VARIABLES` and `_map_response()`.
The endpoint is configuration (`BARTHOLOMEW_FORECAST_API_URL`), **unset by default**, so an
unconfigured Bartholomew makes no outbound calls at all and the capability reports itself
*declared but unavailable*. `OPEN_METEO_FORECAST_URL` exists as a documented reference constant
and is deliberately not wired in as a fallback. No provider name appears anywhere in
`forecast_intents.py`, `runtime_contract.py` or `skill_registry.py` — clause (b)'s prohibition on
provider-named architecture. All four properties are pinned by tests.

## 7. Points decided at approval (2026-08-27)

1. **Provider: Open-Meteo.** Chosen over an external search/research provider because it needs no
   API key or account, so the real-world verification step can be run immediately.
2. **Permission level: `ask`, not `auto`.** Taylor's decision, explicitly so this slice proves the
   governed-egress/consent path properly. Not to be optimised to `auto` yet.
3. **Live verification is Taylor's to run.** The development environment cannot reach external
   hosts; see §9.

## 8. Done enough to test

- An ordinary sentence produces a real external call and an attributed, provenance-bearing answer.
- The brake, a policy denial, a refused consent and an unresolvable place name each produce **zero
  external requests** and a truthful reply.
- Only the six declared fields leave, and the audit record of the egress matches what was sent.
- Every provider failure shape degrades truthfully.

## 9. What is verified, and what is not

**Verified against a real HTTP server on loopback** (not a mock — a mocked client would prove only
that the code called itself): every property in §8, across
`tests/test_forecast_external_capability.py` and `tests/test_forecast_chat_seam.py`.

**NOT yet verified: the live call to the real Open-Meteo endpoint.** The development environment's
egress proxy refuses arbitrary external hosts, so no request has ever been made to
`api.open-meteo.com` from this code. What loopback cannot prove is that the real endpoint's
response shape matches `_map_response()`'s expectations. Until the procedure below has been run,
the live integration is **untested**, and nothing in this repository should say otherwise.

### Local verification procedure

```bash
pip install -e . -r requirements-dev.txt

export BARTHOLOMEW_FORECAST_API_URL="https://api.open-meteo.com/v1/forecast"
export BARTHOLOMEW_FORECAST_LATITUDE="-33.8688"     # your own location
export BARTHOLOMEW_FORECAST_LONGITUDE="151.2093"

uvicorn app:app --port 5173     # then http://localhost:5173 -> /ui/
```

Then, in the UI:

1. Ask **"will it rain tomorrow?"** → expect an attributed forecast naming `api.open-meteo.com`,
   with the figures and the "not something I know independently" qualifier.
2. Ask **"what's the weather in Melbourne?"** → expect a truthful refusal, and confirm no request
   was made.
3. Run `python -m bartholomew.cli brake on`, ask again → expect no forecast, and confirm with a
   packet capture or the provider's absence of traffic that **nothing left the machine**. Then
   `brake off`.
4. Unset `BARTHOLOMEW_FORECAST_API_URL`, restart, ask again → expect a clean "no provider is
   configured", never a guess.
5. Check `skill_action_audit` for one row per attempt, and the chat Reflection's
   `forecast_action.disclosed` for exactly the six fields.

Expect a consent prompt on the first lookup of each session (permission level `ask`); refusing it
must produce "Nothing was sent to the forecast provider."

## 10. What this slice deliberately does not do

No provider selection, routing, broker, registry, marketplace or performance learning. No second
provider. No geocoding. No proactive/scheduled forecast checks. No promotion of external content
into memory. No framework-wide sandbox enforcement. Each of those is either explicitly
unauthorised by `DECISIONS.md` clause (f), or is complexity that real use has not yet earned —
which is `docs/TILT.md`'s whole point.
