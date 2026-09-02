# Operator runbook: devices and trusted groups

> **Status:** Working reference (2026-09-01). **Non-canonical** — a document
> under `docs/`, not one of the canonical SSOT docs. It is the local-alpha
> procedure for `bartholomew devices`, `bartholomew groups` and
> `bartholomew share`. The mechanism, and what it does not close, is in
> [`E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md`](E_DEVICE_TRUST_AND_TRUSTED_GROUPS.md).
> Where this note and a canonical document disagree, the canonical document
> wins.

## 0. Posture, before anything else

**The default network posture is local and private, and nothing in this
runbook changes it.** Every command below talks to the control-plane database
on disk, not over HTTP — the same rule `bartholomew accounts` follows, for the
same reason: an authenticated remote surface that can enrol a device is an
authenticated remote surface that can enrol an attacker's device.

Do not, in the course of any procedure here:

* expose Bartholomew publicly, or set `BARTH_API_ALLOW_NON_LOOPBACK=1` to make
  something reachable;
* configure router port forwarding;
* provision cloud infrastructure of any kind;
* commit a credential to the repository, a dotfile, or a shell history;
* print a credential after its one-time issuance;
* weaken TLS, `BARTH_AUTH_MODE`, or any authentication default for
  convenience.

Two rules the tooling enforces so you do not have to remember them: a secret
is printed **once**, at the moment it is minted, and no read command can print
one; and `devices complete` reads its secret from **stdin**, never from argv,
so it never lands in shell history or `ps`.

Set the control-plane path explicitly if this deployment does not use the
default:

```bash
export BARTH_PLATFORM_DB_PATH=/var/lib/bartholomew/platform.db
export BARTH_DATA_ROOT=/var/lib/bartholomew
```

## 1. Device lifecycle

### 1.1 Initialise the schema

Every command below calls `init_platform_schema()` first, and it is idempotent
and additive — `CREATE TABLE IF NOT EXISTS` only, no destructive DDL and no
backfill. So there is no separate migration step; the first command creates
what it needs.

```bash
bartholomew accounts list      # confirms the control plane is readable
bartholomew devices capabilities
```

`devices capabilities` prints the frozen vocabulary. Anything absent from it
is **unsupported**, not approximated.

### 1.2 Create a pending enrolment

```bash
bartholomew devices enrol <user_id> desk-pc --platform windows
```

Issues no credential and authorises nothing. Note the printed `device_id` —
that is the registry's identity for this machine, and it is not the same thing
as the label the companion puts in `payload["device_id"]`.

### 1.3 Approve it, and transfer the one-time secret safely

```bash
bartholomew devices approve <device_id> --approver "$(whoami)" --ttl-hours 4
```

The enrolment secret is printed once and stored nowhere. Move it to the
machine **out of band**: a password manager's secure note, or typed at that
machine's console. Not email, not chat, not a shared drive, not a ticket.
Choose the shortest `--ttl-hours` that fits the trip; every hour past that is
a window in which a leaked note is still a working credential.

If it is lost before use, approve again — the previous secret is revoked in
the same transaction, so there is never a second live one.

### 1.4 Complete enrolment (the companion's first authenticated contact)

On the device, write its manifest:

```json
{
  "platform": "windows",
  "companion_version": "0.1.0-prototype",
  "capabilities": [{"kind": "windows.open_url", "version": 1}]
}
```

Then, with the secret on stdin and never on the command line:

```bash
cat /run/secrets/enrolment | bartholomew devices complete --manifest-file manifest.json
```

This prints the **device credential**, once. It also reports any declared
capability this deployment does not understand: those are recorded and
authorise nothing.

### 1.5 Configure the companion without committing credentials

Put the credential in a file the service user alone can read, and reference it
from the unit rather than writing it into any tracked file:

```ini
# /etc/bartholomew/companion.env   — chmod 600, owned by the service user
BARTH_COMPANION_BASE_URL=https://127.0.0.1:8765
BARTH_COMPANION_SOURCE_ID=device:<device_id>
BARTH_COMPANION_DEVICE_ID=desk-pc
BARTH_COMPANION_CREDENTIAL_HEADERS=X-Bartholomew-Device-Credential: <credential>
```

`BARTH_COMPANION_SOURCE_ID` must be exactly `device:<device_id>`: the route
compares the submitted `source_id` against the verified one and refuses with
403 on a mismatch, which is a visible failure rather than a silent
misattribution.

On the Bartholomew side, opening the surface is a deliberate decision:

```ini
Environment=BARTH_DEVICE_INBOUND_AUTH=1
```

