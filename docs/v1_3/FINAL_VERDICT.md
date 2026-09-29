# JCC FINAL VERDICT

**JCC v1.3 PRODUCT EDITION — LOCAL RELEASE VERDICT**
Measured 2026-09-30 07:17 UTC+8 · branch `release/jcc-v1.3-final` · HEAD `9bfcc8d61e41`

| Verdict field | Value | Why |
|---|---|---|
| `PRODUCT_READY` | **true** | 30/30 release legs PASS, `blockers=0`, `project_complete=true`. The product runs, serves and measures. |
| `HOST_ACTIVATION_PENDING` | **true** | 3 actions remain that only a human on this Windows host can perform (Task Scheduler + WorkBuddy CLI login). |
| `PROJECT_COMPLETE` | **false** | Not because anything is broken — because two of the three remaining actions need Windows privilege and the third needs an interactive login, and none may be simulated. |

**One-line conclusion:** the product is finished and measured; what is unfinished is the host, and it is unfinished honestly.

**Gate one-liner:** `30/30 legs PASS · blockers=0 · release_readiness=RELEASE_READY_PENDING_HOST_ACTIVATION · final_release=false · actuation_enabled=false`

---

## 1. Anchors — what this verdict is attached to

| Item | Value |
|---|---|
| `PRODUCT_FREEZE_SHA` | `9bfcc8d61e41fb364cd45e4cb3c9eda46db780bb` ("v1.3.4-wave10: the operational plane is measured in a real browser") |
| Freeze definition | *the commit that introduced `docs/v1_3/FINAL_PRODUCT_FREEZE.md`* — resolved, not hard-coded (leg `E5`) |
| `EVIDENCE_HEAD` | this commit (it carries the measured result, the 30 legs, the 11 signed legs and the manifest) |
| Final local tag | `jcc-v1.3.4-product-local` → this commit |
| Branch | `release/jcc-v1.3-final` |
| Rollback tags | `jcc-v1.3-w10-rollback-before → 13d3849`, `jcc-v1.3-w9.3-rollback-before → 05d4907` |

**Predecessor anchors were not touched.** The earlier v1.3 release keeps `PRODUCT_FREEZE_SHA=c3060f7`, `EVIDENCE_HEAD=e836636`, tag `jcc-v1.3-product-local`. Nothing was forced, moved or deleted, and this release does **not** pretend the old freeze covers code written after it.

---

## 2. Tests — 30 legs, every one of them measured at this HEAD

| Group | Legs | Result |
|---|---|---|
| A — product contract | A1 19/19 · A2 47/47 · A3 23/23 · A4 26/26 · A5 26/26 · A6 43/43 · A7 55/55 · A8 `GO high=0 medium=0 low=0 ran=9/9` | 8/8 PASS |
| B — Wave gates | B1 30/30 · B2 30/30 · B3 19/19 · B4 17/17 · B5 6/6 · B6 20/20 · B7 54/54 · **B8 26/26** | 8/8 PASS |
| C — regression | C1 **865 passed / 0 failed / 0 errors / 0 skipped** (floor 786) · C2 **248 passed** (floor 248) · C3a `tsc --noEmit` exit 0 · C3b vitest 94 passed · C3c build exit 0 | 5/5 PASS |
| D — live invariants | D1 GREEN pid 7028 http 200 untouched · D2 `actuation=False localhost_only=True` · D3 UI audit positives=4 banned=0 | 3/3 PASS |
| E — release hygiene | E1 product_dirty=0 · E2 33 gate inputs tracked, 0 missing · E3 **12/12 A8 pins match HEAD blobs** · E4 no listeners left behind · E5 freeze anchor resolves | 5/5 PASS |
| F — the gate's own falsifiers | F1 7/7 falsifiers behaved | 1/1 PASS |

Rule applied: `passed>0 AND failed==0 AND errors==0 AND skipped==0`. **A skipped test is a failure here**; nothing was marked "known-pending" to make a number green.

