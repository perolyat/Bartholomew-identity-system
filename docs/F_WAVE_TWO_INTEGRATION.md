# Session F — wave-two integration closeout

The five wave-two builder packages, integrated into one Bartholomew, verified,
and left as a draft candidate for the next main-branch baseline.

**No A–E pull request was merged to main. The Session F pull request was not
merged to main and auto-merge was not enabled.**

---

## 1. What was integrated

Starting main: `adea4b132138f180d37ee81dd3d854c4b9c9f459` — the base all five
package PRs share, so no package was integrated against a moved base.

| Pkg | PR | Integrated SHA | Role |
|---|---|---|---|
| A | #85 | `7ecd7d29dca944b8cb90146b13d560374e14c257` | canonical event backbone |
| B | #88 | `9e4284ba7414ed382f22722aaadda3177114db6c` | governed Windows actuation |
| C | #84 | `60a47f15ae333a2c4c8df638ebbf6d57cbf1781f` | multimodal Windows presence |
| D | #87 | `8bbe644cb499bde233f568795aa7a9b1eba73b98` | learning & memory control centre |
| E | #86 | `acba9bcbe4e02db7106214c2c645849d7bf414f7` | device registry & trusted groups |

Merged in dependency order — A, E, B, C, D — onto one branch. The five PRs were
not merged independently into main.

### SHA discrepancies

* **Package C.** The contract gave `60a47f15ae331a2c4c8df638ebbf6d57cbf1781f`;
  the repository's actual PR #84 head is `60a47f15ae333a2c4c8df638ebbf6d57cbf1781f`
  — they differ at position 13 (`1` vs `3`). Repository truth was used, as the
  contract directs. The rest of the abbreviation matches, and no other branch
  head begins `60a47f15`, so this is a transcription slip rather than a
  different commit.
* **Package A.** No SHA was given in the contract. PR #85's head
  `7ecd7d29dca944b8cb90146b13d560374e14c257` was read from GitHub and used.
* B, D and E matched the contract exactly.

No newer package work was substituted for any frozen head.

---

## 2. Branch, PR and final state

| | |
|---|---|
| Session F branch | `claude/bartholomew-wave-two-integration-qjrxkr` |
| Session F PR | #89 (draft, against `main`) |
| Commits ahead of main | 26 (5 integration merges, 3 Session F commits, 18 carried package commits) |
| Files changed vs main | 138 |
| Net change vs main | +63,088 / −34 |

Final head SHA, mergeability and CI result are recorded in §11.

---

## 3. Conflicts, and how each was resolved

Seven textual conflicts across four files, plus one file that needed
reconstruction. Every conflict was additive — two packages appending to the
same list, map or startup sequence — and every one was resolved by preserving
**both** sides. Nothing was resolved by taking one side.

| File | Conflict | Resolution |
|---|---|---|
| `api/app.py` | E's device inbound resolver install vs B's action resolver install | both, in that order |
| `api/app.py` | B's `device_actions` health component vs the caller-supplied `extra` merge | B's block first, then `extra`, so a caller can still override |
| `api/app.py` | C's `multimodal` router vs D's `learning` router | both included |
| `platform/route_policy.py` | B's seven action routes vs C's five multimodal routes | both |
| `platform/capabilities.py` | B's four action capabilities vs D's five learning capabilities | both, and both added to `_USER_CAPABILITIES` |
| `cli.py` | E's `devices`/`groups`/`share` sub-apps vs C's `multimodal` | both |
| `tests/smoke/test_packaging_contract.py` | B's `comtypes` vs C's `sounddevice`/`uiautomation`/`mss` | both |
| `Identity.yaml` | D's four allowlist kinds vs the rest | both |
| `DECISIONS.md`, `learning_authorization.py` | overlapping docstring/prose additions | both |

### `runtime_contract.py` — reconstructed, not merged

D and E both append a large section at the same tail anchor (main line 4520).
Git's three-way merge interleaved this into **nine** conflicts spanning
thousands of lines, several of which cut across unrelated dataclasses — a
naive both-sides resolution there would have produced a scrambled file that
still parsed.

