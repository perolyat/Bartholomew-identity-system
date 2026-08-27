# Session C handoff — retrieval quality / real embedder (2026-08-27)

> Working note, non-canonical. `docs/RETRIEVAL_EMBEDDER.md` is the implementation
> record; `RISKS.md` and the Decision Register remain the authorities on OP-W003.

## What this branch did

The four approved stages, all on `claude/retrieval-quality-real-embedder-i4x3aw`:

1. **Truthful mode reporting.** Four distinguishable states (`real`, `disabled`,
   `unavailable`, `dev_fallback`) from one accessor, surfaced on `/api/health`
   and `bartholomew embeddings stats`.
2. **Explicit fail/degraded behaviour.** A failed model load raises and produces
   no vectors; the deterministic embedder serves only under
   `BARTHO_EMBED_ALLOW_FALLBACK=1`.
3. **Deliberate enablement.** `sentence-transformers` as an opt-in extra, model
   loaded from a provisioned local path with the hub forced offline,
   `embeddings provision` as the one authorised online step, `embeddings
   rebuild` to regenerate vectors that cannot honestly be retrieved against.
4. **Measured quality.** A bounded fixture and `embeddings evaluate`, reporting
   top-1/top-3 per mode and category alongside the embedder that produced them.

## What is deliberately NOT done

- **The model is not installed.** That is the design, not an omission: ordinary
  startup must not depend on an uncontrolled download. Someone with a machine
  to provision on runs `pip install -e '.[embeddings]'` then
  `bartholomew embeddings provision`.
- **The relevance gate is untouched.** `DEFAULT_MIN_SHARED_TERMS` and the
  lexical dominance rule are unchanged. Only the before-numbers exist; retuning
  needs after-numbers from a real embedder.
- **OP-W003's branch decision is still Taylor's.** The evidence it was waiting
  on now exists; choosing "intended embedder enabled" versus "fallback
  explicitly approved" is a recorded decision, not something this branch takes.

## The one number worth carrying forward

On the deterministic fallback — the state Test #1 ran in — the vector arm
scores **0% top-1** and returns something for **every** irrelevant query.
Hybrid's 38% is FTS's 31% plus one paraphrase case. Anyone tempted to treat the
current hybrid setup as semantic retrieval should read that table first
(`docs/RETRIEVAL_EMBEDDER.md`).

## Traps

Listed in full at the end of `docs/RETRIEVAL_EMBEDDER.md`. The two most likely
to be reintroduced by an unrelated change:

- writing `cfg.provider` / `cfg.model` into `memory_embeddings` instead of
  `EmbeddingEngine.storage_identity` — that conflation *is* OP-W003;
- a bare `except Exception` around the model load, which restores the silent
  fallback exactly.

## Per-user isolation (for Session B)

No cross-user mutable embedding or retrieval state was introduced. The engine
singleton holds an immutable model and config only — no user text, no queries,
no derived cache — and must not become a place to cache user-derived content.
Vectors and results live in the per-runtime database, addressed by `db_path`.
Authentication and tenancy are untouched and remain Session B's.
