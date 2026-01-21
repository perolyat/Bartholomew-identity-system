# Skill Registry Implementation (Stage 4)

> **Status:** ✅ Implemented
> **Date:** 2026-01-22
> **Tests:** 44/44 passing

## Overview

Stage 4 implements a modular skill system for Bartholomew, enabling standardized skill manifests, dynamic loading/unloading, permission enforcement, and starter skills for common functionality.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Skill Registry                           │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐    │
│  │  Manifest   │  │ Permission  │  │   GlobalWorkspace    │    │
│  │  Loader     │  │  Checker    │  │   Event Router       │    │
│  └─────────────┘  └─────────────┘  └──────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    ┌──────────┐        ┌──────────┐        ┌──────────────┐
    │  Tasks   │        │  Notify  │        │ Calendar     │
    │  Skill   │        │  Skill   │        │ Draft Skill  │
    └──────────┘        └──────────┘        └──────────────┘
```

## Components

### 1. Skill Manifest (`bartholomew/kernel/skill_manifest.py`)

Defines the YAML schema for skill declarations:

```yaml
skill_id: "tasks"
name: "Task Manager"
version: "1.0.0"
entry_module: "bartholomew.skills.tasks"
entry_class: "TasksSkill"

permissions:
  level: "auto"  # ask | auto | never
  requires:
    - "memory.write"
    - "nudge.create"
  sandbox:
    filesystem: ["./data/tasks"]
    network: []

subscriptions:
  - channel: "tasks"
    events: ["task_due"]

actions:
  - name: "create"
    parameters:
      - name: "title"
        type: "string"
        required: true
```

### 2. Skill Base Class (`bartholomew/kernel/skill_base.py`)

Abstract base class all skills inherit from:

```python
class SkillBase(ABC):
    @property
    @abstractmethod
    def skill_id(self) -> str: ...

    @abstractmethod
    async def initialize(self, context: SkillContext) -> None: ...

    @abstractmethod
    async def shutdown(self) -> None: ...

    async def execute(self, action: str, params: dict) -> SkillResult: ...
    async def handle_event(self, event: WorkspaceEvent) -> None: ...
```

### 3. Permission Checker (`bartholomew/kernel/skill_permissions.py`)

Enforces skill permissions with:
- **Auto permissions**: Granted by manifest level="auto"
- **Session grants**: Valid until restart
- **Persistent grants**: Stored in database
- **Audit logging**: All checks logged

Permission categories:
- `memory.read`, `memory.write`, `memory.delete`
- `nudge.create`, `nudge.read`, `nudge.dismiss`
- `filesystem.read`, `filesystem.write`
- `network.fetch`
- `system.status`, `system.config`

### 4. Skill Registry (`bartholomew/kernel/skill_registry.py`)

Central coordinator for skill lifecycle:

```python
registry = SkillRegistry(
    skills_dir="config/skills",
    db_path="./data/bartholomew.db",
    workspace=global_workspace,
    kernel=experience_kernel,
)

# Discovery
manifests = registry.discover_skills()

# Load/Unload
await registry.load_skill("tasks")
await registry.unload_skill("tasks")

# Execute action
result = await registry.execute_action(
    "tasks", "create", {"title": "Test task"}
)
```

## Starter Skills

### Tasks Skill (`bartholomew/skills/tasks.py`)

Task management with CRUD operations and reminders.

**Actions:**
- `create(title, description?, due_date?, priority?, tags?)`
- `list(status?, tags?, limit?)`
- `get(task_id)`
- `complete(task_id)`
- `delete(task_id)`
- `update(task_id, ...fields)`

**Events emitted:** `task_created`, `task_completed`, `task_deleted`, `task_overdue`

### Notify Skill (`bartholomew/skills/notify.py`)

Notification management with quiet hours support.

**Actions:**
- `send(message, title?, priority?, sound?)`
- `queue(message, deliver_at?, deliver_after_quiet_hours?)`
- `list_pending(limit?)`
- `cancel(notification_id)`
- `get_quiet_hours()`
- `is_quiet_hours()`

**Events emitted:** `notification_sent`, `notification_queued`, `notification_dismissed`

### Calendar Draft Skill (`bartholomew/skills/calendar_draft.py`)

Draft calendar events with natural language parsing and .ics export.

**Actions:**
- `create(title, start, end?, description?, location?, all_day?, reminder_minutes?)`
- `list(from_date?, to_date?, limit?)`
- `get(event_id)`
- `update(event_id, ...fields)`
- `delete(event_id)`
- `export_ics(event_id?, from_date?, to_date?)`
- `parse_datetime(text)` - Parse natural language dates

**Natural language parsing supports:**
- "today", "tomorrow", "yesterday"
- "next Monday", "this Friday"
- "at 3pm", "at 15:00"
- "tomorrow at 3pm"
- "in 2 hours", "in 30 minutes"

**Events emitted:** `event_drafted`, `event_updated`, `event_deleted`, `event_exported`

## Files Created

```
config/skills/
├── skill.schema.json      # JSON Schema for validation
├── tasks.yaml             # Tasks skill manifest
├── notify.yaml            # Notify skill manifest
└── calendar_draft.yaml    # Calendar draft skill manifest

bartholomew/kernel/
├── skill_manifest.py      # Manifest dataclasses
├── skill_base.py          # Abstract base class
├── skill_permissions.py   # Permission checker
└── skill_registry.py      # Central registry

bartholomew/skills/
├── __init__.py            # Package init
├── tasks.py               # Tasks skill implementation
├── notify.py              # Notify skill implementation
└── calendar_draft.py      # Calendar draft skill implementation

tests/
└── test_skill_registry.py # 44 tests
```

## Database Schema

New tables added by the skill system:

```sql
-- Permission storage
CREATE TABLE skill_permissions (
    id INTEGER PRIMARY KEY,
    skill_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    status TEXT NOT NULL,
    granted_by TEXT,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(skill_id, permission)
);

-- Permission audit log
CREATE TABLE permission_audit (
    id INTEGER PRIMARY KEY,
    skill_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

-- Registry state
CREATE TABLE skill_registry_state (
    skill_id TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    last_loaded TEXT,
    last_error TEXT
);

-- Tasks skill
CREATE TABLE skill_tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    priority TEXT DEFAULT 'medium',
    due_date TEXT,
    tags_json TEXT,
    created_at TEXT,
    completed_at TEXT
);

-- Notifications skill
CREATE TABLE skill_notifications (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    priority TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'pending',
    deliver_at TEXT,
    created_at TEXT,
    sent_at TEXT
);

-- Calendar draft skill
CREATE TABLE skill_calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT,
    all_day INTEGER DEFAULT 0,
    created_at TEXT
);
```

## Exit Criteria (from ROADMAP.md)

- [x] Skill manifest schema defined + enforced
- [x] Registry can list/load skills; permission model applied
- [x] Starter skills working end-to-end: tasks + notify + calendar draft

## Verification Commands

```bash
# Run all skill registry tests
pytest -q tests/test_skill_registry.py

# Quick check
pytest -q tests/test_skill_registry.py -k "TestSkillRegistry"
```

## Future Enhancements

- **Phase 4.6**: Daemon integration (auto-load skills on startup)
- **Phase 4.7**: API endpoints for skill management
- Skill hot-reloading
- Skill versioning and upgrades
- Community skill marketplace
