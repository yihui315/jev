# JEV COMMAND CENTER — FINAL STOP CONDITIONS

**Owner:** lead (Orion) · **Enforced by:** `artifacts/acceptance/final_gate_run.py`
**Status of this document:** the machine-readable mirror of the gate's checklist.
**Difference from everything before it:** a PASS of *any* earlier gate — P4, P5, an
individual acceptance runner, a security GO, an SRE GO — is **not** a stop condition here.
Only the checklist below, when fully satisfied, sets `PROJECT_COMPLETE=true`.

## Why this document exists

On 2026-09-25 a P4 Freeze Gate PASS was reported as "PRODUCTION-READY LOCAL RELEASE".
That verdict is **revoked**. A P4 PASS establishes only `P4_ACCEPTED`. It does not
establish `PROJECT_COMPLETE`, `FINAL_RELEASE`, or `PRODUCTION_READY_LOCAL_RELEASE`.

The revocation was not cosmetic. At the moment the wrong verdict was issued, the tree
already failed the checklist below in at least these places:

| area | observed state |
| --- | --- |
| Windows scripts (`scripts/*.ps1`) | **nothing exists** — `scripts/` held only `emit_release_evidence.py` |
| Agent adapters (`adapters/`) | **empty directory** |
| Control UI / apps (`apps/`) | **empty directory** |
| Projection facades (`facades/`) | **empty directory** |
| `FINAL_ACCEPTANCE_REPORT.md`, `FINAL_ACTIVATION_CHECKLIST.md` | do not exist |
| `artifacts/release/final-manifest.json` | does not exist |
| KD-004 | still OPEN |
| P5 residual (`core.py` `_broadcast` swallowing) | still present |

A subjective judgment that a platform is "ready" is not evidence. This checklist exists
so that the answer is produced by a command that can fail.

## Rules of the gate

1. **No fail-open.** If a required check cannot be evaluated, the gate answers `false`,
   never `true`.
2. **Zero tests is a FAIL**, not a skip.
3. **An unparsable tally is a FAIL.** The gate takes its counts from pytest's
   junit report, which records the outcome of **every** testcase, and
   cross-checks them against the human summary line; any disagreement is fatal.
   The summary line alone cannot prove "0 failed" — pytest prints a counter only
   when it is non-zero, so a fully green run says `627 passed` and never mentions
   the zeros. Parsing it made a 627/627-green suite report failed/errors/skipped
   as UNKNOWN (a false FAIL); the junit report does not have that blind spot.
4. **Evidence that disagrees with ground truth is a FAIL** — wrong hash, wrong path,
   stale anchor, SHA-1 in a sha256 field.
5. **Unexplained dirty/freeze provenance is a FAIL.**
6. There is **no "known-pending" exemption**. Nothing is absorbed into a PASS.

Outcome statuses: `OK` · `MISSING` · `FAILED` · `DEGRADED` (verified-degraded, only where
the condition permits it) · `PENDING_EXTERNAL_ACTIVATION` (needs a credential the project
never had; **not** a failure).

## A. Component completion

`PROJECT_COMPLETE` requires **all** of:

| ID | Component | Condition |
| --- | --- | --- |
| C-01 | JCC Core | COMPLETE |
| C-02 | AEP | COMPLETE |
| C-03 | State Store | COMPLETE |
| C-04 | Audit Tape | COMPLETE |
| C-05 | Jev Provider Layer | COMPLETE or `PENDING_EXTERNAL_ACTIVATION` |
| C-06 | Shadow Supervisor | COMPLETE |
| C-07 | Policy Engine | COMPLETE |
| C-08 | Budget Engine | COMPLETE |
| C-09 | Signed Directive | COMPLETE |
| C-10 | Safe Actuation | COMPLETE |

## B. Adapters — `COMPLETE` **or** `VERIFIED DEGRADED`

A degraded adapter is allowed **only** if its degradation is *verified* by a capability
probe recorded in the gate output (status `VERIFIED` capability only). Guessing is not
verification. An adapter with no probe at all is `MISSING`, not degraded.

| ID | Adapter | Condition |
| --- | --- | --- |
| A-01 | WorkBuddy Adapter | COMPLETE or VERIFIED DEGRADED (recommendation-only / human-gated retry if no verified steering contract) |
| A-02 | Claude Adapter | COMPLETE or VERIFIED DEGRADED |
| A-03 | Codex Adapter | COMPLETE or VERIFIED DEGRADED |
| A-04 | Hermes Adapter | COMPLETE or VERIFIED DEGRADED |

## C. Platform surfaces

