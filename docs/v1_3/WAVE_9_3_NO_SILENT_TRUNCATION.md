# WAVE 9.3 — NO SILENT TRUNCATION (and a hypothesis that did not survive its own measurement)

**Wave:** 9.3 (v1.3 Product Edition, branch `release/jcc-v1.3-final`)
**Order reference:** `@long-text` §AH item 10 ("real Chrome E2E / performance"),
which is what put a real browser in front of the plane and thereby found this.
**Rollback tag:** `jcc-v1.3-w9.3-rollback-before`
**A8 impact:** one pinned file moved (`packages/jcc_core/server.py`); `OPEN_GET_ENDPOINTS` 27 → 28

---

## 1. The defect, and the fact that only a browser could see it

While building the real-browser performance gate for the operational 3D plane
(Wave 10, same night), the gate needed a workspace large enough to be worth
measuring. Seeding 500 missions exposed this:

```
useDomain.ts:  api.missions({ limit: 200 }),
               api.deliverables({ limit: 200 }),
```

`limit: 200` is a *hardcoded client ceiling*. The API's own default is also 200.
So with 500 missions in the store:

* `GET /api/v1/domain/missions` returns 200 rows;
* the client stores those 200 as `missions`;
* `MissionsView` renders **"200 missions"**;
* `buildDomainWorld(projects, missions, …)` builds the plane from 200;
* the 3D world draws 200 bodies.

**Nothing said anything.** No error, no hint, no visual difference from a
workspace that genuinely holds 200 missions. A user with 500 missions — the
"real work operating system" scale the product exists for — would see 300 of
their missions silently missing and the product would look like it had lost them.

Why every existing gate was blind to it: they all assert on *shape*. The
pytest suites assert response schemas and status codes; the W2 UI gate asserts
the shell renders real persisted state (it does — the first 200 of it); the W6
world gate is a **structural** gate over the TypeScript source (it proves the
projection has no write path, not that it is complete). **No existing instrument
compared the size of what the product displayed with the size of what existed.**
The Wave 10 gate could, because it counts the plane's draw calls; that count is
the first thing in this project that had to disagree with the domain when the
product was truncating.

This is the same defect class the deliverables view already fixed once — its own
docstring records "capped at 50 missions and silently dropped the rest" — and the
same principle the attention badge states for counts: *a silent zero is how an
alert gets missed.*

## 2. The fix: keep the bound, make it VISIBLE

Making the lists unbounded would be the wrong fix. A bounded read is correct; an
unbounded one is its own defect (one query pulls the whole table into a
response). The defect was that **the bound was invisible**.

| Layer | Change |
|-------|--------|
| `packages/jcc_core/domain.py` | new `domain_counts()` — four `COUNT(*)`s, uncapped. `MAX_DOMAIN_LIST_LIMIT = 2000` becomes the ONE documented ceiling that both `list_missions` and `list_deliverables` clamp to (they previously had two silent ones: 200 and 1000). |
| `packages/jcc_core/server.py` | new open read `GET /api/v1/domain/counts` → `{"schema": "jcc/v1.3-domain-counts/1", projects, archived_projects, missions, deliverables}`. Added to `OPEN_GET_ENDPOINTS` (27 → 28) and to the declared set in `test_server_surface.py`. |
| `apps/…/src/app/domainApi.ts` | `counts()` (token-free) and `DOMAIN_LIST_LIMIT = 1000`, so the client's own ceiling is a decision instead of a coincidence with the server's default. |
| `apps/…/src/app/truncation.ts` | **new**: `classifyTruncation(shown, total, ceiling)` → `complete` \| `truncated` \| `at-ceiling` \| `unknown`, plus `truncationNote()`. |
| `apps/…/src/app/useDomain.ts` | fetches counts in the SAME `Promise.all` as the lists (so the two can never come from different moments by more than one poll), exposes `counts`, `missionsTruncation`, `deliverablesTruncation`. |
| `apps/…/src/ui/Feedback.tsx` | new `ScopeNote` — the disclosure line. |
| `MissionsView`, `OperationalWorldOverlay`, `DeliverablesView` | render it. The Missions headline now reads "200 of 500 missions"; the plane's overlay says its projection is partial. |

