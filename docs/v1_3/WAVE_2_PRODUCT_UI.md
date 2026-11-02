# JCC v1.3 — WAVE 2: PRODUCT HOME + MISSION STUDIO

> **Status: ACCEPTED**
> Scope: `apps/command_center_3d` product UI layer (frontend only) + `tools/v1_3_wave2_ui_gate.py`.
> Backend (`packages/jcc_core`) **untouched** in this Wave — Wave 1 domain API is consumed as-is.

---

## 1. What this Wave changes (and what it does not)

v1.2.x shipped an "AI Agent **Visual** Command Center": the primary surface was a 3D scene
with a graph/table fallback. WAVE 2 inverts that. The product is now an **AI work operating
system**: an operator opens JCC to answer *"what work do I have, what is running, and what
needs me?"* — not to look at a 3D toy.

**Delivered:**

1. A real design system (shadcn/ui pattern) replacing the ad-hoc v1.2 CSS.
2. A 13-surface product navigation shell (sidebar + top bar + command palette).
3. A **Home** view that answers the operator's first question in under 2 seconds.
4. A **Mission Studio** that creates a real `Project` → `Mission` (DRAFT) → launch sequence
   against the Wave 1 domain API.
5. Read surfaces for Projects / Missions / Needs Attention / Deliverables / Audit /
   Diagnostics / Settings — all bound to real API data, none to demo fixtures.

**Not in scope (by design):**

- The 3D World surface retains its v1.2 implementation and is reachable via **legacy views**
  (`world` / `graph` / `table`). WAVE 6 rebuilds it. WAVE 2 does not regress it.
- No backend, schema, policy, or actuation change. `Actuation=False` unchanged.
- No fake data. Every domain surface shows `—` / UNKNOWN when the API has not answered.

---

## 2. Design system (the single source of styling truth)

Per the v1.3 order, one system and only one: **shadcn/ui pattern + Radix primitives +
Tailwind CSS + lucide icons + cmdk**. Ant Design / Material UI / Chakra are forbidden and
absent.

### 2.1 Dependencies added (all `--save-exact`)

| Layer | Packages |
|---|---|
| CSS engine | `tailwindcss@3.4.17`, `postcss@8.4.49`, `autoprefixer@10.4.20`, `tailwindcss-animate@1.0.7` |
| Class composition | `clsx@2.1.1`, `tailwind-merge@2.6.0`, `class-variance-authority@0.7.1` |
| Radix primitives | `@radix-ui/react-dialog`, `-label`, `-scroll-area`, `-select`, `-separator`, `-slot`, `-switch`, `-tabs`, `-tooltip`, `-dropdown-menu` (10) |
| Icons | `lucide-react@0.469.0` |
| Command palette | `cmdk@1.0.4` |

React, TypeScript and Vite remain the stack — no framework migration.

### 2.2 Theme tokens

`src/index.css` defines two HSL token sets on `:root` / `[data-theme="dark"]` (default) and
`[data-theme="light"]`. The dark set mirrors the v1.2 palette (`#0e1116` / `#d6dde6`) so the
legacy 3D surfaces keep their look.

Semantic tokens map 1:1 onto `tailwind.config.js`: `background`, `foreground`, `card`,
`popover`, `primary`, `secondary`, `muted`, `accent`, `destructive`, `border`, `input`,
`ring`, plus a `status` group.

### 2.3 The honesty rule is enforced at the token level

`--status-unknown: 212 14% 52%` — a **desaturated grey**, never green. `Badge`'s `unknown`
variant is the single place a non-answer is rendered, and it is grey by construction. This
is the mechanism behind the standing product rule:

> **UNKNOWN must never be displayed as CONNECTED / SAFE / VERIFIED / OK.**

There is no code path that can give `unknown` a positive tone, because
`status.ts::toneVariant(tone)` is a total 1:1 map and `unknown → "unknown"` badge.

---

## 3. Navigation shell

