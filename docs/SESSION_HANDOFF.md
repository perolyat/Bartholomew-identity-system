# Session handoff — 2026-08-15

> Working note for whoever picks this up next (human or agent). Non-canonical.
> Delete or overwrite freely; it describes a moment, not a decision.

## Where things stand

`main` is at the merge of PR #52. Branch `claude/project-status-review-pfpysy` carries two
further commits, open as **PR #53**.

| PR | State | What |
|---|---|---|
| #51 | merged | Serve the minimal UI from the API app at `/ui` |
| #52 | merged | Real model path: anti-fabrication fix, live wiring, UI hierarchy |
| #53 | open | Vision document + hybrid model routing (step 1) |

## Read these first, in this order

1. **`docs/VISION_AND_PERSONAL_DEPLOYMENT.md`** — what Bartholomew is for, who for, the
   deployment target, and the six-step sequence everything else is measured against. It is the
   context this session had and a fresh session will not.
2. **`docs/TILT.md`** — execution-sequencing priority. Note §8 of the vision document argues
   about TILT's *precondition* rather than overriding it, and carries an exit condition.
3. **`MASTER_PLAN.md`** "Next 3 Moves" — still says real-world use of slice 1 is next. The
   vision document supersedes that **if approved**; nothing canonical has been changed yet.

## What changed under the hood, and why it matters

Three defects were fixed that are easy to reintroduce:

- **A failed model backend used to answer with mock text.** `ModelRouter.route()` ended in an
  unconditional `"Mock response for: ..."` return, so any provider failure produced a fabricated
  reply indistinguishable from a real one. Now only the explicit `stub` backend may return
  non-model text; everything else raises `ModelBackendError`. Pinned by
  `tests/test_model_backend_honesty.py`. **Do not add a fallback that returns text on failure.**
- **The live chat path ran on the stub.** Two independent causes (no identity reached the router;
  the backend hint was never anything but `stub`). Fixed in PR #52.
- **`Identity.yaml`'s budget cap was decorative.** `select_model()` accepted a `budget_exhausted`
  flag nothing ever computed. `identity_interpreter/orchestrator/budget_ledger.py` now computes it
  from recorded spend, and **fails closed** — an unreadable ledger reports exhausted rather than
  allowing uncapped spend. Keep that direction.

Two traps worth knowing before touching the model path:

- **Never forward sampling parameters to a cloud Claude model.** `Identity.yaml` carries
  `temperature: 0.2` / `top_p: 0.9` and `select_model()` returns them for every model, but current
  Claude models reject them with a 400. `cloud_llm.py` deliberately drops them; a test asserts
  their *absence*.
- **`Orchestrator(identity_config=...)` is the wrong wiring.** It also builds
  `ContextBuilder → MemoryManager`, which is the superseded conversational-memory path and a hard
  OS-keystore dependency (it crashed startup on a headless host). Use `model_identity_config=`,
  which reaches the model router only.

## Two decisions waiting on the user

1. **Approve or correct the vision document.** If the thesis is wrong, everything sequenced from
   it is wrong. Highest-leverage thing available.
2. **The local/cloud data boundary** (vision doc §9, open question 2). Proposed default: anything
   ambient, routine, or touching stored personal facts stays local; genuinely hard reasoning may
   go cloud within budget. Step 2 is cleanly unblocked once this is settled.

## Next work, in order (vision doc §7)

1. ~~Hybrid model routing~~ — done, in PR #53.
2. **S5.4 — experience → learning/consolidation loop**, closing the reflection-ownership gap
   (`ReflectionGenerator` authoritative, `NarratorEngine` supplementary). This is where the
   interruption budget in vision §2.1 becomes tunable.
3. **Personal deployment** — LAN/VPN binding plus a local email+password login (no email delivery,
   no account system). Vision §4.
4. **Voice** — capture, transcription, streaming, persona-bearing output. The governed seam
   already exists (`run_voice_through_runtime_contract()`, `voice` parking-brake scope); only the
   functionality is missing.
5. **S5.5–S5.7** — initiative scaffolding, dry-run, then controlled live initiative.
6. **Reach** — devices and automations, deliberately last.

## Running it locally

```
ollama pull mistral:7b-instruct      # local model; cloud stays off without an API key
uvicorn app:app --port 5173
```

Then `http://localhost:5173` (redirects to `/ui/`). Check `/api/health` — `"model_real": true`
means a real model is wired; `false` means the stub, and nothing observed afterwards means
anything.

## Known-inaccurate canonical text (flagged, not yet corrected)

- **`RISKS.md`** describes `/api/water/log` and `/api/water/today` as "live, working, legacy code".
  Neither endpoint exists; only the UI panel is real, and it is now labelled accordingly.
- **`MASTER_PLAN.md`** P0 item 1 records a `fastapi>=0.104,<0.121` ceiling; `requirements.txt` is
  `>=0.134,<0.141` (raised for CVE-2026-54283).

## Still outstanding from earlier in the session

The `docs/TILT.md` amendment on polish — that polish is acceptable when it carries a named
justification, an honest read of near- and long-term value, and a comparison against the
alternative use of the time. Agreed in principle, never written.
