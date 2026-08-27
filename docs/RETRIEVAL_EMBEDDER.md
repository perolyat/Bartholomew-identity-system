# Retrieval embedder and retrieval-mode truthfulness

> Implementation record for the OP-W003 work package. Canonical for how the
> embedding layer reports itself and how the real model is provisioned; the
> Post-Test #1 Decision Register remains the authority on OP-W003's closure.

## What OP-W003 actually says

`RISKS.md`: *"Retrieval ran on a fallback embedder (register OP-W003, S1, Band
C). `sentence_transformers` was unavailable and a deterministic fallback
embedder was used. **The risk is not the fallback itself but that retrieval
mode and quality were not known and truthfully reported at the time.**"*

`ROADMAP.md` Band C: *"**OP-W003** retrieval-mode decision — intended embedder
enabled, or fallback explicitly approved with measured quality and
degraded-state reporting."*

So the deliverable is not "install a model". It is: the system knows which
embedder is running, says so, and there is a measurement of what that costs.

## The four states

`EmbeddingMode`, reported by `get_embedding_status()` — the single accessor
every surface reads:

| Mode | Meaning | `semantic` | `degraded` |
|---|---|---|---|
| `real` | A genuine semantic model is loaded and serving | yes | no |
| `disabled` | Embeddings deliberately off (`BARTHO_EMBED_ENABLED` unset) | no | no |
| `unavailable` | A model was configured and could not be loaded — fail closed | no | yes |
| `dev_fallback` | The deterministic hash embedder, explicitly enabled | no | yes |

`disabled` is not degraded: the system is doing what it was told, and FTS
retrieval still works. `unavailable` is degraded and produces **no vectors at
all** — that is the fail-closed choice, because writing meaningless vectors is
worse than writing none.

### Where it surfaces

- `GET /api/health` — `retrieval_mode_configured`, `retrieval_mode_effective`,
  `retrieval_semantic`, `retrieval_degraded`, `retrieval_degraded_reason`,
  `embedding_mode`, `embedding_provider`, `embedding_model`.
- `bartholomew embeddings stats` — the same values, from the same accessor.
- The startup banner, which now reports the *effective* mode.

`retrieval_semantic` is the field that answers OP-W003's question directly.
It is deliberately separate from the mode, exactly as `model_reachable` is
separate from `model_real`: a `hybrid` configuration running the deterministic
embedder is structurally hybrid and is **not** semantic retrieval. Reporting
only the mode would call that hybrid, and be believed.

## The fallback is explicit, never automatic

Before this work, any failure to import `sentence-transformers` — including it
simply not being an installed dependency, which was every deployment — silently
produced SHA-256 hash vectors. That is the condition OP-W003 records.

Now: a load failure raises `EmbedderUnavailableError`. The deterministic
embedder serves only when `BARTHO_EMBED_ALLOW_FALLBACK=1` is set deliberately.
The test session sets it in `conftest.py`, which is the only reason CI can run
without a model.

## Stored provenance, and the two populations

`memory_embeddings` carries `embedder_kind`:

| Kind | Written by | Retrievable |
|---|---|---|
| `semantic` | a real model | yes, in the semantic population |
| `deterministic-hash` | the dev fallback | yes, in its own population |
| `unverified` | nothing — migration marker only | **no** |

Writers record `EmbeddingEngine.storage_identity`, which derives provider,
model and kind from the engine's *live status*. Writing `cfg.provider` /
`cfg.model` regardless of what actually ran is the defect that made hash
vectors indistinguishable from semantic ones in storage.

`VectorStore.search()` filters on `embedder_kind` and **`allow_mismatch` does
not relax that filter**. Mixing the populations is not a tuning choice.

### Rows written before the column existed

They cannot be classified from the row alone — they record a real model's name
whatever produced them. The migration therefore marks every pre-existing row
`unverified` rather than guessing, which excludes it from retrieval. Nothing is
deleted: not the row, and certainly not the source memory. `bartholomew
embeddings rebuild` regenerates them from the authoritative retained source
text; rows with no retained source stay excluded and are reported.

## Disposition of OP-W003 (2026-08-27): DEFERRED

**OP-W003 is not closed by this work, and neither of Band C's two branches was taken.**

- The real embedder is **not** adopted as the default or intended configuration. The measurement
  below is a bounded synthetic fixture characterising the *fallback*; it is not representative
  real-world evidence about a real model, and is not a basis for a policy change.
- The fallback is **not** "explicitly approved with measured quality" either — the measurement
  argues against approving it.
- **What closes OP-W003 is stronger, representative real-world retrieval evidence** against a
  provisioned real model on real content. Until then OP-W003 stands as a Band C blocker.

What this work discharges is the *reporting* half — the reason OP-W003 was recorded at all.
Retrieval mode is now known and truthfully reported. That is a prerequisite for the decision, not
the decision.

**Current shipped behaviour is the conservative one**, and the section below describes what an
operator would have to do deliberately to change it: `sentence-transformers` is an uninstalled
opt-in extra, `model_path` is unset, `allow_download` is false, and `BARTHO_EMBED_ENABLED` is off
by default. Retrieval runs FTS-first, as it did before.