Leave it unset and inbound capture stays fail-closed, which is the correct
state for a deployment that has not decided. It refuses to start alongside
`BARTH_INBOUND_ALLOW_TEST_RESOLVER`.

### 1.6 Inspect status and manifest

```bash
bartholomew devices list <user_id>
bartholomew devices show <device_id>
bartholomew devices manifest <device_id>
bartholomew devices audit --user-id <user_id>
```

`last seen` updates only after a credential actually verifies, so it means
"this device was genuinely here", not "somebody guessed at this id".

### 1.7 Rotate

```bash
bartholomew devices rotate <device_id> --actor "$(whoami)"
```

One transaction: the previous credential is revoked as the new one is
inserted, so there is no window in which both work. Reconfigure the companion,
then confirm the old one is dead (§1.9).

### 1.8 Disable, and re-enable

```bash
bartholomew devices disable <device_id> --actor "$(whoami)"
bartholomew devices enable  <device_id> --actor "$(whoami)"
```

Reversible and credential-preserving. The right answer to "the laptop is at
the office", and the wrong answer to "the laptop is gone" — see §4.

### 1.9 Confirm an old credential no longer works

```bash
printf '%s' "<old credential>" | curl -sS -o /dev/null -w '%{http_code}\n' \
  --cacert /etc/bartholomew/tls/cert.pem \
  -H "X-Bartholomew-Device-Credential: $(cat -)" \
  -H 'Content-Type: application/json' \
  --data '{"source_id":"device:<device_id>","event_id":"probe-1",
           "event_type":"probe","payload":{}}' \
  https://127.0.0.1:8765/api/inbound/events
```

**401 is the pass.** Anything else means the credential still authenticates
and the rotation or revocation did not take; stop and investigate before
continuing. Never add `-k` / `--insecure` to make this command succeed.

### 1.10 Remove local device configuration

Delete the companion's environment file and its state file, then confirm:

```bash
rm -f /etc/bartholomew/companion.env ~/.bartholomew/companion-state.json
bartholomew devices show <device_id>     # status should be disabled or revoked
```

Removing the local files stops the machine from talking. It does **not**
revoke anything — a copy of the credential taken beforehand still works until
you revoke it. Revoke first, delete second.

### 1.11 Roll back the schema or the feature

There is nothing to un-migrate: the schema change is four new tables added
with `CREATE TABLE IF NOT EXISTS`, and no existing table, column or row is
touched. Rolling back is therefore a matter of turning the feature off, in
increasing order of severity:

1. **Stop admitting devices.** Unset `BARTH_DEVICE_INBOUND_AUTH` and restart.
   Inbound capture returns to fail-closed; nothing is deleted.
2. **Stop the devices themselves.** `bartholomew devices revoke` each one.
3. **Revert the code.** The old code ignores the four new tables entirely, so
   a checkout of an earlier revision runs against the same database file
   unchanged.
4. **Remove the rows**, only if you genuinely want the enrolment history gone,
   and after taking a copy of `platform.db`:

   ```sql
   DELETE FROM platform_device_credentials;
   DELETE FROM platform_devices;
   ```

   The audit rows in `platform_audit` are append-only by convention and should
   be left alone; deleting them removes the record of what was enrolled and
   revoked, which is the thing you would want during an incident.

## 2. Trusted groups

```bash
bartholomew groups create <owner_user_id> "Household"
bartholomew groups invite <group_id> <invited_user_id> --actor <owner_user_id> --role member
bartholomew groups invitations <invited_user_id>
bartholomew groups accept <invitation_id> --actor <invited_user_id>
bartholomew groups members <group_id> --actor <a_member_user_id>
```

An invitation confers nothing until accepted, expires (default 7 days,
`--ttl-days`), names exactly one account, and can be accepted only by that
account.

Changing and ending membership:

```bash
bartholomew groups set-role <group_id> <user_id> admin --actor <owner_user_id>
bartholomew groups remove  <group_id> <user_id>       --actor <owner_or_admin>
bartholomew groups leave   <group_id>                 --actor <member_user_id>
bartholomew groups archive <group_id>                 --actor <owner_user_id>
bartholomew groups audit   <group_id>                 --actor <a_member_user_id>
```

`member` and `admin` are the assignable roles; ownership is established by
creating a group and is not transferable. The owner archives rather than
leaves, so the record of what was shared survives.

## 3. Sharing

### 3.1 Select and sanitize

Write the record you have chosen to share to a file — `kind`, `key`, `value`
as it is stored — then:

```bash
bartholomew share propose --record-file record.json --kind competency \
    --group <group_id> --publisher <your_user_id> --out proposed.json
```

