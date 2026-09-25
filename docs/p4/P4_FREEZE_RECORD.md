# P4 Freeze Record — JEV COMMAND CENTER

**Date:** 2026-09-25
**Freeze record commit:** _(this commit)_
**Product-blob `frozen_at` (manifest):** `e6f5c15`
**Full suite baseline run:** HEAD `8e4e5e1`, `CODEBUDDY_SAFE_DELETE_ENABLED=0` → **476 passed, 0 failed** (441s).

This document is the TRACKED freeze record. Its sha256 anchors MUST match
`artifacts/release/manifest.json` and `packages/jcc_core/tests/test_p3_acceptance_gate.py::_BASELINE_HASHES`
(the A8 gate). All three are generated from the same source: `sha256(git cat-file blob <commit>:<path>)`,
i.e. the LF-normalised canonical blob (the `_sha()` convention — `.gitattributes` `* text=auto eol=lf` makes
the stored blob LF, so this equals the raw-disk sha256 on any clean checkout).

## 1. A8 re-baseline — 6 pinned production artifacts moved

All six moves are byte-current work already in HEAD (no dirty values). New values are blobs at the
freeze commit, taken with `git rev-parse <freeze>:<path>`, never from a worktree.

| artifact | old (9ee4507-era) | new (freeze) | driver |
|---|---|---|---|
| `packages/jcc_core/core.py` | `6e44314a9578d2ced819bbf2c220b366a37323e82c51cf644392b915a4a6d117` | `7d19e0c62defc051a6a7d18da10dd713db4d0d0b5371215ce51ca22c18778d84` | #82 R1-R4 + F1/F2 + M1/M2 |
| `packages/jcc_core/state_store.py` | `399b08994b231e624a286d90b8c946f76bb14912e45490806a227c4d1be41587` | `b66674356a29781d84acb437095de76a3484da91a639a39d42d8c0abe6f76f95` | #82 collapse + F1/F2 + M1 + M2 |
| `packages/jcc_core/run.py` | `d820cd848befeac1ed99a66e710b5353a53a23409f0cfddb27e4b986606765f3` | `0f9acd373fcc64bb7100a3c200b4e9088b640a05f2b49a8aa4c47d9fd84ce035` | #77 loopback-bind fix |
| `packages/jcc_core/server.py` | `92ac49cf294935c7c7d187dce5eea09b10cbd90a41ea150e5ae1a005c810beff` | `9bca4bd4a499d1d658a9bd32a74127be6c7b3eaae6d5757e1f0681d5fb4b7cce` | #82 counter collapse |
| `packages/supervisor/sentinel.py` | `65dbfea5050e9e6a0087354b219c88a741d2b4ff4cb9598c8beff7987b2655eb` | `21d8f5fa8a984ed741f97eaa3aac25fb3d18bc3c4b424fae36ec0e5d3486237f` | S-1..S-4 + origin write |
| `packages/supervisor/budget.py` | `96a11b69dd0414826735e98b932975cca50861968cd833b4bf9a3ef9c1d1c1b2` | `c47c85b91af5af1006e1620bc36dc559f43ef62da2b6e642dfa99ea9991e59ab` | fail-closed cost + origin write |

6 artifacts unchanged (sha256, anchor): `config/jcc.yaml` `4ae55770ed70dbd0ec940514f51ebf8adf3e703a9a49105656d8b9e643acaee0`,
`actuator.py` `5f0e338c3618e434702e1f50f2c037c5b604e642aa0f364d93cd96383a6e1ae2`,
`decision.py` `b61c88177e776aeacac4cda17bd14410a0f8ef4e88de91f3b6faa3262ad0e31c`,
`policy.py` `a4fe24cb1b2ad4b55ce91cb8360245a6df77c2033b3ace829cbf0614f2359258`,
`config.py` `af0ddc1868abf19edc8f7babd5edc61c7fd47ed7ee31187e2798edc844f1c08f`,
`packages/supervisor/shadow_store.py` `b101aebef464bab5f01ebb7cf3ef3318262d5cd4f73c61343ded92e3dcae5a3e`.

## 2. A8 negative-control gate

- `test_A8_negative_control_enabled_without_allowlist` now reports **all** moved pins (not just the first),
  and the failure message mandates re-baseline from `git rev-parse <freeze-commit>:<path>`.
