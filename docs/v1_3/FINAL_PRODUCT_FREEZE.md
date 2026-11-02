# JCC v1.3 Product Edition — Final Product Freeze

**Status:** FROZEN (additive product surface; protected GREEN 4778 untouched)
**Branch:** `feature/jcc-v1.2-visual-command-center` (v1.1 → v1.2 → v1.3 evolve on one line)
**Release tag:** `jcc-v1.3-product-local` (local only — no remote, no push)

## 1. What the freeze anchor IS (and why it is not a sha in this file)

**PRODUCT_FREEZE_SHA = the commit that introduced this document.**

Resolve it with:

```
git log -1 --format=%H -- docs/v1_3/FINAL_PRODUCT_FREEZE.md
```

This is not evasion, it is the only self-consistent definition. A document cannot
contain the sha of the commit that carries it: writing the value changes the
commit, which changes the value. Every release that hard-codes it pays for the
mistake either with a contradictory document or with a follow-up "refresh the
anchor" commit — and that refresh is precisely the ritual the v1.3 Wave 0 model
(`docs/v1_3/WAVE_0_RELEASE_FOUNDATION.md`) exists to retire.

The definition is **checkable**, not merely asserted. Two independent legs
enforce it against the live repository:

| Where | What it asserts |
|-------|-----------------|
| `tools/final_gate_run_v1_3.py --only E5_FREEZE_ANCHOR_RESOLVES` | `git log -1 --format=%H -- <this file>` equals `HEAD`, and the file is ≥ 2000 bytes so an empty file cannot satisfy the anchor |
| `tools/emit_v1_3_legs.py` | refuses to sign any leg unless that same resolution equals the commit the final gate measured |

Because this file is introduced by the freeze commit and never edited afterwards,
the value the anchor resolves to **cannot go stale**.

## 2. Scope of the freeze

| Surface | Certified at the freeze |
|---------|------------------------|
| `packages/jcc_core` | core engine, state store, server, domain, capability probe |
| `apps/command_center_3d` | React 19 + TS + Vite product SPA, ASGI serving path |
| `config` | `jcc.yaml` (protected GREEN) and `jcc-v1.3.yaml` (product) |
| `scripts` | the operator control plane: `jcc-v1.3.ps1` (up/down/restart/status/doctor/repair/mark), `jcc-v1.3-register-tasks.ps1`, `lib/JccCommon.ps1` |
| `tools/*gate*.py`, `tools/jcc_release_manifest.py`, `tools/emit_v1_3_*`, `tools/v1_3_wave10_browser_perf.py`, `tools/v1_3_domain_io_profile.py` | the gates and the published measurements — an untracked gate is an unreproducible verdict, and a quoted number whose instrument is not in the repository is not evidence |
| `tests/e2e` | the v1.3 end-to-end contract |
| `docs/v1_3/FINAL_PRODUCT_FREEZE.md` | this document |

`scripts/` is newly inside the certification. The v1.2 manifest certified a
product whose runtime wrapper sat outside the certified set; v1.3 closes that.

## 3. Hard invariants — measured, and re-measured at the freeze

| Invariant | State | How it is measured |
|-----------|-------|--------------------|
| Protected GREEN 4778 (v1.0.1, pid 7588) never stopped, restarted, signalled or written | **ENFORCED** | gate leg `D1_GREEN_PROTECTION` reads pid + HTTP 200 + `/api/health` before and after every leg; the aggregate blocks if the pid or the reading changed |
| Actuation stays OFF | **ENFORCED** | gate leg `D2_PRODUCT_CONFIG_INERT` **boots** `config/jcc-v1.3.yaml` on a fresh temp DB and reads `/api/health`: `actuation_enabled=false`, `localhost_only=true` |
| Loopback only | **ENFORCED** | same live health reading; plus the serving path refuses a non-loopback `--host` before binding |
| The SPA is served by a real ASGI server, not python's stdlib demo server | **ENFORCED** | `tools/v1_3_ui_runtime_audit.py`, AST-based, with positive evidence required (`uvicorn_imported` / `uvicorn_run_called` / `starlette_app_referenced` / `serves_files`) — and the auditor itself is proved falsifiable by feeding it a file that really imports `http.server` |
| No paid provider call / no external publish / no git push | **YES** | `jev.provider=mock`; no remote configured for this release; the tag is local |
| UNKNOWN is never rendered as CONNECTED / SAFE / VERIFIED | **ENFORCED** | W7 rule: a probe measures capability, the caller can never state the outcome, and a v1.3 probe never returns `CONTROL_VERIFIED` with actuation off |
| A bounded list is never presented as a complete one | **ENFORCED** | W9.3: `GET /api/v1/domain/counts` reports uncapped totals; the client classifies every list as `complete`/`truncated`/`at-ceiling`/`unknown` and says so. `unknown` and `at-ceiling` are never rendered as complete — pinned by `apps/…/src/app/truncation.test.ts` and, over HTTP, by `tests/e2e/test_domain_counts_v1_3.py::test_a_truncated_list_is_DETECTABLE` |
| The operational plane renders the domain it is given | **MEASURED** | W10 gate leg `B8_W10_BROWSER_PERF` counts the plane's real WebGL draw calls in real Chrome and asserts they scale with the domain (measured: 1.015 draw calls per added mission, monotonic across 50→200→500→1000) |
| Ports | 4778 GREEN (frozen) · 4779 product API · 4780 product UI (uvicorn ASGI) · 4781 dev · 4782 smoke-only | `config/jcc-v1.3.yaml`; gate leg `E4_NO_LISTENERS_LEFT_BEHIND` proves nothing is left holding them |

