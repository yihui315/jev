# JCC v1.3 — WAVE 3: FIRST REAL WORKBUDDY MISSION

> **Status: ACCEPTED** · Gate `FIRST_REAL_WORKBUDDY_MISSION=PASS` (30/30)
> Scope: `packages/jcc_core/{mission_runtime.py, workbuddy_launch.py, domain.py, server.py}`
> + `apps/command_center_3d` launch UI + two new test modules + one gate script.

---

## 1. The hole this Wave closes

Wave 1 gave the control plane a Mission domain. A mission could be created,
transitioned through a 10-state machine, and given an `AgentRun`. **But nothing
connected a Mission to the AEP telemetry pipeline.**

That meant the domain could say `RUNNING` while the engine's session store,
assessment, policy, directive and audit tape all stayed empty. A mission that
claims to run with no evidence behind it is precisely the class of lie this
project exists to prevent — and before this Wave, the product would have told it.

Wave 3 is the **bridge**. It is also the Wave the v1.3 order calls "the most
important Gate", because until a real mission produces real telemetry, everything
downstream (Needs Attention, Deliverables, the 3D World, connection verification)
is rendering an empty system.

---

## 2. The launch chain (what actually happens)

```
POST /api/v1/domain/missions/{mid}/launch
        │
        ├─ 0. capture_launch_event()
        │     reads  ~/.workbuddy/sessions/<pid>.json  (the file the WorkBuddy
        │     runtime itself writes and heartbeats), requires pid ALIVE and
        │     heartbeat FRESH (<120 s). No live session ⇒ 409, not a fake.
        │
        ├─ 1. adapter.normalize(event)        ← the PRODUCTION adapter
        │     WorkBuddyAdapter._normalize_session_file → AEPEnvelope
        │     (a refused/unidentifiable event surfaces as a refusal)
        │
        ├─ 2. domain.create_run()             ← AgentRun opened only now
        │     attach_session(mission, sid) + attach_run_session(run, sid)
        │
        ├─ 3. state machine walk: DRAFT → READY → STARTING → RUNNING
        │     one legal step at a time; a refusal STOPS the walk
        │
        ├─ 4. engine.ingest(envelope)
        │     assessment → deterministic policy → signed-ness=False directive
        │     → audit tape → SSE broadcast  (acted=False, always)
        │
        └─ 5. mark_run_launched()
```

Every step is recorded in the response's `steps[]` array, including the step that
failed. The UI prints that array verbatim.

### 2.1 Why a session file and not a hook payload

There are two real telemetry channels on this host. Verified, not assumed:

| Channel | State on this host | Used? |
|---|---|---|
| Native hooks (`jcc_emit.py` via `settings.json`) | `hooks == {}` — **not installed** | No |
| Session registry `~/.workbuddy/sessions/<pid>.json` | **653 files, 2 live** at the time of writing | **Yes** |

A hook-driven launch would have no producer here; claiming otherwise would be a
fake integration. The session registry is written and heartbeat-refreshed by the
WorkBuddy runtime itself, so reading it is reading *the agent's own record* — the
adapter then normalises it through the production code path
(`WorkBuddyAdapter._normalize_session_file`), not a driver-local shortcut.

### 2.2 Liveness is three-state, and the distinction matters

`workbuddy_launch.liveness()` returns `live` / `stale` / `dead`, never a bool:

- `dead`  — the recorded pid is gone (WorkBuddy exited)
- `stale` — pid alive but no recent heartbeat (**hung / wedged**)
- `live`  — pid alive **and** heartbeat fresh

Collapsing `stale` into `dead` would hide a hung WorkBuddy behind "not running" —
exactly the silent-unknown this project bans. `LAUNCHABLE_AGENTS = ("workbuddy",)`
is the server-side truth; the other three agents are selectable for planning but
the launch path refuses them (`preflight`), rather than pretending they can run.

---

## 3. Design rules enforced (not documented, *enforced*)

1. **The runtime never fabricates an AEP envelope.** There is no code path in
   `mission_runtime.py` that constructs one. It comes from an adapter or the
   launch is refused.