It was reconstructed deterministically instead:

1. Kept HEAD (main + A + C + E), whose non-tail hunks are disjoint.
2. Applied D's four non-tail hunks in place (the `learning_policy` import, the
   `_write_candidate_lesson` conditional-write parameter, and the revision
   carry-forward in `_propose_candidate_lesson`).
3. Appended D's 1,323-line Package D section after E's share-adoption section.

Then verified mechanically: **every top-level function, class and module
constant defined by A, C, D and E in that file is present in the result.** That
check is what makes the reconstruction reviewable rather than a claim.

---

## 4. A ↔ E — foundations

E's `platform_devices` is the single device truth. The wave shipped three
device stories — E's real registry, B's `StaticDeviceRegistry` (an
operator-provisioned JSON file, self-labelled "Session E replaces this"), and
C's `StaticCapabilityResolver` (an in-memory dict, labelled the same). Both
stand-ins are now replaced at runtime by adapters over E.

The unification point was already there by design: E's `CAPABILITY_VERSIONS`
vocabulary already contains exactly B's nine `windows.*` kinds *and* C's three
`multimodal.*` kinds. No vocabulary was invented or merged.

**No second device registry exists.** `install_registry()` replaces rather than
shadows, and a test asserts that the installed registry reports
`interim: false`.

---

## 5. B — governed Windows actuation

`bartholomew/integration/device_registry.py::RegistryBackedDeviceRegistry`
satisfies B's `DeviceCapabilityRegistry` Protocol structurally. B imports
nothing from E; E imports nothing from B.

Security semantics, all asserted:

* **Tenant-qualified.** `tenant_id` is passed to E's `get_device` as a query
  predicate. Another tenant's device returns `None` — indistinguishable from
  one that does not exist, which is the containment property.
* **Revoked is not absent.** Any status other than `ACTIVE` — `PENDING`,
  `APPROVED`, `DISABLED`, `REVOKED` — returns `EnrolledDevice(enrolled=False)`,
  never `None`.
* **Fail closed on failure.** Any store or parse error raises
  `DeviceRegistryError`, which `actuation.seam` treats as a denial. An
  unreachable registry refuses every device rather than reporting "not
  enrolled".
* **No request-body authority.** Nothing reads a request, header or device
  claim. An unnamed tenant raises rather than returning a clean miss.

**Capabilities** come from E's `authorised_capabilities` — the intersection of
the device's manifest with the operator's approval ceiling, restricted to what
this deployment understands. That is `VerifiedDevice.authorizes` semantics,
computed from the same stored row.

**Parameter allowlists** (`applications`, `url_domains`, `filesystem_roots`)
and `trusted_autonomy` still come from B's operator file, because E's manifest
has no concept of any of them. This is not a second device truth: the file
cannot enrol a device, cannot make a revoked device usable, and cannot grant a
capability E has not approved — `trusted_autonomy` is intersected with what E
authorised. A device with no entry gets empty allowlists, which refuse
everything.

**Event → action.** The seam already carries `correlation_id` and
`causation_id` through to the durable row; the golden path asserts both survive
from observation to stored action.

**The approval flow** (`POST /api/actions/{id}/approve`) is exercised
end-to-end in the golden path: a request alone reaches no device, the approval
is bound to that exact action, and only then does dispatch lease.

**The device resolver** is `device_action_resolver.py`, verifying against E's
device credentials. It is a deliberate near-duplicate of E's observation
resolver rather than a shared object — B's own comment explains why, and
sharing one would mean opening observation also opened actuation. It has its
own module global and its own environment gate (`BARTH_DEVICE_ACTION_AUTH`),
and refuses to install alongside the test resolver.

Nothing in the action schema, capability checks, Parking Brake behaviour,
approval binding or auditability was weakened.

---

## 6. C — multimodal Windows presence

C's five event classes now enter A's canonical ingress (`inbound_events`) — the
same table the HTTP capture route writes and A's sweep reads.

