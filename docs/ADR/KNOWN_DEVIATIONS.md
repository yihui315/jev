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
| KD-001 | 2026-09-25 | Control plane, `packages/jcc_core/server.py` `_manual` | The engine actuation port (`ActuationPort.dispatch`) has **two** call sites, not the one ADR-0008 D-41's frozen falsifier allows: `packages/jcc_core/core.py:311` (the engine's) and `packages/jcc_core/server.py:239` (`server._manual`). The second routes a control-plane manual directive (`executable=False`, `requires_human=True`) into the engine port from outside policy.**As re-characterised by erratum E-12-1 (2026-09-25): the deviation is the *call-site count against D-41's frozen falsifier wording*, and it is INERT — D-41 makes the engine port "permanently inert … never an attach point — in P3 or in P4+", so a real actuator can never be injected at `engine.actuator` however many places call it. It is therefore not a second attach point. It is also not a D-28 policy *bypass* in today's effect: the manual directive can never carry a signature.** The remedy (ADR-0012 §1/§Relationship — the endpoint becomes a verifier of a policy-signed directive) is **specified but unlanded**. | Making the code match the ADR changes the D-28 manual-plane contract (who signs, who arbitrates), and ADR-0012's replacement path does not exist in code yet, so there is nothing to reroute to. Both are protocol decisions, not implementation details. | `packages/jcc_core/tests/test_server_surface.py::test_engine_port_dispatch_call_sites_are_pinned` (shape) and `::test_the_manual_path_produces_no_execution_result` (consequence) — 5 passed, exit 0, 2026-09-25 | Protocol owner (ADR-0008) + platform owner (ADR-0012 implementer) — reported to the lead 2026-09-25 |
| KD-002 | 2026-09-25 | Control plane, `packages/jcc_core/server.py` `_manual` | Manual directives carry **no signature and no verified identity**: `directive_id` is server-minted (`dir_manual_<ms>`), `evidence_hash` is null, and `requires_human=True` is a self-declaration by the server rather than a checked claim. **Measured 2026-09-25 (erratum E-12-1, probes 2–3): the manual directive is not merely unsigned but permanently un-signable — `verify_directive` returns `UNSIGNED` both with no key and with a key supplied, so it can never become a trusted directive at any future attach point. `requires_human` however IS checked, not self-declared: a correctly HMAC-signed directive that self-reports it is still refused unless an approval exists for that exact `directive_id` (probes 4–6).** | ADR-0008 errata E-6 defers the signing half of D-28 to P4. The trust anchor (which key verifies a control-plane manual directive; enforced by default or not) is unspecified. | `packages/jcc_core/tests/test_server_surface.py::test_the_manual_path_produces_no_execution_result` — the path is inert, so no real side-effect is reachable today | Protocol owner (ADR-0008) + platform owner (ADR-0012 implementer) — reported to the lead 2026-09-25 |
| KD-003 | 2026-09-25 | Budget, `packages/jcc_core/state_store.py` `budget_snapshot` | The `directives` table holds **two rows per economic event** because two planes persist the same issued directive (engine `dir_<session>_<event>`, sentinel's charging copy `shadow_<session>_<event>`). `budget_snapshot` collapses them to one before deriving any counter, so the counters are correct; the duplication itself remains. | Removing the second writer changes `packages/supervisor/budget.py::_persist_directive`, which is outside `packages/jcc_core`. Collapsing the rows in the store is the part that is in scope and is sufficient to keep R1/R2 true. | `packages/jcc_core/tests/test_directive_identity.py` (all three measured scenarios) | `packages/supervisor` owner (sentinel) — root cause; the `origin` write (see KD-004) is what lets the two rows be told apart |
| KD-004 | 2026-09-25 | Persistence, `packages/jcc_core/state_store.py` `directives.origin` | The `origin` column (F1/F2) is **not back-fillable in place**: a database written before it existed has `origin NULL` on every row, and the row is then refused by the F2 gate (`NULL != origin`), because an unattributed row is the exact thing F1/F2 exist to refuse. Writing a pre-F1 database therefore requires a MIGRATION that assigns each existing row to a plane, and that decision (how to attribute historical rows, and whether any exist at all) is a protocol decision, not an implementation detail. | P4 ships without a migration path. The clause that used to justify this entry — *"every store in the tree and every test creates the schema fresh, so the refusal is unreachable today"* — is **measurably FALSE** and has been corrected (census 2026-09-25, atlas-3): **1 172** stores in this tree carry a `directives` table with **no `origin` column at all**, and **1 734** rows are NULL priors. What survives the measurement is a narrower claim: every one of those stores is a test artefact under a pytest basetemp root or `runtime/`, and the release evidence set holds **0** `.db` files, so no *admitted* store is affected today. The refusal is therefore **not unreachable** — it is never triggered in-tree, which is a different claim from the one this cell used to make. | `packages/jcc_core/tests/test_directive_identity.py::test_f2_a_cross_plane_overwrite_is_refused_and_leaves_the_row_alone` (the NULL-prior branch); **and** the executable pin `docs/tools/census_directive_origins.py --strict` (exit 1 while any unattributable row exists in the corpus), plus the falsifier requisition in the status note. | Protocol owner — reported to the lead 2026-09-25; measurement + non-closure 2026-09-25 (atlas-3) |
| KD-005 | 2026-09-25 | Operator skill, `packages/operator_skill/gates.py` `SignedDirectiveGate.issue` (mint site `gates.py:173`) | The operator-skill plane mints its **own** `Directive` outside the policy kernel: `params={"source": "operator-skill", "requires_human": True}`, `executable=False`, `shadow=True`, unsigned at issue and signed afterwards by `SignedDirectiveGate.sign`. This is the **third** production mint site in `packages/` (after `core.py:294`) and it was **not enumerated in this register** until 2026-09-25, although the register header claims every place where the code deviates is enumerated. It is **not** a deviation from ratified INV-41 — ADR-0012 erratum E-12-1 §E-12-1.5 clause 4 classifies the operator-skill gateway as an operator-intent record, which is what this code is. The deviation registered here is the **omission**, and with it the absence of any pin. | The site is intentional and shipped: it is the operator-skill gateway's request shape, and everything downstream of it is gated (capability → policy → signing → human approval → budget). It was omitted because the enumeration was done by hand and stopped at `packages/jcc_core`. | **UNPINNED — no test asserts the properties of this mint site.** `operator_skill/tests/test_gateway_refusal.py::test_budget_gate_absent_refuses` and `::test_budget_gate_exhausted_refuses` **exercise** `issue()` (at `test_gateway_refusal.py:175` and `:206`) but assert only the budget verdict; `test_negative.py:95-96` and `_helpers.py:328-329` construct the same shape inside a fixture without asserting the mint site produces it. **Run 2026-09-25:** `operator_skill/tests/test_gateway_refusal.py` **9 passed, exit 0.** Recorded unpinned rather than given a fake pin. | Protocol owner (`docs/ADR`) — removal requires a new ruling here |
| KD-006 | 2026-09-25 | Supervisor, `packages/supervisor/sentinel.py` `arbitrate_and_record` (mint site `sentinel.py:414`) | The supervisor plane mints its **own** `Directive` — `shadow=True`, `executable=False`, `params` carrying no `source` key — outside the policy kernel. This is the **fourth** production mint site in `packages/` and one of the **two the E-05 architecture leg did not count** when it established that `server.py` is a second mint site. Not enumerated here until 2026-09-25. As with KD-005 it is **not** a deviation from ratified INV-41 (clause 3/4: the supervisor plane produces shadow-plane records); the deviation registered is the **omission**. | The shadow plane records independently of the engine by design — KD-003 exists precisely because two planes persist the same issued directive — and the mint lives in `packages/supervisor`, which an enumeration scoped to `packages/jcc_core` never reached. | `supervisor/tests/test_supervisor.py::test_no_recorded_directive_is_ever_executable` — **5 passed, exit 0, 2026-09-25** (parametrised over 5 signal overrides). It pins the **consequence** of this mint site (every stored row has `acted == 0`, the D-40 falsifier in on-disk form) but does **not** pin the mint site's existence or its `params`; no test anywhere in the tree enumerates mint sites. The load-bearing half of this entry is pinned, the identity half is not. | Protocol owner (`docs/ADR`) — removal requires a new ruling here |

**Closure condition for KD-005 / KD-006 (protocol owner, 2026-09-25).** Both rows exist to keep the
register's enumeration claim true, so they may be removed only when the command below yields **exactly**
these four lines and no other production (non-test) construction site:

```
git grep -n "Directive(" -- 'packages/*/*.py' ':!*/tests/*' ':!*schemas.py'
```

Current output — **4 lines**, matching `packages/jcc_core/core.py:294`,
`packages/jcc_core/server.py:208`, `packages/operator_skill/gates.py:173`,
`packages/supervisor/sentinel.py:414`, all four listed in the table above. Verification run
2026-09-25. Until this holds, the two rows stay: dropping either without a new ruling here is exactly the
silent-resolution failure the register preamble forbids.

| KD-007 | 2026-09-25 | Actuation, `packages/jcc_core/actuator.py` `class Actuator` | The engine actuation port is inert **by convention, not by construction**. `Actuator` is an ordinary class with no `@final` and no `__init_subclass__` guard, and `CoreEngine.__init__` (`core.py:69`) stores it as a plain attribute, so nothing in the type system stops a subclass from overriding `dispatch`. D-41's *"permanently inert \u2026 never an attach point"* is therefore an assertion about how the code happens to be written, not an enforced invariant. | Recorded by the lead on the E-05 leg's residual finding, 2026-09-25. `packages/jcc_core/actuator.py:32`. Not funded now because `packages/` is the product freeze: sealing it would invalidate the security leg signed at the freeze and stop the release for a change that alters no behaviour today. The hardenings item is to be funded in the next release, where the same edit costs nothing. | **None.** No test pins this and none can, without a product change: it is a static observation about the absence of an enforcement mechanism. A deviation that names no anchor is recorded as such rather than given a fake anchor. | Protocol owner (ADR-0008) + platform owner -- reported to the lead 2026-09-25 |

## Ruling on KD-001 / KD-002, 2026-09-25 (protocol owner, `docs/ADR`): STILL OPEN — both re-characterised, neither closed, neither downgraded to "as designed"

Raised by the E-05 architecture leg, which returned NO-GO on the ground that
`server.py:206-239` breaks the invariant *"the policy kernel is the sole producer of directives"*.
Ruling issued as **ADR-0012 erratum E-12-1**. Summary here because a register that disagrees with the
ADR it registers is the drift this project fights; the full reasoning is in the erratum.

**The three measured findings that decide it.**

1. The invariant the leg NO-GO'd over is **not a normative statement in any ADR** — see finding F7 in
   erratum E-12-1 §E-12-1.2 for the corrected wording (an earlier draft of this register claimed the
   phrase appears "nowhere in `docs/`"; that was false at the moment it was written, because this very
   sentence introduces the phrase. **Correct claim: the invariant has no normative force and exists only
   as the paraphrase the register and the leg attached to KD-001.**), and `packages/` holds
   **four** production `Directive(...)` mint sites, not the two the leg counted — `gates.py:173`
   (operator-skill) and `sentinel.py:414` (supervisor) mint outside the policy kernel and were **not
   enumerated here** until **KD-005** and **KD-006** were added on 2026-09-25. So the claim was already
   false before the disputed site existed.
2. The deviation in **KD-001 is real** — D-41's frozen falsifier says `ActuationPort.dispatch` has
   *exactly ONE* call site (`core.py:143`) — but it is **inert**: D-41 makes the engine port never an
   attach point, so no real actuator can ever be injected there however many call sites it has.
3. **KD-002 is real and structural**: the manual directive is permanently un-signable, not temporarily
   unsigned.

**What changed in the two rows above.** Only the *characterisation* and the *owner* cells. Both entries
remain OPEN. KD-001's "D-28 policy bypass" phrasing was corrected to what is measured — the call-site
count against frozen text — because the register must not overstate today's effect while understating the
future risk. The "downgrade KD-001 to as designed" option offered at the NO-GO is **rejected**: the
register's own preamble forbids an entry being resolved silently, and the frozen text of D-41 currently
contradicts "as designed". What the erratum *does* provide is a **recommended restatement of the
invariant (INV-41)**, which is binding only on the lead's ratification; **if it is not ratified, D-41's
frozen wording governs and KD-001 stays a live deviation.**

**Boundary, so a future reader can classify the next mint site.** A mint-and-dispatch site is an
operator-intent record, not a machine directive, iff all three hold: (B1) the port is the P4 attach port
`DispatchPort` and the caller is `Sentinel._dispatch`; (B2) the directive carries `executable=False`; (B3)
`requires_human=True` and the approval gate is a **check** on that exact `directive_id`. **Do not
classify on `params["source"]`** — `core.py` sets no `params` and `sentinel.py` sets none with a `source`
key, so that test mis-classifies two of the four sites.

**Falsifier for this ruling.** Void if (i) any `ActuationPort.dispatch` call site returns a `reason`
other than `"shadow_mode"`; or (ii) any `Directive(...)` site sets `executable=True`; or (iii) a
construction site omits `executable=False` and its directive reaches a port; or (iv) a real actuation
implementation is reachable from anywhere other than `Sentinel._dispatch`. Not observed today.

**Not decided here** (protocol decisions still open, and outside `docs/`): deleting `server.py:239`, and
rerouting it through the Sentinel — the latter changes who signs what, and ADR-0012's verifier has never
been implemented, so there is no working alternative to route to today.

---

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

### Mint sites outside `packages/` — resolved 2026-09-25

The register had left this open. It is now closed by enumeration: `git grep -n "Directive("` over
`scripts/ tools/ policies/ adapters/ evals/ config/ apps/ facades/` returns hits **only** in test
code (`facades/*/tests/conftest.py`, `facades/*/tests/test_determinism.py`,
`tests/e2e/test_api_e2e.py`). Production `Directive(...)` mint sites outside `packages/` are
**zero**; inside `packages/` they are exactly four (`core.py:294`, `server.py:208`,
`gates.py:173`, `sentinel.py:414`), matching the KD-005 cell above. `gates.py:173` remains
UNPINNED, which is recorded rather than papered over.
