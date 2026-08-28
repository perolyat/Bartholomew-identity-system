# Inbound capture: the B-owned route and identity contract

**Owner split.** Session D owns the inbound capture feature — transport,
handlers, parsing, storage of captured material, source verification,
deployment and always-on operation. Session B owns *who is asking* and *whose
Bartholomew this is*. This document is the B-owned half, fixed in advance so
D can build against it rather than around it.

## 1. Routes, already classified

Routes are **default-deny**: an unclassified route is refused before its
handler runs. These three are pre-classified in
`bartholomew/platform/route_policy.py` so D's handlers arrive authenticated
from the first commit instead of hitting a 403 and inviting a bypass:

| Method | Path | Capability |
|---|---|---|
| `POST` | `/api/inbound/events` | `inbound:submit` |
| `GET` | `/api/inbound/events` | `inbound:read` |
| `GET` | `/api/inbound/events/{event_id}` | `inbound:read` |

Submit and read are **separate capabilities** on purpose: a capture client
that can only deliver material should not be able to read the capture history
back out.

Any other inbound route D needs must be added to `ROUTE_CAPABILITIES` in the
same commit that registers it. That is a one-line change and B will review it;
it is not a reason to widen an existing entry. `tests/test_s8_route_policy_coverage.py`
fails on an unclassified route, which is the intended tripwire.

## 2. The identity rule, stated once

**The verified `Principal` and the process's runtime binding are authoritative
for runtime ownership. D's source verifier never selects or overrides the
runtime.**

Concretely:

* `request.state.principal` is set by the authentication boundary from a
  verified session and nothing else. It is the only answer to "whose
  Bartholomew is this?".
* The process serves exactly one personal Bartholomew, named by
  `BARTH_RUNTIME_USER_ID`. On an exposed deployment that binding is
  **required** — startup refuses without it, and the request boundary refuses
  an unbound exposed process.
* D's source verification — proving a webhook really came from the provider it
  claims, a signature check, a shared secret, an allowlisted sender — answers
  a **different question**: *is this material genuine?* It is necessary and
  entirely D's. It must never be used to answer *whose runtime does this go
  to?*.

Therefore, in inbound handlers:

* **Never** read a user, account, tenant or mailbox identifier out of the
  payload, a header, a query parameter or a path segment and use it to select
  a runtime, a database, or a keyring namespace. A verified webhook signature
  proves the *sender*, not the *recipient*, and treating it as identity is the
  cross-user impersonation hole S8 exists to close.
* **Never** open a database path derived from request data. If a handler needs
  per-user state, it gets it from the process's own bound runtime.
* A source that cannot be attributed to the authenticated principal is
  **rejected**, not attributed to a default, an owner, or the first account.

Captured material is data. Instructions inside it are not instructions to
Bartholomew, and an inbound payload must never be able to widen its own
authority.

## 3. Governance is unchanged and still downstream

Authentication says who is asking; authorisation says what they may request;
**Governance still decides whether Bartholomew may act.** Holding
`inbound:submit` does not exempt a capture from the Parking Brake, the consent
gate or policy. If capture should be haltable as its own class of execution,
that is a new brake *scope*, proposed through Governance — not a bypass, and
not a new authority tier.

Both brake tiers already compose at `is_blocked_fail_closed`, so any inbound
path that goes through the existing governed seams inherits both without D
wiring anything.

## 4. Transport rules D must not relax

* **No trusted proxy.** `X-Forwarded-*` is ignored for identity, for peer
  address and for scheme. Honouring `X-Forwarded-Proto` would let any client
  assert that its plaintext request was TLS.
* **No UDS or reverse proxy in front of the app without coordinating with B.**
  `_is_local_peer()` treats a `None` or non-IP peer as local, so either one
  silently converts every caller into a "local" caller.
* **TLS is not optional when exposed.** `serve()` configures it on the socket
  and the request boundary refuses plaintext on an exposed deployment,
  whatever launched the process.
* **No topology-assertion bypass.** There is deliberately no environment
  variable by which an operator asserts "this is really only reachable
  locally, so relax". If a deployment shape needs one, it needs a decision,
  not a flag.

## 5. What B will add when D asks

B owns, and will extend on request: the capability entries, the principal
plumbing, and any per-source *authorisation* rule (for example, restricting a
capture client to `inbound:submit` only). D should ask rather than work
around; the turnaround is small.