- New premise pin `test_A8_gitattributes_enforces_lf_eol` asserts repo-root `.gitattributes` exists and
  contains `* text=auto eol=lf`; removing the attribute turns the pin red (the hash convention's premise
  is now guarded, not silently assumed).
- `_sha()` docstring corrected: it no longer claims `core.autocrlf=true and NO .gitattributes`; it records
  the measured fact that normalisation is a no-op today and is retained as insurance against
  `core.autocrlf=true` clones without attributes (the 49d91d4 incident).

## 3. Release-evidence mechanism (P4-A9 / ADR-0009 EV-11)

- `scripts/emit_release_evidence.py` re-emitted `artifacts/release/manifest.json` at `frozen_at=e6f5c15`.
- **EV-11a sidecar**: `manifest.ev11a.<commit>.json` — the generator runs twice at the same commit,
  serialises twice, and the manifest is rejected unless both sha256 digests are equal. `ran_at`/`basetemp`
  are stripped to the sidecar; the file is named by commit, not by clock.
- Each manifest A8 entry's `sha256` is `sha256(git cat-file blob <frozen_at>:<path>)` — verified 12/12
  equal to the A8 gate's `_BASELINE_HASHES`. (Earlier re-emit wrongly used git's SHA-1 object id; fixed
  in `8e4e5e1`.)
- **Path-defect found and fixed during freeze sign-off.** The on-disk manifest had recorded `shadow_store.py`
  under `packages/jcc_core/` — a path that does **not** exist (the real file is `packages/supervisor/shadow_store.py`).
  The hash value was correct (copied from the A8 gate) but the *path* was wrong, so EV-11 verification against
  that path would have failed. The manifest was regenerated from the corrected `scripts/emit_release_evidence.py`
  pin list (which already uses `packages/supervisor/shadow_store.py`). The 12 entries now reconcile **12/12**
  against the A8 gate `_BASELINE_HASHES` **and** the committed HEAD blobs (independently re-computed with
  `sha256(git cat-file blob HEAD:<path>)`); the stale emit is superseded.

## 4. #82 directive-origin attribution — independent security review

- **Verdict: GO** (commit `252cb5b`, `docs/SECURITY-P3-REVIEW.md` ADDENDUM #82). 18 independent nails green.
- `origin` column three attack surfaces CLOSED: no-origin → `TypeError`; forged id/origin mismatch →
  `ValueError` + zero rows landed; M1 keeps un-attributable rows `NULL` and in the `refused` list.
- K-fork directionality: unrecognisable shapes never merge → only over-count (conservative), and
  `add_directive`'s F1 rejects any unrecognisable shape, so an attacker cannot inflate the budget.
- **Residual (NON-BLOCKING, P5):** the engine-side `core.py:74-79` `_broadcast` `except Exception: pass`
  still swallows the shadow result, so a caller cannot distinguish "shadow arbitrated and replaced with
  ESCALATE" from "shadow never ran". S-1..S-4 make the failure visible *inside the sentinel process*
  but §E's `out`-field injection / `_broadcast` refactor was not done. Tracked for P5.

## 5. `rate_used` semantic change (recorded)

`budget_snapshot()` now counts **one economic event once** (the two directive planes collapsed via
`directive_event_key()`), not one row per plane. The integration test (`test_p3_core_integration.py`)
reads `directives_used` **4 → 2** after the #82 fold. This is a counter-semantics change, not a
regression; recorded here so the freeze is not mistaken for a metric drop.

## 6. KD-004 status — STILL OPEN

M1 (`backfill_directive_origins`) is a *no-automatic-side-effect* migration: it is never invoked by
`_init_db` / `add_directive` (pinned by `test_m1_is_not_reachable_from_any_automatic_path`). Because it
is never invoked, it has **never run on a real `jcc.db`**. ADR-0011 clause 2 requires a census proving
every pre-F1 store has `origin IS NULL` rows == 0 before closing — that census is not done. KD-004
stays OPEN; it must not be closed without a new ruling + the census. (See ADR-0011 "Correction recorded
on 2026-09-25": the earlier "0 unrecognised rows" claim was withdrawn — non-derivable rows DO exist,
all observed are short synthetic fixture ids.)

## 7. Activation posture

No external/high-risk action was taken during P4. Everything is local-first: no provider calls, no
publish, no external message, no production-DB write outside the test harness, no process kill. The
release is a **PRODUCTION-READY LOCAL RELEASE** gated by the A8 negative control + full suite + EV-11
reproducibility + independent security GO.
