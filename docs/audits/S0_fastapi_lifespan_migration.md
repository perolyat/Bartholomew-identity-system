# FastAPI Lifespan Migration Ticket

## Status
📋 **BACKLOG** - To be addressed in S1/S2

## Context
FastAPI's `@app.on_event("startup")` and `@app.on_event("shutdown")` decorators are deprecated in favor of the `lifespan` context manager pattern.

## Current State
Deprecation warnings observed in test output from:
- `bartholomew_api_bridge_v0_1/services/api/app.py` lines 98, 138

## Migration Scope

### Files to Update
1. `bartholomew_api_bridge_v0_1/services/api/app.py`
   - Replace `@app.on_event("startup")` with `lifespan` context manager
   - Replace `@app.on_event("shutdown")` with cleanup in lifespan exit

### Example Migration Pattern

**Before:**
```python
@app.on_event("startup")
async def startup():
    global _kernel, _kernel_task
    # initialization code
    pass

@app.on_event("shutdown")
async def shutdown():
    global _kernel
    # cleanup code
    pass
```

**After:**
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global _kernel, _kernel_task
    # initialization code
    yield
    # Shutdown
    # cleanup code
    pass

app = FastAPI(lifespan=lifespan)
```

## Acceptance Criteria
- [ ] All `@app.on_event` decorators replaced with `lifespan` pattern
- [ ] No deprecation warnings in test output
- [ ] Existing startup/shutdown behavior preserved
- [ ] Tests pass without modification

## Priority
**Low** - Deprecation warning only, no functional impact

## Estimated Effort
1-2 hours

## References
- FastAPI docs: https://fastapi.tiangolo.com/advanced/events/#lifespan
- Related to: S0.3 housekeeping tasks
