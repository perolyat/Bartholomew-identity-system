# TOOLING — connectors, plugins, and how sessions should use them

> **Status:** Reference, not canonical. Where this contradicts a canonical document
> (see `MASTER_PLAN.md`'s "Canonical docs"), the canonical document wins.
>
> **Added:** 2026-08-15. Records the external tooling configured for planning and building
> sessions, and — more importantly — **which system owns which fact**, so the repository and the
> trackers do not drift apart.

---

## 1. The ownership rule (read this first)

Bartholomew's SSOT is **this repository**. `MASTER_PLAN.md`'s non-negotiable #4 is "No doc sprawl —
canonical docs are the only SSOT." Every tool below is subordinate to that.

| Fact | Owner | Must never be authoritative in |
|---|---|---|
| Decisions, alternatives rejected | `DECISIONS.md` | Linear, Todoist, any doc tool |
| Approvals + commit hashes | `MASTER_PLAN.md` Approval Ledger | anywhere else |
| Risks, tech debt, open findings | `RISKS.md` | Linear |
| Stage gates + exit evidence | `ROADMAP.md` | Linear |
| Execution priority | `docs/TILT.md` | anywhere else |
| Interface contracts | `INTERFACES.md` | anywhere else |
| **Work in flight** — what's active, blocked, next | **Linear** | — |
| **Human-only chores + session prep** | **Todoist** | — |
| **Raw tester feedback, pre-decision** | **wherever it is captured** | — until promoted to a canonical doc |

The test: *if this repo were the only thing that survived, would anything important be lost?* If
yes, it is in the wrong place.

---

## 2. Connected and configured

### 2.1 Linear — work in flight

**Status:** connected, configured 2026-08-15.
Workspace `BartholomewAI` (`BAR`), project **Usable POC**.

Set up in this pass:

- Project **Usable POC**, whose description carries the ownership rule above.
- Labels encoding the governance state, which is what makes Linear useful *for this project
  specifically* rather than generic issue tracking:
  - `needs-approval` — proposed but **not** authorised. Sequencing is not approval.
  - `approved` — explicit approval recorded in the Approval Ledger.
  - `real-use` — directly advances time-to-real-use. Highest priority class.
  - `governance` — touches consent gates, parking brake, redaction/encryption, retention.
  - `tilt-deferred` — fails `docs/TILT.md`'s prioritisation test.
- **BAR-5** — Put slice 1 into real use. The recorded next move. Urgent, `real-use`.
- **BAR-6** — Scope slice 2 from slice 1's real feedback. Blocked by BAR-5, deliberately unscoped.

**How you should use it.** Open Linear at the start of a planning session to answer one question:
*what is actually in flight?* That is all it is for. Do not paste rationale into issues — when you
find yourself explaining *why*, that text belongs in `DECISIONS.md`. Keep the label honest: an
issue moves to `approved` only once you have actually approved it and the ledger has the hash.
When an issue closes, the durable record goes into the canonical docs; the Linear issue is
disposable.

**How Claude uses it.** At the start of a session, check the Usable POC project for what is active
and blocked, rather than re-deriving it from `MASTER_PLAN.md`'s prose. Never treat an `approved`
label as approval — verify against the Approval Ledger, because the label is a convenience and the
ledger is the record. Never file a risk or a decision as a Linear issue.

### 2.2 Todoist — human-only chores

**Status:** connected, configured 2026-08-15. Project **Bartholomew Sessions**.

The lane boundary matters because Todoist and Linear will otherwise dual-track the same backlog:

- **Linear** — project work in flight.
- **Todoist** — things only *you* can do, especially things blocking a session.
- **Repo** — everything durable.

Seeded with the three real blockers, all labelled `blocks-claude`: stand up a webhook endpoint,
actually use capture/recall and record the reaction, and enable the plugins/connectors in §4.

**How you should use it.** If a task describes something Claude could do in a session, it is in the
wrong lane — move it to Linear. The `blocks-claude` label is the valuable part: it is the list of
things that make a session productive before it starts.

**How Claude uses it.** Check `blocks-claude` when a session appears stalled on something external.
Do not file project work here.

### 2.3 Ansvar AI — regulatory grounding for the consent/privacy design

**Status:** connected. **Free tier** — verified 2026-08-15.

Free tier gives: 100 `search`/day, one jurisdiction *or* one framework per call, primary
legislation plus regulator decisions. It does **not** give workflows (1 teaser run/month), the
audit ledger, or premium fan-out to case law and preparatory works.

This is not a filler connector. Bartholomew's non-negotiables — consent gating for "ask before
store", redaction before storage, encryption at rest for sensitive kinds, enforceable retention/TTL
— are the exact obligations GDPR codifies. A verified query in this pass returned:

- **GDPR Art. 7** — *"the controller shall be able to demonstrate that the data subject has
  consented"*. Demonstrability, not just gating. This is an argument for the audit/provenance
  record being part of the consent gate rather than adjacent to it.
- **GDPR Art. 9** — explicit consent for special categories, which maps onto the "sensitive
  kinds/fields" distinction already in the memory rules.
- **GDPR Art. 8** — child's consent conditions.

**How you should use it.** Reach for it when designing anything touching consent, retention,
erasure, or sensitive categories — before the design hardens, not after. It answers "what does the
law actually require" with citations, which is very different from reasoning from memory about
privacy.

**How Claude uses it.** `search` requires an explicit scope — it does not infer one from the query.
Use `frameworks=['GDPR']` or a single `jurisdictions=[...]` code, with one or two canonical concept
terms, never a multi-concept compound. Cite the article and the EUR-Lex URL. **This is regulatory
research, not legal advice** — findings inform design and belong in `DECISIONS.md` as rationale,
flagged as such.

### 2.4 UseMyContext.ai — personal context

**Status:** connected, **profile empty** as of 2026-08-15.

Useful for grounding what the tester actually wants as slice 2 gets scoped. Currently contributes
nothing because there is no profile. Filling it is a short interview at usemycontext.ai (or ask
Claude to walk through it — answers are saved as pending suggestions you review before anything is
stored).

Low priority. It becomes worth doing when slice 2 scoping starts, not before.

### 2.5 GitHub — PRs, CI, reviews

**Status:** available in session, scoped to `perolyat/bartholomew-identity-system`.

**How Claude uses it.** Read CI job logs directly when a run fails instead of guessing from the
status. Check `ci.yml`'s four jobs (`quality`, `tests`, `critical`, `windows`) against the
Gatekeeper definition in `CI.md` before claiming a PR is mergeable.

### 2.6 Vercel — idle

**Status:** connected, nothing deployed.

There is no hosted surface yet; the deployment decision on record is hybrid local-first. Leave it
connected and unused. It becomes relevant only if a hosted consumer shell is ever approved — which
it is not today.

---

## 3. Blocked: Superhuman Docs

**Status:** connected and authenticated, but **unusable** as of 2026-08-15.

`whoami` returns the account with **zero workspaces**, `search` returns nothing, and
`document_create` fails with an internal error — there is no personal folder to create into. To fix
it, sign in at docs.superhuman.com and create a workspace/doc once; the connector should work after
that.

**A correction to the earlier recommendation.** I initially suggested moving the Approval Ledger
and risk register into Superhuman tables to cut the size of `DECISIONS.md` and `RISKS.md`. Having
read the doc governance properly, **do not do that.** Those ledgers are canonical. Moving them into
a non-repo system would make an external tool authoritative for approvals and risks, which directly
contradicts `MASTER_PLAN.md`'s "canonical docs are the only SSOT" and would break the audit trail
for a consent-first system. The size problem is real, but the fix is grepping rather than reading
(see `CLAUDE.md` §4), not relocating the authority.

What Superhuman *is* well suited to, once it works: the **raw real-use feedback log** for BAR-5.
Tester reactions are input data, not doctrine — they do not belong in a canonical doc until
they have been turned into a decision, and a table is a better shape for them than markdown.

---

## 4. To enable — requires your action in claude.ai

Claude cannot enable connectors or plugins. These are all in claude.ai settings.

### 4.1 Context7 — highest-value addition

**What:** up-to-date library documentation (`resolve-library-id`, `query-docs`).
**Why here:** FastAPI, Pydantic v2, SQLite FTS5, APScheduler, pytest — the exact libraries the
kernel leans on. It stops stale-API guessing during build sessions.

**Setup:** claude.ai → Settings → Connectors → Browse connectors → search "Context7" → Connect.
Then enable it for the chat you are working in.

**How Claude should use it:** before writing code against a library API that is not already used in
the repo, and whenever a library error suggests the API has moved. Not for questions the repo
already answers — the codebase is the better source for how *this* project uses a library.

### 4.2 Engineering plugin

**What:** skills for `architecture` (ADRs), `system-design`, `tech-debt`, `testing-strategy`,
`documentation`, `code-review`, `debug`, `incident-response`.
**Why here:** these map almost one-to-one onto artifacts you already maintain by hand.

**Setup:** claude.ai → Settings → Capabilities → Plugins → find **Engineering** → Enable.

**Mapping to this repo:**

| Skill | Feeds |
|---|---|
| `architecture` | `DECISIONS.md` entries — decision, alternatives, consequences |
| `system-design` | slice design in `docs/POC_SLICE_*.md` |
| `tech-debt` | `RISKS.md` watchlist |
| `testing-strategy` | `TEST_MATRIX.md` |
| `documentation` | implementation notes under `docs/` |
| `code-review` | the PR checklist in `CHECKLISTS.md` |

**Caveat:** these skills are generic. Their output is a **draft** that must be reshaped to this
repo's conventions — the dated-correction style, the "documentation-only, no production code
touched" scope statement, the approval-gate wording. The `approval-entry` skill in `.claude/skills/`
knows those conventions; the plugin does not.

### 4.3 Product Management plugin

**What:** `write-spec`, `roadmap-update`, `sprint-planning`, `synthesize-research`.
**Why here:** `write-spec` is the right shape for slice planning notes; `synthesize-research` is
directly useful for turning raw slice-1 feedback into scoped slice-2 candidates — which is the very
next thing this project needs.

**Setup:** claude.ai → Settings → Capabilities → Plugins → **Product Management** → Enable.

**Caveat:** same as above, plus one specific to this project — the plugin's sprint/roadmap framing
assumes a team cadence that does not exist here. Use `write-spec` and `synthesize-research`; ignore
`sprint-planning`.

### 4.4 Google Calendar — already installed, just toggled off

Enable it in the chat's connector settings if you want sessions timeboxed against the TILT
priority. Low value otherwise.

### 4.5 Exa — optional

Web + code-docs search, for prior-art research on cognitive-architecture patterns during planning
sessions. Genuinely useful, but Context7 covers the build-session need better. Add it if planning
sessions are where you feel the gap.

### 4.6 Sentry — deferred, but queue it

**Do not connect yet.** The moment slice 1 is running against a real webhook with real usage,
errors coming back as data becomes valuable. Before that there is nothing to observe. Revisit when
BAR-5 closes.

---

## 5. Deliberately not recommended

- **Notion, Google Drive** — a second document home works directly against the repo-as-SSOT
  principle and the "no doc sprawl" non-negotiable. The project has already run two documentation
  reconciliation passes to undo sprawl; do not import more.
- **Figma, Miro** — no UI design workstream exists. Reconsider if a consumer shell is approved.
- **v0, Lovable, CodeWords** — app-generation tools aimed at greenfield front-ends. Nothing to do
  with a governed Python cognitive runtime.
- **Moving canonical ledgers to any external tool** — see §3.

---

## 6. Repo-local Claude configuration

Not connectors, but the highest-leverage part of this setup. See `CLAUDE.md` for the rules
themselves.

- **`CLAUDE.md`** — loaded every session. Approval gate, TILT priority, canonical doc map, the
  command traps (`pytest` is not the full suite; unpinned `ruff` lies; `barth` is not installed).
- **`.claude/hooks/session-start.sh`** — creates `.venv`, installs the package and both
  requirements files, and puts the venv on `PATH` so `ruff`/`black` resolve to the **pinned**
  versions. Runs synchronously, remote sessions only. Validated 2026-08-15: pinned ruff 0.14.3 and
  black 26.3.1 resolve correctly, lint and tests pass.
- **`.claude/settings.json`** — registers the hook, pre-approves read-only and test commands.
**Governance skills** — encode the rituals:

- **`.claude/skills/ssot-check`** — check a claim against the 14 canonical docs before writing.
- **`.claude/skills/slice-plan`** — scope a slice against the TILT test; produce a planning note.
- **`.claude/skills/approval-entry`** — draft the Approval Ledger line and `DECISIONS.md` entry in
  this repo's exact conventions.

**Routing skills** — added 2026-08-15. The canonical docs total ~586KB, which previously reached a
session only if Claude remembered to grep for it. These five carry section anchors and a grep
strategy for the large docs and load automatically when the work matches:
`runtime-map` (`COGNITIVE_RUNTIME.md`), `interfaces` (`INTERFACES.md`), `ci-triage` (`CI.md` +
`TEST_MATRIX.md`), `product-principles` (`CONSTITUTION.md`), `risk-check` (`RISKS.md` +
`ASSUMPTIONS.md`).

They are **routers, not copies** — they point at headings and never duplicate content, so the
canonical docs stay the single source and there is nothing to keep in sync. All 48 referenced
headings were verified to resolve when the skills were written; they reference heading *text*
rather than line numbers so they survive edits. If a doc is restructured, re-run that check.

Note the design constraint: every skill's description sits permanently in context, so this layer
works at roughly eight skills, not thirty. Adding more means choosing what to drop.

Invoke the skills as `/ssot-check`, `/slice-plan`, `/approval-entry`. They also trigger
automatically when a session's work matches their description.

**Note:** the SessionStart hook only takes effect for sessions started from a branch that contains
it. It applies to every future session once merged to `main`.
