# Device trust and trusted groups

> **Status:** Working reference (2026-09-01). **Non-canonical** — a document
> under `docs/`, not one of the 14 canonical SSOT docs. It describes a
> mechanism, how to operate it, and what it does not close. It does **not**
> authorise remote exposure, and it does not close a readiness band;
> `MASTER_PLAN.md`, `DECISIONS.md`, `ASSUMPTIONS.md` and `RISKS.md` remain the
> authorities. Where this note and a canonical document disagree, the
> canonical document wins.

## 0. The package in one paragraph

Two trust foundations that need the same three things — authenticated
identity, tenant isolation and immediate revocation — so they were built
together. **Device trust** turns a companion's `device_id` from a claim into
something the platform issued, declared a capability manifest for, and can
withdraw. **Trusted groups** let a household or a small set of deliberately
trusted people hand one another *specific, sanitized, typed* things, where the
recipient's own governance decides what becomes of them. Neither is cloud
infrastructure, neither is cross-user learning, and neither actuates anything.

## 1. What was already true

Reused, not duplicated:

* the control plane (`bartholomew/platform/`) — accounts, sessions, the
  `Principal` type, per-user runtime resolution, the platform audit table and
  the platform brake tier;
* the inbound authentication seam (`services/api/inbound_auth.py`), whose
  module docstring already names its intended production call site: *"the
  authenticated control plane installs its own verifier at startup"*;
* the Runtime Contract seam — Observation → Interpretation → CandidateAction →
  Governance → Memory → Reflection — and its fail-closed Parking Brake read;
* PR #83's candidate-bound learning authorization
  (`bartholomew/kernel/learning_authorization.py`).

Nothing here adds a second authority for any of them. There is no new audit
log, no new brake scope, no new principal kind, no new consent mechanism and
no new memory write path.

## 2. Device trust

### The lifecycle

`pending → approved → active → (disabled ⇄ active) → revoked`

* **pending** — an operator has written down that a machine is expected. No
  credential exists; it authenticates nothing.
* **approved** — a **one-time enrolment secret** has been issued. It completes
  enrolment and does nothing else: `verify_device_credential` refuses it, so
  an approved device still cannot act as an authenticated source.
* **active** — first verified contact happened, the manifest is registered,
  and a long-lived device credential exists. Only here can it act.
* **disabled** — reversible and credential-preserving. Nothing authenticates
  while it holds.
* **revoked** — terminal, immediate, and every credential goes with it.

Re-approving a device that is still `approved` is permitted, and is the
recovery path for an enrolment secret that went astray: the previous one is
revoked in the same transaction, so there is never a second live secret.
Disable and re-enable restore the status a device actually reached — a device
that was only ever `pending` comes back `pending`, not `approved` with no
credential and no way to be issued one.

### Credentials

256 bits of `secrets` randomness. **Only the SHA-256 digest is stored**, as
`sessions.py` stores session tokens and for the same reason: read access to
the table must not yield a usable credential. A plain digest rather than a
password KDF is correct here — the secret has no guessable structure for a KDF
to protect. The plaintext is returned once, by the function that mints it, and
is never stored, logged, audited or returned by any read path.

Verification fails closed on every axis: unknown digest, wrong purpose,
expired, superseded by rotation, revoked, device not active, account disabled
or gone, and — when the caller names an expected tenant — a tenant mismatch.
There is no branch that returns a degraded or partially trusted device.

### The capability manifest

The frozen logical shape:

```json
{
  "device_id": "stable enrolled id",
  "platform": "windows",
  "companion_version": "version",
  "capabilities": [{"kind": "windows.open_url", "version": 1}]
}
```

Four rules, each enforced rather than advised:

* **`device_id` comes from the registry, never from the declaration.** A
  companion describing itself does not get to choose which self.
* **Declaring is not authorising.** `authorizes(kind, version)` is true only
  when the device declared it *and* this deployment understands it.
