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
| KD-004 | 2026-09-25 | Persistence, `packages/jcc_core/state_store.py` `directives.origin` | The `origin` column (F1/F2) is **not back-fillable in place**: a database written before it existed has `origin NULL` on every row, and the row is then refused by the F2 gate (`NULL != origin`), because an unattributed row is the exact thing F1/F2 exist to refuse. Writing a pre-F1 database therefore requires a MIGRATION that assigns each existing row to a plane, and that decision (how to attribute historical rows, and whether any exist at all) is a protocol decision, not an implementation detail. | P4 ships without a migration path; every store in the tree and every test creates the schema fresh, so the refusal is unreachable today. | `packages/jcc_core/tests/test_directive_identity.py::test_f2_a_cross_plane_overwrite_is_refused_and_leaves_the_row_alone` (the NULL-prior branch) | Protocol owner — reported to the lead 2026-09-25 |

**Status note on KD-004 (protocol owner, 2026-09-25).** The protocol half is decided —
**ADR-0011**, the M1 backfill rule: attribute only `origin IS NULL` rows, derive the value through
`state_store.directive_plane_of` (the function F1 uses), and **never default** an unrecognised shape
to `engine`; refuse and report instead. Measured basis for the ruling:
1 648 pre-F1 rows in this tree, 1 452 `dir_…` + 196 `shadow_…`, **0** of unrecognised shape.

The code half is **shipped** as of `973217f` (ADR-0011 M1,
`state_store.backfill_directive_origins`): operator-invoked only, no automatic
side effect, clauses 1-4 as ruled, pinned by
`test_directive_identity.py::test_m1_*` and by the contradiction nail that must
stay red. **The entry stays open anyway**: ADR-0011 states that no implementer
may close KD-004 without a new ruling here, and the ADR's own withdrawal
conditions (census showing zero NULL priors in every admitted store) have not
been measured against a released corpus. Also still open: M1 has never been run
against a real `jcc.db` — only against test fixtures.

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