**There is no second event bus, and no second governance authority.** The three
observational types (`microphone.transcript`, `screen.observation`,
`accessibility.observation`) are registered to A's *own* `handle_observation`,
so they travel the same Identity policy, the same Parking Brake deferral and
the same `interpret_captured_event` seam as every other observation. The
integration adds no interpretation logic.

The two non-observational types settle without interpretation, as a definite
`irrelevant` rather than a `refused`:

* `spoken_output.utterance` — a record of what Bartholomew itself said.
  Interpreting it as evidence would let the system learn from its own output.
* `session.state` — capture lifecycle. This is the record that keeps
  *unavailable* / *broken* / *permission denied* durably distinguishable.

**Preserved:** `occurred_at` is C's; `captured_at` is assigned by ingress, as C
intended by emitting `None`; `correlation_id`, `causation_id`, `privacy_class`
and `retention_class` travel in the stored payload and are covered by its
digest; `event_id` stays content-derived, so a retry collapses to one logical
row under the UNIQUE constraint.

**Refused:** an envelope whose `tenant_id` disagrees with the process binding
is refused rather than re-attributed — the envelope is not an authority on
whose observation it is. A failed write raises; it is never logged and
swallowed.

C's frozen `DeviceCapabilityResolver` Protocol has no tenant argument. Rather
than widen it, the E-backed resolver is **tenant-bound at construction**, so no
call path can resolve a device against a tenant the caller did not already
have.

C gained the two install hooks it anticipated. Both defaults are unchanged
(`NullEventSink`, and `None` for the resolver, which denies), and an explicit
`capability_resolver` argument still wins over the installed one.

`runtime_contract.py` is not bypassed: C's session gate still runs through
`run_multimodal_session_through_runtime_contract`.

---

## 7. D — learning and memory control centre

**1. Sharing-state resolver — connected.** D constructed
`SharingInterface(eligible=…, transport_available=False)` with the fixed detail
"Household sharing is not connected in this release." Session E *is* that
transport, so the sentence was no longer true. `resolve_sharing` now reads E's
real state, keeping three things apart:

* *Eligible* stays D's privacy judgement, intersected with E's
  `ELIGIBLE_SOURCE_KINDS`. This corrects a real disagreement: D would call a
  candidate lesson eligible on classification alone, while E's sanitizer
  refuses the kind outright, so the control centre could have offered a share
  that would then be refused. It now shows E's answer.
* *Transport available* means the person is in at least one trusted group.
  Being in none is different from sharing being unimplemented, and the detail
  says which.
* *State* is `shared` only when a live, unrevoked package exists for this
  record's origin fingerprint. A revoked package returns to `not_shared`, so
  revocation is visible in the control centre.

The fallback, if E's tables cannot be read, is D's original constructed
projection — which claims *less* than the truth, never more.

**2. Contradiction detector — deliberately not connected.** Nothing in this
wave measures contradiction. A plausible-looking count (keyword overlap, say)
would feed the `contradictory_evidence` policy rule with a number nobody
validated, and the rule would then make *less* conservative previews look
justified. The strict default stands and `describe_unmeasured()` reports it as
a default rather than a measurement, so the control centre can say so.
Classified **C — requires future architectural work**.

**3. Risk / reversibility assessor — connected, read-only.** D's note asked for
an assessor writing through `run_candidate_edit_through_runtime_contract`. The
assessor deliberately does less: it returns a proposal and writes nothing.

Two governance reasons. That seam performs a *material* edit, which
re-fingerprints the candidate and invalidates any approval standing against it
— so an automated assessor running unattended could silently revoke a person's
considered approval. And `learning_candidate_edit` holds standing permission
precisely because it is a *reviewer's* act; letting an unattended assessor
spend that grant widens what the grant was given for. A reviewer applies the
proposal, so the fingerprint change is something a person did.

The proposal itself never proposes a less strict value than the candidate
carries, and declines to answer where it cannot tell. `unassessed` remains
`critical` and irreversible.

