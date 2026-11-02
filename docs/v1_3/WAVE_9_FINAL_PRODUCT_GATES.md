# JCC v1.3 Wave 9 — Final Product Gates

**Owner:** Orion (release governance)
**Status:** the gate is the deliverable; its measured run is recorded in §8
**Product freeze:** the commit that introduced `docs/v1_3/FINAL_PRODUCT_FREEZE.md`
(resolve with `git log -1 --format=%H -- docs/v1_3/FINAL_PRODUCT_FREEZE.md`)

## 0. The gate's question

> Does the v1.3 Product Edition actually work, end to end, **measured at the
> freeze commit** — and does every claim this release makes rest on a measurement
> rather than on a remembered string?

Everything in this wave is downstream of the second half of that sentence. The
v1.3 surface is unusually well covered by per-wave gates (W2…W8), and that is
precisely the risk: eight green verdicts produced at eight different commits are
**not** a green verdict at the ninth. A gate that was written in Wave 2 and never
re-run cannot notice that Wave 5 renamed the thing it measured.

## 1. Why v1.3 needs its own gate

`artifacts/acceptance/final_gate_run.py` (v1.0.1-ops) and
`tools/final_gate_run_v1_2.py` certify different freeze SHAs of a different
product surface, and both are built around the **self-referential manifest model**
that v1.3 Wave 0 retired (`docs/v1_3/WAVE_0_RELEASE_FOUNDATION.md`).

`tools/final_gate_run_v1_3.py` therefore re-implements the same *guarantees* and
none of the *assumptions*: every verdict is computed from a live measurement
taken now, and nothing is read from a document this release also writes.

One consequence is worth stating plainly, because it looks like an omission: **the
release manifest is deliberately NOT a leg of this gate.** The manifest certifies
the `PRODUCT_FREEZE_SHA` and its `gate` block is populated *from this gate's own
result*, so making the manifest a leg would be exactly the self-referential loop
Wave 0 removed. Emitting and `--check`ing the manifest is a release step that runs
*after* the freeze commit (§5).

## 2. The leg set

| Group | Legs | What a leg proves |
|-------|------|-------------------|
| **A** contract | `A1_FREEZE_MODEL` (19) · `A2_DOMAIN` · `A3_MISSION` · `A4_ATTENTION` · `A5_DELIVERABLES` · `A6_CONNECTIONS` · `A7_INVARIANTS` (55) · `A8_SECURITY_GATE` | each Wave's contract still holds at the freeze, re-measured |
| **B** wave gates | `B1_W2_PRODUCT_UI` (30) · `B2_W3_MISSION_GATE` (30) · `B3_W4_ATTENTION_GATE` (19) · `B4_W5_DELIVERABLES_GATE` (17) · `B5_W6_WORLD_GATE` (6) · `B6_W7_CONNECTION_GATE` (20) · `B7_W8_RUNTIME_GATE` (54) | the seven whole Wave gates are **re-run**, not cited |
| **C** regression | `C1_CORE_E2E` (786) · `C2_TOOLS_TESTS` (248) · `C3a_TYPECHECK` · `C3b_VITEST` (76) · `C3c_BUILD` | the full suites at the freeze |
| **D** live invariants | `D1_GREEN_PROTECTION` · `D2_PRODUCT_CONFIG_INERT` · `D3_UI_IS_NOT_STDLIB` | the protected service is untouched; the frozen product config really is inert when **booted**; the SPA is served by ASGI |
| **E** release hygiene | `E1_FROZEN_TREE` · `E2_GATE_INPUTS_TRACKED` · `E3_A8_PINS_VS_HEAD_BLOBS` · `E4_NO_LISTENERS_LEFT_BEHIND` · `E5_FREEZE_ANCHOR_RESOLVES` | the release is reproducible **from a clone** |
| **F** self-falsification | `F1_INSTRUMENTS_ARE_FALSIFIABLE` (7 falsifiers) | this gate's own instruments can fail |

The parenthesised numbers are **floors carried forward from documented history**,
not values read off this run: `A1`=19, `A7`=55, `B2`=30, `B6`=20, `B7`=54,
`C1`=786, `C2`=248, `C3b`≥71. A leg that reports fewer fails, so the measured set
cannot silently shrink. (`C2` was 247 for four waves; this wave's one added
falsifier is why it reads 248, and the reason is recorded in §4.5 rather than
left as an unexplained +1.)

## 3. The five non-vacuity rules

Each rule below is a defect this project has already paid for once. They are
implemented as code, and Group F **proves they can fail** instead of asserting it.