---

## 3. Real Chrome performance — measured, with the rasterizer named

Leg `B8_W10_BROWSER_PERF`: real domain seeded through real POSTs, real bundle served by the product's **own** ASGI server (`serve_visual.py` under uvicorn), real cross-origin topology (SPA 4780 → API 4779, real CORS preflight), driven in **real Chrome**.

| Objects in domain | median frame | p95 frame | fps | draw calls/frame | expected draws | triangles/frame |
|---|---|---|---|---|---|---|
| 50 | 33.3 ms | 33.4 ms | 36.2 | 71.4 | 71 | 67 091 |
| 200 | 66.7 ms | 83.4 ms | 14.1 | 222.7 | 221 | 234 121 |
| 500 | 166.6 ms | 166.8 ms | 6.2 | 530.1 | 521 | 573 406 |
| 1000 | 300.1 ms | 333.3 ms | 3.2 | 1 053.9 | 1 021 | 1 151 505 |

- **The work scales with the domain**: 1.034 draws per added mission, monotonic across all four sizes (slope check requires ≥0.9 visibility fraction).
- **14 product views opened in the same browser session, 0 uncaught errors, 0 console errors, `page_errors=[]`.**
- **The rasterizer is named, so the number cannot be quoted without it**: `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)` → `renderer_class=SOFTWARE`. Launched four ways (default, `--enable-gpu`, `--use-angle=d3d11`, `--use-angle=gl`); **all four are CPU**. These are a software rasterizer's floor, **not** hardware FPS, and no target FPS is claimed.
- Seeding 1000 missions took 206.5 s (0.206 s/mission) — the measurement is of real persisted work, not of a synthetic scene.

---

## 4. The one failure tonight — and what it actually was

`B5_W6_WORLD_GATE` failed its first run at 07:07:48 with `TSC_CLEAN rc=3221225477` (0xC0000005, **access violation**, empty output). It was **not** a type error:

| Evidence | Value |
|---|---|
| Same command, 2 minutes earlier, leg `C3a_TYPECHECK` | `exit=0` |
| Same gate run, immediately after, `WORLD_TESTS_PASS` | vitest 33/33 passed |
| `tsc --noEmit` re-run on a quiet host, 3× | `rc=0`, 5.3 s, no output, 3/3 |
| Host history | 3 unattended hard reboots tonight (Kernel-Power 41 at 02:24:28, 02:25:53, 06:13:10); the host has a long, open memory-instability investigation |

**Decision:** it was a native crash of `tsc` on a destabilised host, not a product defect. The leg was re-run (`--only B5_W6_WORLD_GATE`) and passed **6/6**. This is recorded rather than hidden: the re-run is visible in the result file as `legs_run_this_invocation=[B5_W6_WORLD_GATE, F1, E4]` with the other **27 legs carried from the same HEAD** — a repair run is not a fresh verdict, it is the same verdict re-measured where it had failed. The instrument failed **closed** (it recorded `rc=3221225477` and an empty tail; it did not fold the crash into a pass) — that is why this is visible at all.

---

## 5. WorkBuddy — live classification (read from the running product, not asserted)

`GET /api/connections` on a live v1.3 API booted from `config/jcc-v1.3.yaml`:

| Agent | telemetry | adapter | AEP | control capability | connection state |
|---|---|---|---|---|---|
| **WorkBuddy** | CONNECTED | CONNECTED | OK | **VIEW_ONLY** | UNKNOWN |
| Claude | CONNECTED | CONNECTED | OK | VIEW_ONLY | UNKNOWN |
| Codex | **DEGRADED** | DEGRADED | OK | VIEW_ONLY | UNKNOWN |
| Hermes | CONNECTED | CONNECTED | OK | VIEW_ONLY | UNKNOWN |
| Jev provider | UNKNOWN | UNKNOWN | **UNVERIFIED** | VIEW_ONLY | UNKNOWN |