* **Nor is being approved.** `approve_enrolment(permitted_capabilities=...)`
  records an operator ceiling, and a capability outside it is refused however
  loudly the device declares it. Approving a *machine* and believing its
  *capability list* are two different acts, and only the first is one an
  operator performed; an unreadable ceiling authorises nothing rather than
  everything. `None` leaves the ceiling at whatever this deployment
  understands, which is right for a machine the operator is standing in front
  of and wrong for one they are not.
* **Unknown is unsupported, never approximated.** `windows.open_url` at
  version 2 is not version 1 with extras; it is an unknown contract. Unknown
  entries are recorded verbatim so an operator can see what a newer companion
  claims, and they authorise nothing.

The vocabulary is frozen at twelve kinds, all version 1, and
`tests/test_device_registry_trust.py` pins the exact set. **Package E
implements none of them**: there is no Windows actuation code, no microphone,
no screen capture and no speech synthesis behind these names. They exist so
Sessions B and C can ask "may I, on this device?" and get a fail-closed answer
before writing the handler that would act.

### Where it plugs in

`bartholomew/platform/device_inbound.py` is a `VerifiedInboundSource` adapter
for the existing inbound route. It contributes one thing — a credential
becomes `source_id = "device:<device_id>"`, `verified_by = "device-credential"`
— and reuses the route's fail-closed default, its `source_id` comparison, the
Parking Brake, the Identity gate, capture, idempotency and provenance
unchanged. It adds no field to the envelope and no column to `inbound_events`.

It is **off unless `BARTH_DEVICE_INBOUND_AUTH` is set**, and it refuses to
install alongside the test-only resolver: a deployment configured with both
has said two contradictory things about how it authenticates, and startup is
where that stops.

## 3. Trusted groups

An opt-in group is a list of accounts that have each accepted an invitation,
plus an audit trail of how that list changed. It is not a directory, not a
social graph, not discovery, and not a place things travel automatically.

* **Roles** — `owner` (created it; may archive), `admin` (may invite and
  remove ordinary members), `member` (may publish and read the inbox). Roles
  administer the group; none of them grants publishing or adoption rights over
  anyone else.
* **Invitations** are explicit, name one account, expire, and are single-use.
  There is no link and no code that "anyone with this may join".
* **Enumeration is the boundary.** Every read takes an actor and refuses a
  non-member with the *same* error a nonexistent group produces — the same
  error *type*, not merely the same words, since an exception class is an
  oracle too. The symmetry is the property: otherwise an outsider could
  confirm a group exists, and that a particular account has somewhere to share
  to, without being in it. A caller-chosen `share_id` gets the same treatment
  at publication: the "use `publish_revision()`" hint fires only for a share
  this publisher published into this group.
* **A membership is only as live as its account.** The one function every
  group read re-derives membership through joins `platform_accounts` and
  refuses a disabled account, so `accounts disable` stops group access in the
  same breath it stops sessions and devices.
* **Membership is re-checked inside the transaction that writes.** A check on
  one connection and a write on another are two transactions, and a
  `remove_member` committing between them would not stop the write.
* **Archiving is not deleting.** It stops new activity; the record of what was
  shared, and the provenance attached to anything already adopted, survives.

## 4. What may cross, and what may not

Four package types and nothing else: `competency`, `correction`,
`household_routine`, `guidance`. There is deliberately **no generic
raw-memory package**.

Three layers, in order, each failing closed:

1. **Eligibility.** Only an explicitly named record of an eligible kind may
   enter sanitization. Raw memory, conversation history, episodes, inbound
   events, reflections, objectives, personal facts, competency *evidence*,
   candidate rows, approvals and exports are structurally ineligible.
2. **Prohibited fields refuse the whole publication.** A field from
   `PROHIBITED_FIELD_NAMES` anywhere at any depth — credentials, secrets,
   approvals, transcripts, media, objectives, health, precise location,
   inferred relationships, financial identifiers, exports — refuses the
   package outright rather than being stripped. If a record contains a
   credential, the answer is "not shareable", not "shareable minus the
   credential".

   Matching folds plurals and looks for the high-signal terms in
   `PROHIBITED_FIELD_SUBSTRINGS` *anywhere inside* the normalised key, because
   exact-name matching alone let `passwords`, `refresh_token` and
   `client_secret` through. Over-refusing is the safe direction here: a
   refusal costs a publisher one decision, and the alternative costs them a
   credential.
