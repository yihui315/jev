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
| KD-003 | 2026-09-25 | Budget, `packages/jcc_core/state_store.py` `budget_snapshot` | The `directives` table holds **two rows per economic event** because two planes persist the same issued directive (engine `dir_<session>_<event>`, sentinel's charging copy `shadow_<session>_<event>`). `budget_snapshot` collapses them to one before deriving any counter, so the counters are correct; the duplication itself remains. | Removing the second writer changes `packages/supervisor/budget.py::_persist_directive`, which is outside `packages/jcc_core`. Collapsing the rows in the store is the part that is in scope and is sufficient to keep R1/R2 true. | `packages/jcc_core/tests/test_directive_identity.py` (all three measured scenarios) | `packages/supervisor` owner (sentinel) — root cause, for the follow-up |

## Notes on what is deliberately NOT recorded

* `model_units_used` reporting 0.0 is **not** a deviation: the `scorer_tokens`
  dimension is waived by design and the waiver is published through
  `BudgetSnapshot.waived_dimensions` and `is_enforced()`. See
  `packages/jcc_core/tests/test_p3_acceptance_gate.py::
  test_budget_scorer_tokens_is_inert_by_design`.
* A `directives` row written by either plane is not itself a defect; two planes
  observing the same event is expected. The defect was that the counters read
  both rows.