2. **The runtime cannot actuate.** `MissionRuntime.__init__(domain, engine)` takes
   no actuator. The actuation seam is not reachable from this module. Every ingest
   reports `acted=False` with `actuation_reason="shadow_mode"`.
3. **A refused transition is a refusal.** The walk stops at the first illegal
   step; it never skips a state to "make it work".
4. **Identity is the adapter's.** The session id in the envelope is the one the
   adapter derived from the real record. The runtime never invents one and then
   claims telemetry arrived under it.
5. **No orphaned runs.** The event is resolved *before* any `AgentRun` is opened,
   so a refused launch leaves zero runs behind.

---

## 4. Three real defects found and fixed during this Wave

All three were found by measurement, and each is now pinned by a test.

### D-W3-1 — Import-side-effect store helpers (silently absent)

The first draft attached `attach_run_session` / `mark_run_launched` / `end_run`
onto `DomainStore` from inside `mission_runtime.py` at import time. Any caller
that imported only `jcc_core.domain` therefore got a store **without** those
methods — and my own diagnostic proved it: `AttributeError: 'DomainStore' object
has no attribute 'attach_run_session'`.

**Symptom in the gate:** `AGENT_RUN_BOUND` FAILed with `session: null` — the
mission carried the session id while its run did not.

**Fix:** moved the three methods into `domain.py` where they belong. An
import-side-effect helper is invisible to a caller that does not trigger the
import; that is not a design, it is a trap.

### D-W3-2 — Orphaned run on a refused launch

The first draft created the `AgentRun` as step 0 and only then resolved the
event. A launch refused for "no telemetry" therefore left a dead run attached to
a DRAFT mission.

**Symptom in the gate:** `AGENT_RUN_BOUND` FAILed with `runs: 2` for one launch.

**Fix:** resolve the event first; open the run only once there is something real
to run on. Verified: `runs: 1`.

### D-W3-3 — A refused re-launch downgraded a healthy mission

`_fail_run` unconditionally moved the mission to `NEEDS_ATTENTION` on failure.
But a *re-launch* of an already-RUNNING mission is refused by the state machine
(no `RUNNING → READY` edge) — and that refusal is the *second attempt* failing,
not the work. The first run was still healthy.

**Symptom in the test:** `test_relaunch_of_a_running_mission_is_refused_by_the_state_machine`
observed `RUNNING → NEEDS_ATTENTION`.

**Fix:** `_has_other_live_run()` — the mission state is only changed when the
failure is the mission's problem. Discovered because a test I had written on a
wrong assumption failed honestly; the state machine was right and my test was
wrong, so I corrected the test **and** the runtime.

### D-W3-4 — The v1.1 security gate's S-01 probe could not show the gate OPEN

**Not a Wave 3 regression — a pre-existing instrument defect that Wave 3 made
visible, and that was fixed here rather than waived.**

`tools/v1_1_security_gate.py` check S-01 replays one generic body
(`{"session_id":"s01",...,"event_type":"session_start"}`) against *every* mutating
route with three tokens, and classified a route as vacuous (MEDIUM) whenever the
CORRECT token did not return 2xx. That predicate only holds for routes whose body
schema happens to match the probe payload.

Wave 1 added eight `/api/v1/domain/*` write routes whose bodies differ
(`CreateProjectBody` needs `name`, `transition` needs `to_state`, …), so FastAPI's
request-body validation answered **422 before** `Depends(require_control_token)`
ever ran. Wave 3 added a ninth (`POST /api/v1/domain/missions/{mid}/launch`).
The committed artifact recorded `medium=8` from the *pre-Wave-1* tree, so
`test_v1_1_security_gate_artifact.py::test_check_mode_reports_the_artifact_is_current`
failed deterministically — a checker that says "the artifact is current" while the
live tree disagrees is precisely a false declaration.

**Diagnosis (read-only, `.workbuddy/scratch/s01_probe_diag.py`):** across all 22
mutating routes, `no_token` and `wrong_token` returned **403 everywhere**
(`PASS_FIRST_FAULT = 0`). The auth gate was fail-closed and correct on every
route. The defect was in the *measuring instrument*, not the product.

