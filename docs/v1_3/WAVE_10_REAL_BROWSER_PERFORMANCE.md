# WAVE 10 — Real-browser performance of the operational plane

**Gate:** `tools/v1_3_wave10_browser_perf.py` (release leg `B8_W10_BROWSER_PERF`)
**Evidence:** `.workbuddy/scratch/v1_3_wave10_browser_perf_<stamp>.json` (gitignored) — the
numbers live there and are **not** copied into this document (see §6).
**Status:** measured · the freeze does not restate its numbers

---

## 1. The claim this wave was written to stop making

`docs/v1_3/FINAL_PRODUCT_FREEZE.md` §7 carried this line:

> **Deliberately NOT claimed — a real-browser frame rate at 50/200/500/1000 objects.**
> The product has no stress route; timing a synthetic page would be a measurement of
> the harness, not of the product.

That reasoning was correct, and it is also why the wave exists: the honest answer to
"we cannot measure it" is to **build the instrument**, not to keep the line. Wave 10
removes the refusal by removing its premise — there is now a route that reaches a
real domain without a synthetic page.

---

## 2. Why a synthetic page would have been worthless (and what replaced it)

A performance harness is only as good as the stack it measures. Every layer below is
the **shipping** layer, not a stand-in:

| Layer | What the gate uses | What it refuses to use |
|-------|-------------------|------------------------|
| The domain | real `POST /api/v1/domain/projects` and `/missions` through the real API | a fixture array, a mocked store, a JS constant |
| The config | `config/jcc-v1.3.yaml`, the shipped file | a test `Config` with tuned numbers |
| The server | `create_app(engine, cfg)` from `packages/jcc_core` | a bare `http.server` |
| The bundle | `apps/command_center_3d/dist/`, and it asserts `dist/` postdates `src/` | a dev server, a rebuilt-on-the-fly bundle |
| The SPA server | `apps/command_center_3d/serve_visual.py` via uvicorn/ASGI — the product's own serving path | python's stdlib demo server (the thing W9 audited out) |
| The origin | `127.0.0.1:4780`, cross-origin to the API, i.e. **the real CORS topology** | same-origin proxying that hides the preflight |
| The browser | real Chrome via Playwright, at the real UI origin | jsdom, a headless "canvas shim", SSR snapshots |
| The renderer | the product's own render loop and its own WebGL draw calls | a hand-written `requestAnimationFrame` loop with no scene in it |

The gate also **clicks the product's own navigation** (`World`) and waits for the
operational canvas to exist. That wait is itself a fact: the empty-domain branch
renders an informational card, **not** a `<canvas>` — so a mounted canvas is
simultaneous proof that the plane mounted *and* that the domain it was given is not
empty. A perf gate that cannot tell "rendered the domain" from "rendered nothing"
would be measuring the wrong zeros.

---

## 3. What is measured, and how the instrument avoids lying

### 3.1 Draw calls — counted by wrapping the real WebGL entry points

An init script runs **before any bundle code**, wrapping
`WebGLRenderingContext.prototype` and `WebGL2RenderingContext.prototype`
`drawElements` / `drawArrays` / `drawElementsInstanced` / `drawArraysInstanced`.
This is deliberately a different altitude from the frame timer: `requestAnimationFrame`
fires whether or not the scene drew anything, so an FPS number alone cannot
distinguish "fast because it is efficient" from "fast because it is empty".

Draw calls are compared against the plane's documented arithmetic,
`draws ≈ 1 (gridHelper) + 2 × projects + 1 × missions`, and against the **domain's**
own totals read from `GET /api/v1/domain/counts`.

### 3.2 The domain comparison is the check that earns its keep

The gate compares *what the plane drew* with *what the domain holds*. That comparison
is not decoration: it is the exact instrument that produced the **Wave 9.3 defect**
(see `docs/v1_3/WAVE_9_3_NO_SILENT_TRUNCATION.md`). At 500 missions the plane drew
200, and every other gate in this release — including the ones that assert the
projection layer, the API shape and the scene contract — reported green, because
each of them only ever asserted shape, never scale. The plane's draw count was the
first thing in the project that could not pass while the product was lying about its
own size.

The wave 9.3 fix is therefore load-bearing for this gate to be meaningful, and this
gate is the regression that keeps the fix honest.

### 3.3 Frames — and why the rasterizer is named in the evidence

Frame times come from a `requestAnimationFrame` loop with the first frames discarded
as warm-up. The evidence records `median` / `p95` / `min` / `max`, the frame count and
the elapsed window.

**Every row carries the renderer string and a derived `renderer_class`.** On this
host, headless Chrome rasterizes through ANGLE/SwiftShader. That is an *environmental*
property, and it was established empirically rather than assumed — Chrome was launched
four ways:

