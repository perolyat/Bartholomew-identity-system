# Platform / Personal-Identity Architecture — Review and Handoff (2026-08-15)

> **Non-canonical.** This is a review artifact produced for the architecture decision recorded in
> `DECISIONS.md` ("One shared Bartholomew platform; many strongly isolated personal Bartholomew
> identities"). Per `MASTER_PLAN.md`'s "Canonical docs" section, everything under `docs/` except
> `docs/TILT.md` is a reference, not an authority. Where this file and a canonical document
> disagree, the canonical document wins.
>
> **Status: PROPOSED — awaiting user approval. Nothing is committed.** Per `MASTER_PLAN.md`'s Doc
> Governance section and `CHECKLISTS.md`'s Commit authorization checklist, no doc or code change is
> committed without explicit user authorisation.
>
> **Purpose:** to let the proposed documentation changes be reviewed independently, and to preserve
> the code findings so they are not rediscovered from scratch.

---

## 1. What was reviewed

The full canonical documentation set (14 documents, per `MASTER_PLAN.md`), plus the implemented
code across `bartholomew/`, `bartholomew_api_bridge_v0_1/` and `identity_interpreter/` (115 Python
modules), with particular attention to identity, authentication boundaries, memory ownership,
observation provenance, Executive runtime state, scheduler/background work, database schema,
storage abstraction, capability execution context, Governance, audit, and the API/client boundary.

---

## 2. Repository findings

### 2.1 What the documentation already got right

Four existing canonical positions are consistent with this architecture and were **reused rather
than restated** — the one-authority-per-concept principle made this the main constraint on where
new text could go:

| Existing authority | What it already establishes |
|---|---|
| `DECISIONS.md` — "Deployment architecture — hybrid local-first" (2026-07-28) | Local-authoritative sensitive memory, governance, parking brake and emergency shutdown; optional cloud services; explicit rejection of a pure hosted service *and* of "simple token auth". |
| `CONSTITUTION.md` — "Personal learning vs. potentially generalisable and system-level learning" (2026-08-08) | Personal learning never auto-promotes to shared/global/product knowledge; name removal is not depersonalisation; no cross-instance mechanism exists or is authorised. |
| `CONSTITUTION.md` — Safety/Product invariants §1 and §3 (2026-07-28) | Independent emergency shutdown; data portability ("trust may be a product advantage; lock-in must not be"). |
| `COGNITIVE_RUNTIME.md` — Ownership table | Already model- and storage-agnostic: "SQLite now, Postgres later", "YAML today, database tomorrow", "local skills today, remote services / MCP later". |

**The gap was the multi-user dimension itself.** Across all fourteen canonical documents there was
exactly one incidental mention of single-user operation (`docs/TILT.md` line 194, in passing). No
document stated how many users exist, what a "personal Bartholomew" is as distinct from the
platform, or that Bartholomew is not its LLM. That silence — not any wrong statement — was the risk:
it left each future session free to assume whichever model was locally convenient.

### 2.2 What the code assumes

Verified by search, not inferred:

- **No user, tenant, owner, or account concept exists anywhere.** `grep` for
  `user_id|tenant|owner_id|account_id` across all three packages returns **zero matches**.
- **One process serves one person.** `BARTH_DB_PATH` (default `data/barth.db`) resolves a single
  SQLite database that *is* the personal state.
- **The API bridge has no authentication** and treats every caller as the owner. `INTERFACES.md` §6
  already records this accurately as a local/dev surface, gated behind Stage 6.
- **Module-level singletons hold personal runtime state**: `narrator.py`, `encryption_engine.py`,
  `memory_rules.py`, `retrieval_config.py`, `metrics_registry.py`.
- **Two audit surfaces already carry a provenance field** that could later carry identity —
  `governance_audit.actor`, `skill_permissions.granted_by` — though both currently record which
  *subsystem/surface* acted, not which *person*.
- **Naming collision worth knowing:** `bartholomew/kernel/request_admission.py` describes itself as
  "identity-bound". That means per-request admission tokens, **not** user identity. A future reader
  could easily misread it as tenancy work that already exists.

### 2.3 Does anything already built fundamentally conflict?

**No.** Specifically, the three failure modes that would have been expensive to reverse are all
absent:

1. Nothing equates Bartholomew with a particular model. `Identity.yaml` names models under
   `deployment_profile.models`, but as *routing policy* consumed by `select_model` — configuration
   about which resource to call, not a definition of Bartholomew's identity. The ownership table
   independently names the Identity System, Executive and Memory Substrate as owners, never a model.
2. No personal state is structurally unable to acquire an owner. Every persisted record is an
   ordinary row in an ordinary schema.
3. Nothing prevents export or migration of personal state.

### 2.4 Architectural traps discovered

**None of severity "serious".** Three migration seams are worth recording, and all three are now in
`RISKS.md`'s tech-debt watchlist and classified in `COGNITIVE_RUNTIME.md`:

**(a) `memories` is uniquely indexed on `(kind, key)` globally** — `memory_store.py`:
`CREATE UNIQUE INDEX uq_memories_kind_key ON memories(kind, key)`.

- *What the code does:* enforces one row per `(kind, key)` across the entire database; this is what
  makes `upsert_memory()` an upsert.
- *Why it conflicts:* under many identities, uniqueness must be per identity — two users may each
  have a `user_profile`/`home_address`.
- *Does it need correcting now?* **No.** The fix is an additive migration (add ownership column,
  rebuild index over `(owner, kind, key)`), no more expensive later than now. Doing it now adds an
  unused column serving no current requirement, which `CONSTITUTION.md` calls premature abstraction.
- *Smallest safe correction (if ever wanted):* exactly that additive migration.
- *Consequence of deferring:* essentially none **unless** new code starts relying on the
  *global-ness* of the constraint (store-wide deduplication, "there is exactly one home address",
  caching keyed on `(kind, key)` alone). That reliance, not the index, is what would turn a cheap
  migration into an expensive one — which is why it is flagged for review attention rather than fixed.

**(b) Personal runtime state in module-level singletons** — acceptable for the PoC. The
multi-identity form (a runtime context per identity) requires constructing these differently, not
rewriting them. Watch item: a singleton that begins caching *personal content* rather than
configuration would be harder to separate.

**(c) No on-behalf-of identity on scheduler drives or capability execution** — background cognition
acting for someone is exactly where "on whose behalf?" must eventually be answerable. Correct for a
single-identity PoC; recorded so the seam is not rediscovered.

---

## 3. Canonical documentation changes proposed

Placement follows the project's one-authority-per-concept rule; each concept is stated once and
cross-referenced elsewhere.

| File | Authority it establishes | Why this file |
|---|---|---|
| `CONSTITUTION.md` | New section **"One Platform, Many Personal Bartholomews"**: the three-layer distinction (platform / underlying intelligence / personal Bartholomew); Bartholomew-is-not-the-LLM; identity portability; client-vs-Bartholomew; hybrid + local Governance authority; strong isolation; the governing principle for the current stage; what it does *not* authorise; and the **binding conflict-surfacing rule**. Plus one bullet in "Expectations of the Architect" and a header amendment note. | It is explicitly the enduring-intent document — "every architectural proposal should be evaluated against this document first", and conflicts "must be resolved explicitly, never silently overridden". This decision is exactly that class of principle. |
| `DECISIONS.md` | New dated entry with the repository's Decision/Alternatives/Why/Consequences format, including an explicit **relationship-to-existing-decisions** paragraph. | It is the ADR store; the repository has no separate `adr/` directory, and every prior architectural decision of this weight lives here. |
| `COGNITIVE_RUNTIME.md` | One Ownership-table row + new **"Personal-identity ownership"** subsection: what the runtime assumes today, a classification table for each single-user assumption, and the constraint on new work. | It is the canonical "grounded in the code as it exists today" document and holds the ownership table. Current-state facts belong here, not in `CONSTITUTION.md`. |
| `CHECKLISTS.md` | New **"Platform and personal-identity architecture checklist"** — the operational form of the conflict-surfacing rule, plus ownership-representability, execution-beneficiary, identity-not-pinned, local-stop, personal-learning, and an anti-premature-platform-work item. | Checklists are where invariants become enforceable at change time; this mirrors how the 2026-07-28 product/safety invariants were operationalised. |
| `RISKS.md` | Three tech-debt watchlist entries (2.4a–c), each explicitly "not a defect, no fix authorised". | The tech-debt watchlist is the established home for characterised-but-deliberately-unfixed findings. |
| `ASSUMPTIONS.md` | **A9** — the current deployment serves exactly one personal identity; its single-user conveniences are stage-appropriate, not architectural commitments. Includes two cheap partial validations. | Makes an invisible assumption a tracked one, which is this document's stated purpose. |
| `ROADMAP.md` | One line in "What we will not do yet": multi-user/tenancy/platform infrastructure is FUTURE PLATFORM WORK, out of current scope. | The scope guard belongs where scope lives. **No stage's exit criteria, sequencing, or status changed.** |
| `MASTER_PLAN.md` | One "Key invariants" bullet — the architecture diagram is a single-user PoC deployment and is *not* evidence that platform infrastructure exists — plus a header note. | It is the SSOT for current state; this prevents the diagram being misread as a shipped platform. "Next 3 Moves" is unchanged. |
| `.github/copilot-instructions.md` | Agent-facing restatement of the five key points and the stop-and-tell-the-user rule. | It is read automatically by coding agents, and is corrected in place rather than archived for exactly this reason. (`.clinerules/Cline instructions.txt` is a zero-byte file and was left alone.) |

**Duplication check.** Personal-vs-shared learning is **cross-referenced, not restated** —
`CONSTITUTION.md`'s 2026-08-08 section remains its single authority. Hybrid local-first is
**extended, not replaced** — the new ADR says so explicitly. Emergency shutdown is
cross-referenced to invariant §1. Data portability is cross-referenced to invariant §3.

---

## 4. ADR decision

**A dedicated entry in `DECISIONS.md` was warranted.** The repository uses `DECISIONS.md` as its
ADR store — there is no `adr/` directory or numbered-ADR convention — so a new dated entry in the
existing format is the correct instrument, and creating a separate ADR file would itself violate
the no-doc-sprawl rule.

Relationship to existing entries:

- **Extends** "Deployment architecture — hybrid local-first" (2026-07-28). That entry answers
  *where authority and compute sit* for one user; this one answers *how many users there are, what a
  personal Bartholomew is, and what must remain replaceable underneath it*. The lightweight-client
  direction recorded here is explicitly bounded by that entry's cloud-independence requirement.
- **Builds on** "Personal, potentially generalisable, and system-level learning are architecturally
  distinct" (2026-08-08) without restating or weakening it.
- **Constrained by** "Usable POC / time-to-real-use prioritisation" (2026-08-12) — which is why
  nothing here is implementation.
- **Consistent with** "One authority per architectural concept" and "Canonical SSOT docs".

---

## 5. PoC impact

**NOW — required to protect the architecture**

- Documentation only, as listed in §3. **No code changes. No schema changes. No new dependencies.
  No test changes.**
- The one behavioural change is to *how new code is reviewed*: `CHECKLISTS.md`'s new checklist.

**DOCUMENT NOW / IMPLEMENT LATER**

- Ownership/tenancy as a first-class concept wherever persistence or execution requires it.
- Per-identity uniqueness for `memories` (additive migration).
- On-behalf-of identity for scheduler drives, capability execution, and audit records.
- Per-identity runtime context replacing process-global singletons.
- Caller identity attaching at the existing `app.py` admission-middleware chokepoint.

**FUTURE PLATFORM WORK**

- Multi-tenant infrastructure, production tenancy, authentication/authorization systems, a
  client/server split, distributed services, identity migration/portability tooling, scalable
  backend deployment, and any cross-instance or product-level learning pipeline.

**Scope confirmation.** The current usable-PoC scope has **not** expanded. `MASTER_PLAN.md`'s
"Next 3 Moves" is unchanged — putting slice 1 into real-world use remains the next move. No stage
gate, exit criterion, or sequencing changed in `ROADMAP.md`. `docs/TILT.md` is untouched and its
priority still governs.

---

## 6. Code implications

- **Must change now:** **nothing.** No finding met the bar of "would make future migration
  disproportionately difficult if deferred".
- **Should eventually change:** the three seams in §2.4, plus caller identity at the API boundary —
  all as part of separately-approved future platform work, not now.
- **Migration seams to preserve:** the single admission-middleware chokepoint in `app.py` (one
  place for caller identity, not per-route); `governance_audit.actor` and
  `skill_permissions.granted_by` as existing provenance fields that can later carry identity; the
  storage-agnostic shared-executor pattern from Phase B stage B2; and the ownership table's
  already-replaceable owners.
- **Constraint on new code (the actual deliverable of this review):** do not introduce new persisted
  personal state that could not later acquire an owner, new background execution whose beneficiary
  is unrecoverable, or new global uniqueness constraints over personal data.

---

## 7. Conflict-protection mechanism — where it is encoded

Four layers, so a future session encounters it by whichever route it arrives:

1. **`CONSTITUTION.md`** — "One Platform, Many Personal Bartholomews" → "Conflict-surfacing rule
   (binding)": the eight named properties and the requirement to inform the user *before*
   implementing a conflicting design. This is the authority.
2. **`CONSTITUTION.md`** — "Expectations of the Architect": a bullet making it a standing duty of
   the role.
3. **`CHECKLISTS.md`** — "Platform and personal-identity architecture checklist": the first item is
   the binding PASS/BLOCKED gate, applied at change time.
4. **`.github/copilot-instructions.md`** — the agent-facing surface read automatically by coding
   agents, with the explicit instruction to stop and tell the user.

---

## 8. Final architectural test

A future developer reading only the canonical documentation should now correctly conclude all ten
of the following. Each is traceable to a specific location:

| Conclusion | Where it is established |
|---|---|
| Bartholomew is not the LLM | `CONSTITUTION.md` § "Bartholomew is not the LLM"; `DECISIONS.md` consequence (a); `COGNITIVE_RUNTIME.md` ownership table |
| A new customer does not receive a duplicated Bartholomew intelligence/model | `CONSTITUTION.md` § opening paragraph; `DECISIONS.md` decision statement + rejected alternative (c) |
| There can eventually be one shared platform serving many users | `CONSTITUTION.md` § "The three layers"; `DECISIONS.md` |
| Every user nevertheless has a strongly isolated, persistent personal Bartholomew | `CONSTITUTION.md` § "Strong isolation… is non-negotiable"; `CHECKLISTS.md` |
| Personal identity/state creates continuity and individuality | `CONSTITUTION.md` § layer 3 and "This is my Bartholomew" |
| The user's Bartholomew survives changes of device, infrastructure and models | `CONSTITUTION.md` § "Identity is portable across infrastructure" |
| Local authoritative Governance remains possible | `CONSTITUTION.md` § "Hybrid architecture and local Governance authority"; `DECISIONS.md` hybrid local-first (2026-07-28) |
| Personal data does not become shared platform knowledge | `CONSTITUTION.md` § "Personal learning vs. potentially generalisable…" (2026-08-08, unchanged), cross-referenced from the new section |
| The current single-user PoC needs no premature multi-user infrastructure | `CONSTITUTION.md` § "What this does not authorise" + "The governing principle"; `ROADMAP.md` "What we will not do yet"; `MASTER_PLAN.md` key invariant; `ASSUMPTIONS.md` A9 |
| Threatening proposals must be surfaced to the user before implementation | `CONSTITUTION.md` § "Conflict-surfacing rule (binding)"; `CHECKLISTS.md`; `.github/copilot-instructions.md` |

---

## 9. Review instructions

```bash
# Full diff of proposed changes (nothing is staged or committed)
git diff
git status

# Discard everything, if rejected
git checkout -- .
rm docs/PLATFORM_IDENTITY_ARCHITECTURE_REVIEW.md
```

**Approval requested for:** committing the documentation changes listed in §3 to branch
`claude/bartholomew-platform-identity-architecture-xzwxfc`. No code, schema, test, dependency or
CI change is included or requested.