| ID | Surface | Condition |
| --- | --- | --- |
| S-01 | JCC Operator Skill (`jcc_status` … `jcc_budget`) | COMPLETE, with TRACE + negative + prompt-injection + privilege-escalation tests |
| S-02 | MCP Facade | COMPLETE |
| S-03 | Observability (OpenTelemetry/OpenInference exporter abstraction, feature-flagged) | COMPLETE; exporter failure must not stop the Supervisor |
| S-04 | Control UI (Dashboard / Sessions / Session Detail / Decision Center / Security / Settings / Audit / System Health) | COMPLETE; wired to the **real** JCC API, not mock pages |
| S-05 | Demo Mode | COMPLETE; deterministic four-agent fixtures, UI must be labelled `DEMO` |
| S-06 | Session Center Facade | COMPLETE + tested; read-only projection first; 3D crash must not affect Core |
| S-07 | Claw3D Facade | COMPLETE + tested; same rules |

S-06/S-07 note: if the external Session Center / Claw3D product is unavailable, the
facade's **own** contract and tests still must exist; the external integration is
`OPTIONAL VISUAL INTEGRATION` and must not fail the platform.

## D. Windows productisation — all must PASS

PowerShell 5.1 compatible. No blunt killing of node/python processes: PID file +
ownership marker + known port/process identity required.

| ID | Script | Condition |
| --- | --- | --- |
| W-01 | `scripts\bootstrap.ps1` | PASS |
| W-02 | `scripts\start.ps1` | PASS |
| W-03 | `scripts\stop.ps1` | PASS |
| W-04 | `scripts\restart.ps1` | PASS |
| W-05 | `scripts\status.ps1` | PASS |
| W-06 | `scripts\doctor.ps1` | PASS |
| W-07 | `scripts\acceptance.ps1` | PASS |

## E. End-to-end and gate verdicts — all must PASS / GO

| ID | Check | Condition |
| --- | --- | --- |
| E-01 | Demo E2E | PASS |
| E-02 | API E2E | PASS |
| E-03 | UI Smoke | PASS |
| E-04 | Windows Restart Recovery | PASS |
| E-05 | Architecture Gate | GO |
| E-06 | Security Gate | GO |
| E-07 | SRE/QA Gate | GO |

### How the freeze is identified (do not just read the constant)

`RELEASE_FREEZE` in the gate is `df3e7d9…`. That value was derived, not
guessed, and the derivation is recorded here so the next reader can re-derive it
instead of trusting a literal:

1. A freeze is the last commit that carries all product/ops code **and no
   release evidence**. Release evidence is the set the gate calls evidence:
   `artifacts/`, `tools/`, `docs/`, `scripts/`, `.gitignore`, the root
   checklist, the final report — in short, everything in `EVIDENCE_PREFIXES`
   plus `FINAL_ACCEPTANCE_REPORT.md`.
2. `FINAL_ACTIVATION_CHECKLIST.md` is **F-02**, i.e. release evidence.
3. Therefore the commit that first added the checklist is already past the
   freeze, and the freeze is the commit just before it.
4. `dc154b0` added the checklist; `df3e7d9` changed only `scripts/doctor.ps1`
   and added none. **The freeze is `df3e7d9`.**

The first attempt to fix `RELEASE_FREEZE` chose `dc154b0`, and the signer
refused it on exactly this ground. A freeze that contains evidence would let a
leg point at a commit whose provenance includes a document that only exists
because the release is being produced — the signature would describe a
particular state of the release process rather than the product. That is why
the freeze excludes evidence, and why moving the constant invalidates all three
leg signatures.

Each leg's signature must name either the current `HEAD` **or the release
freeze** (`RELEASE_FREEZE`, a constant in the gate). The freeze position exists
because a signature cannot contain itself and simultaneously claim to have been
written at the very commit that contains it — demanding `release_sha ==
rev-parse HEAD` makes every leg off by one commit and unsatisfiable by any real
signature. When a leg is signed at the freeze, the gate proves the signature is
still about the product in `HEAD` by diffing `HEAD` against the freeze and
requiring every changed path to be release evidence (`artifacts/`, `docs/`,
`tools/`, `scripts/`, `.gitignore`, the root checklist). Anything else moving
after the freeze means the signer described a different product. A GO must never
be inherited from P3/P4.

## F. Final evidence — all must be COMPLETE / 0

| ID | Item | Condition |
| --- | --- | --- |
| F-01 | `artifacts\acceptance\FINAL_ACCEPTANCE_REPORT.md` | COMPLETE |
| F-02 | `FINAL_ACTIVATION_CHECKLIST.md` (repo root) | COMPLETE |
| F-03 | `artifacts\release\final-manifest.json` | COMPLETE; regenerable twice with identical substantive fields; every path exists; every sha256 recomputable |
| F-04 | blockers | `blocker = 0` |
| F-05 | unresolved HIGH | `high = 0` |
| F-06 | final gate itself | `final_gate_run.py --full` exit `0` |