**Fix (two parts, non-vacuity preserved):**
1. The "gate opens" predicate is now `correct_token not in (401, 403)`. A 422/404
   is *downstream of auth* — it proves the token was accepted and the request moved
   past the gate. Only 401/403 would prove the gate refused a valid token.
2. Non-vacuity now requires **(a)** no route refuses the correct token at the auth
   layer **and (b)** at least one route actually reaches 2xx — otherwise a build
   where every handler 422s would look "clean" while proving nothing.

Result: `verdict=GO high=0 medium=0 ran=9/9`, and
`artifacts/release/V1_1_SECURITY_GATE.json` refreshed in this same commit.

---

## 5. Gate evidence — `tools/v1_3_wave3_mission_gate.py`

Real v1.3 ASGI app on an ephemeral port, isolated temp DB, real HTTP, real SSE
subscriber attached *before* the launch.

Evidence: `.workbuddy/scratch/v1_3_wave3_gate_20260929-072225.json`

```
verdict: PASS    passed: 30 / 30    failures: []
```

| # | Step | Observed |
|---|---|---|
| 1 | `BOOT` | app answered on ephemeral port |
| 2 | `LAUNCH_PROBE_OPEN` | 200 |
| 3 | `LAUNCH_PROBE_SCHEMA` | `jcc/v1.3-launch-probe/1` |
| 4 | `LAUNCH_PROBE_ACTUATION_FALSE` | `false` |
| 5 | `LAUNCH_PROBE_HONEST` | `{ok: true, session_count: 653}` |
| 6 | `HOST_SESSIONS_EXIST` | **653** session files read from disk |
| 7 | `REAL_SESSION_AVAILABLE` | `9cbdd2f1-…-fa4914ae076b` (pid 6876, live) |
| 8 | `CREATE_PROJECT` | `prj_45bc28625a804285` |
| 9 | `CREATE_MISSION_DRAFT` | `DRAFT` |
| 10 | `MISSION_CREATE_TOKEN_GATED` | refused without token |
| 11 | `LAUNCH_REQUIRES_REAL_SESSION` | `{ok: false}` — **refused, never faked** |
| 12 | `REFUSED_LAUNCH_LEAVES_NO_RUNNING` | stayed `DRAFT` |
| 13 | `LAUNCH_REAL` | `{ok: true}` |
| 14 | `LAUNCH_REAL_SESSION_FROM_REGISTRY` | session id **is in the host registry** |
| 15 | `LAUNCH_INGESTED` | `true` |
| 16 | `LAUNCH_INGEST_ACTION` | `CONTINUE` |
| 17 | `LAUNCH_ACTUATION_FALSE` | `acted: false` |
| 18 | `LAUNCH_SESSION_STATUS` | `active` |
| 19 | `LAUNCH_STEPS_ALL_OK` | `normalize/create_run/READY/STARTING/RUNNING/ingest` all true |
| 20 | `MISSION_RUNNING` | `RUNNING` |
| 21 | `MISSION_SESSION_BOUND` | mission carries the real session |
| 22 | `MISSION_TIMELINE` | `["DRAFT","READY","STARTING","RUNNING"]` |
| 23 | `AGENT_RUN_BOUND` | **runs: 1**, session bound |
| 24 | `WORKLOAD_REAL` | `{live: 1, runs: 1}` |
| 25 | `WORKLOAD_ACTUATION_FALSE` | `false` |
| 26 | `AUDIT_TAPE_HAS_SESSION` | `audit_rows: 1` |
| 27 | `SESSION_IN_STORE` | session exists in the state store |
| 28 | `SSE_BROADCAST` | **7/7** events received by a pre-attached subscriber |
| 29 | `HOME_COUNTS_RUNNING` | `1` |
| 30 | `HEALTH_ACTUATION_FALSE` | `false` |

> An earlier run of this gate (**28/30**) failed `AGENT_RUN_BOUND` and
> `AUDIT_TAPE_HAS_SESSION`, which is exactly how D-W3-1 and D-W3-2 were caught —
> see §4. The gate was not relaxed; the product was fixed.

