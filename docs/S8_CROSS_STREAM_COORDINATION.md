# S8 cross-stream coordination notes (Session B)

Two seam requirements that are cheap to honour now and expensive to retrofit.
Recorded here because the target sessions were not reachable for direct
messaging when the decisions were taken. **Neither is a request for another
stream to implement Session B's work** — each is a constraint to avoid
silently invalidating an S8 security property.

## For Session D — Always-on + Inbound Capture

**The `_is_local_peer()` trust assumption must not become remotely reachable.**

`bartholomew_api_bridge_v0_1/services/api/app.py` enforces a loopback-only
network boundary (PR #65). Its peer check deliberately treats two cases as
local:

* `request.client` is `None` — no peer address (UNIX-domain socket, or an
  ASGI transport with no network under it);
* the peer is not an IP address at all — Starlette's in-process `TestClient`
  reports the sentinel `"testclient"`.

Both are correct for in-process callers. Both mean that **introducing a
reverse proxy or a UNIX-domain socket in front of the app converts every
caller into a "local" caller**, silently neutralising the boundary. The
boundary also ignores `X-Forwarded-*` on purpose: those headers are
attacker-controlled and no trusted proxy is part of the architecture.

Therefore, for any always-on/hosted deployment work:

1. Do not put the app behind a reverse proxy or UNIX socket without
   coordinating with Session B first. The fix is not "trust
   `X-Forwarded-For`" — it is that authentication, not peer address,
   becomes the boundary (`BARTH_AUTH_MODE=enforced`, which
   `BARTH_API_ALLOW_NON_LOOPBACK=1` now forces and cannot be overridden).
2. **Inbound capture must arrive as an authenticated principal like any
   other request.** No privileged side channel, no unauthenticated ingest
   route, no "internal" bypass path. A capture endpoint that trusts a
   caller-supplied user/tenant identifier is the exact cross-user
   impersonation hole S8 exists to prevent.
3. Deployment/hosting/lifecycle changes in `app.py` remain Session D's.
   Authentication, authorisation and request identity remain Session B's.
   The seam is `bartholomew/platform/http_identity.py`: D routes and
   supervises requests, B decides who is asking.

## For Session C — Retrieval Quality / OP-W003

**Embedding and retrieval state must be per-runtime, never a process-global
cache.**

Under the approved Alpha isolation model each user gets their own kernel
runtime and their own database. A module-level embedder cache keyed only by
content — a common and otherwise sensible optimisation — would be shared
across every user in the process and would become a **cross-user
information leak**: user A's text, and the vectors derived from it, reachable
from user B's retrieval path.

Therefore, when the real embedder lands:

1. Cache instances per runtime/store, not at module scope. If a global model
   *weights* cache is needed for memory reasons that is fine — weights are
   not user data. **Content, embeddings, chunk text and query results must
   not be cached process-globally.**
2. Any vector index or FTS handle must be derived from the runtime's own
   `db_path`, not from a module-level singleton.
3. Session B will assert this with an isolation test rather than modify
   retrieval internals. Relevance quality remains entirely Session C's.

## For Session D — the container now requires a deployment decision

**Status: flagged, not resolved. Session B did not change the Dockerfile or
docker-compose.yml.**

`BARTH_API_ALLOW_NON_LOOPBACK=1` now forces authentication **and** TLS on, and
the process refuses to start otherwise (`bartholomew/platform/exposure.py`).
That rule is deliberate and approved: it is what makes an unauthenticated
remote deployment structurally impossible.

The Dockerfile sets that variable — not because the container is LAN-exposed,
but because Docker's bridge peer address is not loopback while the host
publish (`127.0.0.1:5173:5173`) still confines reachability to the host. The
variable therefore currently conflates two different questions:

* *may the request boundary accept a non-loopback peer?* (the container's
  actual need); and
* *is this deployment genuinely reachable from a network?* (what forces
  authentication and TLS).

Consequence: **`docker compose up` will now fail to start** until either TLS
material is provided or the deployment declares itself local. No test breaks —
the container path is not exercised in CI — so this is an operational change,
not a red build.

Three options, for Session D to choose with Taylor, none of which Session B
should decide alone:

1. **Give the container TLS material** (a generated self-signed cert for local
   use, real material for Alpha) and run it authenticated. Most honest;
   highest local-dev friction, since it also needs a provisioned account.
2. **Split the variable in two** — keep `BARTH_API_ALLOW_NON_LOOPBACK` for the
   request boundary, and add an explicit deployment-reachability declaration
   that drives the authentication/TLS requirement. Preserves local container
   UX, but the new variable is an operator assertion about topology and must
   be designed so it cannot become a quiet bypass.
3. **Run the local container loopback-only** and reach it another way.

Whatever is chosen, the invariant to preserve is the one the tests encode:
**a genuinely network-reachable Bartholomew must never run unauthenticated or
without TLS, and no environment variable may downgrade that to a warning.**
