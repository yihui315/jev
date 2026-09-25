# KNOWN DEVIATIONS register

Every place where the shipped code does not match a frozen ADR or spec ruling,
recorded with the evidence that makes it reviewable and an owner who may remove
it. A deviation is not a bug: a bug gets fixed in place. A deviation is a place
where the correct fix is a **protocol decision**, so the code ships as-is, the
discrepancy is pinned by tests (shape AND consequence), and this register is the
version-controlled, greppable record that the final release documentation can
cite.

No entry may be silently resolved by the implementer who found it. Removing an
entry requires the protocol owner's decision and a new ruling here.

Format is deliberately boring: the deviations are behavioural, not narrative.

| ID | Date | Area | Deviation | Why it ships | Pinned by | Owner (may remove) |
| --- | --- | --- | --- | --- | --- | --- |
| KD-001 | 2026-09-25 | Control plane, `packages/jcc_core/server.py` `_manual` | The engine actuation port (`ActuationPort.dispatch`) has **two** call sites instead of the one ADR-0008 D-41 allows: `packages/jcc_core/core.py` (the engine's) and `server._manual`. The second routes a control-plane manual directive into the actuation seam from outside policy — a D-28 policy bypass. | Making the code match the ADR changes the D-28 manual-plane contract (who signs, who arbitrates), which is a protocol decision, not an implementation detail. | `packages/jcc_core/tests/test_server_surface.py::test_engine_port_dispatch_call_sites_are_pinned` (shape) and `::test_the_manual_path_produces_no_execution_result` (consequence) | Protocol owner (ADR-0008) — reported to the lead 2026-09-25 |
| KD-002 | 2026-09-25 | Control plane, `packages/jcc_core/server.py` `_manual` | Manual directives carry **no signature and no verified identity**: `directive_id` is server-minted (`dir_manual_<ms>`), `evidence_hash` is null, and `requires_human=True` is a self-declaration by the server rather than a checked claim. | ADR-0008 errata E-6 defers the signing half of D-28 to P4. The trust anchor (which key verifies a control-plane manual directive; enforced by default or not) is unspecified. | `packages/jcc_core/tests/test_server_surface.py::test_the_manual_path_produces_no_execution_result` — the path is inert, so no real side-effect is reachable today | Protocol owner (ADR-0008) — reported to the lead 2026-09-25 |
| KD-003 | 2026-09-25 | Budget, `packages/jcc_core/state_store.py` `budget_snapshot` | The `directives` table holds **two rows per economic event** because two planes persist the same issued directive (engine `dir_<session>_<event>`, sentinel's charging copy `shadow_<session>_<event>`). `budget_snapshot` collapses them to one before deriving any counter, so the counters are correct; the duplication itself remains. | Removing the second writer changes `packages/supervisor/budget.py::_persist_directive`, which is outside `packages/jcc_core`. Collapsing the rows in the store is the part that is in scope and is sufficient to keep R1/R2 true. | `packages/jcc_core/tests/test_directive_identity.py` (all three measured scenarios) | `packages/supervisor` owner (sentinel) — root cause; the `origin` write (see KD-004) is what lets the two rows be told apart |
| KD-004 | 2026-09-25 | Persistence, `packages/jcc_core/state_store.py` `directives.origin` | The `origin` column (F1/F2) is **not back-fillable in place**: a database written before it existed has `origin NULL` on every row, and the row is then refused by the F2 gate (`NULL != origin`), because an unattributed row is the exact thing F1/F2 exist to refuse. Writing a pre-F1 database therefore requires a MIGRATION that assigns each existing row to a plane, and that decision (how to attribute historical rows, and whether any exist at all) is a protocol decision, not an implementation detail. | P4 ships without a migration path. The clause that used to justify this entry — *"every store in the tree and every test creates the schema fresh, so the refusal is unreachable today"* — is **measurably FALSE** and has been corrected (census 2026-09-25, atlas-3): **1 172** stores in this tree carry a `directives` table with **no `origin` column at all**, and **1 734** rows are NULL priors. What survives the measurement is a narrower claim: every one of those stores is a test artefact under a pytest basetemp root or `runtime/`, and the release evidence set holds **0** `.db` files, so no *admitted* store is affected today. The refusal is therefore **not unreachable** — it is never triggered in-tree, which is a different claim from the one this cell used to make. | `packages/jcc_core/tests/test_directive_identity.py::test_f2_a_cross_plane_overwrite_is_refused_and_leaves_the_row_alone` (the NULL-prior branch); **and** the executable pin `docs/tools/census_directive_origins.py --strict` (exit 1 while any unattributable row exists in the corpus), plus the falsifier requisition in the status note. | Protocol owner — reported to the lead 2026-09-25; measurement + non-closure 2026-09-25 (atlas-3) |

**Status note on KD-004 (protocol owner, 2026-09-25).** The protocol half is decided —
**ADR-0011**, the M1 backfill rule: attribute only `origin IS NULL` rows, derive the value through
`state_store.directive_plane_of` (the function F1 uses), and **never default** an unrecognised shape
to `engine`; refuse and report instead. Measured basis for the ruling:
Non-derivable rows do exist: an independent census by the lead refuted the "0 of unrecognised shape"
figure in the first draft of ADR-0011 (its method matched only files named `jcc.db`, which misses the
`m_*.db` / `live.db` / `det.db` stores the tree also holds). Correctly measured, the corpus holds
non-derivable rows whose ids are short synthetic fixture ids, not engine-shaped
`dir_<session>_<event>` ones. ADR-0011 records the correction; the ruling itself is unchanged.

The code half is **shipped** as of `973217f` (ADR-0011 M1,
`state_store.backfill_directive_origins`): operator-invoked only, no automatic
side effect, clauses 1-4 as ruled, pinned by
`test_directive_identity.py::test_m1_*` and by the contradiction nail that must
stay red. **The entry stays open anyway**: ADR-0011 states that no implementer
may close KD-004 without a new ruling here, and the ADR's own withdrawal
conditions (census showing zero NULL priors in every admitted store) have not
been measured against a released corpus. Also still open: M1 has never been run
against a real `jcc.db` — only against test fixtures.

*(Both of those have since been measured, on 2026-09-25 — see the ruling below.
The measurement did not close the entry; it produced numbers that keep it open.
This paragraph is left in place unedited so the history of the entry is not
rewritten by someone reading it later and assuming the questions were only ever
asked once.)*

---

### Ruling on KD-004, 2026-09-25 (protocol owner, atlas-3): **NOT CLOSED — and the measurement says so twice**

Both conditions that kept the entry open have now been **measured locally**, and
both measurements came back **negative for closing it**. This is not a waiver
and not a judgement call; it is the arithmetic of ADR-0011's own withdrawal
conditions against a corpus.

**Measurement 1 — the census (blocker (a)).** Run by
`docs/tools/census_directive_origins.py`, which walks **every** `*.db` on the
extension, opens each store **read-only**, and reports an unreadable file as an
error rather than as zero. Final stamped run on `F:\jev`, started
**2026-09-25 15:16:00 local (2026-09-25T07:16:00Z, epoch 1790320560.21)**,
command

```
.venv/Scripts/python.exe docs/tools/census_directive_origins.py \
    --root F:/jev --out .workbuddy/scratch/kd004-census-FINAL.json
```

sidecar `.workbuddy/scratch/kd004-census-FINAL.json`. It is one snapshot; a
rerun minutes later moves the row counts and may move the unattributable counts
(73 → 78 → 78 over ten minutes, from my own pytest runs) while leaving the
pre-F1 store count at **1 172** and the shape set at the same six ids. The table:

| measured | value |
|---|---|
| `*.db` files matched (by extension) | **1 541** |
| …of which named `jcc.db` | 1 313 |
| stores with a `directives` table | 1 498 |
| **stores with NO `origin` column (genuinely pre-F1)** | **1 172** |
| stores whose schema IS fresh (post-F1) | 326 |
| stores with no `directives` table | 43 |
| directive rows | 2 349 |
| …of which sit in the 1 172 pre-F1 stores | 1 706 |
| rows with `origin IS NULL` (NULL priors) | **1 734** |
| rows already carrying a stored origin | 615 |
| **unattributable rows** (`directive_plane_of` → `None`) | **78**, in **68** stores |
| unreadable files (counted as errors, not skipped) | **0** |
| `.db` files under `artifacts/` (the release set) | **0** |
| stores with a non-empty `-wal` sibling | 4 |
| `census_directive_origins.py --strict` exit code | **1** |

The unattributable shapes this run: `dir0`, `esc0`, `rt0`, `zog_s_1`,
`weird_s-x_e1`, `oops_s-m1_e1`. All three runs of this census (1 491 / 1 519 /
1 541 files, ~6 minutes apart) saw the **same six shapes**; only the row counts
moved (73 → 78 → 78), because the corpus is written concurrently by other
people's pytest runs. The **store count 1 172 was identical in all three runs**
and is the stable structural fact; the row counts are snapshots, which is why
every number here is stamped with the run's start time rather than stated bare.

**Reconciliation of the 14 vs 54 vs 78 spread.** It is explained, with numbers,
by the method and not by churn. Of the 78 unattributable rows in this final
run, **72 sit in stores whose filename is NOT `jcc.db`** and only 6 in stores
named `jcc.db`; 62 of the 68 affected stores are non-`jcc.db` names. A census
that matches the *filename* `jcc.db` — the method of the first ADR-0011 draft —
therefore cannot see 92 % of the phenomenon it was measuring. That is the
mechanical explanation for the withdrawn "0 unrecognised rows", and it is why
the standing method note is *match `*.db`, never a filename*.

**Measurement 2 — M1 against a real pre-F1 database (blocker (b)).**
`backfill_directive_origins` was executed against three REAL pre-F1 stores,
each one **copied into `.workbuddy/scratch/kd004-m1/` first**; the original was
hashed before and after and was byte-identical in all three cases. Targets were
picked by the census, not by hand:

| target | rows | NULL priors | attributed | refused | admissible | run 2 |
|---|---|---|---|---|---|---|
| **A** `runtime/jcc/_lead_verify.db` — the only store in the tree not under a pytest root, predating the F1 commit `b29d587` | 3 | 3 | **3** (all `engine`) | 0 | True | attributed=0 → no-op |
| **B** largest pre-F1 store (16 rows, no unattributable rows) | 16 | 16 | **16** (8 `engine`, 8 `supervisor`) | 0 | True | attributed=0 → no-op |
| **C** largest pre-F1 store **with** unattributable rows | 2 | 2 | **0** | **2** (`dir0`, `esc0`) | **False** | still 0, still refused |

Two distinct idempotencies are measured separately, because conflating them is
how a real defect hides behind the word "idempotent":

* **Row-level (the safety property): HOLDS on all three targets.** The
  `{directive_id: origin}` map is identical after run 1 and run 2, and run 2
  attributes nothing. No row ever gains or changes an origin however often M1
  runs.
* **Report-level: HOLDS on A and B, and *does not* hold on C — correctly.** On
  C the second run re-lists `dir0`/`esc0` under `refused`. That is not a defect:
  the refusal is a property of the **data**, so an *empty* `refused` on run 2
  would mean M1 had started attributing rows it cannot attribute. A
  no-op-for-the-wrong-reason would be worse than the visible re-report.

After M1, no row anywhere among the three targets contradicts its own id prefix.

**Why the entry does not close.** ADR-0011's withdrawal conditions are three
"all must hold, and all are measured" conditions. Condition 1 (zero NULL priors
in every admitted store) is measured **false**: 1 734 NULL priors, and
`--strict` exits 1. Condition 2 (every pre-F1 corpus exhausted — each either
migrated with an empty `refused` list, or proven to hold zero rows) is measured
**false** independently of how condition 1 is scoped: **47** pre-F1 stores
contain rows M1 can never attribute, and target C above shows M1 refusing them
and marking its own report inadmissible. So the entry stays open on the ADR's
own terms, and the data agrees. (Condition 3 — nothing in the release set cites
a derived value as attribution — has **not** been measured by me; the release
set holds no `.db` file at all, so it needs the release owner, not the census.)

**The falsifier.** Any one of these observations, if it were made instead of the
above, would falsify the conclusion that M1 is safe to run on a real pre-F1
store:

> After `backfill_directive_origins` has run on a real pre-F1 database, a row
> whose `directive_plane_of(directive_id)` is `None` reads a non-NULL
> `origin` — i.e. a row listed under `refused` later shows an attribution, or a
> second run reports `attributed > 0`.

Measured against target C: `{dir0: None, esc0: None}` after two runs, with
`attributed == 0` on both. Not falsified. `docs/tools/census_directive_origins.py
--strict` is the executable form of the same falsifier and exits 1 on the
current corpus, which is the measured reason the entry cannot close.

**The falsifier was itself falsified — i.e. shown able to go red.** A falsifier
that has never been observed failing is decoration. With the rejected ADR-0011
default installed (the module-level `directive_plane_of` monkeypatched to return
`"engine"` for an unrecognised shape, which is the "engine, best-effort" clause
without editing any file), the same fixture migrates to
`{"dir0": "engine"}` — so the assertion above would go red. The production code
was not touched by this: the patch was applied in-process and reverted, and
`git diff HEAD -- packages/` is empty.

**Source-level pin — one gap I cannot close from `docs/`.** The invariant the
falsifier describes is *not* yet asserted in `packages/`, and the only place it
belongs is the one file ADR-0011 closes new M1 nails to,
`test_directive_identity.py`. **Scope: I own `docs/`, not `packages/`** (hard
instruction), and ADR-0011 also rules that M1 falsifiers have exactly one owner
so that a pin cannot be edited by whoever needs it to pass. So this is
**drafted, not landed**: hand to `forge` (owner of the tests) the single
assertion below.

```python
def test_a_refused_row_never_carries_an_origin_after_m1(tmp_path):
    """Falsifier for KD-004 (atlas-3, 2026-09-25).

    An unattributable row may be REFUSED, never attributed. Green here is a
    REGRESSION SIGNAL: it goes red if M1 has started stamping a shape it cannot
    read, which is ADR-0011 clause 3 fabricated with the migration's own hands.
    """
    from jcc_core.state_store import (
        StateStore, backfill_directive_origins, directive_plane_of)

    s = StateStore(str(tmp_path / "c.db"))
    with s.connection() as conn:
        conn.execute(
            "INSERT INTO directives (directive_id, session_id, timestamp, action) "
            "VALUES (?,?,?,?)", ("dir0", "s-c", 1.0, "PAUSE"))
        conn.commit()
    backfill_directive_origins(s.db_path)
    backfill_directive_origins(s.db_path)          # twice: a re-run must not fabricate

    with s.connection() as conn:
        origins = dict(conn.execute(
            "SELECT directive_id, origin FROM directives").fetchall())
    assert directive_plane_of("dir0") is None, "the fixture must be unattributable"
    assert origins == {"dir0": None}, (
        f"an unattributable row was attributed: {origins}; ADR-0011 clause 3 "
        "refuses, and never falls back to 'engine'")
```

The M1 suite that already exists was re-run green after this measurement:
`packages/jcc_core/tests/test_directive_identity.py` **29 passed** (91.44 s) and
`test_aegis_p4_review_82.py` **18 passed** (22.46 s), including the contradiction
nail that must stay red. `packages/` was **not modified** — `git diff HEAD --
packages/` is empty, and the working-tree sha256 of `state_store.py`
(`b6667435…`) is the already-recorded mid-chain re-anchor value
(`old 399b0899… -> new b6667435…`), not a drift introduced here.

## Notes on what is deliberately NOT recorded

* `model_units_used` reporting 0.0 is **not** a deviation: the `scorer_tokens`
  dimension is waived by design and the waiver is published through
  `BudgetSnapshot.waived_dimensions` and `is_enforced()`. See
  `packages/jcc_core/tests/test_p3_acceptance_gate.py::
  test_budget_scorer_tokens_is_inert_by_design`.
* A `directives` row written by either plane is not itself a defect; two planes
  observing the same event is expected. The defect was that the counters read
  both rows.
* The suite's pass/fail numbers depend on the environment, and that is recorded
  as a run precondition, not as a deviation of the product:
  ``CODEBUDDY_SAFE_DELETE_ENABLED=0`` in `packages/conftest.py`. With the
  machine's safe-delete guard on, the same tree measures "0 failed, N errors"
  because the guard interrupts pytest's own `tmp_path` setup. The guarded run
  now ABORTS with ``pytest.UsageError`` (exit 4) instead of reporting those
  tests as skipped, so the configuration cannot read as a pass.