### 3.1 Single-source registry — `src/app/navigation.ts`

```
ViewMode = home | projects | missions | needs-attention | deliverables | studio
         | world | graph | table | connections | audit | diagnostics | settings   (13)
NavGroup = "Work" | "Control" | "System"
```

`NAV_ITEMS` is the one list the sidebar, the command palette and the view router all read.
Adding a surface is a one-line change; a surface that is in the registry but has no renderer
would be a visible gap rather than a silent one.

`coerceView(v, fallback="home")` — an unknown hash value **falls back to Home**, it never
renders a blank screen. `LEGACY_VIEWS = ["world","graph","table","connections"]` marks the
v1.2 surfaces that remain reachable but are not part of the primary product flow.
`showsInspectorRail(v)` gates the legacy inspector to those four only.

### 3.2 Shell components

| Component | Responsibility |
|---|---|
| `Sidebar.tsx` | Product rail from `navigation.ts`. Off-canvas at `< lg` with scrim. `AttentionCountBadge`: `null`/unknown → grey `?`, `0` → hidden, `>0` → warn badge. |
| `TopBar.tsx` | Title = `navLabel(view)`; connection badge; **actuation badge** (`false` → ok "Actuation OFF (fail-closed)", `true` → danger, `null` → unknown); control-token input (memory only); refresh; `data-testid="cta-new-mission"`. |
| `CommandPalette.tsx` | cmdk + Radix Dialog. Ctrl/⌘+K. Groups: destinations, actions, projects, missions, theme. |
| `App.tsx` | `useState(coerceView(readViewFromHash()))` + `hashchange` sync; theme → `documentElement[data-theme]` + localStorage; `<h2 class="sr-only" aria-live="polite">` announces the active surface. |

**View state lives in the App + URL hash**, not in a global store. The v1.2 `SceneStore`
remains the single source of truth *for the 3D scene only*, so the legacy surfaces keep
working unchanged while the product shell stays a plain React app.

### 3.3 Product shell components (`src/ui/`)

`Button` (cva: default/destructive/outline/secondary/ghost/link × default/sm/lg/icon,
`asChild` via Radix Slot) · `Card` (+Header/Title/Description/Content/Footer) · `Badge`
(cva incl. status variants) · `Field` (Label/Input/Textarea/Select — the Select is a
**styled native `<select>`**, deliberately not a JS dropdown: no portal race, no a11y
regression) · `Separator` · `Dialog` · `CommandPalette` · `Tooltip` · `Feedback`
(`Skeleton`, `EmptyState`, `InlineError`).

---

## 4. Product surfaces

### 4.1 Home — `HomeView.tsx`

The operator's first screen. Five stat cards bound to the domain home summary:

| testid | Meaning | Unknown rendering |
|---|---|---|
| `stat-active-projects` | Projects not archived | `—` (never `0`) |
| `stat-running-missions` | Missions in `RUNNING` | `—` |
| `stat-needs-attention` | Open attention items | `—` |
| `stat-deliverables` | Artifacts of kind deliverable | `—` |
| `stat-completed` | Terminally completed missions | `—` |

Crucially a **failed fetch renders `—`, not `0`**: `0` is a claim ("there are none"),
`—` is an honest non-answer. Plus: a Needs Attention list, and a System Health card
(Transport / Provider / Policy / Audit chain / Actuation / Scope).

### 4.2 Mission Studio — `MissionStudio.tsx`

Eight fields, stable ids for the gate: `msn-title` (required) · `msn-project` (required) ·
`msn-agent` · `msn-goal` · `msn-wd` · `msn-budget` · `msn-ctx` · `msn-perms`.

- Permissions are chips over `PERMISSION_CATALOG` (`fs.read` `fs.write` `shell.exec`
  `net.outbound` `git.commit` `test.run` `build.run`); `parsePermissions` de-dupes, trims,
  sorts.
