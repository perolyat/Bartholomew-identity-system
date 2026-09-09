# Research register — external product/architecture research

> **Not canonical (a reference, not an authority).** This is a *location* for externally sourced
> research and the evidence grading applied to it, in the same sense `docs/evidence/test-1/` is the
> location for Real-World Test #1 evidence. Nothing recorded here is a decision, a scope change or
> an approval. Where a line here appears to contradict a canonical document
> (`MASTER_PLAN.md`'s "Canonical docs"), the canonical document wins.
>
> **Purpose.** Bartholomew's planning documents repeatedly need to answer "how strong is this
> claim?" about material that came from outside the repository. Without a register, external
> marketing claims tend to get quoted into planning prose and then read, two passes later, as
> settled fact. This file keeps the grading attached to the claim.

## Evidence grades

Every externally sourced statement used in planning carries exactly one grade. The grades are
ordered; nothing may be promoted a grade without new material of that grade.

| Grade | Meaning | May be used to… |
|---|---|---|
| **E1 — independently verified** | Reproduced or directly observed by this project (hands-on use, our own measurement, our own tests). | justify promotion of a candidate; close an evidence threshold. |
| **E2 — credible external reporting** | Reported by an independent party with no commercial stake in the claim. | motivate investigation; never alone close an evidence threshold. |
| **E3 — company claim** | Stated by the vendor about its own product (site, blog, launch coverage sourced from the vendor, funding/press material). | record that a claim exists; never treated as a fact about the world. |
| **E4 — inference** | Our reading of E1–E3 material, not stated by anyone. | frame questions; must be labelled as ours. |
| **E5 — Bartholomew design conclusion** | A conclusion this project reached, which stands on Bartholomew's own reasoning whether or not the external claim survives. | feed `DECISIONS.md` / `CONSTITUTION.md` on its own merits. |

**The load-bearing rule:** a Bartholomew principle or candidate must be justifiable at **E5** —
i.e. it must survive the external claim turning out to be false. External material may *prompt* a
principle; it may not be the *reason* one is binding.

---

## RSCH-01 — Violoop (2026-09-08)

**Subject.** Violoop, an early / pre-launch AI-agent product built around a dedicated palm-sized
device that attaches to a personal computer and operates it on the user's behalf.

**Status of the subject.** Pre-launch at the time of this research. No independent hands-on
evaluation has been performed by this project. Post-launch monitoring is being handled separately;
later independent evidence may promote, weaken or reject any of the candidates derived here.

**Standing caution.** Every performance, reliability, security and user-research statement
originating from the vendor is **E3**. None of it is recorded anywhere in this repository as an
industry fact, a benchmark, or a reason a Bartholomew candidate should be promoted.

### Claim ledger

| # | Claim / observation | Grade | How it may be used |
|---|---|---|---|
| 1 | Users often know they want help but do not want to compose a detailed prompt; the product surfaces one or two contextually relevant actions instead of requiring a chat turn. | E3 (product framing) / E5 (the design conclusion) | The *pattern* is adopted at E5 — it restates `CONSTITUTION.md`'s Burden-Reduction and cognitive-accessibility invariants, which stand independently. |
| 2 | Preparation of work is separable from committing consequential actions, and much more can be done proactively while it stays reversible. | E4 (our reading) / E5 | Adopted at E5. It is a restatement of Bartholomew's own consequence/reversibility reasoning, not a Violoop mechanism. |
| 3 | Computer actions should be routed to the most deterministic available mechanism rather than treating everything as visual clicking. | E3 (their public architecture framing) / E5 | Adopted at E5 as a routing *principle*. No claimed ordering, coverage or success rate is adopted. |
| 4 | Claimed action success rates / reliability figures. | E3 | Recorded as existing. **Not** usable as a target, a baseline, or evidence for promotion. |
| 5 | Local-first perception with on-device privacy filtering. | E3 (vendor blog) | Motivates the companion-side perception/filtering candidate only. Bartholomew's own privacy invariants remain the authority. |
| 6 | Workflow learning — the product learns how a user gets things done, not only facts about them. | E3 | Motivates the Workflow-memory candidate. Bartholomew already requires governed, inspectable, reversible learning; that requirement is unchanged. |
| 7 | Eventual personalized model adaptation / fine-tuning as a direction. | E3 | Recorded as long-range research only, explicitly **not** promoted (see the candidate register). |
| 8 | "Artificial intuition" — preparing relevant work before the user asks. | E3 (vendor language) | The anthropomorphic term is **not** adopted as an architectural definition. The governed-proactivity concept is E5. |
| 9 | Tasks continue after the user leaves the immediate interaction. | E3 | Motivates the persistent-delegation candidate. |
| 10 | Dedicated hardware as the delivery vehicle. | E3/E2 (that the device exists — launch coverage, no hands-on evaluation by this project) / E4 (that hardware is *necessary*) | Explicitly **not** adopted as a near-term dependency. |
| 11 | Funding/valuation and user-research claims in press coverage. | E2 at best, largely E3 restated | Context only. No planning consequence. |

### Bartholomew design conclusions drawn (all E5)

These stand on Bartholomew's own documented principles and survive any of the E3 claims above
being false. They are recorded in the canonical documents named, not here:

1. Routine executive assistance must not require the user to open a chat surface and restate
   context — `CONSTITUTION.md`, "Ambient executive assistance".
2. Preparation and commitment are architecturally distinct planes with different governance
   postures — `CONSTITUTION.md`; `COGNITIVE_RUNTIME.md`; `DECISIONS.md`.
3. Actuation should be routed to the most deterministic, least destructive, most verifiable and
   most recoverable route available — `CONSTITUTION.md`; candidate W04-C02.
4. Learning must distinguish knowledge, preference, episodic, workflow, competency and
   policy/boundary content — `COGNITIVE_RUNTIME.md`.
5. Proactivity must be evidence-based, useful, governed and interruption-aware — `CONSTITUTION.md`.
6. Central brain, distributed nervous system — a framing for the **existing** server-centric
   decision, not a new topology.
7. Durable product value lives in the governed personal model, not in whichever foundation model
   is currently strongest.
8. Model-weight personalization is a long-range research direction with a high evidence threshold,
   behind memory → lessons → workflow models → versioned competencies → policy adaptation.

### Sources

Consulted 2026-09-08. Vendor-controlled sources are **E3** by definition; press coverage of a
pre-launch product largely restates vendor material and is treated as **E3 unless the outlet
performed its own hands-on evaluation** (none of the below is known to have).

- <https://violoop.com/> — vendor (E3)
- <https://www.violoop.ai/> — vendor (E3)
- <https://www.violoop.ai/blog/local-first-ai-privacy-explained/> — vendor (E3)
- <https://finance.sina.com.cn/tech/roll/2026-09-01/doc-iniqhsea4757326.shtml> — press (E3/E2)
- <https://m.leiphone.com/category/industrynews/7PXLDeu5ZmrxynHT.html> — press (E3/E2)
- <https://recodex.pro/violoop-raises-hundreds-of-millions-in-angel-and-pre-a-funding-can-ai-agent-hardware-truly/> — press, funding (E3/E2)
- <https://www.pcguide.com/pro/news-pro/violoop-unveiled-at-ifa-berlin-a-palm-sized-device-that-turns-any-computer-into-an-autonomous-ai-assistant/> — press, launch (E3/E2)

### Where the consequences live

- Principles: `CONSTITUTION.md` — "Preparation and commitment; ambient executive assistance".
- Runtime semantics: `COGNITIVE_RUNTIME.md` — memory-kind reconciliation and the correction →
  lesson lifecycle.
- Decision of record: `DECISIONS.md` — "Violoop research integration…".
- Candidates: `docs/waves/W04/W04_CANDIDATE_REGISTER.md`.
- Risks: `RISKS.md` — R7, R8.
