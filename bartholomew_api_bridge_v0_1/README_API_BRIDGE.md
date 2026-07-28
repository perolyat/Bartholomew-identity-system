
# Bartholomew API Bridge v0.1 (FastAPI) + Minimal UI

This package adds a stable REST bridge and a tiny local UI so you can chat with Bartholomew
without relying on a fragile builder preview. *(Corrected 2026-07-28: "and log water" removed
from the headline description — water logging is a legacy Stage 0 example endpoint still present
in the API, not the package's current primary purpose; see `RISKS.md`'s tech-debt watchlist.)*

## Files
- `app.py` — root shim so you can run `uvicorn app:app --reload --port 5173`
- `services/api/app.py` — FastAPI app (chat, water, health, conversation stubs)
- `services/api/db.py` — SQLite helper using `data/barth.db`
- `services/api/models.py` — Pydantic I/O models
- `ui/minimal/index.html` — zero-dependency UI (open in a browser at the same origin as API if served statically)
- `scripts/curl_smoke.sh` — quick endpoint smoke tests
- `tests/http_smoke.test.http` — VS Code REST Client tests

## Install
```bash
pip install fastapi uvicorn pydantic python-dateutil
# (Optional) if zoneinfo not available: pip install tzdata
```

## Run
```bash
uvicorn app:app --reload --port 5173
# API docs: http://localhost:5173/docs
```

## Test (curl)
```bash
bash scripts/curl_smoke.sh
```

## Notes
- Timezone is **Australia/Brisbane** for day-boundary ("today") calculations; the legacy
  water-logging endpoints are one example of this, not the reason for it.
- If the `identity_interpreter` import fails, the API uses a benign stub for `/api/chat` so the UI still works. Once paths are correct, it will call your real Orchestrator (via the governed
  Runtime Contract seam when the kernel is running — see `COGNITIVE_RUNTIME.md`).
- Database file is created at `data/barth.db` automatically.