| Rule | Statement | The defect it retires |
|------|-----------|----------------------|
| **R1** | a pytest leg passes only on `exit 0 AND passed>0 AND failed==0 AND errors==0 AND skipped==0` | "26 skipped / exit 0" once read exactly like a pass |
| **R2** | a gate leg's verdict comes from the evidence file the gate wrote, and **that file must postdate the leg's start** | "the tool ran once" and "THIS run ran" are different claims; a launcher once printed the *previous* run's output 2 s after launch |
| **R3** | every leg asserts a floor on what it examined | "passes because it looked at nothing" |
| **R4** | a leg that claims evidence but leaves no file on disk fails; the aggregate re-checks every leg record exists | `invoked` is not `succeeded` |
| **R5** | Group F feeds the parsers and the AST auditor synthetic inputs they **must** reject | a gate whose own instruments cannot fail is a green washer |

R2 deserves its extra detail. `parse_gate_stdout` reads only the child's *claim*;
`judge_gate_evidence` then opens the claimed artifact and requires
`verdict == PASS`, `passed == total > 0`, `failures == []`, and
`mtime_ns >= leg_start_ns`. A gate whose stdout says PASS while its artifact says
FAIL fails the leg — and Group F's `CLAIM_MUST_AGREE_WITH_ARTIFACT`,
`STALE_EVIDENCE_IS_REFUSED` and `THE_JUDGE_ALSO_ACCEPTS` bracket that behaviour
from both sides, so the judge cannot pass by rejecting everything either.

Completeness and freshness are enforced at the aggregate as well: the gate
declares every leg it is supposed to produce, and the result is `NOT_RELEASE_READY`
if any is absent (`EXPECTED_LEGS_ABSENT`) or was measured at another commit
(`LEGS_MEASURED_AT_ANOTHER_HEAD`). Legs recorded at a previous HEAD are deleted
before a run, so a repair run at a moved HEAD cannot re-use them.

## 4. What this gate found

The final gate earned its keep before it ever reached a green state. Five defects
were found, and two of them — 4.3 and 4.5 — were real product defects. The other
three were in the instruments, or in the environment, that measured it.

### 4.1 The W2 product-UI gate measured a topology the product does not ship

`tools/v1_3_wave2_ui_gate.py` booted the API on an **ephemeral** port and served
`dist/` behind a bespoke `http.server` that reverse-proxied `/api/*` to that port,
justified by a docstring claim that this is "what the real product does when the
durable runtime serves UI + API from one port".

That premise is false. The shipped topology is two processes: the API on
`127.0.0.1:4779` and the SPA on `127.0.0.1:4780` served by
`apps/command_center_3d/serve_visual.py` — a **static** ASGI server that serves
`/health`, `/api/health` and the bundle, and does not proxy `/api/v1/*`. The SPA
therefore talks to the API **cross-origin**, which is why `server.cors_origins`
allowlists 4780/4781.

The gate only ever passed because `dist/` happened to have been built with
`VITE_API_BASE=same-origin`, a non-default build. Once the bundle was rebuilt with
the default (an absolute `http://127.0.0.1:4779`), every browser request bypassed
the gate's proxy and hit a port where nothing was listening. Measured before the
fix: **18/23**, with `LIVE_REAL_DATA`, `LIVE_REAL_MISSION`, `LIVE_MISSION_STATE`,
`STUDIO_PROJECT_OPTION` failing and six `ERR_CONNECTION_REFUSED` console errors.

The repair is not to rebuild the artifact into the shape the harness wants. The
gate now loads `config/jcc-v1.3.yaml`, asserts its declared ports, refuses to run
if either product port is already held (never adopt a process it did not start),
serves `dist/` with the **product's own** server module, drives real Chrome at the
real UI origin so the CORS allowlist is exercised, and asserts every port is
released again. Measured after the repair: **30/30**.

### 4.2 The sandbox's bulk-delete counter aborted a healthy gate

The W8 runtime gate's `repair` action renames stale pid bookkeeping — it never
deletes. The sandbox shim, however, counts deletions **per agent turn** and raises
`SAFE_DELETE_BULK_CONFIRM_REQUIRED {"count":50,"threshold":50}` once the counter
saturates; the process then dies with an **empty stdout**, which looks exactly like
a product failure. It was not: the quarantine it was performing had already
succeeded on disk.

Every leg therefore runs under `CODEBUDDY_SAFE_DELETE_ENABLED=0` — the reporting
configuration `packages/conftest.py` itself names — and each pytest leg gets a
**fresh, never-before-existing** `TEMP`, which also removes the second
contamination class this project has hit (`tools/tests` reading another run's
leftovers under `$TEMP/jev-pytest/`).

### 4.3 One API base, four definitions

`App.tsx` understood the `VITE_API_BASE=same-origin` sentinel and collapsed it to
`""`; `JevProviderCard.tsx` (twice) and `ConnectionsView.tsx` concatenated the raw
value, producing `same-origin/api/...` — a path, not an origin. `SceneStore.ts`
carried a fifth hard-coded copy. A configuration knob honoured in one of four
places is worse than no knob: it looks supported and silently half-works.

