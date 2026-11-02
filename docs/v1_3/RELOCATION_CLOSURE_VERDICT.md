# JCC v1.3.5 — RELOCATION CLOSURE VERDICT

> Written at HEAD `3a382de`, branch `release/jcc-v1.3-final`.
> Every number here is a measurement taken in this session. Nothing is carried
> over from an earlier report, and no earlier PASS is inherited.

## The one-line verdict

**`FULL_GATE_AT_CURRENT_HEAD=FAIL 21/30 blockers=10` · `NOT_RELEASE_READY` ·
`FINAL_RELEASE=false` · `NO_PRODUCT_REGRESSION_FOUND`.**

The ten blockers are **not** ten defects. Sorted by what they actually are:

| Cause | Blockers | Product defect? |
|---|---|---|
| GREEN 4778 was not running (pid=0 at start and end) | `D1_GREEN_PROTECTION`, `B7_W8_RUNTIME_GATE` (5 of its 54), `E5_FREEZE_ANCHOR_RESOLVES`, `GREEN_4778_CHANGED` | no — host precondition |
| Host has no hardware GPU in this session | `B8_W10_BROWSER_PERF` (3 of 34) | no — renderer is `Microsoft Basic Render Driver`, `SOFTWARE` |
| Test-fixture / isolation / staleness in the `tools/tests` suite | `C2_TOOLS_TESTS` (21 failed + 12 errors), `C1_CORE_E2E` (2 of 869) | **not yet adjudicated — see below** |
| This session's own edits, already fixed and committed after the run | `A1_FREEZE_MODEL`, `A8_SECURITY_GATE`, `E1_FROZEN_TREE` | no — fixed at `cf9b2ea` / `3a382de` |

## What this round settled

The project had been blocked since 2026-09-30 by a `PRIMARY_EXECUTION_BLOCKER`
that said a sandboxed process cannot create a SQLite database, cannot delete a
file, and therefore cannot serve the product API. **All three halves of that are
false on this host, and the gate has now run end to end because of it.**

| Claim of 2026-09-30 | Measured 2026-10-04 |
|---|---|
| fresh WAL database creation fails | **16/16 pass** — 4 locations x `wal`/`delete`/`truncate`/`persist` |
| `os.unlink` / `os.remove` / `shutil.rmtree` are denied | **all succeed** |
| a sandboxed product API cannot serve | `/api/health` **200 in 0.07 s**, `/api/v1/domain/counts` **0.02 s**, `/api/v1/domain/home` **0.04 s** |

Two findings that only became visible because of that:

1. **The checkout had moved from `F:\jev` to `D:\jev` and the product did not
   notice** — the repo root was typed into twenty places rather than derived, so
   every one of them became a load-time failure. Fixed in two commits
   (`cf9b2ea`, `3a382de`): `jcc_core.config` now derives `REPO_ROOT` from
   `__file__`, and 37 live files across `tools/`, `tools/tests/`,
   `tools/continuation/`, `tools/finalize/` and `scripts/` derive it too.
   Verified by booting the product with **no `JCC_DB_PATH` at all**: it found
   its own database and answered on all three endpoints.
2. **The gate caught the relocation instead of being fooled by it.** A1 exited 2
   on the first run because `tools/tests/test_release_manifest_v1_3.py` still
   pointed at a drive that no longer exists. That file now passes **19/19**,
   exactly its declared floor.

## Legs that passed, and what they cost to believe

21 of 30, including every leg that had previously been unreachable:

* `C1_CORE_E2E` — **869 passed**, and it ran at all only because the database
  blocker was gone.
* `B1_W2_PRODUCT_UI` 30/30 · `B2_W3_MISSION_GATE` 30/30 · `B3_W4_ATTENTION_GATE`
  19/19 · `B4_W5_DELIVERABLES_GATE` 17/17 · `B5_W6_WORLD_GATE` 6/6 ·
  `B6_W7_CONNECTION_GATE` 20/20 — all six Wave gates green at the new head.
