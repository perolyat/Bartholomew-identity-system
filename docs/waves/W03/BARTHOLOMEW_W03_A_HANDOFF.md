# BARTHOLOMEW W03-A HANDOFF — Live Windows Perception & State Evidence

| | |
|---|---|
| Session | **W03-A — Live Windows Perception & State Evidence** |
| Immutable id | `W03-A` |
| Branch | `wave/w03-a-live-windows-perception` |
| Pull request | `[W03-A] Live Windows Perception & State Evidence` — **#93** |
| Baseline | `main @ e96e6a6dfc3f71a68d44010c9954bb4e7a0c5195` (W03-PREP closeout; carries `99ee734`) |
| Implementation commits | `5496b4d` (feat: the package) · `99123d6` (style: black layout in two test signatures) |
| Frozen head | the PR head carrying this handoff (recorded on the PR) |
| Required CI tier | Integration |
| Status | **frozen** — head declared final for W03-F integration; nothing further will be pushed to this branch without telling W03-F |

The manifest (`W03_MANIFEST.yaml`) still reads `status: ready_to_start` for
W03-A. `docs/waves/W03` is owned by W03-PREP, so W03-A did not edit it; the
status flip to `frozen` is W03-PREP's / W03-F's one-line change.

---

## 1. What W03-A built

The wave-two multimodal package reached an `ACTIVE` screen session and then
did nothing: no production code captured, serialized or emitted an
observation; the kernel received `element_count`, not the elements;
observation and inference were one string; nothing could read a window back
after an action. W03-A closes exactly that gap — the **Observe** leg, and the
read primitive the **Verify** leg consumes — inside the package boundary the
manifest allocates to it.

### 1.1 The `observation-event` shared contract (`bartholomew/multimodal/observation.py`)

`ObservationEvent` carries five **distinct** fields, enforced by its
constructor rather than by convention:

| field | rule |
|---|---|
| `observed_event` | `ObservedEvent(kind, summary, facts)` — what the sensor read. Kinds: `window_state`, `inactivity`, `unavailable`, `read_back`. Facts are bounded, strict JSON; summary ≤ 300 chars. |
| `inferred_state` | nullable. A record with no inference is complete and valid. |
| `confidence` | present **iff** `inferred_state` is; must lie in `[0.0, 1.0)` — a certain inference is refused. |
| `competing_explanations` | non-empty **iff** `inferred_state` is present. |
| `provenance` | `source`, `occurred_at`, `captured_at` (the capture session's own stamp), `digest` (`sha256:` of the observed event), `session_id`, `device_id`. A digest that does not match the observed event is refused, on construction and on `from_dict`. |

Plus the content's `classification` (privacy/retention class, redaction
trail, truncation flag), never recomputed downstream.

**On the wire** the payload is `ObservationEvent.as_dict()` (with
`observation_event_version: 1`, `session_id`, `modality`) under the two
existing event types `multimodal.accessibility.observation` /
`multimodal.screen.observation`. **No new event type, no new handler, no
second bus**: the registry still holds exactly the five wave-two multimodal
types (asserted). The envelope-level `captured_at` stays `None` for ingress
to assign (§3.1); `occurred_at` is the record's own.

The canonical non-collapse assertion (W03_TEST_CONTRACTS §4) is a test:
`ObservedEvent.inactivity(20 * 60)` yields *"no keyboard or mouse input for
20 minutes"* with `inferred_state=None`, and the rendered record contains no
"overwhelmed" / "intervention" / "distress".

### 1.2 The bounded inferencer (`inference.py`)

`BoundedInferencer` reads **structural facts only** (idle duration, whether a
focused editable control has a value, whether anything was readable) — never
a label, title or value text, asserted both behaviourally (a poisoned label
yields the identical inference to a benign one) and structurally (an AST test
that the module subscripts no text-bearing key). Every inference has
confidence ≤ 0.6 and ≥ 2 competing explanations. Vocabulary:
`user_may_be_away_from_pc`, `user_may_be_editing_in_focused_control`,
`user_may_be_reading_or_reviewing`. `NullInferencer` infers nothing. The
inferencer is pluggable but any replacement still passes the constructor's
checks.