Repaired by introducing `apps/command_center_3d/src/lib/apiBase.ts` as the single
definition, importing it in all four call sites, and pinning the semantics with
`src/lib/apiBase.test.ts` (5 tests, vitest 71 → 76).

### 4.4 The gate's own input list had two holes in it

`E2_GATE_INPUTS_TRACKED` asserts that *every input this release rests on* is
committed at HEAD. It listed 26 paths and was missing two of the release's own
decision carriers:

* `tools/emit_v1_3_legs.py` — the tool that produces the signed legs the manifest
  then embeds; its sibling `tools/emit_v1_3_release_manifest.py` **was** listed,
  which is what made the omission invisible.
* `tools/v1_1_security_gate.py` **and** the `artifacts/release/V1_1_SECURITY_GATE.json`
  its `--check` re-reads — a tool this very gate executes (`A8_SECURITY_GATE`), so
  one of the gate's own legs rested on an input the gate did not certify.

Neither absence would have produced a red leg: both files exist on this machine, so
the release would have been signed by an input list that *looked* complete and had
two holes in it. That is the same defect class as §4.1 — an instrument whose
statement is stronger than its check — and it was caught this time by reading the
list against what the gate actually invokes, **not** by watching a leg fail. A gate
that only ever finds defects by being red will miss this kind entirely.

The list is now 29 entries, each of which resolves at HEAD; the code carries the
reason for each addition so a future edit is a decision rather than a drift, and
`E2` fails outright if the list ever drops below 20 entries.

### 4.5 The verification record published a fingerprint of the operator's home directory

The gate's own `C2_TOOLS_TESTS` leg came back **242 passed / 5 failed** against a
floor of 247. This was the first time that leg had ever been run at the freeze
candidate, and it was red for a reason this wave had not caused.

All five failures traced to one line of JSON:

```
credential_evidence.file_presence["C:/Users/Administrator/.codex/config.toml"].bytes
    committed = 5806   fresh = 5870
```

`file_presence()` published the **exact byte size of files outside this
repository**, and `tools/v1_1_emit_real_jev_verification.py --check` — the check
that makes the record trustworthy — compares a fresh generation against the
committed one. The operator's Codex config had grown by 64 bytes on its own, so
the record was declared STALE and five tests failed: the `--check` test itself,
three parametrised canary-sensitivity tests (which must observe a clean reading
*before* injecting anything), and the e2e-fixture lifecycle test, which reads
`--check` at both ends.

Two things make this a defect **in the record** rather than a test to relax:

* The module's own docstring promises "PRESENT / ABSENT / SECURITY_BLOCKER only",
  and for env values it publishes only a coarse *length bucket* — an exact length
  is already treated as something that must not leave the process. An exact file
  size is the same kind of fact.
* The emitter already had the rule, in writing, for `tracked_file_count`: *"a key
  belongs in `VOLATILE_KEYS` when it is PROVENANCE, not when it is a CLAIM"* — and
  it explicitly rejects the alternative, because *"a false failure is worse than
  no check: it teaches the operator to ignore the check."*

`file_presence()` now publishes `state` and `is_file` only. Removing the key is
deliberately stronger than adding it to the volatile list: the record stops
carrying a fingerprint of the operator's home directory at all, so there is
nothing left to drift. `test_the_file_presence_block_is_a_claim_not_a_host_measurement`
pins the shape, with a vacuity floor of its own (the block lists ≥10 candidates,
so it cannot pass by walking nothing) and a measured falsifier — it rejects the
previous artifact and rejects an emptied block, and accepts the regenerated one.

**The tool's own advice — "re-run without `--check` and commit the result" — was
the wrong repair here.** It removes the symptom and re-creates it the next time
the operator edits a local file; that is the refresh ritual Wave 0 retired. The
record was re-baselined **once**, after the cause was removed, and only the six
`bytes` keys plus the already-classified provenance fields moved.

Consequences recorded elsewhere: `C2_TOOLS_TESTS`'s floor is raised 247 → 248,
because this wave adds exactly one test and a floor is a minimum carried forward
from documented history (§2); and the tracked acceptance evidence
`artifacts/acceptance/CONTINUATION_INFRA_TEST.json` — which the red run had
rewritten to `status: FAIL` — is rewritten by the green run and committed, so the
file a controller reads to decide the platform runs does not ship a stale red.

## 5. Release steps, in order