- **Validation is pure** (`missionForm.ts`, unit-tested): a non-numeric budget is an error —
  it is not silently coerced to `0`. An empty working directory is `null`, not `""`.
- `LAUNCHABLE_AGENTS = ["workbuddy"]` — only WorkBuddy is launchable today; the other
  agents are selectable for planning but the launch path does not pretend they can run.
- **Launch is a preview-then-execute dialog** that shows the exact sequence it will perform
  (`DRAFT → READY → STARTING → RUNNING` + create AgentRun). `runLaunch` walks the sequence
  step by step and **stops at the first refusal** — it does not fast-forward, and it does
  not swallow the server's rejection reason.

### 4.3 Domain read surfaces

| View | Data source | Note |
|---|---|---|
| `ProjectsView` | `api.projects()` / `api.project(pid)` | Lazy expand loads missions per project. |
| `MissionsView` | `api.missions()` + `api.missionTimeline(id)` | Status filter chips; detail drawer shows the **server-authored** timeline, not a client reconstruction. |
| `NeedsAttentionView` | `api.needsAttention()` | `null` → unknown (never an empty list). Empty → genuine inbox-zero. **Read-only by design in WAVE 2** — no fake "resolve" button that would lie about a control plane that does not exist yet. |
| `DeliverablesView` | fan-out over `api.missionArtifacts(id)` | Per-mission failure is reported individually; one failure does not blank the page. |
| `AuditView` | `/api/audit` + `/api/audit/verify` | Chain verification result surfaced verbatim. |
| `DiagnosticsView` | 9 real endpoint probes | Reports real reachability per endpoint. |
| `SettingsView` | theme select, token reveal/clear, API base | Token is memory-only and never persisted. |

### 4.4 Domain API client — `src/app/domainApi.ts`

One client, one place that knows the wire format. `ApiResult<T>{ok,status,data,error}`;
`request()` uses an `AbortController` (15 s) and sends the token **only** in the
`X-JCC-Token` header — never in a URL, never in `localStorage`, never logged. A non-2xx
response returns `ok:false` with the server's `detail` preserved.

`src/app/useDomain.ts` is the read hook: home/projects/missions/attention, 5 s poll, and
`attention` **stays `null` until a successful fetch** — a failed poll cannot turn a live
inbox into "empty".

---

## 5. Live-honesty rules (product-level invariants)

1. **UNKNOWN ≠ green.** Enforced at the token layer (§2.3) and in `status.ts`.
2. **`—` ≠ `0`.** A missing datum renders `—`; only a real zero renders `0`.
3. **No demo data in a product surface.** Every value on screen traces to a domain API call.
4. **Launch does not over-claim.** Only `workbuddy` is in `LAUNCHABLE_AGENTS`.
5. **Needs Attention is not fake-actionable.** A control plane that does not exist is not
   rendered as if it did.
6. **`Actuation=False` is visible.** The top bar states it, and `true`/`null` are `danger`/
   `unknown`, never `ok`.

---

## 6. Tests and gate evidence

### 6.1 Frontend unit tests — 48 passed

`navigation.test.ts` (8) · `status.test.ts` (11) · `missionForm.test.ts` (10) ·
`domainApi.test.ts` (5) + v1.2 suites. Run with `vitest`.

> One **real defect** was found by these tests and fixed: `status.test.ts` originally
> asserted `["COMPLETED"].toContain("RUNNING")`. The assertion was wrong (`RUNNING` is
> correctly a live-green state); it was replaced with an explicit
> `green.sort() === ["COMPLETED","RUNNING"]` plus negative assertions
> (`NEEDS_ATTENTION→warn`, `FAILED→danger`) so the test now measures the invariant it names.

### 6.2 Wave 2 product UI gate — **PASS 23/23**