## 4. The v1.3 surface this freeze covers

| Wave | Deliverable | Commit |
|------|-------------|--------|
| W0 | non-self-referential release manifest model | `8f0447a` |
| W1 | Project / Mission domain + state machine + API | `1be13ca` |
| W2 | product home, mission studio, design system | `7cec039` |
| W3 | first REAL WorkBuddy mission (launch bridge) | `b83a2ee` |
| W4 | needs-attention inbox, preview-first control | `6a46d1f` |
| W5 | deliverables, server-side view, human verdict | `948f43c` |
| W6 | operational 3D world (a map of the work, not of the engine) | `b2782e9` |
| W7 | agent connection verification (measured, not asserted) | `ff21bb7` |
| W8 | Windows durable product runtime (single fail-closed control plane) | `ff3fa69` |
| W9 | final product gates | `c3060f7` — product freeze · `e836636` — measured gate result, signed legs and manifest |
| W9.1 | the host launcher starts work, and refuses to lie about it | `8e417b8` |
| W9.2 | the mission launch control plane — Start Mission starts work | `05d4907` |
| W9.3 | no silent truncation (a bounded list must say it is bounded) | `13d3849` |
| W10 | real-browser plane performance · **this freeze** | *this commit* — product freeze |

**W9.1 → W10 are additive product work on a *finalization* branch, not a rewrite
of the W9 freeze.** They change product code, so they carry their own freeze
anchor: this document was re-introduced by this commit, and the value E5 resolves
is now THIS commit. The W9 anchor (`c3060f7` / evidence `e836636`) is not moved,
not deleted, and does not claim to cover the code added after it — the whole
point of re-anchoring rather than re-labelling.

## 5. Release readiness at the freeze

The authoritative, measured statement is
`artifacts/release/v1.3_product/FINAL_GATE_V1_3_RESULT.json`, produced by
`tools/final_gate_run_v1_3.py --full` at this commit. The freeze does **not**
restate its numbers: a number copied into a second file is a number that can
disagree with the first, and this project has already shipped one document whose
quoted digest belonged to a different artefact.

Readiness is one of two values:

* `RELEASE_READY_PENDING_HOST_ACTIVATION` — every gate leg passed; only HOST
  actions remain (section 6).
* `NOT_RELEASE_READY` — at least one gate leg failed, and the blocking leg ids
  are listed in the result under `blockers`.

`project_complete` is `true` only when `blockers` is empty. `final_release` is
deliberately stricter: it is also `false` while any HOST action in section 6 is
outstanding. The two are never conflated.

## 6. PENDING_EXTERNAL_ACTION — host actions, not product defects

These cannot be performed from the agent sandbox, and none of them is evidence
that the product is broken. They are recorded here so a reader of the tag knows
exactly what has and has not been done on a host.

1. **Remove two stray Scheduled Tasks.** `JCC-v1.3-API` and `JCC-v1.3-PROBE` were
   created during the measured incident recorded in
   `docs/v1_3/WAVE_8_DURABLE_RUNTIME.md` §6, before the registration script was
   made opt-in. From an elevated PowerShell on the host:

   ```powershell
   Unregister-ScheduledTask -TaskName JCC-v1.3-API   -Confirm:$false
   Unregister-ScheduledTask -TaskName JCC-v1.3-PROBE -Confirm:$false
   ```

   They were not removed from the agent session because `schtasks.exe` is
   blacklisted there, Windows' own unregister path goes through the same blind
   CIM provider whose failure caused the incident, and the sandbox forbids
   deleting task files by hand.

2. **Create the durable tasks.** The registration itself cannot be applied from
   the sandbox:

   ```powershell
   # elevated
   powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.3-register-tasks.ps1 -Apply
   powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.3.ps1 -Action mark
   ```

   Until this runs, the product is operated by `scripts\jcc-v1.3.ps1` and is NOT
   registered to survive a reboot.

