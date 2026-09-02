# Learning and Memory Control Centre (Package D)

One place where a person can see what Bartholomew believes he has learned, what
he is proposing to learn, what a future auto-acceptance policy *would* have
decided, and what any of it stands on — and can act on all of it without a
single write reaching the database except through an authority that already
existed.

**Automatic acceptance is structurally disabled in this release.** The policy
system is built in full and runs in shadow mode only. See
[Structural proof](#structural-proof-that-automatic-acceptance-is-disabled).

---

## What was added

| Layer | File | Role |
|---|---|---|
| Policy engine | `bartholomew/kernel/learning_policy.py` | Pure data + deterministic evaluator. No I/O. |
| Governed seams | `bartholomew/kernel/runtime_contract.py` (additive tail) | Shadow evaluation, policy read/update, candidate edit, competency correction and revocation. |
| API | `bartholomew_api_bridge_v0_1/services/api/routes/learning.py` | `/api/learning/*`, a translation layer over those seams. |
| UI | `bartholomew_api_bridge_v0_1/ui/minimal/index.html` | A third view, `#/learning`. |

Nothing here is a second memory authority, a second approval system, a second
privacy engine or a second audit log. Every write goes through
`MemoryStore.upsert_memory()`; every decision is audited through the existing
`ActionReflection` sink; acceptance still runs through PR #83's
candidate-bound approval.

---

## Memory kinds and migration

**No schema migration is required.** Three new `MemoryStore` *kinds* are
introduced. `memories` is a `(kind, key, value, ts)` table with no per-kind
schema, so a new kind is new rows, not new columns:

| Kind | Key shape | Contents |
|---|---|---|
| `learning_policy` | `default`, plus `default@r<N>` archives | One tenant's policy configuration, versioned. |
| `learning_shadow_evaluation` | `<competency_id>.<slug>@<policy revision>` | One inspectable preview. |
| `candidate_lesson_revision` | `<competency_id>.<slug>@r<N>` | A superseded candidate revision, archived on material edit. |

All three are **absent from `competency.COMPETENCY_KINDS`**, so the chat
retrieval seam's kind filter (`COMPETENCY_KINDS + PERSONAL_FACT_KINDS`) cannot
see them. Nothing here can be cited as knowledge by a later reasoning turn.

All three are registered with `privacy_guard.register_structural_schema()`, so
their schema *key names* are treated as structure rather than content. Every
**value** is still scanned in full.

### Changes to an existing kind

`candidate_lesson` rows gain five optional fields:

- `display_state` — administrative only (`normal` / `pinned` / `set_aside`).
- `risk_class`, `reversible`, `affected_applications`, `sharing_eligible` —
  reviewer-assigned material dimensions, all defaulting to *unassessed*.

**Existing rows need no migration.** `CandidateLesson.from_dict()` defaults every
one of them, and an unassessed field contributes nothing to the material
fingerprint — so a candidate written before this release digests exactly as it
did under PR #83, and **any acceptance approval granted before the upgrade
remains valid after it**.

---

## Startup behaviour

No new startup work, no new service, no new process, no new database.

- The router is registered in `app.py` like every other, and is **not** added to
  `_ADMISSION_EXEMPT_PATHS`: every route here reads or mutates governed learning
  state and needs a live kernel, so it is refused during the startup and
  shutdown windows like any other real ingress point.
- No policy row is written at startup. A runtime that has never been configured
  reads the built-in safe default (revision 0). `learning_policy` rows appear
  only when someone saves a policy.
- The UI view is loaded on demand, when the tab is opened — an ordinary session
  that never opens it makes no extra requests.

---

## Safe default policy configuration

`learning_policy.default_policy()` is what an unconfigured runtime uses. It
refuses everything this wave can produce:

| Setting | Default | Effect |
|---|---|---|
| `enabled_categories` | `[]` | No lesson category could ever be accepted. |
| `min_supporting_experiences` | `2` | S5.4 produces exactly one, so nothing clears it. |
| `min_confidence` | `0.8` | Well above `SINGLE_EXPERIENCE_CONFIDENCE` (0.4). |
| `max_risk` | `low` | Unassessed risk is treated as `critical`. |
| `require_reversible` | `true` | Unassessed reversibility is treated as irreversible. |
| `excluded_privacy_classes` | secure, sensitive, health, emotional, third-party | Every class the shipped rules gate on consent, plus health. |
| `excluded_classifications` | `potentially_generalisable`, `system` | |
| `exclude_sharing_eligible` | `true` | |
| `requested_execution_mode` | `shadow` | |

None of this is load-bearing for safety — the execution mode is — but a default
that *looked* permissive would misrepresent the system to whoever reads it
first.

**Confirmation of execution mode.** Every response that mentions a policy
carries a `shadow_mode` block:

```json
{"execution_mode": "shadow", "automatic_acceptance_enabled": false,
 "notice": "Bartholomew is running this policy in preview only. …"}
```

To confirm on a running deployment:

```bash
curl -s localhost:8000/api/learning/overview | python -m json.tool | head -20
```

`execution_mode` must read `shadow` and `automatic_acceptance_enabled` must read
`false`. The UI banner renders these values rather than a constant in the page,
so the banner and the behaviour cannot disagree.

---

## Frontend build and run

**There is no build step and no separate frontend.** The UI is the existing
single-file `bartholomew_api_bridge_v0_1/ui/minimal/index.html`, served by the
same FastAPI app at `/ui`. No npm, no bundler, no generated output is committed.

Run it exactly as before:

```bash
python -m pip install -e .
python -m uvicorn bartholomew_api_bridge_v0_1.services.api.app:app --reload
# then open http://127.0.0.1:8000/ui/#/learning
```

---

## Upgrade

1. **Back up the database first.** Every operation in this package is governed,
   but revocation and forgetting are permanent and this schema has no
   soft-delete tier:

   ```bash
   # Stop the runtime, then:
   sqlite3 data/barth.db ".backup 'data/barth-pre-package-d.db'"
   # Per-user deployments: repeat for each data/users/<user_id>/barth.db
   ```

   `.backup` is used rather than `cp` because the database runs in WAL mode and
   a file copy can miss uncommitted WAL content.

2. Deploy the code and restart the runtime. No migration command is needed.

3. Confirm the execution mode with the `curl` above.

4. Optionally configure a policy. Until you do, the safe default applies.

---

## Rollback

Rolling back the code is sufficient and safe. The three new kinds are rows in
`memories` that no earlier code path reads:

- `learning_policy` / `learning_shadow_evaluation` / `candidate_lesson_revision`
  rows are simply never queried by pre-Package-D code.
- `candidate_lesson` rows written after the upgrade carry five extra JSON keys.
  Pre-Package-D `CandidateLesson.from_dict()` ignores unknown keys, so those
  candidates still load, still validate, and still accept and reject correctly.
- **One consequence to know about:** if a reviewer *assigned* one of the four new
  material dimensions before the rollback, the candidate's fingerprint under the
  old code differs from the one under the new code, so an approval granted
  before the rollback will be refused afterwards. This fails safe — acceptance
  is refused, never wrongly granted — and is fixed by re-approving the candidate.

To also remove the rows (not required):

```sql
DELETE FROM memories WHERE kind IN
  ('learning_policy', 'learning_shadow_evaluation', 'candidate_lesson_revision');
```

Do this only from a stopped runtime, and prefer restoring the backup. Note that
deleting `candidate_lesson_revision` rows discards archived edit history.

---

## Incompatible or corrupted policy revisions

`load_learning_policy()` **fails to the safe default, never open**:

- An unparseable policy row → the built-in default is used, a warning is logged,
  and **the unreadable row is left in place for inspection** rather than
  overwritten.
- A policy row missing fields a newer release added → those fields take their
  (conservative) defaults.
- A policy row containing an `execution_mode` of `"auto"` — hand-edited, or
  written by a hypothetical future release — **changes nothing**:
  `LearningPolicy.from_dict()` does not read that field back, and
  `execution_mode` is a property returning a module constant.

To recover a corrupted policy, save a new one through `PUT /api/learning/policy`
with `expected_revision: 0` (the default's revision), or delete the row:

```sql
DELETE FROM memories WHERE kind = 'learning_policy' AND key = 'default';
```

Superseded revisions (`default@r<N>`) are read only by
`GET /api/learning/policy/history` and can be pruned freely; doing so makes older
shadow evaluations' `policy_revision` unresolvable but breaks nothing.

### A policy change held for consent

A policy naming a privacy class (e.g. `user.health` under
`excluded_privacy_classes`) contains that word, and `privacy_guard` scans stored
values in full. Such a revision is **queued in the existing pending-consent
inbox and not stored**; the previous policy stays in force and the API returns
`outcome: "queued_for_consent"` with an explanation. Approve it in **Pending
Memory Consent** on the ordinary view to apply it. This is deliberate — see the
DECISIONS.md entry.

---

## UI routes and screens

One new view: **Learning & Memory**, at `#/learning`. Nothing was moved off the
existing two views.

| Screen area | What it shows |
|---|---|
| Shadow-mode banner | First thing on the view. States that Bartholomew is not accepting lessons on his own, and the live execution mode read from the server. |
| Counts | Waiting / accepted / rejected candidates, recallable knowledge, previews recorded, approvals granted. |
| Candidate lessons | Each proposal: its rule, when it applies, confidence, epistemic status, classification, privacy class, risk (or "risk not assessed"), sharing eligibility, and the objective and verbatim observations it stands on. Controls: Edit, Approve this exact lesson, Accept (disabled until an approval applies), Reject, Preview policy, and an export tick-box. |
| Accepted knowledge | What he can actually recall. Correct (supersedes, keeps history) and Stop recalling this (revokes, keeps the audit). |
| Everything he remembers | Personal memories and preferences, for export selection. Reading/correcting/forgetting stays on the ordinary view. |
| Export | Ticked records only; reports everything it left out and why. |
| Preview policy | Every configurable dimension, with the future-mode control and its disclaimer directly beneath it. |
| Recorded previews | Each preview, its decision chip, its reasons, and the policy revision it ran under. |
| Acceptance approvals | Every approval and whether it still applies. |

Diagnostics (fingerprints, matched rule ids, policy revisions) are available
inside a collapsed `<details>` under the plain-language explanation, never in
front of it.

---

## API routes

| Method | Path | Capability |
|---|---|---|
| GET | `/api/learning/overview` | `learning:read` |
| GET | `/api/learning/candidates` | `learning:read` |
| GET | `/api/learning/candidates/{competency_id}/{slug}` | `learning:read` |
| GET | `/api/learning/competencies` | `learning:read` |
| GET | `/api/learning/approvals` | `learning:read` |
| GET | `/api/learning/evaluations` | `learning:read` |
| POST | `…/candidates/{competency_id}/{slug}/edit` | `learning:review` |
| POST | `…/candidates/{competency_id}/{slug}/reject` | `learning:review` |
| POST | `…/candidates/{competency_id}/{slug}/shadow-evaluate` | `learning:review` |
| POST | `…/competencies/{kind}/{key}/correct` | `learning:review` |
| POST | `…/competencies/{kind}/{key}/revoke` | `learning:review` |
| POST | `…/candidates/{competency_id}/{slug}/approve` | **`learning:approve`** |
| POST | `…/candidates/{competency_id}/{slug}/accept` | **`learning:approve`** |
| GET / PUT | `/api/learning/policy` | `learning:policy` |
| GET | `/api/learning/policy/history` | `learning:policy` |
| POST | `/api/learning/export` | `learning:export` |

Five capabilities rather than one. The split follows the architecture, not the
screen: the only two rows behind `learning:approve` are the two acts that can
make a lesson trusted. A future delegated reviewer can hold `learning:review`
without being able to make anything trusted.

---

## Structural proof that automatic acceptance is disabled

Five independent properties. Any one of them is sufficient; all five hold.

1. **The policy engine cannot write.** `learning_policy.py` imports only
   `json`, `dataclasses`, `typing`, `competency` and `candidate_learning`
   (pure-data modules) and `privacy_guard`'s schema registry. Pinned by
   `test_the_policy_module_imports_nothing_that_can_write`.
2. **It never names an approval.** No identifier in its syntax tree references
   `LearningAcceptanceApproval`, `grant_learning_acceptance_approval`,
   `fingerprint_for`, `upsert_memory` or `accept`. Pinned by
   `test_the_policy_module_never_names_an_approval_type`.
3. **A decision is not a permission.** `ShadowDecision.authorizes_acceptance` is
   a property returning `False` with no setter;
   `evaluate_learning_admission()` never reads an evaluation record and has no
   parameter that could carry one.
4. **The execution mode is a `Final` module constant.**
   `LearningPolicy.execution_mode` returns it and ignores
   `requested_execution_mode`; `from_dict()` never reads a stored
   `execution_mode` back.
5. **The write surface is enumerated and enforced.**
   `FORBIDDEN_SHADOW_WRITE_KINDS` is derived from `COMPETENCY_KINDS` plus the
   candidate and approval kinds; `_assert_shadow_writable()` raises
   `ShadowWriteViolationError` for every one of them.

End-to-end, with a fully permissive policy, `requested_execution_mode: "auto"`,
and `learning_accept` added to the Identity allowlist:

- the preview returns `would_accept`;
- the candidate's review state, revision and reviewer are byte-identical
  afterwards;
- no `learning_acceptance_approval` row exists;
- no `competency*` row exists;
- acceptance is refused with `acceptance_approval_required`.

Pinned by `test_e6_a_permissive_identity_policy_cannot_turn_preview_into_acceptance`
and `test_e7_a_configured_auto_accept_rule_still_consolidates_nothing`.

---

## Interfaces other sessions connect to

### Session E — household sharing

Package D owns *eligibility* and the *displayed state*; Session E owns the
transport and the group registry, and neither is implemented here.

```python
@dataclass(frozen=True)
class SharingInterface:
    eligible: bool                    # Package D computes this
    state: str = SHARING_NOT_SHARED   # "not_shared" | "offered" | "shared"
    transport_available: bool = False # Session E sets this True
    detail: str = "Household sharing is not connected in this release."
```

- `learning_policy.sharing_for(facts)` builds it; `CandidateLesson.
  effective_sharing_eligible` derives eligibility (explicit reviewer assignment,
  else `classification != "personal"`).
- Everything this wave produces reports `state == "not_shared"` and
  `transport_available == False`, so the UI says "sharing is not connected"
  rather than showing an empty list that reads like "nothing has been shared".
- **What Session E must supply:** a way to resolve the real `state` for a
  candidate key and to set `transport_available`. Nothing in Package D writes a
  sharing state, and no route accepts one.

### Session F — adapters to connect

1. **Sharing state resolver** — replace the constructed
   `SharingInterface` in `routes/learning.py::_candidate_projection` and
   `_competency_projection` with Session E's real lookup. The JSON shape is
   already stable; the UI reads `sharing.transport_available`,
   `sharing.eligible`, `sharing.state` and `sharing.detail`.
2. **Contradiction detector** — `contradicting_evidence_count` is currently
   supplied per request (`ShadowEvaluationRequest`) and defaults to 0, because
   nothing in this wave measures contradiction. A detector should feed
   `run_shadow_learning_evaluation_through_runtime_contract(...,
   contradicting_evidence_count=N)`. The policy rule that reads it
   (`contradictory_evidence`) is already implemented and tested.
3. **Risk / reversibility assessor** — `risk_class` and `reversible` are
   reviewer-assigned and default to unassessed (treated as `critical` and
   irreversible). An automated assessor should write them through the governed
   edit seam (`run_candidate_edit_through_runtime_contract`), which will
   correctly treat the change as material and invalidate any prior approval.
4. **Affected-application resolver** — `affected_applications` is
   reviewer-supplied today; the policy rule reading it is implemented.

---

## Known limitations

- **Only one lesson category exists.** `LESSON_KINDS` is `{procedural}`, so the
  category controls have one entry. Widening it is a separately authorised
  decision.
- **Contradiction, risk, reversibility and affected applications are not
  measured** — see Session F above. They default to the strictest value, so
  their absence makes previews more conservative, never less.
- **`recorded_by` / `editor` / `approver` are route-derived, not authenticated
  identities.** The UI sends `"you"`. This matches the rest of the API bridge and
  the S8 posture where a bound runtime serves exactly one person; it is not a
  multi-reviewer attribution system.
- **A policy naming a privacy class is held for consent** (see above). Working as
  designed, but surprising the first time.
- **Superseded candidate revisions accumulate.** Each material edit archives one
  `candidate_lesson_revision` row; there is no pruning policy yet.
- **`experience_age_days` uses the candidate's provenance timestamp**, not the
  objective's completion time, because the candidate does not copy it. Ages are
  therefore "since the lesson was proposed".
- **No JavaScript test runner.** The page's properties are pinned structurally
  (`tests/test_ui_learning_control_centre.py`), as every other UI suite here is.
