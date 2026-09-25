# P3 (Jev Shadow) — RELEASE GATE

**Status: P3 ACCEPTED — all three legs GO.**
**Product freeze:** `280bfa8` — the last commit that changes production code.
**Certification SHA:** `3936c96` — the tree the clean verdict was taken on.
**Ruling authority:** lead (Orion). **Date:** 2026-09-25.
**Supersedes:** the earlier version of this record pinned at `49d91d4` with `GO WITH CONDITIONS`
across two legs. That version is preserved in history (`665909a`) as the record *for that SHA*.

---

## 1. The verdict

### 1.1 Canonical run (the numbers that count)

```
F:\jev\.venv\Scripts\python.exe <detached worktree>\artifacts\acceptance\p3_gate_run.py --full
```

| Field | Value |
|---|---|
| HEAD | `3936c96` |
| dirty | **`False`** |
| passed / failed / errors / skipped | **355 / 0 / 0 / 0** |
| `XFAIL` / `XPASS` | 0 / 0 |
| runner exit code | **0** |
| A-matrix A1–A11 (incl. **A8**) | **all PASSED** |
| D-40 falsifiers (a–e + non-vacuity) | **all PASSED** |
| absorbed failures | **none** (runner `KNOWN_PENDING` is empty) |
| vacuity guard | not triggered (`[]`) |
| parse reconciliation | **`355 == 355`, reconciled** |

Gate rule enforced: `passed > 0 AND failed == 0 AND errors == 0 AND skipped == 0`. A skip is a
FAILURE. Nothing is reclassified.

