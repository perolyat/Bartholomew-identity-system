# Test #1 evidence manifest

> **Created:** 2026-08-20. **Digest method:** SHA-256 (`sha256sum`), lowercase hex, computed over
> the exact bytes of the file. Where a digest is taken against a file *as it stood at the tested
> commit* rather than as it stands in the working tree, that is stated explicitly and the git blob
> id is recorded alongside, so the digest stays checkable even if the working-tree file later
> changes for unrelated reasons.
>
> **Evidence freeze:** commit `854a8da7fd107db33a933c4bdb01bf3fd7eb69bd` (merge commit for PR #58).
> See `README.md` for the full provenance record, including why `main` is not the freeze.

---

## 1. Present — post-test interpretation (`interpretation/`)

Interpretation *about* the test, not evidence *from* it. Preserved byte-for-byte; not edited.

| Artifact | Path | Bytes | SHA-256 |
|---|---|---:|---|
| Post-Test #1 Decision Register v2.2 (approved by Taylor 2026-08-20) | `interpretation/BARTHOLOMEW_POST_TEST_1_DECISION_REGISTER_v2_2_FINAL_APPROVAL_CANDIDATE.md` | 43068 | `ebc282bbdc19123310a070a6cd41a27d2c0bbd4cd7e0b323d5d0245ca24aa798` |

**On the filename and the internal status line.** The register's own front matter still reads
`Status: FINAL APPROVAL CANDIDATE — NOT YET APPROVED`, and the filename still carries
`FINAL_APPROVAL_CANDIDATE`. **Both are left exactly as they were, deliberately.** The document was
preserved unmodified, and the approval is recorded *about* it — in `MASTER_PLAN.md`'s Approval
Ledger and in `DECISIONS.md` — rather than by editing the artifact to say it was approved. Taylor's
approval statement of 2026-08-20 establishes the authority; the file's internal wording does not
override it, and this repository has no established approved-artifact renaming process that would
require the filename to change.

## 2. Present — test procedure (context, not results)

Records *how* Test #1 was to be run and what counted as a pass. It is a pre-test procedure: it
evidences the intended method, **not** any observed outcome.

| Artifact | Path | SHA-256 (as at tested commit) | git blob id |
|---|---|---|---|
| First controlled real-world test — procedure | `docs/FIRST_REAL_WORLD_TEST.md` | `645b6259d6a645e65377ea0492d55558146859b102a82a190e291b14b381dbc1` | `6408dc642d098f929690f3a9f4fdf98b710a9a49` |

## 3. Present — the tested implementation itself

The strongest surviving artifact of Test #1 is the code that was tested. It is preserved by git, not
by copying, and is addressable by the freeze hash.

| Artifact | How to retrieve | Digest |
|---|---|---|
| Tested implementation (whole tree) | `git checkout 854a8da7fd107db33a933c4bdb01bf3fd7eb69bd` | commit hash **is** the digest |
| Parking Brake scope allowlist as tested | `git show 854a8da…:bartholomew_api_bridge_v0_1/services/api/routes/governance.py` | `1c1c704783c46609d5c726187620c71c69fe8fb17e81ce0f9d1bd4318ce0b4e6` |
| Policy configuration as tested | `git show 854a8da…:config/policy.yaml` | `679e1a01b72e48b82c21ef3f06918cf2aa4907d13bb57baf06130114c102b81c` |

## 4. Absent — raw Test #1 artifacts the register cites

**None of the following were accessible to this documentation pass. None were invented, and none
were reconstructed from the Decision Register.** Each row names what the register cites, so a future
reader can see precisely which claims are currently untraceable to a preserved artifact.

| Register reference | Artifact the register cites | Status |
|---|---|---|
| PT-F001 | Startup/health record ~`2026-08-19 19:40 +10`; restart `19:48:21`; ready retest `19:48:43` | **Absent** |
| START-N001 | Scheduler nudge record ID 1 ~`2026-08-19 19:40:16 +10` | **Absent** |
| OP-W001/002 | Restart stderr, `2026-08-19 19:48:21 +10` (STANDARD + STRONG ephemeral dev keys) | **Absent** |
| OP-W003 | HPT-001 record; restart stderr / runtime fallback ~`2026-08-19 19:58:52 +10` | **Absent** |
| OP-W004 | Restart stderr, two `Failed to log audit: database is locked` warnings (second ~`20:06:37 +10`) | **Absent** |
| OP-W005 | Shutdown capture `2026-08-20 13:36:27–13:36:30 +10`; redirected log tail | **Absent** |
| NUDGE-F001 | Phase A pending nudge records IDs 3/4 and later repeated curiosity records | **Absent** |
| B-F001 | Phase A→B queue-growth series: UI 51 @ `12:58:31`; backend 52 @ `13:06:02`, 53 @ `13:12:28`, 54 @ `13:33:18`; ID60 @ `13:35:43`; frozen 55 @ ~`13:36` | **Absent** |
| SEC-F002a | Synthetic-password chat / working-memory sequence ~`2026-08-19 22:20–22:21 +10` | **Absent** |
| SEC-F002b | `working-memory(1).json` / working-memory-context capture from the same sequence | **Absent** |
| SEC-H001 | Phase B Finding 002 side observation `2026-08-20 13:15:05 +10` | **Absent** |
| UI-SYNC001a | Final live capture `2026-08-20 13:33:17–13:35:34 +10` (browser 51 vs backend/API 54) | **Absent** |
| TECH-F001 | HPT-003 `2026-08-19 20:02:00 +10`; HPT-016 `20:23:47` | **Absent** |
| PB-F001 | PB-000 completed `2026-08-19 22:08:23 +10`; governance revisions 15/16/17 | **Absent** |
| PB-F002 | Exhaustive Parking Brake matrix record, `2026-08-19`, 48 cases containing `global` or `skills` | **Absent** |
| MF-F001 | FUNC-010 Phase A sweep `2026-08-19 22:16–22:43 +10`; API activations `0.57/0.54/0.51/0.51/0.48` | **Absent** |
| MF-F002 | FUNC-011/013/015; final DB showing 16 episode rows for 8 source events | **Absent** |
| MF-F003 | FUNC-017 Water; `GET /api/water/today` 404 capture | **Absent** |
| MF-L001 / MF-L002 | FUNC-011 Affect slider record; FUNC-012 Clear Focus record | **Absent** |
| HU-F001 – HU-F008 | Product Test Finding 002; HPT-005/006/007/011/012/013/014 records | **Absent** |
| B-F002 – B-F005 | Phase B Findings 002, 003 (+ `13:20:36` extension), 004, 005 | **Absent** |
| B-Q008 | `PHASE-B-OPEN-QUESTION-008.md` `2026-08-20 13:26:20 +10` | **Absent** |
| B-V006 / B-V007 / B-V009 / B-V010 | Phase B Vision Findings 006, 007, 009; B-V010 formal closure record `2026-08-20` | **Absent** |
| INFRA-L001 / DATA-U001 | Chrome-control interruption record; nudge ID 7 state-transition record | **Absent** |
| §7 successes | 17-function machine functional baseline sheet (11 PASS / 4 PARTIAL / 1 FAIL / 1 BLOCKED); 64-combination Parking Brake configuration-state matrix (63 PASS / 1 PARTIAL); 128 engage/disengage transitions across revisions 16–143 | **Absent** |
| §4 accounting | The Test #1 handoff document containing the 38 historical evidence items | **Absent** |

## 5. Consequence for traceability

Because §4 is entirely absent, **no §5 register row's case ID, timestamp, or quoted log line is
currently independently traceable within this repository.** The approved evidence-access limitation
recorded in the register and in `README.md` remains in force and is not weakened by the existence of
this manifest. Sections 1–3 are what a reader can check today; section 4 is what they cannot.