`tools/v1_3_wave2_ui_gate.py`. The gate boots a **real v1.3 ASGI app** on an ephemeral port
(`Config(server=ServerConfig(), state_store_db_path=str(db))` + `StateStore` +
`CoreEngine(store, build_provider(cfg), Actuator(enabled=False), cfg)` + `create_app`), serves
the built `dist/` from a `ThreadingHTTPServer` that **reverse-proxies `/api/*` same-origin**,
and drives **real Chrome** via Playwright.

Evidence: `.workbuddy/scratch/v1_3_wave2_ui_gate_20260929-064909.json`

```
verdict: PASS   passed: 23 / 23   failures: []
```

| # | Step | Observed |
|---|---|---|
| 1 | `BOOT` | ephemeral port answered `/api/v1/domain/home` |
| 2 | `BOOT_SCHEMA` | `jcc/v1.3-domain-home/1` |
| 3 | `API_CREATE_PROJECT` | `prj_871d0c93a47f4293` |
| 4 | `API_CREATE_MISSION` | created in `DRAFT` |
| 5 | `API_TOKEN_FAIL_CLOSED` | write without token → **403** |
| 6 | `CHROME` | Chrome 156.0.8072.0 (real channel) |
| 7 | `SHELL_SIDEBAR` | present |
| 8 | `SHELL_TOPBAR` | present |
| 9 | `HOME_DEFAULT` | Home lands first |
| 10 | `HOME_COUNTERS` | 5/5 |
| 11 | `HOME_CTA` | New Mission CTA |
| 12 | `LIVE_COUNTER_NONZERO` | `ACTIVE PROJECTS 1` — counts the API-created project |
| 13 | `LIVE_REAL_DATA` | API-created project renders on Projects |
| 14 | `LIVE_REAL_MISSION` | API-created mission renders |
| 15 | `LIVE_MISSION_STATE` | mission shows its real `DRAFT` state |
| 16 | `NAV_ALL` | 13/13 surfaces |
| 17 | `STUDIO_FORM` | 8/8 fields |
| 18 | `STUDIO_PROJECT_OPTION` | API-created project is selectable |
| 19 | `PALETTE_CTRLK` | palette opens |
| 20 | `THEME_ATTR` | `dark` |
| 21 | `A11Y_LANDMARKS` | `nav`/`header`/`main` |
| 22 | `RESPONSIVE_MOBILE` | navigation present at 420 px |
| 23 | `NO_CONSOLE_ERRORS` | **0** |

> An earlier run (`…064815.json`) is **FAIL 20/23** and is retained as the diagnostic trail;
> the three failures were the gate's own assertions, not the product:
> (a) `LIVE_REAL_DATA` asserted a project *name* on Home — Home shows counts, not entity
> names, so the assertion was split into `LIVE_COUNTER_NONZERO` + `LIVE_REAL_DATA`;
> (b) two assertions raced the initial fetch. Both were gate defects and were fixed in the
> gate, not masked in the product.

### 6.3 Regression — 79 passed

```
test_p3_acceptance_gate.py + packages/jcc_core/tests/test_domain_v1_3.py
  + tests/e2e/test_domain_api_v1_3.py            →  79 passed, 1 warning in 224.79s
```

This confirms WAVE 2 (which touched **no** `packages/` file) leaves the P3 acceptance gate
and the **A8 12-file hash pin** intact.

### 6.4 Build

`vite build` succeeds. Product chunk `357 KB` / `106 KB` gzip; three.js is isolated into its
own `936 KB` chunk so the product shell never pays for 3D; CSS `26.68 KB`.

---

## 7. Environment notes (reproducible build)

These are host-specific traps discovered while landing this Wave, recorded so the next
operator does not re-discover them:

1. **The npm shim is broken** — `D:/Program Files/nodejs/npm.cmd` raises `MODULE_NOT_FOUND`.
   Use the managed runtime:
   `C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-3/node.exe`
   with `node_modules/npm/bin/npm-cli.js` from the same tree.