| Step | Action | Why this order |
|------|--------|----------------|
| 1 | commit the freeze (gate + tools + freeze document + the §4.5 fix and the re-baselined v1.1 record) | `E5` requires the freeze document to be **introduced by** the commit the gate measures, and every `tools/**` and `tests/**` path is product-shaped, so a fix discovered later would move the freeze itself |
| 2 | `final_gate_run_v1_3.py --full` at that commit | every leg's `head` must equal the freeze, or the aggregate blocks |
| 3 | `emit_v1_3_legs.py` | refuses to sign unless the freeze anchor resolves to the measured commit, and fails any leg whose gate legs are missing |
| 4 | `emit_v1_3_release_manifest.py --emit --freeze <freeze>` | the manifest certifies the freeze and embeds this gate's `gate` block |
| 5 | commit the release metadata (result, legs, signatures, manifest, sidecars) | `artifacts/release/**` and `docs/v1_3/**` are the only allow-listed paths after the freeze; **a single product-file diff in `freeze..tag` is a hard FAIL** |
| 6 | `emit_v1_3_release_manifest.py --check` | directional and never writes: it must be CLEAN *without* re-emitting |
| 7 | tag `jcc-v1.3-product-local` | the tag descends from the freeze with no product drift |

The manifest's `tag_head` is deliberately `null`: a commit cannot contain the sha
of the tag created on it, and re-stamping the manifest afterwards is the ritual
Wave 0 retired. The tag is a git object — resolve it with
`git rev-parse jcc-v1.3-product-local`.

## 6. Deliberately NOT claimed

Listed so their absence is visible rather than implied. Each would be an
unmeasured claim if written as a pass.

* Real-browser FPS at node counts 50/200/500/1000 (the product has no stress route).
* Parent-exit survival of the product runtime (the W8 restart transcript is
  evidence about the *controller*; only the host Task Scheduler registration can
  settle it, and it is not applied — see `FINAL_PRODUCT_FREEZE.md` §6.2).
* DPAPI-at-rest for the control token (it remains an environment variable).
* Reboot recovery (follows from the durable registration; configured, not proven).
* Anything about the v1.0.1-ops / v1.1 release profile or
  `docs/FINAL_STOP_CONDITIONS.md` semantics — the older profile is neither
  inherited nor repaired here, and its known `DEGRADED`-tolerance is not
  re-interpreted.
* Desktop shell / Tauri (explicitly not a v1.3 blocker).
* Any remote push, external publication or production deployment.

## 7. Evidence map

| Artefact | Path | Tracked |
|----------|------|---------|
| gate result (the release decision) | `artifacts/release/v1.3_product/FINAL_GATE_V1_3_RESULT.json` | yes |
| per-leg records (29) | `artifacts/release/v1.3_product/legs/V1_3_LEG_*.json` | yes |
| signed legs (10 dimensions) + bundle | `artifacts/release/v1.3_product/legs/V1_3_SIGNED_LEG_*.json`, `V1_3_LEG_SIGNATURES.json` | yes |
| release manifest + sidecar | `artifacts/release/v1.3_product/V1_3_RELEASE_MANIFEST.json` / `.sha256` | yes |
| run transcript | `artifacts/release/v1.3_product/FINAL_GATE_V1_3_RUN.log` | no — `*.log` is gitignored; diagnostics only |

The v1.2 manifest kept its counterpart under `artifacts/acceptance/`, which
`.gitignore` excludes — so the file the release rested on did not travel with the
repository and a clone could not verify it. The v1.3 decision carrier is
deliberately placed under the tracked `artifacts/release/**`, and the gate's
`E2_GATE_INPUTS_TRACKED` leg asserts every input it rests on is committed **at
HEAD** (not merely staged in the index).

## 8. Measured result at the freeze

Recorded here from the single `--full` run at the freeze commit; the
authoritative, machine-readable record is the gate result named in §7.

See `artifacts/release/v1.3_product/FINAL_GATE_V1_3_RESULT.json`:
`project_complete`, `legs_passed` / `legs_total`, `blockers`,
`release_readiness`, `green_untouched` and `pending_host_activation`.

## 9. Operator actions carried forward

Both are HOST actions recorded in `docs/v1_3/FINAL_PRODUCT_FREEZE.md` §6, repeated
here so a reader of this wave is not sent to another file for them:

```powershell
# 1. remove the two stray tasks created during the W8 measured incident
Unregister-ScheduledTask -TaskName JCC-v1.3-API   -Confirm:$false
Unregister-ScheduledTask -TaskName JCC-v1.3-PROBE -Confirm:$false

# 2. create the durable tasks, then adopt them
powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.3-register-tasks.ps1 -Apply
powershell -ExecutionPolicy Bypass -File scripts\jcc-v1.3.ps1 -Action mark
```

Until (2) runs, the product is operated by `scripts\jcc-v1.3.ps1` and is **not**
registered to survive a reboot.
