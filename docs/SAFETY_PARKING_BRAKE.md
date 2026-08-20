## Safety Parking Brake

> **Status:** Operational/reference documentation for the Parking Brake — how to use it and how it
> is wired. **Not the authority on brake semantics.** `COGNITIVE_RUNTIME.md`'s "The kill-switch:
> `ParkingBrake`" section is the canonical authority for scope, authority tiers, precedence, and the
> "inspect, but do not mutate" rule; `DECISIONS.md` holds the decisions. This document describes;
> it does not decide.
>
> **Corrected 2026-08-20 (Post-Test #1 Decision Register v2.2, §13 item 3).** This document
> described **five** scopes. There are **six** — it was missing `training`. The correction is
> recorded below along with the two caveats that must travel with it, because a corrected scope list
> is easy to mistake for a proof of enforcement, and it is not one.

### Overview

The Parking Brake is a fail-closed safety mechanism that can block specific Bartholomew components
at runtime. When engaged, it prevents execution of designated subsystems (skills, sight, voice,
scheduler, training) until explicitly disengaged.

### Features

- **Fail-closed design**: Components refuse to start when brake is engaged
- **Scoped control**: Block individual components or all components via "global"
- **Persistent state**: Survives process restarts via SQLite storage
- **Audit trail**: All engage/disengage actions logged to `safety.audit` memory kind
- **Zero UX impact when disabled**: Default state is OFF, no performance overhead

### Scopes

The brake supports **six** scopes:

1. **global** - Blocks all components (supersedes all other scopes)
2. **skills** - Blocks orchestrator/skills execution
3. **sight** - Blocks visual capture pipeline
4. **voice** - Blocks voice I/O streaming
5. **scheduler** - Blocks autonomous drive execution
6. **training** - Blocks training/knowledge-ingestion submissions

The authoritative allowlist in code is `VALID_SCOPES` in
`bartholomew_api_bridge_v0_1/services/api/routes/governance.py`; `bartholomew/cli.py`'s `brake on
--scope` help lists the same six. `training` is enforced at the Runtime Contract's
training-ingestion seam (`bartholomew/kernel/runtime_contract.py`, using
`bartholomew/kernel/training.py`'s `TRAINING_BRAKE_SCOPE`), fail-closed and **before any record is
processed**, so a blocked brake yields zero writes and zero consent-queue entries.

> **Known configuration discrepancy, flagged not fixed (2026-08-20):** `config/policy.yaml`'s
> `parking_brake.affected_components` lists only four (`skills`, `sight`, `voice`, `scheduler`).
> That was found during a documentation-only pass with no authority to change configuration, and is
> recorded in `RISKS.md`'s tech-debt watchlist for someone who can. Do not treat that file as the
> scope list.

#### Two caveats that travel with this correction

**1. This correction is documentation accuracy, not enforcement proof.** Real-World Test #1's
Parking Brake configuration-state matrix executed 64 combinations (63 PASS, 1 PARTIAL PASS, 0 FAIL)
and directly validated UI selection, displayed active state, governance-state persistence, and the
skills-backed Notifications enforcement probe — 128 engage/disengage transitions across revisions
16–143 matched expectations. **The matrix did not independently execute or prove direct `sight`,
`voice`, `scheduler`, or `training` enforcement for every combination.** What Test #1 proved about
those four is that the brake's *configuration state* behaved correctly, not that every gated code
path refused at the moment it mattered. Direct per-capability enforcement is the subject of safety
gate **S5** in the Post-Test #1 register, and it has not been discharged. Recording `training` here
does not change that in either direction.

**2. The `sight` / `voice` brake-authority migration seam is still open.** `RISKS.md`'s
"Parking Brake read/write authority is split" entry (2026-08-15, amended 2026-08-20) is the
canonical record: the `sight` and `voice` seams still read the legacy `ParkingBrake`/`BrakeStorage`
(`system_flags`) path, while write authority has been the `GovernanceStore` path since Phase B stage
B6. **Those seams must consolidate
onto the authoritative Governance path before real `sight`/`voice` capability is enabled** — this is
constraint **C6** in the Post-Test #1 register and a **Band B** prerequisite in `ROADMAP.md`'s
"Post-Test #1 readiness bands". **That consolidation is not implemented, and the 2026-08-20
documentation pass deliberately did not implement it** — it is implementation work requiring its own
approval. Note that `sight`/`voice` are today gated by three things, not one: the brake check, then
the Identity Policy Decision, then an always-required fail-closed device consent gate (item 11.21).
The seam is about which brake *authority* those checks read, not about whether they run.

### CLI Usage

#### Engage Brake

```bash
# Engage with global scope (blocks everything)
bartholomew brake on

# Engage with specific scopes
bartholomew brake on --scope skills --scope scheduler

# Engage sight and voice only
bartholomew brake on --scope sight --scope voice
```

#### Disengage Brake

```bash
# Disengage (allow all components)
bartholomew brake off
```

#### Check Status

```bash
# View current brake state
bartholomew brake status
```

### Python API

```python
from bartholomew.orchestrator.safety.parking_brake import (
    ParkingBrake, BrakeStorage
)

# Initialize with database path
storage = BrakeStorage("data/barth.db")  # corrected 2026-07-28: real default is data/barth.db
brake = ParkingBrake(storage)

# Check current state
state = brake.state()
print(f"Engaged: {state.engaged}")
print(f"Scopes: {state.scopes}")

# Engage with specific scopes
brake.engage("skills", "scheduler")

# Engage with global scope
brake.engage()  # Defaults to "global"

# Disengage
brake.disengage()

# Check if specific scope is blocked
if brake.is_blocked("skills"):
    print("Skills execution is blocked")
```

### Configuration

Add to `config/policy.yaml` (path corrected 2026-08-20 — this document said
`bartholomew/config/policy.yaml`, which does not exist; `bartholomew/config/` holds
`embeddings.yaml` and `memory_rules.yaml` only):

```yaml
parking_brake:
  fail_closed: true
  affected_components: [skills, sight, voice, scheduler]
```

> This is the file as it currently stands, quoted accurately. See the configuration-discrepancy note
> under "Scopes" above: this list is **not** the scope allowlist, and it omits `training` and
> `global`.

Add to `bartholomew/config/memory_rules.yaml`:

```yaml
- kind: safety.audit
  summarize: false
  recall_policy: always_keep
  encrypt: standard
```

### Architecture

#### Storage

- **system_flags table**: Stores brake state as JSON `{"engaged": bool, "scopes": []}`
- **memories table**: Audit trail entries with kind `safety.audit`

#### Gating Points

Each component checks brake status before execution:

**Skills (Orchestrator)**
```python
if brake.is_blocked("skills"):
    raise RuntimeError("ParkingBrake: skills blocked")
```

**Sight Pipeline**
```python
if brake.is_blocked("sight"):
    return {"blocked": True}
```

**Voice Stream**
```python
if brake.is_blocked("voice"):
    return  # Early return
```

**Scheduler**
```python
if brake.is_blocked("scheduler"):
    raise RuntimeError("ParkingBrake: scheduler blocked")
```

**Training ingestion** — checked at the Runtime Contract seam, fail-closed, before any record in the
submission is processed:
```python
blocked = await is_blocked_fail_closed_off_loop(training.TRAINING_BRAKE_SCOPE, ...)
if blocked:
    # every record in the submission gets OUTCOME_BLOCKED_BY_GOVERNANCE;
    # zero writes, zero consent-queue entries
```

### Examples

#### Emergency Shutdown

> **Terminology caveat (2026-08-20).** "Emergency shutdown" here means *halting autonomous
> operations through the brake*. It is **not** the independent emergency shutdown that Post-Test #1
> decisions D11 and safety gate S9 require before unattended real sensing or consequential device
> agency. That one must work **outside** Bartholomew's ordinary UI and **without** its in-process
> cooperation; this CLI path is in-process and depends on the runtime behaving. **The independent
> emergency stop does not exist yet.** Do not read this example as evidence that it does.

```bash
# Immediately halt all autonomous operations
bartholomew brake on

# Verify status
bartholomew brake status
# Output: Status: ENGAGED (blocking)
#         Scopes: global
```

#### Selective Blocking

```bash
# Block only scheduler while allowing interactive use
bartholomew brake on --scope scheduler

# Later, allow scheduler but block skills
bartholomew brake on --scope skills
```

#### Process Restart

The brake state persists across restarts:

```bash
# Terminal 1: Engage brake
bartholomew brake on --scope scheduler

# Terminal 2: Restart daemon
# Scheduler will remain blocked after restart

# Terminal 1: Verify
bartholomew brake status
# Output: Status: ENGAGED (blocking)
#         Scopes: scheduler
```

### Safety Guarantees

1. **Fail-closed**: If brake is engaged, components will not execute
2. **Persistent**: State survives process crashes and restarts
3. **Audited**: All state changes logged to safety.audit
4. **Backward compatible**: Default OFF state means no impact on existing deployments

### Testing

Run the test suite:

```bash
# Unit tests
pytest tests/test_parking_brake_persistence_roundtrip.py
pytest tests/test_parking_brake_scoped_blocks.py

# Integration tests
pytest tests/integration/test_parking_brake_integration.py
```

### Troubleshooting

**Component execution fails with "ParkingBrake" error**

Check brake status:
```bash
bartholomew brake status
```

If engaged, disengage:
```bash
bartholomew brake off
```

**Brake state not persisting**

Verify database permissions and that `system_flags` table exists:
```bash
sqlite3 data/barth.db ".schema system_flags"
```

**Audit trail not recording**

Check that `safety.audit` memory rule is configured in `memory_rules.yaml` and that the MemoryStore instance is passed to BrakeStorage.
