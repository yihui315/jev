# WAVE 6 — Operational 3D World

> v1.3 PRODUCT EDITION. Turn the 3D plane from a *v1.2 engine-telemetry toy* into
> an **operational map of the work the user actually runs** — without ever making
> the 3D plane a second source of truth.
> Owner: Orion (design) / Weaver (3D) / Prism (UX) / Forge (projection maths) /
> Metric (tests + gate).

## 1. The hole this Wave closes

The `world` view is a v1.2 relic. It renders the **engine's** telemetry: a Core
node (provider health), agent clusters, and session satellites, all fed from
`GET /api/v1/scene`. It answers *"what is the Jev engine seeing?"* — which is the
right question for a supervision console, and the wrong one for the product the
user now opens every day.

Since Wave 1–5 the user's actual world is:

```
Project ──▶ Mission (10-state lifecycle) ──▶ Deliverable (PRODUCED|VERIFIED)
                     │
                     └─▶ Needs Attention (when blocked)
```

None of that is in the 3D plane. A user who opens **World** sees agent spheres for
telemetry sessions and **cannot see their own projects, missions or deliverables
at all**. The operational surface — the one place a 3D view earns its keep, by
showing *many missions at once, grouped by project, coloured by state* — does not
exist.

Wave 6 builds it, under one hard constraint carried from the project's founding
rule:

> **The Visual Plane is not a source of truth.** The 3D world renders a projection.
> It never owns state, never writes, and when a fact is unknown it renders unknown.

## 2. The rule everything hangs on

> **The 3D world shows what the DOMAIN says, and nothing else.** Every node's
> position, colour and label is derived from a value the API returned. There is no
> decorative node, no placeholder mission, no "demo" cluster.

Consequences, all enforced:

1. **Empty domain → empty space.** With no projects, the world is empty (plus an
   honest "no projects yet" overlay). It is not seeded with sample stars.
2. **Colour is a fact, not a mood.** A mission node's colour is a pure function of
   its `MissionStatus` (a closed 10-value vocabulary). `NEEDS_ATTENTION` is the
   attention tone; `COMPLETED`/`FAILED`/`CANCELLED` are terminal and visually
   distinct; nothing is coloured "healthy" unless the domain says so.
3. **UNKNOWN stays UNKNOWN.** If a read fails, that section renders an explicit
   "unknown" state — never an empty-but-confident space, and never a zero.
4. **Clicking navigates, it does not mutate.** Selecting a mission in 3D takes the
   user to the Mission (or opens its inspector); it never changes domain state.
   The 3D plane has no write path at all.

## 3. What gets built

### 3.1 Projection (`apps/command_center_3d/src/store/domainWorld.ts`)

A **pure** module (no React, no fetch) that maps the already-open domain reads to
world geometry. Pure so its placement/colour rules are unit-testable and cannot
drift from the domain vocabulary.

```ts
export interface WorldMission {
  missionId: string; title: string; projectId: string;
  status: MissionStatus;      // the closed 10-value vocabulary
  attentionKind: string | null;
  hasDeliverables: boolean;
}
export interface WorldProject {
  projectId: string; name: string;
  missions: WorldMission[];
  counts: { total: number; running: number; attention: number; done: number };
}
export interface DomainWorld {
  projects: WorldProject[];
  totalMissions: number;
  empty: boolean;
}
export function buildDomainWorld(
  projects: Project[], missions: Mission[], deliverables: Deliverable[],
): DomainWorld;
```

Rules encoded here (and tested):

- A mission belongs to exactly one project (`projectId`); a mission whose project
  is unknown is **dropped and counted** as `orphanMissions` (never silently placed).
- `hasDeliverables` is true iff a deliverable references the mission.
- Counts are derived, never passed in.
- Position is a deterministic function of ids (stable across reloads), so the map
  does not shuffle when data refreshes.

### 3.2 Mission state vocabulary (`status.ts`)

`missionStatusTone(status)` maps all ten states to the existing tone set:

| Status | Tone | Rationale |
|---|---|---|
| `DRAFT`, `READY` | `unknown` | not started; no claim |
| `STARTING`, `RUNNING` | `info` | work in flight |
| `NEEDS_ATTENTION` | `warn` | a human is required |
| `PAUSED` | `unknown` | halted by choice |
| `VERIFYING` | `info` | work done, check pending |
| `COMPLETED` | `ok` | terminal success |
| `FAILED` | `danger` | terminal failure |
| `CANCELLED` | `unknown` | terminal, not a success |

Unknown status → `unknown` (never `ok`).

### 3.3 Scene (`src/scene/OperationalWorld.tsx`)

A new R3F scene, selected when the user opens **World**:

- **Project** = a slowly-rotating ring/platform in space, labelled.
- **Mission** = a body orbiting its project; colour = `missionStatusTone`;
  `NEEDS_ATTENTION` pulses; terminal missions are dimmed/settled.
- **Deliverable** = a small satellite around a mission that has any.
- **Hover** = a DOM tooltip (accessible, not canvas text) with title + status.
- **Click mission** = navigate to the Missions view focused on it; **click
  deliverable** = navigate to Deliverables. No writes.

The v1.2 `CommandCenter3D` scene is **kept** (it is the engine-supervision view)
and reachable; Wave 6 adds the operational scene as the default for `world`, with
a toggle, so nothing is deleted and the legacy contract still holds.

### 3.4 Honest states