Read it the way the product intends it: `connection_state=UNKNOWN` means *no live session has been observed* — it is **never** rendered as CONNECTED. `control_capability=VIEW_ONLY` is the honest ceiling while `actuation_enabled=false`: leg `B6` asserts a probe can **never** return `CONTROL_VERIFIED`, that `UNKNOWN` never renders as CONNECTED, and that a bare `CONTROL_VERIFIED` with no `evidence_ref` is refused (422).

`GET /api/onboarding/state` → **0 of 7 steps complete, `next_step=1`**. `GET /api/v1/domain/counts` → **projects 0, missions 0, archived 0, deliverables 0**. That is the true initial state: tonight's measurements ran against isolated temp stores by design, so the live domain never held data. No data loss is implied and none is claimed.

---

## 6. GREEN 4778 — before and after

| | pid | http |
|---|---|---|
| gate start | 7028 | 200 |
| gate end | **7028** | **200** |

`green_untouched=true`. **Said plainly:** the host rebooted three times tonight, and that changed GREEN's pid from the 7588 recorded earlier to **7028**. The leg asserts *this run did not change it*, and it did not; the pid change came from the host, and it is written here rather than smoothed over. 4778 was never probed, never stopped, never re-bound.

---

## 7. Security, reliability, and what is proven vs merely believed

**Security** — `A8_SECURITY_GATE`: `GO`, `high=0 medium=0 low=0`, 9 checks ran (a gate that runs zero checks fails). `E3`: **12/12 A8 hash pins match the HEAD blobs**, so no pinned product file moved silently. `D3`: the UI serving path audits clean (`positives=4 banned=0`) and the auditor is itself falsifiable — an empty file fails it for *missing positive evidence*. `B6` 20/20 (above). `actuation_enabled=false`, `localhost_only=true`.

**Reliability** — `B7_W8_RUNTIME_GATE` 54/54: the product refuses a non-loopback bind (exit 3, and nothing left listening), the three ports are disjoint (4778 GREEN / 4779 API / 4780 UI), `-Action up -DryRun` changes nothing, and the restart transcript covers the controller.

**Proven vs not proven** (this distinction is the point):
- **Proven:** the controller restart path, loopback-only serving, ASGI (never the stdlib demo server), port disjointness, clean teardown (`E4`: 4779/4780/4781/4782 all free afterwards).
- **NOT proven, and not claimed:** that the product outlives its *launcher*. A restart transcript about a controller is not proof that a service survives parent exit; only the host Task Scheduler registration can establish that, and it is pending.

---

## 8. Release manifest

`artifacts/release/v1.3_product/V1_3_RELEASE_MANIFEST.json` — **CHECK CLEAN**, 20 certified paths, sidecar `sha256 = 436bf816794b4e0c539a8e43b2a9d0a83f09f12e17148f2ab1930a59025baf38`.

The certification is directional and re-runnable: it verifies that every product artifact's bytes **at the freeze commit** match, that the freeze is an ancestor of the evidence head, and that **nothing product-shaped changed** in the freeze→evidence window. `git checkout jcc-v1.3.4-product-local && tools/emit_v1_3_release_manifest.py --check` is CLEAN by construction — the manifest never carries its own commit's sha.

Signed legs: 11 dimensions, `LEGS CHECK CLEAN` (every evidence digest still matches). Two read `GO_WITH_PENDING` (RUNTIME, BROWSER_PERF) — a signature that omitted the host work would be a signature that lies.

---

## 9. Remaining blockers — exactly three, all of them host work

