# JEV COMMAND CENTER — FINAL ACCEPTANCE REPORT

**Owner:** lead (Orion) · **Produced by:** `artifacts/acceptance/final_gate_run.py --full`
**Status of this document:** the human-readable mirror of the gate's verdict. The gate is the stop condition; this file explains it.

---

## 1. Verdict (one line)

`PROJECT_COMPLETE=true` at HEAD `b0d6d2368c1c`, gate exit `0`.

This supersedes the earlier version of this file, which reported `PROJECT_COMPLETE=false` at HEAD `497a951` with E-05/E-07 unsigned and F-01/F-03 absent. That was an accurate mirror of the run it described; it is no longer a mirror of anything.

| measure | value |
| --- | --- |
| `PROJECT_COMPLETE` | **true** |
| HEAD | `b0d6d2368c1c8947a5aade35980c3a74abfea622` |
| missing | none |
| failed | none |
| degraded | A-01, A-02, A-03, A-04, S-04, S-05 |
| pending external activation | none |
| blockers (`blocker`) | 0 |
| unresolved HIGH (`high`) | 0 |
| exit code | 0 |

## 2. The only stop condition

```
F:\jev> F:\jev\.venv\Scripts\python.exe F:\jev\artifacts\acceptance\final_gate_run.py --full
```

Exit `0` ⟺ `PROJECT_COMPLETE=true`. A PASS of P4, of an individual acceptance runner, or of any single leg is **not** a stop condition — that distinction was added after a P4 PASS was reported as "PRODUCTION-READY LOCAL RELEASE" while `scripts/`, `adapters/`, `apps/` and `facades/` were still empty directories.

## 3. What the gate measured (run at HEAD `b0d6d23`)

| group | result |
| --- | --- |
| T-01…T-07 test totals | 627 passed, 0 failed, 0 errors, 0 skipped across 5 scopes; every scope collected > 0, and the gate computed these itself rather than importing them |
| C-01…C-10 components | OK |
| A-01…A-04 adapters | DEGRADED |
| S-01, S-02, S-03, S-06, S-07 | DEGRADED / OK |
| W-01…W-07 Windows scripts | OK |
| E-01…E-07 end-to-end and gate verdicts | OK |
| F-01…F-06 final evidence | OK |
| P-01…P-05 provenance | OK |

### The three legs, as actually signed

| ID | leg | verdict | release_sha | E-07 head |
| --- | --- | --- | --- | --- |
| E-05 | architecture | `GO WITH CONDITIONS` | `df3e7d91ac27` | `-` |
| E-06 | security | `GO` | `df3e7d91ac27` | `-` |
| E-07 | sre | `GO` | `df3e7d91ac27` | `a47bf7b9561d` |

All three sit at the release freeze `df3e7d91ac274d9…` while E-07 ran at `a47bf7b9561d`. The gate accepts that position and proves it by requiring every path changed since the freeze to be release evidence.

### E-07, the SRE leg's input

`verdict=GO`, **22/22 steps PASS**, abort `null`, step 14 THROUGHPUT `ok=5182 failed=0 rate=86.33/s` over a 60 s window (the bar is ≥900 with 0 failures, unchanged).

## 4. Product freeze

The freeze is `df3e7d91ac274d971ef110199d5b65acf131aacd` — "fix(ops): stop doctor.ps1 crashing at check 7", which changes only `scripts/doctor.ps1`.

A leg may be signed at HEAD **or at the freeze**. The freeze exists because a signature cannot contain itself and simultaneously claim to have been written at the very commit containing it; demanding `release_sha == rev-parse HEAD` makes every leg off by one commit and unsatisfiable by any real signature.

The freeze was first anchored at `dc154b0…`. That was wrong: `dc154b0` is the commit that first added `FINAL_ACTIVATION_CHECKLIST.md`, which is **F-02, release evidence**, and a freeze must carry no evidence. The derivation is recorded in `docs/FINAL_STOP_CONDITIONS.md` sec. E.

## 5. The SRE leg was NO-GO, and why that NO-GO was correct then and is superseded now

The previous SRE signature concluded **NO-GO** with nine conditions, led by two STOP SHIP items: `scripts\start.ps1` could not start the service (`$psi.EnvironmentVariables[...]`, line 136, throws `NullArray`), and `scripts\restart.ps1` therefore could not restore it.

Those were operator-lifecycle defects, not product-data-plane defects, and they were real: `start.ps1` exited 1 before creating any process, reproduced four times including on a clean host. `scripts/` is a **release-evidence prefix**, so repairing it does not invalidate a leg signed at the freeze — which is why the repair was done rather than the wording softened.

The repair is in HEAD: `git diff --name-only -- scripts/` between the freeze and HEAD shows `start.ps1`, `restart.ps1`, `stop.ps1`, `status.ps1` and `lib/JccCommon.ps1`. The current evidence is that they now work: E-07 drives `start.ps1` (step 6, step 19), `restart.ps1` (step 11) and `stop.ps1` (step 16) and all pass, and the controlled-start and restart-acceptance phases reproduce it at runtime.

The superseded signature is preserved, not deleted: `artifacts/acceptance/final_sre_go.superseded-no-go.json` (sha256 `8c2379f869f9…`), and the new document carries a `supersedes` block naming its verdict, signer and signing time. Neither signer is erased by the other.