## Provisioning the real model

`sentence-transformers` is an **extra**, not a core dependency — it pulls
torch, and FTS-only retrieval is a supported, honestly-reported state.

```bash
pip install -e '.[embeddings]'          # the library
bartholomew embeddings provision         # the model assets, deliberately
export BARTHO_EMBED_MODEL_PATH=/path/to/model   # or embeddings.yaml model_path
export BARTHO_EMBED_ENABLED=1
bartholomew embeddings stats             # expect mode=real, semantic=yes
bartholomew embeddings rebuild           # regenerate non-semantic vectors
```

Ordinary loading forces the model hub offline, so **retrieval can never trigger
a download**. `embeddings provision` is the one authorised online step; normal
startup does not depend on an uncontrolled first-run fetch.

Model: `BAAI/bge-small-en-v1.5`, dim 384 — already the repository's intended
model, and 384 matches the dimension `sqlite-vss` is hardcoded to, so no
dimension migration is needed. Changing the model still requires
`embeddings rebuild-vss` and `embeddings rebuild`.

## Measured retrieval quality

`bartholomew embeddings evaluate` runs a bounded fixture (12 memories, 15
cases, in `tests/fixtures/retrieval_eval_corpus.py`) across `fts`, `vector` and
`hybrid`, reporting top-1 and top-3 per mode and per category, always together
with the embedder that produced the numbers.

**Know what this fixture is and is not.** It is 15 synthetic cases over 12
synthetic memories, written by the same author as the code it measures. It is
enough to characterise gross behaviour — and it was: it showed the fallback
vector arm is noise. It is **not** representative real-world retrieval evidence,
and it must not be cited as though it were. That distinction is exactly why
OP-W003 is deferred rather than closed.

**The harness measures; it does not gate.** There is no pass mark, and the
fixture tests assert nothing about quality — only that the apparatus is sound.
A threshold tuned until the fixture went green would describe the tuning, not
the retrieval.

### Baseline: deterministic fallback embedder (2026-08-27)

Measured on this branch with `BARTHO_EMBED_ALLOW_FALLBACK=1`, no real model
installed. **This is the state Real-World Test #1 ran in, now quantified:**

| Mode | Top-1 | Top-3 | Irrelevant queries answered |
|---|---|---|---|
| `fts` | 31% | 31% | 0 / 2 |
| `vector` | 0% | 8% | **2 / 2** |
| `hybrid` | 38% | 46% | 2 / 2 |

By category, the vector arm scores **0/2 on lexical, 0/2 on paraphrase, 0/2 on
semantic-low-overlap, 0/3 on sparse** — and returns something for *every*
irrelevant query. It is not weak retrieval; it is noise that ranks. FTS carries
everything hybrid gets right (3/3 sparse, 1/2 lexical), and hybrid's advantage
over FTS on paraphrase is one case.

This independently confirms the S5.3 characterisation recorded at
`competency_reasoning.DEFAULT_MIN_SHARED_TERMS` — that fallback vector scores
are anti-correlated with relevance — and it is why the lexical relevance gate
exists.

### The relevance gate was not touched

`DEFAULT_MIN_SHARED_TERMS` and the lexical dominance rule in
`_dominant_competency()` are **unchanged**. They are correct under the
fallback, and the evidence needed to recalibrate them is evidence from a *real*
embedder, which nobody has yet measured. The baseline above is the before-half
of that comparison. Re-run `embeddings evaluate` once a model is provisioned,
record the after-half here, and only then consider the smallest justified
adjustment.

## Per-user isolation (Session B seam)

The Alpha architecture is a shared control plane over per-user isolated
runtimes. This work introduces **no cross-user mutable state**:

- the engine singleton holds an immutable loaded model and its configuration —
  no user text, no queries, no derived cache. It is safe to share within one
  runtime process, and is **not** a place to add any user-derived cache;
- vectors, retrieval results and every cache containing user-derived content
  live in the per-runtime SQLite database, addressed by `db_path`;
- `_hub_offline()` restores the environment variables it sets, so one runtime
  provisioning a model cannot leak a global setting into another.

Authentication and tenancy are not implemented here and are Session B's.

## Traps that are easy to reintroduce

- **Never write `cfg.provider` / `cfg.model` to `memory_embeddings`.** Use
  `EmbeddingEngine.storage_identity`. The configured identity is what was
  asked for; the effective one is what ran. Conflating them *is* OP-W003.
- **Never relax the `embedder_kind` filter** in `VectorStore.search()`, and
  never let `allow_mismatch` reach it.
- **Never make the fallback automatic again.** A bare
  `except Exception: self.fallback = True` around the model load restores the
  original defect exactly.
- **Never let model loading reach the network on an ordinary path.** Only
  `embeddings provision` may download.
- **Do not tune the relevance gate against the eval fixture.** The fixture
  exists to be measured, not passed.
