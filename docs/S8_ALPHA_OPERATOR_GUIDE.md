# Running Bartholomew with authentication (S8 Alpha)

Operator-facing. Covers what changed, how to provision Alpha participants,
and what the deployment refuses to do.

## What changed

Nothing, if you run Bartholomew the way it has always run locally. A
loopback-only deployment still defaults to `BARTH_AUTH_MODE=disabled` and
behaves exactly as before.

Everything changes the moment the deployment is non-loopback:

| `BARTH_API_ALLOW_NON_LOOPBACK` | `BARTH_AUTH_MODE` | TLS | Result |
|---|---|---|---|
| unset | unset | — | Loopback-only, unauthenticated. The existing local behaviour. |
| unset | `enforced` | — | Loopback-only, authenticated. Use this to develop against the boundary. |
| `1` | unset or `enforced` | required | Authenticated, TLS-only, **and a bound runtime user is required**. The Alpha posture. |
| `1` | `disabled` | — | **Refuses to start.** |
| `1` | anything | missing | **Refuses to start.** |
| any | typo | — | **Refuses to start.** |
| `1` | any | present | **Refuses to start** without `BARTH_RUNTIME_USER_ID`, or if it names no account, a disabled account, or an administrator. |
| `1` | any | present | **Refuses to start** if `BARTH_DB_PATH` or `BARTHO_MEMORY_KEYRING_SERVICE` is not the bound user's. |

There is no environment variable that downgrades a refusal to a warning, and
a test asserts that none appears.

## Provisioning Alpha participants

Accounts are operator-created. There is no self-registration endpoint, no
password-reset flow, and no remote account-management surface — deliberately.

```bash
# An Alpha participant. Omit --password to generate a strong one, printed once.
bartholomew accounts create alice

# A platform administrator. A distinct authority kind, not a user with extras:
# it has no personal Bartholomew and cannot read anyone's memory.
bartholomew accounts create ops --admin

bartholomew accounts list

# Disables the account AND revokes every live session, effective on the very
# next request.
bartholomew accounts disable <user_id>
```

## Running an exposed deployment

Launch through the canonical serve path, which configures TLS on the socket
and runs the exposure checks *before* binding:

```bash
export BARTH_API_ALLOW_NON_LOOPBACK=1
export BARTH_API_TLS_CERTFILE=/path/cert.pem BARTH_API_TLS_KEYFILE=/path/key.pem
export BARTH_RUNTIME_USER_ID=<user_id from `bartholomew accounts list`>
export BARTH_DB_PATH=<that user's database>
export BARTHO_MEMORY_KEYRING_SERVICE=<that user's keyring namespace>
python -c "import app; app.serve()"
```

Do **not** start it with the `uvicorn` CLI and a non-loopback `--host`: that
bypasses `serve()`, so nothing configures TLS. The request boundary refuses
every plaintext request in that case rather than failing open, but the fix is
to use the supported path.

One process serves **one** personal Bartholomew. The bound user is verified
against the database and keyring namespace actually in use, and any other
authenticated identity is refused by that process.

## Per-user isolation

Each participant gets their own database, data directory and keyring
namespace under `$BARTH_DATA_ROOT/users/<user_id>/`. Isolation is a file and
process boundary, not a query predicate. A process serves one personal
runtime; if an authenticated identity does not match the runtime the process
is serving, the request is refused rather than served the wrong data.

## The two Parking Brake tiers

```bash
bartholomew brake on --scope skills            # Personal: this user only
bartholomew platform-brake on --scope skills --actor ops --reason "defect"
```

They compose restrictively — execution proceeds only if neither blocks — and
releasing one never releases the other. A user cannot override a platform
halt.

**The Personal brake never depends on the control plane.** It lives in the
user's own database and is reachable through the CLI with no session, no
network and no platform. If the control plane is down, a user can still stop
their own Bartholomew. This is tested, including with the control-plane store
destroyed.

## Logging in

```bash
curl -sk https://host:5173/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"..."}'
```

Returns a session token, also set as an `HttpOnly; Secure; SameSite=Strict`
cookie. Browsers use the cookie; non-browser clients send
`Authorization: Bearer <token>`. Sessions expire absolutely (12h) and on idle
(1h), are bound to the client that created them, and are revoked immediately
by logout, by `accounts disable`, or on a client-fingerprint mismatch.

**Known limitation, deliberately accepted for Alpha:** a session token is a
bearer credential and is replayable by anyone who captures it. TLS is what
prevents capture, which is why it is mandatory rather than advised. Genuine
per-request replay resistance arrives with device authentication. See
`bartholomew/platform/sessions.py` for the recorded S8 review.
