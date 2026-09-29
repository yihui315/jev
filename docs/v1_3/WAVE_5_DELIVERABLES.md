# WAVE 5 — Deliverables

> v1.3 PRODUCT EDITION. The half of the product loop that turns *work* into an
> *outcome a human can accept*. Status: see commit + evidence at the end.
> Owner: Orion (design) / Forge (domain) / Atlas (API + aggregate) / Prism (UI) /
> Metric (tests + gate).

## 1. The hole this Wave closes

Wave 3/4 close the *left* half of the loop: a Mission launches a real agent,
telemetry flows through AEP, and when work blocks it lands in Needs Attention with
a control surface. But the product promise is not "the agent ran" — it is "you got
something you can use." That half is missing in four specific ways:

1. **A produced artifact can never be *accepted*.** `ArtifactStatus` has
   `VERIFIED` / `REJECTED`, but nothing in the codebase ever sets them. Every
   artifact is stuck at `PRODUCED` forever, so "deliverable" and "raw output" are
   the same thing. A user has no way to say "this one is real, ship it."
2. **A mission's output is not a first-class object.** Artifacts are rows that
   hang off a mission; the Deliverables screen fans out over missions client-side
   and sorts the pile. There is no server-side deliverable view, no package, no
   "what did this mission actually hand me."
3. **The Home counter lies by omission.** `recent_deliverables` counts **all**
   artifacts (`SELECT COUNT(*) FROM artifacts`), regardless of status. A pile of
   `REJECTED` artifacts counts the same as accepted ones. An aggregate that cannot
   distinguish accepted from rejected is not a deliverable metric.
4. **A deliverable has no identity to look up.** There is no
   `GET /artifacts/{aid}` — the only read is the per-mission list. A UI cannot link
   to one deliverable, and a human cannot open exactly what was produced.

Wave 5 makes a deliverable an object with an honest lifecycle, a server-side view,
a truthful aggregate, and an addressable identity.

## 2. The distinction this Wave hangs on

> **PRODUCED** and **VERIFIED** are different claims, and a system that renders
> them the same is claiming a human checked something nobody checked.

- **PRODUCED** — the agent *emitted* this. A fact about the machine. It is
  evidence that work happened, not that the work is good.
- **VERIFIED** — a *human* (or an independent check) looked at it and accepted it.
  A fact about judgement. It can be withdrawn (`REJECTED`) and it can be replaced
  (`SUPERSEDED`).

The whole point of the Deliverables screen is to move the first into the second,
and to be explicit that this is a human act. So:

1. **Acceptance is an explicit, recorded, human-gated act.** It writes
   `status`, `decided_by`, `decided_at`. It is never inferred from "the mission
   completed."
2. **A deliverable that was never verified is never shown as accepted.** The UI
   shows `PRODUCED` (neutral), `VERIFIED` (green), `REJECTED` (red),
   `SUPERSEDED` (grey) — four distinct states, none folded.
3. **The aggregate counts what it says.** `recent_deliverables` splits into
   `produced` and `verified`; the headline number is the *verified* one, because
   that is the one the user cares about and the one the system must not inflate.

Two corollaries, enforced in code:

- **An unverified deliverable cannot move a mission to COMPLETED on its own.** The
  mission state machine already gates that; Wave 5 does not touch it.
- **A verification needs no host prerequisite** (Wave 4 already treats human
  judgement as prerequisite-free) — but it *does* need the artifact to exist and
  not already be final.

## 3. The contract

### 3.1 Domain (`packages/jcc_core/domain.py`)

```python
def get_artifact(self, artifact_id: str) -> Optional[Artifact]: ...

def verify_artifact(self, artifact_id: str, status: ArtifactStatus, *,
                    decided_by: str, note: str = "") -> Artifact:
    """Record a human verdict on an artifact. Terminal states are final."""

def list_deliverables(self, *, status: Optional[ArtifactStatus] = None,
                      mission_id: Optional[str] = None,
                      limit: int = 200) -> List[Artifact]: ...
```

Rules:

- `verify_artifact` accepts only `VERIFIED` / `REJECTED` (a verdict, never back to
  `PRODUCED`). Deciding an already-final artifact raises (a verdict is final, same
  rule as Wave 4's approvals).
- The verdict is recorded in `provenance["verified_by"]` / `provenance["verified_at"]`
  / `provenance["verify_note"]` — we do not add columns for a fact that belongs to
  the artifact's own record, and `provenance` is already the agreed place for
  per-artifact evidence.
- `list_deliverables` is a server-side view: it joins artifacts to their mission
  so the row carries `mission_title` (the UI no longer fans out client-side).

### 3.2 Aggregate (`home_summary`)

`recent_deliverables` becomes an object, not a scalar:

```json
"deliverables": { "produced": 7, "verified": 3, "total": 7 }
```

The old scalar is kept as `deliverables_total` only if needed for compatibility;
the Home card reads `verified` as the headline and `produced` as the caveat. This
is a *breaking change to a counter's meaning* and is called out in the commit —
the previous number was counting rejected artifacts as deliverables.

### 3.3 API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/domain/deliverables` | open | server-side deliverable view (joined to mission) |
| `GET` | `/api/v1/domain/artifacts/{aid}` | open | one deliverable by identity |
| `POST` | `/api/v1/domain/artifacts/{aid}/verify` | token | **record a human verdict** `{status, decided_by, note}` |

`verify` is a real write behind the control token, not a preview-first control
(Wave 4 owns the preview-first pattern for *agent-affecting* actions); recording a
human verdict on an inert artifact cannot re-enter work, so it is a direct write —
but it is still token-gated and still audit-visible via the artifact record.

### 3.4 UI (`DeliverablesView`)

- Loads from the **server-side** `/deliverables` view (no client fan-out), so a
  mission past the old 50-mission cap is no longer silently dropped.
- Four status states rendered distinctly; a filter lets the user see only
  `produced` (awaiting judgement) or only `verified` (accepted).
- Each row offers **Accept** / **Reject** when the artifact is not yet final. This
  is the human act from §2; it is disabled with a reason when the control token is
  absent rather than shown as a dead green button.

## 4. Falsifiers

1. **A verdict can be faked**: assert `verify_artifact` on an unknown id raises,
   and that an already-final artifact cannot be re-decided.
2. **The aggregate inflates**: create N produced + M rejected + K verified; assert
   `deliverables.verified == K` and `produced == N+M` (rejected is still
   "produced-but-not-accepted"), and that the *headline* is `verified`.
3. **A rejected artifact is shown as a deliverable**: assert the server-side view
   with `status=VERIFIED` excludes rejected rows.
4. **A deliverable has no identity**: assert `GET /artifacts/{aid}` 404s on an
   unknown id and returns the exact artifact otherwise.
5. **Verification writes without a human**: assert the POST is refused without the
   control token (401/403).

## 5. Boundaries held

- Actuation stays disabled; verification is a record, never an effect on an agent.
- No demo data: an empty store yields an empty deliverable list, not a sample.
- `PRODUCED` is never rendered as accepted; four states stay four states.
- GREEN 4778 untouched.

## 6. Evidence

All numbers below are from real runs on the W5 working tree.

### 6.1 Wave-5 gate

| Field | Value |
|---|---|
| Runner | `tools/v1_3_wave5_deliverables_gate.py` (real v1.3 ASGI app + real HTTP) |
| Verdict | **PASS 17 / 17**, `failures: []` |
| Evidence JSON | `.workbuddy/scratch/v1_3_wave5_gate_20260929-081442.json` (untracked — `artifacts/` is gitignored) |
| sha256 | `d9bdd2cf57396b736d387caf676bf623e9535745945f5e76cf00555f346361e1` |

Key steps asserted: `DELIVERABLES_OPEN`, `DELIVERABLE_HAS_IDENTITY`,
`UNKNOWN_ID_IS_404`, `HOME_CARRIES_HONEST_DELIVERABLES`,
**`PRODUCED_IS_NOT_VERIFIED`** (creating an artifact does not move `verified`),
`VERIFY_REQUIRES_TOKEN`, `TOKENLESS_VERIFY_DID_NOT_LAND`, `VERIFY_NAMES_A_HUMAN`,
`VERIFY_RECORDS_WHO`, `VERDICT_IS_FINAL`, `VERIFIED_INCREASES_AFTER_ACCEPTANCE`,
`FILTER_EXCLUDES_UNVERIFIED`, `UNKNOWN_STATUS_IS_422`, `VIEW_CARRIES_MISSION_TITLE`,
`HOME_ACTUATION_FALSE`.

### 6.2 Tests (real tallies, clean temp dirs)

| Suite | Result |
|---|---|
| `packages/jcc_core/tests/test_deliverables_v1_3.py` | **15 passed** |
| `tests/e2e/test_deliverables_v1_3.py` | **11 passed** |
| `packages/jcc_core/tests` + `tests/e2e` (full, fresh `--basetemp`) | **743 passed, 0 failed, 0 errors** (495.29s) |
| `tools/tests` (full, fresh TEMP) | **247 passed, 0 failed** (464.08s) |
| A8 + server-surface gate | **55 passed** |
| Frontend `tsc --noEmit` | clean (exit 0) |
| Frontend `vitest run` | **54 passed** (8 files) |

### 6.3 A8 / A11 governance edits

- A8: exactly ONE pinned file moved — `packages/jcc_core/server.py`
  `32d2540d.. → 7845d2ad..` — recorded as window `V1.3-FREEZE-W-5-DELIVERABLES`
  in `docs/p3/A8_REBASELINE.json` (byte change and pin update in ONE commit).
- A11: inbound surface gains the verify POST; `OPEN_GET_ENDPOINTS` 24 → 26
  (`deliverables` and `artifacts/{aid}` are read-only and open).
  `test_server_surface.py` asserts `declared == served` (26 == 26).
- Security-gate artifact re-emitted: `artifacts/release/V1_1_SECURITY_GATE.json`,
  `verdict=GO high=0 medium=0 low=0 info=0 ran=9/9`, `[check] OK`
  sha `5a5ef85c0666efb434dcebea`. Wave 5 changed the route inventory, so the
  artifact was stale by construction; refreshed **in the Wave 5 commit**.

### 6.4 Commit / rollback

| Item | Value |
|---|---|
| Commit | `v1.3-wave5-deliverables` |
| Rollback point | `git tag jcc-v1.3-wave5-rollback-before → 6a46d1f` (Wave 4 commit) |

### 6.5 Boundaries verified

- **GREEN 4778 untouched** — no script here targets the protected service; the
  security gate's S-08 step (protected service and runtime DB untouched) is `ok=True`.
- **Actuation stays off** — `HOME_ACTUATION_FALSE` asserted; verification is a
  record, never an effect on an agent.
- **PRODUCED is never accepted** — `PRODUCED_IS_NOT_VERIFIED` asserted on the wire,
  and `path=VERIFIED` excludes unverified rows.
- **No demo data** — an empty store yields `{produced:0, verified:0, rejected:0,
  total:0}` and an empty list, not a sample.
