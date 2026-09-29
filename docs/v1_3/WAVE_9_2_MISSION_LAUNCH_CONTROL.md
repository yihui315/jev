# WAVE 9.2 — MISSION LAUNCH CONTROL PLANE

> Branch `release/jcc-v1.3-final`. This document is written from measurements on
> this host, not from the design.
>
> **One-line verdict.** The product can now go from *"type a goal, pick an agent,
> press Start Mission"* to **a real process on this host**, and it reports the
> truth about every step — including refusing to call a run "running" when the
> agent never authenticated. It still cannot claim a completed mission, because
> the host has no agent credentials. That gap is measured, named and recorded,
> not papered over.

---

## §1 The hole this wave closes

Wave 9.1 shipped `packages/jcc_core/host_launcher.py`: an 840-line module that can
verify a nine-check signed directive and spawn an allowlisted workload under a
Windows Job Object. It was tested (28 tests) and committed (`8e417b8`).

**Nothing called it.** There was no HTTP route, no service, no UI path. The
product's central promise still terminated at a database row: `POST
/api/v1/domain/missions` created a Mission, and the only launch-adjacent route
(`.../launch`) called `capture_launch_event()`, i.e. it looked for a WorkBuddy
session *that was already running* and ingested it. That is ATTACH. It is not
START.

So §F of the execution order — *"one-click start AND `AUTONOMOUS_ACTUATION=False`"*
— was structurally unsatisfied: you cannot have a one-click start with no code
path from the click to the process.

## §2 What was built

### 2.1 `packages/jcc_core/launch_control.py` (new, the control plane)

A composition layer, not a new source of truth. It owns three things and
delegates everything else.

| Concern | Owner |
| --- | --- |
| What command may run | `host_launcher.LaunchTemplate` registry (unchanged) |
| Whether a directive authorises it | `host_launcher.HostLauncher.verify` (unchanged) |
| Starting the process | `host_launcher.HostLauncher.launch` (unchanged) |
| Walking a Mission to RUNNING | `mission_runtime.MissionRuntime` (unchanged) |
| **Binding a Mission to a command** | `LaunchController` ← new |
| **Minting the launch directive** | `LaunchController` ← new |
| **Composing one honest answer** | `LaunchController.request` ← new |
| **Refusing to ingest an unproductive run** | `LaunchController.request` ← new |

Two additive changes were made to `host_launcher.py` (it is **not** an A8-pinned
file):

* `verify(..., consume: bool = True)` plus `dry_verify()`. Check 4 both *tests
  and spends* the nonce, so a preview could not use `verify_directive` without
  burning the token the commit needs. `consume=False` runs the identical nine
  checks with the replay cache withheld, then asks `ReplayCache.seen()` for
  check 4 without the side effect. Same answer, same reason codes, no second
  implementation.
* `wait(pid, timeout_s)` and a `_procs` list. `launch()` previously dropped the
  `Popen` handle, so the child's exit code was unobtainable — and reporting `0`
  would have been a guess dressed as a fact.

### 2.2 Routes (`server.py`, token-gated POST only)

| Route | Effect |
| --- | --- |
| `POST /api/v1/domain/missions/{mid}/launch-request` | Preview. **Spawns nothing. Does not spend the nonce.** |
| `POST /api/v1/domain/missions/{mid}/launch-commit` | The **only** route in the product that starts a process. |
| `GET  /api/v1/domain/launch/probe` (existing, open) | Gained a `native_launch` capability reading. |

**No new GET route was added**, so `OPEN_GET_ENDPOINTS` is unchanged at 27 and
the `test_server_surface` declared-open set did not move. Only the A11 POST
**exact** set changed — a reviewed edit, which is precisely what that assertion
exists to force.

The probe now answers two questions that were previously conflated:

* `workbuddy` — "is there a live session I could **ATTACH** to?" (unchanged)
* `native_launch` — "can this host actually **START** one?" (new, measured)

### 2.3 Frontend (`MissionStudio.tsx`, `domainApi.ts`)

A **Start Mission** panel beside the existing **Launch…** button, and the panel
says what the difference is rather than leaving the operator to guess. Start is
preview-first: `reviewStart` → dialog showing the **exact argv**, `would_accept`,
`shell`, `actuation`, the directive TTL and any problems → `commitStart`.

The capability badge renders `LOCAL_SPAWN_READY` / `HOST_ACTIVATION_PENDING` /
`UNKNOWN` in three different colours, and `UNKNOWN` is never green.

## §3 The honest-composition rules (the actual product content)

These are the rules that make the surface trustworthy. Each was forced by a
defect found during construction — see §4.

1. **One answer to "will this launch?"** The nine checks and the command
   buildability are computed by the layers that own them and *composed* into one
   `would_accept`. Before the fix, `would_accept` was `true` while
   `preview.launchable` was `false` — two answers to one question, the defect
   class this project bans. When the command is the blocker the reason is
   `COMMAND_NOT_BUILDABLE:<codes>`, so the UI never has to explain a check
   failure that did not happen.

2. **A preview may not lie about a spent token.** `dry_verify` now consults the
   replay cache non-destructively. Before the fix it reported `ok` for an
   already-consumed nonce and the commit then failed `REPLAY` — the preview
   lying about the single thing it exists to predict.

3. **`ACTUATION` and `PROCESS STARTED` are different facts.** The classification
   comes from the child's *own* NDJSON stream via `classify_stream`, never from
   "a process exists". A child that started and produced nothing is
   `STARTED_NO_TELEMETRY`; one refused at authentication is `AUTH_REQUIRED`.

4. **An unproductive run is never ingested as a running mission.**
   `MissionRuntime.launch` walks `DRAFT → READY → STARTING → RUNNING`. Handing it
   a stream that proves only "a process started and was refused" would put a
   mission in RUNNING having done no work. The handoff is therefore gated on the
   **classification**, not on the session: a discovered session is recorded as a
   fact (`NATIVE_SESSION_DISCOVERED`), but `AEP_FIRST_EVENT` is recorded as
   **not obtained** and the mission stays where it honestly is.

5. **The session id is the child's.** `session_id_from_stream` reads the `system`
   init record the CLI emits. When it is absent there is **no** session and no
   AEP event — the receipt factory returns `None` rather than defaulting to the
   mission id we passed on the command line. Defaulting would put a phantom
   session in the engine's store.

6. **A failed start is a human task.** A commit that spawned but did not become a
   running mission raises a pending `LAUNCH_HOST_ACTIVATION` approval carrying
   the exact reason. The mission's **status is deliberately left alone**:
   `NEEDS_ATTENTION` is only reachable from `RUNNING` (see
   `MISSION_TRANSITIONS`), so forcing it would be the same lie by another route.

7. **The goal is never a command.** It occupies exactly one argv slot, last,
   asserted in three suites. The head is resolved by the template; the project
   root comes from the project row, **not** from the request body; the only
   body value that reaches a template flag is `model`, matched against
   `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$` and refused otherwise.

## §4 Defects found while building this (each fixed, each tested)

| # | Defect | How it surfaced | Fix |
| --- | --- | --- | --- |
| 1 | An `AUTH_REQUIRED` run with a real session id was handed to `MissionRuntime.launch`, walking the mission to RUNNING with no work done | A unit test asserted "no phantom AEP ingest" and failed | Gate the handoff on classification (§3.4) |
| 2 | `would_accept` (checks) and `preview.launchable` (command) could disagree | A test asserted the stale project root must not preview as launchable; `would_accept` was `true` | Compose one answer (§3.1) |
| 3 | `dry_verify` returned `ok` for an already-spent nonce | A test verified-then-dry-verified and expected `REPLAY` | Non-destructive peek in `verify(consume=False)` (§3.2) |
| 4 | `LAUNCH_REQUESTED` appeared **twice** in the ordered step list (controller + launcher) | A step-order test failed | Drop the launcher's duplicate so the mandated order names it once (§9 of the order) |
| 5 | `capability_version` was digested from a **single** template while the launcher executed a **registry** | Reading the code against its own docstring | `build_intent` takes the instance's `registry_digest` |
| 6 | A run with a model turn but **no** session id raised no operator task | Reviewing the approval condition | Raise on `receipt is None OR classification != LAUNCHED` |

Defect 1 is the important one: it is the mechanism by which a product comes to
show a mission running that did no work, and it was reachable on the very first
end-to-end path that worked.

## §5 Tests

| Suite | Count | Result |
| --- | --- | --- |
| `packages/jcc_core/tests/test_launch_control_v1_3.py` (new) | 28 | pass |
| `packages/jcc_core/tests/test_host_launcher_v1_3.py` (extended) | 28 | pass |
| `tests/e2e/test_launch_control_v1_3.py` (new) | 10 | pass |
| `packages/jcc_core/tests/test_p3_acceptance_gate.py` + `test_server_surface.py` | 55 | pass |
| Frontend `vitest` (incl. 4 new client-contract tests) | 80 | pass |
| Frontend `tsc --noEmit` / `vite build` | — | clean |

Coverage worth naming: the nine-check refusals are **parameterised** and each
asserts `spawned is False`; the committed path uses a **real child process**
(a python one-liner emitting CLI-shaped NDJSON) so the classification assertions
do not depend on the WorkBuddy CLI's credential state; and the AEP handoff is
tested with a synthetic `LAUNCHED` stream to prove the positive path works
*before* credentials exist.

The e2e suite deliberately exercises only the **preview** route and the token
gate. The in-process app runs the production template registry, whose head
resolves to the real WorkBuddy CLI, so a commit there would spawn a real agent
from inside `pytest`. That belongs to an operator-initiated run.

## §6 Gate and freeze impact

* **A8 pin: 1 of 12 files moved** — `packages/jcc_core/server.py`
  `09f9955ca759… → dbb661bbe0e3…`. Re-baselined with old→new + reason in
  `test_p3_acceptance_gate.py`, in the same commit that moves it, as required.
  `host_launcher.py` and `launch_control.py` are **not** pinned.
* **A11 inbound surface**: two entries added to the exact POST set, with a
  comment stating that `launch-commit` is the only route in the product that
  spawns.
* **`V1_1_SECURITY_GATE.json` re-emitted** (route inventory). Gate verdict
  `GO high=0 medium=0 low=0 ran=9/9`; the currency check now reports
  `OK: the committed artifact matches (sha 01d22e769c3324b7421007b6)`.
* **No new GET route** → `OPEN_GET_ENDPOINTS` unchanged (27).
* A8 self-check run *before* the commit: 55 passed.

## §7 Honest scope — what this wave does NOT claim

* **`JCC_LAUNCHED_NATIVE_WORKBUDDY_MISSION` is still NOT claimed.** The
  end-to-end measurement in Wave 9.1 stands: the CLI starts, emits well-formed
  NDJSON and reports `Authentication required` for a launcher-spawned process.
  This wave makes that outcome *reachable and correctly reported*; it does not
  cross the credential boundary.
* **`PROJECT_COMPLETE = false`.** Per §AF, the missing host privilege is not
  forged into a completion. The honest state is
  `PRODUCT_READY = true` / `HOST_ACTIVATION_PENDING = true`.
* **No real Chrome E2E or performance numbers in this wave.** The frontend was
  verified by `tsc`, `vitest` and a production `vite build`; a real-browser pass
  over the new panel is a separate, later step.
* **The mission's own status does not change on a failed start** — deliberate,
  and explained in §3.6. The pending approval is the record.

## §8 The blocker, and the morning action

```
BLOCKER  : a launcher-spawned WorkBuddy CLI cannot authenticate on this host.
WHY      : the CLI resolves its credential from state owned by the WorkBuddy
           desktop runtime; a process spawned from the JCC API service does not
           inherit it.