| Situation | Render |
|---|---|
| Domain reads loading | skeleton ring, "mapping your work…" |
| No projects at all | empty space + "No projects yet — create one to see it here." |
| A read failed | an explicit banner "world is UNKNOWN (reason)" — never an empty confident space |
| WebGL2 absent | fall back to the Table/graph view (existing Wave 8 behaviour), unchanged |

## 4. Falsifiers

1. **A decorative node exists**: build the world from an empty domain and assert
   zero nodes.
2. **Colour lies**: assert every mission status maps to a tone, and that only
   `COMPLETED` maps to `ok`; an unknown status maps to `unknown`.
3. **Orphans are silently placed**: a mission with an unknown project must be
   counted as orphan and not rendered under some project.
4. **Position is unstable**: two calls with the same ids produce identical
   positions.
5. **The 3D plane writes**: assert the operational world module imports no write
   API and calls no POST (structural test).

## 5. Boundaries held

- Visual plane is **not** a source of truth: the world is built from the same open
  reads the rest of the product uses; it writes nothing.
- No demo data: empty domain → empty world.
- UNKNOWN never rendered as green or as zero.
- GREEN 4778 untouched (frontend-only Wave; no server route added).

## 6. Evidence

All numbers below are from real runs on the W6 working tree.

### 6.1 Wave-6 gate

| Field | Value |
|---|---|
| Runner | `tools/v1_3_wave6_world_gate.py` (drives real `tsc` + real `vitest` + structural checks) |
| Verdict | **PASS 6 / 6**, `failures: []` |
| Evidence JSON | `.workbuddy/scratch/v1_3_wave6_gate_20260929-105326.json` (`artifacts/` is gitignored) |
| sha256 | `a6cd9281b88d49e29c7666a9fc88eab9c44669f05455a605dce814bc4eca0ea5` |

Checks asserted: `NODE_FOUND`, `TSC_CLEAN`, `WORLD_TESTS_PASS`,
**`WORLD_HAS_NO_WRITE_PATH`** (the projection and the scene contain no `fetch(` /
`POST` / token / write call — the plane cannot mutate domain state),
`WORLD_IMPORTS_TYPES_ONLY` (the projection imports the API surface **type-only**),
`NAV_DECLARES_OPERATIONAL_WORLD` (both `world` and `engine-world` exist, so the
v1.2 scene was not silently deleted).

### 6.2 Tests (real tallies)

| Suite | Result |
|---|---|
| `src/store/domainWorld.test.ts` (new) | **8 passed** |
| `src/app/navigation.test.ts` | **8 passed** |
| `src/app/status.test.ts` (colour honesty added) | **13 passed** |
| Frontend `vitest run` (all files) | **64 passed** (9 files) |
| Frontend `tsc --noEmit` | clean (exit 0) |
| Frontend `vite build` | success (product 375.6 kB / 110.6 kB gzip, three 936 kB) |
| `packages/jcc_core/tests` + `tests/e2e` (full, fresh `--basetemp`) | **742 passed, 1 skipped, 0 failed, 0 errors** (505.12s) |
| `tools/tests` (full, fresh TEMP) | **247 passed, 0 failed** (457.25s) |

**On the single skip** — `tests/e2e/test_mission_launch_v1_3.py::test_real_launch_produces_real_telemetry`
skips because it refuses to fake a real launch: `capture_launch_event().ok is False`,
`reason == "no_live_workbuddy_session"`. The WorkBuddy sessions that were live
during Wave 3 have aged past the freshness window (`liveness` is a function of
heartbeat age), so the host has no live session to launch against right now. This
is honest, host-conditional behaviour, **not a regression**: total collection is
still **743** (742 passed + 1 skipped), so no test was lost, and this Wave changed
**zero** Python product files. When a live WorkBuddy session exists the test runs
and asserts the real end-to-end telemetry path (as it did in the W3–W5 runs).

### 6.3 Governance

- **No A8/A11 change.** Wave 6 touches **no Python product file** (it adds one
  gate script under `tools/` and frontend modules), so the 12 A8 pins and the
  inbound surface are byte-identical. The security-gate artifact remains
  `[check] OK` (sha `5a5ef85c0666efb434dcebea`) with no re-emit needed.
- Navigation registry: `world` is repurposed to the **operational** map; the v1.2
  engine-telemetry scene moves to the new `engine-world` id. `LEGACY_VIEWS` is
  unchanged (the four v1.2 ids still exist, so the SceneStore contract holds).
  `showsInspectorRail` is now true for `engine-world`/`graph`/`table` and false
  for the operational `world` (it is not session-oriented).

### 6.4 Commit / rollback

| Item | Value |
|---|---|
| Commit | `v1.3-wave6-operational-world` |
| Rollback point | `git tag jcc-v1.3-wave6-rollback-before → 948f43c` (Wave 5 commit) |

### 6.5 Boundaries verified

- **Visual plane is not a source of truth** — `WORLD_HAS_NO_WRITE_PATH` asserted
  structurally; the projection module imports the client type-only.
- **Empty domain → empty world** — asserted in `domainWorld.test.ts`; the view
  renders an explicit "An empty world" card, never decorative nodes.
- **UNKNOWN never green** — `missionStatusColor` maps an unknown status to the
  `unknown` colour, asserted distinct from the `ok` colour.
- **Orphans are counted, not placed** — asserted.
- **GREEN 4778 untouched** — no backend process is started by this Wave.