3. **Everything not explicitly allowed is removed and named.** The content
   projection is a per-kind allowlist; the publisher's envelope
   (`provenance`, `classification`, `confidence`, `supervision`, keys,
   timestamps) is removed and recorded by name in
   `sanitization.removed_fields`. That is what keeps the publisher out of the
   package — `provenance.detail` is exactly the free text that would
   re-identify them.

   The allowlist is re-applied by `TrustedSharePackage.validate()` on the way
   out, so a package assembled by any route other than `propose()` — a
   hand-written file handed to `share publish`, or a future code path — is
   held to it too. Without that, the sanitizer was bypassable by
   construction.

   Surviving values are then scanned for prohibited content, **keys
   included**: inside the content projection a key is content, so an email
   address or a pair of coordinates cannot be published by putting it on the
   left of the colon. A hit refuses, and never redacts.

The frozen package shape:

```json
{
  "share_id": "stable id", "group_id": "opt-in trusted group",
  "publisher_user_id": "authenticated publisher",
  "source_candidate_fingerprint": "origin fingerprint",
  "kind": "competency|correction|household_routine|guidance",
  "content": {}, "sanitization": {"policy_revision": 1, "removed_fields": []},
  "revision": 1, "published_at": "RFC3339 UTC", "revoked_at": null
}
```

## 5. Publishing and adopting are two governed acts, on two sides

1. The user explicitly selects an eligible source record.
2. The system classifies the requested share type against what the record
   actually is.
3. The sanitizer produces a proposed package. **Nothing is written.**
4. Prohibited fields or content refuse publication.
5. The user inspects the sanitized package and its removed-field list.
6. The user publishes it to **one** group, naming that group again explicitly.
7. Eligible members receive it in a group inbox as `delivered`.
8. A recipient declines, inspects, or adopts.
9. Adoption creates an **`adopted_share_candidate`** in the recipient's own
   runtime — a kind absent from `competency.COMPETENCY_KINDS`, so the
   retrieval seam structurally cannot see it in any review state.
10. The candidate follows the recipient's ordinary local governance: the
    Parking Brake first, then the Identity allowlist for adopt / customise /
    reject.
11. It becomes retrievable competency knowledge only after an acceptance
    approval bound to that exact candidate. That is **PR #83's authority, not
    an analogue of it**: `evaluate_share_admission` delegates the accept
    branch to `evaluate_learning_admission(ctx, "learning_accept", ...)`, so
    `share_accept` is absent from the allowlist and adding it there would
    change nothing.

An adopted candidate carries `confidence = 0.35` — lower than a lesson from
the recipient's own experience (0.4), because a rule they watched play out
once is better evidence than a rule someone else wrote down.

Every field `customise()` can edit is one `to_competency_record()` reads, and
vice versa. That correspondence is load-bearing: an earlier cut let a
recipient correct a shared routine, fingerprint the correction into the
approval they granted, and consolidate the publisher's original anyway. A
shared rule's `counterexamples` travel with it for the same reason — the
safety-bearing half of "always call the warranty line first" is "not for a gas
leak".

## 6. Revisions, forks and revocation

* A publisher update inserts a new `(share_id, revision)` row. It never
  rewrites an earlier one.
* Adoption is idempotent for the same package and **refused for a different
  one at the same key**. The exchange is append-only per `(share_id,
  revision)`, so two packages at one key with different content are a
  contradiction — and allowing the second to overwrite the first would let a
  standing `share_adopt` grant replace content an acceptance approval had
  already been granted for. The approval's fingerprint covers the sanitized
  content and its origin as well, so the two guards are independent.
* The recipient's candidate slug carries the share revision, so adopting
  revision 2 writes a **different memory key** from revision 1. A publisher
  update is structurally incapable of overwriting what a recipient adopted,
  accepted or customised.