Nothing is published. Read the output, and in particular read
`sanitization.removed_fields`: that is the publisher's envelope — provenance,
classification, confidence, supervision — which does not travel.

If it refuses, it refuses for a reason worth reading. `this record contains
fields that are never shareable` means exactly that; the fix is to share a
different record, not to edit this one until it passes.

### 3.2 Inspect, then publish

```bash
bartholomew share publish --package-file proposed.json \
    --publisher <your_user_id> --confirm-group <group_id>
```

The group is named a second time on purpose. There is no publish-to-all.

### 3.3 Receive, inspect, adopt

```bash
bartholomew share inbox      <your_user_id>
bartholomew share inspect    <share_id> --actor <your_user_id>
bartholomew share decline    <share_id> --actor <your_user_id>
bartholomew share adopt      <share_id> --actor <your_user_id> --out adopted.json
bartholomew share provenance <share_id> --actor <your_user_id>
```

**Adoption is not acceptance.** It records your decision on the exchange and
hands you the package. Turning it into a local candidate happens in your own
runtime, and making that candidate retrievable knowledge needs a separate,
candidate-bound approval on top — the same approval an entirely local lesson
needs. There is no switch that skips it.

### 3.4 Publish a revision

```bash
bartholomew share revisions <share_id> --actor <your_user_id>
bartholomew share revise <share_id> --record-file updated.json --kind competency \
    --publisher <your_user_id> --expected-revision 1
```

`--expected-revision` is the revision you were looking at. If the share moved
under you, the command refuses and writes nothing — re-read it and decide
again. There is no force flag.

### 3.5 Revoke

```bash
bartholomew share revoke <share_id> --actor <your_user_id>
```

No member may adopt it from now on and no revision may be published. It does
**not** delete what a recipient already adopted: that record is in their
runtime, under their governance. They are shown that it was withdrawn and
decide for themselves.

### 3.6 Handling a revoked or conflicting local adoption

* **Revoked upstream.** `share provenance` shows `revoked: true`, and the
  local candidate's summary reads `[withdrawn upstream]`. A withdrawn share
  cannot be approved or accepted. Anything already accepted stays — review it
  and reject or supersede it on its merits, not automatically.
* **A conflicting update.** `share inbox` shows `update: yes` when a newer
  revision exists than the one you adopted. Adopting it creates a *separate*
  candidate at a separate key; your existing one, customised or not, is
  untouched. Decide about each independently.
* **A local fork.** Once you customise your copy, any approval granted for the
  previous text is invalid — the approval was for what it said then. Approve
  it again for what it says now, or reject it.

## 4. Lost device, and compromised credential

Both procedures start the same way, and the first step is the one that matters:

```bash
bartholomew devices revoke <device_id> --actor "$(whoami)" --reason "lost 2026-09-01"
```

Revocation is terminal and immediate: every credential the device held is
revoked in the same transaction, and the next request on any of them fails.

### 4.1 Lost or stolen device

1. `devices revoke` it, as above.
2. Confirm with §1.9 that the credential now returns 401.
3. `bartholomew devices audit --user-id <user_id>` — read what that device did
   before it was lost. The audit names the device and the events, never
   content.
4. Consider what else lived on that machine. A companion holds a device
   credential; a browser on the same machine may hold a session cookie. If in
   doubt, `bartholomew accounts disable <user_id>` revokes every live session
   for that account in the same transaction, then re-enable it once the person
   has a new password.
5. Enrol the replacement machine from §1.2. There is no un-revoke: a revoked
   device is terminal, deliberately, so a recovered laptop cannot quietly
   resume.

### 4.2 Compromised credential, device still in hand

1. `bartholomew devices rotate <device_id> --actor "$(whoami)"` — one
   transaction, no window in which both credentials work.
2. Reconfigure the companion with the new credential and restart it.
3. Confirm the old credential returns 401 (§1.9).
4. Read `devices audit` for the period the old credential was live.

If you are unsure whether the credential leaked or the whole machine did,
treat it as §4.1. Revoking and re-enrolling costs one short procedure;
guessing wrong costs a machine that still speaks for you.

### 4.3 A trusted group that should not have been trusted

Removing someone stops their access on the next call:

```bash
bartholomew groups remove <group_id> <user_id> --actor <owner_user_id>
```

What they already adopted is theirs and stays in their runtime — that is a
property of the design, not a gap. If material you published should not have
been, `share revoke` it: that stops further adoption and marks it withdrawn
wherever its provenance appears. Neither action reaches into anybody else's
memory, and no procedure in this runbook can.