## 6. Known blind spot: parsing is not running

W-01…W-07 assert only that each `scripts/*.ps1` **parses**. That gap is real and already cost a defect: `scripts\doctor.ps1` parsed cleanly and then crashed at runtime (`InvalidCastFromStringToDoubleOrSingle` in a mixed-type `+` chain), exiting 1 half-way through its own self-check after printing six PASS lines. A check that only parses cannot see that. The gate's six DEGRADED items are, in the same spirit, declarations of limits rather than claims of completeness — see §7.

## 7. Verified-degraded conditions (they do not block activation)

| ID | Condition | Operator consequence |
| --- | --- | --- |
| A-01 | WorkBuddy adapter: no verified steering contract | adapters run recommendation-only |
| A-02 / A-03 / A-04 | Claude / Codex / Hermes: mechanism present, not exercisable here | same as A-01 |
| S-04 | Control UI present, full 8-screen smoke not separately evidenced | treat B-surface completeness as not yet evidenced |
| S-05 | Demo mode exists and is labelled `DEMO` | demo runs must stay labelled `DEMO` |

## 8. What remains before the gate can answer `true`

Nothing in the gate's own checklist: every ID in A–H is `OK`.

Three things remain true outside the checklist and are recorded rather than absorbed:

1. **`jcc_core` is not supervised by any component in this repository.** The shipped watchdog watches the continuation controller; if the service dies, nothing restarts it automatically. Recovery is an operator cycle through the product's scripts (proved in `artifacts/release/WATCHDOG_RECOVERY_REPORT.md`).
2. The scheduled task `JEV-JCC-Continuation-Watchdog` is not registered on this host; registering it needs elevation.
3. **A discrepancy between the written stop condition and this gate's implementation.** `docs/FINAL_STOP_CONDITIONS.md` permits `DEGRADED` for section **B** only (and `PENDING_EXTERNAL_ACTIVATION` for C-05). This gate also emits `g.degraded(...)` for **S-04** and **S-05**, which are section C, and `DEGRADED` never blocks. So `PROJECT_COMPLETE=true` above is this gate's determination while the prose would require those two to be `OK`. It was NOT changed: making the code match the prose would flip this verdict to `false`, which is a stop-condition decision for the owner. The two residuals are named, not absorbed: S-04 `UI smoke test_p2_integration_smoke.py present; full 8-screen coverage not evidenced`; S-05 `demo fixture + DEMO-labelled e2e present (test_demo_e2e.py)`.

## 9. Reproducing this verdict

```
F:\jev> git status --porcelain          # must be empty
F:\jev> git rev-parse HEAD
F:\jev> F:\jev\.venv\Scripts\python.exe F:\jev\artifacts\acceptance\final_gate_run.py --full
```

The verdict is only worth anything if a clone can re-derive it, so that is audited rather than asserted. `artifacts/acceptance/**` is covered by `.gitignore`, which means evidence living there can be **absent from version control while not making the tree dirty** -- the gate would still pass and nobody would notice. Every path this gate actually reads is therefore checked against `git ls-files`:

| | |
| --- | --- |
| gate-consumed paths checked | 38 |
| present but **not** in version control | `artifacts/acceptance/FINAL_ACCEPTANCE_REPORT.md`, `artifacts/acceptance/api_e2e.json`, `artifacts/acceptance/component_evidence/C-01.json`, `artifacts/acceptance/component_evidence/C-02.json`, `artifacts/acceptance/component_evidence/C-03.json`, `artifacts/acceptance/component_evidence/C-04.json`, `artifacts/acceptance/component_evidence/C-06.json`, `artifacts/acceptance/component_evidence/C-07.json`, `artifacts/acceptance/component_evidence/C-08.json`, `artifacts/acceptance/component_evidence/C-09.json`, `artifacts/acceptance/component_evidence/C-10.json`, `artifacts/acceptance/component_evidence/S-01.json`, `artifacts/acceptance/component_evidence/S-02.json`, `artifacts/acceptance/component_evidence/S-03.json`, `artifacts/acceptance/component_evidence/S-06.json`, `artifacts/acceptance/component_evidence/S-07.json`, `artifacts/acceptance/demo_e2e.json`, `artifacts/acceptance/restart_recovery.json`, `artifacts/acceptance/ui_smoke.json`, `artifacts/acceptance/win_scripts/E07_FINAL_EVIDENCE.json`, `artifacts/release/JCC_SOAK_REPORT.md`, `artifacts/release/WATCHDOG_RECOVERY_REPORT.md` |

> WARNING: the paths above are read by the gate but are not committed. A clone would either fail those checks or, worse, be missing evidence silently. Commit them with `git add -f` (they are ignored by `.gitignore`).

Residual limit: the `T-*` checks run `pytest` against the scopes under `packages/`, `apps/` and `facades/`. Those tests are in version control, but the `.venv` they run under is not, so re-deriving the *code* verdict needs a rebuilt environment; the *evidence* verdict needs nothing but the clone.

Regenerated from `artifacts/acceptance/final_gate_result.json` at 2026-09-26T13:03:59.
