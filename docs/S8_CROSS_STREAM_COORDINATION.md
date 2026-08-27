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