---

## 6. Tests

| Suite | Result |
|---|---|
| `packages/jcc_core/tests/test_mission_runtime_v1_3.py` (new) | **13 passed** |
| `tests/e2e/test_mission_launch_v1_3.py` (new) | **10 passed** (incl. the real launch) |
| Frontend `vitest` | **48 passed** |
| `tsc --noEmit` | clean |
| `vite build` | success — product chunk 360 KB / 107 KB gzip, three.js 936 KB isolated, CSS 26.8 KB |

`test_mission_runtime_v1_3.py` pins the three defects: a launch with no event is
refused; the `AgentRun` owns the session; a refused launch leaves no run; and
(after D-W3-3) a refused re-launch does not downgrade a healthy mission.

---

## 7. Frontend

- `MissionStudio` — launch is now **ONE authenticated call** to the server, not a
  client-side transition walk. The dialog live-probes the host and shows either
  "no live session detected — launch will be refused rather than faked" or
  "live session ready: `<id>` (pid)".
- `MissionsView` → the mission drawer gained a **Live workload** panel bound to
  `/workload`: agent runs, session ids, live-telemetry count, and `Actuation OFF`.
  A failed workload read renders "Workload unknown", never "no workload".
- `domainApi` gained `launchProbe()`, `launchMission()`, `missionWorkload()` and
  the `LaunchResult` / `LaunchProbe` / `MissionWorkload` / `LaunchLiveness` types.

---

## 8. Files

**Added**
```
packages/jcc_core/mission_runtime.py          the launch bridge
packages/jcc_core/workbuddy_launch.py         real-session discovery + liveness
packages/jcc_core/tests/test_mission_runtime_v1_3.py
tests/e2e/test_mission_launch_v1_3.py
tools/v1_3_wave3_mission_gate.py
docs/v1_3/WAVE_3_FIRST_REAL_MISSION.md
```

**Modified**
```
packages/jcc_core/domain.py       attach_run_session / mark_run_launched / end_run
packages/jcc_core/server.py       MissionRuntime wiring + launch/probe/workload routes
packages/jcc_core/tests/test_p3_acceptance_gate.py  A8 re-baseline (server.py only) + A11 +launch
packages/jcc_core/tests/test_server_surface.py      declared-open list +2 Wave 3 GETs
tools/v1_1_security_gate.py       D-W3-4 fix: S-01 gate-opens predicate + non-vacuity
docs/p3/A8_REBASELINE.json        window V1.3-FREEZE-W-3-LAUNCH
artifacts/release/V1_1_SECURITY_GATE.json           refreshed (medium 8 -> 0)
apps/command_center_3d/src/app/domainApi.ts
apps/command_center_3d/src/app/MissionStudio.tsx
apps/command_center_3d/src/app/MissionsView.tsx
```

**A8 freeze window:** exactly one pinned artifact moved — `packages/jcc_core/server.py`
(`f4c01f35.. -> 4ac96c0e..`); the other 11 are byte-identical. The byte change and the
pin update are in this one commit, satisfying both wordings of the A8 rule.

**Open-surface change (deliberate, recorded):** the declared open GET set grew
from 21 to **23** routes (`/api/v1/domain/launch/probe`,
`/api/v1/domain/missions/{mid}/workload`). The anti-drift check
`declared == served` still holds at 23 == 23, verified directly.

---

## 9. Rollback

Rollback point tag: `jcc-v1.3-wave3-rollback-before` (pre-Wave-3 commit
`7cec039`). Additive only: new modules, new routes, three new `DomainStore`
methods that UPDATE existing columns. No schema change, so rollback has no
migration implication.

---

## 10. Carried forward

- The launch bridge proves **WorkBuddy only**. Wave 7 verifies the other three
  agents and may add them to `LAUNCHABLE_AGENTS` honestly.
- The launch consumes **one** session record. A long-running mission producing a
  stream of live events (hooks installed) is Wave 4/7 territory.
- `Needs Attention` is produced by the runtime on failure but the **control**
  side (resolving it) is Wave 4.