2. **The default registry fails TLS** (`registry.npmmirror.com` →
   `ERR_SSL_DECRYPTION_FAILED_OR_BAD_RECORD_MAC`). `apps/command_center_3d/.npmrc` pins
   `registry=https://registry.npmjs.org/`. Install with `--fetch-retries 10`.
3. **vitest + the brokered-fs shim**: parallel workers writing the SSR transform cache
   raise `EPERM`, which vitest surfaces as an unhandled error and fails the run.
   Fix: `TEMP='F:\jev\apps\command_center_3d\.vitetmp'` (os.tmpdir honours `TEMP`) plus
   `test.fileParallelism:false` and `test.cache:false` in `vite.config.ts`.
4. **A Vite dev server cannot outlive a background shell here** — use a Python
   `ThreadingHTTPServer` to serve `dist/` (this is what the gate does, with an `/api/*`
   reverse proxy to get same-origin).
5. **`VITE_API_BASE=same-origin`** makes the bundle call relative `/api` paths. Without it
   the bundle falls back to `http://127.0.0.1:4779`, which is not where the gate's server
   lives (this caused 18 × 404 in an earlier smoke run).

---

## 8. Files

**Added**

```
apps/command_center_3d/.npmrc
apps/command_center_3d/postcss.config.js
apps/command_center_3d/tailwind.config.js
apps/command_center_3d/src/index.css
apps/command_center_3d/src/ui/{cn.ts,Button.tsx,Card.tsx,Badge.tsx,Field.tsx,
                                Separator.tsx,Dialog.tsx,CommandPalette.tsx,
                                Tooltip.tsx,Feedback.tsx}
apps/command_center_3d/src/app/{navigation.ts,status.ts,domainApi.ts,missionForm.ts,
                                Sidebar.tsx,TopBar.tsx,HomeView.tsx,MissionStudio.tsx,
                                ProjectsView.tsx,MissionsView.tsx,DeliverablesView.tsx,
                                NeedsAttentionView.tsx,SystemViews.tsx,useDomain.ts}
apps/command_center_3d/src/app/{navigation.test.ts,status.test.ts,
                                missionForm.test.ts,domainApi.test.ts}
tools/v1_3_wave2_ui_gate.py
docs/v1_3/WAVE_2_PRODUCT_UI.md
```

**Modified**

```
apps/command_center_3d/index.html          title → "JEV Command Center"; color-scheme; meta
apps/command_center_3d/package.json        dependencies (+ lockfile)
apps/command_center_3d/vite.config.ts      manualChunks (three|radix); test cache/parallelism
apps/command_center_3d/src/main.tsx        import "./index.css"
apps/command_center_3d/src/App.tsx         rewritten: shell + router + palette + theme
apps/command_center_3d/src/views/ConnectionsView.tsx   API base via prop; v1.3 label
```

**Removed**

```
apps/command_center_3d/src/components/Header.tsx   (git rm; superseded by TopBar+Sidebar)
```

---

## 9. Rollback

Rollback point tag: `jcc-v1.3-wave2-rollback-before`
(pre-Wave-2 commit `1be13ca` — `v1.3-wave1-domain`).

Because WAVE 2 is confined to one application directory plus one new gate script, rollback
is `git checkout jcc-v1.3-wave2-rollback-before -- apps/command_center_3d tools/` with no
database or schema implication.

---

## 10. Carried forward

- The **3D World** is still the v1.2 implementation. WAVE 6 owns its rebuild
  (hierarchy, deterministic layout, LOD/instancing, 60 FPS @ 200 / 30 FPS @ 1000).
- **Needs Attention is read-only.** WAVE 4 adds the control plane (policy-permitted vs
  currently-executable, blocked reasons, human approval for high-risk).
- **Deliverable kinds** are rendered but not yet modelled as first-class domain objects.
  WAVE 5 owns that.
- **Only WorkBuddy is launchable.** WAVE 3 proves the first real WorkBuddy mission end to
  end; WAVE 7 verifies the remaining three agents.
