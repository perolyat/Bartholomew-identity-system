# W03-D — Governed Memory & Learning Consolidation

> Authoritative builder contract. Read `docs/waves/W03/README.md` and
> `docs/waves/W03/W03_PREP_ASSESSMENT.md` first.

## Identity

| | |
|---|---|
| Session | **W03-D — Governed Memory & Learning Consolidation** |
| Immutable id | `W03-D` |
| Branch | `wave/w03-d-governed-memory-learning` |
| PR | `[W03-D] Governed Memory & Learning Consolidation` |
| Handoff | `BARTHOLOMEW_W03_D_HANDOFF.md` |
| Required CI tier | Integration |
| May start | Immediately after W03-PREP |

## Mission

Make the learning/correction cycle useful **and** safe end to end —
`experience -> correction -> candidate lesson -> governance -> memory/competency
-> future behaviour` — and close the research gaps that make recalled memory
dangerous today. The wave-two learning loop already enforces manual acceptance
structurally (candidate-bound approval, shadow-only policy, kind exclusion from
retrieval). W03-D keeps that and adds the missing governance on the **read** side
and the missing **supersession** the closed loop needs.

The baseline gaps this fixes (assessment §Memory): the `memories` schema has no
provenance / confidence / trust / validity-window / supersession / revoked
fields; retrieval-side governance is privacy/consent only (no authority,
provenance, staleness or revocation check); recalled memory is concatenated
verbatim into the LLM prompt with no data-vs-instruction boundary; revocation is
a hard delete with no tombstone, so a revoked key can be silently recreated;
supersession is first-class only for competency/candidate/policy records and
absent for personal facts, preferences, routines and temporary exceptions;
`auto_expire` rules are declared but enforced by no code path.

## Ownership

**Owns (may modify freely):**
- `bartholomew/kernel/memory_store.py` — the single governed write authority and
  the schema (provenance/validity/supersession fields via the existing ALTER
  pattern).
- `bartholomew/kernel/consent_gate.py` — the retrieval-side validity verdict.
- `bartholomew/kernel/memory_rules.py`, `bartholomew/kernel/personal_facts.py`.
- `bartholomew/kernel/candidate_learning.py`, `learning_authorization.py`,
  `learning_policy.py`, `share_adoption.py` — the learning/correction/consolidation
  and cross-user adoption path.
- `bartholomew_api_bridge_v0_1/services/api/routes/learning.py`, `routes/memory.py`.

**Owns and publishes (shared contract `memory-retrieval-governance`):** the
retrieval validity verdict and the provenance/validity fields. W03-B and W03-E
consume them; only W03-D changes their shape.

**May consume (do not modify):** the retrievers
(`retrieval.py`, `hybrid_retriever.py`) — extend them to honour the verdict via
the owned consent-gate seam, but keep relevance logic intact; the append-only
reflections sink for provenance; the per-user runtime binding.

**Must not modify:** multimodal (W03-A); actuation (W03-C); executive (W03-B);
`app.py` router registration (W03-F). W03-A/B/C **call** the governed write path;
they do not edit `memory_store.py`.

## Dependencies

- **Required pre-existing interfaces:** the wave-two learning loop, candidate-bound
  authorization (PR #83), the shadow policy, the kind-exclusion retrieval
  allowlist, the consent-bypass red-team AST guard — all on the baseline head.
- **Other W03 sessions:** none to start. W03-A's observation events become memory
  input (coordinate the observed-vs-inferred kinds so they stay out of retrieval
  competency kinds). W03-B consumes retrieval.
- **Start:** immediately after W03-PREP.

## Explicit non-goals

- **No automatic lesson acceptance.** Manual acceptance remains authoritative for
  the whole wave. The configurable policy infrastructure may be built, exercised
  and tested, but `execution_mode` stays `shadow` and `learning_accept` gains no
  standing permission. Enabling automatic acceptance is deferred and is a director
  decision (see `W03_DEFERRALS.md`).
- **No cross-user/cross-instance promotion.** Sharing stays opt-in, sanitized,
  content-bound adoption through the existing gate; no memory synchronization.
- **No new memory authority.** One governed write path; one retrieval governance
  point.

## Acceptance criteria (observable, testable)

1. **Provenance & validity fields:** `memories` rows carry source / source_type /
   asserted_by / confidence / valid_from / valid_to / superseded_by / revoked_at,
   populated only through `upsert_memory`, surfaced in retrieved items.
2. **Retrieval is evidence, not authority:** a retrieval-side verdict
   (`currently_valid` / `revoked` / `superseded` / `expired`) is honoured by all
   retrievers and the chat/executive retrieval path. Tests prove recalled memory
   cannot grant a permission, widen capability scope, override identity policy,
   cross a user/domain boundary, or resurrect a revoked grant. The
   consent-bypass red-team AST guard is extended to any new bypass parameter.
3. **Instruction/data boundary:** recalled memory is rendered into the prompt in a
   delimited, explicitly non-instructional frame; a test proves stored imperative
   text ("delete everything", "approve the action") does not change a
   `CandidateAction` kind or any actuation proposal.
4. **Supersession is first-class:** a correction narrows/invalidates/supersedes a
   prior preference, permission, routine, lesson or temporary exception; the
   currently-valid state wins over the obsolete one while auditable history is
   preserved (key@rN archive extended to personal facts and preferences).
5. **Revocation tombstone:** a revoked `(kind, key)` cannot be silently recreated
   by re-learning, re-training or personal-fact capture.
6. **Expiry enforced:** `auto_expire` / `expires_in` excludes stale observation-
   kind memories at read time (fail-closed) so a Windows observation stops being
   recalled after its window.
7. **Poisoning resistance:** seeded recalled content containing embedded
   instructions, stale authority, or cross-user/cross-domain data cannot silently
   grant action authority (adversarial suite).
8. **Manual acceptance intact:** a fully permissive policy with `requested_
   execution_mode: auto` and `learning_accept` allowlisted still consolidates
   nothing; consolidation is reachable only from the approved accept branch.

## Testing

- **Package-local:** schema migration; verdict computation; supersession/
  tombstone; expiry; personal-fact conflict handling.
- **Integration:** experience -> candidate -> approve -> consolidate -> retrieved
  and applied in a later turn, against real stores; a correction supersedes and
  the later turn uses the new value.
- **Adversarial/governance:** memory-poisoning suite (embedded instructions,
  stale authority, cross-user/domain); supersession suite (changed preference,
  revoked permission, temporary exception, conflicting observation, explicit
  correction); the permissive-policy-still-shadow suite; the extended AST bypass
  guard.
- **Required CI tier:** Integration.

## Escalation boundary

Stop and report rather than expanding scope if:
- closing a gap seems to require **enabling automatic acceptance** or a
  confidence-based auto-accept in a real deployment;
- retrieval governance would need to read another user's data to compute a
  verdict (cross-user isolation is inviolable);
- a schema change would break the per-user isolation model or the existing
  encryption/consent contract.
