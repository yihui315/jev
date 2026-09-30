# JCC v1.3.5 — FINAL CLOSURE STATUS

> Written at HEAD `2c2807f`, branch `release/jcc-v1.3-final`.
> Every number below is a measurement taken in this session. Nothing here is
> carried over from an earlier report.

## The one-line verdict

**`PRODUCT_READY_AT_OLD_FREEZE=true` · `NEW_FREEZE_CANDIDATE=cb56658` ·
`PROJECT_COMPLETE=false` · `FINAL_RELEASE=false` ·
`PRIMARY_EXECUTION_BLOCKER=sandbox delete denial prevents fresh-WAL full gate`.**

The product at `cb56658` is unchanged and still carries the `9bfcc8d61e41`
result; the current head has **not** been through the full gate, and no old
PASS is inherited.

## What this round actually settled

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | True freeze / evidence heads | `PRODUCT_FREEZE_SHA=cb56658`, `EVIDENCE_HEAD=7c56128` (now `2c2807f`) | linear ancestry via `merge-base --is-ancestor` |
| 2 | Repository hygiene | **PASS** | 13 bounded batches, `remaining=0`, `unexpected_files=[]`, `git status` clean |
| 3/4 | Delete denial classification | `EXECUTION_ENVIRONMENT_DELETE_DENIAL`; product WAL **not** changed | `sandbox_execution_boundary.json` §1 |
| 5 | Host execution boundary | tasks are **not** test runners; `JCC-v1.3-VisualUI` absent | §4 |
| 6 | Gate instrument fixes | aggregate **PASS 8/8**; selftest **PASS 7/7** | §7 |
| 7 | Hardware renderer identity | **HARDWARE** — ANGLE/NVIDIA RTX 2080 Ti/D3D11 | `host_browser_renderer.json` |
| 15 | Durable runtime, **API half** | **VERIFIED** through the product's own ownership contract | §4 |
| 12 | SSE recovery, **real browser** | **PASS 11/11** | `evidence/sse_recovery_verification.json` |
| 14 | Real-browser E2E on bundled chromium | **executed** (SPA loaded, stream live, domain read) | same file |

## The sandbox boundary, characterised rather than assumed

Three separate facts, each measured:

1. **Deleting is refused by API, not by ACL.** `os.unlink` / `os.remove` →
   `WinError 5`; `cmd.exe /c del` on the *same path* → rc 0 and the file is
   gone. An ACL denial is therefore excluded. (The `cmd.exe del` hole is
   recorded as a boundary diagnostic and is **not** used as a release
   workaround.)
2. **Creating a SQLite database fails; writing to one does not.** Fresh
   `journal_mode=wal` and `=delete` both raise `disk I/O error` and leave an
   orphan `-journal`, while `=truncate`/`=persist` work — and a **copy of the
   product's own database accepts a write, commits in 3.23 s, and reads back
   durable**.
3. **Sandbox descendants die at the tool-call boundary.**
   `CREATE_BREAKAWAY_FROM_JOB` → `WinError 5`; a detached child wrote 3 ticks
   inside the call and 0 more in the following 30 s. This is why
   `-Action up`'s 90 s health probe failed: its detached child was already gone.

And the consequence that decides the browser gates:

4. **A sandboxed product API binds its port but cannot serve.** With the warmed
   databases in place, `GET /api/health` answered **200 after 25.05 s** and
   `GET /api/v1/domain/home` was **reset after 38.51 s**, against *immediate*
   200s from the host-owned instance. The same API, run by Task Scheduler,
   answers instantly.

## What could not be measured, and why

* **`B8_W10_BROWSER_PERF`** — driven through
  `.workbuddy/scratch/run_w10_perf_chromium.py`, which supplies a warmed
  database and the bundled chromium **without editing the harness**. It passed
  5 pre-boot checks and then failed `API_BOOT` (verdict **FAIL 5/6**, 193.4 s).
  It refused to print frame times rather than manufacture them.
  **No FPS number is claimed for this head.**
* **Real-browser SSE recovery (STEP 12) — initially reported as blocked, then
  actually measured and passed.** It turned out not to need a sandboxed API at
  all: the UI server does no database work, so the product's own
  `serve_visual.py` serves 4780 fine from here, and the SPA's shipped API base is
  the absolute `http://127.0.0.1:4779`, i.e. the **host-owned** API. With the
  bundled chromium driving the real bundle the full sequence was observed:
  `CONNECTED → outage → RECONNECTING → polling fallback → recovered → LIVE`,
  with **0 of 32 samples showing LIVE during the outage**, **2 poll calls in
  12 s** while down, and **0 poll calls in the 15 s after recovery** — the old
  5 s timer really stopped. See `evidence/sse_recovery_verification.json`, which
  also records that **three of the four probe versions failed because of the
  instrument, not the product**.
* **The fresh-WAL full gate** — unchanged.

## Still not re-certified against the current head

`real mission end-to-end` · `WorkBuddy auth state` · `hardware FPS` ·
`A8 security rebaseline` · `release manifest` · `Visual UI durable runtime`
(the `JCC-v1.3-VisualUI` task is absent — a documented
`PENDING_EXTERNAL_ACTION`) · `repo hygiene` and `git clean` (both hold now, but
must be re-asserted at the freeze that is actually released).

## Final falsifiers — current standing

None of these is a `FINAL_RELEASE=true` condition, because `FINAL_RELEASE` is
already false. Two are worth stating explicitly because they were at risk:

* `foreign HEAD legs must not be aggregated` — **holds**: the aggregate now
  blocks on any verdict that is not literally `PASS`, on `ERROR`/`SKIP`, and on
  a `PASS` that recorded no executed work.
* `GREEN 4778 must not be signalled` — **holds**: pid `2452` and HTTP 200 were
  asserted before and after every single step of both durable-runtime cycles.

## To reach `PROJECT_COMPLETE=true`

Either (a) an execution lane where a process may create a SQLite database and
serve requests — i.e. the host runtime, which currently offers no
general-purpose runner — or (b) a product change to the database contract,
which is explicitly **not** permitted to be made for the sandbox's benefit.