**The count reconciles exactly:**
`355 = 349` (clean run at `4cc657f`) `+ 1` (harbor's C1 falsifier, `4c9c3f0`) `+ 5`
(`packages/supervisor/tests/test_budget_cost_failclosed.py`, `280bfa8`). Nothing was dropped and
nothing was absorbed.

### 1.2 The evidence is NOT in version control — and this document is the anchor

`artifacts/` is gitignored (`.gitignore:8`). Therefore **every machine artifact of this gate —
including the canonical `p3_gate_result.json` and harbor's own `P3_SRE_GATE_HARBOR.md` — lives
outside version control.** Only the runner itself was force-added (`e74b3f2`).

This is the real root cause of the §1.4 provenance incident: that incident was reported as "the
claim was backed only by a gitignored log", and the deeper truth is that *all* the machine evidence
is gitignored. A gate whose evidence is not revision-controlled can lose its verdict without any
commit changing.

Mitigation applied here: the tracked record carries **sha256 anchors** for the artifacts, so the
untracked evidence is at least pinned by content. These hashes are what make the verdict durable:

| Artifact | sha256 (first 16) | Bytes | Meaning |
|---|---|---|---|
| `artifacts/acceptance/p3_gate_result.json` | `9f96bb6907a97edb…` | 41496 | canonical pointer (currently the clean `3936c96` run) |
| `p3_gate_result.verdict_3936c96.json` | `86222c22afaf2604…` | 41546 | **immutable SHA-named verdict record** |
| `p3_gate_result.clean_pre_c1_4cc657f.json` | `2daf168dcbb683db…` | 40828 | the pre-C1 clean run, preserved |
| `p3_gate_result.c1_red_4c9c3f0.json` | `52b8559aa564cce9…` | 21991 | the EXPECTED-RED state, preserved |
| `p3_gate_result.superseded_dirty_1082fff.json` | `425bb0c85d7467f5…` | 40808 | the clobbering dirty run, preserved |
| `artifacts/acceptance/p3_gate_run.py` | `83ce300b1b98dc6a…` | 17513 | the runner (tracked, `e74b3f2`+) |
| `artifacts/acceptance/P3_SRE_GATE_HARBOR.md` | `be7f5cb299479007…` | 19510 | the SRE leg's own report (independent; untracked) |

Tracked source anchors for this freeze:

| Path | sha256 (first 16) | Note |
|---|---|---|
| `packages/supervisor/budget.py` | `96a11b69dd041482…` | the single production change; prefix matches the A8 pin's recorded new digest `96a11b69..` |
| `packages/supervisor/tests/test_budget_cost_failclosed.py` | `52b6b5db645fddcc…` | the 5 new C1/C3 pins |
| `packages/jcc_core/policy.py` | `a4fe24cb1b2ad4b5…` | `COST_UNITS_PER_ACTION`, the single cost authority |

**Independent reproduction.** The canonical pointer and the immutable `verdict_3936c96` record are
**two separate runs** — different worktree, venv, basetemp and wall-clock — that agree on every
substantive field. The verdict is therefore a reproduction, not a single observation. Every hash in
this section, the §1.1 numbers, the §1.3 window claims and the repository facts in §8/§10 were
independently re-derived and confirmed by the SRE leg (which found no error in this record; the one
defect it did find — an earlier version of §2 borrowing the *runner's* hash as the SRE report's — is
what this revision fixes).

**Residual (P4):** make verdict artifacts revision-controlled, or hash-anchor them automatically.
This section is a manual, interim version of that fix.

### 1.3 What changed across the freeze window

- **`49d91d4 → 280bfa8`** touches exactly six files: three docs (`SECURITY-P3-REVIEW.md`,
  `P3_ARCH_GO.md`, `P3_RELEASE_GATE.md`), two test files, and **one production file**
  (`packages/supervisor/budget.py`). **11 of the 12 pinned artifacts remain byte-identical** from
  `49d91d4`; `budget.py` is the single intentional change, and it is the C1 fix.
- **`280bfa8 → 3936c96`** is **test-instrument-only**: `git diff --name-only 280bfa8 3936c96` =
  exactly one path (`packages/jcc_core/tests/test_p3_acceptance_gate.py`), **zero non-test
  changes**. This is harbor's A8 re-baseline, forced by the C1 fix (see §2.1 C1).
- **`3936c96 → c1df72e`** is docs-only (`docs/p3/P3_ARCH_GO.md`).

Consequence: the product freeze is `280bfa8`, and the certification `3936c96` certifies exactly
that product plus a test-only pin update. The trailing commits (`c1df72e` and this record) are
documentation and cannot alter the certified bytes.

### 1.4 Provenance incident (recorded, not papered over)

An earlier run at `49d91d4` reported `HEAD=49d91d4 dirty=False`, `349/0/0/0`, `GATE = PASS` (console
log `.workbuddy/p3_verdict_final.txt`). It was truthful — but backed only by a gitignored log, and
its machine JSON was **subsequently overwritten** by a concurrent run at `1082fff` (`dirty=True`),
because `p3_gate_result.json` is a single mutable path.

Root cause and fix:
- **Cause:** a shared, mutable verdict artifact + a second run against a dirty tree (§1.2 is the
  deeper cause: none of it was revision-controlled).
- **Fix 1 (process):** the canonical verdict is taken in a **detached worktree**; a run on a dirty
  tree must be labelled and must never overwrite the canonical artifact.
- **Fix 2 (durability, partially done):** harbor now writes an **immutable SHA-named** verdict
  (`verdict_3936c96.json`) and preserves every superseded state as a sibling. The remaining half —
  revision control or automatic hash-anchoring — is §6 item 9.
- **Honesty note:** the dirty set at `1082fff` was provably non-production (a doc diff plus scratch
  files) and A8 still passed, so the substantive verdict never depended on it. But the *record* did,
  and that is what was fixed.

---

## 2. The three legs — all GO

| Leg | Verdict at the freeze | Basis |
|---|---|---|
| **Architecture** (ADR-0008 D-1…D-41 conformance) | **GO** (unconditional) | `docs/p3/P3_ARCH_GO.md`, re-signed at `280bfa8`, recorded in-tree at `c1df72e` |
| **Security** | **GO** | `docs/SECURITY-P3-REVIEW.md` §C — re-affirmed at `280bfa8`; product blockers **0** |
| **SRE / Acceptance** | **GO** | `artifacts/acceptance/P3_SRE_GATE_HARBOR.md` + §1.1 above |

### 2.1 Conditions: all closed

| # | Condition | Status |
|---|---|---|
| **C1** | `budget.py:256` `COST_UNITS.get(action, 0.0)` fail-open (governor charged 0.0 for an uncostable action, so the `cost_units` ceiling could never fire) | **CLOSED — `280bfa8`.** `commit()` now indexes `COST_UNITS[action]` directly and raises on an uncostable action; `action is None` preserved; `CONTINUE`'s legitimate 0.0 remains a *table hit*. Contained by `Sentinel.on_event` (`sentinel.py:116-130`, counted as `persistence_failures`); production call site `sentinel.py:215` only ever passes a real `DirectiveAction`. Pinned by 5 tests (alias **identity**, table↔enum **totality**, raise-on-uncostable, exact-value non-vacuity, assessment-path untouched). |
| **C2** | Two stale `server.py` citations in the spec (E-2 `:174-183`→`:184-193`; E-6 `:173`→`:183`) | **Deferred to P4** — task #72. Doc-only; changes no verdict. Every post-verdict commit drifts the SHA and forces the SRE leg to re-run, so the deferral is deliberate, and the task is left *pending* so it cannot become a silent drop. |
| **C3** | Release-record integrity (`02bdb3f` message/content mismatch; orphan `7aa56f6`; no alias-identity pin) | **CLOSED in substance — `280bfa8`**: the alias-identity pin that `02bdb3f` claimed but never wrote now exists. The commit-record annotations are below (§4.1). |
| **H-C1** | Release doc's provenance line not backed by an artifact | **CLOSED** by the `665909a` rewrite + §1.2/§1.4 here. |
| **H-C2** | Accept the A8 pin-portability fix (`4cc657f`) | **Accepted.** |
| **H-C3** | C1/#70 must land — the SRE gate REDs on it until then | **CLOSED** — see C1. Harbor's falsifier flipped **GREEN**. |

### 2.2 The C1 fix's coupling, and the one process nit in it

The commit that fixes C1 is necessarily the commit that REDs the A8 pin, because
`packages/supervisor/budget.py` is one of the 12 pinned artifacts. So the pin must be re-baselined
(old `1efb9186…` → new `96a11b69…`, recorded in `A8_FROZEN_AT`).

**Nit (aegis-2 advisory, recorded):** the re-baseline landed as a *separate* commit (`3936c96`)
rather than in the same commit as the fix (`280bfa8`), while the A8 test's own rule text says the
pin is updated "in the same commit that freezes the SHA". **Net state is correct and green**
(A8 PASSED in the `3936c96` verdict). The separation was not an oversight: the fix belongs to
sentinel (`packages/supervisor/`) and the pin to harbor
(`packages/jcc_core/tests/test_p3_acceptance_gate.py`), and the ownership rules forbid one member
editing another's file — so a single commit was not available without violating ownership. The
**rule text should be relaxed to "the same freeze *window*"**, and that is §6 item 12.

---

## 3. What the gate itself caught (why it is not a rubber stamp)

Every item below was found *because* the gate was built so it could fail:

| # | Defect | How it surfaced | Outcome |
|---|---|---|---|
| 1 | `cost_units_used` hard-coded `0.0` — the `cost_units` limit could never fire | budget exhaustion matrix red | FIXED (`#63`, `b5b418e`) — derived from persisted `directives.action`, one authority |
| 2 | **Two** threshold resolvers (engine vs Sentinel) | R6-1 differential row red | FIXED (`#61`, `63d1e27`) |
| 3 | `A10` audit↔event parity false-FAILed on any rejected ingest | Metric reviewer, against the code | FIXED — exclusion **plus** positive justification assertions |
| 4 | `R6-1` row was **unsatisfiable** (called a bare module-level `policy.decide`) | implementer proved no implementation could make it green | FIXED (`a6796de`) — drives the real wiring, reads persisted state |
| 5 | Runner could print **`GATE = PASS` with zero tests executed** | independent security reviewer's adversarial probe | FIXED — six vacuity guards |
| 6 | Runner's `_STATUS_RE` **silently dropped 10 tests** whose node ids contain spaces (reported 339 while pytest ran 349; a space-id `FAILED` would also have been dropped) | lead reconciling runner totals against a direct pytest run | FIXED (`49d91d4`) — anchor on `[ NN%]` **and reconcile against pytest's tally, fatal on mismatch** |
| 7 | The verdict artifact was **clobberable** by a concurrent dirty run (§1.4) | independent SRE leg re-derivation | RECORDED + immutable SHA-named artifacts; revision-control half in §6 item 9 |
| 8 | A8 pin was **not portable** (absolute keys + un-normalised EOL vs `core.autocrlf=true`) | independent SRE leg's fresh-worktree check | FIXED (`4cc657f`) — EOL-normalised hashes, repo-relative keys, symmetric guard |
| 9 | **Governor-side fail-open** on the cost table, invisible to the SRE harness | arch gate raised C1; the SRE leg then honestly checked **its own instrument** and found it *could not* have caught it | FIXED (`280bfa8`) + the new falsifier (`4c9c3f0`) — see §3.1 |

Defects 5, 6 and 9 are the same *class*: **a verifier whose own arithmetic cannot be checked.**
All are now fatal-by-construction rather than silent.

### 3.1 The most valuable thing that happened in this phase

When the architecture gate flagged C1, the SRE leg did not merely accept the flag — it asked
*whether its own harness would have caught it*, concluded **it would not** (its budget tests drove
cost only through the store derivation and via valid actions), and closed the gap with an
**EXPECTED-RED falsifier** (`4c9c3f0`) that it then insisted must stay red until the product was
fixed — refusing to let a waiver absorb it.

That is the difference between a gate and a formality. The lead's ruling followed from it: a
deliberately-red row inside an "ACCEPTED" suite is an absorbed failure with a label, so **C1 was
fixed rather than waived.** Closing it cost one small fail-closed change; waiving it would have cost
the gate's credibility.

---

## 4. Changes landed before the freeze

| Item | Commit(s) |
|---|---|
| `#60` pytest basetemp → OS temp (safe-delete exempt zone) | `9c0820d` |
| `#63` `cost_units_used` derived from persisted `directives.action`, one cost authority | `b5b418e` |
| `#66` budget `first_seen` wall-clock vs governor monotonic clock pinned | `1746fde` |
| `#68` `StateStore.transaction()` rollback + lock release on exception pinned | `0e08677` |
| `#67` `BEGIN IMMEDIATE` precedes the tail read (+ DEFERRED falsifier) | `cfbba8e` |
| `#61` ONE threshold resolver injected into engine **and** Sentinel | `63d1e27` |
| `#62` manual endpoints dispatch (inert) instead of `Actuator.execute` (D-28/D-41) | `92e069f`, `4b0f127` |
| `#64` config load fails closed (D-30) | `9542933` |
| `#65` `policy.version → p3.0.0` + `SCHEMA_INTERNAL_VERSION` (D-37) | `9ee4507` |
| `#46` gate harness tracked + R6-1 repair + A8 re-baseline | `2175361`, `a6796de` |
| A8 pin portability (EOL-normalised, repo-relative) | `4cc657f` |
| Gate runner parse fix + pytest-tally reconciliation | `49d91d4` |
| **`#70`/C1 fail-closed cost charge** | **`280bfa8`** |
| C1 falsifier (EXPECTED-RED → GREEN) | `4c9c3f0` |
| A8 re-baseline for `#70` (test-only) | `3936c96` |
| Architecture re-sign (`GO`) | `c1df72e` |

Documentation frozen with the code: `P3_ARCHITECTURE_SPEC.md` (errata E-1…E-6 incl. the F-p3-1
waiver row), `P3_METRIC_EVAL_DESIGN.md`, `SECURITY-P3-REVIEW.md`, `P3_ARCH_GO.md`. ADR-0008 itself
is **unchanged**; its errata register stays E-1…E-4 and the decision range remains **D-1…D-41**.

### 4.1 Commit-record corrections (C3)

- **`7aa56f6` is an ORPHAN** — `git merge-base --is-ancestor 7aa56f6 HEAD` exits 1; it is not in the
  frozen tree and must not be cited as part of it. (It shares author-date and stat with `02bdb3f`.)
  It appears in no draft of this record as a source of frozen content; this line exists to prevent
  the mistake being repeated.
- **`02bdb3f` does not do what its message says.** Its message claims it *"pin[s] supervisor
  `COST_UNITS` as an alias of the single core cost authority"*, but its diff adds an **unrelated**
  test (a valid E-5 precondition pin). Consequence at the time: **no test pinned the alias
  identity** — the gate pinned the table's *values*, not the object identity. **Now remedied:**
  `280bfa8` adds both the alias-identity and the table↔enum totality pins.
- Root cause of both: concurrent writers on one shared branch. Mitigation: path-scoped commits only;
  serialise commits at freeze windows. **Annotation only — history is not rewritten.**

---

## 5. Waivers, deferrals and residuals (recorded, not hidden)

| Item | Disposition |
|---|---|
| Engine-side `AssessmentBudgetGuard` not wired (F-p3-1 / F-3) | **WAIVED for P3.** ADR D-5 says the engine *"may consult"* and that the directive-scope budget is *"fully enforced by the Sentinel"* — which **is** wired, so the shadow plane is non-vacuous. Recorded as a waiver (not an implementation) at `P3_ARCHITECTURE_SPEC.md:500`; tracked as **P4**. Residual accepted: a token holder could grow the tape unbounded; blast radius bounded by loopback bind + token gate. Severity unchanged by the C1 fix (aegis-2 re-checked). |
| `scorer_tokens` / `model_units_used` inert | **WAIVED — inert by design.** No persisted column exists. Pinned by a positive test that FAILS if a column is added without wiring the governor. |
| D-28 signing half on the manual control-plane path | **Deferred to P4** (Errata E-6). The operative prohibition — `_manual` must not call `Actuator.execute` — **is** enforced (`server.py:183` `dispatch`). |
| `audit.py` append lacks `BEGIN IMMEDIATE` (F-5) | **Deferred to P4.** Deliberately not churning the tamper-evidence path at freeze. |
| Read-only GETs open (`/api/audit`, `/api/decisions`) (F-6) | **Accepted for P3** — loopback-only bind. **Precondition for any non-loopback deployment.** |
| `sentinel.py:84` silent `thresholds or ThresholdsConfig()` default | **Recorded LOW** (architect). Unreachable in production wiring; P4 tidy-up. |
| `forced_escalate` skips `commit` (`sentinel.py:214`) → a budget-forced escalation is never charged to `cost_units_used` | **Recorded as a deliberate-choice-under-review** (atlas-2 finding; pre-existing and out of C1's scope). Folded into the file's own test matrix, and put to the file's owner (sentinel) to rule deliberate vs oversight. **P4 either way** — not treated as a defect anyone missed. |
| `budget_snapshot` `cost_units`/`scorer_tokens` dimensions (store returns `0.0`) | **Open question, P4** — raised by sentinel as the residual of `#63`. Needs an A-or-B ruling before metric calibration. Recorded as an open question, not asserted as a defect. |
| `7aa56f6` orphan; `02bdb3f` message/content mismatch | **Recorded** (§4.1). Not rewritten. |
| The gate's own evidence is outside version control (§1.2) | **Residual, partially mitigated by hash-anchoring in this document.** §6 item 9. |

---

## 6. P4 backlog (carried forward)

1. Wire the engine-side budget guard (D-5) — closes the F-3 DoS residual.
2. Refresh the stale spec citations (C2 — **task #72**, owner atlas-2, gated on authorisation).
3. D-28 signing on the manual control-plane path (E-6).
4. `audit.py` `BEGIN IMMEDIATE` + single-instance enforcement (F-5).
5. Token-gate or document the read-only GET surface before any non-loopback bind (F-6).
6. Remove `sentinel.py:84`'s silent default (fail closed if no surface is injected).
7. Asymmetric non-repudiation for directives (ADR D-39 — explicitly a P4 decision).
8. **Repo/hygiene:** add `.gitattributes` (`* text=auto eol=lf`) — `core.autocrlf=true` with no
   `.gitattributes` is what made raw A8 hashes non-portable (F-H2). Deferred so it does not
   mass-mark files modified during closeout.
9. **Verdict-artifact durability:** `artifacts/` is gitignored, so the gate's machine evidence is not
   revision-controlled (§1.2). Commit it, or generate a tracked hash-anchor automatically on each
   run. Interim mitigation is §1.2 of this document.
10. Rule `forced_escalate`'s charge behaviour as deliberate (**document it**) or fix it (falsifier).
11. Rule the `budget_snapshot` `cost_units`/`scorer_tokens` dimensions anomaly.
12. Relax the A8 rule text from "the same commit" to "the same freeze *window*" (§2.2), and populate
    the runner's `A8_REBASELINE` provenance line (currently `null`) so the old→new pin change is
    machine-readable rather than comment-only.
13. Metric evaluation activates only with real shadow sessions (corpus-gated; nothing evaluated at P3).
14. `Actuator.execute` must remain unreachable from any port (D-41 must not regress).
15. Annotate (do not rewrite) the `02bdb3f` / `7aa56f6` record.
16. `artifacts/acceptance/P3_ACCEPTANCE_HARNESS_READY.md` §4 rows F-p3-2/F-p3-3 still state
    pre-`#62` facts (manual path calling `execute`; returning `actuation_disabled` in the HTTP body)
    while §0 marks them landed — annotate the rows. Owner: harbor/atlas-2. Note this file is inside
    the gitignored `artifacts/` tree, so it is not revision-controlled (§1.2) — which is itself the
    reason a stale row can sit there unnoticed.

---

## 7. Signature

**P3 RELEASE GATE = PASS. Product freeze `280bfa8`; certification `3936c96`; 355 / 0 / 0 / 0,
`dirty=False`.**

- **Architecture: GO** (unconditional) — `docs/p3/P3_ARCH_GO.md`, re-signed at `280bfa8`, recorded
  in-tree at `c1df72e`; C1 closed, C3 closed in substance, C2 deferred as doc-only.
- **Security: GO** — `docs/SECURITY-P3-REVIEW.md` §C re-affirmed at `280bfa8`; product blockers **0**.
- **SRE / Acceptance: GO** — §1.1 here + `artifacts/acceptance/P3_SRE_GATE_HARBOR.md`; H-C1/H-C2/H-C3
  all closed.

No P3 acceptance decision rests on a runner whose arithmetic cannot fail: the runner refuses to pass
on zero tests, on partial execution, on a shrunken A-matrix, on contradictory return codes, and on
any disagreement between its own parse and pytest's tally. And no condition was absorbed: the one
genuine fail-open found in the frozen window was **fixed**, not waived.

---

## 8. Process incidents and disclosures (this phase)

Recorded because a gate's record is only worth what it is willing to admit.

1. **Root-level scratch vs the `dirty` flag.** The runner's `dirty` is
   `bool(git status --porcelain)`, so a single untracked file voids a canonical run. Several
   teammates were writing scratch to the repo root. **Contained:** the lead moved every offending
   file (never deleted) to `.workbuddy/scratch/`, and broadcast a repo-wide no-root-scratch rule
   routing evidence to `.workbuddy/scratch/`. Note the asymmetry that made this bite: `*.log` is
   ignored (`.gitignore:11`) but `*.txt` is not — so an identical habit had a different blast radius
   depending only on the extension. One file (`_a2_pytest.txt`) was refused relocation while locked
   by an in-flight run; the lead did not yank it mid-write.
2. **Pre-rule permanent deletes — disclosed, not violations.** Two teammates each deleted their own
   scratch before the no-delete rule existed and before any `dirty=False` verdict was pending: the
   architect (`_atlas2_*.txt`) and the metric scientist (`_metric_state.log`, gitignored and
   therefore never able to dirty the tree). No tracked artifact and no evidence was affected, and
   both self-reported unprompted. **Rule now: MOVE, never delete.**
3. **"Delete succeeded" is not "delete happened".** The metric scientist saw a delete report success
   while silently no-op'ing, and verified by re-listing. That is the **third instance** of the
   same class as defects 5/6/9 in §3: an instrument that reports success while having done nothing.
   The defence is always reconciliation against ground truth, never the return value.
4. **A control-bypass recommendation was rejected.** One teammate proposed clearing pytest's temp
   directory with a direct `shutil.rmtree` to get around the sandbox's
   `SAFE_DELETE_BULK_CONFIRM_REQUIRED` guard. **Rejected as a runbook item:** circumventing a safety
   control to make a test run is not an acceptable standing practice, and the guard's condition
   (per-turn deletion count, threshold 50) is environmental, not a product signal. The safe
   alternative is a **fresh, never-before-used `--basetemp`** per run, which also removes the
   retention-cleanup churn that trips the guard.
5. **Muted output channels.** For part of this phase one teammate's shell channels returned exit
   codes without stdout. Evidence was routed through files and read back, and the final verdict was
   taken from the JSON artifact rather than console text. This is why §1.1 is transcribed into a
   tracked document: console output is not a durable evidence carrier.
6. **`dirty=false` by construction.** The canonical verdict is taken in a
   **pristine detached worktree**, so `dirty=False` is a property of the method rather than a
   coincidence of a quiet moment in a shared tree.

---

## 9. Handoff state

- Working tree: clean (`git status --porcelain` empty) at the time of writing.
- All teammate scratch relocated to `.workbuddy/scratch/` and preserved.
- No pending gate conditions. All remaining items are P4 (§6).
- The lead does not rewrite history: `02bdb3f` / `7aa56f6` are annotated, never amended.

---

## 10. Scope exclusions and the "nothing measured" confirmation

P3's honesty claim is that **nothing was evaluated** — no metric result is *measured* anywhere in the
P3 surface, because no real shadow sessions exist (§6 item 13). The metric leg was asked to check
that claim independently and **confirmed it**: no JCC P3 artifact presents mock-derived numbers as
measured results.

**Scope exclusion (recorded because it is a live conflation risk).** The workspace contains a
**separate, gitignored** subtree `jev-cascade/` (a JEV × CASCADE trading application). It is **not**
a JCC artifact and **not** part of the P3 product freeze, and it is outside this gate's scope
entirely — it has no tracked files in this repository. However, it reports metrics that *look*
measured (including a "shadow agreement" figure) while being driven by **mock providers**, and it
does not carry the D-14 mock-only / non-evidential label.

Why this is written down: `jev-cascade` and JCC P3 both use the word *shadow*, and a future reader —
human or agent — could cite the cascade figure as if it were a JCC P3 shadow-plane result. **It is
not.** Any citation of a numeric result as JCC P3 evidence must resolve to the artifacts anchored in
§1.2, not to `jev-cascade/`. If that subtree is ever treated as a deliverable in its own right, it
needs the mock-only label of its own accord (P4, tracked by the metric leg, not by this gate).

**A note on this gate's own drift.** This document is a tracked carrier and was revised after the
verdict: first to add this section and §6 item 16, then to correct a §2 mis-attribution that borrowed
the *runner's* hash as the SRE report's (found by the SRE leg's adversarial read of this file). Both
revisions are docs-only commits and cannot alter the certified bytes of `280bfa8`; they are recorded
here so the revision is visible rather than silent. The certification SHA `3936c96` and the numbers in
§1.1 are unaffected — confirmed by `git diff 3936c96 <HEAD> -- <12 pinned>` being **empty**.