## G. Test totals — computed by the gate, never reused from a previous phase

The gate computes its own collection count and its own passed/failed/errors/skipped.
Totals from P3/P4 are **not** imported. Zero collected tests = FAIL.

| ID | Measure | Condition |
| --- | --- | --- |
| T-01 | tests collected | `> 0` |
| T-02 | aggregate tally | reconciled, `failed`/`errors`/`skipped` all known |
| T-03 | failed | `0` |
| T-04 | errors | `0` |
| T-05 | skipped | `0` |
| T-06 | pytest exit code | `0` in every scope |
| T-07 | provider calls during gate | `0` (no paid provider calls) |

Per-scope execution. A scope whose path does not exist is a **FAIL**, not a skip — the gate
cannot evaluate what it cannot find, and "not found" must never read as "not needed":

| ID | Scope | Paths |
| --- | --- | --- |
| T-10 | unit and contract | `packages/jcc_core/tests`, `packages/supervisor/tests` |
| T-11 | platform surfaces | `packages/operator_skill/tests`, `packages/mcp_facade/tests`, `packages/observability/tests` |
| T-12 | control UI and demo | `apps/control_ui/tests` |
| T-13 | projection facades | `facades/session_center/tests`, `facades/claw3d/tests` |
| T-14 | end to end | `tests/e2e` |

A scope that collects zero tests is a FAIL. A per-line parse reporting more FAILED/ERROR than
pytest's own summary is a FAIL (that is an inconsistency), but a per-line parse reporting fewer
is not, because a node id may contain spaces — an instrument that cries false-FAIL is as much a
defect as one that absorbs a failure. That asymmetry is why the authoritative tally comes from
the junit report: the per-line parse stays as a cheap sanity bound, never as the source of truth.

## I. Windows scripts are parsed — running them is a separate question

W-01..W-07 assert only that each script **parses** under Windows PowerShell 5.1
(`[System.Management.Automation.Language.Parser]::ParseFile`). That is a weak
claim and it bit us: `scripts\doctor.ps1` parses cleanly and then **crashes at
runtime** (a PowerShell mixed-type `+` chain coerces `' GiB free / '` into
`System.Double` and throws `InvalidCastFromStringToDoubleOrSingle`), exiting 1
half-way through its own self-check. A check that only parses cannot see that.

Consequence for the activation checklist (F-02): after running it, read the
output of `scripts\doctor.ps1` end to end, not just its exit code, and treat any
script that aborts mid-report as a defect rather than a partially-good run.

## H. Provenance — a FAIL if unexplained

| ID | Measure | Condition |
| --- | --- | --- |
| P-01 | working tree | clean (`git status --porcelain` empty) |
| P-02 | release manifest | 12/12 aligned with the A8 gate and with committed blobs |
| P-03 | hash anchors | consistent with ground truth |
| P-04 | artifact ownership | every artifact attributed, none fabricated |
| P-05 | freeze provenance | explainable |

## Verdict

`PROJECT_COMPLETE = true` only if every ID in A–H is `OK` (with B permitted to be
`DEGRADED` where a verified probe backs it, and C-05 permitted
`PENDING_EXTERNAL_ACTIVATION`). Otherwise `false`, with `missing`, `failed`,
`degraded` and `pending_external_activation` listed explicitly.

**A PASS of `final_gate_run.py --full` is the only stop condition. Nothing else stops
this project.**

## Engineering notes (lead) — how the verdict stays verifiable

`artifacts/*` is excluded by `.gitignore:18` under ADR-0009 (release evidence lives in
`artifacts/release/`, everything else stays ignored, fail-closed). An **untracked gate is
an unreproducible verdict**: if the runner that produced `PROJECT_COMPLETE=true` is not in
the repository, nobody can ever re-derive that answer from a clone.

Therefore:

1. `artifacts/acceptance/final_gate_run.py` is **force-added** into git despite the
   exclusion, exactly as `artifacts/release/manifest.json` was at `ccb040d`. It is the
   only machine stop condition and it must survive a clone.
2. Every evidence file the gate consumes (`component_evidence/*.json`,
   `adapter_capability/*.json`, `final_{architecture,security,sre}_go.json`, the E2E result
   artifacts, `FINAL_ACCEPTANCE_REPORT.md`, `FINAL_ACTIVATION_CHECKLIST.md`,
   `artifacts/release/final-manifest.json`) is force-added at the moment the final verdict
   is computed — not before, so that a verdict can never be assembled from evidence that a
   later commit has changed.
3. A dirty working tree is a FAIL of P-01/P-05 (see H). Evidence written under
   `artifacts/` does not dirty the tree; scratch work does not belong in the repo root.