3. **Authenticate the WorkBuddy CLI for the account that runs the JCC API.** This
   is the ONE blocker that keeps `JCC_LAUNCHED_NATIVE_WORKBUDDY_MISSION` from
   being claimed (W9.2, `docs/v1_3/WAVE_9_2_MISSION_LAUNCH_CONTROL.md` §8). The
   Start Mission path is complete and measured end to end: the launch intent is
   built from the domain, an operator-approved directive is signed with a short
   TTL and a single-use nonce bound to mission + project root + adapter, the
   Host Launcher verifies nine checks and spawns the workload under a Windows Job
   Object, and the run's own session id is read back from the child's stream. On
   this host the spawned CLI reports `Authentication required`, so the read-back
   classifies the run as `AUTH_REQUIRED` and — by design — the mission is NOT
   walked to `RUNNING` and a `LAUNCH_HOST_ACTIVATION` operator task is raised
   instead.

   ```powershell
   # as the account the JCC API service runs under, once:
   #   log the CLI in so a spawned non-interactive process can obtain credentials
   ```

   Until it is done, the capability probe reports `HOST_ACTIVATION_PENDING` (not
   green) and `final_release` stays `false`. **Nothing in the product pretends
   otherwise**, and no component falls back to a mock to make the path look
   complete.

## 7. Deliberately NOT claimed

Each of these would be an unmeasured claim if it were written as a pass. They are
listed so their absence is visible rather than implied.

* **A hardware frame rate, and any target FPS.** W10 *does* now measure the real
  shipped operational plane in real Chrome over 50/200/500/1000 objects of real
  persisted work (gate leg `B8_W10_BROWSER_PERF`; the numbers are in that leg's
  evidence, and the freeze does not restate them). But headless Chrome on this
  host rasterizes through ANGLE/SwiftShader — verified by launching it four ways
  (default, `--enable-gpu`, `--use-angle=d3d11`, `--use-angle=gl
  --ignore-gpu-blocklist`), every one of which reported `SwiftShader`. The frame
  times are therefore a **software rasterizer's floor**, not this host's GPU
  throughput, and the gate's evidence records the renderer string and a derived
  `renderer_class` so the number cannot be quoted without that context. No
  target-FPS claim is made: there is no measured baseline on this machine for
  what "fast enough" is, and inventing a threshold would be exactly the
  unfalsifiable green the rest of this release exists to remove.
* **More than `DOMAIN_LIST_LIMIT` (1000) objects in one view.** Beyond it the
  product says the view is partial (W9.3) but does not page. The honest limit is
  measured and disclosed, not hidden.
* **That real Chrome is exercised on a GPU-accelerated path.** The browser, the
  bundle, the ASGI server, the cross-origin API calls and the 3D render loop are
  all real; the *rasterizer* is software. Both facts are in the evidence.
* **Parent-exit survival of the product runtime.** The W8 restart transcript is
  evidence about the *controller*. Whether the service outlives its launcher
  depends on the caller's job object, not on the product — only the host Task
  Scheduler registration (section 6.2) can settle it, and the claim stays open
  until it is measured on a host.
* **That domain writes are fast — on any host, or on this one under load.** Measured
  during W10's seed and chased to a root cause that **corrects Wave 9.3 §3** and
  that also corrects my own first correction of it. What is established:
  `DomainStore._conn()` re-issues `PRAGMA journal_mode=WAL` / `synchronous=NORMAL`
  / `foreign_keys=ON` on *every* connection, and the store opens a fresh
  connection per operation — twice per `create_mission` — so it is exposed to the
  host's file-syncing latency on every call. What is refuted: that the cost
  tracks data size (a control with no product code in it is **flat** from 12 KB to
  1.68 MB in three journal modes) and that the HTTP layer contributes (HTTP 178 ms
  vs direct 178 ms for the same call). On a quiet host `create_mission` is 194 ms
  and `get_project` 6.8 ms; under contention the same measurements reached 935 ms
  and 438 ms, and the gate's seed ranged from 0.18 s to 1.4 s per mission. **~14 ms
  of the 194 ms is accounted for; the rest is recorded as unexplained rather than
  guessed at.** The fix — connection reuse — is a structural storage-layer change
  (`StateStore.connection()` is a public "FRESH connection, always closed"
  contextmanager), so it is the top **P4** item rather than a change folded into a
  frozen tree. Evidence: `tools/v1_3_domain_io_profile.py`,
  `docs/v1_3/WAVE_10_REAL_BROWSER_PERFORMANCE.md` §8, and
  `docs/v1_3/WAVE_9_3_NO_SILENT_TRUNCATION.md` §3a — the latter also records that
  `state_store.py`'s `_conn` docstring still carries the superseded explanation
  and is corrected only when that A8 pin is legitimately re-baselined.
* **DPAPI-at-rest for the control token.** The token remains an environment
  variable, which is what W8 shipped and what the health contract exposes.
* **Reboot recovery.** It follows from the durable registration, which is not
  applied (section 6.2). Configured, not proven.

## 8. Out of scope for v1.3

* A desktop shell / Tauri wrapper. Not a v1.3 blocker by the product order.
* Any change to the protected v1.0.1 GREEN service on 4778. It is read-only
  evidence throughout and is never a target.
* The v1.0.1-ops / v1.1 release profile (`artifacts/acceptance/final_gate_run.py`)
  and its `docs/FINAL_STOP_CONDITIONS.md` semantics. v1.3 uses its own
  orchestrator; the older profile is not re-interpreted and its known
  `DEGRADED`-tolerance is neither inherited nor repaired here.
* Any remote push, external publication, or production deployment.
