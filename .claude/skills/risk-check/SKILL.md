---
name: risk-check
description: Check RISKS.md and ASSUMPTIONS.md before treating something as a new discovery. Use when finding a defect, oddity, flaky test, or piece of tech debt; when a change rests on a premise that might be unvalidated; and when deciding whether a problem is already known and accepted rather than newly found.
---

# Risk and assumption check

Most "discoveries" in this repository are already recorded. Check before reporting something as
new — and before treating a known, accepted risk as a blocker.

```bash
grep -niE "<symptom>|<subsystem>" RISKS.md ASSUMPTIONS.md
```

## RISKS.md — the register

| Entry | Covers |
|---|---|
| `### R1 — Consent bypass / privacy leakage` | The top risk. Has a dedicated red-team suite in `TEST_MATRIX.md` |
| `### R2 — Over-automation / unsafe side effects` | Why every subsystem needs a parking-brake gating point |
| `### R3 — SQLite / FTS feature variability` | FTS5/matchinfo/bm25 differ across builds — false confidence risk |
| `### R4 — Windows file locking` | Flaky teardown failures, and how they can mask real ones |
| `### R5 — Encryption envelope round-trip bugs` | |
| `### R6 — Metrics duplication / cardinality blowups` | |
| `## Tech debt watchlist` | **The big one** — known open findings, including F9 (competing `setup.py` / `pyproject.toml` manifests, which is why `barth` is not installed) |
| `## Red-team focus areas` | Where adversarial testing is aimed |

## ASSUMPTIONS.md — unvalidated premises

A1 Linux CI is the baseline · **A1b "tests are green" does not imply the path is bug-free** ·
A2 SQLite builds vary · A3 storage/retrieval-layer consent gates are sufficient defence-in-depth ·
A4 single SQLite DB remains viable · A5 encryption envelope format stability ·
A6 one generic competency model suffices · A7 no fine-tuning needed · A8 the
personal/generalisable/system-level classification will suffice · **A9 the deployment serves
exactly one personal Bartholomew identity**.

Plus two prose entries: provider limits require chunked workflows, and cross-device "one mind"
requires a reviewed threat model before any remote exposure (corrected 2026-07-28).

## Rules

- **Known ≠ fixed, and known ≠ acceptable.** An entry on the watchlist means it was seen and
  recorded, not that it is safe to build on. Say which it is.
- **A1b is the one to remember when reporting results.** Green tests do not prove correctness of
  the tested path. Do not use passing tests as evidence of a property they do not test.
- **A3 is load-bearing for privacy.** Any change moving consent filtering out of the storage or
  retrieval layer invalidates it and is a governance change, not a refactor.
- **New findings go in `RISKS.md`; new premises go in `ASSUMPTIONS.md`.** A defect that only ever
  appears in a commit message or a session transcript is lost. Recording it is part of the work.
- Some entries record deliberately *unresolved* disagreements — e.g. the `privacy_guard` vs
  `memory_rules.yaml` sensitivity-vocabulary mismatch. Do not "fix" one of those without approval;
  the non-resolution is a recorded decision.
