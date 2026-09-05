# Wave 3 (W03) — coordination directory

This directory is authored and owned by **W03-PREP — Wave 3 Preparation & CI**,
the preparation session for Wave 3. It is the authoritative, self-contained record
of Wave 3: a builder or integration session should be able to orient itself from
these files alone, without the chat history that produced them.

| File | Role |
|---|---|
| `W03_MANIFEST.yaml` | **Machine-readable manifest.** Session ids, semantic names, branch / PR / handoff names, ownership, dependencies, shared contracts, required CI tier, status, integration order. Validated by `tests/test_wave_manifest.py` in the PR Fast tier. |
| `W03_PREP_ASSESSMENT.md` | Factual post-Wave-2 baseline: what main and the wave-two candidate head actually contain, the implemented / partial / scaffolded / absent matrix, architectural blockers, research implications. |
| `W03_A_CONTRACT.md` … `W03_E_CONTRACT.md` | One authoritative contract per builder package. |
| `W03_F_CONTRACT.md` | The integration session's contract. |
| `W03_TEST_CONTRACTS.md` | Adversarial and longitudinal test contracts every package and the integration must satisfy. |
| `W03_CI_BASELINE.md` | Measured CI baseline, the tier model, changes made, before/after results, unresolved bottlenecks. |
| `W03_DEFERRALS.md` | The deferral register: important things deliberately not in this wave. |
| `BARTHOLOMEW_W03_<X>_HANDOFF.md` | Written by each session at completion (this directory is their home). |

## Immutable session identity

From Wave 3 on, session identifiers are permanent metadata. Authoritative artifacts never
say "Session A"; they say `W03-A`. The pattern, applied to every session:

| Artifact | Pattern | Example |
|---|---|---|
| Session title | `W03-<X> — <Semantic Name>` | `W03-C — Governed Windows Action & Reliability` |
| Branch | `wave/w03-<x>-<slug>` | `wave/w03-c-governed-windows-action` |
| Pull request | `[W03-<X>] <Semantic Name>` | `[W03-C] Governed Windows Action & Reliability` |
| Handoff | `BARTHOLOMEW_W03_<X>_HANDOFF.md` | `BARTHOLOMEW_W03_C_HANDOFF.md` |
| Manifest entry | `sessions[].id == "W03-<X>"` | |

The id must be visible in the Claude session name, the branch, the PR title, the handoff,
the manifest, integration notes and every status or coordination artifact.

## CI tiers (see `W03_CI_BASELINE.md`)

| Tier | Workflow | Runs when | Target |
|---|---|---|---|
| PR Fast | `.github/workflows/ci.yml` | every pull-request push | < 5 min |
| Integration | `.github/workflows/integration.yml` | PR marked ready for review, or labelled `ci:integration`, merge queue, manual | < 10–15 min |
| Merge Candidate | `.github/workflows/merge-candidate.yml` | push to `main`, PR labelled `ci:merge-candidate`, PR from `wave/w03-f-*`, merge queue, manual | < 15–20 min |
| Nightly | `.github/workflows/nightly.yml` | nightly schedule, manual | unbounded |

Each session's `required_ci_tier` in the manifest is the tier that must be green before
its head is declared frozen.

## Status vocabulary (manifest `status`)

`not_started` · `ready_to_start` · `in_progress` · `frozen` (head declared final for
integration) · `integrated` · `complete` · `blocked`.

## Rules every W03 session inherits

1. **One architecture.** Consume the shared contracts named in the manifest; do not invent a
   parallel execution model, event bus, memory, governance, scheduler or audit authority.
2. **Ownership is exclusive.** Modify only what your contract says you own or may modify.
   Anything else is an integration-only change for `W03-F`, or an escalation.
3. **Governance is not weakened to make a test pass.** Never skip, quarantine, disable or
   delete a governance test. The Parking Brake, consent, Identity policy and candidate-bound
   authorization stay downstream of everything you add.
4. **Manual lesson acceptance remains authoritative** for the whole wave.
5. **Stop and report** at your contract's escalation boundary rather than widening scope.
6. **Freeze means freeze.** After declaring a head frozen, push nothing further to that branch
   without telling `W03-F`.