### 1.3 The observation loop (`loop.py`) — the Observe leg, running

`runtime.start_session()` now starts an `ObservationLoop` on a daemon thread
for every screen session that reaches `ACTIVE` (after every existing gate:
tenant/principal binding, device capability, brake, Identity policy, explicit
consent, scope, duration). The loop is registered as the session's stopper
*before* the session becomes `ACTIVE`, so there is no instant the store cannot
stop it. Each wake, in order:

1. **Governance re-read** — session still `ACTIVE`; not expired (else
   `EXPIRED`); Parking Brake read through the one `GovernanceStore`
   (`is_blocked_fail_closed(scope, db_path)`), scope `sight` (which the
   store's `is_blocked` composes with `global`). Engaged →
   `SessionStore.stop_all_for_brake(scope)` — **this is that function's
   production caller** — session `STOPPED` with "stopped by parking brake
   (scope=sight)". Unreadable → `FAILED`, "stopped fail-closed". The wake
   interval (`poll_seconds`, default 1.0 s, ceiling 5.0 s) is the bound on how
   long an engaged brake goes unnoticed.
2. **Capture** on `capture_interval_seconds` (default 5.0 s, ceiling 60 s)
   via the existing `screen.capture_with_fallback` — accessibility first,
   pixels only if the session authorised the fallback; requested scope *is*
   the approved scope, so nothing widens.
3. **Classify and separate** into an `ObservationEvent` (`window_state`, or
   `unavailable` when the provider could not read). The facts carry the
   bounded **content**: up to 24 elements (role/name/value/focused/selected),
   `element_count`, `elements_truncated`, focused element, `omitted_secret_fields`,
   window id/title/application, `idle_seconds` (if the provider offers
   `read_idle_seconds()`), screenshot-fallback facts, evidence reference.
4. **Emit** through the one installed `MultimodalEventSink`
   (`events.get_event_sink()`, which W03-F's `install_seams` points at
   `CanonicalIngressSink`). A refused or failed record ends the session
   `FAILED` with the reason: the loop never keeps observing what it cannot
   record. Session lifecycle (`multimodal.session.state`) is emitted at loop
   start and end, best-effort.

Counters (ticks, emitted, last event id, last error, end reason) appear on
`GET /api/multimodal/status` per live session; content never does.

### 1.4 Read-back (`readback.py`) — the Verify leg's read query

```python
from bartholomew.multimodal.readback import read_back, ReadBackResult
from bartholomew.multimodal.modality import CaptureScope, ScopeKind

result = read_back(
    tenant_id="<tenant>", device_id="<device>",
    target=CaptureScope(ScopeKind.WINDOW, window_id="..."),   # or None: whatever is consented
    requested_by="user:<id>",          # the action's principal; content principals refused
    db_path="<kernel db>",             # brake re-read; defaults like the seams
    store=None,                        # defaults to the process-wide registry
)
result.available      # bool
result.text           # bounded UI-state text (observed half only), or None
result.code           # when unavailable: requester_refused | no_consented_session |
                      #   outside_consented_scope | parking_brake_engaged |
                      #   parking_brake_unreadable | provider_unavailable | read_failed
result.reason         # human-readable
result.event          # ObservationEvent(kind="read_back", no inference)
result.event_id       # backbone event id the read was recorded under
result.provenance_degraded / provenance_error   # read succeeded, record failed
```

Governance is the *existing* order, not a second authority: a read is served
**only** from a live, consented `ACTIVE` screen session for that tenant and
device whose approved scope `covers()` the target; read-back **never starts a
session**; the brake is re-read at the moment of the read (engaged → the
session is stopped through `stop_all_for_brake` and the read is unavailable;
unreadable → unavailable); a model/event/companion/system `requested_by` is
refused; a target outside the consented scope is refused, not approximated.
What comes back is observed content only — a Verify verdict must not be
checked against a guess. Every read is itself emitted as evidence.

**For W03-C:** the read is server-side and in-process; there is no HTTP route
for it (so `route_policy` is untouched). The default `store` is
`bartholomew.multimodal.store.default_store()` — the same registry the routes
show. The signature above is the published surface; changes to it go
through W03-PREP.

### 1.5 Smaller changes inside the boundary

- `events.build_envelope` takes an optional `occurred_at`; new
  `serialize_observation_event`.
- `store.py`: process-wide `default_store()` (the route's `get_store()` now
  returns it; test overrides of `routes.multimodal.get_store` still work),
  `attach_observer` / `observer`.
- `status.py`: per-session `observation` counters.
- `accessibility.py`: optional `read_idle_seconds()` on the provider
  protocol; the UIA provider implements it via `GetLastInputInfo`
  (`pragma: no cover`, read-only — no hook, no synthesized input).
- `runtime.py`: the microphone transcript the wave-two adapter produced but
  returned to nobody is now emitted to the same sink when a listening session
  finishes (a refused record fails the session honestly). `SessionStartResult.
  as_dict()` drops underscore-prefixed in-process handles from the wire.
- `routes/multimodal.py`: no new routes; the start route's docstring records
  that an `ACTIVE` screen session is now driven. `routes/device_consent.py`:
  unchanged.

### 1.6 Explicitly not done (by contract)

No actuation of any kind; no autonomous capture start (every session still
begins with an explicit request and an interactive consent answer; read-back
starts nothing); no interpretation-to-decision; no raw audio/image retention;
no memory write (the governed `MemoryStore.upsert_memory` path is W03-D's
concurrent schema work — observations reach the kernel through the backbone,
and persisting one as a memory row is a consumer's decision, see §5); no
change to `event_processing`, `runtime_contract`, `install.py`, `app.py`,
`route_policy`, the manifest, or any other session's files.

---

## 2. Acceptance criteria — results

| # | Criterion | Result | Proven by |
|---|---|---|---|
| 1 | Observation loop tick produces an `ObservationEvent` with distinct `observed_event`, `inferred_state`, `confidence`, `competing_explanations`, provenance; the 20-minutes-inactive non-collapse test | **met** | `test_w03a_observation_event.py::TestNonCollapse`, `TestContractRefusals`; `test_w03a_observation_loop.py::TestTickShape`; `test_w03a_integration.py::test_twenty_idle_minutes_reaches_the_backbone_without_a_human_state` |
| 2 | Emitted into the canonical backbone; claimable by exactly one consumer; retry collapses to one row; no second bus (registry assertion) | **met** | `test_w03a_integration.py::test_an_observation_lands_in_the_one_ingress_and_is_claimable_once`, `::test_a_retry_collapses_to_one_logical_row`; `test_w03a_observation_event.py::TestNoSecondEventBus` (AST + registry = the five wave-two types) |
| 3 | Kernel receives observation content, not merely `element_count` | **met** | `test_w03a_observation_loop.py::TestTickShape::test_the_kernel_receives_content_not_merely_a_count` (through `MultimodalObservationPayload.parse`, the parser the registry uses); integration row contains the element text |
| 4 | Governed read-back primitive with honest "unavailable"; exposed for W03-C | **met** | `test_w03a_readback.py` (24 tests: every unavailable code, success text, no inference, emitted as evidence, degraded record reported); `test_w03a_integration.py::test_content_principals_can_neither_start_nor_read` |
| 5 | Every capture path passes the existing governance order; engaged brake (global or scope) stops an in-flight session within a bounded interval; `stop_all_for_brake` wired | **met** | `test_w03a_observation_loop.py::TestBrakeAndExpiry` (scope brake via `stop_all_for_brake`, running-thread bound < 2 s, unreadable → fail-closed, expiry); `test_w03a_integration.py::test_a_brake_engaged_mid_session_ends_the_running_loop_within_bound[sight,global]` against the real `GovernanceStore`; `::test_a_brake_engaged_before_start_refuses_at_the_seam` |
| 6 | Privacy/retention: no raw audio/image persisted; secret content refused/redacted with the redaction recorded; truncation recorded | **met** | `test_w03a_observation_loop.py::TestPrivacy` (password field omitted + `RESTRICTED`, secret text `[redacted]` + record, element bound recorded, summary bound recorded, no raw fields); new modules open no file for writing (AST/source assertion) |
| 7 | Envelope `tenant_id` disagreeing with the process binding is refused, not re-attributed | **met** | `test_w03a_observation_loop.py::TestEmitRefusals` (session `FAILED`, reason names the refusal); `test_w03a_integration.py::test_a_tenant_mismatch_is_refused_by_ingress_and_writes_nothing` through the real `CanonicalIngressSink` |

Adversarial/governance (contract §Testing): a poisoned accessibility label
carrying imperative text is stored verbatim as observation content; the
seam's `CandidateAction.kind` stays `multimodal_screen_capture`; the inference
is identical to a benign label's; the real `process_batch` pass settles it as
an observation with no objective opened
(`test_a_poisoned_label_is_stored_as_content_and_grants_nothing`). A session
started with a `model:` / `companion:` / `event:` principal is refused
(existing suite + `test_content_principals_can_neither_start_nor_read`, which
extends the rule to read-back). **W03-D coordination:** the assertion "stored
imperative text does not change a `CandidateAction` kind" is made here on the
multimodal seam's own candidate action and on the backbone disposition; the
memory-side half (a recalled observation cannot change a kind or grant
authority) is W03-D's to assert on its retrieval verdict — W03-A's payload
gives it `observed_event` / `inferred_state` as distinct fields to key on.

---

## 3. Tests and CI

New suites (all in the default marker set except the integration file):

| file | tier | tests |
|---|---|---|
| `tests/test_w03a_observation_event.py` | PR Fast | 34 |
| `tests/test_w03a_observation_loop.py` | PR Fast | 31 |
| `tests/test_w03a_readback.py` | PR Fast | 24 |
| `tests/test_w03a_integration.py` (`@integration`) | Integration `critical` | 10 |

Verification run locally on the frozen head, per `W03_CI_BASELINE.md`:

| check | result |
|---|---|
| `pre-commit run --all-files` (black, ruff, hygiene hooks) | pass (black, ruff, end-of-file, trailing-whitespace, private-key, yaml, large-files) |
| `tests/smoke/test_packaging_contract.py` + `tests/test_wave_manifest.py` | pass (14 tests) |
| default suite, `-n auto --dist loadfile`, `--cov-fail-under=70` (Integration `tests-coverage`) | pass — 4242 passed, 2 skipped, 7 min 10 s; coverage 79.40% (gate 70%) |
| `-m "integration or slow"` + clean-start lifecycle + scheduler readiness + parking-brake governance (Integration `critical`) | pass — integration/slow: 140 passed, 21 skipped (12 min 17 s, serial); lifecycle + readiness + brake: 33 passed |
| existing multimodal / Session F / companion / consent suites (309 tests) | pass, unchanged |

GitHub Actions on PR #93: the PR Fast quality and smoke jobs are green on `99123d6` (a first quality run on `5496b4d` failed on black's magic-trailing-comma layout in two test signatures and was fixed by `99123d6`); the Integration tier runs on the draft under the `ci:integration` label. The frozen head is the commit carrying this handoff; its PR Fast + Integration results are on the PR's checks tab and in W03-A's closing status, and the head is declared frozen only once both are green.

Real UIA / `mss` backends and the Win32 idle probe stay `pragma: no cover`
and are exercised only in the Nightly Windows tier / live retest, as the
contract requires. Hardware is simulated here by controlled providers; nothing
governance-shaped is mocked in the integration file (real seam, real
`GovernanceStore`, real `inbound_events`, real sweep/claim, real
`process_batch` with the real interpretation seam and objective store).

---

## 4. Files

Owned by W03-A (manifest `owns`):

- `bartholomew/multimodal/observation.py` (new), `inference.py` (new),
  `loop.py` (new), `readback.py` (new)
- `bartholomew/multimodal/__init__.py`, `accessibility.py`, `events.py`,
  `runtime.py`, `status.py`, `store.py`
- `bartholomew_api_bridge_v0_1/services/api/routes/multimodal.py`
- `bartholomew/integration/multimodal_events.py` — **unchanged** (the sink
  already refuses tenant mismatch and collapses retries; W03-A's events ride
  it as-is)
- `bartholomew_api_bridge_v0_1/services/api/routes/device_consent.py` — unchanged
- tests: `tests/test_w03a_*.py` (new); this handoff.

Nothing outside that list was modified.

---

## 5. Residual risks and integration notes (for W03-F, W03-B, W03-C, W03-D)

1. **Unbound-process attribution (pre-existing, W03-F to confirm).**
   `CanonicalIngressSink` writes `runtime_id = self.runtime_id or
   envelope.tenant_id`. In an *unbound* process (`BARTH_RUNTIME_USER_ID`
   unset) the sink is installed with `runtime_id=None`, so multimodal rows
   are attributed to the enrolment account id, while the backbone's
   processor claims `runtime_id IS NULL` — such rows would be captured but
   not claimed. The recorded live procedure (`docs/G` §8) binds
   `BARTH_RUNTIME_USER_ID` to the account, in which case ids match and rows
   are claimed (the integration tests run bound). W03-A left the sink's
   attribution rule alone — it is the platform binding model, not
   perception — and flags it here: the live retest should run bound, or
   W03-F should decide the unbound rule in `install.py`.
2. **Interpretation is still keyword matching.** Observation content now
   reaches `interpret_captured_event` with confidence and competing
   explanations alongside; the seam itself still text-matches against live
   objectives (its own package). W03-B consumes the structured payload
   (`observed_event` / `inferred_state`), not the seam's verdict, for
   decisions.
3. **No memory row is written by W03-A.** Persisting an observation as
   evidence through `MemoryStore.upsert_memory` was permitted, not required;
   with W03-D changing the `memories` schema concurrently (provenance /
   validity / supersession fields, and the observation-kind expiry rule),
   writing rows now would couple to a moving shape. The payload already
   carries what D's row needs (`provenance`, `confidence`, `classification`,
   `observed_event` vs `inferred_state`). Wiring the write is a one-call
   consumer step once D's shape is frozen; W03-A recommends W03-F or W03-D
   own it.
4. **Loop cadence in tests.** `start_session` starts the thread by default;
   every W03-A test that starts one stops it and joins with a bound. Existing
   wave-two tests that start screen sessions without stopping them leave a
   daemon thread polling the brake once a second until the process ends —
   harmless, but W03-F's integration suite should stop what it starts, or
   pass `run_observation_loop=False` and tick explicitly.
5. **Idle time is provider-supplied and optional.** Only the UIA provider
   reads it (Win32 `GetLastInputInfo`, `no cover`). Off Windows `idle_seconds`
   is `None` and no presence inference is made — which is the honest answer,
   not a gap to paper over.
6. **Verify on a real desktop is not established here.** The read-back path
   is proven against a controlled provider; the live `type_text` + read-back
   verification is W03-C's / W03-F's real-world-test target, as the contract
   records.

---

## 6. Deferrals honoured

Every session is explicitly requested and interactively consented (deferral
6: no autonomous capture-start); screen/accessibility only, bounded and
consented (deferral 1); the microphone backend stays pluggable with no STT
shipped (deferral 14); nothing here acts (deferral 5/7).

---

## 7. Statement

W03-A is complete within its published boundary: the Observe leg runs, the
observation event separates evidence from inference by construction, the
event lands on the one backbone and is claimable once, the brake stops a live
session within a bounded interval through the previously uncalled
`stop_all_for_brake`, and the Verify leg has a governed, honest read-back to
consume. The head is frozen for W03-F; W03-B and W03-C can build against the
published `observation-event` shape and `read_back` signature.