EVIDENCE : packages/jcc_core/launch_control.py classify_stream ->
           AUTH_REQUIRED; measured end-to-end in Wave 9.1 (three-environment
           probe) and reproduced by the unit test that uses an AUTH_SCRIPT.
IMPACT   : JCC_LAUNCHED_NATIVE_WORKBUDDY_MISSION cannot be claimed;
           PROJECT_COMPLETE = false. The capability reads
           HOST_ACTIVATION_PENDING, not green.
MORNING_ACTION
   (a) sign the CLI in for the account that runs the JCC API service, OR
   (b) confirm the supported way for a non-app process to obtain CLI credentials.
   Then: press Start Mission and read the classification.
```

## §9 Files

| File | Change |
| --- | --- |
| `packages/jcc_core/launch_control.py` | **new** — the control plane |
| `packages/jcc_core/host_launcher.py` | `consume=False` / `dry_verify` / `wait` / `_procs` |
| `packages/jcc_core/server.py` | 2 POST routes + `native_launch` in the probe + controller wiring |
| `packages/jcc_core/tests/test_launch_control_v1_3.py` | **new** — 28 tests |
| `packages/jcc_core/tests/test_p3_acceptance_gate.py` | A8 re-baseline + A11 exact set |
| `tests/e2e/test_launch_control_v1_3.py` | **new** — 10 tests |
| `apps/command_center_3d/src/app/domainApi.ts` | `launchRequest` / `launchCommit` + types |
| `apps/command_center_3d/src/app/MissionStudio.tsx` | Start Mission panel + preview dialog |
| `apps/command_center_3d/src/app/domainApi.test.ts` | +4 client-contract tests |
| `artifacts/release/V1_1_SECURITY_GATE.json` | re-emitted |

## §10 Rollback

```
git tag  jcc-v1.3-w9.2-rollback-before <parent-of-this-wave-commit>
git reset --hard jcc-v1.3-w9.2-rollback-before
```

Reverting is safe and complete: the wave adds one module, two routes, one
frontend panel, and a re-baseline. Nothing existing changed behaviour — the
Wave-3 attach path (`POST .../launch`) is untouched and still does exactly what
it did.