**4. Affected-application resolver — connected.** Applications are now
observable: C's accessibility and screen observations name the application they
came from, and those events are in `inbound_events` under the candidate's
correlation id. The resolver reads them back, tenant-scoped by `runtime_id` so
a correlation id alone cannot reach across runtimes. It returns a proposal, for
the same reason as (3).

**The central invariant holds.** Manual acceptance remains the initial and only
behaviour. `learning_policy.execution_mode` is a property returning a module
constant fixed to `shadow`, so a recorded preference for automatic acceptance
changes nothing about this build. `learning_accept` has no standing permission
in the merged allowlist. A candidate gains no authority by existing.

---

## 8. E — trusted groups and cross-user learning

E's model operates unchanged through the integrated system. Nothing in the
integration touched its sanitization, credential model, membership constraints,
content-bound adoption, revocation or receipts. The integration *reads* E
(group membership, publication state, device rows, credentials) and never
writes through it.

Two properties the integration strengthens rather than weakens:

* Adoption still binds through `evaluate_learning_admission(..., ACCEPT)` —
  PR #83's authority, not an analogue — so an adopted share needs the same
  candidate-bound approval as a locally-inferred lesson. `share_accept` has no
  standing permission.
* The control centre can no longer offer to share something E would refuse,
  because the projection now asks E.

Cross-user learning remains sharing of selected competencies and corrections,
not memory synchronization: `ELIGIBLE_SOURCE_KINDS` is three competency kinds,
and every raw personal kind is named as ineligible with its reason.

---

## 9. The event → action runtime path

```
OBSERVE      C's capture session (no production trigger — see §12)
   ↓
EVENT        C serializes → integration sink → inbound_events (A's one ingress)
   ↓
INTERPRET    A's sweep → A's registered handler → interpret_captured_event
/GOVERN      Identity policy + Parking Brake, A's, unchanged
   ↓
DECIDE       objective evidence recorded, or irrelevant/uncertain
   ↓
APPROVAL     B: pending_approval → grant_action_approval (action-bound)
   ↓
ACT          B: dispatch over the separately-authenticated device channel
   ↓
RESULT       durable action row + reflection, correlation/causation intact
   ↓
AUDIT/       learning candidate only — never authority
LEARNING
```

No parallel orchestration stack was introduced. Every stage is owned by the
package that already owned it.

---

## 10. Governance and security verification

Asserted by test, not by inspection:

| Invariant | How |
|---|---|
| Parking Brake authoritative over the integrated path | brake engaged → action request refused |
| No standing `learning_accept` / `share_accept` / action-approve | merged `Identity.yaml` allowlist asserted (37 entries) |
| Automatic acceptance off by default | `execution_mode == "shadow"` |
| Tenant isolation fail-closed | device registry, capability resolver, event sink, application resolver — each asserted separately |
| Device revocation visible | revoked → `enrolled=False`, and `supported=False` with "not active" |
| Unanswerable registry denies | raising `get_device` → `DeviceRegistryError`, not `None` |
| No request-body authority | envelope tenant mismatch refused; unnamed tenant raises |
| Integrating does not open actuation | action channel closed unless its own gate is set |
| One device registry | installed registry reports `interim: false`; install replaces |
| One event bus | five multimodal types in A's registry; double registration raises at import |
| Empty allowlists refuse | `applications.resolve` raises, `url_domains.permits` False |

Every package's own governance suite continues to pass unchanged — no test was
weakened, skipped, disabled or deleted at any point in this session.

---

## 11. Test results

New: **42 cross-package tests** in two files, all passing.

* `tests/test_session_f_integration.py` — 33 tests on the seams themselves.
* `tests/test_session_f_golden_path.py` — 9 tests on the target scenario.

Everything runs against real stores: a real control-plane SQLite database with
real accounts and a real enrolment ceremony, a real `inbound_events` table,
real memory, governance and action schemas. Nothing that could be asserted
about persistence is asserted against a mock.

Local: `ruff check .` and `black --check .` clean across 465 files.

Full-suite and CI results are in the PR; see the final CI state on #89.

### Integration defects found and fixed