1. **Unregister the two stray Scheduled Tasks** `JCC-v1.3-API` and `JCC-v1.3-PROBE`. `schtasks.exe` is blacklisted in the agent sandbox and Windows' own unregister path goes through the same blind CIM provider. Tonight `JCC-v1.3-API` had re-created a 4779 listener after the 06:13 reboot; it was identified (session-0 `SYSTEM` python, command line identical to the registered task, health read-back `open_get_endpoints=28`) and force-stopped, with full evidence in `.workbuddy/scratch/overnight/port_4779_cleanup_evidence.md`. It will come back on the next boot until this is done.
2. **Register the durable runtime:** `scripts\jcc-v1.3-register-tasks.ps1 -Apply` from an **elevated** PowerShell, then `scripts\jcc-v1.3.ps1 -Action mark` to adopt them. This is the only thing that can make "the service outlives its launcher" claimable.
3. **Authenticate the WorkBuddy CLI** for the account the API runs under. Until then a launcher-spawned non-interactive process reads `AUTH_REQUIRED`, the mission is deliberately **not** walked to RUNNING, and the capability reads `HOST_ACTIVATION_PENDING`.

None of the three can be faked from here, and none was faked: doing so would have turned `PROJECT_COMPLETE` into a lie about the host, not a fact about the product.

---

## 10. Morning — how to actually use it

```powershell
cd F:\jev
.\scripts\jcc-v1.3.ps1 -Action up -DryRun    # preflight, changes nothing
.\scripts\jcc-v1.3.ps1 -Action up           # API 127.0.0.1:4779 + UI 127.0.0.1:4780
```

Then open **http://127.0.0.1:4780**. GREEN on 4778 is left alone by both commands. Note: `-Action status` will report `DEGRADED` with an ownership warning until blocker 1 and 2 are done — the marker is deliberately **not** fabricated, so the warning is telling the truth.

---

## 11. Deliberately NOT claimed

1. **No hardware frame rate and no target FPS.** The curve is measured (§3) on a **software** rasterizer; the renderer string and class travel with every number.
2. **No single-view rendering beyond `DOMAIN_LIST_LIMIT` (1000).** The product *says* the view is partial (Wave 9.3) and shows counts; it does not page.
3. **No proof that the runtime outlives its launcher** — pending blocker 2.
4. **No DPAPI-at-rest for the control token** — it stays an environment variable, as shipped.
5. **No claim that domain writes are fast.** Measured and *not* fixed: `DomainStore._conn()` re-issues three PRAGMAs per connection and opens a fresh connection per operation (twice per mission creation) → `create_mission` 194 ms on a quiet host, up to ~1.4 s under load, with ~180 ms of that **unexplained and recorded as unexplained**. Connection reuse is a storage-layer contract change and is the first P4 item, not a tonight change.

---

## 12. Evidence index

| What | Path |
|---|---|
| Gate result (+ sha256 sidecar) | `artifacts/release/v1.3_product/FINAL_GATE_V1_3_RESULT.json` |
| 30 legs, one file each | `artifacts/release/v1.3_product/legs/V1_3_LEG_*.json` |
| 11 signed legs + signature bundle | `artifacts/release/v1.3_product/legs/V1_3_SIGNED_LEG_*.json`, `V1_3_LEG_SIGNATURES.json` |
| Release manifest (+ sidecar) | `artifacts/release/v1.3_product/V1_3_RELEASE_MANIFEST.json` |
| Freeze document (the anchor itself) | `docs/v1_3/FINAL_PRODUCT_FREEZE.md` |
| Wave 10 browser performance | `docs/v1_3/WAVE_10_REAL_BROWSER_PERFORMANCE.md` |
| Wave 9.3 no silent truncation | `docs/v1_3/WAVE_9_3_NO_SILENT_TRUNCATION.md` |
| Gate runner / instruments | `tools/final_gate_run_v1_3.py`, `tools/v1_3_wave10_browser_perf.py`, `tools/v1_3_domain_io_profile.py` |

**Reproduce the verdict:** `F:\jev\.venv\Scripts\python.exe tools/final_gate_run_v1_3.py --full` at the freeze commit (needs 4779/4780 free: `.\scripts\jcc-v1.3.ps1 -Action down` first if something is holding them).
