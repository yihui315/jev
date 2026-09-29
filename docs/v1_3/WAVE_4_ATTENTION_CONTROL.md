# WAVE 4 — Needs Attention + Control

> v1.3 PRODUCT EDITION. Status: **ACCEPTED** (see commit + gate evidence at the end).
> Owner: Orion (design) / Forge (resolver) / Atlas (coupling + API) / Prism (UI) / Metric (tests+gate).

## 1. The hole this Wave closes

Wave 2 shipped an inbox that is honest but inert: it lists every `NEEDS_ATTENTION`
mission with its kind and reason, and it says so out loud ("Nothing on this screen
mutates state"). That was the correct first move — a button that *looked* like it
resolved an item while only changing local React state would be a lie.

Wave 3 then made the inbox *populated by real work*: a real launch that fails
mid-walk now lands a mission in `NEEDS_ATTENTION` with a reason (D-W3-1/2/3 all do
this). So the inbox has real content and no way to act on it.

Wave 4 makes the inbox a **control surface** — but the whole point is *how*. A
control surface for an autonomous system is dangerous precisely where it is
convenient. This Wave is therefore built around one distinction that the rest of
the design follows from.

## 2. The one distinction everything hangs on

> **POLICY PERMITTED** and **CURRENTLY EXECUTABLE** are different questions, and
> collapsing them into one green button is the defect this Wave exists to prevent.

- **POLICY PERMITTED** — does this action *exist* in the deterministic policy
  vocabulary? It is a fact about the *rules*, computable offline, and it does not
  change while you look at the screen.
- **CURRENTLY EXECUTABLE** — do the *prerequisites really hold on THIS host right
  now*? It is a fact about the *world*: is there actually a live session to resume,
  is there actually a PENDING approval to decide, is the connection's capability
  actually `CONTROL_VERIFIED`.

A mission can be **policy permitted but not currently executable** (e.g. "resume
the run" is a legal action, but the agent's session died and nothing is live to
resume). Rendering that as a green "Resume" button would be a lie of exactly the
kind this project keeps hunting. So the UI shows **two separate signals**, never
one.

Two corollaries, enforced:

1. **UNKNOWN is never permitted and never executable.** If the resolver cannot
   determine a prerequisite (the probe could not run, the connection is in an
   unknown state), the answer is `UNKNOWN`, rendered neutral — never green. This is
   the "探针三态" discipline: *cannot determine* must not fold into *fine*.
2. **Every blocked action carries a `blocked_reason` from a closed vocabulary.**
   Free text cannot be rendered honestly (the UI cannot know what to highlight),
   so the resolver returns an enum, and the UI maps it to copy.

## 3. The contract (`packages/jcc_core/attention_control.py`)

Pure functions over data the store already holds — no I/O in the resolver, so its
truth table is unit-testable and cannot drift from a live probe.

```python
class Executability(str, Enum):
    EXECUTABLE = "EXECUTABLE"          # prerequisites hold NOW
    BLOCKED = "BLOCKED"                # a known prerequisite is missing
    UNKNOWN = "UNKNOWN"                # could not be determined -- never green

class BlockedReason(str, Enum):
    NO_LIVE_SESSION = "NO_LIVE_SESSION"
    NO_PENDING_APPROVAL = "NO_PENDING_APPROVAL"
    CONNECTION_NOT_CONTROL_VERIFIED = "CONNECTION_NOT_CONTROL_VERIFIED"
    CONNECTION_UNKNOWN = "CONNECTION_UNKNOWN"
    ACTUATION_DISABLED = "ACTUATION_DISABLED"
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    NOT_IN_ATTENTION = "NOT_IN_ATTENTION"   # mission is not NEEDS_ATTENTION

class AttentionAction(BaseModel):
    action: str                 # closed verb: resume | decide_approval | verify |
                                #               acknowledge_deliverable | reconnect
    label: str
    risk: str                   # "low" | "high"  (high -> needs an approval step)
    policy_permitted: bool
    executability: Executability
    blocked_reason: Optional[BlockedReason]
    detail: str                 # human note; NEVER the only signal
```

`resolve_attention(items, context) -> list[AttentionResolution]` where

```python
context = {
    "live_session_ids": set[str],       # from workbuddy_launch.liveness == live
    "pending_approval_ids": set[str],
    "connection_capability": {cid: "CONTROL_VERIFIED" | "TELEMETRY_ONLY" | "UNKNOWN"},
    "actuation_enabled": bool,          # False in v1.3, always
}
```

### 3.1 Per-kind action table

| Attention kind | Candidate action | policy_permitted | executable when | risk |
|---|---|---|---|---|
| `APPROVAL_REQUIRED` | `decide_approval` | yes | a PENDING approval exists for this mission | low |
| `AGENT_STUCK` | `resume` | yes | a live session exists for the mission's run | **high** (re-enters work) |
| `POLICY_ESCALATION` | `resume` | yes | a live session exists | **high** |
| `VERIFICATION_REQUIRED` | `verify` | yes | always (a human check needs no prerequisite) | low |
| `BUDGET_WARNING` | `resume` | yes | a live session exists | **high** |
| `CONNECTION_LOST` | `reconnect` | yes | connection capability is `CONTROL_VERIFIED` | low |
| `DELIVERABLE_READY` | `acknowledge_deliverable` | yes | always | low |

Rules that fall out of the table:

- `actuation_enabled=False` (v1.3, always) forces every **high-risk** action to
  `BLOCKED` with `ACTUATION_DISABLED`. The inbox will therefore show "policy
  permitted, blocked: actuation disabled" for resumable work — which is the honest
  state of v1.3, not a bug.
- An action on a mission that is **not** `NEEDS_ATTENTION` returns
  `BLOCKED / NOT_IN_ATTENTION` — so a stale UI cannot act on an already-resolved
  item.
- An unrecognised kind returns **no** proposed actions (never a default action).

## 4. Approval → mission coupling (the reviewed rule)

Today `decide_approval` updates only the approval row. It does **not** touch the
mission. Wave 4 adds an explicit coupling, and its rule is deliberately narrow:

> Deciding an `APPROVAL_REQUIRED` approval returns its mission to `RUNNING`
> **iff there is no other unresolved blocker on that mission.**

"Other blocker" = another `PENDING` approval on the same mission. We do **not**
guess about live sessions here: an approval decision is a human saying "yes, go",
and the *resume* itself is a separate, preview-first action (it needs a live
session, checked at that time). Coupling the decision to "resume" would make a
"yes" silently start work — exactly the thing Actuation=False forbids.

- `approve` + no other pending approval → mission `NEEDS_ATTENTION → RUNNING`
  (legal edge, exists in the state machine).
- `approve` + another pending approval → mission stays `NEEDS_ATTENTION`; the inbox
  simply shows one fewer item on that mission.
- `reject` → mission stays `NEEDS_ATTENTION` (a rejection is not a resolution; the
  operator must choose what to do next, visibly).

The rule is recorded in `docs/p3/A8_REBASELINE.json`'s sibling window note if it
ever moves a pinned file, and it is asserted by unit tests, not by prose.

## 5. API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/domain/needs-attention/resolutions` | open | inbox + per-item resolution in one payload |
| `POST` | `/api/v1/domain/needs-attention/{mid}/resolve` | token | **preview-first**: `{action, dry_run}` |

`dry_run: true` (the default) returns exactly what *would* change and mutates
nothing. `dry_run: false` applies. **Preview and apply share one code path**, so the
preview cannot disagree with the effect — the Wave-4 falsifier for "the preview
lied" is that the DB is byte-identical across a preview request.

## 6. UI (preview-first, two signals)

- Each row shows the kind badge, the reason, and **two** chips: `POLICY PERMITTED`
  and `CURRENTLY EXECUTABLE` (`EXECUTABLE` / `BLOCKED: <reason>` / `UNKNOWN`).
  UNKNOWN renders neutral. There is no green for "we don't know".
- Clicking an action opens a **preview dialog** listing the exact changes the server
  returned for `dry_run:true`. The confirm button is a **second** action. A click
  never mutates.
- High-risk actions show a distinct "this re-enters work" warning and require the
  confirm to be typed/held (an explicit approval step), not a stray click.

## 7. Falsifiers

1. **The preview lies**: a `dry_run` request must leave the DB byte-identical
   (hash the domain DB before/after). Proven in the gate.
2. **UNKNOWN is treated as fine**: force the context to be unreadable (drop the
   capability map) and assert every affected action is `UNKNOWN`, not `EXECUTABLE`.
3. **Action on a resolved mission**: move a mission out of `NEEDS_ATTENTION` then
   ask to resolve it → `BLOCKED / NOT_IN_ATTENTION`.
4. **A rejected approval silently resolves the mission**: reject, then assert the
   mission is still `NEEDS_ATTENTION`.
5. **Actuation leaks through "resume"**: assert every high-risk action is
   `BLOCKED / ACTUATION_DISABLED` while `actuation_enabled=False`.

## 8. Boundaries held

- Actuation stays disabled: `acted=false`, no actuator is reachable from the
  resolver or the coupling path.
- UNKNOWN never renders green.
- No demo data: resolutions are computed from the store, and an empty store yields
  an empty inbox, not a sample.
- GREEN 4778 untouched.

## 9. Evidence

All numbers below are from real runs on the W4 working tree; the gate and the
two suites are the authoritative artifacts.

### 9.1 Wave-4 gate

| Field | Value |
|---|---|
| Runner | `tools/v1_3_wave4_attention_gate.py` (real v1.3 ASGI app + real HTTP) |
| Verdict | **PASS 19 / 19**, `failures: []` |
| Evidence JSON | `.workbuddy/scratch/v1_3_wave4_gate_20260929-074404.json` (untracked by design — see note) |
| sha256 | `126f328b86bb371be69d643d58506307a6c38fae4e32ec809d6eb17bc9f7624b` |

Key steps asserted: `POLICY_VS_EXECUTABLE_ARE_SEPARATE_FIELDS`,
`RESUME_POLICY_PERMITTED`, `ACTUATION_DISABLED_BLOCKS_RESUME`,
`UNKNOWN_NEVER_EXECUTABLE`, `PREVIEW_RETURNS_PLAN`, **`PREVIEW_DOES_NOT_MUTATE`**
(domain DB sha256 identical before/after: `58789904cc23` → `58789904cc23`),
`APPLY_REQUIRES_TOKEN`, `APPLY_VERIFY_TRANSITIONS`, `APPLY_CLEARS_ATTENTION`,
`BLOCKED_APPLY_DOES_NOT_MOVE`, `RESOLVE_NOT_IN_ATTENTION_REFUSED`,
`HEALTH_ACTUATION_FALSE`.

> Note on artifact location: `artifacts/` is gitignored (`.gitignore:8`), so the
> gate JSON is *not* in version control. Its sha256 is anchored here, in a tracked
> file, per the project rule that a gate which cannot be reproduced is not a gate.

### 9.2 Tests (real tallies, clean temp dirs)

| Suite | Result |
|---|---|
| `packages/jcc_core/tests/test_attention_control_v1_3.py` | **17 passed** |
| `tests/e2e/test_attention_control_v1_3.py` | **9 passed** |
| `packages/jcc_core/tests` + `tests/e2e` (full, fresh `--basetemp`) | **717 passed, 0 failed, 0 errors** (415.77s) |
| `tools/tests` (full, fresh `TEMP`) | **247 passed, 0 failed** (460.05s) |
| `packages/jcc_core/tests/test_p3_acceptance_gate.py` (A8) | **32 passed** |
| Frontend `tsc --noEmit` | clean (exit 0) |
| Frontend `vitest run` | **51 passed** (8 files) |

### 9.3 A8 / A11 governance edits

- A8: exactly ONE pinned file moved — `packages/jcc_core/server.py`
  `4ac96c0e.. → 32d2540d..` — recorded as window
  `V1.3-FREEZE-W-4-ATTENTION` in `docs/p3/A8_REBASELINE.json` (the byte change and
  the pin update are in ONE commit).
- A11: inbound surface gains the token-gated resolve POST; `OPEN_GET_ENDPOINTS`
  23 → 24 (the resolutions read is read-only and open). `test_server_surface.py`
  asserts `declared == served` (24 == 24).
- Security-gate artifact re-emitted: `artifacts/release/V1_1_SECURITY_GATE.json`,
  `verdict=GO high=0 medium=0 low=0 info=0 ran=9/9`, `[check] OK` sha
  `0591e8a63e5b000d40a19a17`. Wave 4 changed the route inventory, so the artifact
  was stale by construction; it is refreshed **in the Wave 4 commit** (`--check`
  now passes).

### 9.4 Commit / rollback

| Item | Value |
|---|---|
| Commit | `v1.3-wave4-needs-attention` |
| Rollback point | `git tag jcc-v1.3-wave4-rollback-before → b83a2ee` (Wave 3 commit) |

### 9.5 Boundaries verified

- **GREEN 4778 untouched** — no script in this Wave targets the protected service;
  the security gate's S-08 step (protected service and runtime DB untouched) ran
  `ok=True`.
- **Actuation stays off** — `RESOLUTIONS_ACTUATION_FALSE` and `HEALTH_ACTUATION_FALSE`
  both asserted; every high-risk action resolves `BLOCKED / ACTUATION_DISABLED`.
- **UNKNOWN never green** — `UNKNOWN_NEVER_EXECUTABLE` asserted on the wire.