* `C3a_TYPECHECK` · `C3b_VITEST` · `C3c_BUILD` — the frontend is intact.
* `F1_INSTRUMENTS_ARE_FALSIFIABLE` **7/7** — this gate still knows how to fail.
* `E3_A8_PINS_VS_HEAD_BLOBS` **12/12** — the pin re-baseline is consistent with
  the committed blobs, which is the check that matters after touching a pinned
  artifact.
* `D2_PRODUCT_CONFIG_INERT` — `actuation=False`, localhost-only, boot OK.
* `B8_W10_BROWSER_PERF` reached **31/34** and refused to report a frame rate it
  could not attribute to an accelerated adapter: the renderer it measured is
  `ANGLE (Microsoft Basic Render Driver ... D3D11)`, class `SOFTWARE`.

## The blocker that is genuinely open

`C2_TOOLS_TESTS`: **209 passed / 21 failed / 12 errors / 6 skipped**, floor 248.
And `C1_CORE_E2E` carries **2 failures out of 869**:

```
FAILED test_audit_chain_atomicity.py::test_threaded_appends_keep_one_contiguous_chain
FAILED test_p2_integration_smoke.py::test_http_end_to_end_four_agents_distinct_no_secret_leak
    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb2 in position 8
```

The `tools/tests` failures cluster into recognisable families — artifact-currency
checks (`test_check_mode_reports_the_*_is_current` for the capability matrix and
the real-jev artifacts), test-isolation checks asserting environment variables are
unchanged, and continuation-infra checks. **None of them is adjudicated yet.**
`test_p2_integration_smoke` failing with a `UnicodeDecodeError` on a GBK host is
the one most likely to be a real defect and is the first thing to look at.

This is stated as unresolved on purpose. A 21-failure cluster that nobody has
read is not "environmental"; it is unexamined.

## The two legs that were this session's own fault, now closed

* `A1_FREEZE_MODEL` exited 2 on the first run — see above. Re-measured at 19/19.
* `A8_SECURITY_GATE` exited 1 **while printing `verdict=GO high=0 ... ran=9/9`**.
  That is the shape of a staleness complaint, not a finding: `config.py` moved
  under it, so the committed artifact no longer matched a fresh run. Re-emitted;
  `--check` now reports `[check] OK ... sha 3a7400c8`. Worth recording that the
  emitter takes `--out`, not `--emit`.

`E1_FROZEN_TREE` (`product_dirty=28 metadata_dirty=33`) was likewise a true
reading of a tree carrying this session's uncommitted work; it is clean now that
`cf9b2ea` and `3a382de` are in.

## Standing falsifiers

* **GREEN must not be signalled** — holds, vacuously and therefore not
  usefully: `pid_before=0`, `pid_after=0`. The assertion never had a live
  process to protect. It needs a run with GREEN actually up.
* **`foreign HEAD legs must not be aggregated`** — holds: the runner dropped 31
  leg records measured at another head before counting anything.

## To reach `NOT_RELEASE_READY=false`

1. **Start GREEN 4778 and hold it up for the whole run.** Four blockers and five
   of B7's 54 checks are downstream of `pid=0`. Bringing GREEN up is a host
   action, not a code change; the launch command is in `scripts/`.
2. **Adjudicate `C2_TOOLS_TESTS`'s 21 failures and 12 errors**, starting with the
   two `C1_CORE_E2E` failures — a GBK-host `UnicodeDecodeError` in an
   end-to-end smoke test is a plausible real defect and must not be filed under
   "environmental" without reading it.
3. **Re-run the full gate on a clean tree at the head that will actually be
   released.** This run's numbers belong to `cf9b2ea`, and two of its blockers
   were fixed at `3a382de` afterwards. A verdict is a statement about one commit.

No tag was created. `jcc-v1.3.4-product-local` was not moved. The rollback tag
`jcc-v1.3.5-relocation-rollback-before` points at `b680021`, the last head that
predates this session's product changes.