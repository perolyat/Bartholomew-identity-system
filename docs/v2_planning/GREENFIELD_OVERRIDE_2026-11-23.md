# SOLOMON V2 — Owner's clean-slate implementation decision

**Date:** 2026-11-23 (Australia/Brisbane)  
**Decision:** **APPROVED — DIRECT OWNER OVERRIDE**, not an assistant recommendation.  
**Airtable:** V2 Programme `R05-GREENFIELD-01`, record `recjVm8oZydFdqyHi`.  
**Supersedes:** The previous delegated R05/R07 proposal to selectively retain/refactor/migrate **old Bartholomew implementation code**. Historical reasoning remains preserved but is **not an active instruction**.  
**Current GitHub status:** This draft PR exists in the **historical repository only to transfer planning documents**. The old repository is *not* the next implementation repository. This document does not authorise a merge to the old repo's `main` or creation of a new code repository without the separate build kickoff.

## Explicit user instruction

> We won't be using any of the old code bas, we will be starting from scratch this time around so there's one decision off the list.

## Binding implementation boundary

1. **New implementation, not a refactor.** The next coding assistant must start with a **new clean repository and new application code**. No fork or working copy of the historical Bartholomew implementation is the implementation starting point.
2. **Zero previous project code reuse.** Do not copy, migrate, import or adapt old project's application modules, source files, classes, database/storage schema or migrations, configuration files, CI workflows, tests, binaries, or executable runtime data. Do not bypass this rule by pasting renamed code into a new directory.
3. **Prior repository is reference-only.** Read its historical architecture decisions, incidents, tests-as-*ideas*, postmortems and accumulated lessons. Never use the old repo as a dependency, package, install, or deployment target. Historical source details are evidence for what not to repeat—not proof new code works.
4. **Do not reinvent commodity software.** Fresh choices of mature third-party libraries, operating system SDKs, databases, standard protocols, hosted services or open-source packages are allowed after checking licences, security, privacy, maintenance, capabilities and total cost. These are **external dependencies**, not reuse of old Bartholomew code.
5. **Keep all approved product outcomes and safety boundaries.** Source of requirements: the 96 currently approved authoritative V2 Decisions requirements, owner-approved full/usable Version 1 goal, MEM-03 fault/deletion recovery, independent GOV-AUTH-1 and Parking Brake, model and device continuity, real independently verified effects, rigorous negative tests and owner control. Rebuilding clean does *not* waive any of them.
6. **Retain the frontend-led, contract-first, vertically integrated delivery method.** Define user journeys and all authorised, denied, pending, unknown, interrupted and recovery states before code; implement UI, one Executive, independent Governance, memory, effect ledger, OS endpoint and outcome-verification paths as coherent new code. A polished mock or tiny prototype cannot count as finished Version 1.
7. **Re-evaluate the new technical stack in P0.** The old repository's 2026 server-centric design is historical architecture context; select the new logical Executive/server/hosting, client framework, phone-native bridge, database, cryptographic protocols, model integrations, test runner and deployment strategy against current V2 requirements and real Windows/Android feasibility. Don't automatically adopt old FastAPI, Python, SQLAlchemy, SQLite, ECI module or directory layout merely because they existed before. The product constraints of a single model-independent identity, independent permissions and locally enforceable stop still apply.
8. **New independent assurance from zero.** Build a new test suite and CI. The previous Windows/SQLite incident, weak or vacuous tests, stale build, lost real user value and unproven model planning remain risk lessons/negative test ideas; they do not establish current new-project failures or passes. Run all new integration, security, reliability, memory-deletion and owner pilot gates on the exact newly built app.
9. **No automatic implementation authorisation.** Choosing greenfield resolves the **reuse versus rebuild** decision. Creating the new repository, coding, paid providers, financial or health data access, phone alarms, deployment and final user release remain bounded by the applicable kickoff/governance decisions.

## Corrected handoff summary

**What is decided:** 100% greenfield **project-authored code**, fresh repository, no migration of old modules or state; preserve approved capabilities, safety and failure lessons; frontend-led contract-first vertically integrated implementation method; a real complete Version 1 is the target.

**What P0 must determine:** technology choices suited to the new design, secure physical Windows/Android test constraints, authoritative one-Executive topology and local stop semantics, supported UI/native phone capability, persistence/deletion/effect contracts, and cost/performance evidence from safe bounded feasibility spikes.

**What P0 must NOT do:** run/patch/fork the old application, copy old tests or schemas, silently deploy, connect private accounts, presume background-phone capabilities, or count the prior project's CI green as evidence about the new project.

**Recorded in:** Airtable R05-GREENFIELD-01 (authoritative current decision), R04/R05/R07 supersession notes, this draft GitHub planning PR. The user need not reapprove the clean-slate choice.
