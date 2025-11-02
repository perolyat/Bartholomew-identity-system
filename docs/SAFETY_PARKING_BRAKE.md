## Safety Parking Brake

### Overview

The Parking Brake is a fail-closed safety mechanism that can block specific Bartholomew components at runtime. When engaged, it prevents execution of designated subsystems (skills, sight, voice, scheduler) until explicitly disengaged.

### Features

- **Fail-closed design**: Components refuse to start when brake is engaged
- **Scoped control**: Block individual components or all components via "global"
- **Persistent state**: Survives process restarts via SQLite storage
- **Audit trail**: All engage/disengage actions logged to `safety.audit` memory kind
- **Zero UX impact when disabled**: Default state is OFF, no performance overhead

### Scopes

The brake supports five scopes:

1. **global** - Blocks all components (supersedes all other scopes)
2. **skills** - Blocks orchestrator/skills execution
3. **sight** - Blocks visual capture pipeline
4. **voice** - Blocks voice I/O streaming
5. **scheduler** - Blocks autonomous drive execution

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
storage = BrakeStorage("data/bartholomew.db")
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

Add to `bartholomew/config/policy.yaml`:

```yaml
parking_brake:
  fail_closed: true
  affected_components: [skills, sight, voice, scheduler]
```

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

### Examples

#### Emergency Shutdown

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
sqlite3 data/bartholomew.db ".schema system_flags"
```

**Audit trail not recording**

Check that `safety.audit` memory rule is configured in `memory_rules.yaml` and that the MemoryStore instance is passed to BrakeStorage.