### The honesty rules the classifier encodes

1. **`unknown` is never `complete`.** If the counts read fails, the state is
   `unknown` and the note says "the total could not be read, so this list may be
   incomplete". Collapsing that to "complete" is precisely the original defect.
2. **`at-ceiling` is never `complete`.** A list of exactly `ceiling` rows with no
   total means the bound was reached and there may be more. Before this wave that
   case *was* the defect: 200 rows, unprovable.
3. **`complete` says nothing.** `truncationNote` returns `null` when the list is
   whole. A note on every screen becomes furniture, and furniture is what gets
   ignored on the screen where it matters.
4. **A longer list than the reported total is not "truncated".** A race between
   the two reads must not render a negative hidden count.

## 3. A hypothesis that did not survive its own measurement (and was reverted)

Domain writes measured **186 ms each**, which made seeding a large workspace
unusable. The first hypothesis: `DomainStore._conn()` re-issues
`PRAGMA journal_mode=WAL` on every connection while WAL is a *persistent*
property of the database file, so ~95 ms of negotiation was being paid per
operation.

A first measurement appeared to confirm it (94.87 ms for
`connect+pragma+close` versus 2.35 ms for `connect+close`) — but that
measurement changed **two** things at once: the pragma AND the file's existence
state (the "bare" timing ran against a brand-new file). Re-measured on a stable,
already-WAL database:

| Operation | ms/op |
|-----------|-------|
| `connect + close` | 2.41 |
| `connect + PRAGMA journal_mode=WAL + close` | 6.22 |
| `connect + insert + commit + close` | ~5 |
| `insert + commit` on a **shared** connection | 0.67 |

Then a `cProfile` of the real write path (30 × `create_mission`, 5.536 s total):

| Function | cumtime | per call |
|----------|---------|----------|
| `create_mission` | 5.536 s | 185 ms |
| **`sqlite3.Connection.close()`** | **4.926 s** | **82 ms × 60 calls** |
| `get_project` | 2.681 s | 89 ms |
| `_conn` | 0.541 s | 9 ms |
| `sqlite3.connect` | 0.132 s | 2.2 ms |

**`Connection.close()` is the cost: 82 ms, twice per write.** That is a
filesystem/IO property of this host (a machine with two consumer security
suites and a documented history of storage and memory instability), not a SQLite
setting. The pragma hypothesis was **wrong**, so the change was **reverted** —
including in the A8-pinned `state_store.py`, where the docstring now records the
measurement and the reason the "obvious" cleanup must not be repeated.

**Recorded as a P4 item, not fixed here:** the real fix is connection reuse (one
connection, or one per thread, per store) rather than open/close per operation —
which would also make `create_mission`'s two writes atomic, since today the
mission row and its creation event are committed separately. That is a
structural change to the storage layer, and a structural change to the storage
layer is not something to fold into a late-night wave.

This section exists because the repository's own rule is that a claim quoted
from memory is not evidence: the number, the instrument, and the reversal are
here so the next person does not re-derive the wrong conclusion.

### 3a. AMENDMENT (same night, later): the reverted hypothesis was re-measured on a live product database, and the conclusion above does not hold in that state

The table in §3 was measured on a **stable, already-WAL, freshly-created**
database. Wave 10's seed then put a real number on the state the product
*actually* runs in, and re-opening the question was unavoidable: the same host
showed 186 ms/write in §3 and **1.2 s/write** for the same call an hour later.

Re-measured on a **private copy of the live 400 KB product database** (WAL, with
`-wal`/`-shm` present, one process, one thread, no contention, no HTTP):