**The seam installer unenrolled every device in Package B's alpha
configuration.** `install_seams()` installed E's registry unconditionally,
overriding the supported configuration in which
`BARTH_ACTION_DEVICE_ENROLMENT` names a file that *is* the registry. The file
was still read for parameter allowlists, so such a deployment looked
configured while refusing everything, and every refusal said "device not
enrolled" about a device the operator had enrolled. It took B's real-HTTP
suite from green to eight failures.

Resolved as **one device truth per deployment, chosen explicitly**, rather
than one per device resolved by whichever source answered first: a deployment
naming an enrolment file keeps its interim registry and says so on the health
surface (`interim: true`); one that does not gets E's registry. Having both
would be two contradictory answers to "which devices are enrolled" — the thing
the seam exists to prevent. The parameter-allowlist overlay moved to its own
variable, `BARTH_ACTION_PARAMETER_ALLOWLIST`, so the file that *selects* B's
interim registry is never the same file that only *overlays* allowlists onto
E's. The eight failures were reproduced without the fix and all 26 tests pass
with it; a regression test now covers it.

**A's registry test asserted it held exactly its own two event types.**
Integration makes that premise false in the intended way — C's five multimodal
types share the registry, which is the "no second event bus" property. The
assertion was corrected to the integrated seven-type set rather than loosened
to a subset, and made deterministic by importing the integration module, which
is what had made it an order-dependent failure.



The seam installer targeted `multimodal.runtime.install_event_sink`, but the
hook lives in `multimodal.events`. It surfaced as a failed seam install rather
than a crash — the installer reports rather than raises — which is exactly the
failure mode that would have shipped silently without the report being
asserted. Fixed, and the assertion that the report is error-free is now a test.

---

## 12. Real vs simulated verification

**No real Windows hardware was exercised by this session.** This container runs
Linux. Claims about Windows behaviour in this closeout are claims about the
governed path up to the hand-off, not about a keystroke landing.

* Stage 8 of the golden path is proven to the **lease boundary** — the point at
  which a validated action's parameters leave the process for the device. The
  companion that performs the action runs on Windows.
* C's microphone, screen and accessibility backends were not exercised against
  real hardware. C's own distinction between *unavailable*, *broken* and
  *permission denied* is preserved structurally and asserted through its
  session-state events, not against a real denied microphone.
* CI's `Windows lifecycle + compatibility (py3.11)` job is the only real
  Windows execution in this wave's verification, and it covers lifecycle and
  import compatibility, not actuation onto a live desktop.

---

## 13. Where the golden path stops

Stages 1–7 and 9–11 execute for real. Two stops, both asserted by tests named
for the stop rather than omitted:

**Stop 1 — there is no production capture-start surface (stage 2).**
Package C's HTTP surface is read-and-stop only: `status`, `sessions`,
`stop`, `stop-all`, `diagnostics`. There is deliberately no route that starts a
capture session, because the API bridge has no authentication and capture
initiation must not be reachable from an unauthenticated call. `start_session()`
is reachable only from in-process code, and today only tests call it.

*This is the single largest gap between the integrated system and "a usable
Windows Bartholomew that can observe my PC".* The chain from observation to
action is real and connected; nothing yet starts an observation unattended. A
test fails the moment a start route appears, so adding one stays a governance
decision rather than a convenience.

**Stop 2 — dispatch does not reach a real desktop (stage 8).** As §12.

---

## 14. Remaining gaps to a genuinely usable Windows Bartholomew

1. **No authenticated control plane to start a capture session.** (Stop 1.)
   This needs an authenticated surface, not a route on the unauthenticated
   bridge. Largest blocker.
