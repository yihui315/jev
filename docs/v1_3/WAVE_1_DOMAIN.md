# JCC v1.3 Wave 1 — Project / Mission Domain

**Owner:** Orion (architecture) + Forge (Python core) + Atlas (migrations/API)
**Status:** LANDED — domain module + API surface + 47 tests green; A8/A11 pins deliberately re-baselined

## 1. Why this wave exists

JCC v1.2 was a *session viewer*: agents ran, telemetry arrived, and the UI showed
sessions. A **work operating system** needs a unit of work the operator creates
before any agent exists. That unit is the **Mission**, and it lives under a
**Project**.

Wave 1 adds the smallest domain that makes the product loop possible:

```
Project -> Mission -> AgentRun -> Session (existing)
                    -> Artifact
           Connection -> Capability
           Mission   -> Approval
```

## 2. The seven entities

| Entity | Table | Notes |
|--------|-------|-------|
| **Project** | `projects` | name, root_path, description, archived |
| **Mission** | `missions` | goal, agent, working_directory, context, permissions, budget, status |
| **AgentRun** | `agent_runs` | one launch of a Mission; carries the Session id |
| **Connection** | `domain_connections` | agent link; stores the secret **env var NAME**, never a value |
| **Capability** | `capabilities` | measured per (connection, kind); UNIQUE |
| **Approval** | `approvals` | PENDING → APPROVED/REJECTED/EXPIRED; decision is final |
| **Artifact** | `artifacts` | kind, sha256, bytes, status, provenance → Deliverable |

Plus `mission_events`: an append-only transition log so the timeline is
**derivable from the store** — the frontend never has to remember a transition
it did not see.

## 3. Mission state machine (authoritative in Python)

Ten states, constrained by `MISSION_TRANSITIONS`:

```
DRAFT ──► READY ──► STARTING ──► RUNNING ──┬─► NEEDS_ATTENTION ─┬─► COMPLETED
                                           ├─► PAUSED ──────────┤
                                           ├─► VERIFYING ───────┤
                                           └─► FAILED / CANCELLED
```

Load-bearing properties, each with a falsifier:

- **`DRAFT → RUNNING` is refused.** The launch handshake (READY → STARTING) cannot
  be skipped, so "launched" can never be claimed without the launch step.
- **An illegal transition leaves the row untouched** — and records nothing in
  `mission_events` (a refused attempt is not history).
- **Terminal → only DRAFT** (an explicit re-open), never an automatic retry.
- **CANCELLED is reachable from every live state.**
- **The server enforces the same rules** (`MissionStatus(to)` + `validate_transition`),
  so a client cannot invent a state the domain refuses.

`AttentionKind` gives NEEDS_ATTENTION an honest reason: APPROVAL_REQUIRED,
AGENT_STUCK, POLICY_ESCALATION, VERIFICATION_REQUIRED, BUDGET_WARNING,
CONNECTION_LOST, DELIVERABLE_READY.

## 4. Capability gate — the only door to effectful action

`is_control_verified(connection_id)` returns `True` **only** for
`CONTROL_VERIFIED`, and `False` for a missing row (UNKNOWN), `TELEMETRY_ONLY`,
`ADAPTER_CONTRACT_LIVE`, and `UNAVAILABLE`. Seeing events and speaking the
adapter contract are not the same as having exercised a control path.

## 5. Additive migrations (no v1.2 database is broken)

`DOMAIN_SCHEMA` is `CREATE TABLE IF NOT EXISTS` only — no `ALTER`, no `DROP`.
A v1.2 database opens with its `sessions` / `events` / `assessments` /
`directives` / `audit_tape` tables byte-identical, and gains the domain tables
beside them. `test_domain_schema_is_additive_on_an_existing_v1_2_db` builds a
v1.2-shaped database, opens `DomainStore` on it, and asserts the old tables
survive.

## 6. API surface

**Open GET (no token):** `/api/v1/domain/home`, `/projects`,
`/projects/{pid}`, `/missions`, `/missions/{mid}`, `/missions/{mid}/timeline`,
`/missions/{mid}/runs`, `/missions/{mid}/artifacts`, `/connections`,
`/needs-attention`.

**Token-gated POST:** create project/mission, transition mission, create run,
add artifact, upsert connection, set capability, create/decide approval.

Every domain write was verified to return 401/403 without a token, for all nine
routes (`test_every_domain_write_is_token_gated`).

## 7. Test evidence

| Scope | File | Result |
|-------|------|--------|
| Domain model + state machine + migrations | `packages/jcc_core/tests/test_domain_v1_3.py` | **30 passed** |
| API over real HTTP | `tests/e2e/test_domain_api_v1_3.py` | **17 passed** |
| Full e2e suite (no regression) | `tests/e2e` | **51 passed** |
| A8/A11 + surface pins | `test_p3_acceptance_gate.py`, `test_server_surface.py` | **32 + 23 passed** |

## 8. Two pinned invariants deliberately updated (and one latent defect fixed)

Adding a domain surface *must* move two invariants. Both were updated with the
new value spelled out, never by loosening the assertion:

1. **A11 inbound surface** — was `{assess,pause,steer,retry}`; now enumerates all
   21 POST routes exactly. Still an exact set, so a new route cannot appear
   without a reviewed edit.
2. **Declared open surface** — was 8 entries; now 21. The anti-drift test
   (`declared == served`) still holds.

**Latent defect found and FIXED (not exempted).** `test_actuator_execute_has_no_call_site_in_the_product_tree`
was failing on 11 `c.execute` lines in `packages/jcc_core/connections.py` (v1.2.1,
commit `ae3946c`). The D-41 AST scanner allowlists receiver names
`conn/cur/cursor/real/db/sqlite/snapshot`; `connections.py` used the bare name
`c`, which the scanner did not recognise — a **false positive that looked like a
real actuator call site**. It was invisible because `packages/jcc_core/tests` was
not a v1.2 gating scope. Fix: renamed the SQLite receiver `c → conn` (17 sites,
no behaviour change). The tripwire now reads **zero** non-DB `.execute` call
sites, which is what the invariant actually claims.

## 9. A8 re-baseline

`packages/jcc_core/server.py` `old 7d16a3f8.. → new f4c01f35..` — Wave 1 wires
`DomainStore` and adds the `/api/v1/domain/*` surface. Recorded in `A8_FROZEN_AT`
with the reason, as the pin requires.

## 10. Rollback point

`jcc-v1.3-wave1-rollback-before` → parent of the Wave 1 commit.