| Series (median of 5) | ms |
|---|---|
| `connect + close` | **26.9** |
| `connect + foreign_keys=ON + close` | 5.3 |
| `connect + synchronous=NORMAL + close` | **686.3** |
| `connect + journal_mode=WAL + close` | **410.9** |
| full `DomainStore._conn()` (all three pragmas) + close | 403.7 |
| the same, plus one `SELECT` | 539.2 |
| whole `get_project` (one open/close) | 437.9 |
| whole `domain_counts` (one open/close, 4 × COUNT) | 772.1 |
| whole `list_missions(1000)` | 1007.5 |
| whole `needs_attention` | 1100.3 |
| whole `create_mission` (**two** open/close pairs) | **934.8** |

A control with **no product code in the loop** (raw `sqlite3`, one table, four
file sizes 12 KB → 1.68 MB, three journal modes, bulk-filled so the row counts
are reached cheaply) established what the cost is *not*. Both this control and
the pragma decomposition below are **published and re-runnable** as
`tools/v1_3_domain_io_profile.py`:

| `journal_mode` | 12 KB | 36 KB | 1.68 MB |
|---|---|---|---|
| `delete` (rollback journal) | 106 ms | 227 ms | 299 ms |
| `memory` | 216 ms | 169 ms | 159 ms |
| `wal` | 0.9 ms | 0.9 ms | **1.1 ms** |

**The per-commit cost does not track the data size** — it is flat from 12 KB to
1.68 MB in every mode — so "the write path is O(n)" is refuted. What the control
does show is that on this host a *file-syncing* operation costs 100–700 ms and a
WAL append costs ~1 ms.

**What this changes about §3.** The pragma hypothesis was recorded as *wrong*
and reverted. On the state the product actually runs in, the two pragmas
`_conn()` re-issues per connection cost 410–686 ms while `connect + close` costs
27 ms — so the reversal in §3 was drawn from a sample that did not include the
database state the product ships against, and the "obvious cleanup" that §3's
last paragraph tells the next person not to repeat **is in fact the right
cleanup for this state**. The original numbers are left standing above rather
than edited: they were a real measurement of a real state, they are simply not
the state that matters.

Two things follow, and neither is being done tonight:

1. §3's attribution to `Connection.close()` (82 ms × 2) is **superseded**.
   `close()` measures 27 ms here. The dominant term is the pragma/sync work
   `_conn()` performs per connection.
2. The `_conn` docstring in `packages/jcc_core/state_store.py` — the A8-pinned
   file — carries §3's now-superseded conclusion and tells a future reader not
   to repeat the fix. Correcting that comment means moving an A8 pin, and a pin
   is not moved for a comment at freeze time; it is corrected in the same commit
   as the structural fix below, which re-baselines that pin anyway. Until then
   the comment is **known to be misleading**, and this amendment is the record
   that says so.

**Still recorded as a P4 item, not fixed here** — and now with hard numbers: the
fix is connection reuse (one connection, or one per thread, per store), which
makes the per-connection pragmas run once per process instead of once per
operation, and would recover ~0.4–1.1 s per domain operation on this host. It
also makes `create_mission`'s check and insert share one transaction. It is a
structural change to the storage layer — `StateStore.connection()` is a *public*
contextmanager whose contract is "a FRESH connection, always closed", and many
tests open the same database concurrently — so it needs its own wave with its own
falsifiers, not a late-night edit to a tree that is about to be frozen.

**A caveat on the numbers above, stated rather than hidden:** five repetitions
per series is enough to rank the costs, not to pin them. `synchronous=NORMAL`
alone (686 ms) measured *slower* than all three pragmas together (404 ms), which
cannot be a deterministic property and is a sign that these medians are
dominated by host fsync variance. The ranking is robust; the individual figures
are not, and nothing was changed on the strength of them.

### 3b. The magnitudes in §3a were ALSO over-read, and the reason is now measured

§3a's figures were taken while this session was running its own I/O probes
against the same disk, and they are a property of that contention rather than of
the product. The published profiler
(`tools/v1_3_domain_io_profile.py`) re-measures the same series on a quiet host
and the difference is large:

| Series | §3a (contended) | quiet host |
|---|---|---|
| `connect + close` | 26.9 ms | **1.6–1.9 ms** |
| `connect + journal_mode=WAL + close` | 410.9 ms | **4.8–6.5 ms** |
| full `_conn()` + close | 403.7 ms | **6.9 ms** |
| whole `get_project` | 437.9 ms | **6.8 ms** |
| whole `create_mission` | 934.8 ms | **194.1 ms** |

So §3a's reversal of §3 was directionally right (the exposure is the
per-connection work, not `close()`) and its magnitudes were wrong by up to 60×.
The tool now prints the spread beside every median, and
`docs/v1_3/WAVE_10_REAL_BROWSER_PERFORMANCE.md` §8 presents both conditions
side by side. **Three successive conclusions were drawn from this one cost and
only the third survived a controlled measurement** — which is itself the argument
for publishing the instrument and printing its spread rather than quoting a
single figure.

Published separately, because they are separate facts: the commit cost does not
track data size (raw control, three journal modes, 12 KB → 1.68 MB, flat), and
the HTTP layer contributes nothing (HTTP 178 ms vs direct 178 ms for the same
call in one process).

## 4. Tests

| Suite | Added | What it pins |
|-------|-------|--------------|
| `packages/jcc_core/tests/test_domain_counts_v1_3.py` (new, 7) | empty domain → zeros not unknown; counts follow the real domain; the list bound holds while the count does not; the ceiling is a sane documented number; one constant shared by both lists; the bound has a floor (`limit=0`/negative); archived ≠ active |
| `tests/e2e/test_domain_counts_v1_3.py` (new, 6) | the route is open + declared in `/api/health.open_get_endpoints`; counts match the real domain; **`test_a_truncated_list_is_DETECTABLE`** (bounded read says 2, totals say 5); archived reported separately; a second call is byte-identical (it is a read); schema |
| `apps/…/src/app/truncation.test.ts` (new, 11) | `unknown` ≠ `complete`; `at-ceiling` ≠ `complete`; empty ≠ unknown; complete renders nothing; singular/plural; the two-step and one-step forms agree |
| `apps/…/src/app/domainApi.test.ts` (+3 → 21) | `counts()` hits the open route with **no token**; the client asks for `DOMAIN_LIST_LIMIT`, not 200; a failed counts read surfaces as an error, never zeros (`data === null`) |

Frontend total: **94 passed** (was 80).

## 5. A8 / A11 / security-gate impact

* `packages/jcc_core/server.py` moved: `dbb661bbe0e3…` → `61a55d1d4902…`,
  recorded in `_BASELINE_HASHES` and in the `A8_FROZEN_AT` narrative.
* `packages/jcc_core/domain.py` is **not** an A8-pinned file, so its change moves
  no pin; the pin moves only because `server.py` gained the route.
* `OPEN_GET_ENDPOINTS` 27 → 28; the declared set in `test_server_surface.py`
  moved with it (a reviewed edit, which is what that test exists to force).
* **No POST route was added**, so the A11 exact set is unchanged.
* `artifacts/release/V1_1_SECURITY_GATE.json` re-emitted:
  `verdict=GO high=0 medium=0 low=0 ran=9/9`, `--check` clean.
  This was **required**, not optional: changing the route surface makes
  `tools/tests/test_v1_1_security_gate_artifact.py::test_check_mode_reports_the_artifact_is_current`
  fail by construction — observed, then fixed by re-emitting.
* `state_store.py`, `connections.py`: **byte-identical to the previous commit**
  (the reverted hypothesis above).

## 6. Deliberately NOT claimed

* **That the product now shows all missions.** It shows up to
  `DOMAIN_LIST_LIMIT = 1000` and **says so** when it is a partial view. Beyond
  1000 objects the honest answer is a paged view, which v1.3 does not have.
  The plane at 1000 objects is measured in Wave 10; the disclosure is what makes
  the remaining gap truthful rather than invisible.
* **That 186 ms/write is fixed.** It is measured, explained, and left as-is (§3).
* **A `total` on the list responses themselves.** The counts read is a separate
  endpoint precisely so no existing response shape changed, which kept the blast
  radius to one new route instead of a response-envelope migration across every
  view and test.