* A customised adoption is a **local fork**, and customising changes the
  fingerprint an approval binds to — so it needs approving again for what it
  now says. That is the intended consequence: the reviewer approved the old
  text.
* A revision that does not follow the revision the publisher last saw is
  refused as a concurrent edit. There is no force flag and no
  last-write-wins branch.
* Revocation stops new adoption and further revisions, and stays visible in
  the recipient's provenance — on the exchange through
  `share_exchange.provenance()`, and in the recipient's own runtime through
  `runtime_contract.record_upstream_revocation()`, which a management surface
  calls after reading it. It does **not** delete or disable what a
  recipient already adopted — a publisher who could reach into another
  person's runtime would have a remote delete, which is a larger power than
  "un-share" and is not one this design grants.
* Removing a member stops access on the next call, because membership is
  re-derived on every read rather than cached.

## 7. Threat model: what this closes, and what it does not

**Closed.** Unenrolled, pending, disabled and revoked devices authenticate
nothing. A disabled account's group access stops with its sessions. A credential does not cross devices or tenants. Rotation invalidates
the previous credential in one transaction. No plaintext credential survives
issuance, in any control-plane row, any read surface, or any log. A device
cannot act outside its registered manifest, and an unknown capability version
is unsupported. A claimed payload `device_id` cannot override the verified
identity. Non-members cannot enumerate a group, its members, its invitations
or its packages. Prohibited content cannot be published. Nothing a group
shares becomes the recipient's knowledge without their own candidate-bound
approval.

**Not closed, and stated rather than implied:**

* **Bearer credentials.** A device credential is not a per-request signature.
  TLS is what prevents capture, which is why a non-loopback bind mandates it
  and cannot turn it off. `sessions.py` records the same limitation for
  session tokens; Package E gives the identity a place to live and a way to be
  withdrawn, not a signing scheme.
* **Control-plane isolation is by predicate, not by file.** Per-user *memory*
  is isolated by process and filesystem boundary (`runtime_registry`). Devices
  and groups are control-plane objects in the shared `platform.db`, so their
  isolation is `WHERE user_id = ?` — weaker in kind, and load-bearing. The
  mitigations are that the module is small, every read takes an actor, there
  is no "list everything" helper, and no per-user kernel is given a write
  path to the credential table.
* **The operator is trusted.** Enrolment, approval and rotation are local CLI
  operations against the control-plane database. Anyone with that file and
  shell access can enrol a device.
* **Remote exposure is still gated.** `ASSUMPTIONS.md` and `RISKS.md` record
  that no cross-device auth mechanism may be treated as sufficient until a
  reviewed threat model exists. This document does not claim to be that
  review, and Package E authorises no exposure: the default posture is
  loopback, authentication disabled locally, TLS mandatory the moment it is
  not.
* **A compromised recipient is a compromised recipient.** Sanitization bounds
  what a publisher sends; it cannot bound what a recipient does with what they
  legitimately received.
* **The sanitizer's content scan is a backstop, not a classifier.** What
  actually keeps the prohibited categories out is the eligibility rule and the
  per-kind allowlist. A scan that guessed would either refuse everything or
  lull a publisher into believing more had been checked than was.

## 8. Deliberately not built

Global learning, public discovery, a marketplace, anonymous sharing,
group-wide automatic acceptance, automatic propagation, confidence-based
adoption, system-level promotion, training on other users' raw memories,
centralised cloud learning, cross-group search, customer-scale multi-tenancy,
billing. Also: no Windows actuation, no multimodal capture, no HTTP routes for
devices, groups or sharing — the operator surface is `bartholomew devices` /
`groups` / `share`, including the recipient-side `share adopt-local`,
`approve-local`, `review-local` and `mark-revoked`. A web surface is a UI
stream's to connect; `INTERFACES.md`'s "Device registry and trusted-group
sharing" entry names exactly what, under **"Not implemented here, and left for
Session F to connect"**.