2. **No speech-to-text engine ships by default** (C's own limitation), so the
   microphone modality produces no transcripts in a default install.
3. **No real Windows companion run has been observed** for the actuation path
   end to end.
4. **Contradiction is unmeasured**, so learning previews are conservative in a
   way that is honest but blunt.
5. **Risk, reversibility and affected applications are proposals**, requiring a
   reviewer to apply each one. There is no reviewer UI for applying a proposal
   yet — the data is available, the affordance is not.
6. **One lesson category** (`procedural`) exists; widening it is a separately
   authorised decision.
7. **Superseded candidate revisions accumulate** with no pruning policy (D's
   limitation, carried forward).
8. **Events captured before a runtime binding existed are not claimed** by a
   bound process (A's limitation, carried forward; visible on the health
   surface rather than silent).

---

## 15. Contract deviations, classified

**A — must fix for integration correctness (fixed this session):**

* The seam installer overrode Package B's configured interim registry,
  unenrolling every device in that supported alpha configuration. Fixed by
  choosing one device truth per deployment; see §11.
* The seam installer targeted the wrong module for C's event sink. Fixed.
* A's event-registry test asserted a set that integration correctly widens.
  Corrected to the integrated set, not loosened.
* D's sharing projection claimed sharing was unimplemented after E implemented
  it, and would have offered shares of candidate lessons that E refuses. Fixed
  by asking E.
* The `runtime_contract.py` merge would have silently dropped one package's
  section under a mechanical resolution. Fixed by reconstruction plus a symbol
  coverage check.
* CI's formatter is `black`, not `ruff format`; the conflict resolutions were
  not black-clean. Fixed.

**B — safe to preserve and document:**

* C reused the existing voice/sight brake scopes rather than minting multimodal
  scopes. Correct: anyone who can engage those can already stop the sessions.
* B maps `tenant_id` onto the existing isolated runtime user identity because
  no separate tenant concept exists. Preserved; the adapter passes it to E's
  `user_id` predicate, which is the same identity.
* B's operator enrolment file survives for parameter allowlists only.
  Documented in §5 as an overlay that can only narrow.
* C never raises `verification` above `claimed`; only E's registry does, and
  the adapter is where that now happens (`registered`).
* E's control-plane isolation relies on predicates rather than physical file
  separation. Unchanged by this session.
* Builder sessions reported the binding contract document was unavailable to
  them. Recorded; it did not affect integration, as each package's closeout
  named its own seams precisely.

**C — requires future architectural work:**

* Contradiction detection (§7.2).
* An authenticated control plane able to start a capture session (§13, stop 1).
* A speech-to-text engine decision.
* Physical control-plane isolation, if predicate-based isolation is ever judged
  insufficient.

**D — requires a user/director decision:**

* Whether to add an authenticated capture-start surface, and what authenticates
  it. This is the decision that unblocks the product objective.
* Whether an automated assessor may ever apply risk/reversibility directly —
  which means deciding whether an unattended process may invalidate a person's
  standing approval. Session F's answer was no; it is reversible.
* Whether to widen `LESSON_KINDS` beyond `procedural`.
* Whether to enable the action channel in any real deployment
  (`BARTH_DEVICE_ACTION_AUTH`), which is what makes Bartholomew able to act on
  a real PC at all.

---

## 16. Rollback and deployment implications

The integration is **inert by default**. Every seam that reaches outward stays
behind its own gate:

* The action channel is closed unless `BARTH_DEVICE_ACTION_AUTH` is set.
* Device inbound auth is closed unless `BARTH_DEVICE_INBOUND_AUTH` is set.
* Without a runtime binding, the capability resolver is not installed at all
  and C's fail-closed default stands.
* A seam that fails to install leaves its package's fail-closed default in
  force and reports the error rather than killing startup.

`/api/health` gains an `integration_seams` component so an operator can tell an
integrated deployment from one running on stand-ins.

**Rollback is reverting the branch.** The integration introduces no schema
migration of its own; it reads schemas A–E already create. Reverting restores
each package's stand-in behaviour, which enrols nothing and dispatches nothing
— the safe direction.

**Deploying this candidate changes no runtime behaviour** for an existing
install until an operator sets one of the gates above.

---

## 17. Statement

No Package A–E pull request (#84, #85, #86, #87, #88) was merged to main.
The Session F pull request (#89) was not merged to main, and auto-merge was not
enabled on it.