| Launch | Reported renderer |
|--------|-------------------|
| default | `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)` |
| `--enable-gpu` | same |
| `--enable-gpu --use-angle=d3d11` | same |
| `--enable-gpu --use-angle=gl --ignore-gpu-blocklist` | same |

All four fall back to the CPU rasterizer. The frame times are therefore a **software
floor**, and the gate refuses to print an FPS without the renderer beside it. Headless
is used because it is the only reproducible browser mode available to an unattended
run — not because it flatters the numbers.

---

## 4. The falsifiers this gate is built around

| Check | What it rejects |
|-------|-----------------|
| `PRODUCT_CONFIG_IS_THE_SHIPPED_ONE` | measuring a test config with different limits or a mock provider |
| `BUNDLE_IS_NEWER_THAN_THE_SOURCE_IT_CONTAINS` | timing a **stale** `dist/` — the mistake that would silently invalidate the whole run; `dist/index.html` must be at least as new as the newest `src/**/*.{ts,tsx,css,html}` |
| `UI_ORIGIN_IS_IN_THE_CORS_ALLOWLIST` | a harness that "works" only because CORS is disabled |
| `PRECONDITION_PRODUCT_PORTS_FREE` | colliding with a process that is already serving 4779/4780, which would make the measurement someone else's |
| `UI_SERVED_BY_THE_SHIPPED_ASGI_SERVER` | a stand-in server for the SPA |
| `CORS_PREFLIGHT_FOR_THE_UI_ORIGIN` | a browser run whose cross-origin calls never actually happened |
| `WEBGL_IS_LIVE_IN_REAL_CHROME` | a run where WebGL silently degraded to no context — `renderer_class` must not be `UNKNOWN` |
| `SEEDED_GROUND_TRUTH_*` | seeding that only pretended to grow the domain (read back from `counts`, not from the seed loop's own counter) |
| `WORLD_MOUNTED_*` | measuring the empty-domain branch |
| `FRAME_TIME_FINITE_*` | `NaN`/`null` frame times passing as numbers |
| `DRAWS_SCALE_WITH_THE_DOMAIN` | a plane that mounted but did not draw the added work — requires monotonic draw growth across all four counts **and** ≥ 90 % visibility of the newly added missions |
| `VIEW_SWEEP_NO_UNCAUGHT_ERRORS` | a real-Chrome E2E sweep across the product's own navigation labels with any `error` / `unhandledrejection` |
| `TEMP_ROOT_IS_USABLE` | a `$TEMP` that Playwright cannot write its profile under |
| `PORTS_RELEASED_AFTER_TEARDOWN` | a gate that leaves a product listener behind and poisons the next run |

---

## 5. A defect in the gate itself, found by running it

The first full run reported **9/12 FAIL**, and the failure was **mine, not the
product's**: the invoking command had pointed `TEMP` / `TMP` at a directory that did
not exist, so `BrowserType.launch` died with
`ENOENT ... mkdtemp 'playwright-artifacts-XXXXXX'`.

Two things are worth recording about it:

1. **The gate failed loudly instead of reporting zeros.** It could easily have printed
   "0 frames measured, 0 failures" and gone green — which is precisely the
   silent-success class this release keeps deleting. The floor in the runner
   (`B8_W10_BROWSER_PERF`, 26 checks) exists so that a leg which measures nothing
   cannot be signed.
2. **The fix went into the instrument, not into the transcript.** `TEMP_ROOT_IS_USABLE`
   was added: the gate now creates the temp root if it is missing and fails closed if
   it cannot, and it records the scratch path it actually used. The runner's floor was
   raised from 25 to 26 to match.

### 5a. A second instrument defect: forty minutes of silence is indistinguishable from a hang

The rehearsal run seeded 1000 missions into the real domain through the real API.
Measured throughput: **~1.4 s per mission**, ~23 minutes of seeding, and the gate
printed **nothing** between `API_BOOT` and the first `SEEDED_GROUND_TRUTH_50`.
Its evidence file was not written until the very end. So for forty minutes a gate
that was working correctly looked exactly like a gate that had wedged — and the
only way to tell was to poll the product's own `/api/v1/domain/counts` endpoint
from outside and watch the domain grow.

The cause was plain and is worth stating because it affects every long unattended
run in this repository: **Python block-buffers stdout when it is redirected to a
file** (8 KiB), so the log is not a transcript, it is a flush schedule.

Three changes, none of which touch the checks:

* `sys.stdout.reconfigure(line_buffering=True)` in `main()`, so the log becomes a
  live transcript.
* A seed heartbeat every 100 missions with elapsed time and seconds-per-mission,
  so a stall is distinguishable from work in progress.
* The seed cost is recorded in the evidence as `seed.per_node`,
  `seed.total_seconds` and `seed.seconds_per_mission` — the number that explains
  why this gate takes minutes rather than seconds (§8), so a reader can tell
  "slow because the domain is large" from "slow because the harness is stuck".

The gate also gained `--nodes`, which exists so an operator can smoke-test the
*harness* in minutes instead of an hour. A run with a shortened node list is
explicitly **not** a release leg: it cannot meet its check floor, and it fails
loudly rather than quietly reporting a smaller total.

### 5b. The rehearsal is not the evidence

The 26/26 run was a **rehearsal**: it established that the harness works, that the
plane mounts and draws at all four counts, and that the product's real cross-origin
topology is exercised end to end. It is not the signed number. The authoritative
`B8_W10_BROWSER_PERF` evidence is produced by `tools/final_gate_run_v1_3.py --full`
at the freeze commit, because a leg signed from a different invocation than the one
the gate ran is the exact thing the signer's `product_freeze_sha` check exists to
prevent.

---

## 6. Deliberately NOT claimed

Written down so their absence is visible rather than implied:

* **A hardware frame rate, and any target FPS.** The rasterizer is software (§3.3).
  There is no measured baseline on this machine for what "fast enough" is, and
  inventing a threshold would be the unfalsifiable green this release exists to
  remove. The gate asserts falsifiable things — the domain was rendered, the work
  scaled, no frame was catastrophic — and refuses the rest.
* **A GPU-accelerated rendering path.** Four launch configurations were tried; all
  four rasterize in software. This is a property of the environment.
* **Behaviour on a discrete GPU, or on a machine other than this host.**
* **Frame-rate numbers restated in this document.** The numbers are in the gate's
  evidence, under `measurement.rows`, with the renderer string beside them. Copying
  them here would create a second place for them to disagree with the first — a
  mistake this project has already shipped once.
* **Paging beyond `DOMAIN_LIST_LIMIT`.** Wave 9.3 made the product *say* a view is
  bounded; it did not make the product page. Wave 10 measures up to the ceiling and
  does not claim behaviour past it.

---

## 8. A measured finding the seed produced: host IO, multiplied by the connection count

Building an instrument that writes 1000 real objects into the real product is
itself a measurement, and it produced a finding that **corrects the previous
wave's conclusion** — twice, because the first correction was itself over-read
from a contended host.

**The observation.** Seeding ran at ~1.4 s per mission on the first full run,
against the 186 ms that the same call had profiled at earlier in the same session
(`docs/v1_3/WAVE_9_3_NO_SILENT_TRUNCATION.md` §3). Both were measurements of one
call, so one of them had to be wrong.

The instrument for everything below is published — `tools/v1_3_domain_io_profile.py`
— and it reproduces on any host. It carries one falsifiable claim, which it can
fail.

**What it is not, part 1: it is not O(n) in the data.** A control containing no
product code at all (raw `sqlite3`, one table, four file sizes from 12 KB to
1.68 MB, three journal modes, bulk-filled so the row counts are reached cheaply)
is **flat in file size**, measured twice, once under load and once quiet:

| `journal_mode` | 12 KB | 36 KB | 94 KB | 422 KB | 1.68 MB |
|---|---|---|---|---|---|
| `delete` (rollback journal) | 106 / 39 | 227 / 37 | — / 41 | 198 / 42 | 299 / 39 ms |
| `memory` | 216 / 39 | 169 / 41 | — / 41 | 143 / 41 | 159 / 39 ms |
| `wal` | 0.9 / 3.8 | 0.9 / 4.0 | — / 4.0 | 1.0 / 4.1 | **1.1 / 4.1 ms** |

(*loaded* / *quiet*, median.) Across a **137× range of file sizes** the commit
cost moves by less than a factor of two, in every journal mode. So "the write
path is O(n)" is refuted. The control also refutes "commits are the problem": a
WAL commit is 1–4 ms.

**What it is not, part 2: it is not the HTTP layer.** A probe booting the real
app on the real product port with the real config, then timing the *same* call
both ways in one process: **HTTP POST 178 ms, direct `DomainStore.create_mission`
178 ms** — identical. The API adds nothing measurable; the store is the cost.

**What it is.** On a quiet host, against a 1000-row store database, median of 9:

| Series | ms |
|---|---|
| `connect + close` | **1.9** |
| `connect + foreign_keys=ON + close` | 2.4 |
| `connect + synchronous=NORMAL + close` | 5.9 |
| `connect + journal_mode=WAL + close` | 6.5 |
| full `DomainStore._conn()` (all three pragmas) + close | 6.9 |
| the same, plus one `SELECT` | 7.3 |
| whole `get_project` | 6.8 |
| whole `list_projects` (one row) | 88.5 |
| whole `domain_counts` (4 × COUNT) | 91.7 |
| whole `needs_attention` | 97.2 |
| whole `list_missions(1000)` | 104.7 |
| whole `create_mission` (**two** `_conn()` pairs) | **194.1** |

`DomainStore._conn()` issues `PRAGMA journal_mode=WAL`, `PRAGMA
synchronous=NORMAL` and `PRAGMA foreign_keys=ON` **on every connection**, and the
store opens a fresh connection per operation — twice per `create_mission` (once
inside `get_project`, once for the insert).

**The accounting, including what is NOT explained.** The three pragmas cost ~5 ms
over a bare `connect + close`, and a raw WAL commit is ~4 ms, so connection setup
plus the existence check accounts for roughly **14 ms of the 194 ms**. **The
remaining ~180 ms is not attributed by this instrument**, and the same is true of
the ~90 ms floor on the list/count operations, which do no more work than a
single-row read. That residue is recorded as **open** rather than explained by a
guess. Naming it is the point: a number whose cause is asserted without
measurement is the defect this whole release is about.

`Connection.close()` — the figure Wave 9.3 §3 blamed — measures **0.06–0.13 ms**,
on every journal mode and every file size. It is the cheapest thing in the
picture.

**The part that matters — and the part that was over-read.** The cost is
dominated by the host's file-syncing latency, which is **not stable**. Across
runs on this one machine within two hours, the same operations measured:

| Series | quiet | loaded |
|---|---|---|
| `connect + close` | 1.6–27 ms | 27 ms |
| `connect + journal_mode=WAL + close` | 4.8–6.5 ms | 411 ms |
| whole `get_project` | 4.9–6.8 ms | 438 ms |
| the gate's own seed | 0.18–0.9 s/mission | 1.4 s/mission |

A **26× swing in the same measurement** is why the first correction in this
section was itself wrong: the 400–690 ms figures were taken while this session
was running probes of its own against the same disk, and they are a property of
that contention, not of the product. The published tool prints its spread
(`min`/`p90`/`max`) next to every median for exactly this reason.

What survives all conditions is: **(a)** the commit cost does not track data size;
**(b)** the HTTP layer is not the cost; **(c)** the store pays a connection — with
three PRAGMAs — per operation, **twice per mission creation**, and so is exposed
to whatever the host charges for a file-syncing operation on every single call.
That structural fact is the product's own contribution to the number, and it is
the thing a future wave would fix.

**This supersedes Wave 9.3 §3**, which attributed the cost to `Connection.close()`
(82 ms × 2) and reverted a pragma change on the strength of a measurement taken
against a *freshly-created* database rather than the state the product runs in.
The amendment and the unchanged original numbers are in
`docs/v1_3/WAVE_9_3_NO_SILENT_TRUNCATION.md` §3a.

**Why this wave did not fix it.** Connection reuse — one connection (or one per
thread) per store, which lets the pragmas run once per process instead of once per
operation — is the fix, and it also removes a redundant read from every mission
creation. It is a structural change to the storage layer:
`StateStore.connection()` is a **public** contextmanager whose contract is "a
FRESH connection, always closed", and many tests open the same database
concurrently. That needs its own wave with its own falsifiers, not a late-night
edit to a tree that is about to be frozen. Recorded as the top **P4** item.

**What is claimed instead.** The measurements, the two refutations, the
structural exposure, and the residue that is **not** explained — named as open
rather than filled in with a plausible story. The gate's runtime is attributed to
the same finding, and the gate now reports its own seeding throughput in its
evidence (`seed.seconds_per_mission`) rather than leaving a reader to guess.

---

## 9. What this wave changes about the freeze

`docs/v1_3/FINAL_PRODUCT_FREEZE.md` is re-introduced by the commit that carries this
wave, which makes that commit the new `PRODUCT_FREEZE_SHA`. This is not a
re-labelling of the W9 freeze:

* the W9 anchor (`c3060f7`, evidence `e836636`, tag `jcc-v1.3-product-local`) is
  **not moved, not deleted, and does not claim to cover** the code added after it;
* the W9.1 → W10 commits are additive product work on the finalization branch, and
  the freeze document says so in §4;
* the anchor is checked, not asserted: `E5_FREEZE_ANCHOR_RESOLVES` requires
  `git log -1 --format=%H -- docs/v1_3/FINAL_PRODUCT_FREEZE.md` to equal `HEAD`, and
  `tools/emit_v1_3_legs.py` refuses to sign a leg unless that same resolution equals
  the commit the gate measured.
