# Bartholomew Autonomous Development Policy

Status: operational policy for the autonomous development controller.

## Authority

Taylor's 2026-08-27 instruction to set up a genuinely autonomous development loop is standing task-level authorization for the controller to create **bounded, reversible branch commits and pull requests** that satisfy this policy. It is not blanket authority to change project governance or architecture.

The canonical repository remains authoritative. If this policy conflicts with a canonical SSOT document, the canonical document wins and the autonomous run must stop and escalate.

## Purpose

Keep Bartholomew moving when a human builder is unavailable by allowing Claude Code to complete one small, already-authorized development task at a time while preserving an auditable GitHub trail.

## Hard boundaries

The autonomous controller MUST NOT:

- write directly to `main`;
- merge its own pull request;
- weaken, delete, skip, quarantine, or rewrite tests merely to obtain green CI;
- change canonical SSOT documents, project governance, Parking Brake/consent semantics, privacy/security boundaries, authentication/authorization, durable-memory semantics, database/schema contracts, external-data egress policy, dependency trust, release/deployment policy, or CI/workflow policy;
- add, expose, rotate, print, or commit credentials, API keys, tokens, private keys, customer data, or other secrets;
- add or upgrade dependencies;
- use force-pushes or bypass repository protections;
- duplicate work already active in another autonomous, Claude, or human pull request;
- treat external/provider output as established fact when project provenance rules require evidence status.

When any of those is necessary, the run stops and produces an escalation instead of implementing the change.

## Allowed autonomous work

One bounded task per run, selected from work already authorized by the current repository direction, such as:

- small correctness fixes with clear existing intent;
- reliability fixes that preserve interfaces and governance;
- missing regression tests for existing behavior;
- narrow usability fixes that do not alter product authority;
- low-risk integration completion using already-approved interfaces;
- non-canonical implementation notes that truthfully document an already-made bounded change.

Prefer tangible capability and reliability over polish-only work.

## Change containment

The workflow creates a fresh `autonomous/run-*` branch from current `main`. Claude may edit only the checked-out workspace. Claude is denied direct `git push`, GitHub CLI, and ad-hoc network-fetch commands. After Claude finishes, a separate workflow guard validates changed paths before any commit is created.

The controller treats the following as escalation-only areas:

- canonical SSOT documents;
- `.github/` and automation-controller files;
- dependency manifests and lock/config files;
- identity/policy configuration;
- migrations, schema definitions, authentication, security, governance, Parking Brake, consent, and durable-memory policy code.

## Verification

Before the workflow may push an autonomous branch it must, at minimum:

1. pass the path/authority guard;
2. pass pinned Ruff checks;
3. pass pinned Black check-only formatting;
4. pass the repository default pytest suite;
5. contain an actual coherent diff.

The resulting pull request then runs the repository's normal CI, including critical integration/lifecycle and Windows checks. CI is authoritative; the workflow never edits tests in response to a failure merely to make the run green.

## Merge authority

This workflow does not merge. Safe merge decisions remain with the independent ChatGPT oversight loop. That overseer may merge only a narrowly-scoped autonomous PR whose required CI is green, whose diff remains inside this policy, that has no unresolved review findings or conflicting active work, and that does not require a consequential user decision.

Anything ambiguous is escalated to Taylor.

## Failure behavior

Fail closed. If authentication is unavailable, another autonomous PR is already open, the guard blocks the diff, verification fails, or the task is ambiguous, do not manufacture progress. Leave the repository unchanged and surface the blocker through the workflow/oversight trail.